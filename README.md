# Marcos-AI — portable voice assistant

Client-server voice assistant, built to replace a bedroom Alexa without giving
up portability.

- `docs/ultraplan-v3-assistente-voz-portatil.md` — the specification
- `docs/decisions.md` — where the build diverged from it, and why
- `docs/diario-de-bordo.md` — the narrative: doubts, attempts, what broke
- `lab/RESULTS.md` — measured numbers for every STT and TTS candidate
- `lab/docs/comandos.md` — every bench command and flag, in one place
- `docs/repository-structure.md` — what each folder is for, and why

## Layout

```
device/      runs on the PC now, on a Pi 5 later
  audio/         capture (mic + VAD), playback
  activation/    wake word, GPIO button, power-source detection
  router/        level-0 intent matching: regex + slot extraction
  local/         timers, alarms, the clock -- SQLite + scheduler, no network
  stt/           faster-whisper: transcription happens before the wire (D1)
  tts/          Piper: the assistant's own voice, synthesised locally
  rosto/         the face: web app served locally, state over WS (D27)
  ws_client.py   the single connection to the gateway
  state.py       state machine + the observer hook the face subscribes to
gateway/     runs with uvicorn today, in Docker on a VPS later
  api/           WebSocket, token auth, the turn loop
  llm/           interface + swappable implementations
  tools/         device_tools.py  declared here, executed on the device (D18)
                 spotify.py       executed here: this is where secrets live (D21)
                 search.py        web search, swappable provider (D24)
  conversation/  history and context assembly
  data/          the Spotify refresh token -- gitignored
  Dockerfile     gateway-only image; see requirements-gateway.txt (D15)
common/      shared message schemas -- the contract, never logic
scripts/     dev tools; scripts/rosto.py drives the face without a microphone
lab/         bench for picking the STT and TTS engines (not production)
docs/        the plan, the structure guide, the decision log, the diary
.claude/     project skills: /documentar closes a work session
```

Two non-negotiables from the plan: **two separate processes from the first
commit**, and **alarms/timers always execute on the device**.

## Setup

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3:8b                    # the LLM the .env points at (D20)
```

`.env.example` documents every variable. The ones without a safe default are
`DEVICE_TOKEN` and, if you want music, the two Spotify credentials -- everything
else works as shipped, web search included.

Every command below assumes the project root as the working directory.

## Phase 0 — the loop, end to end

Goal: mic -> STT -> LLM -> TTS round trip, two processes, WebSocket on localhost.
Accepted when 10 consecutive turns transcribe reliably in pt-BR.

**Wired end to end.** You speak; the mic records until the VAD hears you stop;
faster-whisper transcribes **on the device**; the gateway asks Ollama and streams
the answer back one sentence at a time; Piper speaks it with the owner's own
voice. No audio crosses the network in either direction -- only text (D1, D7,
D13). The acceptance criterion -- 10 consecutive turns transcribing reliably --
is the next thing to check with a real microphone.

```powershell
ollama serve                                            # or the Ollama app
.\.venv\Scripts\python.exe -m uvicorn gateway.main:app  # terminal 2
.\.venv\Scripts\python.exe -m device.main               # terminal 3
```

`--text` swaps the microphone for the keyboard and skips loading the STT: if the
answer is wrong with typed input, the problem is not the microphone. `--verbose`
prints what was heard and how long each stage took.

Pick the microphone with `AUDIO_INPUT_DEVICE` (a name fragment is enough) --
whatever Windows picked by default is often a headset that is not plugged in,
and a silent recording looks exactly like a broken model.

If the gateway goes away mid-conversation, the device does not: it reconnects
with growing backoff and goes back to listening (D14). What it does not get back
is the answer to that turn -- history lives in the gateway's session and dies
with the connection. A rejected token is the one failure that is not retried.

Swapping the LLM later touches one function, `build_llm` in `gateway/main.py`.

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Phase 1 -- the gateway in a container

Goal: containerised gateway, token auth, simulated link latency.
Accepted when `docker compose up` brings everything up.

Token auth and the simulated delay work today (`SIMULATED_LATENCY_MS` in `.env`;
80 is wifi, 150 is 4G). **The container is written but unverified**, and will
stay that way until the VPS: firmware virtualisation is off on this machine by
choice, so there is no WSL2 and no Docker Desktop (D16). The VPS runs Docker
Engine on Linux, where none of that applies. Treat the command below as
untested:

```powershell
docker compose -f gateway/docker-compose.yml up --build
python -m device.main      # unchanged: it still connects to ws://localhost:8000/ws
```

The image installs `requirements-gateway.txt`, not `requirements.txt`. After
D13 the gateway holds no model at all -- `gateway/` and `common/` import
fastapi, httpx, dotenv and the standard library -- so the full file would drag
torch, transformers and the whole bench into a container that never uses them
(D15). A new gateway dependency goes in that file, and only if `gateway/`
imports it.

Two things that only bite inside a container, both handled in the compose file:
`localhost` means the container itself, so `OLLAMA_URL` is overridden with
`host.docker.internal`; and that name only resolves on Docker Engine for Linux
-- the VPS case -- because of the `extra_hosts` line.

## Phase 2 -- what works with the internet down

Goal: local timers, alarms and reminders behind an intent router.
Accepted when a timer works with the gateway switched off.

**Done, and verified by running it.** The router looks at the sentence before
anything touches the network: if it is level 0 -- a timer, an alarm, the time,
"cancel", "what do I have" -- the device answers on its own, offline, with no
LLM. Anything it does not recognise goes up to the gateway (D17).

```powershell
# no gateway running at all
.\.venv\Scripts\python.exe -m device.main --text
```
```
gateway: fora do ar -- timer, alarme e hora continuam
voce:   marcos> Sao 20 horas e 2 minutos.   [nivel 0, local]
voce:   marcos> Timer de 5 segundos.        [nivel 0, local]
  marcos> Seu timer acabou.                 [timer]
```

Schedules live in SQLite (`SCHEDULES_DB`) because an alarm has to survive the
process -- the Pi reboots, and waking you up is the one job that cannot depend
on anything else being alive. One thing the router deliberately does **not** do
is guess: an unmatched sentence goes to the LLM, because a router that guesses
is worse than no router at all (plan section 5, rule 1).

Still missing from this phase, on purpose: embedding similarity for paraphrases
(waiting on a real level-2 log to say which paraphrases people actually use),
and volume control (OS mixer, platform-specific).

## Phase 3 -- tools

Goal: tools on the gateway. Accepted when the LLM calls the right ones and does
not invent calls that do not exist. **Delivered**, minus Home Assistant, which
is out of scope.

**Done: the tools that run back on the device.** The gateway declares
`criar_timer`, `criar_alarme`, `listar_agendamentos` and `cancelar_agendamento`,
but executes none of them -- it carries the call to the device and waits for the
result. Execution lands in the same `device/local/` code the regex router
already used, so a sentence understood by the regex and one understood only by
the LLM end up identical (D18).

```
voce> me lembra de tirar o bolo quando der uma hora e meia
  [ferramenta criar_timer {'segundos': '5400'}]
marcos> Timer de 1 hora e meia.
```

A device tool's result **is** the answer: it is spoken as returned, with no
second LLM round. That started as a latency win (3.7s -> 2.1s) and turned out to
be a correctness fix -- asked to list two schedules, the model rewrote the list
and dropped one.

**The model had to change for this to work.** With tools declared, llama3.1:8b
stops answering general knowledge -- same prompt, same question, "Não sei" with
tools and "Canberra" without (D19). Measured across three candidates:

| | llama3.1:8b | qwen3:4b | qwen3:8b |
|---|---|---|---|
| Right tool | 4/5 | 5/5 | **5/5** |
| General knowledge | 0/7 | 3/7 | **6/7** |
| Median turn | **1.7s** | 5.5s | 2.8s |

`qwen3:8b` is the default now, with `LLM_THINK=false` (D20). Thinking is off
because qwen3 reasons by default, which costs latency and can leak: qwen3:4b
returned its draft as content, and on a voice assistant that means the device
**says** "Okay, the user is asking..." out loud. Run `ollama pull qwen3:8b`.

An 8B still hallucinates -- it once attributed Dom Casmurro to the wrong author.
That is what the web search below is for.

**Spotify.** Six music tools, and the first ones the
gateway executes itself rather than forwarding -- the client secret and refresh
token live on the server and never travel the wire (D21). Setup is one-time:

```powershell
# 1. https://developer.spotify.com/dashboard -> Create app
#    Redirect URI must be exactly http://127.0.0.1:8888/callback
#    (Spotify rejects "localhost"; 127.0.0.1 only)
# 2. Put the client id and secret in .env
# 3. Authorise once; the refresh token lands in gateway/data/ (gitignored)
.\.venv\Scripts\python.exe -m gateway.tools.spotify_auth
```

Playback control needs **Spotify Premium** -- the API answers 403 without it,
and the client turns that into a spoken sentence rather than a traceback.

With no credentials configured the music tools are **not declared to the model
at all**, and `/health` reports `"spotify": "off"`. That follows from D19: a
small model that sees an unavailable tool tries to use it anyway. What it does
instead is admit it cannot:

```
voce> toca chico buarque
marcos> Nao sei tocar Chico Buarque.
        Posso ajudar com timers, alarmes ou listar/agendar coisas?
```

**Verified against a real account.** The first real run found three things the
mocked tests could not: Spotify answers 403 both for "no Premium" and for
"command not valid right now", so the message no longer blames Premium blindly;
playing a bare track leaves a one-item queue, so a track now plays in its
**album context** and "skip" continues the record; and the model claimed to have
paused without calling the tool -- caused by history that recorded only the
spoken sentence, fixed in D22.

```
voce> toca construcao do chico buarque  -> Tocando Construcao, de Chico Buarque.
voce> que musica e essa                 -> Construcao, de Chico Buarque.
voce> pula essa                         -> Proxima.
voce> pausa a musica                    -> Pausado.
```

**Where the music plays.** `SPOTIFY_DEVICE` (default `Marcos`) names the device
to use when nobody says where. The order is: the device named in the sentence ->
this preferred one -> whatever is already active -> the first in the list. It
points at the Pi on purpose: with `raspotify` (a `librespot` package) announcing
itself as "Marcos", asking for a song plays it on the assistant's own speaker
rather than on a PC in another room -- which is the difference between being the
speaker and being a remote control for one (D23). Until the Pi exists the name
matches nothing and the fallback keeps today's behaviour. `listar_aparelhos` and
`trocar_aparelho` cover "where can it play" and "move it to the echo dot".

Devices match by exact name, then by name fragment, then by **spoken type** --
nobody says "play on the iPhone", they say "play on the phone", so `celular`,
`caixa de som` and `computador` map onto the `type` the API reports. Verified by
actually playing on each: phone, Echo Dot, and PC. A device only appears once
its Spotify app is open and active, which is one more argument for raspotify on
the Pi -- it announces itself around the clock.

**Web search** is the answer to an 8B model that makes things up. `ddgs`
(DuckDuckGo) is the default and needs no key, account or card; `SEARCH_PROVIDER=brave`
swaps it for a keyed one behind the same interface (D24). It is the project's
first **non-terminal** tool: it returns five page extracts rather than a finished
sentence, so the model has to write the spoken answer from them.

| | before | with search |
|---|---|---|
| "quem escreveu Grande Sertão: Veredas" | *"não sei"* | João Guimarães Rosa |
| "distância da Terra até a Lua" | *"não sei"* | 384.400 km |
| "capital da Austrália" | Canberra | Canberra, **without searching** |

It does not search what it already knows -- 2.1s for those, 7-12s for a searched
turn. Grounding costs time; that is the trade, not a defect.

**Reading the first result** goes one step further: `SEARCH_READ_PAGE=1` opens
the top hit and feeds its text to the model alongside the extracts. Wikipedia
gets its own path -- it answers 403 to scraping with *any* User-Agent (measured)
and asks callers to use its API, so that is what we do. With that, page reading
went from 4/10 first results to **10/10**, median 0.62s.

It ships **off**, and the reason is the measurement, not caution: across four
questions the extracts already answered every one, and the page added 1-3s to
the slowest turn the device has for marginally richer wording. The code is
written and tested; what is missing is the question that justifies it (D28).

Home Assistant is out of scope -- one smart bulb does not justify Tailscale and
phase 5: a single smart bulb does not pay for a Home Assistant instance and a
Tailscale link to reach it.

## Phase 4 -- the face

Four animated states -- idle, listening, thinking, speaking -- as a local web
page. It comes up with the device; no extra process to start.

```powershell
.\.venv\Scripts\python.exe -m device.main --text --rosto   # opens the browser too
```

The URL is printed on boot (`http://127.0.0.1:8080/` by default). Two query
params help while designing:

- `?debug` prints the current state and emotion at the bottom of the screen
- `?demo` cycles every state and emotion with no device running

To iterate on the face without loading Whisper and speaking a sentence for every
CSS tweak, drive it from the keyboard:

```powershell
.\.venv\Scripts\python.exe -m scripts.rosto
> listening
> happy
```

| Variable | Default | What it does |
|---|---|---|
| `FACE_PORT` | `8080` | port for the page and its WebSocket (same port) |
| `FACE_ENABLED` | `1` | `0` boots the device with no face at all |
| `FACE_THEME` | `minimo` | which face `/` serves: `minimo` or `anime` |
| `DISPLAY_MODE` | `window` | `kiosk` on the Pi, once there is a Pi |

**Two faces, one protocol.** `minimo` is two rounded rectangles; `anime` is
almond eyes with iris, lashes, brows and a mouth. Both are served by the same
server, driven by the same `face.js`, and fed the same `{state, emotion}` --
the theme is the drawing layer and nothing else. Either file is always reachable
by name (`/minimo.html`, `/anime.html`), so comparing them takes no restart.

**Why a web app, and what it costs** (D27): the screen will not stay a face --
transcript, a running timer, album art -- and that is interface, where HTML is
cheap and a canvas is not. The cost is a browser competing for the same four
cores as `faster-whisper`. The mitigation is a rule, not a hope: **only
`transform` and `opacity` animate**, so the work stays on the GPU. No `<canvas>`,
no `requestAnimationFrame`.

The face is a subscriber to `StateMachine.transition()`, which is what makes the
renderer swappable: replacing Chromium with a native app later rewrites one
subscriber, not the architecture. And it can never take the device down -- a
subscriber that raises becomes a log line, a server that cannot bind returns
`False` and the turn goes on. The screen is a showcase; the timer is a function.

**Emotion is a second axis, not a fifth state.** `Emotion` is declared in the
protocol as an optional field and nothing sends it yet: fitting a new axis into
the wire format later is expensive, an empty optional field is not.

**The acceptance criterion changed.** "Does not stutter during audio" measures
nothing -- the face can be smooth and still steal the STT's core. It is now
**the face must not make the STT's RTF worse**, measured on the Pi, page open vs.
page closed. Nothing here has been measured on a Pi: there is no Pi yet.

## Choosing STT and TTS

`lab/` is where engines are measured before becoming an implementation under
`gateway/` or `device/`. It ranks them on the same pt-BR phrase set, reports
RTF and WER/CER, and flags which models are Portuguese specialists.

```powershell
.\.venv\Scripts\python.exe -m lab.devices                           # pick mic and speaker
.\.venv\Scripts\python.exe -m lab.list                              # what is on the bench
.\.venv\Scripts\python.exe -m lab.run_tts --engine piper --play     # hear it
.\.venv\Scripts\python.exe -m lab.run_stt --engine faster-whisper --size tiny,base,small --record
```

Current standings and the open questions live in `lab/RESULTS.md`.

Speaker recognition (who is talking) and the path to a custom voice are covered
in `docs/voz-e-locutor.md`:

```powershell
.\.venv\Scripts\python.exe -m lab.run_speaker enroll Ideraldo
.\.venv\Scripts\python.exe -m lab.run_speaker who
```

## What comes next

**Phase 1's container is written but never executed** (D15), and its first real
run will be on the VPS (D16). Nothing else waits on it.

**Phase 4 is up on the PC** -- the four states animate and the browser follows
the real state machine. What is left for it is the Pi: kiosk mode, and the RTF
measurement that is now its acceptance criterion (D27).

**Portable translator mode** is the next marker. Speak Portuguese,
have the device speak English or Chinese, and the reverse. Two of the three pieces already exist --
Whisper is multilingual by nature, Piper has a voice per language -- so what is
missing is machine translation in the middle. The candidate is **opus-mt on
CTranslate2**, the same runtime `faster_whisper` already uses. NLLB-200 600M is
recorded as a quality ceiling and a **non-candidate on the device**: it is
autoregressive over 600M parameters on four ARM cores, which is what ruled out a
local LLM on day 1.

**The biggest open question is the hardware.** Every number so far was measured
on a PC. Nothing has run on a Pi yet -- not even Whisper -- and moving from the
bench to real device code already pushed RTF from 0.43 to 0.6-0.9.

**Waiting on hardware** -- measuring STT on the Pi, raspotify, ducking, and the
one `docker compose up` that has never run -- is collected in a single checklist
at the end of `docs/diario-de-bordo.md`, so none of it has to be rediscovered.

The reasoning behind all of it is in `docs/diario-de-bordo.md`.
