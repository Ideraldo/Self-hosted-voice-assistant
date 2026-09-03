---
name: documentar
description: Fecha o dia de trabalho no Ideraldinho-AI atualizando a documentação. Acrescenta uma entrada narrativa ao diário de bordo (docs/diario-de-bordo.md) com o que foi feito, tentado, medido e quebrado; e propaga o que mudou para decisions.md, lab/RESULTS.md e os READMEs. Use quando o usuário pedir para documentar o dia, registrar a sessão, atualizar o diário, ou rodar /documentar.
---

# Documentar o dia de trabalho

Esta skill fecha uma sessão de trabalho do Ideraldinho-AI escrevendo a documentação
correspondente. O objetivo final é o **vídeo** que o usuário vai gravar no fim do
projeto, contando os dias, as decisões, as incertezas e as falhas.

Por isso a peça central é a narrativa, não o changelog. Um resumo de commits não
serve: o que interessa é **o que estava em dúvida antes da decisão** e **o que
deu errado no caminho**.

---

## Os cinco documentos e seus papéis

Nunca misturar. Cada um tem uma função e um tom.

| Arquivo | Papel | Tom | Esta skill escreve? |
|---|---|---|---|
| `docs/ultraplan-v3-assistente-voz-portatil.md` | A especificação | — | **NUNCA.** Ver abaixo |
| `docs/decisions.md` | Decisões fechadas: o quê e o porquê | Seco, numerado (D1, D2…) | Só se uma decisão foi fechada |
| `docs/diario-de-bordo.md` | A história: dúvidas, tentativas, falhas | Narrativo, primeira pessoa | **Sempre** |
| `lab/RESULTS.md` | Números medidos de STT/TTS | Tabelas | Só se houve medição nova |
| `lab/docs/comandos.md` | Como rodar cada script, com os parâmetros | Referência | Só se um comando mudou |

**O ultraplan não se reescreve, em nenhuma hipótese.** Ele registra o que se
sabia quando foi escrito, e essa é justamente a utilidade dele. Se a construção
contradiz o plano, isso vira uma decisão em `decisions.md` — não uma edição no
plano.

---

## Passo a passo

### 1. Levantar o que aconteceu

Antes de escrever qualquer coisa:

- `git log --oneline` desde a última entrada do diário, para ver o que foi feito.
- `git diff --stat` do mesmo intervalo, para ver o tamanho de cada mudança.
- Ler o fim de `docs/diario-de-bordo.md` para saber em que dia parou e não
  repetir o que já está lá.
- Recuperar da conversa atual: o que foi tentado e abandonado, os erros que
  apareceram, os números que saíram nas execuções.

O git conta *o que* mudou. A conversa conta *por quê*, e é a parte que importa.

### 2. Perguntar o que só o usuário sabe

O material mais valioso do diário não está no repositório: está na cabeça do
usuário. Se a conversa atual não deixar claro, **pergunte** — de forma curta e
específica, no máximo duas ou três perguntas:

- O que te deixou em dúvida hoje?
- Alguma coisa que você achou que ia funcionar e não funcionou?
- Alguma decisão que você tomou por instinto e ainda não tem certeza?

Se a conversa já responde tudo isso, não pergunte nada. Nunca invente uma dúvida
ou uma impressão que o usuário não expressou.

### 3. Escrever a entrada do diário

Acrescente uma seção nova ao fim de `docs/diario-de-bordo.md`, antes da seção
"Onde estamos agora".

**Numeração:** continue a sequência existente (Dia 1, Dia 2, Dia 3…). Uma sessão
de trabalho é um dia. Se um dia tiver várias fases longas, use
`## Dia N (continuação)`, como já acontece no arquivo.

**O que registrar, nesta ordem de prioridade:**

1. **O que deu errado e por quê.** É o que ninguém publica e todo mundo passa.
   Inclua a mensagem de erro quando ela for curta e reveladora.
2. **A dúvida antes da decisão.** A decisão sozinha não ensina nada; o que ensina
   é o que estava em jogo e o que quase foi escolhido.
3. **Números medidos**, com contexto e data. Número solto envelhece mal.
4. **O que foi descartado, e o motivo.** Evita refazer o mesmo caminho.

**Como escrever:**

- Português. Caminhos, nomes de arquivo e de comando em inglês, como no código.
- Primeira pessoa quando for ação ou impressão do usuário ("reprovei as duas
  vozes", "perguntei uma coisa que parecia boba"). O diário é dele.
- Blocos de código para saídas reais de terminal — são as melhores citações para
  o vídeo.
- Tabelas para comparações numéricas.
- Sem enfeite. Um erro descrito com precisão vale mais que um adjetivo.

**Nunca:**

- Inventar número que não foi medido, ou arredondar para parecer melhor.
- Reescrever entradas antigas, exceto para corrigir um erro factual — e nesse
  caso, corrigir de fato, sem apagar o que se aprendeu.
- Transformar uma falha em vitória. O bug que quase custou uma noite é o melhor
  conteúdo que o arquivo tem.

### 4. Propagar para os outros documentos

Só o que se aplicar:

- **Decisão fechada** → nova entrada `D{n}` em `docs/decisions.md`, no formato
  existente: data, "o plano diz", "o que mudou", "por quê", "consequências".
  Depois, referencie-a do diário.
- **Medição nova** de STT ou TTS → atualizar a tabela correspondente em
  `lab/RESULTS.md`, incluindo a coluna subjetiva se o usuário deu opinião.
- **Estrutura de pastas mudou** → `docs/repository-structure.md` e o `README.md`
  da raiz.
- **Comando novo ou alterado, ou parâmetro adicionado** → `lab/docs/comandos.md`,
  que é a referência única de como rodar cada coisa, **e** o `README.md` da pasta
  correspondente (`lab/README.md`, `lab/finetune/README.md`), que explica o
  porquê. Verifique o texto contra o `--help` real do script em vez de escrever
  de memória: parâmetro documentado errado custa mais que parâmetro não
  documentado.

Regra geral: se um `.md` descreve algo que mudou hoje, ele entra no mesmo commit.

### 5. Atualizar "Onde estamos agora"

No fim do diário existe uma seção com o estado atual: a tabela do que está
fechado, a lista do que está em aberto, e o que ainda nem começou. Ela é a única
parte do arquivo que **é reescrita** em vez de crescer.

### 6. Commitar

Um commit só, com todos os `.md` tocados. Mensagem em português, corpo
explicando o que a entrada registra. Depois `git push`.

Não commite código junto com a documentação, a não ser que o código tenha sido
escrito nesta mesma invocação da skill.

---

## O que esta skill NÃO faz

Deixar claro para não haver desentendimento:

- **Não escreve código nem corrige bugs.** É só documentação.
- **Não roda testes, treinos ou benchmarks.** Ela registra números que já foram
  medidos; não gera números novos. Se falta um número, diga isso no diário em vez
  de medir por conta própria.
- **Não edita o ultraplan.**
- **Não roda em subagente.** O material bom está nesta conversa — as tentativas,
  os erros, o que o usuário disse. Um agente novo começaria sem nada disso e
  produziria um resumo de commits, que é exatamente o que não serve.
- **Não inventa.** Se não há informação suficiente para uma entrada honesta,
  pergunte ao usuário ou escreva uma entrada curta. Diário curto e verdadeiro é
  melhor que diário longo e inventado.

---

## Nota sobre o vídeo

O usuário vai gravar contando o desenvolvimento. Ao escrever, pense no que
renderia uma boa cena:

- "Testei o treino com uma época antes de deixar rodando a noite. Quatro coisas
  quebravam." → ótimo.
- "Implementei o pipeline de fine-tune." → inútil.

A diferença é sempre a mesma: tensão e consequência, não lista de tarefas.
