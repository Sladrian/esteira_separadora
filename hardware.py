"""Imports de hardware são locais para permitir testes sem Raspberry."""
import logging
from pathlib import Path
import threading
import time
from core import SorterFault

log = logging.getLogger(__name__)


def usb_cameras():
    """Somente interfaces UVC de imagem; ignora codecs do Pi e nós de metadados."""
    devices = []
    for node in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            if (node / "device/driver").resolve().name != "uvcvideo":
                continue
            if (node / "index").read_text().strip() != "0":
                continue
            devices.append(str(Path("/dev") / node.name))
        except OSError:
            continue
    return devices


class Camera:
    """Escolhe a fonte ao iniciar; nunca troca de câmera durante uma passagem."""
    def __init__(self, cfg):
        backend = cfg.get("backend", "auto")
        if backend not in ("auto", "usb", "csi"):
            raise ValueError("Backend de câmera deve ser auto, usb ou csi")
        self.camera = None
        if backend != "csi":
            devices = usb_cameras()
            requested = cfg.get("usb_device")
            if requested:
                # O caminho estável by-id pode apontar para um /dev/video diferente após reinício.
                devices = [requested] if any(Path(d).resolve() == Path(requested).resolve()
                                            for d in devices) else []
                if not devices:
                    raise SorterFault("Câmera USB configurada não encontrada: " + requested)
            if len(devices) > 1:
                raise SorterFault("Mais de uma câmera USB; escolha camera.usb_device: " + ", ".join(devices))
            if devices:
                try:
                    self.camera = USBCamera(cfg, devices[0])
                except SorterFault as exc:
                    if backend == "usb" or requested:
                        raise
                    log.warning("USB indisponível (%s); tentando CSI ao iniciar", exc)
            if self.camera is None and backend == "usb":
                raise SorterFault("Nenhuma câmera reconhecida por USB; confira conexão, permissões e driver UVC")
        if self.camera is None:
            self.camera = CSICamera(cfg)
        self.kind = self.camera.kind
        log.info("Fonte selecionada: %s. Confira cores e tempos com esta câmera.", self.kind.upper())

    def read(self):
        return self.camera.read()

    def close(self):
        self.camera.close()


class USBCamera:
    kind = "usb"

    def __init__(self, cfg, device):
        import cv2
        self.cv2, self.cfg, self.device = cv2, cfg, device
        self.condition = threading.Condition()
        self.stopping = False
        self.error = None
        self.latest = None
        self.sequence = self.delivered = 0
        self.camera = cv2.VideoCapture(device, cv2.CAP_V4L2)
        if not self.camera.isOpened():
            self.camera.release()
            raise SorterFault("Não foi possível abrir " + device + "; confira permissões e outros apps")
        try:
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
            self.camera.set(cv2.CAP_PROP_FPS, cfg["fps"])
            self.camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            # Um produtor contínuo evita acumular quadros antigos durante o processamento.
            self.thread = threading.Thread(target=self._capture, daemon=True, name="camera-usb")
            self.thread.start()
            time.sleep(cfg["warmup_s"])
            self.read()  # Exige imagem válida antes de anunciar a câmera como pronta.
            log.info("Câmera USB: %s | exposição e branco controlados pelo driver UVC", device)
        except BaseException:
            self.close()
            raise

    def _capture(self):
        try:
            while not self.stopping:
                ok, frame = self.camera.read()
                captured_at = time.monotonic()
                if not ok or frame is None:
                    raise SorterFault("Câmera USB sem imagem; confira o cabo e pare a esteira")
                if frame.shape[:2] != (self.cfg["height"], self.cfg["width"]):
                    frame = self.cv2.resize(frame, (self.cfg["width"], self.cfg["height"]))
                with self.condition:
                    self.sequence += 1
                    self.latest = frame, captured_at, {
                        "FrameSequence": self.sequence, "TimestampSource": "host-receive", "CameraBackend": "usb"}
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = exc
                self.condition.notify_all()
        finally:
            self.camera.release()

    def read(self):
        with self.condition:
            ready = self.condition.wait_for(
                lambda: self.sequence > self.delivered or self.error or self.stopping, timeout=1.0)
            if self.error:
                raise SorterFault(str(self.error))
            if not ready or self.stopping:
                raise SorterFault("Timeout da câmera USB; pare a esteira")
            self.delivered = self.sequence
            return self.latest

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        thread = getattr(self, "thread", None)
        if thread is None:
            self.camera.release()
        else:
            thread.join(timeout=1.2)
            if thread.is_alive():
                # Não chama release em paralelo com um read travado no driver.
                log.warning("Driver USB não encerrou a captura; finalize o processo antes de reabrir")


class CSICamera:
    kind = "csi"

    def __init__(self, cfg):
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise SorterFault("Nenhuma câmera reconhecida por USB e Picamera2 indisponível para CSI") from exc
        cameras = [info for info in Picamera2.global_camera_info()
                   if "usb" not in str(info.get("Id", "")).lower()]
        if not cameras:
            raise SorterFault("Nenhuma câmera reconhecida. Confira USB ou CSI, cabo e modelo com o Pi desligado")
        self.camera = Picamera2(cameras[0].get("Num", 0))
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
    def __init__(self, cfg, dry_run=False, tolerate_errors=False):
        self.cfg, self.dry_run, self.pi = cfg, dry_run, None
        self.tolerate_errors = tolerate_errors
        self.disabled = {}
        self.error = None
        if not dry_run:
            try:
                import pigpio
                self.pi = pigpio.pi("localhost", 8888)
                if not self.pi.connected:
                    self.pi.stop()
                    self.pi = None
                    raise SorterFault("pigpiod indisponível; execute: sudo pigpiod -l -f")
            except Exception as exc:
                self.error = str(exc)
                if not tolerate_errors:
                    raise
                self.disabled = {side: self.error for side in cfg}
                log.warning("SEM ACIONAMENTO: %s. Reconhecimento continua; reinicie o programa após corrigir", exc)
        try:
            self.park()
        except BaseException:
            self.close()
            raise

    def set_pulse(self, side, pulse):
        if side in self.disabled:
            return False
        try:
            if self.pi is not None:
                result = self.pi.set_servo_pulsewidth(self.cfg[side]["gpio"], pulse)
                if result < 0:
                    raise SorterFault(f"Falha no servo {side}: código {result}")
        except Exception as exc:
            if not self.tolerate_errors:
                raise
            # Tenta retirar o sinal do canal que falhou, sem afetar o outro.
            try:
                self.pi.set_servo_pulsewidth(self.cfg[side]["gpio"], 0)
            except Exception:
                pass
            self.disabled[side] = str(exc)
            log.warning("SEM ACIONAMENTO no servo %s: %s; leitura continua. Reinicie após corrigir", side, exc)
            return False
        log.info("%sservo %s: %s us", "SIMULAÇÃO " if self.dry_run else "", side, pulse)
        return True

    def status(self):
        return {side: ("simulado" if self.dry_run else "indisponível" if side in self.disabled else "sinal habilitado (sem retorno)")
                for side in self.cfg}

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
