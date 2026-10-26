# Testar com a esteira funcionando — atualização de 19/10/2026

Esta versão roda até Ctrl+C, ou em segundo plano mesmo após fechar o SSH.
Reconhece componentes separados simultaneamente dentro do retângulo branco e
acompanha cada um por posição. Uma cor desconhecida não encerra a leitura.
O motor pode ser regulado pelo painel quando o controle L298N está configurado.
Veja [CONTROLE-DO-MOTOR.md](CONTROLE-DO-MOTOR.md). Sem essa configuração, o comando
da correia continua externo, como na versão original.

Validação no notebook: **83 testes automáticos passaram**, incluindo múltiplas cores,
falha de servo, recuperação da câmera, painel HTTP e atualização com preservação de
configuração/rollback. O painel foi conferido no navegador: alteração numérica,
desenho da área com o mouse e confirmação ao salvar. Avisos de falha durante os
testes são intencionais, gerados pelas simulações de erros.

## Instalar sem apagar a calibração do Raspberry

O ZIP de atualização não contém `config.json`: as cores, a câmera, a área e os tempos
salvos no Raspberry são preservados. Não copie o config padrão do notebook por cima.

Copie `esteira-atualizacao-2026-10-19.zip` para o Raspberry usando o IP atual, por exemplo:

```powershell
scp .\esteira-atualizacao-2026-10-19.zip admin@IP_DO_RASPBERRY:~/
```

No SSH, encerre a versão anterior com Ctrl+C e extraia em uma pasta separada:

```bash
python3 -m zipfile -e ~/esteira-atualizacao-2026-10-19.zip ~/esteira-atualizacao-2026-10-19
python3 ~/esteira-atualizacao-2026-10-19/atualizar.py ~/esteira-separadora
cd ~/esteira-separadora
```

O instalador exige que câmera/programa anterior estejam fechados, testa o código
antes da troca e cria backup. Não liga câmera nem servos. A instalação física desta
atualização ainda precisa ser feita; os testes no notebook usam imagens e GPIO simulados.

## Ligações dos servos

Os nomes direita/esquerda indicam o **destino da tampinha**, não o lado onde o servo
está preso. Se o servo montado à direita empurra para a esquerda, seu sinal vai ao
canal **esquerda**, GPIO19.

| Função | GPIO BCM | Pino físico do conector de 40 pinos |
|---|---|---|
| Rosa → saída direita | GPIO18 | 12 |
| Azul → saída esquerda | GPIO19 | 35 |
| Terra comum entre fonte dos servos e Raspberry | GND | 6 |
| Verde | — | Segue reto |

O fio de sinal costuma ser laranja/amarelo. O positivo do servo vai a uma fonte
externa regulada apropriada ao modelo (5 V nos SG90 usuais), e o negativo ao negativo
dessa fonte. Ligue esse negativo ao GND do Raspberry. Não alimente servo pelo GPIO.
Confirme o conector com `pinout`; faça a ligação com a alimentação desligada.

Fontes da pinagem: [GPIO Zero, mapa do conector J8](https://github.com/gpiozero/gpiozero/blob/master/gpiozero/pins/data.py)
e [documentação GPIO do Raspberry](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio-and-the-40-pin-header).

Um servo comum de três fios **não confirma movimento**. Ausência do servo/cabo não
é detectável pelo pulso enviado. Falta de pigpio/pigpiod ou erro reportado pelo canal
gera aviso e deixa o reconhecimento rodando. O canal com erro fica sem novos comandos
até reiniciar o programa após corrigir; o painel mostra o estado de cada canal.

## Iniciar e abrir a câmera pelo notebook

Para comandar servos, o daemon precisa estar ativo. Confira:

```bash
pgrep -a pigpiod
```

Se não estiver rodando:

```bash
sudo pigpiod -l -f
```

No terminal do **notebook**, abra a conexão com o encaminhamento do painel:

```powershell
ssh -L 8080:127.0.0.1:8080 admin@IP_DO_RASPBERRY
```

Nessa sessão, no Raspberry:

```bash
cd ~/esteira-separadora
python3 app.py run --live --web
```

Abra no navegador do notebook o endereço completo que aparece depois de `PAINEL:`.
É um endereço `http://127.0.0.1:8080/.../` com um trecho aleatório; copie tudo.
Espere **PRONTO** e a mensagem de leitura ativa, com a faixa inicialmente vazia.
Sem `--live`, câmera e painel funcionam em simulação, sem pulsos nos GPIOs.
Sem `--seconds`, não existe limite de 30/60 segundos. Ctrl+C encerra e recolhe os braços.

O painel usa a MESMA captura do reconhecimento. Não abra `camera_preview.py` junto.
São quatro imagens por segundo no painel para poupar o Pi; a leitura usa os quadros
da câmera, normalmente 15 por segundo. Não há gravação contínua de vídeo.

## Ajustar o retângulo branco

Arraste na imagem para desenhar a nova área, ou altere X, Y, Largura e Altura.
Clique em **Salvar e aplicar**. É preciso parar de alimentar e aguardar a faixa e
as estações ficarem vazias para mudar a área; não é necessário reiniciar o programa.
As cores salvas são mantidas. A área precisa conter pelo menos a quantidade mínima
de pixels configurada e ficar inteiramente dentro da imagem.

Pelo terminal, com o programa encerrado, o equivalente é:

```bash
python3 app.py ajustar --roi 110 105 80 25
python3 app.py config
```

Esses números são só o último enquadramento usado; ajuste ao posicionamento de hoje.
Mudar o ponto de leitura altera a distância aos braços: confira também os tempos.

## Ajustar os tempos e as posições

No painel, altere os campos de cada destino e clique em **Salvar e aplicar**.
Os tempos novos valem para próximas detecções; as que já estão na fila mantêm os
valores anteriores. A posição recolhida/desvio só pode mudar com faixa e fila vazias.
Cada alteração é validada, salva em `config.json` e deixa o estado anterior em `.bak`.

Com o programa encerrado, também pode usar comandos curtos:

```bash
python3 app.py ajustar --servo direita --tempo 2.0 --antecipacao 0.20 --permanencia 0.25
python3 app.py ajustar --servo esquerda --tempo 3.0 --antecipacao 0.20 --permanencia 0.25
python3 app.py ajustar --servo direita --recolhido 1500 --desvio 1800
python3 app.py ajustar --servo esquerda --recolhido 1500 --desvio 1200
python3 app.py ajustar --servo direita --retorno 0.25
```

- **Tempo até o braço:** tempo entre a primeira aparição do objeto no retângulo e sua
  chegada ao desvio. Meça com velocidade fixa, separadamente para cada estação.
- **Antecipação:** quanto antes dessa chegada o comando de desvio será enviado.
- **Permanência:** quanto tempo após a chegada estimada o braço permanece desviando.
- **Retorno:** tempo reservado para recolher o braço antes de outra passagem.
- **Recolhido/desvio:** pulsos entre 1000 e 2000 µs. O sentido depende da montagem.

Exemplo: tempo 2,0 s, antecipação 0,2 s e permanência 0,25 s → manda desviar em
1,8 s e manda recolher em 2,25 s desde a primeira aparição. Para chegar mais cedo,
reduza o tempo até o braço ou aumente a antecipação. Para manter por mais tempo,
aumente a permanência. A câmera precisa de tempo para confirmar a cor; valores de
percurso curtos demais são rejeitados.

Para verificar posições com a correia parada, comece sem espátula e com passos pequenos:

```bash
python3 app.py servo direita 1550 --live
python3 app.py servo esquerda 1450 --live
```

Esses comandos manuais retornam ao repouso após um segundo e exigem pigpiod funcionando.
Eles são independentes do modo contínuo; não podem executar junto com `run`.

## Rodar em segundo plano, sem depender da sessão SSH

Depois de ajustar posições e tempos:

```bash
python3 controle.py iniciar
python3 controle.py status
python3 controle.py parar
```

`iniciar` usa modo real e painel. Para reconhecer sem pulsos: `python3 controle.py iniciar --simular`.
`status` mostra os últimos registros, incluindo o endereço do painel ao iniciar.
Se esse endereço já saiu das últimas linhas: `grep 'PAINEL:' esteira.log*`.
Os registros giram entre três arquivos de no máximo 2 MB cada.
O reconhecimento continua se o SSH fechar; para rever o painel, reabra o túnel SSH.
Não há inicialização automática ao ligar o Raspberry nesta atualização.

## O que acontece quando algo sai do esperado

- Duas cores visíveis: cada componente separado recebe uma trajetória e uma decisão.
  Não precisa esvaziar o retângulo entre duas tampinhas que já estão separadas na imagem.
- Cor indefinida: aguarda uma cor estável durante a trajetória; se sair sem confirmar,
  registra desconhecida e não pede desvio para ela. A leitura continua.
- Tampinhas próximas: desaparece a parada global de 1,2 s. Se as janelas físicas dos
  braços se sobrepuserem, os desvios conflitantes são cancelados e contados nos avisos.
  Isso evita comandar posições incompatíveis, mas a peça poderá ir ao recipiente errado.
- Fila cheia ou desvio atrasado: descarta movimentos afetados e mantém a leitura.
- Falha na câmera: cancela movimentos pendentes, tenta recolher, tenta reconectar
  à mesma fonte escolhida inicialmente e exige faixa vazia antes de rastrear novamente.
  Aguarda as peças já em trânsito saírem das estações antes de retomar desvios.
- Servo sem confirmação/sem conexão: a leitura continua; observe os braços e os
  recipientes, pois contagens não são confirmação de separação física.

As tampinhas ainda precisam estar separadas fisicamente e a velocidade precisa ser
constante. Duas encostadas podem virar um componente, e cruzamentos/oclusões podem
trocar trajetórias. A câmera atual usa exposição longa (~60 ms), que pode borrar
peças rápidas: confira iluminação e imagens em movimento antes de acelerar.
Não foram feitos ensaios físicos dos braços com esta atualização.
