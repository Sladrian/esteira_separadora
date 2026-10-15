#!/usr/bin/env python3
"""Esteira separadora: CLI para Raspberry Pi OS Lite. Veja LEIA-ME.md."""
import argparse
from contextlib import ExitStack, contextmanager
import json
import logging
from pathlib import Path
import signal
import sys
import time

from core import Controller, Debouncer, SorterFault, load_config
from hardware import Camera, Servos

BASE = Path(__file__).resolve().parent
log = logging.getLogger("esteira")


@contextmanager
def process_lock():
    # Evita que dois programas desta entrega comandem os mesmos GPIOs.
    import fcntl
    with open("/tmp/esteira-separadora.lock", "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SorterFault("Outra instância da esteira já está aberta") from None
        yield


def save_config(path, cfg):
    path = Path(path)
    # Verifica antes de substituir; backup do estado imediatamente anterior.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    load_config(tmp)
    path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    tmp.replace(path)


def run(cfg, args):
    import cv2
    from vision import Vision, annotate
    cv2.setNumThreads(1)
    stop = False
    def stop_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    with ExitStack() as stack:
        camera = Camera(cfg["camera"])
        stack.callback(camera.close)
        servos = Servos(cfg["servos"], dry_run=not args.live)
        stack.callback(servos.close)
        # Recolhe os braços antes de começar. Ligue a esteira somente após PRONTO.
        time.sleep(max(s["return_s"] for s in cfg["servos"].values()))
        control = Controller(cfg, servos)
        control.start()
        stack.callback(control.close)
        detector, debouncer = Vision(cfg["vision"]), Debouncer(cfg["vision"])
        last_sensor_ts = None
        started = time.monotonic()
        report_at = started + 10
        frames = 0
        log.info("PRONTO | %s | comece com a faixa vazia; Ctrl+C encerra",
                 "SERVOS ATIVOS" if args.live else "SIMULAÇÃO, sem GPIO")
        while not stop:
            frame, captured_at, meta = camera.read()
            if stop:
                break
            sensor_ts = meta.get("SensorTimestamp")
            if sensor_ts is None or (last_sensor_ts is not None and sensor_ts <= last_sensor_ts):
                raise SorterFault("Timestamp da câmera ausente ou repetido; pare a esteira")
            last_sensor_ts = sensor_ts
            control.beat()
            label, fractions, _, _ = detector.inspect(frame)
            event = debouncer.update(label, captured_at)
            if event:
                color, first_seen = event
                destination = control.submit(color, first_seen)
                log.info("Tampinha %s -> %s | reconhecida, destino programado", color, destination)
                if color == "desconhecida":
                    log.warning("Cor sem confiança: segue reto. Verifique o recipiente final")
            frames += 1
            if time.monotonic() >= report_at:
                log.info("FPS médio %.1f | contagens de detecção %s", frames/(time.monotonic()-started),
                         dict(control.counts))
                report_at = time.monotonic()+10
            if args.preview:
                cv2.imshow("Esteira - Q para sair", annotate(frame, cfg["vision"]["roi"], label, fractions))
                if cv2.waitKey(1) & 0xff == ord("q"):
                    stop = True
            if args.seconds and time.monotonic()-started >= args.seconds:
                stop = True
        control.check()
        log.info("Encerrando; detecções: %s. Pare a esteira antes de reiniciar.", dict(control.counts))
        if args.preview:
            cv2.destroyAllWindows()


def photograph(cfg, args):
    import cv2
    import numpy as np
    from vision import Vision, annotate, learn_ranges
    with ExitStack() as stack:
        camera = Camera(cfg["camera"])
        stack.callback(camera.close)
        frame, _, meta = camera.read()
        if args.command == "balance":
            gains = meta.get("ColourGains")
            if gains is None:
                raise SorterFault("Balanço de branco não disponível")
            cfg["camera"]["colour_gains"] = list(gains)
            save_config(args.config, cfg)
            log.info("Balanço salvo: %s. Retire o papel branco antes de aprender as cores.", gains)
            return
        detector = Vision(cfg["vision"])
        label, fractions, hsv, mask = detector.inspect(frame)
        log.info("Resultado: %s | frações: %s", label or "vazio", fractions)
        log.info("Exposição %s us; ganho %s; ColourGains %s", meta.get("ExposureTime"),
                 meta.get("AnalogueGain"), meta.get("ColourGains"))
        if args.command == "learn":
            if args.color not in cfg["vision"]["colors"]:
                raise ValueError("Cor não configurada: " + args.color)
            ranges = learn_ranges(hsv, mask, cfg["vision"]["min_object_pixels"])
            cfg["vision"]["colors"][args.color]["ranges"] = ranges
            if cfg["camera"].get("colour_gains") is None:
                cfg["camera"]["colour_gains"] = list(meta["ColourGains"])
            save_config(args.config, cfg)
            label, fractions, _, _ = Vision(cfg["vision"]).inspect(frame)
            log.info("Faixas de %s salvas: %s | classificação após ajuste: %s", args.color, ranges, label)
            if label != args.color:
                log.warning("Amostra não ficou distinta das outras cores; ajuste as faixas e repita")
        if np.count_nonzero(mask):
            log.info("HSV mediano do objeto: %s", np.median(hsv[mask > 0], axis=0).tolist())
        path = Path(args.output)
        if not cv2.imwrite(str(path), annotate(frame, cfg["vision"]["roi"], label, fractions)):
            raise OSError("Não foi possível salvar " + str(path))
        log.info("Foto salva em %s", path.resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(BASE / "config.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="Reconhecimento contínuo; simulação por padrão")
    p.add_argument("--live", action="store_true", help="Aciona os servos físicos")
    p.add_argument("--preview", action="store_true", help="Exige ambiente gráfico; desnecessário no Lite")
    p.add_argument("--seconds", type=float, default=0, help="0 = até Ctrl+C")
    p = sub.add_parser("photo", help="Uma foto anotada, sem movimentar os servos")
    p.add_argument("--output", default="calibracao.jpg")
    p = sub.add_parser("learn", help="Aprende a faixa HSV de uma tampinha parada na ROI")
    p.add_argument("color")
    p.add_argument("--output", default="calibracao.jpg")
    sub.add_parser("balance", help="Fixa o branco: papel branco sob a câmera, esteira parada")
    p = sub.add_parser("servo", help="Testa uma posição; use inicialmente sem a espátula")
    p.add_argument("side", choices=("direita", "esquerda"))
    p.add_argument("pulse_us", type=int)
    p.add_argument("--live", action="store_true")
    sub.add_parser("check", help="Valida config, espaço e bibliotecas; não aciona GPIO")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = load_config(args.config)
        if args.command == "check":
            import shutil
            import cv2
            import numpy
            import picamera2
            import pigpio
            free = shutil.disk_usage(BASE).free/(1024**3)
            print(f"Config OK | OpenCV {cv2.__version__} | NumPy {numpy.__version__} | livre {free:.2f} GiB")
            print("Imports Picamera2/pigpio OK. Câmera e GPIO ainda precisam de testes físicos.")
            if free < 1:
                log.warning("Menos de 1 GiB livre; libere espaço antes de instalar atualizações")
            return 0
        if args.command == "servo" and not 1000 <= args.pulse_us <= 2000:
            raise ValueError("Neste projeto use pulsos entre 1000 e 2000 us")
        if args.command == "run" and args.seconds < 0:
            raise ValueError("--seconds não pode ser negativo")
        with process_lock():
            if args.command == "run":
                run(cfg, args)
            elif args.command == "servo":
                servos = Servos(cfg["servos"], dry_run=not args.live)
                try:
                    servos.set_pulse(args.side, args.pulse_us)
                    time.sleep(1)
                finally:
                    servos.close()
            else:
                # balance deve recalcular AWB mesmo com ganhos já salvos.
                if args.command == "balance":
                    cfg["camera"]["colour_gains"] = None
                photograph(cfg, args)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log.error("INTERROMPIDO: %s. PARE A ESTEIRA; o programa não controla o motor dela.", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
