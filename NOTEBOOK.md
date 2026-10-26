# Testar cores no notebook (Windows)

Esta cópia veio do repositório privado Sladrian/esteira, revisão
4d7f2e82d36ad28517b76a6a3b51b0143fa4fcbc. O arquivo notebook.py é a adaptação
local. Ele reutiliza core.py e vision.py e não importa as bibliotecas do Raspberry.

## Preparar e abrir

Neste notebook as dependências já foram preparadas: dê dois cliques em
`abrir-cores.cmd`. O atalho usa o Python do Codex e as dependências locais
em work/python-deps; para outro computador, siga a instalação abaixo.

Com Python 3.10 ou posterior instalado, abra um terminal nesta pasta:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe notebook.py
```

O programa seleciona a Brio 300 pelo nome, mesmo se o Windows mudar o índice.
Para outro modelo, use `--camera-name "Nome da câmera"` ou `--camera 1` para
escolher por índice. Se a câmera não for encontrada, o erro lista as opções.
Feche outros aplicativos que estejam usando a câmera USB.

Coloque a câmera acima de um fundo cinza, preto ou branco, com luz constante.
Posicione uma tampinha inteira dentro da faixa branca, sem outros objetos coloridos.
Quando a imagem estabilizar:

- Tecla 1: aprender rosa.
- Tecla 2: aprender azul.
- Tecla 3: aprender verde.
- Q ou Esc: fechar e liberar a câmera.

A prévia exibe a cor e a porcentagem da área colorida atribuída a cada cor.
Essas porcentagens não são probabilidades estatísticas. Esta ferramenta serve
para testar cores; não conta passagens nem comanda os servos.

As teclas salvam as faixas em config.notebook.json, com backup da versão anterior.
O config.json original é preservado. A imagem é processada ao vivo e não é gravada.
Sem ajuste manual, exposição e branco da webcam ficam sob controle do driver; as configurações
específicas da câmera do Raspberry não são aplicadas à webcam.

## Tampinhas em movimento

Se a porcentagem cair muito durante o movimento, teste uma exposição mais curta:

```powershell
.\.venv\Scripts\python.exe notebook.py --exposure -7 --fps 30
```

Na Brio 300 deste notebook o driver confirmou -7 (aproximadamente 8 ms),
contra -5 (aproximadamente 31 ms) na leitura inicial. Isso reduz o tempo em que
a peça se desloca durante a captura; o resultado precisa ser verificado na prática.
Se a imagem escurecer, ilumine melhor a pista antes de aprender novamente as cores.
O nome da cor só é aceito quando atende aos limites de área e de margem configurados.

Reaprenda as três cores após mudar a exposição ou a iluminação. Ao aprender uma cor,
os ajustes de exposição e FPS solicitados ficam em notebook_capture, junto das
faixas HSV, em config.notebook.json. O painel mostra o FPS medido e a exposição.
O driver pode manter a exposição manual para outros aplicativos, que podem
restabelecer o modo automático nas próprias configurações de câmera.

## Depois, no Raspberry

O app.py e os arquivos originais continuam disponíveis. Instale e execute
conforme README.md e LEIA-ME.md. O notebook.py é uma ferramenta complementar.
Refaça balance e learn rosa/azul/verde no Raspberry com a câmera e a iluminação
definitivas: os limites aprendidos na webcam são uma referência, não uma calibração
garantida para outro conjunto óptico. Não substitua config.json por config.notebook.json.

## Verificar sem câmera

```powershell
.\.venv\Scripts\python.exe notebook.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
