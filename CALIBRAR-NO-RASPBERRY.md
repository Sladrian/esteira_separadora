# Calibrar a câmera no Raspberry

**Para ajustar o retângulo e os tempos durante os testes atuais, use o painel descrito
em [TESTE-NA-ESTEIRA.md](TESTE-NA-ESTEIRA.md).** Os comandos de aprendizado abaixo continuam
exigindo execução contínua encerrada e somente uma amostra na faixa.

Projeto instalado em `/home/admin/esteira-separadora`. Execute os comandos
no terminal SSH do Raspberry, com a correia parada e os servos sem acionamento.
Mantenha a câmera fixa e a iluminação que será usada no teste.

```bash
cd ~/esteira-separadora
```

## 1. Conferir o enquadramento

A foto `teste-camera-atualizacao.jpg` foi tirada durante a verificação da câmera.
O retângulo indica onde o programa procura a tampinha. Uma tampinha deve caber
inteira nessa faixa. Se for necessário mudar o enquadramento ou o retângulo,
faça isso antes do aprendizado das cores.

Para repetir a foto:

```bash
python3 app.py photo --output enquadramento.jpg
```

Para copiar a foto ao notebook, em OUTRO terminal do Windows (fora do SSH):

```powershell
scp admin@10.196.123.148:/home/admin/esteira-separadora/enquadramento.jpg .
```

## 2. Referência branca (câmera do cabo flat)

Coloque papel branco preenchendo boa parte da imagem sob a mesma iluminação
que será usada nas tampinhas. Execute:

```bash
python3 app.py balance
```

Depois retire o papel e volte ao fundo normal. Este comando é para CSI;
com webcam USB o balanço é ajustado nos controles da própria câmera.

## 3. Aprender as três cores

Coloque somente a tampinha ROSA dentro do retângulo e execute:

```bash
python3 app.py learn rosa --output rosa.jpg
```

Troque pela AZUL e execute:

```bash
python3 app.py learn azul --output azul.jpg
```

Troque pela VERDE e execute:

```bash
python3 app.py learn verde --output verde.jpg
```

Confira em cada comando a classificação após o ajuste. Se não corresponder
à cor colocada, ajuste iluminação/enquadramento e repita aquela amostra.
As faixas e o branco ficam salvos em config.json, com backup da versão anterior.

## 4. Testar passagens sem acionar os servos

Comece com o retângulo vazio. Execute:

```bash
python3 app.py run --seconds 60
```

Passe uma tampinha por vez e retire-a completamente antes da próxima.
O terminal mostra a cor e o destino programado. Sem `--live`, os comandos dos
servos são simulados. Ctrl+C encerra antes dos 60 segundos.

Uma foto estática boa não confirma o reconhecimento em movimento. Para este
teste, confira também a exposição e o LED: a exposição atual é de cerca de
60 ms e pode borrar as tampinhas. Se mudarmos a exposição ou a iluminação,
confira novamente as cores antes de testar os desvios físicos.

Não é necessário recalibrar ao ligar novamente, mantendo a mesma montagem.
