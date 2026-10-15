"""Imports de hardware são locais para permitir testes sem Raspberry."""
import logging
import time
from core import SorterFault

log = logging.getLogger(__name__)


class Camera:
    def __init__(self, cfg):
        from picamera2 import Picamera2
        if not Picamera2.global_camera_info():
            raise SorterFault("Nenhuma câmera reconhecida. Confira CSI, cabo e modelo com o Pi desligado")
        self.camera = Picamera2()
        try:
            controls = {"FrameRate": cfg["fps"], "AeEnable": False,
                        "ExposureTime": cfg["exposure_us"], "AnalogueGain": cfg["analogue_gain"]}
            if cfg.get("colour_gains"):
                controls.update(AwbEnable=False, ColourGains=tuple(cfg["colour_gains"]))
            # RGB888 produz arrays B,G,R no Picamera2 (nomenclatura libcamera).
            setup = self.camera.create_video_configuration(
                main={"size": (cfg["width"], cfg["height"]), "format": "RGB888"},
                controls=controls, buffer_count=3, queue=False)
            self.camera.configure(setup)
            self.camera.start()
            time.sleep(cfg["warmup_s"])
            metadata = self.camera.capture_metadata(wait=2.0)
            if cfg["lock_white_balance"] and not cfg.get("colour_gains"):
                gains = metadata.get("ColourGains")
                if gains is None:
                    raise SorterFault("Câmera não forneceu ColourGains para fixar o branco")
                self.camera.set_controls({"AwbEnable": False, "ColourGains": gains})
            # Descarta os primeiros quadros após alterar controles.
            for _ in range(3):
                self.camera.capture_metadata(wait=2.0)
            log.info("Câmera: %s", self.camera.camera_properties.get("Model", "não identificado"))
        except BaseException:
            self.camera.close()
            raise

    def read(self):
        request = self.camera.capture_request(wait=1.0)
        try:
            frame = request.make_array("main")
            metadata = request.get_metadata()
        finally:
            request.release()
        # Referência temporal = recebimento do quadro. travel_s é calibrado
        # empiricamente usando essa referência, já incluindo a latência da câmera.
        return frame, time.monotonic(), metadata

    def close(self):
        self.camera.close()


class Servos:
    def __init__(self, cfg, dry_run=False):
        self.cfg, self.dry_run, self.pi = cfg, dry_run, None
        if not dry_run:
            import pigpio
            self.pi = pigpio.pi("127.0.0.1", 8888)
            if not self.pi.connected:
                self.pi.stop()
                raise SorterFault("pigpiod indisponível; execute: sudo pigpiod -l -f")
        try:
            self.park()
        except BaseException:
            self.close()
            raise

    def set_pulse(self, side, pulse):
        if self.pi is not None:
            result = self.pi.set_servo_pulsewidth(self.cfg[side]["gpio"], pulse)
            if result < 0:
                raise SorterFault(f"Falha no servo {side}: código {result}")
        log.info("%sservo %s: %s us", "SIMULAÇÃO " if self.dry_run else "", side, pulse)

    def park(self):
        # Tenta recolher ambos mesmo se um canal falhar.
        errors = []
        for side, s in self.cfg.items():
            try:
                self.set_pulse(side, s["rest_us"])
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise errors[0]

    def close(self):
        if self.pi is None:
            return
        # Em encerramento normal: repouso, tempo para retorno, depois remove PWM.
        try:
            self.park()
            time.sleep(max(s["return_s"] for s in self.cfg.values()))
        except Exception:
            log.exception("Não foi possível recolher os servos")
        finally:
            for s in self.cfg.values():
                try:
                    self.pi.set_servo_pulsewidth(s["gpio"], 0)
                except Exception:
                    log.exception("Falha ao remover sinal do GPIO %s", s["gpio"])
            self.pi.stop()
            self.pi = None
