"""Prévia e calibração com webcam USB, usando a mesma visão do Raspberry."""
import argparse
import copy
import logging
from pathlib import Path
import sys

from core import SorterFault, load_config

BASE = Path(__file__).resolve().parent
WINDOW = "Esteira | USB | Q: sair | 1: rosa | 2: azul | 3: verde"


def select_camera(cameras, index=None, name="Brio 300"):
    matches = [c for c in cameras if (c.index == index if index is not None
                                     else name.casefold() in c.name.casefold())]
    if len(matches) != 1:
        available = ", ".join(f"{c.index}: {c.name}" for c in cameras) or "nenhuma"
        raise SorterFault(f"Selecione uma câmera conectada com --camera INDICE. Disponíveis: {available}")
    return matches[0]


def set_exposure(camera, value):
    """Unidade DirectShow: expoente inteiro em base 2 segundos."""
    import cv2
    if sys.platform != "win32":
        raise ValueError("O ajuste --exposure desta ferramenta exige DirectShow no Windows")
    if not isinstance(value, int) or not -13 <= value <= -1:
        raise ValueError("Exposição deve ser um inteiro de -13 a -1")
    previous = camera.get(cv2.CAP_PROP_EXPOSURE)
    camera.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
    accepted = camera.set(cv2.CAP_PROP_EXPOSURE, value)
    actual = camera.get(cv2.CAP_PROP_EXPOSURE)
    if not accepted or abs(actual - value) > 0.1:
        camera.set(cv2.CAP_PROP_EXPOSURE, previous)
        raise SorterFault("A câmera não confirmou a exposição solicitada")
    return actual


def save_calibration(path, cfg):
    """Grava a calibração local com validação e backup antes da troca."""
    import json
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        load_config(temporary)
        if path.exists():
            path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def calibrate(cfg, hsv, mask, color):
    from vision import Vision, learn_ranges
    candidate = copy.deepcopy(cfg)
    ranges = learn_ranges(hsv, mask, cfg["vision"]["min_object_pixels"])
    candidate["vision"]["colors"][color]["ranges"] = ranges
    # A cor aprendida precisa vencer as outras no próprio quadro amostrado.
    import cv2
    import numpy as np
    x, y, w, h = cfg["vision"]["roi"]
    frame = np.zeros((cfg["camera"]["height"], cfg["camera"]["width"], 3), np.uint8)
    frame[y:y+h, x:x+w] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    label, _, _, _ = Vision(candidate["vision"]).inspect(frame)
    if label != color:
        raise ValueError("Amostra não distingue " + color + "; confira cor, reflexos e iluminação")
    return candidate


def open_camera(index, cfg):
    import cv2
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    camera = cv2.VideoCapture(index, backend)
    try:
        if not camera.isOpened():
            raise SorterFault(f"Câmera {index} indisponível. Feche outros apps e tente --camera 0 ou --camera 1")
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, cfg["width"])
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg["height"])
        camera.set(cv2.CAP_PROP_FPS, cfg["fps"])
        return camera
    except BaseException:
        camera.release()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=None, help="Índice explícito da câmera; prevalece sobre o nome")
    parser.add_argument("--camera-name", default="Brio 300", help="Nome da câmera USB (padrão: Brio 300)")
    parser.add_argument("--exposure", type=int, choices=range(-13, 0), help="Exposição manual DirectShow; -7 = aprox. 8 ms")
    parser.add_argument("--fps", type=int, help="Taxa de captura pedida ao driver (5 a 60)")
    parser.add_argument("--config", type=Path, default=BASE / "config.notebook.json")
    parser.add_argument("--check", action="store_true", help="Valida bibliotecas e configuração sem abrir câmera")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    camera = None
    try:
        import cv2
        import time
        from vision import Vision, annotate
        from cv2_enumerate_cameras import enumerate_cameras
        if args.camera is not None and args.camera < 0:
            raise ValueError("Índice da câmera deve ser zero ou maior")
        if args.config.resolve() == (BASE / "config.json").resolve():
            raise ValueError("Use um arquivo próprio para o notebook; preserve config.json do Raspberry")
        cfg = load_config(args.config if args.config.exists() else BASE / "config.json")
        capture_settings = cfg.get("notebook_capture", {})
        exposure = args.exposure if args.exposure is not None else capture_settings.get("exposure")
        requested_fps = args.fps if args.fps is not None else capture_settings.get("fps", cfg["camera"]["fps"])
        if not isinstance(requested_fps, (int, float)) or not 5 <= requested_fps <= 60:
            raise ValueError("FPS da webcam deve ficar entre 5 e 60")
        if args.check:
            print(f"Notebook OK | OpenCV {cv2.__version__} | cores: {', '.join(cfg['vision']['colors'])}")
            return 0
        if not {"rosa", "azul", "verde"}.issubset(cfg["vision"]["colors"]):
            raise ValueError("As teclas de aprendizado exigem as cores rosa, azul e verde")
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        selected = select_camera(list(enumerate_cameras(backend)), args.camera, args.camera_name)
        camera = open_camera(selected.index, dict(cfg["camera"], fps=requested_fps))
        if exposure is not None:
            set_exposure(camera, exposure)
            print(f"Exposição manual confirmada: {1000 * 2**exposure:.1f} ms. Se escurecer, aumente a iluminação.")
        # Os ajustes da webcam acompanham as faixas HSV, sem alterar a câmera do Pi.
        cfg["notebook_capture"] = {"exposure": exposure, "fps": requested_fps}
        window = WINDOW.replace("USB", selected.name)
        print(f"Câmera {selected.index}: {selected.name} | coloque uma tampinha na faixa branca, sobre fundo neutro.")
        print("1 = aprender rosa | 2 = aprender azul | 3 = aprender verde | Q/Esc = sair")
        print(f"Calibração local: {args.config}")
        print("Mantenha a iluminação constante. Reaprenda as cores após mudar a exposição ou a luz.")
        cv2.setNumThreads(1)
        ready_at = time.monotonic() + cfg["camera"]["warmup_s"]
        keys = {ord("1"): "rosa", ord("2"): "azul", ord("3"): "verde"}
        detector = Vision(cfg["vision"])
        message, message_until = "", 0
        measured_fps, fps_frames, fps_started = 0.0, 0, time.monotonic()
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window, 960, 720)
        while True:
            ok, frame = camera.read()
            if not ok or frame is None:
                raise SorterFault("Câmera USB parou de enviar imagens; confira o cabo ou outro app usando a câmera")
            # Mantém o contrato da ROI mesmo se o driver ignorar a resolução pedida.
            frame = cv2.resize(frame, (cfg["camera"]["width"], cfg["camera"]["height"]))
            fps_frames += 1
            elapsed = time.monotonic() - fps_started
            if elapsed >= 1:
                measured_fps = fps_frames / elapsed
                fps_frames, fps_started = 0, time.monotonic()
            hsv = mask = None
            try:
                label, fractions, hsv, mask = detector.inspect(frame)
                shown = annotate(frame, cfg["vision"]["roi"], label, fractions)
            except SorterFault as exc:
                shown = annotate(frame, cfg["vision"]["roi"], "uma tampinha por vez", {})
            capture_text = f"{measured_fps:.0f} FPS"
            if exposure is not None:
                capture_text += f" | {1000 * 2**exposure:.1f} ms"
            cv2.putText(shown, capture_text, (4, 40), 0, .35, (255,255,255), 1)
            if time.monotonic() < ready_at:
                cv2.putText(shown, "Aquecendo camera...", (4, 225), 0, .4, (255,255,255), 1)
            elif time.monotonic() < message_until:
                cv2.putText(shown, message, (4, 225), 0, .4, (255,255,255), 1)
            cv2.imshow(window, shown)
            key = cv2.waitKey(1) & 0xff
            if key in (ord("q"), ord("Q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
            if key in keys and time.monotonic() >= ready_at:
                color = keys[key]
                try:
                    if hsv is None or mask is None:
                        raise ValueError("Coloque somente uma tampinha na faixa")
                    candidate = calibrate(cfg, hsv, mask, color)
                    save_calibration(args.config, candidate)
                    cfg, detector = candidate, Vision(candidate["vision"])
                    print(f"{color}: faixa aprendida e salva.")
                    message = color + " salvo"
                except (ValueError, SorterFault) as exc:
                    print(f"Não foi possível aprender {color}: {exc}")
                    message = "Amostra invalida; veja o terminal"
                message_until = time.monotonic() + 4
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logging.error("Não foi possível iniciar: %s", exc)
        return 1
    finally:
        if camera is not None:
            camera.release()
            import cv2
            cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
