# O gateway, o modelo e as ferramentas

O gateway é o processo que fala com o LLM, guarda os segredos e carrega as
chamadas de ferramenta. No dia a dia roda com uvicorn no localhost; o destino é
Docker numa VPS, e o contêiner já roda — só não nela ainda.

Decisões relacionadas: [D15](decisions.md), [D16](decisions.md),
[D18](decisions.md), [D19](decisions.md), [D20](decisions.md),
[D24](decisions.md), [D28](decisions.md).

---

## O contêiner: rodou, e o que rodar achou

Autenticação por token e o atraso simulado funcionam hoje
(`SIMULATED_LATENCY_MS` no `.env`; 80 é wifi, 150 é 4G).

O contêiner passou meses escrito sem rodar: no PC de desenvolvimento a
virtualização de firmware fica desligada por opção, então não há WSL2 nem Docker
Desktop (D16). O primeiro `up` foi em 21/11/2026, numa máquina Linux com Docker
Engine:

```bash
docker compose -f gateway/docker-compose.yml up --build
python -m device.main      # inalterado: continua ligando em ws://localhost:8000/ws
```

**O que foi verificado:**

- a imagem constrói, com 279 MB;
- `/health` responde;
- token errado fecha o WebSocket com 1008;
- um turno inteiro passa pelo WebSocket;
- o LLM no host é alcançado por `host.docker.internal`. Isso foi validado contra
  um Ollama falso: o caminho de rede está provado, o modelo não.

**O que rodar achou, e ler não tinha achado:**

- `gateway/data/` não estava no `.dockerignore`, então `COPY gateway/` levava o
  refresh token do Spotify para dentro da imagem. Agora está.
- Não havia volume, e o token morria a cada `up --build` — num contêiner que não
  tem navegador para autorizar de novo. Agora ele mora no volume nomeado
  `gateway-data`, e o diretório é criado na imagem já com o usuário não-root
  como dono, porque o volume herda o dono do diretório da imagem.
- `docker compose cp` grava o token como root, e aí o gateway não consegue
  regravá-lo quando o token é renovado. Para levar o token de uma máquina que já
  autorizou, o compose documenta o caminho pelo `exec`:

  ```bash
  docker compose -f gateway/docker-compose.yml exec -T gateway \
      sh -c 'cat > gateway/data/spotify_token.json' \
      < gateway/data/spotify_token.json
  ```

O CI (`.github/workflows/ci.yml`) repete isso a cada push: constrói a imagem,
sobe o compose com o `.env.example`, espera o `/health` e confere que nem o
`.env` nem o token estão dentro da imagem e que o processo não roda como root.
O CI não faz turno nenhum — não há modelo lá.

**O que continua sem verificação:**

- numa VPS de verdade;
- com o modelo real atrás do `host.docker.internal`;
- no Windows com Docker Desktop.

A imagem instala `requirements-gateway.txt`, não `requirements.txt`. Depois da
D13 o gateway não guarda modelo nenhum — `gateway/` e `common/` importam fastapi,
httpx, dotenv e a biblioteca padrão —, então o arquivo cheio arrastaria torch,
transformers e a bancada inteira para dentro de um contêiner que nunca os usa
(D15). Dependência nova do gateway vai naquele arquivo, e só se `gateway/`
importar.

Duas coisas que só mordem dentro do contêiner, ambas tratadas no compose:
`localhost` significa o próprio contêiner, então `OLLAMA_URL` é sobrescrito com
`host.docker.internal` (ou com `OLLAMA_URL_DOCKER`, se definido — é o que se
aponta para o modelo na VPS); e esse nome só resolve no Docker Engine para
Linux — o caso da VPS — por causa da linha `extra_hosts`.

---

## As ferramentas que rodam de volta no dispositivo

O gateway declara `criar_timer`, `criar_alarme`, `listar_agendamentos` e
`cancelar_agendamento`, mas não executa nenhuma: leva a chamada até o dispositivo
e espera o resultado. A execução cai no mesmo código de `device/local/` que o
roteador de regex já usava, então uma frase entendida pelo regex e uma entendida
só pelo LLM terminam idênticas (D18).

```
voce> me lembra de tirar o bolo quando der uma hora e meia
  [ferramenta criar_timer {'segundos': '5400'}]
ideraldinho> Timer de 1 hora e meia.
```

O resultado de uma ferramenta de dispositivo **é** a resposta: é falado como
voltou, sem uma segunda rodada de LLM. Começou como ganho de latência
(3,7s → 2,1s) e acabou virando correção de correção: pedido para listar dois
agendamentos, o modelo reescrevia a lista e derrubava um.

---

## A escolha do modelo

Com ferramentas declaradas, o llama3.1:8b para de responder conhecimento geral —
mesmo prompt, mesma pergunta, "Não sei" com ferramentas e "Canberra" sem (D19).
Medido em três candidatos:

| | llama3.1:8b | qwen3:4b | qwen3:8b |
|---|---|---|---|
| Ferramenta certa | 4/5 | 5/5 | **5/5** |
| Conhecimento geral | 0/7 | 3/7 | **6/7** |
| Turno mediano | **1,7s** | 5,5s | 2,8s |

`qwen3:8b` é o padrão, com `LLM_THINK=false` (D20). O raciocínio fica desligado
porque o qwen3 raciocina por padrão, o que custa latência e pode vazar: o
qwen3:4b devolveu o rascunho como conteúdo, e num assistente de voz isso
significa o aparelho **falar** "Okay, the user is asking..." em voz alta.

### Trocar de modelo é trocar o `.env`

O gateway fala com dois tipos de provedor, escolhidos por `LLM_PROVIDER`:

| Valor | O que alcança |
|---|---|
| `ollama` (padrão) | o Ollama local, como sempre |
| `openai-compat` | qualquer API no formato Chat Completions: OpenAI, Gemini, Groq, DeepSeek, Mistral, OpenRouter, e o próprio Ollama em `/v1` |

Para o segundo bastam `LLM_BASE_URL`, `LLM_API_KEY` e `LLM_MODEL`. O cliente é
`httpx` puro, sem SDK ([D15](decisions.md)). `LLM_REASONING_EFFORT`,
`LLM_TEMPERATURE` e `LLM_EXTRA_BODY` só vão no pedido quando preenchidos, porque
cada provedor recusa um campo diferente. `LLM_THINK` continua valendo só para o
Ollama.

**Raciocínio nunca vira fala.** O cliente lê só o conteúdo da resposta, ignora
os campos de raciocínio e corta `<think>…</think>` do texto, mesmo quando a
marca chega partida entre dois pedaços.

**Reserva.** `LLM_FALLBACK_*` aceita os mesmos sufixos e descreve um segundo
provedor — por exemplo, o Ollama de casa alcançado por um túnel. A troca só
acontece **antes** da primeira palavra: se o principal não começa a responder em
`LLM_FIRST_DELTA_TIMEOUT` segundos (padrão 8), ou devolve erro, o turno vai para
o reserva. Depois que algo já foi falado, a falha encerra o turno com o aviso de
modelo fora do ar. Quem falhou descansa 60 s, para o turno seguinte não pagar a
espera de novo. O `/health` mostra os dois, sem a chave.

**Nada disso rodou contra um provedor de verdade.** Os 38 testes usam um
servidor falso, e os detalhes de cada API — o que o Gemini exige de volta numa
chamada de ferramenta, quais valores de `reasoning_effort` cada um aceita —
vieram de documentação, não de resposta real. O primeiro teste com chave é que
decide. A escolha do modelo de nuvem continua em aberto: ver
[próximos passos](proximos-passos.md).

Um 8B ainda alucina — já atribuiu Dom Casmurro ao autor errado. É para isso que
serve a busca.

---

## Busca na internet

`ddgs` (DuckDuckGo) é o padrão e não precisa de chave, conta nem cartão;
`SEARCH_PROVIDER=brave` troca por um com chave atrás da mesma interface (D24). É
a primeira ferramenta **não terminal** do projeto: devolve cinco trechos de
página em vez de uma frase pronta, então o modelo tem que redigir a resposta
falada a partir deles.

| | antes | com busca |
|---|---|---|
| "quem escreveu Grande Sertão: Veredas" | *"não sei"* | João Guimarães Rosa |
| "distância da Terra até a Lua" | *"não sei"* | 384.400 km |
| "capital da Austrália" | Canberra | Canberra, **sem buscar** |

Ele não busca o que já sabe — 2,1s para essas, 7–12s para um turno com busca.
Fundamentar custa tempo; essa é a troca, não um defeito.

### Ler a primeira página

`SEARCH_READ_PAGE=1` abre o primeiro resultado e entrega o texto dele ao modelo
junto com os trechos. A Wikipedia tem caminho próprio: ela responde 403 a
scraping com *qualquer* User-Agent (medido) e pede no corpo do erro que se use a
API, então é o que se faz. Com isso a leitura de página foi de 4/10 primeiros
resultados para **10/10**, mediana de 0,62s.

Vem **desligado**, e a razão é a medição, não cautela: nas quatro perguntas os
trechos já respondiam todas, e a página somava 1–3s ao turno mais lento que o
aparelho tem em troca de redação marginalmente mais rica. O código está escrito e
testado; o que falta é a pergunta que o justifique (D28).

---

Home Assistant está fora de escopo: uma lâmpada inteligente não paga uma
instância do Home Assistant mais um link Tailscale para alcançá-la.
