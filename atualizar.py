#!/usr/bin/env python3
"""Instala este pacote em uma pasta existente, preservando config.json e backups."""
import argparse
from datetime import datetime
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    target = args.destino.expanduser().resolve()
    if source == target or not (target/"config.json").is_file():
        parser.error("Extraia o pacote em OUTRA pasta e indique a pasta existente que contém config.json")
    files = sorted([p for p in source.iterdir() if p.is_file() and p.suffix in (".py", ".html", ".md")] +
                   [p for p in (source/"tests").iterdir() if p.suffix in (".py", ".json")])
    with open("/tmp/esteira-separadora.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Encerre a execução/câmera antes: python3 controle.py parar, ou Ctrl+C")
        config_before = (target/"config.json").read_bytes()
        with tempfile.TemporaryDirectory(prefix="esteira-atualizacao-") as directory:
            stage = Path(directory)
            for path in files:
                relative = path.relative_to(source)
                (stage/relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, stage/relative)
            (stage/"config.json").write_bytes(config_before)
            env = dict(os.environ, PYTHONPATH=str(stage))
            subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                           cwd=stage, env=env, check=True)
            subprocess.run([sys.executable, "app.py", "config"], cwd=stage, env=env, check=True)
            backup = target.parent/"esteira-backups"/datetime.now().strftime("continua-%Y%m%d-%H%M%S-%f")
            backup.mkdir(parents=True)
            (backup/"config.json").write_bytes(config_before)
            installed = []
            try:
                for path in files:
                    relative = path.relative_to(source)
                    destination = target/relative
                    existed = destination.exists()
                    if existed:
                        (backup/relative).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(destination, backup/relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_name(destination.name+".atualizando")
                    shutil.copy2(stage/relative, temporary)
                    temporary.replace(destination)
                    installed.append((relative, existed))
                if (target/"config.json").read_bytes() != config_before:
                    raise RuntimeError("A configuração mudou durante a atualização")
            except Exception:
                for relative, existed in reversed(installed):
                    if existed:
                        shutil.copy2(backup/relative, target/relative)
                    else:
                        (target/relative).unlink(missing_ok=True)
                raise
        print(f"Atualização instalada. Cores, ROI e tempos preservados. Backup: {backup}")
        print("Não foram iniciados câmera nem servos. Consulte TESTE-NA-ESTEIRA.md para começar.")


if __name__ == "__main__":
    raise SystemExit(main())
