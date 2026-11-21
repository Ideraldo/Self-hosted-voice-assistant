# Próximos passos

O roteiro do que vem depois da Fase 7, escrito em 21/11/2026. O
[plano original](ultraplan-v3-assistente-voz-portatil.md) continua sendo a
especificação; este documento é a fila de trabalho, e muda a cada sessão.

Ele foi montado lendo o código, não executando: onde um número aparece como
critério de aceite, ele é a meta, não uma medição. O que já foi medido está em
[`lab/RESULTS.md`](../lab/RESULTS.md) e no [diário](diario-de-bordo.md).

O que organiza a fila é a pergunta "isto depende de quê?" — de nada, da VPS, do
hardware, ou de uma decisão minha.

---

## O que mudou o plano

1. **O LLM vai para a nuvem, e custo não decide.** Uma casa faz 50 a 200 turnos
   por dia, e nessa escala todos os candidatos custam menos de US$ 10 por mês.
   Decidem a latência, a chamada de ferramentas e a política de dados. A
   escolha só fecha na bancada (A3), não em tabela de preço.
2. **O histórico de hoje só vale para o Ollama.** As chamadas de ferramenta não
   têm id, e o corte do histórico pode separar uma chamada do seu resultado. O
   Ollama aceita; uma API de nuvem devolve erro 400.
3. **O aparelho não sabe fazer uma pergunta e ouvir a resposta.** Com o wake
   word ligado, depois de "Confirma?" ele volta a esperar o nome; e um "cancela"
   cai no regex do nível 0 e cancela um timer. Qualquer confirmação falada
   depende de resolver isso antes.
4. **Lançar gasto por voz é viável, mas escrita de dinheiro por voz precisa de
   um desenho próprio.** O app de finanças já expõe um servidor MCP. O difícil
   não é chamar a ferramenta, é não gravar "quinze" quando eu disse
   "cinquenta".

---

## A feature nova: gastos por voz

Conectar o Ideraldinho ao servidor MCP do meu app de finanças pessoais, para
lançar um gasto falando. **Só gastos na primeira versão**: investimento fica de
fora, porque hoje o servidor não tem como listar nem desfazer um aporte, e
escrita que não se desfaz por voz não entra.

### Onde mora

- **O cliente MCP fica no gateway**, que é onde moram os segredos (D21). Escrito
  à mão com `httpx`, como o resto: `initialize`, `tools/list`, `tools/call`.
  Trazer um SDK inteiro para substituir cem linhas é o argumento que a D15 e a
  D25 já recusaram.
- **O modelo não vê as ferramentas MCP cruas.** O servidor trabalha com ids; o
  modelo trabalha com nomes ("no Nubank", "em restaurantes"). O gateway declara
  as suas próprias ferramentas e traduz — o mesmo padrão do `spotify.py`.
- **Sem token, as ferramentas não são declaradas**, como já acontece com a
  música.
- **Fora do modo finanças, o modelo não enxerga ferramenta de dinheiro
  nenhuma.** Dentro dele, as onze de música saem e entram as de finanças: o
  total cai, em vez de subir para vinte.

### O princípio: o modelo propõe, o código grava

Nenhuma ferramenta que o modelo enxerga grava nada.

1. **Ativação explícita.** Uma frase abre o modo ("anota um gasto", "modo
   finanças"), reconhecida por regex antes do LLM. O modo expira sozinho.
2. **Proposta.** O modelo monta o lançamento com nomes. O gateway resolve
   método e categoria contra a lista que o app devolve; se ficar ambíguo, ou não
   casar, pergunta. Data relativa ("ontem") é resolvida por código.
3. **Checagem cruzada do valor.** Um parser determinístico extrai o valor da
   frase original — "50 reais", "mil e duzentos", "87 e 50", "cinquenta conto" —
   e compara com o que o modelo mandou. Divergiu, pergunta de novo.
4. **Leitura de volta, por extenso.** "Vou lançar oitenta e sete reais no
   Outback, no cartão, ontem, quinta, dia dezenove. Confirma?" Valor e data são
   escritos em palavras por código; o Piper nunca recebe "R$ 87,50".
5. **Confirmação fora do LLM.** A frase seguinte passa primeiro por regex:
   "sim / confirma / pode" grava, "não / cancela / esquece" descarta, qualquer
   outra coisa é correção e volta ao modelo. A pendência expira em um minuto.
6. **Valor alto pede o número de novo.** Acima de um limite configurável, um
   "sim" não basta.
7. **Escrita nunca tem retentativa automática.** Em timeout o aparelho diz que
   não sabe se gravou, e confere.
8. **Diário e desfazer.** Cada escrita vai para um arquivo local com o id
   devolvido, os argumentos e a frase original. "Desfaz o último" lê o que vai
   apagar, pede confirmação e apaga pelo id guardado — sem busca feita pelo
   modelo.
9. **Marca de origem.** Todo lançamento por voz leva essa marca nas
   observações, para auditoria no app.

**Método de pagamento não dito:** usa o padrão de `FINANCAS_METODO_PADRAO` e
sempre o lê em voz alta na confirmação, para eu poder corrigir.

**Quem pode lançar.** O pior caso de outra pessoa da casa falando é um
lançamento falso — visível, marcado e reversível; nenhum dinheiro se move. O
risco maior é outro: o token guardado na VPS dá acesso às finanças inteiras, e
não só às ferramentas que eu uso. A primeira versão fica com modo explícito,
leitura de volta, limites e diário. Trava por reconhecimento de locutor é uma
segunda versão, e só depois de medida: ela dissuade, não autentica.

### O que medir antes de confiar

| Medida | Critério |
|---|---|
| Valor certo de ponta a ponta (fala → STT → modelo → parser), em 100+ frases minhas, incluindo pares que soam parecido | zero lançamento errado gravado sem a leitura de volta mostrar o erro; erro de valor antes da confirmação abaixo de 5% |
| Parser de valores, em tabela de formatos | 100% |
| O modelo em modo finanças: ferramenta certa, argumentos certos, nenhuma escrita narrada sem chamada | 95% ou mais, por modelo candidato |
| Resolução de método e categoria com os nomes reais | zero casamento errado silencioso; ambíguo sempre pergunta |
| Regex de confirmação sobre transcrições reais de "sim", "não", "isso", "pode" | zero "não" lido como "sim" |
| O Piper lendo valores e datas, transcrito de volta pelo Whisper | 100% dos valores recuperados |

---

## A fila

Tamanho: P, M ou G. Os itens de um mesmo grupo que não dependem um do outro
podem andar em paralelo.

### A — dá para fazer agora, sem hardware nem VPS

| | O quê | Depende de | Aceite | Tam. |
|---|---|---|---|---|
| **A1** | Histórico com id em cada chamada de ferramenta, e corte por turno inteiro | — | depois de qualquer sequência de turnos e cortes, todo resultado tem a sua chamada antes, com o mesmo id, e o histórico começa em `user` | P |
| **A2** | Clientes de LLM de nuvem (um compatível com OpenAI, que cobre a maioria; um nativo se o escolhido pedir) e queda automática para o reserva | A1 | os testes de protocolo passam com cada provedor falso; com o primário derrubado, o turno é respondido pelo reserva sem fechar o WebSocket | M |
| **A3** | `lab/run_llm.py`: a bancada da D19/D20 refeita com as ferramentas reais, incluindo o `tipo` do Spotify em fala espontânea (aberto desde a D32) | A2 e chaves | tabela por modelo com acerto e mediana/p90; escolhe quem fizer 95% em ferramentas e primeira frase abaixo de 1,5 s | M |
| **A4** | Janela de continuação: o gateway avisa que fez uma pergunta, e o aparelho ouve a resposta sem exigir o nome e sem passar pelo nível 0 | — | com o wake word ligado, pergunta → "sim" funciona 10 em 10; "cancela" durante a pergunta não cancela timer | P–M |
| **A5** | Documentação alinhada ao código | — | nenhuma das divergências levantadas ao desenhar a arquitetura continua aberta | P |
| **A6** | Gastos por voz: (a) cliente MCP e autorização; (b) parser de dinheiro, valor e data por extenso; (c) ferramentas, pendência, confirmação, diário | A1, A4 | com servidor falso, nenhum caminho grava sem confirmação; com o real, 20 lançamentos de teste conferidos no app e desfeitos por voz | G |
| **A7** | Bancada de finanças: as medidas da tabela acima, sobre gravações minhas | A6 | os números, com data, em `lab/RESULTS.md` | M |
| **A8** | Modo tradutor no dispositivo | resultado de `lab/traducao/` | a definir com o número da bancada | M |
| **A9** | Wake word: falso positivo por hora com microfone e sala reais; e um v4 treinado com muitas vozes | microfone (manual); chave de TTS para as vozes | menos de 1 falso positivo por hora; o v4 só substitui o v3 se mantiver 94% e melhorar com vozes de terceiros | M |

Sobre o A9: o v2 já aprendeu a palavra errada uma vez com dado sintético (D31).
O conjunto de teste com voz real fica sempre fora do treino.

### B — precisa da VPS

| | O quê | Aceite | Tam. |
|---|---|---|---|
| **B1** | Deploy: compose de produção com proxy TLS e `wss://`, token forte, firewall | 20 turnos reais com primeira frase em mediana abaixo de 1,5 s e p90 abaixo de 2,5 s | M |
| **B2** | Segredos e operação: tokens no volume, backup do volume, rotação de log, checagem externa de saúde | recriar o contêiner não perde nenhuma autorização; restaurar o backup num volume novo funciona | P |
| **B3** | Contingência: o Ollama de casa alcançado por túnel | com as APIs de nuvem bloqueadas, o turno responde pelo modelo de casa | P |

Na VPS não há Ollama: sem o A2, o gateway sobe lá sem modelo.

### C — precisa de hardware

Na ordem em que vale fazer quando a Pi chegar:

1. Medir STT (`small` contra `base`), Piper, wake word e tradutor **juntos**:
   RTF e memória. É a maior incerteza do projeto ([D9](decisions.md)).
2. Áudio com cancelamento de eco — e só então barge-in e ducking, que são o
   mesmo problema ([D23](decisions.md)).
3. raspotify com o nome do aparelho.
4. Volume como nível 0.
5. Botão, e o rosto em tela cheia.

**O que deve pesar na compra, e dá para medir antes:**

- **Memória.** Whisper, Piper, wake word, tradutor nos dois sentidos e um
  navegador em tela cheia, somados, apontam para 8 GB. Medir a memória
  residente do processo `device` no PC com tudo carregado é o melhor número que
  existe hoje.
- **CPU.** Rodar o pipeline no PC limitado a quatro threads dá um teto
  otimista. O RTF do STT já foi de 0,43 para 0,6–0,9 só saindo da bancada.
- **Áudio.** Um speakerphone USB com cancelamento de eco. Sem ele, barge-in e
  wake word durante a música não existem.
- **Tela.** Decidir a conexão antes, porque o rosto é uma página web.

### D — precisa de uma decisão ou de uma chave minha

| | O quê | Destrava |
|---|---|---|
| D1 | Chaves dos provedores de LLM que entram na bancada | A3, B1 |
| D2 | Autorizar o Ideraldinho no app de finanças, com uma conta de teste | o teste real do A6 |
| D3 | Domínio para o gateway | B1 |
| D4 | Política de finanças: limite sem repetição, teto por lançamento | a configuração do A6 |
| D5 | Reconexão: implementar o segundo plano que a [D14](decisions.md) descreve, ou assumir o comportamento atual e corrigir o texto | fecha o A5 |

---

## Ordem das próximas sessões

1. **A1, A4 e A5 em paralelo.** Enquanto isso, a medição manual do wake word
   (A9).
2. **A2**, e do A6 as partes (a) e (b), que não dependem uma da outra.
3. **A3 com as chaves, e a escolha do modelo.** A6 (c). A7 com gravações.
4. **Com a VPS:** B1 e B2; a autorização real do app de finanças; os 20
   lançamentos de teste.
5. **A8** conforme o resultado da tradução; o v4 do wake word.

---

## Ideias que cabem aqui

- **A leitura de volta que informa.** Depois de gravar um gasto: "Lançado.
  Sobram trezentos e vinte na meta de restaurantes este mês." O dado já vem do
  resumo do app, e é o que transforma o registro num hábito útil.
- **Lembrete de nível 0.** Está no plano e não existe. É a mesma tabela dos
  timers, com um texto falado. Pequeno, offline, e fecha a substituição da
  Alexa.
- **Frases fixas pré-sintetizadas.** "Timer de dez minutos", "Confirma?", "Não
  consigo falar com o servidor": cache em disco pelo hash do texto. É o que mais
  reduz a latência percebida na Pi.
- **Ligar a emoção do rosto com regra, não com LLM.** O eixo já existe no
  protocolo e no rosto; falta alguém preencher.
- **Histórico que sobrevive à reconexão.** Com VPS e Wi-Fi de verdade a queda
  vira rotina, e uma confirmação pendente não pode sumir em silêncio.

---

## O que ainda é hipótese

- Nada sobre o servidor MCP foi executado: o desenho vem da leitura do código
  dele. Como a autorização se comporta para um cliente sem navegador na VPS só
  se descobre testando.
- O comportamento do Piper lendo valores, e o do Whisper com números falados em
  pt-BR, são hipóteses até a bancada do A7.
- Os preços e a latência dos modelos de nuvem vêm de pesquisa, e só uma parte
  foi conferida na página do próprio provedor. Vale o que a bancada medir.
