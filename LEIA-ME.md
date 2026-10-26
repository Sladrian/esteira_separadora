# Esteira separadora de tampinhas — Raspberry Pi 3B

Para a rotina contínua de 19/10/2026 e seus comandos de painel/tempos/área,
consulte primeiro [TESTE-NA-ESTEIRA.md](TESTE-NA-ESTEIRA.md).

Projeto de software para Raspberry Pi 3B, microSD de 16 GB, Raspberry Pi OS Lite,
câmera CSI compatível com Picamera2 e dois microservos posicionais SG90.

Configuração inicial: **rosa → direita; azul → esquerda; verde → reto**.
Você pode trocar as saídas no `config.json`.

Na instalação inicial, 27 testes automáticos passaram no seu Raspberry Pi 3B,
com Raspberry Pi OS baseado no Debian 13/Trixie. A câmera OV5647 foi reconhecida e
testada em 320 × 240. O funcionamento da separação física ainda depende de calibrar
imagem, servos e tempo de percurso.
Os SG90 e as espátulas ainda não foram acionados nesta validação.

## 1. O que este projeto faz

- Captura 320 × 240 a 15 quadros/s como configuração inicial; desempenho real deve ser medido no Pi.
- Processa somente um recorte de 200 × 60 pixels com OpenCV/HSV, sem rede neural.
- Confirma a mesma cor em três quadros e conta uma vez por passagem.
- Exige faixa vazia ao iniciar; depois rastreia componentes separados, mesmo com outra tampinha visível.
- Agenda o movimento do servo sem pausar a captura da câmera.
- Reserva tempo de passagem nas duas estações, inclusive para a tampinha verde.
- Executa por terminal, aprende cores de amostras e salva fotos apenas quando solicitado.
- Mantém a leitura diante de desconhecidas, múltiplos componentes, conflitos e falhas de servo.
- Cancela movimentos em falhas de câmera e tenta reconectar à mesma fonte.
- Permite ajustar a área e os tempos em um painel local opcional, sem desktop no Pi.

As contagens representam **detecções e destinos programados**. Não há sensor para
confirmar que a tampinha realmente caiu no recipiente.

Objetos coloridos sem classificação confiável seguem reto e geram aviso; portanto,
o recipiente final pode conter verde **e rejeitos**. Tampinhas brancas, transparentes
ou muito dessaturadas podem não ser detectadas pela máscara de ocupação.

## 2. Montagem de referência

Use uma pista central que mantenha as tampinhas em fila única, sem sobreposição,
com iluminação fixa e difusa, câmera firme apontada de cima e fundo neutro fosco.
Comece alimentando **uma tampinha a cada 2 segundos**, em velocidade baixa e constante.

Exemplo geométrico para orientar a montagem, sujeito a calibração:

```text
Sentido da esteira →

Entrada → CÂMERA / faixa de leitura → DESVIO DIREITA → DESVIO ESQUERDA → RETO
          x = 10 cm                  x = 30 cm       x = 40 cm         saída
                                      ↓ rosa         ↓ azul          verde

Exemplo: esteira útil com ~50 cm de comprimento e ~8 cm de largura;
velocidade inicial de 10 cm/s; guia central ligeiramente maior que a tampinha.
```

Com esses valores, os percursos desde a leitura até as espátulas são aproximadamente
2 s para direita e 3 s para esquerda: os valores iniciais do arquivo de configuração.
As espátulas devem ser leves, não travar a correia e ficar fora da pista em repouso.
O SG90 rotaciona o braço; é o formato/curso da espátula que precisa conduzir a
tampinha até a borda correta. Teste isso manualmente antes de alimentar em sequência.

A altura da câmera depende da lente e do foco. Na foto de teste, a tampinha deve
ficar nítida e ocupar pelo menos cerca de 20 pixels de diâmetro. Algumas câmeras
OV5647 têm foco fixo: aproximar a câmera pode exigir ajuste de foco conforme o módulo.

O movimento do motor da esteira é independente nesta versão: use seu driver e
alimentação próprios, mantendo velocidade constante. Não conecte motor DC aos GPIOs.
O driver/motor não foram informados, por isso o software não liga, regula ou para a
correia. A montagem deve permitir parar sua alimentação/comando prontamente.

## 3. Ligações dos SG90

Desligue a alimentação antes de conectar. A pinagem abaixo é do conector de 40 pinos
do Raspberry Pi 3B. A coluna GPIO usa numeração **BCM**, não a posição física.

| Ligação | Destino |
|---|---|
| Sinal do servo de saída direita | GPIO18 / pino físico 12 |
| Sinal do servo de saída esquerda | GPIO19 / pino físico 35 |
| Positivo de ambos os servos, normalmente vermelho | Positivo de fonte externa regulada adequada aos servos |
| Negativo de ambos, normalmente marrom/preto | Negativo da fonte externa |
| Negativo da fonte externa | GND do Raspberry / pino físico 6 |
| Câmera | Conector CSI do Raspberry, com a placa desligada |

Para microservos SG90 usuais, uma fonte regulada de **5 V com capacidade de 2 A**
é uma referência inicial para os dois, separada da alimentação do Pi; confirme a
tensão e o consumo dos seus exemplares e não mantenha os motores travados.
O terra precisa ser comum. Não una o positivo da fonte dos servos ao 5 V do Pi
nesta montagem. O GPIO fornece somente o sinal, nunca a energia do servo.
Capacitor de 470–1000 µF no barramento dos servos, próximo deles e com polaridade
correta, pode ajudar com transientes; não substitui uma fonte dimensionada.

Use SG90 **posicionais**. Se forem versões modificadas para rotação contínua,
este programa de posições não se aplica. Ao desligar os pulsos, um SG90 perde
o comando de sustentação; mantenha a mecânica estável no repouso.

## 4. Instalação no seu Raspberry Pi OS Lite

Use o sistema Lite que já está instalado se for uma versão compatível com Picamera2
(Bullseye ou posterior, sem ativar a pilha de câmera antiga). Confira:

```bash
cat /etc/os-release
df -h /
```

Copie a pasta inteira `esteira-separadora` para a sua pasta de usuário no Raspberry.
Exemplo, se ela estiver em `~/esteira-separadora`:

```bash
cd ~/esteira-separadora
bash install.sh
```

O instalador usa APT, sem compilar OpenCV e sem instalar interface gráfica. Os pacotes
são instalados automaticamente; confira o espaço livre antes de iniciar. Se o daemon
`pigpiod` não estiver no APT (como no Trixie deste Raspberry), compila apenas esse
pequeno daemon a partir da versão oficial v79, verificando SHA-256, e o instala em
`/usr/local/bin/pigpiod`. Não use `pip install opencv-python`
para instalar as dependências no Raspberry; Picamera2/libcamera devem permanecer
compatíveis com os pacotes do sistema. Use o `python3` do sistema nos comandos abaixo.

Se o APT não encontrar um pacote, confira se é Raspberry Pi OS e se os repositórios
oficiais estão habilitados. A distribuição e a versão precisam ser conhecidas antes
de escolher uma alternativa de instalação; não misture repositórios de versões distintas.

Teste a identificação da câmera:

```bash
python3 -c "from picamera2 import Picamera2; print(Picamera2.global_camera_info())"
python3 app.py check
python3 -m unittest discover -s tests -v
```

A Camera Module v1 oficial usa **OV5647**. Você informou OV5646; a saída do primeiro
comando vai ajudar a identificar o módulo. Uma lista vazia significa que a câmera
ainda não foi reconhecida: confira cabo CSI, encaixe, orientação dos contatos e
compatibilidade do módulo antes de prosseguir. Não ative a opção de câmera legacy.

## 5. Primeiro confira a imagem, sem acionar servo

Deixe a esteira parada e coloque uma tampinha na região central da imagem:

```bash
python3 app.py photo --output teste.jpg
```

Abra `teste.jpg` em outro computador. Por SSH, copie com SCP, substituindo usuário
e IP pelos seus dados. Exemplo a executar no computador, não no Pi:

```bash
scp seu_usuario@IP_DO_PI:~/esteira-separadora/teste.jpg .
```

O retângulo branco marca a ROI: `[x, y, largura, altura]`, em pixels, começando no
canto superior esquerdo da imagem. Ajuste `vision.roi` no `config.json` com `nano`
para cobrir a pista onde a tampinha passa, sem incluir as espátulas ou objetos coloridos externos.

```bash
nano config.json
```

A detecção só funciona nessa faixa. A tampinha precisa permanecer visível nela por
pelo menos cerca de 0,2 s e existir um trecho vazio entre tampinhas por cerca de
0,27 s, considerando 15 FPS efetivos. Reduza a velocidade se necessário.

O ajuste atual usa `exposure_us: 59973` e `analogue_gain: 4.0625` para testes de imagem
no ambiente pouco iluminado observado. Esses valores substituem o ajuste inicial
de 6000 µs/ganho 1,5, que deixou a cena escura. Antes de separar tampinhas em movimento,
ilumine a pista e reduza a exposição conforme o teste: exposição longa aumenta o borrão,
e ganho alto aumenta o ruído. Repita o aprendizado de cores após alterar esses valores.
Os ganhos de branco e a exposição ficam estáveis durante o processamento, evitando
que o sistema mude as cores conforme as tampinhas passam.

## 6. Aprender rosa, azul e verde

Com a iluminação definitiva, coloque uma folha branca fosca sob a câmera e execute:

```bash
python3 app.py balance
```

Retire a folha. Com a esteira parada, coloque **só a tampinha rosa** dentro do retângulo
e execute o primeiro comando abaixo. Troque pela azul para o segundo e pela verde para o terceiro:

```bash
python3 app.py learn rosa --output rosa.jpg
python3 app.py learn azul --output azul.jpg
python3 app.py learn verde --output verde.jpg
```

Cada comando salva as faixas no `config.json` e mantém a versão anterior em
`config.json.bak`. Os ganhos de branco são reutilizados nas próximas execuções.
Não mude a iluminação, a exposição ou o branco depois sem repetir o aprendizado.

Confira as três fotos e repita `photo` com várias tampinhas de cada cor. Reflexos,
estampas grandes e tons muito próximos podem exigir ampliar/restringir as faixas HSV.
No OpenCV, H vai de 0 a 179; S e V de 0 a 255. Rosa-claro pouco saturado pode exigir
reduzir o limite inferior de saturação em `occupancy_hsv` antes do aprendizado.
Fundo colorido entra nessa máscara e atrapalha a detecção; use fundo neutro.

## 7. Testar os servos com a correia parada

Comece com as espátulas removidas. O SG90 pode se mover assim que receber o primeiro
comando de repouso. O pulso inicial de repouso é 1500 µs para ambos; o sentido físico
depende de como os braços estão montados.

Inicie o daemon local de pulsos temporizados, uma vez após cada reinício do Pi:

```bash
sudo pigpiod -l -f
```

Se ele já estiver ativo, não abra outra cópia. Para verificar:

```bash
pgrep -a pigpiod
```

Teste posições com passos pequenos. Cada comando aplica por 1 s e retorna ao repouso:

```bash
python3 app.py servo direita 1550 --live
python3 app.py servo esquerda 1450 --live
```

Aumente/diminua em passos de 50 µs até encontrar curso suficiente. Sem `--live`, o
comando apenas imprime a simulação. O código limita os pulsos a 1000–2000 µs, mas
a mecânica pode travar antes desses limites: respeite o curso real de cada exemplar.

Salve as posições em `servos.direita` e `servos.esquerda`:

- `rest_us`: espátula recolhida, pista livre.
- `push_us`: posição que conduz a tampinha para a saída desejada.
- `advance_s`: tempo para o braço chegar ao desvio antes da tampinha; começa em 0,20 s.
- `hold_after_arrival_s`: mantém o desvio até a tampinha sair; começa em 0,25 s.
- `return_s`: tempo reservado para recolher o braço; começa em 0,25 s.

Instale as espátulas com o servo em repouso. Ajuste o ângulo do braço mecanicamente
e repita o teste antes de colocar a correia em movimento. Os servos mantêm pulsos
em repouso durante a execução; ao encerrar normalmente, o programa recolhe e remove os pulsos.

## 8. Calibrar o percurso

`travel_s` é o tempo **efetivo entre a primeira leitura estável da passagem e a chegada
à espátula**, incluindo o efeito da latência da câmera. O software usa como referência
o primeiro dos quadros que confirmaram a cor; não começa a contagem só no terceiro.

Use `distância / velocidade` para uma estimativa inicial, medindo separadamente
cada estação. Exemplo: 20 cm a 10 cm/s → 2 s; 30 cm a 10 cm/s → 3 s.
Depois ajuste observando tampinhas reais, porque a região de leitura e a latência
introduzem diferença em relação à distância geométrica.

Para cada servo, o programa manda desviar em `t0 + travel_s - advance_s` e manda
recolher em `t0 + travel_s + hold_after_arrival_s`.

- Se o braço só chegar depois da tampinha, reduza `travel_s` ou aumente `advance_s`.
- Se o braço voltar antes da tampinha, aumente `travel_s` e/ou a duração de manutenção,
  observando se a antecipação continua suficiente.
- Mude um parâmetro por vez e repita com uma única tampinha.

Mudança na velocidade exige nova calibração. Não há encoder ou sensor de chegada.
Uma tampinha que escorrega, uma correia que varia a velocidade ou peças que encostam
umas nas outras quebram a hipótese de tempo fixo deste projeto.

## 9. Executar

Comece sem acionar GPIO para validar reconhecimento com a correia andando:

```bash
python3 app.py run --seconds 60
```

Depois de ajustar posições e tempos, execute com os servos:

```bash
python3 app.py run --live
```

Espere aparecer **PRONTO**, mantenha a faixa vazia inicialmente e só depois comece
a alimentar. Comece com 2 s entre tampinhas. A rotina contínua não interrompe mais
por intervalo global de 1,2 s; avalia cada janela de passagem nos braços e cancela
desvios conflitantes, mantendo a captura. Isso não garante a separação de peças próximas.
Inclua o comprimento da tampinha e a extensão da ROI no espaçamento físico.

Ctrl+C encerra o software. Pare também a correia. Em uma falha detectada, o programa
cancela os movimentos pendentes e tenta recolher os braços, mas **não para o motor
da esteira**. Uma queda de energia ou travamento total do sistema não permite
garantir a posição dos braços; interrompa a alimentação/comando da correia.

Tampinhas juntas da mesma cor podem parecer um só objeto e gerar apenas uma contagem.
O programa rastreia múltiplos componentes separados, mas não substitui um alimentador
que garanta separação física. Não acrescente tampinhas durante reinicializações.

## 10. Validar a montagem

Antes de usar em sequência, faça e registre estes ensaios:

1. Dez fotos de cada cor, variando a orientação da tampinha, sem falsos resultados.
2. Dez passagens de cada cor sem servos, verificando uma detecção por tampinha.
3. Dez passagens isoladas por saída, com servos e velocidade fixa.
4. Sequência rosa, azul, verde repetida dez vezes, começando com intervalo de 2 s.
5. Verifique fisicamente os recipientes; as contagens do programa não comprovam a separação.
6. Interrompa normalmente e confirme que a posição recolhida deixa a pista livre.

Se falhar, não aumente a velocidade: ajuste iluminação, foco, ROI, espaçamento ou tempos.
Os testes automáticos cobrem lógica e imagens artificiais, não os ensaios acima.

## 11. Limites de memória e armazenamento

Há painel web local opcional com `--web`; não exige desktop no Pi, rede neural ou gravação contínua.
O programa usa buffers limitados e guarda no máximo 32 eventos pendentes por padrão.
O quadro BGR de 320 × 240 ocupa cerca de 225 KiB; Picamera2, libcamera, OpenCV e
buffers internos usam memória adicional. Esse valor não é o consumo total de RAM.

O cartão de 16 GB é destinado ao sistema Lite, pacotes e arquivos do projeto.
O espaço livre depende da versão e do que já está instalado. Neste Raspberry, um
ensaio curto de 60 quadros com captura e visão alcançou 14,75 FPS e pico de memória
residente de cerca de 162 MiB no processo. Isso não inclui toda a memória do sistema
ou reservas do hardware e não garante desempenho em operação contínua. Confira:

```bash
free -h
df -h /
top
vcgencmd get_throttled
```

O programa imprime FPS médio a cada 10 s. Se ficar lento, mantenha 320 × 240,
feche processos extras e reduza a velocidade da correia. Um aumento de resolução
exige rever ROI, área mínima e os tempos de presença/vazio. Não é necessário trocar
o cartão ou o Raspberry antes de testar esse caminho leve.

## 12. Arquivos e diagnóstico

| Arquivo | Função |
|---|---|
| `app.py` | Comandos de execução, fotos, calibração e teste de servo |
| `core.py` | Confirmação de passagem, reservas, fila e vigilância de câmera |
| `vision.py` | Máscaras HSV, ocupação, classificação e aprendizado |
| `hardware.py` | Captura Picamera2 e pulsos por pigpio |
| `config.json` | Cores, pinos, imagem e parâmetros da montagem |
| `install.sh` | Instalação pelo APT no Raspberry |
| `install_pigpiod.py` | Fallback de instalação do daemon oficial para Trixie/Pi 3 |
| `tests/` | Testes sem hardware e configuração isolada para testes |

Erros comuns:

| Sintoma | Verificação |
|---|---|
| Câmera não aparece | CSI, cabo, contatos, modelo e versão do OS; desligue antes de reconectar |
| Tudo vazio | ROI fora da pista, foco ruim, área mínima alta, pouca iluminação ou saturação baixa |
| Muitas desconhecidas | Aprenda as cores novamente, verifique branco e reflexos |
| Mais de um objeto | Remova objetos coloridos da ROI e aumente o espaçamento |
| SG90 treme ou Pi reinicia | Fonte, terra comum, fios, travamento mecânico e alimentação separada |
| pigpiod indisponível | Execute `sudo pigpiod -l -f` e confira `pgrep -a pigpiod` |
| Tampinha contada mais de uma vez | Evite reflexos/perda de máscara e aumente `empty_frames` com cuidado |
| Objeto fica na faixa | Verifique se a correia parou ou se o fundo está sendo detectado |
| Acionamento atrasado | Diminua carga do Pi, aumente distância câmera/servo e diminua velocidade |

## Fontes técnicas

- [Picamera2: instalação oficial e compatibilidade](https://github.com/raspberrypi/picamera2)
- [Manual Picamera2: formato RGB888, controles e buffers](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [OpenCV: segmentação de cores com HSV e inRange](https://docs.opencv.org/4.x/da/d97/tutorial_threshold_inRange.html)
- [pigpio: API e pulsos temporizados](https://github.com/joan2937/pigpio)
- [Raspberry Pi: câmeras e sensores](https://www.raspberrypi.com/documentation/accessories/camera.html)
- [Raspberry Pi: armazenamento e instalação](https://www.raspberrypi.com/documentation/computers/getting-started.html)
- [Tower Pro: SG90 — confirme a variante do seu exemplar](https://towerpro.com.tw/product/sg90-7/)
