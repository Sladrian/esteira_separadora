"""Rastreamento limitado e acionamentos independentes para operação contínua."""
from collections import Counter
import copy
import heapq
import logging
import math
import threading
import time

log = logging.getLogger("esteira")


class Tracker:
    """Associa componentes por posição prevista; uma decisão por trajetória visível."""
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracks = {}
        self.next_id = 0
        self.empty = 0
        self.ready = False

    def update(self, detections, now):
        events = []
        if not self.ready:
            self.empty = self.empty+1 if not detections else 0
            self.ready = self.empty >= self.cfg["empty_frames"]
            return events
        # Limites mantêm memória/custo previsíveis mesmo com ruído na imagem.
        detections = detections[:64]
        gate = self.cfg.get("tracking_distance_px", 45)
        pairs = []
        for ident, track in self.tracks.items():
            dt = min(.3, max(0, now-track["last"]))
            prediction = tuple(track["center"][i]+track["velocity"][i]*dt for i in (0, 1))
            for index, item in enumerate(detections):
                distance = math.dist(prediction, item["center"])
                if distance <= gate:
                    # Cor é uma preferência, nunca requisito: reflexos mudam o rótulo.
                    penalty = 8 if item["label"] != track["label"] else 0
                    pairs.append((distance+penalty, ident, index))
        used_tracks, used_items = set(), set()
        matches = {}
        for _, ident, index in sorted(pairs):
            if ident not in used_tracks and index not in used_items:
                matches[index] = ident
                used_tracks.add(ident)
                used_items.add(index)
        for index, item in enumerate(detections):
            if index in matches:
                track = self.tracks[matches[index]]
                dt = max(.001, now-track["last"])
                track["velocity"] = tuple((item["center"][i]-track["center"][i])/dt for i in (0, 1))
            else:
                if len(self.tracks) >= 128:
                    continue
                self.next_id += 1
                track = {"first": now, "last": now, "center": item["center"],
                         "velocity": (0, 0), "label": None, "stable": 0,
                         "sent": False, "missed": 0}
                self.tracks[self.next_id] = track
                used_tracks.add(self.next_id)
            track["stable"] = track["stable"]+1 if track["label"] == item["label"] else 1
            track.update(center=item["center"], last=now, label=item["label"], missed=0)
            if not track["sent"] and item["label"] != "desconhecida" and track["stable"] >= self.cfg["stable_frames"]:
                events.append((item["label"], track["first"]))
                track["sent"] = True
            elif not track["sent"] and now-track["first"] > self.cfg["max_occupied_s"]:
                events.append(("desconhecida", track["first"]))
                track["sent"] = True
        for ident in list(self.tracks):
            if ident not in used_tracks:
                track = self.tracks[ident]
                track["missed"] += 1
                if track["missed"] >= self.cfg["empty_frames"]:
                    if not track["sent"]:
                        events.append(("desconhecida", track["first"]))
                    del self.tracks[ident]
        return events


class ContinuousController:
    """Conflitos descartam movimentos, sem interromper a captura.

    Cada tampinha protege sua passagem nas duas estações. Um conflito cancela
    também o desvio anterior, para não empurrar a tampinha de outra cor.
    """
    def __init__(self, cfg, driver):
        self.cfg = copy.deepcopy(cfg)
        self.driver = driver
        self.condition = threading.Condition()
        self.queue = []
        self.passages = []
        self.active = {}
        self.counts = Counter()
        self.skipped = Counter()
        self.next_id = 0
        self.heartbeat = time.monotonic()
        self.suspended = False
        self.inhibit_until = 0
        self.stopping = False
        self.thread = threading.Thread(target=self._worker, daemon=True, name="servos-continuos")

    def start(self):
        self.thread.start()

    def check(self):
        if self.thread.ident is not None and not self.thread.is_alive() and not self.stopping:
            raise RuntimeError("Controle de servos encerrou inesperadamente")

    def beat(self):
        with self.condition:
            self.heartbeat = time.monotonic()
            self.suspended = self.heartbeat < self.inhibit_until
            self.condition.notify_all()

    def _pulse(self, side, pulse):
        try:
            result = self.driver.set_pulse(side, pulse)
            if result is False:
                self.skipped["servo_indisponivel"] += 1
        except Exception as exc:
            self.skipped["erro_servo"] += 1
            log.warning("Servo %s indisponível: %s; leitura continua", side, exc)

    def _park(self):
        for side, settings in self.cfg["servos"].items():
            self._pulse(side, settings["rest_us"])
        self.active.clear()

    def pause(self, reason):
        with self.condition:
            self.inhibit_until = max([time.monotonic(), self.inhibit_until] + [p["end"] for p in self.passages])
            self.queue.clear()
            self.passages.clear()
            self.suspended = True
            self._park()
            log.warning("Movimentos cancelados: %s. Leitura será retomada com faixa vazia", reason)

    def reconfigure(self, cfg):
        with self.condition:
            # Eventos já programados conservam seus tempos. ROI muda apenas com
            # fila vazia, exigido pelo chamador. Reservas existentes são mantidas.
            self.cfg = copy.deepcopy(cfg)
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            now = time.monotonic()
            return {"counts": dict(self.counts), "skipped": dict(self.skipped),
                    "pending": len(self.queue), "busy": bool(self.queue or self.active or
                        any(p["end"] > now for p in self.passages)), "suspended": self.suspended}

    def submit(self, label, detected_at):
        with self.condition:
            now = time.monotonic()
            self.counts[label] += 1
            self.passages = [p for p in self.passages if p["end"] > now]
            self.next_id += 1
            ident = self.next_id
            entry = self.cfg["vision"]["colors"].get(label)
            dest = entry["destination"] if entry else "reto"
            windows = {}
            for side, settings in self.cfg["servos"].items():
                arrival = detected_at+settings["travel_s"]
                windows[side] = (arrival-settings["advance_s"],
                                 arrival+settings["hold_after_arrival_s"],
                                 arrival+settings["hold_after_arrival_s"]+settings["return_s"])
            blocked = set()
            for old in self.passages:
                for side, (start, _, end) in windows.items():
                    a, _, b = old["windows"][side]
                    if start < b and a < end:
                        blocked.add(side)
                        self.queue = [event for event in self.queue if not (event[2] == side and event[4] == old["id"])]
                        heapq.heapify(self.queue)
                        if self.active.get(side) == old["id"]:
                            self._pulse(side, self.cfg["servos"][side]["rest_us"])
                            self.active.pop(side, None)
            if blocked:
                self.skipped["conflito"] += 1
                log.warning("Passagens próximas nas estações %s; desvios conflitantes cancelados, leitura continua", ", ".join(sorted(blocked)))
            if len(self.passages) >= self.cfg["timing"]["max_pending_events"]:
                self.skipped["fila_cheia"] += 1
                self.pause("fila cheia; reduza a alimentação")
                return dest, "fila cheia; sem acionamento"
            self.passages.append({"id": ident, "windows": windows, "end": max(w[2] for w in windows.values())})
            if dest == "reto":
                return dest, "sem desvio"
            start, end, _ = windows[dest]
            if self.suspended or dest in blocked or start < now:
                if self.suspended:
                    self.skipped["aguardando_liberar_estacoes"] += 1
                if start < now:
                    self.skipped["deteccao_tardia"] += 1
                return dest, "sem acionamento: conflito ou detecção tardia"
            if len(self.queue)+2 > self.cfg["timing"]["max_pending_events"]:
                self.skipped["fila_cheia"] += 1
                return dest, "sem acionamento: fila cheia"
            settings = self.cfg["servos"][dest]
            # Id antes do lado desempata eventos simultâneos sem comparar objetos.
            heapq.heappush(self.queue, (start, ident*2, dest, settings["push_us"], ident, True))
            heapq.heappush(self.queue, (end, ident*2+1, dest, settings["rest_us"], ident, False))
            self.condition.notify_all()
            return dest, f"desvio em {start-now:.2f}s; recolhe em {end-now:.2f}s"

    def _worker(self):
        try:
            with self.condition:
                while not self.stopping:
                    now = time.monotonic()
                    if not self.suspended and now-self.heartbeat > self.cfg["timing"]["camera_timeout_s"]:
                        self.pause("câmera sem novos quadros")
                    if self.queue and self.queue[0][0] <= now:
                        due, _, side, pulse, ident, pushing = heapq.heappop(self.queue)
                        if pushing and now-due > self.cfg["timing"]["max_lateness_s"]:
                            self.skipped["acionamento_atrasado"] += 1
                            log.warning("Desvio %s atrasado; descartado, leitura continua", side)
                            continue
                        self._pulse(side, pulse)
                        if pushing:
                            self.active[side] = ident
                        else:
                            self.active.pop(side, None)
                        continue
                    delay = .05 if not self.queue else min(.05, max(0, self.queue[0][0]-now))
                    self.condition.wait(delay)
        finally:
            self._park()

    def close(self):
        with self.condition:
            self.stopping = True
            self.queue.clear()
            self.condition.notify_all()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)

