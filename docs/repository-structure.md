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
| `gateway/` | O servidor | Docker no localhost | VPS em São Paulo |
| `common/` | O contrato entre os dois | importado pelos dois | idem |
| `lab/` | A bancada de avaliação | só no PC | não vai para lugar nenhum |

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
  activation/   o que ACORDA o aparelho: wake word, botão GPIO,
                detecção de energia (seção 7)
  audio/        captura (microfone + VAD), playback, escolha de I/O
  stt/          transcrição local: faster-whisper, antes do fio (D13)
  router/       roteador de intenções (seção 5): decide se resolve
                aqui ou manda para o gateway. Hoje só regex (D17)
  local/        timers, alarmes, a hora — SQLite + agendador, sem rede
  rosto/        o rosto (D27): servidor local + static/ com a página
  state.py      IDLE → LISTENING → THINKING → SPEAKING, e o gancho
                de observador que o rosto assina
  tts/          síntese local: a voz própria, treinada em lab/finetune
  ws_client.py  a ÚNICA conexão com o mundo externo
  config.py     áudio, display e rosto por variável de ambiente
  main.py       entrypoint do processo (--text, --verbose)
```

O fluxo lê de cima para baixo: `activation/` dispara → `audio/` captura →
`router/` decide → ou `local/` executa, ou `ws_client.py` manda para o gateway.
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
  llm/             base.py (Protocol ProvedorLLM) + implementações
  tts/             base.py (Protocol) + cache de frases fixas
  tools/           device_tools.py: o LLM pede, o Pi executa (D18)
                   spotify.py: o gateway executa, porque aqui ha segredo (D21)
                   spotify_auth.py: consentimento, roda uma vez
                   search.py: busca na internet, provedor trocavel (D24)
  data/            refresh token do Spotify -- fora do git
  conversation/    histórico e montagem de contexto
  config.py        segredos e parâmetros — nunca saem do servidor
  main.py          entrypoint (uvicorn)
  Dockerfile       imagem enxuta: so requirements-gateway.txt (D15)
  docker-compose.yml
```

Na raiz, ao lado deles: `requirements-gateway.txt` (o que a imagem instala) e
`.dockerignore` (o contexto de build e a raiz, por causa de `common/` — sem
ignorar, `.venv` e `lab/models/` viajariam junto).

`llm/` e `tts/` seguem o mesmo padrão: `base.py` define a interface, os
arquivos vizinhos são implementações intercambiáveis, e **nada fora do módulo
sabe qual está ativa**. É isso que permite trocar STT de nuvem por
faster-whisper, ou a API do LLM por Ollama, sem tocar em `api/`.

`conversation/` é separado de `llm/` porque histórico e montagem de prompt não
mudam quando o provedor muda — e o cache de prefixo da seção 6, que pesa mais na
conta que a escolha do modelo, mora aqui.

---

## 5. `tests/`, `scripts/` e `lab/`

Estas três **não estão na seção 3 do plano**; foram acrescentadas durante a
construção.

- **`tests/`** — a seção 10 é escrita inteira em critérios de aceite, e boa
  parte deles é testável. Hoje cobre a máquina de estados, o contrato de
  mensagens de `common/`, a montagem de histórico do gateway e a pontuação
  do `lab/`.
- **`scripts/`** — utilitários que não rodam em produção: medir latência,
  pré-gerar o cache de TTS (regra 2 da seção 5), gravar as 150–200 amostras de
  voz do wake word. **Está vazia por enquanto.**
- **`lab/`** — a bancada onde os motores de STT e TTS são medidos antes de
  virarem implementação. Ver adiante.

---

## 6. `lab/` — escolher um motor com número, não com opinião

O plano deixa STT e TTS em aberto ("API de nuvem, trocável por faster-whisper";
"Piper ou TTS de nuvem"). `lab/` existe para fechar essas escolhas.

```
lab/
  phrases.py     as frases pt-BR de teste — a constante de toda comparação
  registry.py    quais motores existem, com a flag [PT] de especialista
  devices.py     escolha de microfone e alto-falante
  speaker/       reconhecimento de locutor (quem falou, não o quê)
  run_speaker.py cadastro e teste de vozes
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

Os `Protocol` em `lab/tts/base.py` e `lab/stt/base.py` espelham de propósito os
de `gateway/`: o vencedor migra como implementação, sem reescrita.

`lab/` **não roda em produção** e pode importar o que quiser dos dois lados —
é a única pasta com essa liberdade, porque não existe nem na Pi nem na VPS.
Detalhes de uso em `lab/README.md`.

---

## 7. Onde colocar um arquivo novo

1. Precisa existir na Pi? → `device/`
2. Só no servidor? → `gateway/`
3. As duas pontas precisam concordar sobre isso? → `common/`
4. É um motor sendo avaliado, ou a medição dele? → `lab/`
5. Não roda em produção? → `scripts/` ou `tests/`

E uma decisão que contraria o plano? → registre em [`decisions.md`](decisions.md)
antes de escrever o código. A decisão **D1** já move STT e TTS do gateway para o
dispositivo, então a leitura da seção 4 acima muda: `gateway/tts/` continua
existindo como interface sem uso no caminho principal, e `gateway/stt/` foi
removido em **D13** — a transcrição acontece antes do fio, em `device/stt/`.
