# Referência de comandos

Todos os comandos da bancada num lugar só, com os parâmetros reais de cada um.
Os READMEs explicam *por quê*; este arquivo responde *como*, sem precisar
procurar.

Em todos os exemplos, **`py` significa `.\.venv\Scripts\python.exe`** e o
diretório de trabalho é a raiz do projeto. Rodando da raiz, o `-m` já coloca o
projeto no `sys.path` — não precisa de `PYTHONPATH`.

```powershell
# atalho para a sessão inteira do terminal
Set-Alias py .\.venv\Scripts\python.exe
```

---

## Índice

| Preciso… | Comando |
|---|---|
| Ver tudo que está na bancada | `py -m lab.list` |
| Escolher microfone e alto-falante | `py -m lab.devices` |
| Ouvir uma voz | `py -m lab.run_tts --engine piper --voice X --play` |
| Medir um STT na minha voz | `py -m lab.run_stt --engine faster-whisper --record` |
| Cadastrar / reconhecer quem fala | `py -m lab.run_speaker enroll Nome` |
| Gravar dataset para fine-tune | `py -m lab.finetune.record` |
| Conferir o dataset | `py -m lab.finetune.check` |
| Treinar a voz | `py -m lab.finetune.train --name X` |
| Exportar e ouvir | `py -m lab.finetune.train --export --name X` |
| Ver se decorou | `py -m lab.finetune.generalize --voice X --against Y` |
| Exportar sozinho durante o treino | `py -m lab.finetune.watch --name X` |
| Gravar mais e continuar sem recomeçar | `py -m lab.finetune.prepare --from-run X` |
| Rodar o assistente | `py -m uvicorn gateway.main:app` + `py -m device.main` |
| Testar o nível 0 sem gateway | `py -m device.main --text` (sem subir o gateway) |
| Autorizar o Spotify (uma vez) | `py -m gateway.tools.spotify_auth` |
| Reautorizar depois de mudar `SCOPES` (D32) | apagar `gateway/data/spotify_token.json` e rodar o comando acima |

---

## Setup

```powershell
Copy-Item .env.example .env
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest        # 28 testes
```

Para o fine-tune, o torch precisa vir com CUDA:

```powershell
py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124 --force-reinstall
py -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

> `setuptools` fica fixado em `<81` de propósito: o `webrtcvad` ainda importa
> `pkg_resources`, removido a partir daquela versão.

---

## Dispositivos de áudio

```powershell
py -m lab.devices
```

Lista microfones e alto-falantes, salva a escolha em `lab/.devices.json` e faz um
teste de 3 s dizendo se está **mudo**, **saturado** ou **ok**. Todos os outros
comandos reusam o que ficou salvo.

Para desviar pontualmente, sem mudar o padrão:

| Parâmetro | Onde vale | Exemplo |
|---|---|---|
| `--mic` | `run_stt`, `run_speaker`, `finetune.record` | `--mic fifine` ou `--mic 2` |
| `--speaker` | `run_tts`, `finetune.record` | `--speaker "Fone"` |
| `--pick` | `run_tts`, `run_stt` | reabre o seletor |

---

## Bancada — TTS

```powershell
py -m lab.run_tts --engine piper --voice pt_BR-jeff-medium --play
```

| Parâmetro | Valores | Padrão |
|---|---|---|
| `--engine` | `piper` `kokoro` `mms` `edge` | obrigatório |
| `--voice` | ver tabela abaixo | a primeira do motor |
| `--phrase` | uma das sete, `all`, ou `novas` | `all` |
| `--text` | qualquer texto entre aspas | — |
| `--play` | toca cada resultado | desligado |
| `--speaker` | dispositivo de saída | o salvo |
| `--pick` | escolher a saída agora | desligado |
| `--keep-rate` | não reamostrar para 16 kHz | desligado |

**Vozes disponíveis:**

| Motor | Vozes |
|---|---|
| `piper` | `pt_BR-jeff-medium` `pt_BR-cadu-medium` `pt_BR-miro-high` `pt_BR-dii-high` `pt_BR-faber-medium` `pt_BR-edresson-low` |
| `kokoro` | `pf_dora` `pm_alex` `pm_santa` |
| `mms` | voz única (`por`) |
| `edge` | `pt-BR-FranciscaNeural` `pt-BR-AntonioNeural` `pt-BR-ThalitaMultilingualNeural` — **nuvem, só referência** |

Saída em `lab/out/tts/<motor_voz>/`. O `--keep-rate` serve para julgar a
qualidade nativa do motor; sem ele tudo cai para os 16 kHz do pipeline.

> **Ao julgar uma voz de fine-tune, cuidado com o conjunto fixo.** Duas das sete
> frases — `curta` e `acentos` — estão no corpus de treino, e um modelo que
> decorou soa bem justamente nelas. Três saídas:
>
> - `--phrase novas` — as cinco da bancada que não foram treinadas
> - `--text "..."` — qualquer coisa que você digitar
> - `py -m lab.finetune.generalize --voice X --play` — as 15 do holdout, que
>   ninguém nunca gravou

---

## Bancada — STT

```powershell
# a sua voz (o que decide)
py -m lab.run_stt --engine faster-whisper --size tiny,base,small --record

# repontuar gravações já feitas, sem regravar
py -m lab.run_stt --engine faster-whisper --size small --source voice
```

| Parâmetro | Valores | Padrão |
|---|---|---|
| `--engine` | `faster-whisper` `wav2vec2` `vosk` | obrigatório |
| `--size` | separado por vírgula, ver abaixo | por motor |
| `--source` | `voice` ou uma pasta de `lab/out/tts/` | `voice` |
| `--record` | gravar do microfone agora | desligado |
| `--phrase` | uma das sete, ou `all` | `all` |
| `--seconds` | duração fixa | para sozinho no silêncio |
| `--mic` `--pick` | dispositivo de entrada | o salvo |
| `--quiet` | só a média por modelo | desligado |

**Tamanhos:**

| Motor | Tamanhos |
|---|---|
| `faster-whisper` | `tiny` `base` `small` `medium` `large-v3` |
| `wav2vec2` | `large` `1b` |
| `vosk` | `small` `big` |

Gravações vão para `lab/out/voice/`. Sem `--seconds`, a gravação termina sozinha
após 900 ms de silêncio.

---

## Reconhecimento de locutor

```powershell
py -m lab.run_speaker enroll Ideraldo
py -m lab.run_speaker who
```

| Ação | O que faz |
|---|---|
| `enroll <nome>` | grava 4 frases e cadastra a voz |
| `who` | grava uma e diz de quem é |
| `test` | pontua as gravações de `lab/out/voice/` |
| `list` | vozes cadastradas |
| `forget <nome>` | remove uma voz |

| Parâmetro | Padrão |
|---|---|
| `--takes N` | 4 gravações por pessoa |
| `--threshold` | 0,45 (medido: mesma pessoa 0,69–1,00; impostor 0,02–0,26) |
| `--mic` | o salvo |

Cadastro fica em `lab/speaker/voices.json` — só vetores, nenhum áudio é guardado.

---

## Fine-tune — gravar

```powershell
py -m lab.finetune.record
```

| Parâmetro | O que faz |
|---|---|
| `--status` | quantas frases e quantos minutos já existem |
| `--review N` | ouve a frase N e pergunta se quer refazer |
| `--redo SPEC` | apaga para regravar: `7`, `1-10` ou `2,7,15` |
| `--reset` | apaga tudo (pede confirmação digitada) |
| `--from N` | começa da frase N |
| `--silence MS` | silêncio que encerra a gravação (padrão 1600 ms) |
| `--mic` `--speaker` | dispositivos |

Retoma sempre de onde parou. Durante a gravação: **ENTER** grava, **p** pula,
**q** sai. Recusa automaticamente take baixo demais ou saturado.

A gravação encerra após 1600 ms de silêncio. O valor é generoso porque os
trechos do bloco 3 têm pausa **dentro** deles, entre os dois períodos — com 700
ms, que era o padrão antigo, a gravação cortava no ponto final do meio. Se ainda
cortar, `--silence 2200`.

Grava a 48 kHz (o máximo que o VAD aceita) e reamostra para 22050 com filtro
band-limited. Saída em `lab/finetune/dataset/`.

**O corpus tem 315 frases em três blocos** — 1 a 95 curtas, 96 a 203 longas,
204 a 315 trechos de 10–15 s. Ver `lab/finetune/README.md` para o porquê.

## Fine-tune — conferir antes de treinar

```powershell
py -m lab.finetune.check
```

Sem parâmetros. Procura gravações truncadas, saturadas, baixas ou fora de ritmo,
e já imprime o `--redo` correspondente para colar de volta.

## Fine-tune — treinar

```powershell
py -m lab.finetune.train --base pt_BR-dii-high --name ideraldo
py -m lab.finetune.train --name ideraldo --resume     # depois de interromper
```

| Parâmetro | Padrão | Observação |
|---|---|---|
| `--name` | `ideraldo` | nome do run e da voz |
| `--base` | `pt_BR-dii-high` | `pt_BR-miro-high` é a alternativa |
| `--epochs` | 1000 | ~20 mil passos com 315 gravações |
| `--batch-size` | 8 | baixe para 4 se faltar VRAM, suba se sobrar |
| `--lr` | 1e-4 | metade do padrão do Piper, que é para treino do zero |
| `--resume` | — | retoma com otimizador e época intactos |

O preparo do checkpoint base roda sozinho na primeira vez
(`lab.finetune.prepare --base X` para forçar; `--from-run NOME` parte do seu
próprio treino). Ele resolve PosixPath,
`weights_only` e o contador de época — sem isso o treino encerra na hora sem
fazer nada.

**Retomar depois de desligar o PC:** `--resume`. O `last.ckpt` é reescrito a cada
época. Se ele estiver corrompido por desligamento no meio da escrita, apague-o e
rode `--resume` de novo, que ele pega o `epoch=N` mais recente.

## Fine-tune — exportar

```powershell
py -m lab.finetune.train --list --name ideraldo             # o que dá para exportar
py -m lab.finetune.train --export --name ideraldo           # o mais recente
py -m lab.finetune.train --export --epoch 318 --name ideraldo
```

| Parâmetro | O que faz |
|---|---|
| `--list` | lista os checkpoints do run, com horário |
| `--export` | exporta o mais recente |
| `--epoch N` | exporta aquela época; nome vira `<name>ep<N>` |
| `--checkpoint <arquivo>` | aponta um `.ckpt` exato |
| `--as <nome>` | nome da voz de saída |

Dá para exportar **com o treino rodando**, em outro terminal — não usa a GPU. Uma
época pode ter dois arquivos (`val_mel` e `val_mos`); são o mesmo modelo.

> **Exporte a cada algumas centenas de épocas, não só no fim.** O
> `ModelCheckpoint` do Lightning guarda apenas os melhores e vai apagando os
> antigos: no treino v1, quando fui arquivar, as épocas anteriores à 247 já não
> existiam mais. Só a 113 sobreviveu, porque eu tinha exportado o `.onnx` na
> hora. São 60 MB contra 845 MB de um checkpoint.

O `.onnx` e o `.onnx.json` caem em `lab/models/piper/`, então a voz entra na
bancada como qualquer outra.

## Fine-tune — exportar sozinho durante o treino

```powershell
py -m lab.finetune.watch --name ideraldo
```

Segundo terminal, ao lado do treino. Exporta um `.onnx` a cada 100 épocas.

| Parâmetro | Padrão |
|---|---|
| `--name` | `ideraldo` — o run em andamento |
| `--base` | `pt_BR-dii-high` — de onde copiar o config |
| `--every` | 100 épocas |
| `--interval` | 300 s (5 min) entre verificações |

Existe porque o `ModelCheckpoint` guarda só os cinco melhores e apaga os antigos:
no v1 as épocas anteriores à 247 sumiram antes de eu arquivar. Ctrl+C nele não
afeta o treino.

## Fine-tune — gravar mais depois, sem recomeçar

```powershell
py -m lab.finetune.record                                   # o bloco novo
py -m lab.finetune.prepare --from-run ideraldo --as ideraldo-v2
py -m lab.finetune.train --base ideraldo-v2 --name ideraldo3
```

| Parâmetro | O que faz |
|---|---|
| `--base` | voz publicada a preparar (padrão `pt_BR-dii-high`) |
| `--from-run NOME` | parte do seu próprio treino em vez de uma voz publicada |
| `--as NOME` | nome do checkpoint preparado |

O `prepare` mantém os pesos, zera o contador de época e descarta o otimizador —
que pertence ao treino antigo, com o dataset antigo. Você recomeça já com o
timbre pronto, e as épocas novas só absorvem o material novo.

Roda sozinho na primeira vez que você treina de uma voz publicada; só precisa ser
chamado à mão para partir do seu próprio treino.

## Fine-tune — verificar se decorou

```powershell
py -m lab.finetune.generalize --voice pt_BR-ideraldo-medium --against pt_BR-dii-high
```

| Parâmetro | O que faz |
|---|---|
| `--voice` | voz a testar (obrigatório) |
| `--against` | voz base para comparar |
| `--play` | tocar cada frase do holdout |
| `--quiet` | só as médias |

Sintetiza 15 frases nunca gravadas, transcreve de volta com o Whisper, e compara
com o WER do próprio corpus:

| Corpus | Holdout | Diagnóstico | O que fazer |
|---|---|---|---|
| alto | alto | **ainda cru** | treinar mais |
| baixo | alto | **decorou** | voltar checkpoint / gravar mais |
| baixo | baixo | **saudável** | pronto |

> **A medição usa síntese determinística.** O VITS amostra ruído a cada geração,
> e sem desligar isso a mesma voz mede WER diferente a cada rodada — vimos nove
> pontos de diferença, o bastante para inverter um diagnóstico. O `generalize`
> zera `noise_scale` e `noise_w_scale`; para **ouvir**, use o `run_tts`, que
> mantém o ruído normal e deixa a voz soar viva.

---

## Rodar o assistente

```powershell
ollama serve                          # ou o app do Ollama
py -m uvicorn gateway.main:app        # terminal 2
py -m device.main                     # terminal 3
```

| Flag de `device.main` | Para quê |
|---|---|
| `--text` | digitar em vez de falar; não carrega o STT |
| `--verbose` | log do STT, da captura e do que sobe para o LLM |

O laço completo roda no dispositivo: microfone → VAD → STT → roteador. Nenhum
áudio cruza a rede em nenhuma direção ([D13](../../docs/decisions.md)) — o que
sobe é a frase transcrita, e só quando o roteador não resolve sozinho.

**Timer, alarme e hora funcionam com o gateway desligado** ([D17](../../docs/decisions.md)).
Para conferir isso, é só não subir o terminal 2:

```powershell
py -m device.main --text
# voce: poe um timer de 5 segundos
#   ideraldinho> Timer de 5 segundos.   [nivel 0, local]
```

Variáveis relevantes em `.env`:

| Variável | Para quê |
|---|---|
| `GATEWAY_URL` | o único endereço que o dispositivo conhece |
| `DEVICE_TOKEN` | tem que bater dos dois lados |
| `TTS_VOICE` / `TTS_VOICE_DIR` | qual voz o dispositivo carrega, e de onde |
| `SIMULATED_LATENCY_MS` | 80 = Wi-Fi, 150 = 4G |
| `LLM_PROVIDER` / `LLM_MODEL` | `ollama` / `qwen3:8b` (D20; `ollama pull qwen3:8b`) |
| `LLM_THINK` | `false`: o qwen3 raciocina por padrao, e o rascunho vira fala |
| `LLM_TIMEOUT` | suba se o modelo local estiver sem GPU livre |
| `AUDIO_INPUT_DEVICE` / `AUDIO_OUTPUT_DEVICE` | áudio do dispositivo |
| `STT_MODEL` / `STT_COMPUTE_TYPE` / `STT_MODEL_DIR` | qual Whisper o dispositivo carrega |
| `VAD_SILENCE_MS` / `VAD_AGGRESSIVENESS` | quanto silêncio encerra a fala |
| `SCHEDULES_DB` | onde timers e alarmes ficam entre um boot e outro |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | sem elas, as ferramentas de musica nao existem (D21) |
| `SPOTIFY_REDIRECT_URI` | tem que bater com o dashboard; `127.0.0.1`, nunca `localhost` |
| `SPOTIFY_TOKEN_PATH` / `SPOTIFY_MARKET` | onde o refresh token fica, e o pais da busca |
| `SPOTIFY_DEVICE` | onde tocar quando ninguem disser onde; aponta para a Pi (D23) |
| `SEARCH_PROVIDER` | `duckduckgo` (sem chave), `brave` (com `SEARCH_API_KEY`) ou `none` |
| `SEARCH_REGION` | regiao da busca; `br-pt` |

> **Não rode o Ollama enquanto o fine-tune treina.** Os dois disputam a mesma
> placa e o treino morre com `CUDA out of memory` — aconteceu. `ollama stop
> llama3.1:8b` libera a GPU; depois é só `--resume`.

---

## Onde cada coisa fica

| Caminho | O que é | No git? |
|---|---|---|
| `lab/out/tts/<motor>/` | áudio gerado pelos TTS | não |
| `lab/out/voice/` | suas gravações de teste de STT | não |
| `lab/out/generalize/` | áudio do teste de holdout | não |
| `lab/models/piper/` | vozes `.onnx` | não |
| `lab/models/piper_ckpt/` | checkpoints base e preparados | não |
| `lab/finetune/dataset/` | o dataset de treino | não |
| `lab/finetune/runs/<nome>/` | checkpoints do treino | não |
| `lab/finetune/arquivo/` | vozes .onnx guardadas de treinos antigos | só o LEIAME |
| `lab/models/piper_ckpt/` | checkpoints base e os preparados por `--from-run` | não |
| `lab/.devices.json` | microfone e alto-falante escolhidos | não |
| `lab/speaker/voices.json` | vozes cadastradas | não |

Nada disso vai para o repositório: são dezenas de GB de modelo e áudio. O que
entra no git é o código, os corpora e a documentação.

---

## Documentação relacionada

| Arquivo | O que responde |
|---|---|
| `lab/README.md` | por que a bancada existe e como ler os números |
| `lab/finetune/README.md` | o processo do fine-tune, com os porquês |
| `lab/RESULTS.md` | os números medidos de cada motor |
| `docs/decisions.md` | decisões fechadas e o que elas quebram |
| `docs/diario-de-bordo.md` | a história: dúvidas, tentativas, falhas |
