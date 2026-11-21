# Estrutura do repositório

Documento complementar ao [ultraplan v3](ultraplan-v3-assistente-voz-portatil.md).
Explica **onde cada parte do sistema se encaixa** e o critério para decidir onde
colocar código novo. O plano define o *quê*; este arquivo define o *onde*.

Nomes de pastas e código são em inglês; a documentação é em português.

Onde a construção divergiu do plano, o motivo está em [decisions.md](decisions.md)
— vale ler antes deste arquivo, porque a decisão D1 muda onde STT e TTS moram. O
caminho até cada uma dessas decisões, com o que foi tentado e falhou, está no
[diário de bordo](diario-de-bordo.md).

---

## 1. A divisão de topo: por processo, não por camada técnica

A seção 14 do plano fixa duas coisas como inegociáveis. A primeira é **dois
processos separados desde o primeiro commit**, conversando exclusivamente por
WebSocket. A estrutura reflete isso literalmente:

| Pasta | O que é | Onde roda hoje | Onde roda depois |
|---|---|---|---|
| `device/` | O aparelho | processo no PC (mic + alto-falante) | Raspberry Pi 5 |
| `gateway/` | O servidor | `uvicorn` no PC, sem contêiner | Docker numa VPS em São Paulo |
| `common/` | O contrato entre os dois | importado pelos dois | idem |
| `lab/` | A bancada de avaliação | só no PC | não vai para lugar nenhum |

O contêiner do gateway existe e já rodou — numa máquina Linux com Docker Engine
e, desde então, no CI a cada push —, mas não é como o gateway roda no dia a dia:
no PC de desenvolvimento não há Docker ([D16](decisions.md)). O que foi e o que
não foi verificado está em [gateway-e-modelo.md](gateway-e-modelo.md).

A linha entre `device/` e `gateway/` é uma **fronteira de rede**. Um
`from gateway.llm import ...` dentro de `device/` quebra a regra 1 da seção 2 —
na Pi esse import não existiria. A checagem mental é sempre a mesma:
*isso vai existir na Pi, ou só no servidor?*

Para decidir onde colocar processamento novo, vale a regra 4 da seção 2:
**na dúvida, empurre para o `gateway/`**. O PC de desenvolvimento é muito mais
rápido que a Pi, e o que couber no servidor não vira problema de hardware.

---

## 2. `common/` — só o contrato

As duas pontas precisam concordar sobre o formato das mensagens, e essa é a
única coisa que podem compartilhar.

```
common/
  messages.py        dataclasses do protocolo da seção 4 + formato de áudio
  serialization.py   encode/decode JSON das mensagens de controle
```

Se cada lado definisse a sua versão, um dia o gateway manda `utterance` e o
device ainda manda `audio_end` — e isso aparece na Pi, não no PC.

`common/` é pequeno de propósito: **contrato, nunca lógica**. Se algo aqui só
interessa a um dos lados, está no lugar errado.

---

## 3. `device/` — organizado pela máquina de estados

```
device/
  activation/   o que ACORDA o aparelho. Hoje só wake.py, o wake word
                (D30); botão GPIO e detecção de energia (seção 7)
                estão no plano e não existem
  audio/        capture.py (microfone + VAD) e playback.py
  stt/          transcrição local: faster-whisper, antes do fio (D13)
  router/       roteador de intenções (seção 5): decide se resolve
                aqui ou manda para o gateway. Hoje só regex (D17)
  local/        timers, alarmes, a hora — SQLite + agendador, sem rede
  rosto/        o rosto (D27): servidor local + static/ com a página
  face/         vazia (só um .gitkeep): o nome que o plano dava ao
                rosto. O rosto de verdade é rosto/
  state.py      IDLE → LISTENING → THINKING → SPEAKING, e o gancho
                de observador que o rosto assina
  tts/          síntese local: a voz própria, treinada em lab/finetune
  ws_client.py  a ÚNICA conexão com o mundo externo
  config.py     áudio, STT, voz, wake word, rosto e banco de
                agendamentos, por variável de ambiente
  main.py       entrypoint do processo (--text, --verbose, --rosto)
  data/         schedules.db, criado ao rodar — fora do git
```

O fluxo: `activation/` dispara (se o wake word estiver ligado) → `audio/`
captura → `stt/` transcreve → `router/` decide → ou `local/` executa, ou
`ws_client.py` manda para o gateway → `tts/` fala a resposta.
`rosto/` reflete `state.py` o tempo todo — e por assinatura, não por consulta:
`StateMachine.transition()` avisa quem se registrou, e o rosto é um dos
registrados. Ele fica *dentro* do `transition` porque todas as ~8 chamadas do
`main.py` já passam por lá; espalhar o aviso por elas seria uma chance de
esquecer uma.

**Por que `local/` é separado de `router/`:** o roteador só *classifica*; quem
*executa* é `local/`. E a execução local acontece venha a ordem de onde vier,
inclusive de um `tool_call` devolvido pelo gateway — a segunda regra
inegociável da seção 14. É isso que faz o despertador tocar com a internet caída.

`rosto/` é pacote Python (tem servidor), mas `rosto/static/` não: ali dentro é
HTML, CSS e JS servidos para o Chromium, e nada de lá é importado.

**A camada não pode derrubar o aparelho.** Observador que levanta exceção vira
log; servidor que não consegue abrir a porta devolve `False` e o turno continua.
A tela é vitrine, o timer é função — e essa ordem é a decisão, não um acidente.

---

## 4. `gateway/` — camadas trocáveis atrás de interfaces

```
gateway/
  api/             WebSocket + autenticação por token
  llm/             base.py (Protocol LLMProvider) + ollama.py
  tts/             base.py (Protocol TextToSpeech) + stub.py, que emite
                   silêncio. Sobra da Fase 0: nada importa este pacote
                   desde a D1, e o cache de frases fixas do plano
                   nunca foi escrito
  tools/           device_tools.py: o LLM pede, o dispositivo executa (D18)
                   spotify.py: o gateway executa, porque aqui há segredo (D21)
                   spotify_auth.py: consentimento, roda uma vez
                   search.py: busca na internet, provedor trocável (D24)
  data/            refresh token do Spotify — fora do git e fora da imagem
  conversation/    histórico e montagem de contexto
  timing.py        marca e loga o tempo de cada etapa do turno
  config.py        segredos e parâmetros — nunca saem do servidor
  main.py          o app FastAPI, /health e build_llm; sobe com uvicorn
  Dockerfile       imagem enxuta: só requirements-gateway.txt (D15)
  docker-compose.yml   o gateway, com o volume gateway-data para o token
```

Na raiz, ao lado deles: `requirements-gateway.txt` (o que a imagem instala) e
`.dockerignore` (o contexto de build é a raiz, por causa de `common/` — sem
ignorar, `.venv` e `lab/models/` viajariam junto, e `.env` e `gateway/data/`
levariam segredo para dentro da imagem).

`llm/` segue o padrão que o plano pede: `base.py` define a interface, o arquivo
vizinho é uma implementação, e **nada fora de `build_llm` (em `main.py`) sabe
qual está ativa**. É isso que permite trocar o Ollama por uma API de nuvem sem
tocar em `api/`. Hoje há uma implementação só. `tools/search.py` repete o
desenho em arquivo único: `SearchProvider`, com `DuckDuckGo` e `Brave` atrás.

`conversation/` é separado de `llm/` porque histórico e montagem de prompt não
mudam quando o provedor muda — e o cache de prefixo da seção 6, que pesa mais na
conta que a escolha do modelo, mora aqui.

---

## 5. `tests/`, `scripts/`, `lab/` e o CI

Nenhuma destas **está na seção 3 do plano**; foram acrescentadas durante a
construção.

- **`tests/`** — a seção 10 é escrita inteira em critérios de aceite, e boa
  parte deles é testável. Roda sem microfone, sem modelo e sem rede
  (`python -m pytest -q`). Um arquivo por assunto:

  | Arquivo | O que protege |
  |---|---|
  | `test_state_machine.py` | as transições legais do dispositivo |
  | `test_rosto.py` | o gancho de observador e o servidor do rosto |
  | `test_router.py` | o que o roteador de nível 0 reconhece e o que recusa |
  | `test_local.py` | timers e alarmes com a rede fora |
  | `test_wake.py` | o encanamento do wake word, sem modelo |
  | `test_reconnect.py` | o dispositivo contra um gateway WebSocket real que cai |
  | `test_serialization.py` | o contrato de mensagens de `common/` |
  | `test_session_protocol.py` | o turno no gateway: texto, nunca áudio |
  | `test_conversation.py` | prompt de sistema e histórico |
  | `test_tools.py` | ferramentas de agenda, dos dois lados do fio |
  | `test_spotify.py` | o cliente do Spotify contra um HTTP falso |
  | `test_busca.py` | a busca e a leitura da primeira página |
  | `test_metrics.py` | a pontuação WER/CER do `lab/` |

- **`scripts/`** — utilitários de desenvolvimento, que não rodam em produção.
  Dois hoje: `rosto.py` dirige o rosto pelo teclado, sem microfone nem STT, e
  `wake.py` mostra o score do wake word ao vivo e conta falso positivo por
  hora. O que o plano previa para cá — medir latência, pré-gerar cache de TTS —
  não foi escrito.
- **`lab/`** — a bancada onde os motores são medidos antes de virarem
  implementação. Ver adiante.
- **`.github/workflows/ci.yml`** — dois trabalhos a cada push e a cada pull
  request. `testes` roda a suíte inteira instalando só
  `requirements-gateway.txt` mais pytest e numpy. `container` constrói a imagem
  do gateway, sobe o compose, espera o `/health` e confere que nem o `.env` nem
  o token do Spotify estão dentro da imagem e que o processo não é root.

---

## 6. `lab/` — escolher um motor com número, não com opinião

O plano deixa STT e TTS em aberto ("API de nuvem, trocável por faster-whisper";
"Piper ou TTS de nuvem"). `lab/` nasceu para fechar essas escolhas e passou a
abrigar também os treinos: a voz e o wake word.

```
lab/
  phrases.py     as frases pt-BR de teste — a constante de toda comparação
  registry.py    quais motores existem, com a flag [PT] de especialista
  devices.py     escolha de microfone e alto-falante
  speaker/       reconhecimento de locutor (quem falou, não o quê)
  run_speaker.py cadastro e teste de vozes
  finetune/      o treino da voz do assistente (D4), com README próprio
  wakeword/      gerar, treinar e medir o wake word (D31), com README próprio
  traducao/      bancada de tradução para o modo tradutor — está entrando
                 agora, ainda sem veredito
  list.py        python -m lab.list
  run_tts.py     sintetiza o conjunto e mede tempo e RTF
  run_stt.py     transcreve e pontua com WER/CER
  tts/  stt/     um arquivo por motor candidato
  metrics.py     normalização + WER/CER
  numbers.py     soletra dígitos: "10" e "dez" precisam pontuar igual
  audio.py       wav, playback, gravação de microfone
  out/  models/  áudio gerado e modelos baixados (fora do git)
  docs/          referência de comandos e parâmetros de todos os scripts
  RESULTS.md     o veredito, com a avaliação subjetiva
```

Os `Protocol` em `lab/tts/base.py` e `lab/stt/base.py` foram escritos para o
vencedor migrar como implementação, sem reescrita. Depois da D1 e da D13 o
destino dessa migração é `device/stt/` e `device/tts/`, não o gateway.

`lab/` **não roda em produção** e pode importar o que quiser dos dois lados —
é a única pasta com essa liberdade, porque não existe nem na Pi nem na VPS.
Detalhes de uso em `lab/README.md`.

---

## 6b. `docs/` — o plano, as decisões, e um guia por tema

```
docs/
  ultraplan-v3-...md      a especificação; não é reescrita
  decisions.md            onde a construção divergiu do plano, e por quê
  diario-de-bordo.md      a narrativa: dúvidas, tentativas, o que quebrou
  repository-structure.md este arquivo
  arquitetura.md          a aplicação em diagramas, tirados do código
  nivel-0-offline.md      timer, alarme, hora e roteador sem rede
  gateway-e-modelo.md     contêiner, escolha do LLM, ferramentas, busca
  spotify.md              tipo, playlists, aparelhos, escopos
  wake-word.md            limiar, treino, resultados
  rosto.md                temas, estados, custo de CPU
  voz-e-locutor.md        voz do assistente e reconhecimento de locutor
```

A divisão de trabalho entre eles: o **ultraplan** diz o que era para ser, o
**decisions** diz o que ficou diferente e por quê, o **diário** conta como se
chegou lá, e os guias por tema dizem como usar o que existe hoje. O `README.md`
é só a porta de entrada — visão geral, setup, uso — e aponta para estes.

Documentação de tema nova nasce aqui quando uma seção do README passa a precisar
de mais que meia página.

---

## 7. Onde colocar um arquivo novo

1. Precisa existir na Pi? → `device/`
2. Só no servidor? → `gateway/`
3. As duas pontas precisam concordar sobre isso? → `common/`
4. É um motor sendo avaliado, ou a medição dele? → `lab/`
5. Não roda em produção? → `scripts/` ou `tests/`

O rosto vai em `device/rosto/`, não em `device/face/`.

E uma decisão que contraria o plano? → registre em [`decisions.md`](decisions.md)
antes de escrever o código. A decisão **D1** já move STT e TTS do gateway para o
dispositivo, então a leitura da seção 4 acima muda: `gateway/tts/` continua
existindo como interface que ninguém importa, e `gateway/stt/` foi removido em
**D13** — a transcrição acontece antes do fio, em `device/stt/`.
