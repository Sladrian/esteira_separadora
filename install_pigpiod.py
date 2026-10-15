#!/usr/bin/env python3
"""Fallback para Pi 3/Trixie: compila somente o daemon oficial pigpio v79."""
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

URL = "https://codeload.github.com/joan2937/pigpio/tar.gz/refs/tags/v79"
SHA256 = "c5337c0b7ae888caf0262a6f476af0e2ab67065f7650148a0b21900b8d1eaed7"


def main():
    model = Path("/proc/device-tree/model").read_text().rstrip("\x00")
    if "Raspberry Pi 3 Model B" not in model:
        raise RuntimeError("Este fallback foi preparado para Raspberry Pi 3B")
    if shutil.which("pigpiod"):
        print("pigpiod já instalado; preservando a instalação existente")
        return
    print("Baixando pigpio v79 oficial; verificando SHA-256", flush=True)
    with urlopen(URL, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError("SHA-256 inesperado; nenhuma instalação foi feita")
    required = ("pigpio.c", "pigpio.h", "pigpiod.c", "command.c", "command.h", "custom.cext")
    with tempfile.TemporaryDirectory(prefix="esteira-pigpio-") as temp:
        directory = Path(temp)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
            for name in required:
                member = source.getmember("pigpio-79/"+name)
                if not member.isfile():
                    raise RuntimeError("Arquivo fonte inválido: "+name)
                with source.extractfile(member) as stream:
                    (directory/name).write_bytes(stream.read())
        print("Compilando somente pigpiod, sem compilar OpenCV", flush=True)
        subprocess.run(["gcc", "-O2", "-pthread", "-o", "pigpiod", "pigpiod.c", "pigpio.c",
                        "command.c", "-lrt"], cwd=directory, check=True, timeout=600)
        subprocess.run(["sudo", "install", "-m", "0755", str(directory/"pigpiod"),
                        "/usr/local/bin/pigpiod"], check=True)
    print("Daemon instalado em /usr/local/bin/pigpiod. Nenhum GPIO foi acionado.")


if __name__ == "__main__":
    main()
