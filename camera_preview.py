"""Prévia da câmera por HTTP local; acesse do notebook por um túnel SSH."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import signal
import threading
import time

from app import process_lock
from core import load_config, SorterFault
from hardware import Camera
from vision import Vision, annotate

PAGE = '''<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Câmera da esteira</title><style>
*{box-sizing:border-box}body{margin:0;background:#161819;color:#f4f2ed;font:16px system-ui,sans-serif}
main{max-width:1040px;margin:32px auto;padding:0 24px}header{display:flex;justify-content:space-between;align-items:center;gap:20px}
h1{font-size:25px;font-weight:550;margin:0 0 8px}p{color:#c5c8c8;line-height:1.5}img{width:100%;display:block;background:#000;border:1px solid #434749;margin:20px 0}
button{background:#f4f2ed;color:#171919;border:0;padding:12px 18px;border-radius:6px;cursor:pointer;font:inherit}
button:disabled{opacity:.5}#status{color:#bcd8b8;font-size:14px}strong{color:#fff}
</style><main><header><div><h1>Câmera da esteira</h1><div id="status">Conectando à câmera…</div></div>
<button id="stop">Encerrar câmera</button></header><img id="camera" src="stream" alt="Prévia ao vivo da câmera com a área de leitura marcada">
<p><strong>O retângulo branco é a área de leitura.</strong> Posicione a câmera para que a tampinha passe inteira por ele.</p>
<p>Ao terminar o enquadramento, clique em <strong>Encerrar câmera</strong> para liberar os comandos de calibração no SSH.</p></main>
<script>
const status=document.getElementById('status'),button=document.getElementById('stop'),image=document.getElementById('camera');
let closed=false;
async function refresh(){if(closed)return;try{const r=await fetch('status',{cache:'no-store'});if(!r.ok)throw Error();const s=await r.json();status.textContent=s.error?'Câmera interrompida: '+s.error:'Ao vivo · '+s.camera.toUpperCase()+' · '+s.fps+' quadros/s';}catch(e){status.textContent='Conexão interrompida. Confira o Raspberry.';}}
const timer=setInterval(refresh,2000);refresh();
button.onclick=async()=>{button.disabled=true;try{const r=await fetch('stop',{method:'POST'});if(!r.ok)throw Error();closed=true;clearInterval(timer);image.removeAttribute('src');status.textContent='Câmera liberada. Você já pode calibrar pelo SSH.';}catch(e){button.disabled=false;status.textContent='Não foi possível encerrar. Confira a conexão.';}};
</script></html>'''


class Preview:
    def __init__(self, camera, cfg):
        self.camera, self.cfg = camera, cfg
        self.condition = threading.Condition()
        self.stopping = False
        self.error = None
        self.jpeg = None
        self.sequence = 0
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self.capture, daemon=True)

    def capture(self):
        import cv2
        cv2.setNumThreads(1)
        detector = Vision(self.cfg['vision'])
        try:
            while not self.stopping:
                frame, _, _ = self.camera.read()
                try:
                    label, fractions, _, _ = detector.inspect(frame)
                except SorterFault:
                    label, fractions = 'uma tampinha por vez', {}
                shown = annotate(frame, self.cfg['vision']['roi'], label, fractions)
                ok, encoded = cv2.imencode('.jpg', shown, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    raise RuntimeError('Não foi possível preparar a imagem')
                with self.condition:
                    self.jpeg = encoded.tobytes()
                    self.sequence += 1
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.condition.notify_all()


def make_handler(preview, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def route(self):
            prefix = '/' + token + '/'
            return self.path[len(prefix):] if self.path.startswith(prefix) else None

        def reply(self, body, content_type='text/html; charset=utf-8'):
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.route()
            if route == '':
                self.reply(PAGE.encode('utf-8'))
            elif route == 'status':
                with preview.condition:
                    status = {'camera': preview.camera.kind, 'error': preview.error,
                              'frames': preview.sequence,
                              'fps': round(preview.sequence/max(.01, time.monotonic()-preview.started), 1)}
                self.reply(json.dumps(status).encode(), 'application/json')
            elif route == 'stream':
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                last = 0
                try:
                    while not preview.stopping:
                        with preview.condition:
                            preview.condition.wait_for(lambda: preview.sequence > last or preview.error or preview.stopping, timeout=2)
                            if preview.error or preview.stopping:
                                break
                            if preview.sequence == last:
                                continue
                            last, jpeg = preview.sequence, preview.jpeg
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() +
                                         b'\r\nX-Frame-Id: ' + str(last).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            else:
                self.send_error(404)

        def do_POST(self):
            if self.route() != 'stop':
                self.send_error(404)
                return
            # Encerra o produtor e libera a câmera antes de confirmar à interface.
            with preview.condition:
                preview.stopping = True
                preview.condition.notify_all()
            preview.thread.join(timeout=3)
            if preview.thread.is_alive():
                self.send_error(503, 'Aguarde a captura encerrar')
                return
            preview.camera.close()
            preview.released = True
            self.reply(b'{"stopped":true}', 'application/json')
            threading.Thread(target=self.server.shutdown, daemon=True).start()
    return Handler


def main():
    cfg = load_config(Path(__file__).resolve().parent / 'config.json')
    with process_lock():
        camera = Camera(cfg['camera'])
        preview = Preview(camera, cfg)
        preview.released = False
        token = secrets.token_urlsafe(18)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(preview, token))
        server.daemon_threads = True
        def stop(*_):
            with preview.condition:
                preview.stopping = True
                preview.condition.notify_all()
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            preview.thread.start()
            print('PREVIEW_READY=' + json.dumps({'port': server.server_address[1], 'token': token, 'camera': camera.kind}), flush=True)
            server.serve_forever(poll_interval=.2)
        finally:
            preview.stopping = True
            with preview.condition:
                preview.condition.notify_all()
            preview.thread.join(timeout=3)
            server.server_close()
            if not preview.released and not preview.thread.is_alive():
                camera.close()


if __name__ == '__main__':
    main()
