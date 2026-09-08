# O rosto

Quatro estados animados — `idle`, `listening`, `thinking`, `speaking` — como uma
página web local. Sobe junto com o dispositivo; não há processo extra para
iniciar.

Decisão relacionada: [D27](decisions.md).

```powershell
.\.venv\Scripts\python.exe -m device.main --text --rosto   # abre o navegador também
```

A URL é impressa no boot (`http://127.0.0.1:8080/` por padrão). Dois parâmetros
ajudam enquanto se desenha:

- `?debug` mostra estado e emoção atuais no rodapé
- `?demo` percorre todos os estados e emoções sem dispositivo nenhum rodando

Para iterar no rosto sem carregar o Whisper e falar uma frase a cada ajuste de
CSS, dirija pelo teclado:

```powershell
.\.venv\Scripts\python.exe -m scripts.rosto
> listening
> happy
```

| Variável | Padrão | O que faz |
|---|---|---|
| `FACE_PORT` | `8080` | porta da página e do WebSocket dela (a mesma) |
| `FACE_ENABLED` | `1` | `0` sobe o dispositivo sem rosto nenhum |
| `FACE_THEME` | `minimo` | qual rosto o `/` serve: `minimo` ou `anime` |
| `DISPLAY_MODE` | `window` | `kiosk` na Pi, quando houver Pi |

---

## Dois rostos, um protocolo

`minimo` são dois retângulos arredondados; `anime` são olhos amendoados com
íris, cílios, sobrancelhas e boca. Os dois são servidos pelo mesmo servidor,
dirigidos pelo mesmo `face.js`, alimentados pelo mesmo `{state, emotion}` — o
tema é a camada de desenho e nada mais. Qualquer um dos dois é alcançável pelo
nome (`/minimo.html`, `/anime.html`), então comparar os dois não exige restart.

## Por que uma página web, e o que isso custa

A tela não vai continuar sendo só um rosto — transcrição, timer correndo, capa do
álbum —, e isso é interface, onde HTML é barato e canvas não é.

O custo é um navegador disputando os mesmos quatro núcleos com o
`faster-whisper`. A mitigação é uma regra, não uma esperança: **só `transform` e
`opacity` animam**, para o trabalho ficar na GPU. Sem `<canvas>`, sem
`requestAnimationFrame`.

O rosto é um assinante de `StateMachine.transition()`, e é isso que torna o
renderizador trocável: trocar o Chromium por um app nativo depois reescreve um
assinante, não a arquitetura. E ele nunca pode derrubar o dispositivo — um
assinante que levanta exceção vira linha de log, um servidor que não sobe devolve
`False` e o turno continua. A tela é vitrine; o timer é função.

**Emoção é um segundo eixo, não um quinto estado.** `Emotion` está declarado no
protocolo como campo opcional e nada envia ainda: encaixar um eixo novo no
formato do fio depois é caro, um campo opcional vazio não é.

## O critério de aceite mudou

"Não engasga durante o áudio" não mede nada — o rosto pode estar liso e ainda
assim roubar o núcleo do STT. O critério agora é **o rosto não pode piorar o RTF
do STT**, medido na Pi, com a página aberta contra fechada.

Nada disto foi medido numa Pi: não há Pi.
