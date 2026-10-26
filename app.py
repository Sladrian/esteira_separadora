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
import copy
import threading

from core import SorterFault, load_config
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


def command_motor(control, motor, reset_tracking, action, percent):
    if action == "stop":
        state = motor.command("stop")
        reset_tracking.set()
        control.pause("parada da esteira pelo painel")
        return state
    if action not in ("start", "speed"):
        raise ValueError("Ação do motor inválida")
    with control.condition:
        # O motor pode partir/mudar com objetos presentes. Eventos calculados
        # com a velocidade anterior são cancelados antes de liberar a fila.
        state = motor.command(action, percent)
        control.reset_schedule("velocidade alterada; tempos anteriores cancelados")
        reset_tracking.set()
        return state


def run(cfg, args):
    import cv2
    from continuous import ContinuousController, Tracker
    from vision import Vision, annotate_objects
    from settings import changed_config
    from motor import Motor
    cv2.setNumThreads(1)
    stop = False
    def stop_signal(*_):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    with ExitStack() as stack:
        cfg = copy.deepcopy(cfg)
        servos = Servos(cfg["servos"], dry_run=not args.live, tolerate_errors=True)
        stack.callback(servos.close)
        motor = Motor(cfg.get("motor", {}), cfg["servos"], dry_run=not args.live,
                      timeout=cfg["timing"]["camera_timeout_s"])
        stack.callback(motor.close)
        time.sleep(max(s["return_s"] for s in cfg["servos"].values()))
        control = ContinuousController(cfg, servos)
        control.start()
        stack.callback(control.close)
        detector, tracker = Vision(cfg["vision"]), Tracker(cfg["vision"])
        detections = []
        panel = None
        reset_tracking = threading.Event()
        def motor_command(action, percent):
            return command_motor(control, motor, reset_tracking, action, percent)

        def apply_changes(changes):
            nonlocal cfg, detector, tracker
            updated = changed_config(cfg, changes)
            roi_changed = updated["vision"]["roi"] != cfg["vision"]["roi"]
            positions_changed = any(updated["servos"][side][key] != cfg["servos"][side][key]
                                    for side in cfg["servos"] for key in ("rest_us", "push_us"))
            # Posições ou ROI só mudam após a última passagem alcançar as estações.
            with control.condition:
                if (roi_changed or positions_changed) and (detections or control.snapshot()["busy"]):
                    raise ValueError("Pare de alimentar e aguarde a faixa e os acionamentos ficarem vazios para mudar área/posições")
                save_config(args.config, updated)
                cfg = updated
                control.reconfigure(cfg)
                servos.cfg = cfg["servos"]
                if positions_changed:
                    servos.park()
                if roi_changed:
                    detector, tracker = Vision(cfg["vision"]), Tracker(cfg["vision"])
                log.info("Ajustes salvos; novos tempos valem para as próximas tampinhas")
            return cfg

        if args.web:
            from panel import Panel
            panel = Panel(args.port, cfg, apply_changes, motor_command=motor_command, motor_status=motor.status)
            stack.callback(panel.close)
            log.info("PAINEL: %s", panel.url)
            log.info("Via SSH no notebook: ssh -L %s:127.0.0.1:%s admin@IP_DO_RASPBERRY", args.port, args.port)
        started = time.monotonic()
        report_at = started + 10
        publish_at = started
        frames = 0
        camera = None
        camera_cfg = copy.deepcopy(cfg["camera"])
        last_sensor_ts = last_captured_at = None
        try:
            while not stop and not (args.seconds and time.monotonic()-started >= args.seconds):
                control.check()
                try:
                    if camera is None:
                        camera = Camera(camera_cfg)
                        # A reconexão reutiliza a mesma fonte e a mesma calibração.
                        camera_cfg["backend"] = camera.kind
                        if camera.kind == "usb":
                            camera_cfg["usb_device"] = camera.camera.device
                        tracker = Tracker(cfg["vision"])
                        last_sensor_ts = last_captured_at = None
                        log.info("PRONTO | %s | comece com faixa vazia; execução contínua; Ctrl+C encerra",
                                 "MODO REAL: confira estado dos canais" if args.live else "SIMULAÇÃO, sem GPIO")
                    frame, captured_at, meta = camera.read()
                    if stop:
                        break
                    sensor_ts = meta.get("FrameSequence", meta.get("SensorTimestamp"))
                    if sensor_ts is None or (last_sensor_ts is not None and sensor_ts <= last_sensor_ts):
                        raise SorterFault("Identificador de quadro ausente ou repetido")
                except Exception as exc:
                    motor.stop("Falha na câmera; use Ligar novamente após conferir a esteira")
                    control.pause(str(exc))
                    if panel:
                        panel.message("Sem câmera; tentando reconectar. Confira a correia.")
                    if camera is not None:
                        try:
                            camera.close()
                        except Exception:
                            log.exception("Falha ao liberar câmera")
                    camera = None
                    # Espera interrompível; nunca acumula movimentos durante reconexão.
                    until = time.monotonic()+3
                    while not stop and time.monotonic() < until:
                        if args.seconds and time.monotonic()-started >= args.seconds:
                            break
                        time.sleep(.1)
                    continue
                if last_captured_at is not None and captured_at-last_captured_at > cfg["timing"]["camera_timeout_s"]:
                    motor.stop("Lacuna na captura; confira a esteira antes de ligar novamente")
                    control.pause("lacuna na captura")
                    tracker = Tracker(cfg["vision"])
                if reset_tracking.is_set():
                    tracker = Tracker(cfg["vision"], require_empty=not motor.status()["running"])
                    reset_tracking.clear()
                last_sensor_ts, last_captured_at = sensor_ts, captured_at
                control.beat()
                detections = detector.detect(frame)
                for color, first_seen in tracker.update(detections, captured_at):
                    if motor.cfg["enabled"] and not motor.status()["running"]:
                        with control.condition:
                            control.counts[color] += 1
                        destination, result = "sem desvio", "esteira parada; leitura continua"
                    else:
                        destination, result = control.submit(color, first_seen)
                    log.info("Tampinha %s -> %s | %s", color, destination, result)
                motor.beat(tracker.ready)
                frames += 1
                now = time.monotonic()
                if now >= report_at:
                    log.info("FPS médio %.1f | estado %s | servos %s", frames/max(.01, now-started),
                             control.snapshot(), servos.status())
                    report_at = now+10
                if panel:
                    panel.process_commands()
                    if now >= publish_at:
                        state = control.snapshot()
                        state.update(message="Leitura ativa" if tracker.ready else (
                            "Câmera ativa; pode ligar a esteira com objetos na faixa" if motor.cfg["enabled"] else "Aguardando faixa vazia para iniciar"),
                                     fps=round(frames/max(.01, now-started), 1), servos=servos.status())
                        if state["suspended"]:
                            state["message"] = "Leitura ativa; aguardando as estações liberarem para voltar a desviar"
                        panel.publish(annotate_objects(frame, cfg["vision"]["roi"], detections), state)
                        publish_at = now+.25
                if args.preview:
                    cv2.imshow("Esteira - Q para sair", annotate_objects(frame, cfg["vision"]["roi"], detections))
                    if cv2.waitKey(1) & 0xff == ord("q"):
                        stop = True
        finally:
            motor.stop("Programa encerrado")
            control.pause("encerramento")
            if camera is not None:
                camera.close()
            if args.preview:
                cv2.destroyAllWindows()
        log.info("Encerrando; estado %s. Pare a correia antes de reiniciar.", control.snapshot())


def photograph(cfg, args):
    import cv2
    import numpy as np
    from vision import Vision, annotate, learn_ranges
    with ExitStack() as stack:
        camera = Camera(cfg["camera"])
        stack.callback(camera.close)
        frame, _, meta = camera.read()
        if args.command == "balance":
            if camera.kind == "usb":
                raise SorterFault("USB: ajuste/fixe o branco com os controles UVC da câmera e depois execute learn para as três cores")
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
            if cfg["camera"].get("colour_gains") is None and meta.get("ColourGains") is not None:
                cfg["camera"]["colour_gains"] = list(meta["ColourGains"])
            label, fractions, _, _ = Vision(cfg["vision"]).inspect(frame)
            if label != args.color:
                raise ValueError("Amostra não ficou distinta das outras cores; configuração preservada, confira a tampinha e a luz")
            save_config(args.config, cfg)
            log.info("Faixas de %s salvas: %s | classificação após ajuste: %s", args.color, ranges, label)
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
    p.add_argument("--web", action="store_true", help="Painel da câmera e ajustes no navegador via túnel SSH")
    p.add_argument("--port", type=int, default=8080, help="Porta local do painel (padrão 8080)")
    p.add_argument("--log-file", help="Registro com rotação: 2 MB por arquivo, três arquivos")
    p = sub.add_parser("ajustar", help="Salva área e tempos sem editar JSON; encerre run antes")
    p.add_argument("--roi", nargs=4, type=int, metavar=("X", "Y", "LARGURA", "ALTURA"))
    p.add_argument("--servo", choices=("direita", "esquerda"))
    for name in ("tempo", "antecipacao", "permanencia", "retorno"):
        p.add_argument("--"+name, type=float)
    p.add_argument("--recolhido", type=int)
    p.add_argument("--desvio", type=int)
    sub.add_parser("config", help="Mostra área, GPIO e tempos salvos")
    p = sub.add_parser("motor", help="Configura a ponte L298N sem ligar o motor; encerre run antes")
    p.add_argument("action", choices=("configurar", "desativar"))
    p.add_argument("--canal", choices=("A", "B"), default="A")
    p.add_argument("--pwm", type=int, default=17)
    p.add_argument("--in1", type=int, default=27)
    p.add_argument("--in2", type=int, default=22)
    p.add_argument("--inverter", action="store_true")
    p.add_argument("--sentido-fixo", action="store_true", help="IN1/IN2 já definidos externamente; controla somente ENA/ENB")
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
    if args.command == "run" and args.log_file:
        from logging.handlers import RotatingFileHandler
        handler = RotatingFileHandler(args.log_file, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
    try:
        cfg = load_config(args.config)
        if args.command == "config":
            print(json.dumps({"roi": cfg["vision"]["roi"], "servos": cfg["servos"], "motor": cfg.get("motor", {"enabled": False})}, indent=2, ensure_ascii=False))
            return 0
        if args.command == "check":
            import shutil
            import cv2
            import numpy
            if cfg["camera"].get("backend", "auto") == "csi":
                import picamera2
            import pigpio
            free = shutil.disk_usage(BASE).free/(1024**3)
            print(f"Config OK | OpenCV {cv2.__version__} | NumPy {numpy.__version__} | livre {free:.2f} GiB")
            print("Config e pigpio OK. Picamera2 é necessário para CSI. Câmera e GPIO exigem testes físicos.")
            if free < 1:
                log.warning("Menos de 1 GiB livre; libere espaço antes de instalar atualizações")
            return 0
        if args.command == "servo" and not 1000 <= args.pulse_us <= 2000:
            raise ValueError("Neste projeto use pulsos entre 1000 e 2000 us")
        if args.command == "run" and args.seconds < 0:
            raise ValueError("--seconds não pode ser negativo")
        if args.command == "run" and not 1 <= args.port <= 65535:
            raise ValueError("Porta inválida")
        with process_lock():
            if args.command == "run":
                run(cfg, args)
            elif args.command == "motor":
                from motor import validate_motor
                settings = dict(cfg.get("motor", {}), enabled=False) if args.action == "desativar" else {
                    "enabled": True, "channel": args.canal, "pwm_gpio": args.pwm,
                    "in1_gpio": args.in1, "in2_gpio": args.in2, "reverse": args.inverter,
                    "direction_control": "fixed" if args.sentido_fixo else "gpio"}
                cfg["motor"] = validate_motor(settings, cfg["servos"])
                save_config(args.config, cfg)
                log.info("Motor configurado, sem partida: %s. Ao abrir run --live --web, use Ligar no painel", cfg["motor"])
            elif args.command == "ajustar":
                from settings import changed_config
                fields = {key: getattr(args, name) for name, key in {
                    "tempo": "travel_s", "antecipacao": "advance_s", "permanencia": "hold_after_arrival_s",
                    "retorno": "return_s", "recolhido": "rest_us", "desvio": "push_us"}.items()
                          if getattr(args, name) is not None}
                if fields and not args.servo:
                    raise ValueError("Informe --servo direita ou --servo esquerda")
                changes = {"servos": {args.servo: fields}} if fields else {}
                if args.roi is not None:
                    changes["roi"] = args.roi
                if not changes:
                    raise ValueError("Informe --roi ou --servo com um ajuste de tempo/posição")
                cfg = changed_config(cfg, changes)
                save_config(args.config, cfg)
                log.info("Ajustes salvos. ROI: %s | servos: %s", cfg["vision"]["roi"], cfg["servos"])
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
        log.error("INTERROMPIDO: %s. Confira fisicamente a parada da esteira e dos braços.", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
