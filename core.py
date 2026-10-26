"""Lógica temporal sem câmera, OpenCV ou GPIO: pode ser testada no computador."""
import heapq
import json
import logging
import math
import threading
import time
from collections import Counter
from pathlib import Path


class SorterFault(RuntimeError):
    pass


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return validate_config(cfg)


def validate_config(cfg):
    def need(condition, message):
        if not condition:
            raise ValueError(message)

    def number(value, low, high):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and low <= value <= high)

    c, v, t, servos = (cfg[k] for k in ("camera", "vision", "timing", "servos"))
    need(c.get("backend", "auto") in ("auto", "usb", "csi"), "Backend de câmera inválido")
    device = c.get("usb_device")
    if device is not None:
        import re
        need(isinstance(device, str) and bool(re.fullmatch(r"/dev/video\d+|/dev/v4l/by-id/[^/]+", device)),
             "usb_device deve ser /dev/videoN ou um caminho /dev/v4l/by-id/...")
        need(c.get("backend", "auto") != "csi", "usb_device não se aplica ao backend csi")
    for k in ("width", "height"):
        need(isinstance(c[k], int) and 32 <= c[k] <= 1280, "Tamanho de imagem inválido")
    need(number(c["fps"], 5, 30), "FPS deve ficar entre 5 e 30")
    need(number(c["warmup_s"], 0.5, 10), "warmup_s inválido")
    need(number(c["exposure_us"], 100, 1e6 / c["fps"]), "Exposição inválida")
    need(number(c["analogue_gain"], 1, 16), "Ganho inválido")
    gains = c.get("colour_gains")
    need(gains is None or (isinstance(gains, list) and len(gains) == 2
                          and all(number(g, 0.1, 16) for g in gains)), "ColourGains inválidos")
    x, y, w, h = v["roi"]
    need(all(isinstance(n, int) for n in (x, y, w, h)) and x >= 0 and y >= 0
         and w > 0 and h > 0 and x+w <= c["width"] and y+h <= c["height"], "ROI fora da imagem")
    for k in ("stable_frames", "empty_frames"):
        need(isinstance(v[k], int) and 1 <= v[k] <= 30, k + " inválido")
    need(number(v["min_object_pixels"], 10, w*h), "Área mínima inválida")
    need(number(v["min_color_fraction"], 0.5, 1), "Fração de cor inválida")
    need(number(v["winner_margin"], 0.01, 1), "Margem de cor inválida")
    need(number(v["max_occupied_s"], 1, 60), "Tempo máximo de ocupação inválido")
    need(number(v.get("tracking_distance_px", 45), 5, 500), "Distância de rastreamento inválida")
    need(set(servos) == {"direita", "esquerda"}, "Configure os dois servos")
    need(len(v["colors"]) == 3, "Configure exatamente três cores")
    need("desconhecida" not in v["colors"], "Nome de cor reservado")
    def hsv_range(bounds):
        need(len(bounds) == 2 and all(len(row) == 3 for row in bounds), "Faixa HSV inválida")
        lo, hi = bounds
        need(all(isinstance(n, int) for row in bounds for n in row), "HSV exige inteiros")
        need(all(0 <= lo[i] <= hi[i] <= (179 if i == 0 else 255) for i in range(3)),
             "Limites HSV inválidos; divida a faixa caso atravesse H=0")
    hsv_range(v["occupancy_hsv"])
    for color, entry in v["colors"].items():
        need(entry["destination"] in ("direita", "esquerda", "reto"), "Saída inválida: " + color)
        need(bool(entry["ranges"]), "Falta faixa HSV: " + color)
        for bounds in entry["ranges"]:
            hsv_range(bounds)
    need({e["destination"] for e in v["colors"].values()} == {"direita", "esquerda", "reto"},
         "Use uma cor por saída")
    need(number(t["min_cap_gap_s"], 0.1, 60), "Intervalo de tampinhas inválido")
    need(number(t["max_lateness_s"], 0.001, 0.2), "Tolerância de atraso inválida")
    need(number(t["camera_timeout_s"], 0.3, 5), "Timeout de câmera inválido")
    need(isinstance(t["max_pending_events"], int) and 2 <= t["max_pending_events"] <= 200,
         "Limite da fila inválido")
    pins = []
    for name, s in servos.items():
        need(isinstance(s["gpio"], int) and s["gpio"] in (17, 18, 19, 22, 23, 24, 25, 27),
             "GPIO não previsto neste projeto: " + name)
        pins.append(s["gpio"])
        for k in ("rest_us", "push_us"):
            need(isinstance(s[k], int) and 1000 <= s[k] <= 2000, "Pulso fora de 1000–2000 us")
        need(s["rest_us"] != s["push_us"], "Posições de repouso e desvio iguais")
        for k in ("travel_s", "advance_s", "hold_after_arrival_s", "return_s"):
            need(number(s[k], 0.01, 60), k + " inválido")
        need(s["travel_s"] - s["advance_s"] > v["stable_frames"] / c["fps"] + 0.1,
             "Câmera perto demais do servo para confirmar a cor: " + name)
    need(len(set(pins)) == 2, "Os servos precisam de GPIOs diferentes")
    if "motor" in cfg:
        from motor import validate_motor
        validate_motor(cfg["motor"], servos)
    return cfg


class Debouncer:
    """Uma decisão por ocupação; arma somente após observar a faixa vazia."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.armed = False
        self.empty = self.stable = 0
        self.label = None
        self.first = self.occupied_since = None

    def update(self, label, now):
        # None = realmente vazio; 'desconhecida' = objeto sem cor confiável.
        if label is None:
            self.empty += 1
            self.label, self.stable, self.first = None, 0, None
            if self.empty >= self.cfg["empty_frames"]:
                self.armed = True
                self.occupied_since = None
            return None
        self.empty = 0
        if self.occupied_since is None:
            self.occupied_since = now
        if now - self.occupied_since > self.cfg["max_occupied_s"]:
            raise SorterFault("Faixa ocupada por tempo excessivo; verifique espaçamento/esteira")
        if not self.armed:
            return None
        if label != self.label:
            self.label, self.stable, self.first = label, 1, now
        else:
            self.stable += 1
        if self.stable >= self.cfg["stable_frames"]:
            self.armed = False
            return label, self.first
        return None


class Planner:
    """Reserva a passagem em AMBAS as estações, inclusive para verde/desconhecida."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_cap = -math.inf
        self.reserved_until = {side: -math.inf for side in cfg["servos"]}

    def plan(self, label, detected_at, now):
        if detected_at - self.last_cap < self.cfg["timing"]["min_cap_gap_s"]:
            raise SorterFault("Tampinhas próximas demais; aumente o intervalo de alimentação")
        entry = self.cfg["vision"]["colors"].get(label)
        dest = entry["destination"] if entry else "reto"
        windows, events = {}, []
        for side, s in self.cfg["servos"].items():
            arrival = detected_at + s["travel_s"]
            start = arrival - s["advance_s"]
            end = arrival + s["hold_after_arrival_s"]
            if start < self.reserved_until[side]:
                raise SorterFault("Passagens sobrepostas na estação " + side)
            if start < now:
                raise SorterFault("Detecção tardia; afaste a câmera ou reduza a velocidade")
            windows[side] = end + s["return_s"]
            if side == dest:
                events = [(start, side, s["push_us"]), (end, side, s["rest_us"])]
        # Só altera reservas depois de validar a passagem completa.
        self.last_cap = detected_at
        self.reserved_until = windows
        return dest, events


class Controller:
    """Thread de tempos independente da captura; fila limitada e vigilância da câmera."""
    def __init__(self, cfg, driver):
        self.cfg, self.driver = cfg, driver
        self.planner = Planner(cfg)
        self.condition = threading.Condition()
        self.queue = []
        self.counts = Counter()
        self.error = None
        self.stopping = False
        self.heartbeat = time.monotonic()
        self.thread = threading.Thread(target=self._worker, name="servos", daemon=True)

    def start(self):
        self.thread.start()

    def check(self):
        if self.error:
            raise SorterFault(str(self.error))

    def beat(self):
        with self.condition:
            self.check()
            self.heartbeat = time.monotonic()
            self.condition.notify_all()

    def submit(self, label, detected_at):
        with self.condition:
            self.check()
            if len(self.queue) + 2 > self.cfg["timing"]["max_pending_events"]:
                raise SorterFault("Fila cheia; reduza a alimentação de tampinhas")
            dest, events = self.planner.plan(label, detected_at, time.monotonic())
            for event in events:
                heapq.heappush(self.queue, event)
            self.counts[label] += 1
            self.condition.notify_all()
            return dest

    def _worker(self):
        try:
            with self.condition:
                while not self.stopping:
                    now = time.monotonic()
                    watchdog = self.cfg["timing"]["camera_timeout_s"] - (now - self.heartbeat)
                    if watchdog <= 0:
                        raise SorterFault("Câmera sem novos quadros; pare a esteira")
                    if self.queue and self.queue[0][0] <= now:
                        due, side, pulse = heapq.heappop(self.queue)
                        if now-due > self.cfg["timing"]["max_lateness_s"]:
                            raise SorterFault("Acionamento atrasado; pare a esteira e reduza a carga")
                        self.driver.set_pulse(side, pulse)
                        continue
                    delay = min(0.05, watchdog)
                    if self.queue:
                        delay = min(delay, max(0, self.queue[0][0]-now))
                    self.condition.wait(delay)
        except Exception as exc:
            self.error = exc
            logging.getLogger(__name__).error("FALHA DE CONTROLE: %s. PARE A ESTEIRA.", exc)
        finally:
            try:
                self.driver.park()
            except Exception as exc:
                self.error = self.error or exc

    def close(self):
        with self.condition:
            self.stopping = True
            self.queue.clear()
            self.condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise SorterFault("Thread de servo não encerrou; desligue a alimentação dos servos")
