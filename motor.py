"""Motor DC via L298N: PWM no enable, partida parada e vigilância da captura."""
import logging
import math
import threading
import time

log = logging.getLogger("esteira")
DEFAULTS = {"enabled": False, "driver": "l298n", "channel": "A", "pwm_gpio": 17,
            "in1_gpio": 27, "in2_gpio": 22, "frequency_hz": 1000, "reverse": False,
            "direction_control": "gpio"}


def validate_motor(settings, servos):
    if not isinstance(settings, dict) or not set(settings) <= set(DEFAULTS):
        raise ValueError("Configuração do motor inválida")
    cfg = dict(DEFAULTS, **settings)
    if type(cfg["enabled"]) is not bool or type(cfg["reverse"]) is not bool:
        raise ValueError("enabled e reverse do motor precisam ser booleanos")
    if cfg["driver"] != "l298n" or cfg["channel"] not in ("A", "B"):
        raise ValueError("Use driver l298n e canal A ou B")
    if cfg["direction_control"] not in ("gpio", "fixed"):
        raise ValueError("Controle de sentido inválido")
    if cfg["direction_control"] == "fixed" and cfg["reverse"]:
        raise ValueError("Em sentido fixo, a direção é definida na ligação da ponte")
    pins = [cfg[key] for key in (("pwm_gpio",) if cfg["direction_control"] == "fixed" else ("pwm_gpio", "in1_gpio", "in2_gpio"))]
    if any(type(pin) is not int or pin not in (17, 18, 19, 22, 23, 24, 25, 27) for pin in pins):
        raise ValueError("GPIO do motor inválido")
    if len(set(pins)) != len(pins) or (cfg["enabled"] and set(pins) & {s["gpio"] for s in servos.values()}):
        raise ValueError("GPIOs do motor precisam ser distintos e separados dos servos")
    if type(cfg["frequency_hz"]) is not int or not 100 <= cfg["frequency_hz"] <= 20000:
        raise ValueError("Frequência PWM do motor deve ficar entre 100 e 20000 Hz")
    return cfg


class Motor:
    def __init__(self, settings, servos, dry_run=True, timeout=.8):
        self.cfg = validate_motor(settings, servos)
        self.dry_run, self.timeout = dry_run, timeout
        self.lock = threading.RLock()
        self.pi = None
        self.available = False
        self.running = False
        self.percent = 0
        self.last_frame = None
        self.ready = False
        self.message = "Motor não configurado"
        self.fault = None
        self.stopping = threading.Event()
        self.thread = None
        if not self.cfg["enabled"]:
            return
        try:
            if not dry_run:
                import pigpio
                self.pi = pigpio.pi("localhost", 8888)
                if not self.pi.connected:
                    raise RuntimeError("pigpiod indisponível")
            self._clear_output()
            self._call("set_PWM_range", self.cfg["pwm_gpio"], 1000)
            self._call("set_PWM_frequency", self.cfg["pwm_gpio"], self.cfg["frequency_hz"])
            self.available = True
            self.message = "Parado; aguarde a leitura ativa e use Ligar"
        except Exception as exc:
            self.fault = str(exc)
            self.message = "Motor indisponível: "+str(exc)
            log.warning("%s; reconhecimento continua", self.message)
            try:
                self._clear_output()
            except Exception:
                pass
            if self.pi is not None:
                self.pi.stop()
                self.pi = None
        self.thread = threading.Thread(target=self._watchdog, daemon=True, name="motor-watchdog")
        self.thread.start()

    def _call(self, name, *args):
        if self.dry_run:
            return 0
        if self.pi is None:
            raise RuntimeError("Sem conexão com pigpiod")
        result = getattr(self.pi, name)(*args)
        if result < 0:
            raise RuntimeError(f"Falha no motor: {name}, código {result}")
        return result

    def _clear_output(self):
        # Enable em zero ANTES de mudar os pinos de sentido. Tenta todos os
        # comandos mesmo se um falhar. EN=0 permite parada por inércia (sem freio).
        errors = []
        commands = [("set_PWM_dutycycle", (self.cfg["pwm_gpio"], 0))]
        if self.cfg["direction_control"] == "gpio":
            commands += [("write", (self.cfg["in1_gpio"], 0)), ("write", (self.cfg["in2_gpio"], 0))]
        for name, args in commands:
            try:
                self._call(name, *args)
            except Exception as exc:
                errors.append(exc)
        self.running, self.percent = False, 0
        if errors:
            raise errors[0]

    def beat(self, ready):
        with self.lock:
            self.last_frame, self.ready = time.monotonic(), ready

    def command(self, action, percent=0):
        with self.lock:
            if action == "stop":
                self.stop("Parada solicitada pelo painel")
                return self.status()
            if action not in ("start", "speed"):
                raise ValueError("Ação do motor inválida")
            if not self.cfg["enabled"] or not self.available or self.fault:
                raise ValueError(self.message)
            if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError("Velocidade deve ficar entre 0 e 100%")
            if percent == 0:
                self.stop("Velocidade zero")
                return self.status()
            if self.last_frame is None or time.monotonic()-self.last_frame > self.timeout or not self.ready:
                raise ValueError("Aguarde a câmera ativa e a faixa vazia antes de ligar/alterar a velocidade")
            if action == "speed" and not self.running:
                raise ValueError("Motor parado; use o botão Ligar")
            try:
                reverse = self.cfg["reverse"]
                if self.cfg["direction_control"] == "gpio":
                    self._call("write", self.cfg["in1_gpio"], int(not reverse))
                    self._call("write", self.cfg["in2_gpio"], int(reverse))
                self._call("set_PWM_dutycycle", self.cfg["pwm_gpio"], round(percent*10))
                self.running, self.percent = True, percent
                self.message = "Comando PWM aplicado; velocidade física não medida"
                log.info("%sMotor: PWM %.1f%%", "SIMULAÇÃO " if self.dry_run else "", percent)
            except Exception as exc:
                self.fault = str(exc)
                self.stop("Falha ao comandar o motor: "+str(exc))
                self.available = False
                raise ValueError(self.message) from exc
            return self.status()

    def stop(self, reason="Parado"):
        with self.lock:
            # Não toca em GPIO quando não configurado ou sem conexão inicial.
            if self.cfg["enabled"] and (self.dry_run or self.pi is not None):
                try:
                    self._clear_output()
                except Exception as exc:
                    self.fault = str(exc)
                    self.available = False
                    reason += "; não foi possível confirmar os comandos de parada: "+str(exc)
            self.running, self.percent = False, 0
            self.ready = False
            self.message = reason

    def status(self):
        with self.lock:
            return {"configured": self.cfg["enabled"], "available": self.available,
                    "simulated": self.dry_run, "running": self.running,
                    "percent": self.percent, "message": self.message, "fault": self.fault,
                    "channel": self.cfg["channel"], "pwm_gpio": self.cfg["pwm_gpio"]}

    def _watchdog(self):
        while not self.stopping.wait(.05):
            with self.lock:
                if self.running and (self.last_frame is None or time.monotonic()-self.last_frame > self.timeout):
                    self.stop("Câmera sem quadros; PWM zerado. Use Ligar novamente após conferir a esteira")
                    log.warning("Motor parado por ausência de quadros da câmera")

    def close(self):
        self.stopping.set()
        self.stop("Programa encerrado")
        if self.thread is not None:
            self.thread.join(timeout=2)
        if self.pi is not None:
            self.pi.stop()
            self.pi = None
