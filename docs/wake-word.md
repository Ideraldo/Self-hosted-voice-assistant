# Wake word

O dispositivo pode esperar pelo próprio nome em vez de escutar tudo. Ele vem
**desligado** (`WAKE_ENABLED=1` liga), e o `WAKE_MODEL` padrão continua sendo
`hey_jarvis`, um dos que acompanham o openWakeWord.

O modelo do "Ideraldinho" existe — o v3, treinado aqui (D31). O padrão não
aponta para ele porque ele é um binário fora do git (`lab/models/`,
[D12](decisions.md)): um clone novo não tem o arquivo, e um padrão que aponta
para um arquivo ausente não carrega. Ligar o wake word sem trocar o modelo faz o
aparelho atender por "hey jarvis", o que é pior que não ter wake word nenhum —
serve para testar o encanamento e mais nada.

Decisões relacionadas: [D30](decisions.md), [D31](decisions.md). O detalhe do
treino está em [`lab/wakeword/README.md`](../lab/wakeword/README.md).

---

## Configuração

| Variável | Padrão | O que faz |
|---|---|---|
| `WAKE_ENABLED` | `0` | `1` faz o dispositivo esperar o nome antes de escutar |
| `WAKE_MODEL` | `hey_jarvis` | um nome que vem junto, ou o caminho do seu `.onnx` |
| `WAKE_THRESHOLD` | `0.5` | o número que nenhum código escolhe por você — ver abaixo |

Para usar o modelo treinado, gere-o com os três comandos da seção de treino
abaixo (ele sai em `lab/models/wakeword/ideraldinho.onnx`) e aponte o `.env`
para ele:

```
WAKE_ENABLED=1
WAKE_MODEL=lab/models/wakeword/ideraldinho.onnx
WAKE_THRESHOLD=0.9
```

O `0.9` é o ponto de partida do `lab/wakeword/README.md`, não um veredito — ver
o limiar, abaixo.

**Custo, medido nesta máquina (03/09/2026):** 1,0 ms por frame de 30 ms, RTF
**0,033**, pelo caminho real do dispositivo. A folga para uma Pi cinco vezes mais
lenta é aritmética, não medição.

**O limiar não é constante de código.** Ele depende do microfone, da distância e
do cômodo. Isto mostra o score ao vivo e conta ativações por hora, que é também
como o critério de aceite da fase é medido:

```powershell
.\.venv\Scripts\python.exe -m scripts.wake                  # achar o limiar
.\.venv\Scripts\python.exe -m scripts.wake --segundos 3600  # falso positivo/hora
```

---

### O único momento em que ele ouve sem o nome

Quando o aparelho faz uma pergunta, ele ouve a resposta por `REPLY_WINDOW_S`
segundos (padrão 8) sem esperar o wake word — é o que permite responder "o
timer" a "qual eu cancelo?" sem repetir "Ideraldinho". Fora disso, nada muda.

O que ainda não foi medido, porque exige microfone e sala: se a cauda da
própria fala entra na janela, e o quanto uma TV ligada nesses 8 segundos vira
"resposta". O valor é um ponto de partida, não uma calibração.

## Treinar "Ideraldinho" sem baixar a internet

A receita oficial do openWakeWord quer dezenas de milhares de positivos a partir
de um checkpoint LibriTTS **em inglês**, mais dezenas de GB de negativos. Este
repositório já tinha coisa melhor: sete vozes Piper em pt-BR, as épocas do
fine-tune da voz do dono, e as 315 gravações reais do dataset daquele fine-tune.

```powershell
.\.venv\Scripts\python.exe -m lab.wakeword.gerar     # ~1200 positivos, ~2000 negativos
.\.venv\Scripts\python.exe -m lab.wakeword.treinar   # embutir, treinar, exportar .onnx
.\.venv\Scripts\python.exe -m lab.wakeword.medir     # falso positivo por HORA
```

Duas razões para o caminho local ganhar, e tamanho não é nenhuma delas.

**Pronúncia:** um TTS em inglês diria "Ideraldinho" com fonemas ingleses e o
modelo aprenderia a palavra errada — treinaria bem, mediria bem, e falharia só no
cômodo.

**Negativos:** 315 gravações do dono dizendo outras coisas são o negativo mais
difícil que existe para este caso, e o único que importa, porque é essa a voz
perto do microfone o dia inteiro.

Só o classificador é treinado; o extrator de embedding fica congelado e é o mesmo
dos modelos que vêm prontos. É por isso que 3 mil exemplos fazem o trabalho das
dezenas de milhares da receita.

---

## Onde parou

Acorda em 94,1% das chamadas, e **zero falsos positivos em 44 minutos** de fala
comum e ruído.

Isso não é a mesma coisa que "abaixo de 1 por hora": zero eventos em 44 minutos
só limita a taxa em cerca de 4 por hora, e o dataset não tem ruído de casa real
dentro dele. O número que decide vem do microfone.

## Os dois caminhos errados, que valem a leitura

Nenhum dos dois era problema de ajuste.

**A v1 aprendeu `-aldinho`, não `Ideraldinho`.** Disparava em "Everaldinho" 83%
das vezes e em "Ideraldo", o nome do próprio dono, nunca.

**Uma palavra "adversarial" do treino era idêntica ao alvo.** "Ideraudinho" tem a
mesma transcrição fonética do wake word (`ˌideɾaʊdʒˈiɲʊ` para as duas): a lista
tinha sido escrita olhando para letras, e o modelo ouve fonemas.
`conferir_fonemas()` agora recusa uma lista dessas antes de gerar qualquer coisa.
