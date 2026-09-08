# Decisões tomadas depois do plano

Registro seco: o quê e o porquê de cada decisão fechada. O caminho até ela — as
dúvidas, as tentativas, o que quebrou — está no [diário de bordo](diario-de-bordo.md).

O [ultraplan v3](ultraplan-v3-assistente-voz-portatil.md) é a especificação e
**não é reescrito** — ele registra o que se sabia quando foi escrito. Este
arquivo registra onde a construção divergiu dele, e por quê. Se o código
contradiz o plano, a explicação tem que estar aqui.

---

## D1 — STT e TTS rodam no dispositivo, não no gateway

**Data:** 2026-08-31
**O plano diz:** seção 1 coloca `STT → CLASSIFICADOR → LLM → TTS` inteiramente no
gateway, e o dispositivo só transmite áudio.

**O que mudou:** os dois passam para a Pi. Pela rede sobe só texto.

**Por quê:** o plano tem uma contradição interna. A seção 5 exige que o nível 0
(timer, alarme, hora, volume) funcione **sem rede**, e a seção 1 coloca o
roteador de intenções no dispositivo. Mas o roteador casa regex e embeddings
sobre *texto* — ele precisa de uma transcrição para decidir qualquer coisa. Com
o STT no gateway, um comando de timer com a internet caída nunca chega a ser
classificado. Nível 0 offline só existe com STT local.

O TTS veio junto por consequência: se o aparelho entende offline mas não
consegue falar, ele não confirma o timer que acabou de criar. O Piper mede
RTF 0,05, o que torna o custo dessa decisão quase nulo.

**Consequências:**
- A Pi carrega dois modelos além do wake word. Orçamento de CPU e RAM ficou mais
  apertado; a seção 8 do plano precisa ser relida com isso em mente.
- O tráfego por interação cai de ~0,25 MB para alguns KB (seção 12 fica
  desatualizada, para melhor).
- Fica em aberto se o gateway deve **re-transcrever** o áudio com um modelo maior
  quando a pergunta vai para o LLM, protegendo contra o modelo pequeno errar um
  nome próprio. Decidir com o WER da voz real na mão.

---

## D2 — Nada de STT/TTS de nuvem

**Data:** 2026-08-31
**O plano diz:** seção 9 escolhe "API de nuvem (trocável por faster-whisper)"
para STT, e "Piper ou TTS de nuvem" para TTS.

**O que mudou:** só candidatos offline. O `edge-tts` continua em `lab/` como teto
de qualidade para comparação, marcado como não-candidato.

**Por quê:** decorre de D1 e de preferência explícita. Um motor de nuvem não pode
ser o que diz "timer de dez minutos" com a internet fora.

---

## D3 — Modo texto na Fase 0, antes do microfone

**Data:** 2026-08-31
**O plano diz:** a Fase 0 entrega `mic → STT → LLM → TTS`.

**O que mudou:** o loop dos dois processos foi fechado primeiro em modo texto,
com o que se digita viajando pelo mesmo canal binário que levará PCM.

**Por quê:** valida protocolo, máquina de estados, autenticação e latência
simulada sem depender da escolha de STT, que ainda está aberta em `lab/`. A
captura de áudio substitui só a entrada; nada do caminho já validado muda.

---

## D4 — Voz própria por fine-tune do Piper, não por clonagem zero-shot

**Data:** 2026-08-31
**O plano diz:** seção 9 escolhe "Piper ou TTS de nuvem", sem tratar de voz
personalizada.

**O que mudou:** a voz do assistente passa a ser a do usuário, obtida por
fine-tune de um checkpoint pt-BR.

**Por quê:** clonagem zero-shot (XTTS-v2, F5-TTS) entrega timbre sem treino, mas
são modelos de 1,5 a 2 GB que exigem GPU — na Pi não rodam. O fine-tune do Piper
entrega um modelo de 60 MB a RTF 0,05, que é o único caminho com timbre próprio
**e** velocidade de Pi.

Clonagem zero-shot continua útil para uma coisa: pré-gerar o cache de frases
fixas da regra 2 da seção 5, uma vez no PC, sem custo em execução.

**Consequências:**
- Os checkpoints de treino pt-BR não estão mais no repositório oficial do Piper
  (saiu do ar). Os que sobreviveram estão no OpenVoiceOS, e são de vozes "high"
  que nem aparecem na lista oficial de download.
- Exige 30 a 60 min de gravação do usuário — que servem também para o wake word
  da seção 9.

---

## D5 — Dataset de voz precisa de mais de 15 minutos

**Data:** 2026-08-31
**O que mudou:** o primeiro treino, com 15,2 min e 203 frases, foi descartado. O
corpus subiu para 315 frases (~30 min).

**Por quê:** medido, não suposto. Na época 318 o WER do corpus tinha caído para
11,3% enquanto o de texto novo ficava parado em 39,7% — a assinatura de
memorização. Não era falta de épocas: 14.828 passos já haviam rodado. Era falta
de material para generalizar.

Uma voz que só sabe dizer as frases gravadas é inútil aqui, porque a resposta do
LLM nunca é uma frase pré-escrita.

**Consequências:**
- Todo treino passa a ser validado com `lab.finetune.generalize`, que mede WER
  em frases nunca gravadas *e* no corpus. A distância entre as duas é o que
  distingue "decorou" de "ainda cru" — diagnósticos com decisões opostas.
- Trechos de 10–15 s viraram o formato padrão: o `collate` do Piper preenche o
  batch até a amostra mais longa, e na inferência ele divide por sentença de
  qualquer forma.
- Se 30 min não bastarem, um bloco 4. A medição responde sem adivinhação.

---

## D6 — Hiperparâmetros de fine-tune, não os padrões do Piper

**Data:** 2026-08-31
**O que mudou:** learning rate do gerador de `2e-4` para `1e-4`, e o alvo de
épocas de 2000 para 1000.

**Por quê:** os padrões do Piper são calibrados para **treinar do zero** com
dezenas de milhares de amostras. Num fine-tune de meia hora, `2e-4` afasta o
modelo rápido demais dos pesos da base — que já sabem falar português — e o leva
a ajustar as poucas frases disponíveis, que é a definição de decorar.

Agrava no nosso caso que o `lr_decay` do Piper é `0.999875` **por época**,
pensado para épocas de centenas de passos. As nossas têm ~40, então a taxa quase
não decai ao longo do treino.

Sobre as épocas: a unidade que importa é **passo**, não época. O v1 rodou 368
épocas, que são apenas 14.828 passos; fine-tunes de VITS costumam pedir 10 a 30
mil.

**Consequências:**
- O timbre demora mais a aparecer. É o preço de não memorizar.
- `--lr` fica exposto: se o v2 ainda decorar, `5e-5` é o próximo degrau, antes de
  gravar mais.
- `accumulate_grad_batches` **não é uma opção aqui**: o VITS treina com
  otimização manual (dois otimizadores) e o Lightning recusa acumulação nesse
  modo. Para batch maior, só subindo `--batch-size`.
- Early stopping automático segue descartado, seguindo o aviso dos autores do
  Piper: o mel L1 satura cedo enquanto as perdas adversariais ainda removem
  artefatos audíveis.

---

## D7 — Pela rede sobe só texto; o áudio nasce no dispositivo

**Data:** 2026-09-01
**O plano diz:** seção 4 especifica frames binários de TTS indo do gateway para o
dispositivo.

**O que mudou:** o gateway envia a resposta como texto, uma frase por vez
(`Transcript` com `final=false`), e o dispositivo sintetiza e toca. Nenhum áudio
atravessa a rede em nenhuma direção — no sentido dispositivo→gateway isso ainda
está pendente, junto com o STT local.

**Por quê:** é a consequência prática de [D1](#d1--stt-e-tts-rodam-no-dispositivo-não-no-gateway).
Se a síntese roda na Pi, mandar áudio pronto pelo fio seria pagar duas vezes.

**Consequências:**
- Uma interação custa alguns KB em vez dos ~0,25 MB estimados na seção 12.
- O aparelho fala com a internet caída, que é requisito para confirmar timer e
  alarme.
- `gateway/tts/` continua existindo como interface, mas sem uso no caminho
  principal. A implementação que vale mora em `device/tts/`.
- O streaming por frase foi preservado e virou teste: `tests/test_session_protocol.py`
  falha se alguém voltar a mandar bytes ou a esperar a resposta inteira.

**Medido:** primeiro chunk de áudio em 0,32 s depois do texto chegar, com a voz
`pt_BR-ideraldo-medium` — dentro dos 150–400 ms da seção 11, com folga para a Pi.

---

## D8 — O assistente se chama Marcos

**Data:** 2026-09-01
**O plano diz:** o ultraplan o chama de "BMO" no título e nos exemplos.

**O que mudou:** o nome no código passa a ser Marcos, alinhado ao repositório.
`device_id` vira `marcos-01`, os loggers viram `marcos.*`, e o system prompt
agora diz "Você é o Marcos".

**Por quê:** o aparelho já responde falando, e ouvi-lo dizer "Sou o BMO" deixou a
inconsistência concreta. Renomear agora custa uma varredura; depois de o nome
aparecer em logs, configurações e gravações, custa mais.

**Consequências:** o `DEVICE_ID` padrão mudou. Um `.env` antigo com `bmo-01`
continua funcionando — é só um identificador —, mas convém atualizar.

---

## D9 — STT: começar pelo mais rápido e trocar se doer

**Data:** 2026-09-01
**A tensão:** o `small` acerta (9,0% de WER, 2,2% em comandos) e provavelmente
não cabe no orçamento da Pi; o `base` cabe com folga e erra os comandos.

**O que ficou:** começar com o mais rápido que for aceitável e só subir de
tamanho se a qualidade doer no uso real. Medir na Pi antes de decidir em
definitivo; testar whisper.cpp e sherpa-onnx, que são bem mais rápidos em ARM e
podem tornar a escolha desnecessária.

**Por quê:** é mais barato descobrir que um modelo pequeno bastava do que
descobrir que o grande não cabia depois de construir em cima dele.

**Sobre a divisão local/remoto:** quem decide não é o STT, é o **roteador de
intenções**, e ele trabalha sobre texto — por isso o STT precisa ser local
([D1](#d1--stt-e-tts-rodam-no-dispositivo-não-no-gateway)). O fluxo é o da seção
5 do plano: o dispositivo transcreve, o roteador tenta casar a frase com uma
intenção conhecida, e só manda para a VPS o que não casou.

---

## D10 — Sem VPS por enquanto; tudo local

**Data:** 2026-09-01
**O plano diz:** seção 2 recomenda comprar a VPS antes do hardware, porque é
cancelável e entrega o número de latência real.

**O que mudou:** adiado. O desenvolvimento segue inteiramente local.

**Por quê:** nada do que falta construir depende dela. Roteador, alarmes locais,
rosto e wake word são todos do lado do dispositivo. A VPS entra quando houver o
que medir.

**Consequência:** a Fase 5 continua sendo o primeiro gasto, quando chegar a hora.

---

## D11 — LLM open source, sem provedor de nuvem

**Data:** 2026-09-01
**O plano diz:** seção 6 escolhe `deepseek-v4-flash` via API.

**O que mudou:** o projeto segue em modelo aberto, rodando local via Ollama. A
interface `LLMProvider` continua sendo o ponto de troca, então migrar depois é
escrever uma implementação nova e mudar `build_llm`.

**Por quê:** preferência do usuário, e o Ollama já cobre o desenvolvimento.

**Consequências:**
- A busca fundamentada na internet, que motivou a escolha da API na seção 6,
  continua sendo o teste difícil. Um modelo de 8B pode não dar conta.
- Some a dependência de chave de API e o custo mensal.
- Se a qualidade doer, a troca é localizada — não é reescrita.

---

## D12 — Modelos e gravações ficam fora do repositório

**Data:** 2026-09-01
**O que ficou:** nada de binário grande no git. Versionamos código, os corpora de
frases e os números medidos — o suficiente para refazer qualquer modelo.

**Por quê:** são 32 GB fora do git contra 496 KB de histórico versionado. E há um
motivo mais forte que tamanho: **o `.onnx` treinado é a voz do usuário**, e o
dataset são 30 minutos da voz dele limpos e transcritos. Qualquer um dos dois
clona a voz numa ferramenta moderna. O repositório é público.

**O que existe hoje, e onde:**

| Item | Tamanho | Reproduzível? |
|---|---|---|
| `lab/finetune/dataset/` | 79 MB | **Não** — é uma hora de leitura |
| `lab/models/piper/*.onnx` | 1,3 GB | Sim, a partir do dataset |
| `lab/models/piper_ckpt/` | 2,2 GB | Sim, baixando de novo |
| `lab/finetune/runs/` | 28 GB | Sim, e não vale guardar |
| `lab/finetune/arquivo/` | 364 MB | Não — gerações já apagadas do run |

**O risco que fica em aberto:** o dataset existe só no disco do usuário. Perdê-lo
custa regravar tudo. Backup privado resolve; publicar não é necessário para isso.

**Se um dia for publicar**, as rotas avaliadas:

- **GitHub Releases** — até 2 GB por arquivo, fora do histórico do git, bom para
  "a voz final da versão N".
- **Hugging Face** — feito para modelos, repositório privado grátis. É de lá que
  vieram os checkpoints pt-BR do Piper.
- **Git LFS** — evitar: 1 GB de cota grátis, e cada versão nova de um `.onnx` de
  61 MB consome de novo.

**Falta, e vale fazer antes de publicar qualquer coisa:** um manifesto versionado
dizendo de que época veio cada `.onnx`, com quais hiperparâmetros e qual WER
mediu. Hoje o nome do arquivo é a única pista.

---

## D13 — O áudio nunca entra no fio: o dispositivo transcreve antes de falar

**Data:** 2026-09-01
**O plano diz:** seção 4 especifica frames binários de PCM subindo do
dispositivo para o gateway, e `audio_end` fechando a fala.

**O que mudou:** o dispositivo grava, corta pelo VAD, transcreve com
faster-whisper e manda uma mensagem nova, `utterance`, com a frase pronta.
`audio_end` e os frames binários deixaram de existir no protocolo.

**Por quê:** é [D1](#d1--stt-e-tts-rodam-no-dispositivo-não-no-gateway) chegando
no código. Enquanto o STT era um stub no gateway, o canal binário servia para
carregar o texto digitado e exercitar o protocolo (D3). Com o STT real no
dispositivo, manter os frames seria manter um caminho que ninguém usa — e o
teste que garante que áudio não desce ([D7](#d7--pela-rede-sobe-só-texto-o-áudio-nasce-no-dispositivo))
não tinha simétrico para a subida.

**Consequências:**
- `gateway/stt/` foi removido. A pergunta em aberto de D1 — o gateway
  re-transcrever com um modelo maior — continua em aberto, mas ela precisaria do
  áudio no fio, que é exatamente o que esta decisão tira. Se voltar, volta como
  decisão nova e explícita.
- O gateway não fala mais `listening`: quem sabe que está ouvindo é o
  dispositivo, porque a captura é dele. A máquina de estados passou a ser
  dirigida dos dois lados.
- O modo texto virou `--text` em `device/main.py`, e continua útil: se a resposta
  está errada com o texto digitado, o problema não é o microfone.
- O modelo do STT carrega com `local_files_only` primeiro. Sem isso, o
  faster-whisper consulta o Hugging Face na carga mesmo com o modelo em disco —
  um aparelho que não abre o microfone com a internet caída contradiz a razão de
  D1 existir.

**Medido (PC, `small/int8`, CPU):** carga de 2,0 s; frases de 2,5 a 3,8 s
transcritas em ~2,2 s cada — RTF entre 0,6 e 0,9, contra os 0,43 da bancada, que
media com o modelo já aquecido e sem beam completo em disputa. Cabe no PC; na Pi
é o número que decide entre `small` e `base` (D9).

---

## D14 — O dispositivo reconecta sozinho; o turno perdido não volta

**Data:** 2026-09-01
**O plano diz:** seção 2 trata o WebSocket como um canal que existe. Não diz o
que acontece quando ele deixa de existir.

**O que mudou:** `GatewayClient` reconecta com espera crescente (0,5 s dobrando
até 30 s, com dispersão aleatória) e tenta indefinidamente. A queda no meio de
um turno vira `ConnectionLost`, que o laço do dispositivo trata voltando a
ouvir. Token recusado levanta `AuthRejected` e **não** entra em retentativa.

**Por quê:** não havia nada. Qualquer queda — Wi-Fi oscilando, gateway
reiniciando — terminava o processo do dispositivo com traceback. Na mesa isso
quase nunca acontece; num aparelho de prateleira o modo de falha é ficar mudo
até alguém notar, que é o pior que um assistente de voz pode fazer.

**O que a reconexão não recupera:** a resposta daquele turno. O histórico da
conversa vive na `Session` do gateway, que morre com a conexão; a sessão nova
começa sem memória. Reconectar devolve o aparelho, não o assunto. Persistir
histórico é outra decisão, e não foi tomada.

**Duas escolhas que não são óbvias:**

- **A retentativa mora no envio, não na recepção.** A primeira versão
  reconectava dentro de `receive()`, antes de levantar `ConnectionLost` — e o
  aparelho ficava preso no laço de espera em vez de voltar a ouvir. Agora
  `receive()` só avisa que caiu, e a reconexão acontece quando há uma frase de
  verdade para entregar.
- **`AuthRejected` não herda de `OSError`.** Herdando, ela cairia no `except`
  da retentativa e um token errado viraria espera infinita em vez de erro. Foi
  exatamente o que aconteceu na primeira versão, e o teste é o que fixa isso.

**Verificado:** `tests/test_reconnect.py` sobe um gateway WebSocket real e o
derruba no meio do turno. Em campo, matando o uvicorn entre dois turnos: o turno
seguinte funcionou 2,7 s depois de o gateway voltar, sem religar nada.

---

## D15 — A imagem do gateway não instala o `requirements.txt`

**Data:** 2026-09-01
**O plano diz:** a Fase 1 entrega "gateway containerizado, autenticação por
token, latência simulada", com aceite em `docker compose up` subindo tudo.

**O que mudou:** as dependências do gateway saíram para
`requirements-gateway.txt`, e é só ele que entra na imagem. O `requirements.txt`
passa a ser o ambiente de desenvolvimento — os dois lados mais a bancada — e
inclui o outro por referência.

**Por quê:** o `Dockerfile` foi escrito quando o gateway ainda tinha STT. Depois
de [D13](#d13--o-áudio-nunca-entra-no-fio-o-dispositivo-transcreve-antes-de-falar)
ele não tem modelo nenhum: as importações de `gateway/` e `common/` são
`fastapi`, `httpx`, `dotenv` e biblioteca padrão. Instalar o arquivo inteiro
levaria `torch`, `transformers`, `speechbrain`, `vosk` e `piper-tts[train]` para
dentro da imagem — vários GB para um processo que só fala HTTP e WebSocket, num
container que um dia sobe numa VPS pequena.

**Três coisas que estavam quebradas e não apareciam porque ninguém rodou:**

- **O container não achava o Ollama.** O `.env` tem
  `OLLAMA_URL=http://localhost:11434`, correto para o dispositivo e errado
  dentro do container, onde `localhost` é ele mesmo. O compose agora sobrescreve
  com `host.docker.internal`, e declara `host-gateway` em `extra_hosts` — o
  Docker Desktop resolve esse nome sozinho, o Docker Engine em Linux (o caso da
  VPS) não.
- **Não havia `.dockerignore`.** O contexto de build é a raiz do projeto, porque
  `common/` precisa entrar junto. Sem ignorar nada, iriam para o daemon **41 GB**
  (medido: 5,8 GB de `.venv`, 7,0 GB de `lab/models`, 28 GB de `lab/finetune`)
  antes de a primeira linha do Dockerfile rodar. O `.gitignore` não vale aqui —
  o Docker não lê aquele arquivo.
- **O processo rodava como root.** Corrigido: usuário `ideraldinho`, uid 1000.

**Consequências:**
- Uma dependência nova do gateway tem que ser adicionada em
  `requirements-gateway.txt`, não no outro. A regra é literal: entra ali se
  `gateway/` importa.
- `gateway/stt/` sumiu do disco também — o D13 apagou os `.py` e a pasta ficou
  para trás com um `__pycache__` dentro.

**Não verificado:** `docker compose up` **não foi executado**. Esta máquina não
tem Docker nem WSL2. O critério de aceite da Fase 1 continua em aberto, e o que
está aqui é código escrito com cuidado, não comportamento observado. A primeira
pessoa a rodar isso deve tratar como não testado.

---

## D16 — O Docker fica para a VPS; a virtualização segue desligada aqui

**Data:** 2026-09-01
**O plano diz:** a Fase 1 aceita quando `docker compose up` sobe tudo, na
máquina de desenvolvimento.

**O que mudou:** o critério de aceite da Fase 1 fica **adiado até a Fase 5**, e
será verificado na VPS. Nesta máquina, o gateway continua rodando com `uvicorn`
direto.

**Por quê:** a virtualização está desligada no firmware — confirmado por duas
fontes independentes (`Win32_Processor.VirtualizationFirmwareEnabled` e
`systeminfo`: *"Virtualização Habilitada no Firmware: Não"*). O processador
suporta; é escolha de BIOS. E é escolha deliberada: com ela ligada, o Vanguard
do Valorant não deixa o jogo abrir. Esta máquina também é de jogo.

Sem virtualização não há WSL2, e sem WSL2 não há Docker Desktop. Não é
contornável por elevação nem por configuração do Docker.

**Por que isso custa pouco:** a imagem existe para rodar **na VPS**, que é Linux
com Docker Engine — onde nada disso se aplica. Verificar aqui seria conveniente,
não necessário. E o que o container mudaria no desenvolvimento é nada: o
`uvicorn` sobe o mesmo `gateway.main:app`.

**O que fica em aberto, e é preciso lembrar:** o `Dockerfile`, o
`docker-compose.yml` e o `.dockerignore` de [D15](#d15--a-imagem-do-gateway-não-instala-o-requirementstxt)
**nunca foram executados**. O primeiro `docker compose up` da vida deles vai ser
na VPS, no dia do deploy — que é o pior dia para descobrir um erro de sintaxe.
Se aparecer qualquer outra máquina com Docker antes disso (um notebook, um
runner de CI, a Pi), rodar o build ali é meia hora que se paga.

**Alternativa considerada e descartada:** Docker Desktop com backend Hyper-V em
vez de WSL2. Não resolve — o Hyper-V exige a mesma virtualização de firmware.

---

## D17 — O aparelho liga sem o gateway, e tenta o nível 0 antes da rede

**Data:** 2026-09-01
**O plano diz:** a seção 2 trata a conexão com o gateway como o canal que o
dispositivo abre ao subir. A seção 5 exige que o nível 0 funcione sem rede, mas
não diz o que acontece no momento do boot.

**O que mudou:** duas coisas, e elas são a mesma decisão vista de dois lados.

1. `GatewayClient.__aenter__` tenta conectar **uma vez** e segue mesmo falhando.
   Sem gateway, o aparelho sobe em modo local e avisa. A retentativa infinita de
   [D14](#d14--o-dispositivo-reconecta-sozinho-o-turno-perdido-não-volta)
   continua existindo para quedas em segundo plano, mas o envio de uma frase
   passa a ter orçamento (`CONNECT_BUDGET`, 8 s): quem está esperando resposta
   merece um "não deu" em vez de silêncio.
2. `answer()` consulta o roteador **antes** de tocar na rede. Casou no nível 0,
   resolve e fala; não casou, sobe.

**Por quê:** o critério de aceite da Fase 2 é "timer funciona com o gateway
desligado", e um aparelho que não liga sem o servidor nunca cumpriria isso.
A ordem no `answer()` é o que dá ao timer a latência que a seção 11 orça
(< 200 ms) — consultar a rede primeiro para depois descobrir que a intenção era
local seria pagar o pior caso em todo comando bom.

**Consequências:**
- Com o gateway fora, uma pergunta de nível 2 é respondida com voz: *"não
  consigo falar com o servidor agora; timer e alarme continuam funcionando"*.
  Silêncio seria indistinguível de microfone quebrado.
- `device/local/` e `device/router/` não importam `ws_client` nem `websockets`,
  e há um teste que falha se alguém importar. Não é estilo: é o que impede que,
  um dia, o caminho do despertador passe a depender do Wi-Fi.
- As frases que não casam vão para o log como `nivel 2`. Em um mês esse log diz
  quais intenções promover (plano, seção 5, regra 4).

**O que ficou de fora, e é consciente:** a similaridade por embeddings. Hoje o
nível 0 é só regex, o que cobre formato rígido — duração, horário, "cancela" — e
não cobre paráfrase ("me lembra de tirar o bolo quando der uma meia horinha").
Isso não é uma lacuna silenciosa: o que não casa sobe para o LLM, que é o
comportamento correto pela regra 1. Os embeddings entram quando houver log real
dizendo quais paráfrases as pessoas usam de verdade nesta casa — escolher as
frases de exemplo por adivinhação seria treinar contra um usuário imaginário.

**Também de fora:** volume ("aumenta o volume") está no nível 0 do plano e não
foi implementado. Ele depende do mixer do sistema operacional, que é
código específico de plataforma e não se testa na mesa do jeito que o resto se
testou.

---

## D18 — Ferramentas do dispositivo: o LLM pede, o Pi executa, e o resultado é a resposta

**Data:** 2026-09-01
**O plano diz:** a seção 4, linha 149: *"quando o LLM interpreta 'me acorda às 7',
quem grava e dispara é o Pi, não o gateway"*. A Fase 3 entrega ferramentas no
gateway, aceita quando *"o LLM chama as ferramentas certas e não inventa chamadas
inexistentes"*.

**O que mudou:** `gateway/tools/device_tools.py` declara quatro ferramentas —
`criar_timer`, `criar_alarme`, `listar_agendamentos`, `cancelar_agendamento` —
que o gateway **não executa**. Ele transporta a chamada até o dispositivo, espera
o `tool_result` e segue. A execução acontece em `device/local/`, o mesmo código
que o roteador de regex já usava antes de existir LLM no caminho.

Até aqui o gateway mandava `tool_call` pelo fio e ninguém esperava resposta; do
outro lado, o dispositivo respondia `not implemented` a tudo.

**Por quê:** é a regra 3 da seção 5 (a execução é sempre local) encontrando a
Fase 2. Uma frase que o regex reconhece e uma que só o LLM entende terminam no
**mesmo** `LocalServices`, com os mesmos slots. Se divergissem, o aparelho teria
dois comportamentos para a mesma frase, dependendo de a internet estar de pé.

**Ferramentas terminais.** O resultado do dispositivo já é uma frase redigida
para ser falada, e ela vai ao ar como está — sem uma segunda rodada de LLM.
Começou como economia de latência e virou correção: perguntando *"o que eu tenho
marcado"* com um timer e um alarme na fila, o modelo recebeu os dois e respondeu
só o alarme. Resumir uma lista é perder item. Tentar consertar pelo prompt
("repita sem omitir") saiu pior — o modelo passou a narrar que ia chamar a
ferramenta em vez de responder.

| Turno | Com 2ª rodada de LLM | Com ferramenta terminal |
|---|---|---|
| `listar_agendamentos` | 3,7 s, lista incompleta | **2,1 s**, lista completa |
| `criar_alarme` | 4,1 s | **2,3 s** |

**Consequências:**
- `ToolResult` ganhou `value`: nem toda ferramenta é só "deu certo", e `listar`
  precisa devolver conteúdo. Continua sendo texto — o gateway não conhece as
  estruturas do dispositivo e não deve conhecer.
- Argumentos são convertidos com tolerância no dispositivo. Não é zelo
  gratuito: o llama3.1:8b mandou `{"segundos": "5400"}`, string, com o schema
  dizendo `integer`. Recusar isso seria recusar uma chamada correta.
- Ferramenta inventada volta como falha explícita, nunca como traceback — é
  metade do critério de aceite da fase.
- `MAX_TOOL_ROUNDS = 3`: um modelo pequeno que erra os argumentos repete a mesma
  chamada para sempre, e sem teto o turno nunca termina, com o aparelho parado
  em THINKING.

**O critério de aceite está cumprido pela metade, e a metade que falta é do
modelo, não do código.** Ver [D19](#d19--o-llama-318b-nao-faz-as-duas-coisas).

---

## D19 — O llama3.1:8b não faz as duas coisas

**Data:** 2026-09-01
**O plano diz:** a seção 6 já previa isto — *"modelo pequeno **classifica**,
modelo grande **responde**"* — e a [D11](#d11) escolheu um modelo aberto local
deixando em aberto se um 8B dá conta.

**O que foi medido:** com as quatro ferramentas declaradas, o llama3.1:8b passa
a recusar conhecimento geral. Mesma pergunta, mesmo prompt de sistema, a única
diferença sendo a presença das ferramentas na chamada:

| | "qual a capital da Austrália" |
|---|---|
| **Com** ferramentas | "Não sei a resposta para essa pergunta." |
| **Sem** ferramentas | "A capital da Austrália é Canberra." |

Repetido sobre dez frases, cinco de agenda e cinco de conhecimento geral:

| | Resultado |
|---|---|
| Chamou a ferramenta certa | 4 de 5 |
| Inventou chamada inexistente | 0 de 5 |
| Respondeu conhecimento geral | **0 de 5** — as cinco viraram "não sei" |

**Três variantes testadas, todas piores:**

1. **Tirar a instrução "se não souber, diga que não sabe".** O modelo passou a
   **inventar ferramenta**: cuspiu `{"name": "pesquisar", "parameters": {...}}`
   como texto — uma ferramenta que não existe — e chamou `criar_timer` para
   *"quem escreveu Dom Casmurro"*. É literalmente o modo de falha que o critério
   de aceite da Fase 3 nomeia.
2. **Instruir no prompt que ferramentas servem só para timer e alarme.** Sem
   efeito: as recusas continuaram.
3. **Separar em duas chamadas** (uma decide, outra responde), que é a arquitetura
   da seção 6 com um modelo só. Ficou muito pior — com prompt de decisão, o
   modelo chama ferramenta para tudo: *"qual a capital da Austrália"* virou
   `criar_alarme(hora=0, minuto=0)`.

**O que fica:** a variante atual, que é a melhor das quatro medidas — ferramentas
funcionando, nada inventado, conhecimento geral perdido.

**Um portão por palavra-chave foi considerado e não implementado.** Abrir as
ferramentas só quando a frase menciona timer/alarme/acorda/lembra restaura a
maior parte do conhecimento geral, mas erra nos dois sentidos: fecha em
*"desmarca o que eu agendei"* (perde a intenção) e **abre** em *"me conta uma
piada"*, por causa do "conta" — e ferramenta aberta numa frase dessas é um timer
falso sendo criado. Trocar uma regressão total por uma silenciosa não é troca boa
o bastante para ser feita sem decidir.

**A decisão que isto força:** a D11 deixou em aberto *"se um modelo aberto de 8B
dá conta de busca fundamentada"*. A resposta chegou antes da busca existir, e por
um caminho que ninguém esperava: **ele não dá conta de ter ferramentas e
conhecimento ao mesmo tempo.** As saídas são um modelo maior, um modelo melhor em
ferramentas, ou o modelo de nuvem que a seção 6 sempre apontou — todas atrás da
mesma interface `LLMProvider`, que é o ponto de troca e continua intacto.

---

## D20 — O modelo passa a ser o qwen3:8b, com o raciocínio desligado

**Data:** 2026-09-01
**O plano diz:** a seção 6 escolhe modelo aberto local e prevê trocar por um de
nuvem atrás da mesma interface. A [D11](#d11) escolheu o llama3.1:8b via Ollama.

**O que mudou:** `LLM_MODEL` passa a ser `qwen3:8b`, e o provedor manda
`think: false` na chamada.

**Por quê:** a [D19](#d19--o-llama-318b-nao-faz-as-duas-coisas) mediu que o
llama3.1:8b não tem ferramentas e conhecimento ao mesmo tempo. Repetindo a mesma
bancada de doze frases nos candidatos:

| | llama3.1:8b | qwen3:4b | qwen3:8b |
|---|---|---|---|
| Ferramenta certa | 4/5 | 5/5 | **5/5** |
| Conhecimento geral | 0/7 | 3/7 | **6/7** |
| Mediana por turno | **1,7 s** | 5,5 s | 2,8 s |

O llama chegou a chamar `listar_agendamentos` para *"cancela o alarme que eu
marquei"*. O qwen3:8b acertou as cinco.

**Por que o raciocínio fica desligado.** A pergunta óbvia é se ele não resolveria
a alucinação. Medido no 8b, dezesseis perguntas factuais, metade fáceis e metade
obscuras, duas repetições cada:

| | `think=false` | `think=true` |
|---|---|---|
| Acertos | 11/16 | 12/16 |
| **Erros com confiança** | **1** | **0** |
| "Não sei" | 4 | 5 |
| Mediana | **1,6 s** | **17,1 s** |

Ele **ajuda**, e de um jeito específico: não passou a saber mais, passou a
admitir que não sabe. *"Grande Sertão: Veredas foi escrito por José de Alencar"*
virou *"não sei quem escreveu"*. Recuperou um fato real (o segundo presidente do
Brasil, que sem raciocínio era "não sei" nas duas tentativas).

**Mas custa dez vezes mais tempo, e a seção 11 orça 1,5 s para o nível 2.**
Dezessete segundos de espera para ouvir "não sei" é pior que a alucinação, porque
acontece em *toda* pergunta e não em uma a cada seis.

O argumento decisivo não é a latência, é a causa: o modelo não errou por falta de
raciocínio, errou por não ter o fato. Pensar mais sobre um fato ausente produz
uma justificativa melhor para a mesma resposta errada. O que ataca a causa é
fundamentar — buscar, ler, responder com fonte —, e é por isso que a busca web
importa mais do que parecia quando a ordem das ferramentas foi escolhida.

Fica em `LLM_THINK` para não ser uma escolha trancada no código.

**Vazamento é problema só do 4b.** O 8b respeita `think: false` e não devolveu
rascunho em nenhum dos dois modos. O qwen3:4b devolveu o rascunho como conteúdo:

```
ideraldinho> Okay, the user is asking for the capital of Australia...
```

Num assistente de voz isso não é um log feio: é o aparelho **falando** isso em
voz alta, com a minha voz. O 4b foi descartado por esse motivo, não pela nota.

**Consequências:**
- `OllamaProvider` ganhou o parâmetro `think`, e `LLM_THINK` no `.env`. Fica
  configurável porque um modelo sem raciocínio ignora o campo, e porque a
  medição acima mostra que ligar é uma troca real (menos mentira, muito mais
  espera) e não um erro.
- Turnos de conhecimento geral ficaram em ~1,2 s ponta a ponta, dentro do
  orçamento de 1,5 s da seção 11. Os de ferramenta ficam em ~2,4 s.
- A pergunta em aberto da D11 — *"se um modelo aberto de 8B dá conta"* — vira:
  **dá, para ferramentas e conversa curta.** Para busca fundamentada continua em
  aberto, e agora por um motivo concreto: ele alucina. Perguntado quem escreveu
  Dom Casmurro, respondeu *"Mario Quintana"* uma vez em seis. Achei que fosse
  efeito do histórico da conversa e fui medir: não se reproduziu, 5/5 corretas
  depois. É alucinação avulsa de modelo pequeno, e é o argumento mais forte a
  favor da busca web ser a próxima ferramenta.

**Nota de operação:** o modelo tem 5,2 GB e a placa aqui tem 6 GB. Cabe, mas sem
folga. Quem rodar isto com o fine-tune do Piper ao mesmo tempo vai repetir o
`CUDA out of memory` que já está registrado no `comandos.md`.

---

## D21 — Spotify é a primeira ferramenta que o gateway executa

**Data:** 2026-09-01
**O plano diz:** a seção 3 põe `Ferramentas · Segredos · Histórico` no gateway, e
a Fase 3 entrega "busca web, Spotify, Home Assistant".

**O que mudou:** `gateway/tools/spotify.py` controla playback pela Web API do
Spotify. É a **primeira ferramenta executada no gateway** — as quatro de agenda
([D18](#d18--ferramentas-do-dispositivo-o-llm-pede-o-pi-executa-e-o-resultado-é-a-resposta))
são declaradas lá e executadas no dispositivo.

**Por quê a assimetria:** aqui há segredo. O `client_secret` e o refresh token
ficam no servidor e não descem pelo fio. O dispositivo só ouve *"Tocando
Construção, de Chico Buarque."* — que é, aliás, a mesma regra do
[D7](#d7--pela-rede-sobe-só-texto-o-áudio-nasce-no-dispositivo) vista do outro
lado: pela rede desce texto, não credencial.

**Home Assistant fica de fora da Fase 3.** Uma lâmpada Elgin, hoje controlada
pela Alexa, é todo o parque instalado. Não paga o Tailscale, a instância do HA e
a Fase 5 que o plano exige para chegar nele. Se a casa crescer, volta.

**Degradação é comportamento, não detalhe.** Sem credenciais no `.env` **ou** sem
o refresh token em disco, as ferramentas de música **não são declaradas** ao
modelo. Isso decorre direto do [D19](#d19--o-llama-318b-nao-faz-as-duas-coisas):
um modelo pequeno que vê uma ferramenta indisponível tenta usar mesmo assim.
Verificado com o Spotify desligado:

```
voce> toca chico buarque
ideraldinho> Nao sei tocar Chico Buarque.
        Posso ajudar com timers, alarmes ou listar/agendar coisas?
```

Nenhuma chamada inventada, e o `/health` responde `"spotify": "off"`.

**Detalhes que a documentação atual obrigou a mudar** — o plano avisava na seção
14 que houve remoção de endpoints em fev/2026, então tudo foi conferido contra a
referência viva em vez de escrito de memória:

- Os endpoints de player (`/me/player/play`, `/pause`, `/next`, `/previous`,
  `/devices`, `/currently-playing`) continuam de pé e **não** estão depreciados.
- O `limit` do `/search` hoje é **0–10**. Era 50.
- O `redirect_uri` precisa ser `127.0.0.1`; o Spotify recusa `localhost` desde
  2025. Um `localhost` no dashboard é meia hora de erro `INVALID_CLIENT`.

**Quatro modos de falha tratados, porque são os que acontecem:**

- **403** — todo controle de playback exige Premium. A API não diz "compre
  Premium", diz 403. Vira *"o controle de música precisa de Spotify Premium"*.
- **404 / nenhum aparelho** — mandar tocar com o Spotify fechado em todo lugar
  não faz nada. O cliente lê `/me/player/devices` antes, prefere o que está
  ativo, e se não houver nenhum diz *"abra o Spotify em algum aparelho
  primeiro"*.
- **204 sem corpo** — "nada tocando" responde 204, e `r.json()` num corpo vazio
  derruba cliente ingênuo.
- **Refresh token rotacionado** — o Spotify às vezes devolve um token novo na
  renovação. Ignorar isso mata a conexão semanas depois, longe da causa.

**Uma escolha de segurança que parece detalhe:** o despacho nome → método é um
dicionário explícito, não `getattr(client, nome)`. Com `getattr`, um nome
inventado pelo modelo viraria chamada de método arbitrário do cliente. Há teste
para isso.

**Escopos pedidos:** só `user-read-playback-state` e
`user-modify-playback-state`. Sem playlists, biblioteca, e-mail ou histórico.

**Verificado com conta real em 2026-09-01**, e a estreia revelou três coisas que
o HTTP falso não tinha como mostrar:

1. O 403 ambíguo, acima.
2. Tocar a faixa avulsa (`uris` com um item) deixa a fila com uma música só: o
   primeiro "próxima" acaba com ela em silêncio, e o aparelho ainda dizia
   "Próxima". Agora toca no **contexto do álbum** (`context_uri` + `offset`),
   então pedir uma música começa nela e continua no disco.
3. O modelo dizia ter pausado sem chamar a ferramenta — causa e correção em
   [D22](#d22--o-histórico-guarda-a-chamada-de-ferramenta-não-só-a-frase-falada).

Confirmado funcionando: autorização, renovação de token, listagem de aparelhos,
tocar, pular, pausar e "o que está tocando", com `is_playing` conferido direto na
API a cada passo. Os 27 testes contra `httpx.MockTransport` continuam sendo o que
cobre os caminhos de erro.

---

## D22 — O histórico guarda a chamada de ferramenta, não só a frase falada

**Data:** 2026-09-01
**O plano diz:** a seção 3 põe o histórico da conversa no gateway. Não diz o que
entra nele.

**O que mudou:** toda ferramenta executada grava no histórico o **par**
`tool_call` + `tool_result`, e não apenas a frase que foi falada. `Message`
ganhou o campo `tool_calls`, e o provedor do Ollama passa a serializá-lo.

**Por quê:** a [D18](#d18--ferramentas-do-dispositivo-o-llm-pede-o-pi-executa-e-o-resultado-é-a-resposta)
tornou as ferramentas terminais — o resultado vira fala direto, sem segunda
rodada de LLM — e, sem perceber, parou de chamar `add_tool_result`. O histórico
passou a registrar só isto:

```
user:      toca construcao do chico buarque
assistant: Tocando Construcao, de Chico Buarque.
```

Nenhum vestígio de que uma ferramenta existiu. **O modelo relê essa conversa, vê
que ação virou prosa, e faz prosa no turno seguinte.** Com o Spotify ligado:

```
voce> pausa a musica
ideraldinho> Pausando a musica.        <- e a musica continuou tocando
```

Um assistente que diz ter feito o que não fez é pior que um que recusa.

**Medido**, com o mesmo pedido e três formas de histórico:

| Histórico contém | Chamou a ferramenta |
|---|---|
| Só a frase falada (o que existia) | **0 de 3** |
| Só o papel `tool` com o resultado | 1 de 3 |
| `tool_calls` + `tool` + a frase | **3 de 3** |

Sem histórico nenhum eram 6 de 6. Ou seja: **não era o modelo sendo fraco, era o
histórico ensinando o comportamento errado.**

**Uma correção pelo prompt foi tentada antes e não funcionou.** Generalizar a
regra ("nunca diga que fez algo sem chamar a ferramenta") para além de timers não
mudou nada: o exemplo dentro da própria conversa pesa mais que a instrução no
system prompt. A regra ficou de qualquer forma, porque está certa — mas ela não
era a correção.

**Consequências:**
- `Message.tool_calls` existe no contrato do `LLMProvider`, então qualquer
  provedor futuro (nuvem, outro runtime) precisa saber serializá-lo. É o
  formato padrão de tool use, não uma invenção local.
- O par entra no histórico **também** nas ferramentas terminais, que é onde o
  bug morava. A frase falada continua sendo a do dispositivo.
- O `_trim` pode cortar um `tool_call` deixando o `tool_result` órfão. Não é
  problema hoje (o corte é por número de turnos e o par é adjacente), mas é a
  primeira coisa a olhar se o modelo voltar a se comportar mal em conversa
  longa.

---

## D23 — A música toca no próprio aparelho por padrão

**Data:** 2026-09-01
**O plano diz:** a seção 12 alerta que *"o vilão é a música"* no consumo de dados
e a seção 14 registra `librespot` como alternativa ao Spotify Connect. Não diz
onde a música deve sair.

**O que mudou:** `SPOTIFY_DEVICE` (padrão `Ideraldinho`) nomeia o aparelho onde tocar
quando ninguém disser onde. A escolha passa a ser, em ordem: o aparelho que a
pessoa nomeou na frase → o preferido da configuração → o que está ativo → o
primeiro da lista. Entraram duas ferramentas, `listar_aparelhos` e
`trocar_aparelho`, e `tocar_musica` ganhou um campo `aparelho` opcional.

**Por quê:** é a diferença entre o Ideraldinho **ser** a caixa de som e ser um
controle remoto do PC. A Alexa que ele substitui é, para o Spotify, apenas um
Spotify Connect device — apareceu na lista da conta como `Echo Dot de Ideraldo`.
Na Pi o equivalente é o **raspotify** (empacotamento do `librespot`), que se
anuncia com o nome configurado e toca pela placa de som dela.

**O que isto não exige:** nenhuma mudança na app do dashboard. O raspotify faz
login com a conta diretamente e não usa a Web API; os escopos já autorizados
(`user-read-playback-state`, `user-modify-playback-state`) bastam para listar
aparelhos e transferir. O *Web Playback SDK* continua sem uso — ele serve para
tocar dentro de um navegador.

**Enquanto a Pi não existe**, o nome `Ideraldinho` não casa com nada e a escolha cai
para o aparelho ativo. Verificado com a conta real, tocando de verdade em cada
um:

| Pedido | Onde tocou |
|---|---|
| `"iphone"` | iPhone |
| `"celular"` | iPhone (pelo tipo `Smartphone`) |
| `"echo dot"` | Echo Dot de Ideraldo |
| `"caixa de som"` | Echo Dot (pelo tipo `Speaker`) |
| nada | o aparelho ativo, porque o preferido não existe |
| `"ideraldinho"` | *"não achei ideraldinho entre os aparelhos ligados"* |
| `"geladeira"` | volta para a busca — não parece aparelho |

O celular só entra na lista quando o app está aberto e ativo no aparelho: um
Connect device precisa se anunciar, e não dá para "acordar" um que não está lá.
É mais um argumento para o `raspotify` na Pi, que fica anunciado o tempo todo.

**Casar aparelho pelo tipo falado, não só pelo nome.** Ninguém diz *"toca no
iPhone"*: diz *"toca no celular"*. A API devolve `type` (`Computer`,
`Smartphone`, `Speaker`, `TV`), e `TIPOS_FALADOS` mapeia as palavras que as
pessoas usam. A ordem é nome exato → trecho do nome → tipo, e o tipo vem por
último de propósito: se alguém batizou uma caixa de som de "Computador", o nome
que a pessoa deu ganha do rótulo da API.

Sem essa etapa, *"toca no celular"* não casava com nada e a palavra "celular"
caía dentro da busca — pedir Construção no celular tocou a versão do Ney
Matogrosso.

**Um defeito de desenho de parâmetro, achado em uso.** `busca` e `aparelho` são
dois campos de texto livre lado a lado, e o modelo divide errado:

```
"toca construcao do chico buarque"
   -> tocar_musica {"busca": "Construcao", "aparelho": "Chico Buarque"}
```

O artista foi parar no campo do aparelho, a busca virou só "Construção", e tocou
*Samba de Orly*. A correção não é confiar mais no modelo: antes de usar,
`aparelho` é conferido contra a lista real, e **se não for um aparelho, volta
para dentro da busca**. Recusar seria pior — a frase era perfeitamente
compreensível.

**E a correção da correção.** Dobrar tudo que não casa para dentro da busca
esconde o caso legítimo: pedir para tocar num aparelho que **existe e está
desligado**. Testando *"toca no ideraldinho"* antes de a Pi existir, a busca virou
"Construção Ideraldinho" e tocou outra gravação — silenciosamente. Agora o nome só é
dobrado na busca se **não parecer** referência a aparelho; parece quando é o
nome configurado em `SPOTIFY_DEVICE` ou uma palavra de tipo. Nesses dois casos a
resposta é *"não achei X entre os aparelhos ligados"*, que é a verdade.

**Como os nomes de aparelho existem.** Cada cliente de Spotify se **anuncia**:
ao subir, registra-se nos servidores (e na rede local, por mDNS) dizendo o nome
que ele mesmo escolheu. Ninguém batiza de fora. Na conta desta casa:

| Nome | Quem definiu |
|---|---|
| `RUIPC` | o hostname do Windows — o app desktop usa o nome da máquina |
| `iPhone` | *Ajustes → Geral → Sobre → Nome* |
| `Web Player (Chrome)` | o próprio Spotify, a partir do navegador |
| `Echo Dot de Ideraldo` | o nome dado no app da Alexa |

No `raspotify` isso é uma linha em `/etc/raspotify/conf`, confirmada na fonte:

```
#LIBRESPOT_NAME="Librespot"
```

Comentada, o padrão vira `raspotify (hostname)`. Descomentar com
`LIBRESPOT_NAME="Ideraldinho"` e `sudo systemctl restart raspotify` faz a Pi entrar na
lista com esse nome, indistinguível dos outros para a API.

**Consequência de contrato:** `LIBRESPOT_NAME` na Pi e `SPOTIFY_DEVICE` no
gateway têm que ser a **mesma string**. É a única coisa que liga um ao outro.

**E por que o casamento é por nome, nunca por id:** o `device_id` que a API
devolve não é estável entre reinicializações. Guardar o id do aparelho preferido
funcionaria até o primeiro reboot da Pi, e falharia num lugar onde ninguém
procuraria. Aqui isso foi sorte de desenho e não previsão, mas fica registrado
para não ser "otimizado" depois.

**O que fica para a Fase 6, junto com a Pi:**

- Instalar o raspotify (`curl -sL https://dtcooper.github.io/raspotify/install.sh | sh`)
  e pôr `LIBRESPOT_NAME="Ideraldinho"` no `/etc/raspotify/conf`. Não roda em ARMv6
  (Pi 1 e Zero v1); a Pi 5 está muito acima disso.
- **Ducking**: o `librespot` e o Piper disputam a mesma placa de som. Se o Ideraldinho
  precisa falar enquanto a música toca, alguém tem que abaixar a música. É o
  mesmo problema do barge-in da Fase 7 (interromper o assistente falando), e
  resolver os dois juntos é mais barato que separado.
- O `librespot` exige Premium, que a conta já tem (verificado em D21).

---

## D24 — Busca na internet, sem chave, e a primeira ferramenta não-terminal

**Data:** 2026-09-01
**O plano diz:** a seção 1 lista "busca na internet com resposta fundamentada"
como requisito funcional principal, e a Fase 3 a entrega junto com Spotify e
Home Assistant.

**O que mudou:** `gateway/tools/search.py`, com um `SearchProvider` trocável.
O padrão é **DuckDuckGo via `ddgs`**, que não exige chave, conta nem cartão.
`Brave` existe atrás da mesma interface para quem quiser resultado mais estável.

**Por quê o padrão sem chave:** esta ferramenta é a resposta ao defeito do
[D20](#d20--o-modelo-passa-a-ser-o-qwen38b-com-o-raciocínio-desligado) — o modelo
alucinando com confiança. Exigir cadastro para a fundamentação funcionar seria
trocar um problema por outro, e contraria a preferência que já guiou D2 (nada de
nuvem no caminho crítico) e D11 (modelo aberto). Trocar por Brave é uma variável
de ambiente.

**É a primeira ferramenta não-terminal.** As de agenda (D18) e as do Spotify
(D21) devolvem uma frase pronta que vai ao ar como está. Aqui não existe frase
pronta: existem cinco trechos de páginas, e a resposta falada **tem** que ser
redigida a partir deles. É o caminho de segunda rodada de LLM, que até agora
nenhuma ferramenta usava — o mesmo que D18 tinha desligado por perder itens de
lista. Aqui ele é obrigatório, e é o uso certo dele.

**Resultado, nas perguntas que a bancada do D19 media:**

| | antes (sem busca) | com busca |
|---|---|---|
| "quem escreveu Grande Sertão: Veredas" | *"não sei"* | **João Guimarães Rosa** |
| "quem escreveu O Cortiço" | *"não sei"* | **Aluísio Azevedo** |
| "distância da Terra até a Lua" | *"não sei"* | **384.400 km** |
| "capital da Austrália" | Canberra | Canberra, **sem buscar** |

A última linha importa: o modelo não busca quando sabe. Não foi preciso ensinar
isso — a descrição da ferramenta diz para usar quando não houver certeza, e ele
respeitou, em 2,1 s contra os 7 a 12 s de um turno com busca.

**Latência.** A busca sozinha mede 1,1 a 3,6 s (a primeira chamada do processo
custa 17 s, partida a frio). O turno inteiro fica entre 7 e 12 s: busca, mais
duas rodadas de LLM. Está muito acima do 1,5 s que a seção 11 orça para o nível
2 — e é um custo que a fundamentação tem, não um defeito a corrigir. Um turno de
conhecimento sem busca continua em ~2 s.

**Um bug que só a busca revelou:** o gateway quebrava frase em todo `.`, e a
busca foi a primeira coisa a trazer números formatados. *"384.400 quilômetros"*
virava duas falas — *"aproximadamente 384."* e *"400 quilômetros."*. Agora ponto
entre dígitos não encerra frase. Segurar o ponto de "1899." até a frase seguinte
custa uma fala um pouco mais tarde; partir um número custa uma resposta que soa
quebrada.

**Consequências:**
- `ddgs` entra no `requirements-gateway.txt`, que a D15 mantém enxuto. É a
  primeira dependência do gateway que não é `fastapi`/`httpx`/`dotenv`, e entrou
  porque `gateway/` a importa — a regra do D15 continua valendo.
- Sem `ddgs` instalado, ou com `SEARCH_PROVIDER=none`, a ferramenta não é
  declarada e o `/health` diz `"busca": "off"`. Mesma regra do D19: não oferecer
  ao modelo o que ele não pode usar.
- O system prompt ganhou a única instrução específica de formato que existe:
  responder em uma ou duas frases a partir dos trechos, sem ler a lista nem o
  endereço do site. Sem ela o modelo lê a numeração em voz alta.

---

## D25 — Sem framework de agente no gateway, e por quê

**Data:** 2026-09-01
**O plano diz:** a seção 6 define a interface `LLMProvider` e a seção 3 põe as
ferramentas no gateway. Não menciona framework de orquestração.

**A pergunta que motivou isto:** já que a busca só roda no gateway, na VPS, não
valeria usar um *harness* lá — LangChain, LlamaIndex, smolagents — ou um modelo
especializado em ferramentas, tipo Hermes?

**O que ficou decidido:** nada de framework, e o modelo continua o
[qwen3:8b](#d20--o-modelo-passa-a-ser-o-qwen38b-com-o-raciocínio-desligado). As
duas coisas por motivos diferentes.

### O framework

O laço de ferramentas que existe hoje tem cerca de **40 linhas** em
`gateway/api/session.py`: pede ao modelo, executa, grava o par no histórico,
repete até `MAX_TOOL_ROUNDS`. É o que um harness faz, e já está feito.

O que ele custaria é mensurável. A [D15](#d15--a-imagem-do-gateway-não-instala-o-requirementstxt)
deixou a imagem do gateway com cinco dependências — `fastapi`, `uvicorn`,
`httpx`, `dotenv`, `ddgs` — porque ela roda numa VPS pequena. Um framework de
agente traz dezenas de pacotes transitivos para substituir 40 linhas.

E há um custo que não aparece em `requirements.txt`: **as três correções mais
importantes de hoje dependeram de o mecanismo estar à vista.**

- [D18](#d18--ferramentas-do-dispositivo-o-llm-pede-o-pi-executa-e-o-resultado-é-a-resposta):
  o resultado da ferramenta é a resposta, sem segunda rodada.
- [D22](#d22--o-histórico-guarda-a-chamada-de-ferramenta-não-só-a-frase-falada):
  o histórico precisa guardar `tool_call`, não só a frase falada. Só foi
  encontrado porque o histórico é um `list[Message]` que dá para imprimir.
- [D24](#d24--busca-na-internet-sem-chave-e-a-primeira-ferramenta-não-terminal):
  a quebra de frase não pode partir "384.400".

Nenhuma delas é configuração de framework. São decisões sobre o que entra no
prompt e o que sai como fala — exatamente a camada que um harness abstrai.

### O modelo

Hermes (NousResearch) é treinado para *function calling*, e o argumento faria
sentido se o gargalo fosse esse. Não é: a bancada do
[D20](#d20--o-modelo-passa-a-ser-o-qwen38b-com-o-raciocínio-desligado) mediu o
qwen3:8b em **5/5** na escolha de ferramenta. O que faltava era fundamentação, e
foi a busca que resolveu, não o modelo.

Restrição de hardware, registrada para não se perder: a placa aqui tem 6 GB e o
qwen3:8b já ocupa 5,2. Um Hermes útil (8B) empata; qualquer coisa maior não cabe
sem descarregar o modelo entre turnos.

### Onde um harness ganharia de verdade

**Busca de múltiplos passos.** Hoje a resposta usa só os trechos que o buscador
devolve; um agente de pesquisa abriria as páginas, leria, refinaria a consulta e
sintetizaria. A qualidade melhoraria de verdade.

O que impede não é preguiça, é o orçamento: o turno com busca já mede 7 a 12 s
contra o 1,5 s que a seção 11 orça para o nível 2. Um laço de pesquisa
multi-passo custa 20 a 40 s, e ninguém espera isso falando com um aparelho no
quarto.

**O passo seguinte, se o trecho do buscador não bastar,** é buscar o texto da
primeira página e mandá-lo junto: ~30 linhas em `search.py`, sem framework
nenhum. Se depois disso ainda faltar, a conversa sobre harness volta — com
número, e não com intuição.

**Revisar esta decisão quando:** aparecerem perguntas reais em que o trecho do
buscador é insuficiente (o log de nível 2 registra as frases), ou quando o LLM
sair do Ollama local para um modelo de nuvem e o orçamento de latência mudar de
forma.

---

## D26 — Função que decide sobre "agora" recebe o agora por parâmetro

**Data:** 2026-09-02
**O plano diz:** nada. É uma regra de código, não de arquitetura.

**O que mudou:** `_proxima_ocorrencia` passou a aceitar `agora` como parâmetro
opcional, em vez de chamar `datetime.now()` por dentro.

**Por quê:** o teste do alarme quebrou sozinho às 00:02, sem nenhuma mudança de
código. Ele calculava "duas horas atrás" a partir do relógio e assumia que isso
seria ontem; à meia-noite e dois, duas horas atrás é 22h, e 22h **de hoje** ainda
está no futuro. O alarme foi corretamente marcado para hoje, e a asserção de que
cairia amanhã falhou.

**O código estava certo. O teste é que era refém do relógio** — e passou dois
dias assim porque a suíte nunca tinha rodado de madrugada.

**Consequências:**
- O teste deixou de usar horas relativas e passou a cobrir cinco bordas fixas,
  incluindo as duas da virada do dia.
- A regra vale para o resto: função que decide alguma coisa sobre "agora" recebe
  o agora de quem chama. Sem isso ela só é testável no horário em que funciona.
- O agendador (`device/local/scheduler.py`) ainda lê `time.time()` por dentro,
  e é o outro lugar onde isso vai doer — ali o problema não é o horário, é o
  relógio da Pi, que pode acordar em 1970 e ser corrigido pelo NTP no meio de
  uma espera.

---

## D27 — O rosto é uma página web, e o renderizador é trocável

**Data:** 2026-09-02
**O plano diz:** a seção 11 já previa "HTML/CSS/JS em Chromium kiosk, estado via
WebSocket local", e a seção 5 já abstraía o modo de exibição por variável de
ambiente. Esta decisão confirma o plano — mas por um motivo diferente do que ele
dava, e com um limite escrito.

**A dúvida:** um navegador em cima de uma Pi que ainda nem foi medida. O
Chromium ocioso custa da ordem de centenas de MB de RAM e disputa os mesmos
quatro núcleos que o `faster-whisper` usa — e o risco não é a página travar, é o
**RTF do STT piorar** justamente no estado `THINKING`, que é quando o rosto mais
se mexe. A alternativa séria era `pygame-ce` em SDL2/KMSDRM: sem servidor X, sem
navegador, dezenas de MB, e o rosto virando parte do processo do dispositivo —
sumiriam o servidor WebSocket local, o ciclo de vida do navegador, o autostart
em kiosk e o "quem reinicia o Chromium se ele morrer".

**Por que a página ganhou mesmo assim:** porque a tela não vai ficar só no
rosto. O próximo uso dela é transcrição, timer correndo, capa do álbum,
resultado de busca — e isso é **interface**, onde HTML custa uma linha e um
canvas custa uma tarde. Escolher pygame agora otimizaria o rosto de hoje e
cobraria caro na tela de amanhã. A economia de CPU é real; ela só não é o eixo
que decide.

**O que essa escolha compra de volta, e é o essencial:** a fronteira é o gancho
de observador em `StateMachine.transition()` (`device/state.py`). O rosto é um
assinante. Trocar o Chromium por um app nativo depois é reescrever um assinante,
não a arquitetura — o que torna barato *errar* esta decisão.

**Emoção não é estado.** Os quatro estados são o ciclo do turno; `SPEAKING` é
`SPEAKING` para a hora certa e para uma piada. O humor é um segundo eixo,
ortogonal, e quem o conhece é o gateway, que viu o conteúdo da resposta. Ficou
declarado agora (`Emotion`, campo opcional em `StateMessage`) e **ninguém o
envia ainda**: encaixar um eixo novo no protocolo depois custa caro, e um campo
opcional não custa nada enquanto está vazio.

**Consequências:**
- Só `transform` e `opacity` animam. Qualquer outra propriedade força layout ou
  repintura na CPU, que é a CPU do Whisper. Nada de `<canvas>` nem de
  `requestAnimationFrame`. É a regra que uma regressão futura vai quebrar sem
  aparecer em teste nenhum.
- A tela apaga em `IDLE` — o plano já pedia isso pela bateria, e de quebra zera
  o custo ocioso, que é onde o aparelho passa quase todo o tempo.
- O rosto não pode derrubar o aparelho: observador que levanta exceção vira log,
  servidor que não sobe devolve `False` e o turno continua. A tela é vitrine; o
  timer é função.
- Sem dependência nova: o servidor da página é o próprio `websockets` que o
  `ws_client` já usa, respondendo HTTP pelo `process_request` na mesma porta.

**O critério de aceite da Fase 4 muda.** "Sem travar durante o áudio" não mede
nada: o rosto pode ser fluido e ainda assim roubar o núcleo do STT. O critério
passa a ser **o rosto ligado não piorar o RTF do STT**, medido na Pi, com e sem
a página aberta.

**Nada disso foi medido.** Não há Pi aqui. Os custos citados são ordem de
grandeza conhecida, não medição deste projeto — e é exatamente por isso que a
fronteira trocável existe.

**Adendo, mesmo dia — o tema.** O rosto virou dois: `minimo` (dois retângulos
arredondados) e `anime` (olhos amendoados com íris, cílio, sobrancelha e boca),
escolhidos por `FACE_THEME`. Não é indecisão: é a fronteira sendo usada. Os dois
recebem o mesmo `{state, emotion}`, rodam o mesmo `face.js` e são servidos pelo
mesmo servidor — o que muda é só o desenho.

O `anime` custou quatro geometrias antes de ficar de pé, e o que ele ensinou é
que **desenhar deixa de ser problema de código muito antes do que parece**. Os
erros não foram de CSS: foram de anatomia. Um olho de anime não é uma elipse com
um traço em cima — é uma amêndoa assimétrica, com a íris *cortada* pela pálpebra
e o branco aparecendo só nos cantos. Enquanto a íris coube inteira dentro do
branco, o rosto ficou com cara de desenho infantil, e nenhuma quantidade de
ajuste fino ia consertar isso.

Dois bugs valem registro porque são do tipo que teste não pega:
- o recorte da pálpebra trabalha em coordenadas do viewBox e o cílio anda a
  partir de onde está. Fechar "até 52" cortava zero (o olho começa em 74) e
  ainda assim descia o cílio: cílio boiando no meio do olho. Resolvido dando ao
  recorte a **mesma curva** do cílio — aí o mesmo número serve para os dois;
- o arco do olho fechado (o `^^` de alegria) estava na cor das sobrancelhas.
  No papel ele seria escuro sobre a pele; aqui não há pele, e ele nasceu
  invisível sobre o fundo preto.

**Se um dia a qualidade do desenho for o gargalo,** o caminho não é mais uma
rodada de SVG à mão: é arte pré-renderizada em sprites, tocada pelo mesmo
contrato. Ganha em fidelidade e é ainda mais barata de CPU (decodificação em
hardware); perde em flexibilidade, porque mudar a expressão vira re-render do
asset. Fica registrado como a saída, não como o plano.

**Revisar esta decisão quando:** a medição na Pi mostrar o RTF piorando com a
página aberta. Aí o assinante muda, e o resto fica.

---

## D28 — Ler a primeira página: escrito, medido, e desligado

**Data:** 2026-09-03
**O diário pedia:** "se aparecer pergunta em que o trecho do buscador não
chega, o próximo passo são ~30 linhas lendo a primeira página, e não um
framework (D25)". Foi feito. O que não estava previsto é o que a medição
respondeu.

**A dúvida:** o trecho que o DuckDuckGo devolve tem 400 caracteres. Ele responde
"quando foi" e não parecia responder "por quê" — duas linhas de resumo não
explicam nada. A hipótese era que abrir a página fecharia essa lacuna.

**O que foi construído:** `Leitor`, em `gateway/tools/search.py`. Uma requisição
HTTP com `httpx`, um extrator de texto em cima do `HTMLParser` da biblioteca
padrão, e o texto entrando no prompt amarrado ao `[1]`. Sem dependência nova, e
sem framework de RAG — o extrator inteiro tem menos linhas que a configuração
que um framework pediria.

Os limites são todos defensivos, porque a leitura acontece dentro do turno mais
lento que o aparelho tem: 5 s de timeout, 400 KB de teto de download, 2000
caracteres de corte, `content-type` conferido antes de baixar (PDF não vira
texto), e página que sobrou em menos de 200 caracteres — paywall, app em
JavaScript — devolve nada, porque ocupar prompt sem informar é pior que não ler.

**Só o primeiro resultado.** Abrir os cinco multiplicaria por cinco o pedaço mais
caro do turno, e o buscador já ordenou: se a resposta não está no primeiro, ela
provavelmente também não estava no quarto.

### Duas coisas que a rede ensinou, e nenhuma delas era a esperada

**O User-Agent não abre porta.** A primeira versão fingia ser um Firefox, pelo
motivo de sempre — "senão levam 403". Medido em cinco sites: `httpx` cru,
Firefox, Chrome e um nome honesto entraram exatamente nos mesmos quatro e
levaram 403 do mesmo. Fingir navegador não comprava nada. Ficou o nome do
aparelho.

**A Wikipedia é o caso que importa e era o que estava quebrando.** Ela responde
403 à leitura direta de `/wiki/...` com qualquer User-Agent, e no corpo do erro
manda usar a API. Era metade das falhas — e justamente nas perguntas de
conhecimento, que são a razão da busca existir (D20). O caminho da API entrou:
`action=query&prop=extracts&explaintext`, que devolve o artigo já sem marcação,
melhor do que qualquer coisa que o extrator faria. Detalhe medido: sem uma **URL
de contato** dentro do User-Agent a API também devolve 403; o mesmo pedido passou
a 200 só de acrescentá-la.

Com isso, a leitura foi de **4/10 para 10/10** primeiros resultados lidos,
mediana de 0,62 s e pior caso 0,84 s.

### E aí a medição derrubou a premissa

Quatro perguntas, o mesmo qwen3:8b, duas respostas cada — uma só com os trechos,
outra com os trechos mais a página:

| pergunta | só trechos | com a página |
|---|---|---|
| por que o céu é azul | respondeu | respondeu, e citou oxigênio e nitrogênio |
| por que a Pi 5 perde a hora | respondeu | respondeu igual |
| como funciona o café coado | respondeu | respondeu, e citou o Hario V60 |
| medalhas do Brasil em 2024 | 20, com a divisão certa | igual, mais "segundo melhor da história" |

**O trecho bastou nas quatro.** A página acrescentou vocabulário, não resposta.
E cobrou: de 1 a 3 s por turno, somando o download e o prompt que dobra de
tamanho — num turno que já levava 7 a 12 s.

**Decisão: fica desligado por padrão** (`SEARCH_READ_PAGE=0`). Não é dúvida sobre
o código, que está escrito e coberto por 30 testes; é que a pergunta que
justifica o custo ainda não apareceu. A hipótese do diário era razoável e a
medição disse não — que é exatamente para isso que se mede antes.

**Consequências:**
- A busca ganhou os testes que nunca teve. Ela subiu para produção testada só à
  mão, com rede de verdade; agora os casos que a web serve todo dia — 403, PDF
  no primeiro resultado, paywall, timeout — estão presos em teste.
- A regra da camada é a mesma do rosto (D27): **a leitura não pode derrubar o
  turno.** Toda falha vira `None`, e a busca responde o que já respondia.
- Ligar é uma variável. Se um dia aparecer a pergunta em que o trecho não chega,
  não há nada a escrever.

**Revisar esta decisão quando:** houver log de uso real com uma pergunta que o
trecho não respondeu. Aí a comparação se repete com perguntas de verdade, e não
com quatro que eu escolhi — que é a fraqueza óbvia da medição acima.

---

## D29 — O assistente passa a se chamar Ideraldinho

**Data:** 2026-09-03
**Substitui:** [D8](#d8--o-assistente-se-chama-marcos), que fica no documento
como está. Ela é o registro de uma decisão que foi tomada, não uma afirmação
sobre o presente — reescrevê-la apagaria o motivo pelo qual esta existe.

**O que mudou:** `Marcos` → `Ideraldinho` em todo o código, nos loggers
(`ideraldinho.*`), no `device_id` (`ideraldinho-01`), no system prompt, no título
do rosto, no `SPOTIFY_DEVICE` e nos testes. 27 arquivos, 238 testes passando
depois.

**Por quê:** o aparelho é uma cópia do dono da voz — o TTS é um fine-tune da voz
dele (D4), e o assistente responde com ela. "Marcos" era o nome do repositório e
nada mais; foi escolhido em D8 por alinhamento, não por significado. Um
diminutivo do próprio nome diz o que a coisa é.

**E ele é um wake word melhor.** Cinco sílabas e uma palavra que não existe em
mais nada: as duas coisas que separam um wake word de um gerador de falso
positivo. "Marcos" é curto e comum em conversa — o pior formato possível para
uma palavra que fica escutando a sala o dia inteiro. Isto não é justificativa
inventada depois: é a razão pela qual a troca aconteceu **antes** de treinar o
modelo, e não depois.

**A D8 previu o custo desta decisão, e acertou:** *"renomear agora custa uma
varredura; depois de o nome aparecer em logs, configurações e gravações, custa
mais."* Custou mais — mas ainda antes das gravações do wake word, que é onde
teria ficado caro de verdade.

**Consequências:**
- **`SPOTIFY_DEVICE` mudou de padrão.** Ele tem que ser a mesma string do
  `LIBRESPOT_NAME` no `/etc/raspotify/conf` da Pi (D23) — é o único fio ligando
  os dois. Quando a Pi existir, é `LIBRESPOT_NAME="Ideraldinho"`.
- Um `.env` antigo com `SPOTIFY_DEVICE=Marcos` continua funcionando; só deixa de
  casar com o aparelho quando a Pi se anunciar pelo nome novo.
- O `DEVICE_ID` mudou de padrão de novo. Continua sendo só um identificador.
- O repositório no GitHub continua `Marcos-AI`, e a URL dentro do `USER_AGENT`
  aponta para ele — renomear ali é ação de fora do código, e uma URL que não
  resolve seria pior que um nome velho.
- O **diário fica como foi escrito** onde ele narra o dia 1 escolhendo o nome
  Marcos. Aquilo aconteceu. O resto do diário, que fala no presente, virou
  Ideraldinho.

---

## D30 — O wake word entra pelo encanamento, e o modelo vem depois

**Data:** 2026-09-03
**O plano diz:** Fase 7, openWakeWord, "modelo ONNX treinado com sua voz
(150–200 gravações)", critério de aceite "< 1 falso positivo/hora".

**A dúvida:** a Fase 7 é a primeira coisa do projeto que roda **sem ninguém ter
pedido nada**. Isso inverte a natureza do erro. O STT errar custa uma frase
repetida; o wake word errar custa o aparelho acordando sozinho às três da
manhã. Por isso o critério de aceite dele é um número de falso positivo por
hora, e não uma taxa de acerto — e por isso ele **não pode ser validado aqui**,
sem o microfone, sem a sala e sem a televisão ligada.

**O que foi feito:** a camada `device/activation/`, com o modelo trocável e
tudo o que fica em volta dele testado — 17 testes, nenhum precisando de
microfone. É a mesma escolha do rosto (D27) e da leitura de página (D28): a
parte que quebra calado é o encanamento, não o modelo.

**Números medidos aqui em 03/09/2026, pelo caminho do dispositivo:** 1,0 ms por
frame de 30 ms, **RTF 0,033**. Sobra folga para a Pi ser cinco vezes mais lenta
e ainda ficar em 0,17 — mas isso é aritmética, não medição, e a Pi é quem
responde.

### O que estava difícil de acertar, e não era o modelo

**Os blocos.** O openWakeWord trabalha em 1280 amostras (80 ms) e a captura
entrega frames de 30 ms, porque é o que o webrtcvad aceita. Alguém tem que
juntar os pedaços sem perder amostra na emenda — e uma amostra perdida por
bloco não aparece em teste nenhum, só numa taxa de acerto pior sem explicação.

**O refratário.** A mesma palavra pontua alto em três blocos seguidos. Sem
silenciar depois de um acerto, uma chamada abriria três turnos.

**O reset antes da espera, e não depois do turno.** O modelo guarda estado
entre blocos — é assim que ele reconhece uma palavra que atravessa vários. O
que sobra nesse estado no fim de um turno é **a resposta que o próprio aparelho
acabou de falar**. Reaproveitá-lo é o caminho mais curto para ele acordar com a
própria voz.

**Desligado por padrão**, e por um motivo específico: os modelos que vêm
prontos são `alexa`, `hey_jarvis`, `hey_mycroft` e `hey_rhasspy`. Um aparelho
que atende por "hey jarvis" quando se chama Ideraldinho é pior que um aparelho
sem wake word — ele ensina o usuário a palavra errada.

**O threshold não é um número do código.** Depende do microfone, da distância e
da sala. `python -m scripts.wake` mostra a pontuação ao vivo e conta ativações
por hora, que é como se acha o corte e como se mede o critério de aceite.

**Consequências:**
- Wake word quebrado é um aparelho **sem** wake word, nunca um aparelho que não
  sobe: o modelo que não carrega devolve `False`, e o laço volta a ouvir direto.
- `openwakeword` entra no `requirements.txt` do dispositivo. **Não** entra no do
  gateway — lá não há microfone (D13).
- O `onnxruntime` é reaproveitado do Piper, em vez do `tflite`: um segundo
  runtime na Pi por um modelo de 200 KB seria caro pelo motivo errado.

**Revisar esta decisão quando:** existir o modelo do "Ideraldinho" e um
microfone para medir. Aí o número que interessa — falso positivo por hora —
finalmente pode ser obtido, e o threshold deixa de ser 0,5 por falta de dado.

---

## D31 — O wake word treinado com o que já estava no disco

**Data:** 2026-09-03
**O plano diz:** "modelo ONNX treinado com sua voz (150–200 gravações)".
**A receita do openWakeWord diz:** dezenas de milhares de positivos sintetizados
com o `piper-sample-generator`, mais dezenas de GB de ruído e fala negativa
baixados do HuggingFace, num notebook com GPU.

**A dúvida:** seguir a receita, ou olhar o que o repositório já tinha.

**O que ele já tinha:** sete vozes pt-BR do Piper, dezoito épocas do fine-tune da
voz do dono, e as 315 gravações reais do dataset daquele treino (D4). Escolhido o
caminho local — e a economia de download é a menor das razões.

### As duas razões que decidem, e nenhuma é o tamanho

**A pronúncia.** O checkpoint do `piper-sample-generator` é LibriTTS, em inglês.
Ele diria "Ideraldinho" com fonemas ingleses, e o modelo aprenderia a palavra
errada — o pior tipo de defeito, porque treina bem, mede bem, e só falha na
sala. As vozes pt-BR dizem `ˌideɾaʊdʒˈiɲʊ`, conferido no espeak-ng.

Isso quase passou batido por um motivo instrutivo: a primeira verificação foi
mandar o Whisper transcrever a palavra sintetizada, e ele devolveu "Hidraudinho"
e "hidraldinho". Parecia o Piper errando. Não era — **o mesmo Whisper errou
"que horas são" em quatro das cinco vozes**. O juiz é que estava ruim. Quem
respondeu foi o espeak, que é onde a pronúncia é decidida.

**Os negativos.** As 315 gravações do dono falando outra coisa são o negativo
mais difícil que existe para este caso, e o único que importa de verdade: é essa
voz que fica perto do microfone o dia inteiro. Nenhum dataset do HuggingFace tem
isso.

### O que se treina, e por que cabe numa tarde

O openWakeWord é dois modelos em série. Um extrator congelado transforma áudio em
embeddings de (16, 96); em cima dele roda um classificador pequeno. **Só o
segundo é nosso.** Por isso 3 mil exemplos bastam onde a receita pede dezenas de
milhares — o trabalho pesado já está no extrator, que ninguém treina.

Duas decisões de formato que não são de gosto:

- **Janela de 2,0 s**, porque é o que o extrator converte em exatamente (16, 96).
- **A palavra no fim da janela, não no meio.** Em uso, a janela avaliada é sempre
  a que acabou de passar. Treinar centralizado ensinaria o modelo a esperar meio
  segundo de silêncio depois do nome — meio segundo a mais para acordar.

### O número que o treino reporta engana em uma ordem de grandeza

O treino deu recall 0,975 e precisão 0,967 sobre janelas de 2 s. Parece bom, e
**não quer dizer quase nada**: em uso o modelo não vê 390 janelas, ele vê uma
nova a cada 80 ms — 45 mil por hora. Se fossem independentes, 96,7% de precisão
seriam mais de mil despertares por hora.

Não são independentes, e o refratário come a maior parte. Mas a diferença entre
as duas leituras é grande demais para deixar implícita, e é por isso que
`lab/wakeword/medir.py` existe separado do treino: ele roda a mesma fita como
fluxo, com o refratário ligado, exatamente como o `device/` roda, e reporta na
única unidade que o critério de aceite entende — **falso positivo por hora**.

**Consequências:**
- O `.onnx` sai com entrada (1, 16, 96) e saída (1, 1), que é o formato que o
  `openwakeword.model.Model` carrega por caminho. Nada em `device/` sabe que
  este modelo é caseiro — verificado carregando pelo caminho do dispositivo.
- Os wavs gerados (216 MB) ficam fora do git: são derivados, e dois comandos
  refazem tudo. Mesma regra do D12.
- O threshold de partida sobe de 0,5 para o que a medição em fluxo indicar.

**A fraqueza declarada:** não há ruído de sala real neste dataset. O que há é
ruído sintético — branco, rosa, zumbido de 60 Hz — e um eco simulado. Isso não
soa como uma cozinha às sete da noite, e é o que a medição com o microfone vai
cobrar. Nenhum número obtido aqui substitui `python -m scripts.wake --segundos
3600` com a televisão ligada.

### O primeiro modelo aprendeu a palavra errada, e o erro tem nome

Medido em fluxo, o modelo v1 deu **71 a 101 falsos positivos por hora** — contra
um critério de menos de um. E subir o threshold de 0,30 para 0,95 só levou de
101 para 71, o que já dizia que o problema não era o corte: ele disparava com
confiança, não por pouco.

A quebra por categoria explicou tudo:

| | dispara |
|---|---|
| palavras parecidas | 11,7% |
| o dono falando outra coisa (924 gravações) | 0,1% |
| ruído puro (400 trechos) | 0% |

E a quebra por palavra explicou melhor ainda:

| | dispara |
|---|---|
| Everaldinho | **82,9%** |
| Reginaldinho | 62,9% |
| Geraldinho | 42,9% |
| **Ideraldo** | **0%** |
| que horas são, cadê você, liga a televisão… | 0% |

**O modelo aprendeu `-aldinho`, e não `Ideraldinho`.** Ele decide pelo fim da
palavra e ignora o começo — tanto que "Ideraldo", que divide o começo inteiro e
é o nome do dono da casa, nunca o acorda; enquanto "Everaldinho", que só divide
o fim, acorda em 83% das vezes.

Faz sentido depois de visto: a janela termina logo depois da palavra, então o
fim dela é o que está mais perto da borda que o classificador olha. E uma rede
de três camadas densas pega o atalho que existir.

**O número honesto sem a fita adversária** — só fala real e ruído, que é o que
uma sala de verdade se parece — foi **2,7 por hora**. Ainda acima do critério,
mas na ordem de grandeza certa, e sem nenhum ruído de sala real no treino.

**O conserto não é treinar mais tempo, é mudar o que se ensina:** a lista de
adversárias passou de 6 nomes parecidos para 20, com a família `-aldinho`
ocupando a maior parte, e mais quatro palavras do outro lado da fronteira
(`Ideraldo`, `Ideraldão`, `Ideraldina`, `Ideral`) — o começo certo com o fim
errado. O dataset deixa de parecer a vida real de propósito: o que ele precisa é
ser denso exatamente onde o modelo errava.

### E o segundo erro foi melhor que o primeiro

Corrigida a lista, o modelo v2 melhorou onde devia — "Everaldinho" caiu de 83%
para 53%, "Reginaldinho" de 63% para 18% — e apareceu um campeão novo:
**"Ideraudinho", acordando o aparelho em 94% das vezes.**

Só que "Ideraudinho" não é um erro do modelo. O espeak fonemiza as duas
palavras exatamente igual:

```
Ideraldinho    ˌideɾaʊdʒˈiɲʊ
Ideraudinho    ˌideɾaʊdʒˈiɲʊ
```

**É a mesma palavra.** Escrita diferente, som idêntico — o "l" antes de
consoante vira /w/ em português, então "-raldi-" e "-raudi-" são a mesma coisa.
Eu a tinha posto na lista de negativos justamente por *parecer* parecida no
papel. O modelo recebeu o mesmo som rotulado como positivo e como negativo, e
acertou os dois rótulos que dava para acertar: acordou.

O modelo estava certo. A lista é que estava errada — e ela foi escrita olhando
letra, quando o que o modelo ouve é fonema.

O conserto é uma função de dez linhas, `conferir_fonemas()`, que roda antes de
gerar qualquer coisa e recusa a lista se alguma adversária fonemizar igual ao
nome. Testada pondo o homófono de volta.

### Onde parou

| | v1 | v2 | v3 |
|---|---|---|---|
| acorda quando chamado | 97,0% | — | **94,1%** |
| Everaldinho | 82,9% | 53,1% | 51,0% |
| Reginaldinho | 62,9% | 18,4% | **2,0%** |
| Ideraldo (o nome do dono) | 0% | 0% | 2,0% |
| fala comum + ruído | 2,72/h | 2,72/h | **0 em 44 min** |

O que ainda engana são nomes que eu inventei para serem difíceis e que diferem
do nome por um fonema só — "Ideraldina", "Iberaldinho", "Inderaldinho". Ninguém
diz essas palavras. As que uma casa diz de verdade — "que horas são", "cadê
você", "liga a televisão", e o próprio "Ideraldo" — não acordam o aparelho.

**E "0,00 por hora" é uma frase que não se pode dizer.** Zero eventos em 44
minutos não prova taxa abaixo de 1 por hora: pela regra dos três, o que esses
dados sustentam é um teto de ~4 por hora. O número certo a escrever é **"zero em
44 minutos"**, e o número que decide continua sendo o do microfone.

**Revisar esta decisão quando:** houver microfone. Se o falso positivo real for
alto, o primeiro conserto **não** é treinar mais: é gravar ruído da casa e
refazer o dataset com ele — porque é exatamente a peça que falta.

---

## D32 — A música ganha tipo, e a API do Spotify decide o que é possível

**Data:** 2026-09-04
**O plano diz:** seção 9 lista "controle de música (Spotify)" sem detalhar o que
o controle inclui. A seção 13 já avisava para validar os endpoints na
documentação atual, "houve remoção em fev/2026".

**O que mudou:** `tocar_musica` passa a exigir um campo `tipo` — `musica`,
`album`, `artista` ou `playlist` —, playlists entram (ler, tocar, criar), e
entra o modo aleatório. São onze ferramentas de música, contra seis.

### Por que o tipo era o buraco

Toda busca era `type=track`. Pedir um **artista** achava uma faixa qualquer dele
e tocava o álbum *daquela* faixa — que podia ser um ao vivo de 1999. Funcionava
por acidente, e o acidente era audível.

O caso feio, porém, era outro: "toca minha playlist de treino" virava busca por
uma **faixa** chamada "minha playlist de treino". O Spotify sempre devolve
alguma coisa, então o aparelho tocava música aleatória anunciando que tinha
acertado. É o mesmo padrão do bug do campo `aparelho` (o texto que não é o que
parece indo parar dentro da busca), e o mesmo remédio: reconhecer o que não se
sabe servir e dizer isso.

Três corpos de `/play`, e nenhum intercambiável: música toca no contexto do
álbum com `offset`; álbum toca do começo; artista toca como `context_uri` puro,
porque a documentação diz que `offset` só vale para álbum e playlist. Há um teste
que existe só para impedir que alguém "unifique" isso depois.

### Um de/para pequeno, e por que não um grande

`_tipo_falado` recupera o tipo das palavras da própria frase quando o modelo
deixa o padrão. O conjunto é **fechado**: quatro palavras de tipo, como o
`TIPOS_FALADOS` dos aparelhos é fechado porque só existem seis tipos de
aparelho.

A tabela que **não** foi feita é a de nomes de playlist. Nome de playlist é
conjunto aberto e muda toda semana; uma tabela dessas apodrece em silêncio e o
defeito aparece meses depois. Quem desambigua nome é o modelo, com a frase
inteira na frente — e o enum no schema é o canal para isso.

### Playlists: a ordem é a funcionalidade

As **suas** primeiro (`/me/playlists`), busca pública só depois. Existem
milhares de playlists públicas chamadas "Treino" e nenhuma delas é a sua. A
variante `/users/{id}/playlists` foi removida em fevereiro de 2026 junto com
`Get User's Profile`; a `/me` sobreviveu, e é a única que enxerga playlist
privada.

Playlist criada nasce **privada**. A API cria pública quando ninguém diz nada, e
publicar no perfil de alguém por omissão não é um padrão que se escolheria se
alguém perguntasse.

**Ressalva registrada:** criar playlist por voz continua sendo interação ruim —
nomear, escolher o que entra, confirmar. Entrou porque foi pedido explicitamente
depois dessa ressalva, e `adicionar_atual` existe para que o pedido real
("guarda essa música") tenha resposta, já que a playlist nasce vazia.

### O escopo é gravado no token, não no código

Mudar a constante `SCOPES` não muda nada sozinho: o escopo congela no refresh
token no momento da autorização. Um token de antes das playlists continua
válido, com o escopo velho, e as chamadas novas voltam **403** — que este código
traduzia como "precisa de Premium", dito em voz alta para quem tem Premium.

Agora o escopo concedido vai para o disco junto com o token, e o cliente confere
antes de chamar. Token sem esse campo é de uma versão anterior e assume-se o
escopo antigo, que é o que ele de fato tem. `_save_refresh` passou a preservar o
resto do arquivo: sobrescrevê-lo inteiro faria a primeira renovação apagar o
registro do escopo e derrubar todas as playlists semanas depois, longe da causa.

**Para reautorizar:** apagar `gateway/data/spotify_token.json` e rodar
`py -m gateway.tools.spotify_auth`.

### O que a Spotify decidiu que não dá

Rádio, estação e "toca algo parecido com isso" **não são possíveis**. Em
27/11/2024 a Spotify desligou `recommendations`, `related-artists`,
`audio-features` e `audio-analysis` para apps novos: 403 no mesmo dia, sem fila
de espera e sem caminho de exceção. Este app é novo. Fevereiro de 2026 levou
mais um pedaço, incluindo `Get Artist's Top Tracks` — que a rota do
`context_uri` de artista torna desnecessário — e derrubou o teto do `limit` da
busca de 50 para 10 (o nosso é 1).

Isso fica escrito aqui para que a ideia não volte à mesa daqui a seis meses como
se fosse trabalho pendente.

### O que a conta real ensinou, no mesmo dia

Esta seção foi escrita horas depois do resto, depois de reautorizar e rodar as
onze ferramentas contra a conta de verdade. Dois defeitos apareceram, e nenhum
dos dois podia aparecer num teste sobre HTTP falso.

**1. `limit=1` devolve um item diferente — e pior.** A busca pedia um resultado
só, com a justificativa de que quem escolhe é o Spotify. A premissa está errada:

```
'Pink Floyd'        limit=1: Guns N' Roses  | limit=2: Pink Floyd
'Clube da Esquina'  limit=1: Construção     | limit=2: Clube Da Esquina
'Abbey Road'        limit=1: Abbey Road (Super Deluxe) | limit=2: Abbey Road (Remastered)
```

"Toca Pink Floyd" tocava **Guns N' Roses**, anunciando que tinha acertado. É o
mesmo defeito que esta decisão existe para eliminar, sobrevivendo num parâmetro.
Agora pede `LIMITE_BUSCA = 3` e usa o primeiro. O teste que trava isso confere o
parâmetro, não o resultado — sobre HTTP falso a lista é a que o teste escreveu.

**2. A busca pública de playlist nunca diz "não achei".** `_achar_playlist` caía
na busca pública quando nenhuma das suas casava, e a busca pública sempre devolve
alguma coisa: "playlist que nao existe 12345" voltou uma playlist chamada
**"123445"**. O resultado público agora só passa por `_parece_a_mesma` — o nome
dito precisa estar no nome dela, ou todas as suas palavras de peso precisam
aparecer. Todas, e não a maioria: com duas ou três palavras "a maioria" é uma só,
e uma palavra em comum faz qualquer playlist casar com qualquer nome.

Aqui o custo de recusar é a pessoa repetir; o de aceitar é tocar a playlist de um
estranho.

**O que foi verificado com conta real**, com a faixa conferida a cada passo:

| pedido | o que tocou |
|---|---|
| artista "Pink Floyd" | Wish You Were Here |
| álbum "Abbey Road" | Come Together — a primeira faixa |
| a minha playlist "A NATA" | Ascensão Sonora |
| playlist inventada | recusou, e não trocou a música |
| criar playlist com a atual | criada, com a faixa que tocava |

O `_tipo_falado` também foi conferido contra a API: "minha playlist de treino"
vira busca por `treino` do tipo playlist, e "o album Clube da Esquina" vira
`Clube da Esquina` do tipo álbum.

Continua sem medição: se o qwen3:8b preenche `tipo` corretamente em **fala
espontânea** — a bancada testa o código, não o modelo.

**Revisar esta decisão quando:** houver log de fala espontânea suficiente
para medir se o modelo escolhe o `tipo` certo sem eu escrever a frase.
