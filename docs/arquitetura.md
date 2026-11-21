# Arquitetura em diagramas

Os outros documentos contam a história em prosa. Este desenha a aplicação
inteira: os dois processos, o caminho de um turno, o que passa pelo fio, a
máquina de estados, o laço de ferramentas e onde cada coisa roda.

Os diagramas foram tirados do código, não dos outros documentos. Quando eu
mudar `device/main.py`, `gateway/api/session.py` ou `common/messages.py`, é
aqui que o desenho tem que mudar junto.

Decisões relacionadas: [D1](decisions.md), [D7](decisions.md),
[D13](decisions.md), [D14](decisions.md), [D17](decisions.md),
[D18](decisions.md), [D21](decisions.md), [D22](decisions.md),
[D23](decisions.md), [D24](decisions.md), [D27](decisions.md),
[D30](decisions.md).

Tudo aqui roda num PC hoje. Não há Pi, não há VPS, e a seção
[Implantação](#6-implantação-hoje-e-o-alvo) separa o que existe do que está
planejado.

---

## Em uma tela

Só os componentes e como se ligam: por onde a voz entra, o que fica em casa, o
que vai para o gateway e quais serviços de fora ele alcança. Os diagramas
seguintes abrem cada caixa.

<img src="img/arquitetura-alto-nivel.pt.svg" alt="Arquitetura em alto nível: a voz entra no dispositivo, comandos conhecidos ficam locais, todo o resto cruza um WebSocket só de texto até o gateway, que alcança o LLM, o Spotify e a busca" width="100%">

**O que notar.** Um comando conhecido — timer, alarme, hora — nunca sai do
dispositivo. Todo o resto sobe como texto e volta como texto: o áudio não cruza
a rede em nenhum dos dois sentidos. E só o gateway fala com serviços de fora.

---

## 1. Visão geral

```mermaid
flowchart TB
    subgraph DEV["Processo 1 — device (python -m device.main)"]
        MIC["Microfone + VAD<br/>audio/capture.py"]
        WAKE["Wake word, opcional<br/>activation/wake.py"]
        STT["STT faster-whisper<br/>stt/"]
        ROUTER["Roteador nível 0, regex<br/>router/intents.py"]
        LOCAL["Serviços locais + agendador<br/>local/"]
        DB[("SQLite<br/>schedules.db")]
        TTS["TTS Piper<br/>tts/"]
        SPK["Alto-falante<br/>audio/playback.py"]
        SM["Máquina de estados<br/>state.py"]
        FACE["Servidor do rosto<br/>rosto/server.py"]
        WSC["GatewayClient<br/>ws_client.py"]
    end

    PAGE["Navegador<br/>página do rosto"]

    subgraph GW["Processo 2 — gateway (uvicorn gateway.main:app)"]
        API["/ws + Session<br/>api/session.py"]
        CONV["Conversation<br/>prompt de sistema + histórico"]
        LLMP["LLMProvider<br/>llm/ollama.py"]
        DTOOLS["device_tools.py<br/>só declara"]
        SPOT["spotify.py<br/>executa aqui"]
        SEARCH["search.py<br/>executa aqui"]
        TOKEN[("spotify_token.json<br/>gateway/data/")]
    end

    OLLAMA["Ollama<br/>qwen3:8b"]
    SPOTAPI["Spotify Web API"]
    WEB["DuckDuckGo ou Brave"]

    MIC --> WAKE --> STT --> ROUTER
    ROUTER -->|"casou"| LOCAL
    ROUTER -->|"não casou"| WSC
    LOCAL <--> DB
    LOCAL --> TTS
    WSC -->|"frases da resposta"| TTS
    WSC -->|"tool_call"| LOCAL
    TTS --> SPK
    SM --> FACE
    FACE -->|"WebSocket local<br/>state + emotion"| PAGE

    WSC <==>|"um WebSocket, só JSON de texto<br/>nenhum áudio"| API

    API --> CONV
    API --> LLMP
    API --> DTOOLS
    API --> SPOT
    API --> SEARCH
    SPOT <--> TOKEN
    LLMP -->|"HTTP /api/chat"| OLLAMA
    SPOT -->|"HTTPS"| SPOTAPI
    SEARCH -->|"HTTPS"| WEB

    classDef local fill:#d8f3dc,stroke:#2d6a4f,color:#111
    classDef servidor fill:#dbe9ff,stroke:#1d4e89,color:#111
    classDef internet fill:#ffe5d0,stroke:#b3541e,color:#111
    class MIC,WAKE,STT,ROUTER,LOCAL,DB,TTS,SPK,SM,FACE,PAGE local
    class WSC,API,CONV,LLMP,DTOOLS,SPOT,SEARCH,TOKEN,OLLAMA servidor
    class SPOTAPI,WEB internet
```

O que notar:

- **Verde funciona sem rede nenhuma.** Microfone, wake word, STT, roteador,
  timers e alarmes, TTS e rosto estão todos no processo do dispositivo. É por
  isso que ele liga e responde "que horas são" com o gateway desligado (D17).
- **Azul precisa do gateway**, mas não da internet: hoje o Ollama roda na mesma
  máquina.
- **Laranja precisa da internet.** Só o gateway fala com o Spotify e com o
  buscador; o segredo do Spotify mora em `gateway/data/` e nunca desce (D21).
- **A seta grossa é a única coisa que cruza a rede** entre os dois processos:
  um WebSocket, com mensagens JSON de texto. O áudio nasce e morre no
  dispositivo (D7, D13). Se chegar um frame binário, os dois lados reclamam em
  vez de tocar.
- O `tool_call` volta pelo mesmo WebSocket e termina em `local/`, o mesmo
  código que o roteador de regex usa (D18).

### Onde mora cada coisa

| Caixa | Arquivo | O que faz |
|---|---|---|
| Microfone + VAD | `device/audio/capture.py` | stream aberto uma vez, frames de 30 ms, webrtcvad + piso de ruído |
| Wake word | `device/activation/wake.py` | openWakeWord em blocos de 80 ms; desligado por padrão |
| STT | `device/stt/faster_whisper.py` | transcreve o PCM, offline primeiro |
| Roteador nível 0 | `device/router/intents.py` | normaliza a frase e casa regex com slots |
| Serviços locais | `device/local/service.py` | timer, alarme, listar, cancelar, hora; devolve a frase a falar |
| Agendador | `device/local/scheduler.py` | dorme até o próximo disparo e chama o anúncio |
| SQLite | `device/local/store.py` | tabela `schedules`, em `device/data/schedules.db` |
| TTS | `device/tts/piper_voice.py` | Piper, um bloco de áudio por frase |
| Alto-falante | `device/audio/playback.py` | stream de saída aberto uma vez |
| Máquina de estados | `device/state.py` | transições legais + observadores |
| Servidor do rosto | `device/rosto/server.py` | HTTP estático e WebSocket na mesma porta, só em `127.0.0.1` |
| Página do rosto | `device/rosto/static/` | `minimo.html`, `anime.html`, `face.js` |
| GatewayClient | `device/ws_client.py` | a única conexão para fora; aperto de mão e reconexão |
| Laço do turno | `device/main.py` | amarra tudo acima; `executar_ferramenta` |
| `/ws` + Session | `gateway/main.py`, `gateway/api/session.py` | autenticação, turno, laço de ferramentas |
| Conversation | `gateway/conversation/history.py` | prompt de sistema e histórico aparado |
| LLMProvider | `gateway/llm/base.py`, `gateway/llm/ollama.py` | interface + Ollama em streaming |
| device_tools | `gateway/tools/device_tools.py` | declara as 4 ferramentas de agenda e quais são terminais |
| spotify | `gateway/tools/spotify.py`, `spotify_auth.py` | 11 ferramentas de música, token e escopo |
| search | `gateway/tools/search.py` | `buscar_na_internet`, DuckDuckGo, Brave, leitor de página |
| Tempos do turno | `gateway/timing.py` | marca e loga cada etapa |
| Contrato | `common/messages.py`, `common/serialization.py` | os tipos de mensagem e o JSON |
| Contêiner | `gateway/Dockerfile`, `gateway/docker-compose.yml` | rodou em Linux em 21/11/2026 e roda no CI a cada push; falta a VPS e o modelo real (D16) |

---

## 2. O pipeline de um turno no dispositivo

```mermaid
flowchart TD
    MIC["Microfone<br/>frames de 30 ms, 16 kHz mono"]
    WAKEON{"WAKE_ENABLED=1<br/>e o modelo carregou?"}
    WAKE["Espera o nome<br/>openWakeWord, estado IDLE"]
    VAD["Grava a fala — estado LISTENING<br/>VAD + piso de ruído<br/>700 ms de silêncio encerra"]
    VAZIO{"Alguém falou?"}
    STT["faster-whisper transcreve"]
    TEXTO{"Saiu texto?"}
    TECLADO["Modo --text<br/>teclado no lugar do microfone"]
    ROUTER{"Roteador nível 0<br/>a frase casa um regex<br/>com os slots válidos?"}
    LOCAL["LocalServices.handle<br/>timer, alarme, listar, cancelar, hora"]
    DB[("SQLite")]
    SOBE["send_utterance<br/>texto sobe para o gateway"]
    GW["Gateway + LLM<br/>nível 2"]
    FRASES["Frases chegam uma a uma<br/>transcript final=false"]
    TTS["Piper sintetiza — estado SPEAKING"]
    SPK["Alto-falante"]
    IDLE["Estado IDLE<br/>volta ao começo"]
    AGENDA["Agendador<br/>timer ou alarme venceu"]
    SM["StateMachine.transition"]
    FACE["Rosto<br/>FaceServer.publish para a página"]

    MIC --> WAKEON
    WAKEON -->|"sim"| WAKE --> VAD
    WAKEON -->|"não, padrão"| VAD
    VAD --> VAZIO
    VAZIO -->|"não, 30 s"| IDLE
    VAZIO -->|"sim"| STT --> TEXTO
    TEXTO -->|"não"| IDLE
    TEXTO -->|"sim"| ROUTER
    TECLADO --> ROUTER
    ROUTER -->|"sim: nível 0, sem rede"| LOCAL
    LOCAL <--> DB
    LOCAL --> TTS
    ROUTER -->|"não: sobe"| SOBE --> GW --> FRASES --> TTS
    GW -.->|"tool_call de agenda"| LOCAL
    AGENDA -.->|"anúncio"| TTS
    TTS --> SPK --> IDLE
    SM -.->|"a cada transição"| FACE

    classDef local fill:#d8f3dc,stroke:#2d6a4f,color:#111
    classDef servidor fill:#dbe9ff,stroke:#1d4e89,color:#111
    class MIC,WAKE,VAD,STT,TECLADO,LOCAL,DB,TTS,SPK,IDLE,AGENDA,SM,FACE local
    class SOBE,GW,FRASES servidor
```

O que notar:

- **O roteador vem antes da rede, sempre.** `answer()` em `device/main.py`
  tenta `match_intent` primeiro; só o que não casa vira `utterance`. Slot
  inválido derruba o casamento em vez de virar meia intenção: na dúvida, sobe.
- **Os dois caminhos acabam no mesmo Piper e no mesmo `LocalServices`.** Uma
  frase que o regex pega e uma que só o LLM entende executam o mesmo código
  (D18). O que muda é quem interpretou.
- **Wake word é um desvio opcional.** Com `WAKE_ENABLED=0`, que é o padrão, ou
  se o modelo não carregar, o aparelho ouve direto (D30).
- **O agendador fala por fora do turno.** Ele roda como tarefa no mesmo laço de
  eventos, e um cadeado (`falando`) impede o alarme de falar por cima de uma
  resposta.
- **O rosto não está no caminho do turno.** Ele assina a máquina de estados e
  recebe cada transição; se quebrar, vira linha de log (D27).
- Captura, STT e síntese bloqueiam, então rodam em threads
  (`asyncio.to_thread`) para o laço continuar atendendo o WebSocket.

---

## 3. Sequência de um turno de nível 2

Os nomes das mensagens são os de `common/messages.py`. São sete tipos no total:
`session_start`, `utterance` e `tool_result` sobem; `state`, `transcript`,
`tool_call` e `error` descem.

```mermaid
sequenceDiagram
    autonumber
    actor P as Pessoa
    participant D as Dispositivo
    participant G as Gateway (Session)
    participant L as LLM (Ollama)
    participant W as Buscador

    Note over D,G: Aperto de mão, uma vez por conexão
    D->>G: session_start (device_id, token)
    G-->>D: state idle

    Note over P,L: Turno A — ferramenta que executa no dispositivo
    P->>D: "me avisa em uma hora e meia"
    Note over D: VAD, STT, roteador<br/>nenhum regex casa
    D->>G: utterance (text)
    G-->>D: state thinking
    G-->>D: transcript role=user (eco)
    G->>L: POST /api/chat (histórico + ferramentas)
    L-->>G: tool_calls criar_timer (segundos 5400)
    G-->>D: tool_call (id, name, args)
    Note over D: executar_ferramenta<br/>grava no SQLite<br/>acorda o agendador
    D->>G: tool_result (id, ok, value)
    Note over G: histórico guarda<br/>tool_call + resultado<br/>terminal, sem 2a rodada
    G-->>D: state speaking
    G-->>D: transcript role=assistant final=false
    Note over D: Piper fala a frase
    G-->>D: transcript role=assistant final=true
    G-->>D: state idle

    Note over P,W: Turno B — ferramenta que executa no gateway
    P->>D: "quem escreveu Dom Casmurro"
    D->>G: utterance (text)
    G-->>D: state thinking
    G-->>D: transcript role=user (eco)
    G->>L: POST /api/chat, rodada 1
    L-->>G: tool_calls buscar_na_internet (consulta)
    G->>W: busca, 5 resultados
    W-->>G: títulos e trechos
    Note over G: histórico guarda<br/>tool_call + resultado<br/>busca não é terminal
    G->>L: POST /api/chat, rodada 2
    loop a cada frase que o LLM completa
        L-->>G: pedaços de texto
        G-->>D: state speaking (só na 1a frase)
        G-->>D: transcript role=assistant final=false
        Note over D: Piper fala a frase<br/>o LLM já escreve a próxima
    end
    G-->>D: transcript role=assistant final=true
    G-->>D: state idle
```

O que notar:

- **O gateway dirige o estado do dispositivo durante o nível 2.** `thinking`,
  `speaking` e `idle` chegam como mensagens `state` e o dispositivo aplica na
  própria máquina. O `idle` final é o que encerra o turno do lado de cá.
- **`transcript` tem três usos.** `role=user` é o eco do que foi entendido;
  `role=assistant final=false` é uma frase para falar agora;
  `role=assistant final=true` é a resposta inteira, que o dispositivo não fala
  de novo.
- **`tool_call` e `tool_result` casam pelo `id`.** O gateway espera até 10 s
  pelo resultado e ignora o que não tiver o `id` da chamada atual.
- **No turno A o LLM é consultado uma vez só.** A frase que o dispositivo
  devolveu é a resposta (D18). No turno B são duas rodadas, porque a busca
  devolve material e não uma frase pronta (D24).
- **Spotify é igual ao turno B até a ferramenta, e igual ao A dali em diante:**
  o gateway executa, e a frase que o cliente devolve ("Tocando Construcao, de
  Chico Buarque.") vai ao ar sem segunda rodada (D21).
- O `state idle` do aperto de mão é consumido em `_await_ready`, fora de
  qualquer turno. `error` no lugar dele significa token recusado.

---

## 4. Máquina de estados do dispositivo

A tabela `ALLOWED` de `device/state.py`, com quem dispara cada transição hoje.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> IDLE
    IDLE --> LISTENING: microfone aberto para a fala, ou texto digitado
    LISTENING --> IDLE: 30 s sem fala, transcrição vazia, ou error do gateway
    LISTENING --> THINKING: nível 0 casou, ou o gateway mandou state thinking
    THINKING --> SPEAKING: primeira frase a falar
    THINKING --> IDLE: resposta vazia, ou error do gateway
    SPEAKING --> IDLE: fim do turno, ou error do gateway
    SPEAKING --> LISTENING: barge-in, permitido mas ainda sem quem dispare
```

O que notar:

- **`IDLE` só vai para `LISTENING`.** Não existe atalho `IDLE → THINKING`; o
  nível 0 e o aviso de "servidor fora do ar" passam por `_ate_falar`, que anda
  estado por estado até `SPEAKING`.
- **Transição fora da tabela levanta `ValueError`.** A máquina é o lugar onde
  um erro de ordem aparece cedo.
- **Esperar o wake word é `IDLE`.** A máquina só sai dele quando a gravação da
  fala começa.
- **`SPEAKING → LISTENING` está na tabela e ninguém usa.** É a porta do
  barge-in; hoje o microfone descarta o que entrou enquanto o aparelho falava.
- **Emoção é um segundo eixo, não um estado.** `set_emotion` muda o humor sem
  mexer no ciclo, e cada mudança avisa os observadores. Nada no caminho do
  turno a preenche ainda: o rosto fica em `neutral`.

---

## 5. O laço de ferramentas do gateway

`Session._speak_response` em `gateway/api/session.py`. É o que a D25 chama de
"as 40 linhas".

```mermaid
flowchart TD
    INI["utterance recebida<br/>state thinking, eco, add_user"]
    LLM["LLM responde em stream<br/>prompt = sistema + histórico<br/>ferramentas declaradas"]
    DELTA{"O que veio?"}
    ACUM["Acumula o texto"]
    FIM{"Fechou uma frase?<br/>. ! ? ou quebra de linha<br/>ponto depois de dígito não conta"}
    FLUSH["flush: state speaking na primeira vez<br/>transcript final=false"]
    TETO{"Já foram 3 rodadas<br/>de ferramenta?"}
    DESISTE["Histórico: falhou,<br/>número máximo de tentativas"]
    QUAL{"Qual ferramenta?"}
    BUSCA["executar_busca<br/>no gateway"]
    SPOT["executar_spotify<br/>no gateway"]
    DEVT["_run_device_tool<br/>tool_call para o dispositivo<br/>espera tool_result, 10 s"]
    HIST["Histórico, sempre:<br/>add_tool_call + add_tool_result"]
    TERM{"Resultado é terminal?<br/>agenda ou Spotify"}
    FALA["flush do resultado<br/>falado como veio, sem 2a rodada"]
    RESTO["flush do que sobrou<br/>transcript final=true<br/>add_assistant"]
    IDLE["state idle<br/>log dos tempos do turno"]

    INI --> LLM --> DELTA
    DELTA -->|"texto"| ACUM --> FIM
    FIM -->|"sim"| FLUSH --> DELTA
    FIM -->|"não"| DELTA
    DELTA -->|"acabou o stream"| RESTO
    DELTA -->|"tool_call"| TETO
    TETO -->|"sim"| DESISTE --> RESTO
    TETO -->|"não"| QUAL
    QUAL -->|"buscar_na_internet"| BUSCA --> HIST
    QUAL -->|"uma das 11 de música"| SPOT --> HIST
    QUAL -->|"qualquer outra"| DEVT --> HIST
    HIST --> TERM
    TERM -->|"sim"| FALA --> RESTO
    TERM -->|"não: busca"| LLM
    RESTO --> IDLE
```

O que notar:

- **O despacho é por nome, em três ramos.** Busca e Spotify executam no
  gateway; tudo o mais vira `tool_call` e atravessa o fio. Uma ferramenta que o
  modelo inventou cai no terceiro ramo, e é o dispositivo que responde
  "ferramenta desconhecida".
- **Terminal quer dizer "a frase já está pronta".** As quatro de agenda
  (`TERMINAL_TOOLS`) e todas as do Spotify vão ao ar como voltaram. O modelo
  reescrevendo uma lista perdia item (D18). Só a busca volta para o LLM.
- **O histórico guarda o par `tool_call` + resultado, inclusive nas
  terminais.** O pedido entra como mensagem `assistant` com `tool_calls`, o
  resultado como papel `tool`. Sem isso o modelo para de chamar ferramenta no
  turno seguinte e passa a narrar ação que não fez (D22).
- **Há teto.** `MAX_TOOL_ROUNDS = 3`: no quarto pedido de ferramenta dentro do
  mesmo turno o laço desiste em vez de deixar o aparelho preso em `THINKING`.
- **Falha de ferramenta é texto, não exceção.** Timeout do dispositivo, erro do
  Spotify e busca fora do ar voltam como "falhou: ..." ou como uma frase
  falável.
- **Ferramenta que não pode ser usada não é declarada.** Sem credenciais ou sem
  refresh token, as de música somem da lista; com `SEARCH_PROVIDER=none`, a de
  busca também (D19).

---

## 6. Implantação: hoje e o alvo

Dois desenhos. No segundo, borda tracejada quer dizer **planejado, não existe
ainda** — e lá quase tudo é tracejado.

**Hoje:**

```mermaid
flowchart LR
    subgraph PC["Um PC com Windows"]
        direction TB
        H_PAGE["Rosto<br/>aba do navegador"]
        H_DEV["device<br/>python -m device.main"]
        H_GW["gateway<br/>uvicorn, sem contêiner"]
        H_OLL["Ollama local<br/>qwen3:8b"]
        H_DEV -->|"127.0.0.1:8080"| H_PAGE
        H_DEV <==>|"ws://localhost:8000/ws<br/>só texto"| H_GW
        H_GW -->|"localhost:11434"| H_OLL
    end
    H_SPOT["Spotify Web API"]
    H_WEB["DuckDuckGo ou Brave"]
    H_CONN["Aparelho Spotify Connect ativo<br/>PC, celular ou Echo Dot"]
    H_GW -->|"HTTPS"| H_SPOT
    H_GW -->|"HTTPS"| H_WEB
    H_SPOT -->|"a música sai aqui"| H_CONN

    classDef existe fill:#d8f3dc,stroke:#2d6a4f,color:#111
    classDef internet fill:#ffe5d0,stroke:#b3541e,color:#111
    class H_PAGE,H_DEV,H_GW,H_OLL existe
    class H_SPOT,H_WEB,H_CONN internet
```

**O alvo:**

```mermaid
flowchart LR
    subgraph PI["Raspberry Pi 5 — planejada"]
        direction TB
        A_DEV["device<br/>o mesmo código"]
        A_KIOSK["Rosto<br/>Chromium em kiosk"]
        A_RASP["raspotify / librespot<br/>anunciado como Ideraldinho"]
        A_FALTA["Ainda por escrever:<br/>botão GPIO, modo tradutor,<br/>ducking, volume,<br/>embeddings no roteador"]
        A_DEV --> A_KIOSK
    end
    subgraph VPS["VPS — planejada"]
        A_GW["gateway em contêiner<br/>docker compose<br/>volume gateway-data"]
    end
    A_LLM["LLM<br/>provedor a escolher"]
    A_SPOT["Spotify Web API"]
    A_WEB["DuckDuckGo ou Brave"]

    A_DEV <==>|"GATEWAY_URL<br/>só texto"| A_GW
    A_GW --> A_LLM
    A_GW -->|"HTTPS"| A_SPOT
    A_GW -->|"HTTPS"| A_WEB
    A_SPOT -.->|"SPOTIFY_DEVICE<br/>casa por nome"| A_RASP

    classDef planejado fill:#f4f4f4,stroke:#666,color:#111,stroke-dasharray: 6 4
    classDef internet fill:#ffe5d0,stroke:#b3541e,color:#111
    class A_DEV,A_KIOSK,A_RASP,A_FALTA,A_GW,A_LLM planejado
    class A_SPOT,A_WEB internet
```

O que notar:

- **Migrar é trocar `GATEWAY_URL`.** O dispositivo conhece um endereço só, e
  nenhum import cruza de `device/` para `gateway/`. O contêiner copia apenas
  `common/` e `gateway/`.
- **O contêiner já rodou, mas não aqui** (D16): em Linux, em 21/11/2026, e no
  CI a cada push. No PC o gateway continua subindo com `uvicorn`. O compose
  resolve o Ollama por `host.docker.internal` e guarda o token do Spotify num
  volume. Falta a VPS, e falta o modelo de verdade do outro lado.
- **A música hoje não sai do Ideraldinho.** `SPOTIFY_DEVICE=Ideraldinho` não
  casa com nenhum aparelho, então a escolha cai para o que estiver ativo na
  conta. Quando o raspotify existir na Pi com esse nome, a mesma configuração
  passa a tocar nela (D23).
- **Não existe ainda:** a Pi, a VPS, o LLM fora do Ollama local, o botão GPIO,
  o modo tradutor, ducking, volume e a similaridade por embeddings no roteador.
  A lista do que espera hardware está no fim do
  [diário de bordo](diario-de-bordo.md).

---

## 7. Timers e alarmes: o caminho que não depende de nada

O nível 0 sozinho, com o agendador. É a parte que tem que funcionar com a
internet caída.

```mermaid
flowchart TD
    REGEX["Regex casou<br/>router/intents.py"]
    TOOL["tool_call do gateway<br/>executar_ferramenta"]
    SVC["LocalServices<br/>criar_timer, criar_alarme, cancelar"]
    DB[("schedules<br/>id, kind, label, fire_at,<br/>created_at, fired_at, cancelled")]
    NOTIFY["on_change<br/>Scheduler.notify"]
    TICK["Scheduler: lê os pendentes"]
    DUE{"Algum venceu?"}
    DORME["Dorme até o próximo disparo<br/>no máximo 30 s<br/>ou até ser acordado"]
    MARCA["mark_fired"]
    ATRASO{"Atraso maior<br/>que 120 s?"}
    CALA["Só loga<br/>não anuncia"]
    FALA["anunciar<br/>cadeado falando, Piper, alto-falante"]

    REGEX --> SVC
    TOOL --> SVC
    SVC -->|"add ou cancel"| DB
    SVC --> NOTIFY --> TICK
    DB --> TICK --> DUE
    DUE -->|"não"| DORME --> TICK
    DUE -->|"sim"| MARCA --> ATRASO
    ATRASO -->|"sim"| CALA --> TICK
    ATRASO -->|"não"| FALA --> TICK
```

O que notar:

- **O agendamento vive em disco**, com `fire_at` em epoch. Reiniciar o processo
  não perde alarme; o agendador relê o banco ao subir.
- **Criar ou cancelar acorda o agendador na hora.** Sem o `notify`, um timer de
  5 segundos esperaria a soneca de 30.
- **A soneca tem teto de 30 s** para o caso de o relógio ser corrigido no meio,
  que é o que acontece numa Pi sem RTC.
- **Alarme muito atrasado não toca.** Vencido há mais de 120 s com o aparelho
  fora do ar, ele é marcado como disparado e fica calado.
- O gateway não aparece neste desenho a não ser como uma das duas entradas. Ele
  nunca é responsável por acordar ninguém.

---

## 8. Quando o fio cai

`GatewayClient` em `device/ws_client.py`.

```mermaid
flowchart TD
    BOOT["Boot: uma tentativa de conexão"]
    BOOTOK{"Conectou?"}
    ONLINE["Conectado"]
    MODOLOCAL["Sobe em modo local<br/>nível 0 funciona"]
    FRASE["Frase de nível 2 para enviar"]
    TEM{"O socket está de pé?"}
    ENVIA["Envia utterance<br/>espera a resposta"]
    CHEGOU{"A resposta chegou<br/>até o state idle?"}
    FEITO["Turno completo"]
    CONECTA["Conecta + session_start<br/>espera state idle"]
    RESP{"Como terminou?"}
    AUTH["AuthRejected<br/>token recusado, sem retentativa<br/>o processo termina"]
    CABE{"A próxima espera cabe<br/>no orçamento de 8 s?"}
    ESPERA["Espera 0,5 s dobrando até 30 s<br/>mais até 0,5 s aleatório"]
    PERDIDO["ConnectionLost<br/>fala que não alcança o servidor<br/>volta a IDLE e segue ouvindo"]

    BOOT --> BOOTOK
    BOOTOK -->|"sim"| ONLINE
    BOOTOK -->|"não"| MODOLOCAL
    ONLINE --> FRASE
    MODOLOCAL --> FRASE
    FRASE --> TEM
    TEM -->|"sim"| ENVIA
    TEM -->|"não, ou caiu ao enviar"| CONECTA
    CONECTA --> RESP
    RESP -->|"state idle"| ENVIA
    RESP -->|"error"| AUTH
    RESP -->|"falha de rede"| CABE
    CABE -->|"sim"| ESPERA --> CONECTA
    CABE -->|"não"| PERDIDO
    ENVIA --> CHEGOU
    CHEGOU -->|"sim"| FEITO --> FRASE
    CHEGOU -->|"não, o socket caiu"| PERDIDO
    PERDIDO --> FRASE
```

O que notar:

- **A reconexão mora no envio, não na recepção** (D14). Enquanto o gateway está
  fora, o aparelho continua ouvindo e resolvendo nível 0; só tenta de novo
  quando há uma frase de verdade para entregar.
- **Quem está esperando resposta espera no máximo 8 s** (`CONNECT_BUDGET`).
  Depois disso o aparelho diz "Nao consigo falar com o servidor agora. Timer e
  alarme continuam funcionando." e volta a ouvir.
- **O turno perdido não volta.** O histórico vive na `Session` do gateway, que
  morre com a conexão; a sessão nova começa sem memória.
- **Token errado é o único erro que não entra em retentativa.**
- A página do rosto tem a sua própria reconexão, em `face.js`: espera de 400 ms
  dobrando até 5 s, e o rosto apaga enquanto o servidor não volta.
