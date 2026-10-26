"""Painel local da mesma execução: nenhuma segunda captura de câmera."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import threading
from urllib.parse import urlsplit


class Panel:
    def __init__(self, port, cfg, apply):
        self.lock = threading.Lock()
        self.cfg = copy.deepcopy(cfg)
        self.info = {}
        self.jpeg = None
        self.apply = apply
        self.commands = queue.Queue(maxsize=4)
        self.token = secrets.token_urlsafe(18)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, body, content_type="application/json", code=200):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

            def route(self):
                prefix = "/"+owner.token+"/"
                return self.path[len(prefix):] if self.path.startswith(prefix) else None

            def do_GET(self):
                route = self.route()
                if route == "":
                    self.reply(Path(__file__).with_name("panel.html").read_bytes(), "text/html; charset=utf-8")
                elif route == "status":
                    with owner.lock:
                        data = dict(owner.info, config=owner.cfg)
                    self.reply(json.dumps(data, ensure_ascii=False).encode())
                elif route == "frame":
                    with owner.lock:
                        jpeg = owner.jpeg
                    self.reply(jpeg or b"", "image/jpeg", 200 if jpeg else 503)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.route() != "settings":
                    self.send_error(404)
                    return
                origin = self.headers.get("Origin")
                if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 8192:
                        raise ValueError("Tamanho inválido")
                    command = {"changes": json.loads(self.rfile.read(length)), "done": threading.Event(),
                               "cancelled": False, "lock": threading.Lock()}
                    owner.commands.put_nowait(command)
                    if not command["done"].wait(5):
                        with command["lock"]:
                            command["cancelled"] = True
                        raise ValueError("Câmera indisponível ou ocupada; confira o estado e tente novamente")
                    if "error" in command:
                        raise ValueError(command["error"])
                    self.reply(b'{"ok":true}')
                except (ValueError, queue.Full) as exc:
                    self.reply(json.dumps({"error": str(exc) or "Painel ocupado"}, ensure_ascii=False).encode(), code=400)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name="painel")
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/{self.token}/"

    def process_commands(self):
        # Executado entre quadros; alterações não competem com a captura/controle.
        while True:
            try:
                command = self.commands.get_nowait()
            except queue.Empty:
                return
            with command["lock"]:
                if command["cancelled"]:
                    continue
                try:
                    cfg = self.apply(command["changes"])
                    with self.lock:
                        self.cfg = copy.deepcopy(cfg)
                except Exception as exc:
                    command["error"] = str(exc)
                command["done"].set()

    def publish(self, frame, info):
        import cv2
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with self.lock:
            self.info = info
            self.jpeg = encoded.tobytes() if ok else None

    def message(self, text):
        with self.lock:
            self.info["message"] = text

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
