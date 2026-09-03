# Resultados — STT e TTS

Registro vivo. Cada motor testado ganha uma linha; a coluna **Ouvido** é sua e
vale mais que as outras.

**Escopo fechado: só offline.** STT e TTS rodam na Pi, porque o roteador de
intenções trabalha sobre texto — sem STT local não existe nível 0, e nível 0 com
a internet caída é requisito. Para a VPS sobe só texto.

**`[PT]` marca modelo especializado em português** (treinado ou ajustado só nele),
contra os multilíngues que apenas suportam pt. Veja tudo com `python -m lab.list`.

Medidas em: Windows 11, Python 3.12, CPU (sem GPU). Conjunto: as sete frases de
`lab/phrases.py`. **Na Pi 5, conte com 3 a 5 vezes esses tempos.**

**Este arquivo é só STT e TTS.** As medições de LLM e de ferramentas nasceram
depois, com o assistente já rodando, e por isso moram nas decisões que elas
motivaram — não há script de bancada para elas:

| O que foi medido | Onde |
|---|---|
| llama3.1:8b × qwen3:4b × qwen3:8b: ferramenta certa, conhecimento geral, latência | [D19](../docs/decisions.md), [D20](../docs/decisions.md) |
| `think=true` × `think=false`: acerto factual e latência | [D20](../docs/decisions.md) |
| Histórico com e sem `tool_call`: 0/3 contra 3/3 chamadas | [D22](../docs/decisions.md) |
| Latência da busca e do turno fundamentado | [D24](../docs/decisions.md) |

---

## TTS

**Você reprovou os dois primeiros:** Piper faber soa arrastado, MMS soa robótico.
Kokoro e as outras três vozes do Piper entraram por causa disso e estão geradas,
esperando avaliação.

| | Motor | Voz | RTF | Carga | Nativo | Ouvido |
|---|---|---|---|---|---|---|
| `[PT]` | **Piper** | **jeff-medium** | 0,05 | 1,9 s | 22050 Hz | ✓ **aprovada** |
| `[PT]` | **Piper** | **cadu-medium** | 0,05 | 1,9 s | 22050 Hz | ✓ **aprovada** |
| `[PT]` | Piper | miro-high | 0,05 | 2,8 s | 22050 Hz | *a ouvir (fora da lista oficial)* |
| `[PT]` | Piper | dii-high | 0,05 | 2,7 s | 22050 Hz | *a ouvir (fora da lista oficial)* |
| `[PT]` | Piper | faber-medium | 0,05 | 2,7 s | 22050 Hz | ✗ arrastado |
| `[PT]` | Piper | edresson-low | 0,04 | 2,6 s | 16000 Hz | *a ouvir* |
| | Kokoro | pf_dora (fem) | 0,33 | 15 s | 24000 Hz | *a ouvir* |
| | Kokoro | pm_alex (masc) | 0,34 | 17 s | 24000 Hz | *a ouvir* |
| | Kokoro | pm_santa (masc) | 0,34 | 17 s | 24000 Hz | *a ouvir* |
| `[PT]` | MMS (Meta) | por | 0,27 | ~15 s | 16000 Hz | ✗ robótico |
| | ~~edge-tts~~ | Francisca | 0,62 | — | 24000 Hz | *só referência* |

```powershell
.\.venv\Scripts\python.exe -m lab.run_tts --engine kokoro --voice pm_alex --play
.\.venv\Scripts\python.exe -m lab.run_tts --engine piper --voice pt_BR-jeff-medium --play
```

**Piper** — RTF 0,05 constante, 20x mais rápido que o tempo real, e o único com
folga confortável na Pi. Lê números sem ajuda. É o piso de velocidade que os
outros têm que justificar.

**Kokoro** (StyleTTS2, 82M) — RTF 0,33, ainda dentro do orçamento mas com 6x
menos margem que o Piper. É o mais citado hoje para projetos novos e a aposta
mais provável de agradar. Multilíngue, não especialista.

**MMS-TTS** — pulava todos os números em silêncio; "04538-133" simplesmente
sumia, sem erro nenhum. Corrigido soletrando os dígitos antes da síntese
(`lab/numbers.py`). O áudio da frase foi de 3,55 s para 9,41 s.

**Decidido: jeff e cadu aprovadas, e a voz final sai de fine-tune** — ver
[`lab/finetune/`](finetune/README.md).

### Fine-tune, primeira tentativa: descartada

| Época | WER no corpus | WER em texto novo | Distância |
|---|---|---|---|
| 113 | 26,3% | 39,2% | +12,8% |
| 318 | **11,3%** | **39,7%** | **+28,4%** |

O corpus melhorou muito, o texto novo não saiu do lugar: memorização. A base
`dii-high` faz 22,9% no mesmo holdout. Não era falta de épocas — 14.828 passos
já — era falta de material: 15,2 min e 203 frases.

Corpus ampliado para 315 frases. **Dataset final: 315 gravações, 30,9 min.**

### Fine-tune v2: fechado na época 996

**A voz oficial do Ideraldinho é a época 996**, exportada como
`pt_BR-ideraldo-medium`. Avaliação do usuário: *"não ficou perfeito, mas está
convincente"*.

Comparação entre épocas, com **síntese determinística** (ver o alerta abaixo).
Base `pt_BR-dii-high`: 14,6% em todas as rodadas.

| Época | Corpus | Holdout | Distância |
|---|---|---|---|
| 531 | 13,8% | 25,7% | +11,9% |
| 734 | 15,7% | 29,3% | +13,6% |
| 859 | 15,6% | 28,4% | +12,8% |
| 933 | 10,5% | 27,5% | +17,0% |
| **996** | 14,6% | **23,8%** | **+9,2%** |

A época final ganhou nos dois critérios. Treinar até o fim valeu — e não houve
memorização, apesar de um sinal isolado na 933.

> ⚠️ **A medição estava mentindo, pela terceira vez.** Antes do determinismo, a
> **mesma** época 734 mediu 26,6% e 35,7% de holdout em duas rodadas — nove
> pontos sem nada ter mudado no modelo. Esse ruído chegou a produzir um veredito
> de "DECOROU" para a época 996, que é justamente a melhor.
>
> A causa é o VITS amostrar ruído a cada geração: ótimo para a voz soar viva,
> péssimo para comparar. `PiperTTS(..., deterministic=True)` zera `noise_scale` e
> `noise_w_scale`, e o `generalize` passou a usar isso sempre. A prova de que
> funcionou está na coluna da base: 14,6% em todas as cinco rodadas, contra
> 14,6–23,6% antes.
>
> Só medir com determinismo. Ouvir, com o ruído normal — é ele que faz a voz
> soar viva.

### Fine-tune v2: o caminho até lá

| | v1 (descartado) | v2 |
|---|---|---|
| Áudio | 15,2 min | **30,9 min** |
| Learning rate | 2e-4 (padrão) | **1e-4** |
| Épocas | 368 rodadas | 1000 planejadas |
| Export | manual | automático a cada 100 épocas |

Medições com a métrica corrigida:

| | v1 (ep318) | v2 (ep531) | **v2 (ep734)** |
|---|---|---|---|
| Corpus | 11,3% | 14,6% | 16,8% |
| Holdout | 39,7% | 26,8% | **26,6%** |
| Distância corpus↔holdout | +28,4% | +12,2% | **+9,7%** |
| Base, na mesma rodada | 22,9% | 19,1% | 23,6% |
| **Diferença para a base** | +16,8 | +7,7 | **+3,0** |

**Na época 734 o diagnóstico virou SAUDÁVEL.** A voz treinada está a 3 pontos da
base em texto que nunca viu, contra 17 do v1.

Repare no corpus subindo (11,3% → 16,8%): parece pior e é o que se quer. Um
modelo que decora tem WER baixíssimo nas frases treinadas.

Os erros que restam são de articulação — sibilantes ("chuvisco" → "chuvispo"),
nomes próprios raros, termos técnicos. É o que melhora por último.

> **Ruído:** a base mediu 19,1% numa rodada e 23,6% noutra, sendo o mesmo
> modelo — amostragem estocástica do VITS. A melhora de +7,7 para +3,0 tem
> margem. Já a distância corpus↔holdout compara duas medidas da mesma rodada, e
> a queda dela (28,4 → 12,2 → 9,7) é o sinal mais confiável.

> **A métrica tinha dois defeitos, corrigidos aqui.** "18h45" virava
> `dezoitohquarenta` e "R$ 2.300" virava `dois trezentos`, porque a pontuação
> saía antes de os números serem soletrados. E a amostra "dentro do domínio"
> tinha seis frases: duas medições do mesmo modelo deram 17,4% e 26,9%, variação
> suficiente para virar o diagnóstico. São 20 frases agora.

> O VITS tem amostragem estocástica: a mesma época 113 mediu 39,2% numa rodada e
> 34,4% noutra, uns ±5 pontos de ruído. A queda do corpus está muito além disso.

*Sua avaliação (por voz):* naturalidade ___/5 · prosódia de pergunta ___/5 ·
números e horas ___/5 · cansa depois de 10 usos? ___

---

## Reconhecimento de locutor

Modelo separado do STT (ECAPA-TDNN, 80 MB). Medido com as suas sete gravações:

| Comparação | Similaridade |
|---|---|
| Sua voz × sua própria média | **0,69 – 1,00** |
| Sua voz × Piper faber | 0,21 (máx 0,24) |
| Sua voz × edge Francisca | 0,24 (máx 0,26) |
| Sua voz × MMS | 0,02 (máx 0,08) |

Separação larga: o seu pior caso está bem acima do melhor caso de um impostor.
Limiar fixado em 0,45, no meio do vazio. Ver [`docs/voz-e-locutor.md`](../docs/voz-e-locutor.md).

---
## STT — sua voz, microfone Fifine (o que decide)

Uma única gravação de cada frase, pontuada por todos os modelos. Mesma tomada,
mesmas hesitações, mesmo ruído: é a comparação justa.

| | Motor | Modelo | WER | CER | RTF | Tamanho |
|---|---|---|---|---|---|---|
| | **faster-whisper** | **small / int8** | **9,0%** | **6,4%** | 0,43 | 464 MB |
| | faster-whisper | base / int8 | 24,7% | 7,6% | 0,14 | 145 MB |
| `[PT]` | wav2vec2 XLSR | large-pt | 29,4% | 16,0% | 0,18 | 1,2 GB |
| `[PT]` | Vosk | small-pt-0.3 | 37,2% | 33,0% | 0,37 | 50 MB |
| | faster-whisper | tiny / int8 | 45,6% | 24,7% | **0,12** | 75 MB |

**O `small` ganha, e ganha limpo.** Mas o número agregado esconde o que importa:
**todo o erro dele está nas duas frases que menos parecem um comando.**

| Frase | small |
|---|---|
| `curta` "Timer de dez minutos" | 0% |
| `pergunta` "acender a luz do quarto e baixar o volume" | 0% |
| `estrangeirismo` "playlist Discover Weekly no Spotify" | 0% |
| `acentos` "o avô do José põe açúcar" | 0% |
| `hora` "quinze para as sete" | 11,1% (comeu o "as") |
| `numeros` CEP e moeda | 25% |
| `longa` previsão do tempo | 26,7% |

Nas cinco frases que se parecem com o que você vai falar com o aparelho, o
`small` dá **2,2% de WER**. O `base`, nas mesmas cinco, dá 18,9% — erra
"Timer" ("Time-er"), erra "Toca" ("Taca"). Para um roteador que casa regex,
isso é a diferença entre acertar e não acionar.

**wav2vec2 confirmou o padrão previsto:** erra fonema, nunca frase. "Timer"
virou "taimer", "põe" virou "poeha" — sempre reconhecível, nunca inventado. E
o CER (16,0%) é metade do Vosk (33,0%) com WER parecido. Continua rodando sem
modelo de linguagem (`kenlm` não instala no Windows); com ele o número melhora.

**Vosk piorou na voz real** — 37,2% de WER, 33,0% de CER. Está fora.

**tiny está fora também:** 45,6% na voz real contra 31,7% no áudio sintético. É
o modelo que mais sofre com voz humana, exatamente o oposto do que se quer.

### A tensão que sobra

`small` acerta, mas RTF 0,43 no PC vira algo entre 1,3 e 2,2 na Pi 5 — acima do
tempo real, fora dos 200–500 ms da seção 11. `base` cabe com folga (RTF 0,14),
mas erra os comandos.

Três saídas, em ordem de preferência:

1. **whisper.cpp ou sherpa-onnx** com o mesmo `small`. São muito mais rápidos
   que o faster-whisper em ARM — é provável que isso resolva sozinho.
2. **`base` na Pi só para o roteador, `small` no gateway para o LLM.** Responde
   a pergunta que a decisão [D1](../docs/decisions.md) deixou em aberto: o nível
   0 só precisa casar "põe um timer", e para isso o `base` basta; a pergunta que
   vai para o LLM sobe como áudio e é transcrita direito no servidor.
3. **Aceitar o `small` na Pi** e gastar a latência, se as duas primeiras falharem.

Nenhuma decide sem medir na Pi. Mas já dá para comprar o hardware sabendo que o
software funciona.

---

## STT — áudio sintético (só para referência)

Ranqueia modelos entre si de forma barata, mas **não escolhe o vencedor**: o
mesmo `small` deu 4,1% no áudio do edge-tts, 17,6% no do Piper e 9,0% na sua
voz. Mede tanto a dicção do TTS quanto o ouvido do modelo.

| Motor | Modelo | WER (Piper) | CER | RTF |
|---|---|---|---|---|
| faster-whisper | small | 17,6% | 10,1% | 0,65 |
| faster-whisper | base | 28,3% | 14,7% | 0,21 |
| faster-whisper | tiny | 31,7% | 15,2% | 0,12 |
| wav2vec2 | large-pt | 32,6% | 16,6% | 0,24 |
| Vosk | small-pt | 33,1% | 26,0% | 0,42 |

Repetir sobre a sua voz:

```powershell
.\.venv\Scripts\python.exe -m lab.run_stt --engine faster-whisper --size tiny,base,small --source voice
.\.venv\Scripts\python.exe -m lab.run_stt --engine wav2vec2 --size large --source voice
```

`--source voice` pontua as gravações já feitas, sem regravar.

---

## Fila de testes

1. ~~Piper~~ · ~~MMS~~ · ~~whisper tiny/base/small~~ · ~~wav2vec2 pt~~ · ~~Vosk~~
2. ~~Gravar a voz real e refazer a varredura de STT~~ — **feito, o `small` ganhou**
   e virou produção em `device/stt/` (D13). No PC, com o modelo carregado:
   frases de 2,5 a 3,8 s transcritas em ~2,2 s — RTF de 0,6 a 0,9, acima dos
   0,43 medidos aqui. A bancada mede o motor; o dispositivo paga também o
   `beam_size=5` e o resto do processo. É o número que a Pi tem que bater.
3. ~~Ouvir as vozes do Piper~~ — jeff e cadu aprovadas; voz final sai do fine-tune
3b. ~~Gravar o bloco 3 e retreinar~~ — v2 rodando com 30,9 min e lr 1e-4
3c. **Ouvir a série exportada e rodar o generalize** — decide se 30 min bastam
4. whisper.cpp / sherpa-onnx com o `small`: se forem rápidos o bastante em ARM,
   resolvem a tensão da seção acima sem concessão de qualidade
5. wav2vec2 com decoder kenlm, direto na Pi
6. Medir o vencedor com streaming, cronometrando o primeiro chunk
