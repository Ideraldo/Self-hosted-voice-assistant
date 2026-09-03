# Wake word: treinar o "Ideraldinho" sem baixar a internet

A receita oficial do openWakeWord manda sintetizar dezenas de milhares de
positivos com o `piper-sample-generator` — um checkpoint LibriTTS, **em
inglês** — e misturar com dezenas de GB de ruído e fala baixados do
HuggingFace.

Aqui nada disso foi preciso, porque este repositório já tinha o que importa.

## Por que o caminho local é melhor, e não só mais barato

**A pronúncia.** Um TTS inglês diria "Ideraldinho" com fonemas ingleses, e o
modelo aprenderia a palavra errada — que é o pior tipo de defeito, porque ele
treina bem, mede bem e só falha na sala. As sete vozes pt-BR do Piper que já
estavam em `lab/models/piper/` dizem `ˌideɾaʊdʒˈiɲʊ`, conferido no espeak-ng.

**Os negativos.** As 315 gravações do dataset do fine-tune da voz (D4) são o
dono do aparelho falando outra coisa: voz real, microfone real, sala real. É o
negativo mais difícil que existe para este caso, e é o único que importa de
verdade — é essa voz que fica perto do microfone o dia inteiro.

**O que falta, e não dá para esconder:** ruído de sala real. O que há aqui é
ruído sintético (branco, rosa, zumbido de 60 Hz) e um eco simulado. Isso não
soa como uma cozinha às sete da noite. **É a maior fraqueza deste dataset**, e é
o que a medição com o microfone vai cobrar.

## Os três passos

```bash
python -m lab.wakeword.gerar      # ~1.200 positivos, ~2.000 negativos
python -m lab.wakeword.treinar    # extrai embeddings, treina, exporta .onnx
python -m lab.wakeword.medir      # falso positivo por HORA, em fluxo
```

E então, no `.env`:

```
WAKE_ENABLED=1
WAKE_MODEL=lab/models/wakeword/ideraldinho.onnx
WAKE_THRESHOLD=0.9
```

## O que se treina, exatamente

O openWakeWord é dois modelos em série, e só o segundo é nosso:

```
audio 16 kHz ──> [extrator congelado] ──> embeddings (16, 96) ──> [nosso] ──> 0..1
```

O extrator é o mesmo dos modelos oficiais e não se toca. O que se treina é um
classificador de três camadas densas em cima dele — que é por que isto cabe
numa tarde e em 3 mil exemplos, em vez das dezenas de milhares da receita.

A janela é de **2,0 s** porque é o que o extrator converte em exatamente
(16, 96), que é a entrada do classificador. Não é escolha de gosto.

E a palavra é posta **no fim da janela**, não no meio. Em uso, a janela avaliada
é sempre a que acabou de passar: quando alguém termina de dizer o nome, ele está
no fim do buffer. Treinar centralizado ensinaria o modelo a esperar meio segundo
de silêncio depois do nome — meio segundo a mais para o aparelho acordar.

## Por que `medir.py` existe separado de `treinar.py`

Porque a métrica do treino engana em uma ordem de grandeza, e vale entender
como.

O treino reporta precisão sobre janelas de 2 s. Em uso, o modelo não vê 390
janelas: ele vê **uma nova a cada 80 ms**, ou seja 45 mil por hora. Uma precisão
de 96,7% por janela, se as janelas fossem independentes, daria mais de mil
despertares por hora.

Elas não são independentes, e o refratário come a maior parte das repetições —
mas a única forma de saber quanto sobra é rodar em fluxo, com o refratário
ligado, como o `device/` roda. É o que `medir.py` faz, e é a única métrica aqui
que fala a língua do critério de aceite da Fase 7: **falso positivo por hora**.

## O que nada disto substitui

O microfone. Tudo aqui é áudio sintético tocado em memória, sem placa de som,
sem sala, sem televisão ligada. O número que decide é:

```bash
python -m scripts.wake --segundos 3600
```

com o aparelho parado e ninguém chamando por ele.
