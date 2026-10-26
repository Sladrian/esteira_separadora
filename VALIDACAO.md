# Validação desta entrega

**Validação histórica inicial: 27 testes automáticos passaram diretamente no Raspberry Pi 3B.**

A atualização contínua de 19/10/2026 tem testes adicionais no notebook, incluindo
GPIO e câmeras simulados e requisições reais ao painel local. Instalação no Pi e
validação física dos servos ainda estão pendentes. Veja TESTE-NA-ESTEIRA.md.

Execução no Raspberry do projeto (acesso SSH), em sistema Trixie/aarch64,
com Python 3.13.5, OpenCV 4.10.0, NumPy 2.2.4 e Picamera2 0.3.37. O daemon pigpiod
v79 foi compilado da fonte oficial e sua versão foi conferida sem iniciar os pulsos.

Tempo da suíte: 1,656 s, após importar os módulos. Pico de memória residente do
processo de testes: 140332 KiB (~137 MiB). Esse teste usa imagens artificiais e não
inclui a memória/latência da captura real pela câmera.

## Ensaio da câmera real

A câmera passou a ser reconhecida como **OV5647** depois de reiniciar com o cabo CSI
já conectado. Não foi necessário alterar os overlays do sistema.

- Captura de 60 quadros BGR de 320 × 240, com processamento da ROI e sem acionar GPIO.
- Duração da leitura/processamento: 4,068 s; taxa média: **14,75 FPS**.
- Pico de memória residente do processo: 165904 KiB, aproximadamente **162 MiB**.
- Exposição reportada: 5968 µs; ganho analógico: 1,5.
- Foto salva no Raspberry: `~/esteira-separadora/calibracao-inicial.jpg`.

A ROI padrão ainda precisa ser ajustada à montagem: durante o ensaio houve
quadros com mais de um objeto e objetos sem classificação confiável. Não foram
ensaiadas as três cores em tampinhas reais nem a separação mecânica. O teste curto
não é uma medição de estabilidade em operação contínua.
Na foto inicial, a cena estava escura para a exposição de 6 ms. A iluminação da
pista e, se necessário, a exposição/ganho devem ser ajustados antes de aprender as cores.

## Ajuste posterior do brilho

Foi testada exposição automática para diagnosticar a cena escura, que selecionou
cerca de 66,5 ms com ganho 7,875. Depois de reposicionar a câmera para uma referência
clara, o `config.json` foi atualizado no Raspberry para **59973 µs e ganho 4,0625**,
mantendo os parâmetros fixos durante a captura e backup da versão anterior em
`config.json.bak`. As medições anteriores de FPS/memória
referem-se à configuração inicial de 6 ms.

A câmera precisa ficar apontada para a pista durante a calibração. Enquadrar
superfícies pretas muito próximas torna a foto escura mesmo com exposição maior.
Para a esteira em movimento, melhore a iluminação e reduza a exposição antes
de validar cores e tempos.

Após a instalação, havia 9,45 GiB livres no cartão. A instalação pelo APT e a
sintaxe do instalador shell foram verificadas no Raspberry. Não foi instalada
uma área de trabalho gráfica.

| Grupo | Casos |
|---|---:|
| Configuração: arquivo válido, GPIO duplicado, pulso indevido e câmera ausente | 4 |
| Controle: acionamento independente, câmera sem quadros, atraso e falha de GPIO | 4 |
| Passagem: início vazio, contagem única, perda breve, rearme e objeto preso | 5 |
| Planejamento: saídas, espaçamento, verde, atraso, distâncias e sequência | 7 |
| Visão: cores, vazio, desconhecida, ROI, múltiplos objetos, mistura e aprendizado | 7 |

O teste de integração usa imagens sintéticas que percorrem visão → confirmação de
passagem → planejamento, verificando rosa/direita, azul/esquerda, verde/reto e
desconhecida/reto. O teste da thread usa um controlador de servo simulado.
Os testes de falha emitem mensagens de erro intencionalmente; isso é esperado.

A ajuda dos comandos também foi executada. A leitura do código foi confrontada
com as APIs oficiais Picamera2 e pigpio indicadas no guia.

**Ainda não ensaiado:** pulso elétrico dos GPIOs, consumo e curso dos SG90, foco
e iluminação definitivos, latência até a espátula, estabilidade em operação contínua
e separação mecânica das tampinhas. O Raspberry continuou reportando subtensão e
redução de desempenho ativas (`get_throttled=0x50005`) após reiniciar. Confira a fonte
e o cabo antes dos ensaios mecânicos.
Esses ensaios estão descritos no `LEIA-ME.md` e são necessários antes do uso contínuo.

Para repetir no Raspberry, na pasta do projeto:

```bash
python3 -m unittest discover -s tests -v
```

A configuração de teste é isolada em `tests/fixture.json`: aprender novas faixas
no `config.json` não muda as cores sintéticas usadas nos testes.
