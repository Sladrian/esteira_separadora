#!/bin/sh
# Execute com: bash install.sh (Raspberry Pi OS; não Windows).
set -eu
cd "$(dirname "$0")"
if ! test -r /proc/device-tree/model; then
    echo "Este instalador deve ser executado no Raspberry Pi." >&2
    exit 1
fi
model=$(tr -d '\000' < /proc/device-tree/model)
case "$model" in
    *"Raspberry Pi 3 Model B"*) ;;
    *) echo "Alvo deste projeto: Raspberry Pi 3B. Detectado: $model" >&2; exit 1 ;;
esac
echo "Instalando pacotes prontos via APT em: $model"
echo "Verifique a estimativa de espaço exibida pelo APT (cartão de 16 GB)."
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-picamera2 python3-opencv python3-numpy python3-pigpio
if ! command -v pigpiod >/dev/null 2>&1; then
    pigpio_candidate=$(LC_ALL=C apt-cache policy pigpio | awk '/Candidate:/ {print $2; exit}')
    if [ -n "$pigpio_candidate" ] && [ "$pigpio_candidate" != '(none)' ]; then
        sudo apt-get install -y --no-install-recommends pigpio
    else
        echo 'Daemon pigpiod sem pacote nesta versão. Usando fonte oficial v79 para Pi 3.'
        sudo apt-get install -y --no-install-recommends gcc libc6-dev
        python3 install_pigpiod.py
    fi
fi
python3 app.py check
echo "Instalação concluída. Siga a calibração em LEIA-ME.md antes de usar run --live."
