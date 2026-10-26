#!/usr/bin/env python3
"""Inicia/para a esteira em segundo plano no Raspberry, sobrevivendo à saída do SSH."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
STATE = BASE / ".esteira-processo.json"
LOG = BASE / "esteira.log"


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return fields[19] if fields[0] != "Z" and str(BASE/"app.py").encode() in command else None
    except (OSError, IndexError):
        return None


def running(state):
    return bool(state and identity(state["pid"]) == state["identity"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("iniciar", "parar", "status"))
    parser.add_argument("--simular", action="store_true", help="Reconhece sem enviar pulsos")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with (BASE/".esteira-controle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = json.loads(STATE.read_text())
        except (OSError, ValueError):
            state = None
        if args.command == "iniciar":
            if running(state):
                print(f"Já está rodando (PID {state['pid']}). Use status para consultar o painel.")
                return 0
            command = [sys.executable, "-u", str(BASE/"app.py"), "run", "--web", "--port", str(args.port),
                       "--log-file", str(LOG)]
            if not args.simular:
                command.append("--live")
            process = subprocess.Popen(command, cwd=BASE, start_new_session=True,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(30):
                token = identity(process.pid)
                if token or process.poll() is not None:
                    break
                time.sleep(.1)
            if not token:
                print("Não foi possível iniciar. Confira esteira.log e se outra instância está aberta.")
                return 1
            STATE.write_text(json.dumps({"pid": process.pid, "identity": token}), encoding="utf-8")
            print(f"Iniciado em {'SIMULAÇÃO' if args.simular else 'MODO REAL'}, PID {process.pid}.")
            print("Continua após fechar o SSH. Aguarde a câmera iniciar e use: python3 controle.py status")
            return 0
        if args.command == "parar":
            if not running(state):
                print("Nenhuma execução em segundo plano registrada está ativa.")
                return 0
            os.kill(state["pid"], signal.SIGTERM)
            for _ in range(100):
                if not running(state):
                    print("Programa encerrado. Confira fisicamente a parada da esteira e dos braços.")
                    return 0
                time.sleep(.1)
            print("Encerramento solicitado; câmera ainda está liberando recursos. Confira status.")
            return 1
        print("RODANDO" if running(state) else "PARADO")
        if LOG.exists():
            print("\n".join(LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
