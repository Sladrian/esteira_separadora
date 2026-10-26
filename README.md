# Esteira separadora de tampinhas

**26/10/2026: controle opcional do motor com L298N.** O painel inclui potência PWM,
Ligar e Parar. Começa parado, sem religar automaticamente após falha da câmera.
Veja [CONTROLE-DO-MOTOR.md](CONTROLE-DO-MOTOR.md) para ligação e configuração.

**Atualização de 19/10/2026:** execução contínua, rastreamento de múltiplos componentes,
servos com falhas isoladas e painel de câmera/área/tempos. Comece pelo
[guia para testar com a correia funcionando](TESTE-NA-ESTEIRA.md).
Esta atualização foi testada no notebook; a instalação e o ensaio físico no Pi estão pendentes.

Reconhecimento de cores com **Raspberry Pi 3B**, câmera **OV5647**, OpenCV e dois
microservos **SG90**, usando Raspberry Pi OS Lite e cartão microSD de **16 GB**.

| Cor | Destino |
|---|---|
| Rosa | Direita, acionando o servo correspondente |
| Azul | Esquerda, acionando o servo correspondente |
| Verde | Segue reto, sem movimento de desvio |

O programa trabalha com faixas HSV em imagens de 320 × 240, confirma cada passagem
em vários quadros e agenda os servos em uma thread independente. A esteira deve
ter velocidade constante e receber tampinhas separadas, em fila única.

## Instalação

No Raspberry Pi OS Lite com Picamera2 compatível (Bullseye ou posterior), execute:

```bash
git clone https://github.com/Sladrian/esteira.git ~/esteira-separadora
cd ~/esteira-separadora
bash install.sh
python3 app.py check
```

Se já existir uma pasta `~/esteira-separadora`, preserve sua configuração e os
arquivos existentes antes de escolher onde clonar o repositório.

O instalador usa pacotes APT para OpenCV, NumPy e Picamera2. No Trixie, se o daemon
`pigpiod` não estiver disponível como pacote, compila somente o daemon oficial v79,
com verificação SHA-256. Não é necessário compilar OpenCV ou instalar um desktop.

## Preparar a câmera e as cores

Também é possível usar uma webcam USB UVC no Raspberry. A seleção padrão é
automática: USB primeiro, CSI (cabo flat) se a USB não estiver disponível ao
iniciar. A fonte escolhida aparece no terminal. A troca não acontece durante
a captura normal: em uma falha, o controle cancela movimentos, tenta recolher
os servos e reconectar à mesma fonte selecionada inicialmente. A correia tem comando separado.

No bloco `camera` do `config.json`, `backend` aceita `auto`, `usb` ou `csi`.
Se houver várias webcams, defina `usb_device` com o dispositivo desejado
(por exemplo `/dev/video0` ou um caminho estável de `/dev/v4l/by-id/`).
Um dispositivo explicitamente escolhido não é substituído por outra câmera.

Os campos `exposure_us`, `analogue_gain` e `colour_gains` são próprios da CSI.
Na USB, exposição e balanço de branco são controlados pelo driver UVC; os
valores DirectShow usados no Windows não podem ser copiados diretamente para
Linux. O comando `balance` é próprio da CSI; para USB ajuste o branco nos
controles da webcam e execute os três comandos `learn` abaixo.

Ao trocar a câmera, refaça a calibração das cores e os tempos até os servos.
Na USB, o instante usado pelo programa é o recebimento do quadro no computador;
a fila da aplicação mantém somente o quadro mais recente. O atraso interno do
driver/câmera ainda precisa ser compensado no ensaio de percurso.
O suporte USB Linux e a seleção automática foram testados com câmeras simuladas
no notebook; a verificação física no Raspberry ainda está pendente.

Fixe a câmera sobre a pista, use fundo neutro e iluminação constante. Confira uma
foto antes de ajustar a região de leitura (`vision.roi` no `config.json`):

```bash
python3 app.py photo --output teste.jpg
```

Com a esteira parada, coloque uma referência branca sob a câmera e execute:

```bash
python3 app.py balance
```

Depois retire a referência e coloque uma tampinha de cada vez na região de leitura:

```bash
python3 app.py learn rosa --output rosa.jpg
python3 app.py learn azul --output azul.jpg
python3 app.py learn verde --output verde.jpg
```

**A configuração de exposição atual (~60 ms, ganho 4,0625) foi ajustada para testes
de bancada em ambiente pouco iluminado.** Para tampinhas em movimento, ilumine a
pista e reduza a exposição para evitar borrões. Recalibre as cores após esse ajuste.

## Servos e execução

| Servo | GPIO BCM | Pino físico |
|---|---:|---:|
| Saída direita | 18 | 12 |
| Saída esquerda | 19 | 35 |

Alimente os servos por fonte externa adequada, com o negativo conectado ao GND
do Raspberry. O motor da correia usa seu próprio driver e comando; este código
controla esse motor somente quando a ponte L298N está configurada conforme CONTROLE-DO-MOTOR.md.

Teste primeiro o reconhecimento sem acionar GPIO:

```bash
python3 app.py run --seconds 60
```

Após conferir alimentação, curso das espátulas, cores e tempos de percurso:

```bash
sudo pigpiod -l -f
python3 app.py run --live
```

Use `python3 app.py run --live --web` para abrir também o painel via túnel SSH.
Para manter a execução após sair do SSH, use `python3 controle.py iniciar`;
`python3 controle.py status` consulta e `python3 controle.py parar` encerra.
Os comandos `python3 app.py ajustar --servo direita --tempo 2.0` e
`python3 app.py ajustar --roi 110 105 80 25` salvam ajustes com o programa encerrado.

Use Ctrl+C para encerrar e pare também a correia. Os tempos e pulsos do
`config.json` precisam ser ajustados à montagem. Consulte o
[guia completo de montagem, alimentação e calibração](LEIA-ME.md) antes de usar os servos.

## Validação e limites

Os novos testes cobrem duas cores simultâneas, reconhecimento após uma desconhecida,
conflitos de passagem, falhas de GPIO, reconexão simulada e ajustes HTTP. Ainda é preciso
ensaiar braços, velocidade, iluminação e recipientes reais. A contagem de testes atual
e o pacote estão documentados em TESTE-NA-ESTEIRA.md; os dados abaixo são históricos.

- **27 testes automáticos passaram no Raspberry Pi 3B**, com Python 3.13.5,
  OpenCV 4.10.0, NumPy 2.2.4 e Picamera2 0.3.37.
- Um ensaio curto de captura e processamento com exposição inicial de 6 ms
  alcançou **14,75 FPS**, com pico de **162 MiB de memória residente no processo**.
- Após a instalação, o cartão tinha aproximadamente **9,45 GiB livres**.
- O reconhecimento das três cores em tampinhas reais, os SG90 e a separação
  mecânica ainda precisam de calibração e ensaio.
- O Raspberry de teste reportou subtensão. A fonte e o cabo precisam ser
  conferidos antes dos testes mecânicos.

Para executar os testes sem câmera ou GPIO:

```bash
python3 -m unittest discover -s tests -v
```

Objetos sem cor confiável seguem reto e geram aviso. As contagens indicam
detecções e destinos programados; não confirmam a queda no recipiente.
O programa usa buffers e fila limitados e não grava vídeo continuamente.

Consulte [VALIDACAO.md](VALIDACAO.md) para as medições e os ensaios pendentes.

## Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `app.py` | Execução por terminal, fotos, calibração e teste individual de servo |
| `vision.py` | Ocupação da pista, classificação HSV e aprendizado de cores |
| `core.py` | Contagem por passagem, reservas de tempo, fila e vigilância da câmera |
| `hardware.py` | Captura Picamera2 e controle dos pulsos com pigpio |
| `config.json` | Parâmetros da câmera, cores, GPIOs e tempos da montagem |
| `install.sh` | Instalação das dependências |
| `install_pigpiod.py` | Instalação do daemon oficial em sistemas sem pacote APT |
| `tests/` | Testes da lógica, visão e tratamento de falhas |
