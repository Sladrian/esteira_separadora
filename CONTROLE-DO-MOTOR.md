# Controle da esteira com L298N — 26/10/2026

O motor foi informado no par OUT1/OUT2: canal A. Se as inscrições da placa
indicarem OUT3/OUT4, use canal B e as entradas ENB, IN3 e IN4 correspondentes.
A fotografia mostra o formato de um módulo L298N; confirme pelas inscrições da sua placa.

## Ligações, com a alimentação desligada

| Raspberry, pino físico | GPIO BCM | Entrada da ponte H, canal A |
|---|---|---|
| 11 | 17 | ENA, comando PWM de velocidade |
| 13 | 27 | IN1, sentido |
| 15 | 22 | IN2, sentido |
| 6 | GND | GND da ponte e negativo da alimentação do motor |

O motor fica entre OUT1 e OUT2. A bateria continua no circuito de potência da
ponte H; sua tensão precisa ser compatível com motor e módulo. A tensão da
bateria não foi identificada, portanto esta atualização não altera a alimentação.
Não ligue o positivo da bateria nem o 5V da ponte aos GPIOs do Raspberry.

**Retire o jumper ENA antes de ligar o GPIO17.** Conecte ao sinal ENA, na mesma
fileira de IN1/IN2, e não ao pino de alimentação que o jumper une a ele. O jumper
de habilitação ENA é diferente do jumper do regulador 5V: não remova/mude o do
regulador sem conferir a alimentação. Um resistor de 10 kΩ entre ENA e GND ajuda
a manter o motor desabilitado enquanto o Raspberry inicia ou está desligado.

Os servos continuam no GPIO18/pino físico 12 (destino direita) e GPIO19/pino 35
(destino esquerda). Os três GPIOs do motor são validados para não se sobreporem
entre si nem aos dos servos.

Fontes: [L298/ST, sinais de enable, direção e parada](https://www.st.com/resource/en/datasheet/l298.pdf),
[manual de um módulo L298N/Joy-IT](https://joy-it.net/files/files/Produkte/SBC-MotorDriver2/SBC-MotoDriver2_Anleitung_2024-05-08.pdf),
[mapa GPIO Zero do conector J8](https://github.com/gpiozero/gpiozero/blob/master/gpiozero/pins/data.py).

## Configurar sem mover o motor

Se IN1/IN2 já estão definidos externamente em níveis opostos, é possível usar
somente ENA/GPIO17/pino 11 e GND/pino 6 entre a ponte e o Raspberry:

```bash
python3 app.py motor configurar --canal A --sentido-fixo
```

Nesse modo, o programa não toca em GPIO27/GPIO22. IN1/IN2 não podem ficar soltos.
Continue retirando o jumper ENA antes de conectar o sinal de velocidade.

Encerre `run` com Ctrl+C, ou `python3 controle.py parar`, antes deste comando:

```bash
python3 app.py motor configurar --canal A
```

Isso salva o bloco `motor` no config.json e preserva cores, ROI e tempos dos servos.
Não abre pigpio nem envia sinal. Padrões: PWM=GPIO17, IN1=GPIO27, IN2=GPIO22,
1000 Hz e sentido IN1=alto/IN2=baixo. Para canal B, conecte os mesmos GPIOs a
ENB/IN3/IN4 e use `--canal B`.

Se o sentido físico estiver invertido, pare o programa e use:

```bash
python3 app.py motor configurar --canal A --inverter
```

Não há inversão de sentido com motor em movimento pelo painel.
Para voltar ao comando externo da correia:

```bash
python3 app.py motor desativar
```

## Usar o painel

Garanta que `pigpiod` esteja ativo. Execute com servos e motor reais:

```bash
python3 app.py run --live --web
```

Ou em segundo plano: `python3 controle.py iniciar`. A partida é sempre parada,
mesmo que a execução anterior tenha terminado com velocidade diferente de zero.
Sem `--live`, o controle de motor também é simulado, sem GPIO.

No painel:

1. Aguarde a câmera ficar ativa e a faixa vazia.
2. Escolha o percentual e clique em **Ligar esteira**.
3. Use **Aplicar velocidade** para mudar o percentual com faixa e janelas dos
   braços vazias. Não mude a velocidade com tampinhas em trânsito pela pista.
4. **Parar esteira** zera o PWM sem esperar pela fila de ajustes da câmera e
   cancela movimentos pendentes dos servos. Pode haver parada por inércia.

O percentual é de PWM/potência, não uma medição de cm/s ou RPM. Após mudar a
velocidade, confira os tempos até os braços. Não há encoder para recalculá-los
automaticamente. Após parar, retirar tampinhas em trânsito evita reutilizar uma
referência temporal que deixou de valer.

Se o motor estiver configurado e parado, o reconhecimento continua, mas novas
leituras não programam desvios. Ao ligar, o rastreamento é reiniciado com faixa vazia.

## Perda de câmera e encerramento

A ausência de novos quadros por `camera_timeout_s` dispara um watchdog independente
da captura e solicita PWM zero. Falhas de captura e Ctrl+C também solicitam a parada.
O motor não religa sozinho quando a câmera retorna: precisa de novo clique em Ligar.
Falhas do daemon/GPIO aparecem no painel, mantendo o reconhecimento em execução.

Essas paradas dependem de programa, Raspberry, daemon e ligação elétrica funcionando;
não substituem a chave física da bateria. Se ENA continuar jumpeado ao positivo,
o software não conseguirá regular/desabilitar o enable como previsto.

O código foi testado com GPIO simulado; a montagem do motor ainda precisa de ensaio físico.
