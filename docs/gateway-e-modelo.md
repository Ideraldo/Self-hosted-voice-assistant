# O gateway, o modelo e as ferramentas

O gateway é o processo que fala com o LLM, guarda os segredos e carrega as
chamadas de ferramenta. Hoje roda com uvicorn no localhost; depois, em Docker
numa VPS.

Decisões relacionadas: [D15](decisions.md), [D16](decisions.md),
[D18](decisions.md), [D19](decisions.md), [D20](decisions.md),
[D24](decisions.md), [D28](decisions.md).

---

## O contêiner, que ainda não rodou

Autenticação por token e o atraso simulado funcionam hoje
(`SIMULATED_LATENCY_MS` no `.env`; 80 é wifi, 150 é 4G). **O contêiner está
escrito e não verificado**, e vai continuar assim até a VPS: a virtualização de
firmware está desligada nesta máquina por opção, então não há WSL2 nem Docker
Desktop (D16). A VPS roda Docker Engine no Linux, onde nada disso se aplica.
Trate o comando abaixo como não testado:

```powershell
docker compose -f gateway/docker-compose.yml up --build
python -m device.main      # inalterado: continua ligando em ws://localhost:8000/ws
```

A imagem instala `requirements-gateway.txt`, não `requirements.txt`. Depois da
D13 o gateway não guarda modelo nenhum — `gateway/` e `common/` importam fastapi,
httpx, dotenv e a biblioteca padrão —, então o arquivo cheio arrastaria torch,
transformers e a bancada inteira para dentro de um contêiner que nunca os usa
(D15). Dependência nova do gateway vai naquele arquivo, e só se `gateway/`
importar.

Duas coisas que só mordem dentro do contêiner, ambas tratadas no compose:
`localhost` significa o próprio contêiner, então `OLLAMA_URL` é sobrescrito com
`host.docker.internal`; e esse nome só resolve no Docker Engine para Linux — o
caso da VPS — por causa da linha `extra_hosts`.

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

Trocar o LLM depois mexe em uma função, `build_llm` em `gateway/main.py`.

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
