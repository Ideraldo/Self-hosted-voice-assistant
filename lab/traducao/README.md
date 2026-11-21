# lab/traducao — a peça do meio do modo tradutor

Bancada para o marco "tradutor portátil": falar português e o aparelho falar
inglês, e o contrário. STT (faster-whisper) e TTS (Piper) já existem; aqui se
mede só o que falta, **texto → texto**, em CTranslate2 — o mesmo runtime que o
`faster_whisper` já usa na Pi.

```
lab/traducao/
  frases.py        62 pares pt-BR <-> en com referência — a constante da comparação
  modelos.py       os candidatos, as etiquetas de idioma e o que não existe
  run_traducao.py  traduz, cronometra, mede memória e pontua com chrF
```

## O que isto mede e o que não mede

Mede o motor sozinho, com a frase escrita certa: tamanho em disco, tempo de
carga, memória (delta de RSS), latência por frase (mediana e p90), tokens/s e
chrF contra uma referência escrita à mão.

**Não mede** o que acontece com a transcrição real do Whisper (sem pontuação
confiável, com palavra trocada) e **não mede a Pi**. Texto limpo elogia todo
tradutor, do mesmo jeito que áudio sintético elogia todo STT. É triagem: serve
para descartar, não para aprovar.

chrF contra **uma** referência pune tradução certa com outras palavras. Serve
para ranquear modelos entre si; quem aprova é o olho — por isso o runner imprime
todas as saídas.

## Instalar

O runner só precisa de `ctranslate2`, `sentencepiece`, `sacrebleu` e `psutil`.
A **conversão** precisa também de `transformers` e `torch` — só no PC, nunca no
dispositivo.

```powershell
py -m pip install ctranslate2 sentencepiece sacrebleu psutil      # rodar
py -m pip install transformers torch huggingface_hub              # converter
```

## Baixar e converter

O `ct2-transformers-converter` baixa do Hugging Face e converte num passo. Os
modelos vão para `lab/models/traducao/`, que está fora do git (D12).

```powershell
$M = "lab\models\traducao"

# pt -> en
ct2-transformers-converter --model Helsinki-NLP/opus-mt-ROMANCE-en --output_dir $M\opus-mt-ROMANCE-en-int8 --quantization int8 --copy_files source.spm target.spm vocab.json
ct2-transformers-converter --model Helsinki-NLP/opus-mt-tc-bible-big-roa-en --output_dir $M\opus-mt-tc-bible-big-roa-en-int8 --quantization int8 --copy_files source.spm target.spm vocab.json

# en -> pt
ct2-transformers-converter --model Helsinki-NLP/opus-mt-en-ROMANCE --output_dir $M\opus-mt-en-ROMANCE-int8 --quantization int8 --copy_files source.spm target.spm vocab.json
ct2-transformers-converter --model Helsinki-NLP/opus-mt-en-roa --output_dir $M\opus-mt-en-roa-int8 --quantization int8 --copy_files source.spm target.spm vocab.json
ct2-transformers-converter --model Helsinki-NLP/opus-mt-tc-big-en-pt --output_dir $M\opus-mt-tc-big-en-pt-int8 --quantization int8 --copy_files source.spm target.spm vocab.json

# o teto de comparação (não-candidato no dispositivo)
ct2-transformers-converter --model facebook/nllb-200-distilled-600M --output_dir $M\nllb-200-distilled-600M-int8 --quantization int8 --copy_files sentencepiece.bpe.model tokenizer.json
```

No Linux é o mesmo com `/` e `M=lab/models/traducao`. Atrás de proxy com lista
de hosts: o download passa por `huggingface.co` e os pesos vêm de um CDN à parte
(`us.aws.cdn.hf.co` em 2026-11) — liberar só o primeiro baixa o `config.json` e
falha no `pytorch_model.bin`.

`opus-mt-roa-en` não está na lista de propósito: é byte a byte o mesmo modelo que
o `opus-mt-ROMANCE-en`.

## Rodar

Sempre da raiz do projeto, como o resto do `lab/`.

```powershell
# a comparação inteira: todos os modelos, os dois sentidos, 4 threads e 1
py -m lab.traducao.run_traducao --model all --direction both --threads 4,1 --save

# um modelo, um sentido, lendo as saídas
py -m lab.traducao.run_traducao --model romance-en --direction pt-en --threads 4

# cortar a entrada em orações antes de traduzir (ver "Uma oração por vez")
py -m lab.traducao.run_traducao --model all --direction both --threads 2,1 --split --save

# a variante do português na saída (ver "pt-BR × pt-PT")
py -m lab.traducao.run_traducao --model big-en-pt --direction en-pt --tag por
py -m lab.traducao.run_traducao --model en-romance --direction en-pt --tag pt_br

# greedy em vez de beam 4: mais rápido, um pouco pior
py -m lab.traducao.run_traducao --model romance-en,en-romance --beam 1 --quiet

# só uma categoria ou uma frase
py -m lab.traducao.run_traducao --model nllb-600m --phrase coloquial
```

`--models-dir` (ou a variável `LAB_TRADUCAO_MODELS`) aponta para outra pasta de
modelos. `--save` grava cada rodada em `lab/out/traducao/*.json`, fora do git.

## Como ler os números

**th** — threads do CTranslate2 (`intra_threads`). 4 é a Pi inteira; 1 dá uma
ideia de como a latência escala para um núcleo mais lento. **Um núcleo x86 de
servidor não é um Cortex-A76**: a linha de 1 thread é ordem de grandeza, não
previsão. E mais thread não é mais rápido de graça — com frase curta o custo de
sincronizar pode passar o ganho (na VM da primeira medição, 4 threads foram mais
lentas que 1 nos modelos pequenos). Meça 1, 2 e 4 na máquina que importa.

**carga** — abrir o modelo. Paga-se uma vez no boot, como no Whisper.

**RAM MB** — quanto o RSS do processo cresceu entre antes de carregar e depois
de traduzir tudo. Cada medição roda num processo filho para um modelo não sujar
a memória do outro.

**med ms / p90 ms** — latência por frase, uma frase por vez (é assim que o
aparelho vai chamar). Cada frase roda 3 vezes e vale a mediana.

**tok/s** — tokens gerados pelo decoder por segundo. Marian e NLLB são
autorregressivos: o custo cresce com o tamanho da *saída*.

**chrF** — sobreposição de n-gramas de caractere com a referência, 0 a 100.

**pt-PT** — quantas saídas en→pt têm marca inequívoca de português europeu
("casa de banho", "autocarro", "estou a fazer", "tens"). Detector barato em
`frases.py`, erra para menos.

## Uma oração por vez

Estes modelos foram treinados com uma oração por linha. Diante de duas, costumam
traduzir uma e **jogar a outra fora, sem erro**: "Está muito caro. Tem
desconto?" vira "It's too expensive." Acontece em todos, inclusive no NLLB.

`--split` corta a entrada na pontuação final e traduz cada oração separada. No
dispositivo isso tem que ser regra, não opção — e depende de o Whisper pontuar.

## pt-BR × pt-PT

É o ponto que mais importa nesta bancada, e ele só existe no sentido en→pt:

| Modelo | Etiquetas | |
|---|---|---|
| `opus-mt-en-ROMANCE` | `>>pt_BR<<` `>>pt_br<<` `>>pt<<` `>>pt_PT<<` | escolhe a variante — e as quatro são diferentes |
| `opus-mt-en-roa` | `>>por<<` | não distingue |
| `opus-mt-tc-big-en-pt` | `>>pob<<` `>>por<<` | `pob` = brasileiro |
| NLLB-200 | `por_Latn` | não distingue |

No `en-ROMANCE`, medido: `pt_BR` é brasileiro e literal; `pt_br` é brasileiro
com cara de legenda (enxuga, corta a segunda oração); `pt` é europeu; e `pt_PT`
está **quebrada** — sai uma mistura de espanhol, italiano e português ("La
maleta non arrivò"). Maiúscula e minúscula aqui são modelos de comportamento
diferente, não grafias da mesma coisa.

A etiqueta entra como **primeiro token** da frase de origem. Esquecer a etiqueta
num modelo de saída múltipla não dá erro: dá outro idioma.

No sentido pt→en não há etiqueta, o modelo aceita qualquer português.

Os nomes que parecem óbvios **não existem** no Hugging Face: `opus-mt-pt-en`,
`opus-mt-en-pt`, `opus-mt-tc-big-pt-en`. Está anotado em `modelos.py`.

## Licenças

Conferido no Hugging Face: os opus-mt são Apache-2.0, menos o
`opus-mt-tc-big-en-pt`, que é CC-BY 4.0 (pede atribuição). O NLLB-200 é
**CC-BY-NC** — uso não comercial; mais um motivo para ele ser só régua.

## Resultados

Vão para [`../RESULTS.md`](../RESULTS.md), como o resto da bancada.
