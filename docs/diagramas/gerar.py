"""Desenha os diagramas de arquitetura que usam icones, e grava em docs/img/.

    python docs/diagramas/gerar.py

Por que um script e nao Mermaid: o Mermaid do GitHub so desenha caixas, e um
diagrama de arquitetura se le mais rapido quando o Docker parece o Docker e o
Spotify parece o Spotify. Por que um script e nao uma ferramenta de desenho: o
resultado e um SVG de texto, que cabe num diff, e mudar um rotulo e mudar uma
linha aqui. So usa a biblioteca padrao.

Os icones em `icones/` vem de dois conjuntos abertos: os `marca-*.svg` sao do
Simple Icons (CC0), os demais do Lucide (licenca ISC). As marcas continuam
sendo de quem sao; aqui elas so identificam o servico. Por isso logotipo so
entra em cima do proprio produto: o que e codigo deste repositorio, ou ainda
nao tem fornecedor escolhido (o LLM de nuvem), leva icone neutro do Lucide.

Como o arquivo se organiza: os ajudantes de desenho primeiro, depois uma funcao
por figura com os textos dela logo acima, e `FIGURAS` no fim dizendo o que
`main` grava. Cada figura sai em portugues e em ingles.

Nao ha medicao de texto: quem escreve um rotulo e quem garante que ele cabe.
A conta de bolso e 6,4 px por caractere no corpo 12 e 8,5 px no titulo de
cartao (corpo 15, peso 600). Mudou um texto, rode o script e olhe o SVG.

A convencao de cores e tracos e a mesma em todas as figuras:

- seta preta: chamada local, ou HTTP comum;
- seta azul grossa: o WebSocket entre os dois processos, o unico fio que cruza
  a rede;
- roxo: o que fala Model Context Protocol;
- tracejado: ainda nao existe, ou existe e nao esta ligado de ponta a ponta.
"""

from __future__ import annotations

import re
from pathlib import Path
from xml.sax.saxutils import escape

AQUI = Path(__file__).resolve().parent
ICONES = AQUI / "icones"
SAIDA = AQUI.parent / "img"

FONTE = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
TINTA = "#1f2328"
CINZA = "#59636e"
BORDA = "#d1d9e0"
ZONA = "#f6f8fa"
REDE = "#0969da"       # o que cruza a rede
MCP = "#8250df"        # o que fala Model Context Protocol
LOCAL = "#1f2328"      # o que nunca sai do aparelho

MARCAS = {
    "raspberrypi": "#A22846",
    "docker": "#2496ED",
    "fastapi": "#009688",
    "ollama": "#1f2328",
    "spotify": "#1DB954",
    "duckduckgo": "#DE5833",
    "modelcontextprotocol": "#8250df",
    "python": "#3776AB",
    "sqlite": "#003B57",
}

# Altura de um cartao e a metade dela: quase toda seta sai do meio de um lado.
H = 74
M = H // 2


# --------------------------------------------------------------------------
# Ajudantes de desenho
# --------------------------------------------------------------------------


def icone(nome: str, x: float, y: float, lado: float, cor: str = TINTA) -> str:
    """O miolo de um SVG de `icones/`, reposicionado e colorido."""
    marca = nome in MARCAS
    bruto = (ICONES / (f"marca-{nome}.svg" if marca else f"{nome}.svg")).read_text(encoding="utf-8")
    miolo = re.search(r"<svg[^>]*>(.*)</svg>", bruto, flags=re.S).group(1)
    miolo = re.sub(r"<title>.*?</title>", "", miolo, flags=re.S).strip()
    # Alguns icones do Lucide preenchem um ponto com `currentColor`, que num
    # SVG embutido como <img> e sempre preto: troca pela cor pedida.
    miolo = miolo.replace("currentColor", cor)
    escala = lado / 24
    pintura = (
        f'fill="{MARCAS[nome]}"'
        if marca
        else f'fill="none" stroke="{cor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
    )
    return f'<g transform="translate({x},{y}) scale({escala})" {pintura}>{miolo}</g>'


def texto(x, y, s, tam=13, cor=TINTA, peso=400, ancora="start") -> str:
    return (
        f'<text x="{x}" y="{y}" font-size="{tam}" fill="{cor}" font-weight="{peso}" '
        f'text-anchor="{ancora}">{escape(s)}</text>'
    )


def abrir(L, A, alt) -> list[str]:
    """O comeco de toda figura: as pontas de seta e a tela branca arredondada.

    A tela e branca de proposito, e nao transparente: o GitHub mostra o <img>
    em cima da pagina, que no tema escuro e quase preta, e texto escuro sem
    fundo proprio sumiria.
    """
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {A}" width="{L}" height="{A}" '
        f'font-family="{FONTE}" role="img" aria-label="{escape(alt)}">',
        "<defs>",
        f'<marker id="ponta-local" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LOCAL}"/></marker>',
        f'<marker id="ponta-rede" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{REDE}"/></marker>',
        f'<marker id="ponta-mcp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MCP}"/></marker>',
        "</defs>",
        f'<rect x="0.75" y="0.75" width="{L - 1.5}" height="{A - 1.5}" rx="16" fill="#ffffff" stroke="{BORDA}" stroke-width="1.5"/>',
    ]


def cartao(x, y, w, nome_icone, titulo, sub, h=H, tracejado=False) -> str:
    traco = ' stroke-dasharray="6 4"' if tracejado else ""
    return "".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#ffffff" stroke="{CINZA if tracejado else BORDA}" stroke-width="1.5"{traco}/>',
            f'<rect x="{x + 14}" y="{y + 17}" width="40" height="40" rx="9" fill="{ZONA}" stroke="{BORDA}"/>',
            icone(nome_icone, x + 22, y + 25, 24),
            texto(x + 66, y + 33, titulo, 15, peso=600),
            texto(x + 66, y + 52, sub, 12, CINZA),
        ]
    )


def zona(x, y, w, h, nome_icone, titulo, sub, tracejada=False) -> str:
    borda = f'stroke="{CINZA}" stroke-width="1.5" stroke-dasharray="6 4"' if tracejada else f'stroke="{BORDA}" stroke-width="1.5"'
    return "".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{ZONA}" {borda}/>',
            icone(nome_icone, x + 20, y + 17, 22),
            texto(x + 52, y + 27, titulo, 16, peso=700),
            texto(x + 52, y + 45, sub, 12, CINZA),
        ]
    )


def seta(pontos, cor=LOCAL, grossura=1.75, tracejada=False, dupla=False) -> str:
    """Linha em cotovelos, com cantos arredondados e ponta no fim.

    `dupla` poe ponta tambem no comeco: leitura e escrita no mesmo fio.
    """
    r = 8
    d = f"M{pontos[0][0]},{pontos[0][1]}"
    for i in range(1, len(pontos) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pontos[i - 1], pontos[i], pontos[i + 1]
        ax = x1 - r * ((x1 > x0) - (x1 < x0)); ay = y1 - r * ((y1 > y0) - (y1 < y0))
        bx = x1 + r * ((x2 > x1) - (x2 < x1)); by = y1 + r * ((y2 > y1) - (y2 < y1))
        d += f" L{ax},{ay} Q{x1},{y1} {bx},{by}"
    d += f" L{pontos[-1][0]},{pontos[-1][1]}"
    ponta = {REDE: "rede", MCP: "mcp"}.get(cor, "local")
    traco = ' stroke-dasharray="6 4"' if tracejada else ""
    comeco = f' marker-start="url(#ponta-{ponta})"' if dupla else ""
    return f'<path d="{d}" fill="none" stroke="{cor}" stroke-width="{grossura}"{traco}{comeco} marker-end="url(#ponta-{ponta})"/>'


def rotulo(x, y, s, cor=CINZA, ancora="middle") -> str:
    largura = len(s) * 6.4 + 14
    x0 = x - largura / 2 if ancora == "middle" else x - 7
    return (
        f'<rect x="{x0}" y="{y - 14}" width="{largura}" height="20" rx="6" fill="#ffffff"/>'
        + texto(x if ancora == "middle" else x, y, s, 12, cor, 500, ancora)
    )


def circulo(x, y, nome_icone, legenda) -> str:
    return "".join(
        [
            f'<circle cx="{x}" cy="{y}" r="26" fill="#ffffff" stroke="{BORDA}" stroke-width="1.5"/>',
            icone(nome_icone, x - 12, y - 12, 24),
            texto(x, y + 46, legenda, 12, CINZA, 500, "middle"),
        ]
    )


def selo(x, y, w, nome_icone, s, cor, fundo) -> str:
    """Etiqueta pequena montada na borda de um cartao, onde o olho bate primeiro."""
    return "\n".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="12" fill="{fundo}" stroke="{cor}" stroke-width="1.5"/>',
            icone(nome_icone, x + 9, y + 5.5, 13, cor),
            texto(x + 27, y + 16.5, s, 12, cor, 700),
        ]
    )


def pilula_ws(cx, cy, linha1, linha2) -> str:
    """A pilula do WebSocket. Vai por cima das duas setas azuis, no vao entre os processos."""
    return "\n".join(
        [
            f'<rect x="{cx - 55}" y="{cy - 26}" width="110" height="52" rx="26" fill="#ddf4ff" stroke="{REDE}" stroke-width="1.5"/>',
            texto(cx, cy - 4, linha1, 13, REDE, 700, "middle"),
            texto(cx, cy + 13, linha2, 11.5, REDE, 500, "middle"),
        ]
    )


def linhas(x, y, textos, cor=CINZA, ancora="middle", passo=16, peso=500) -> str:
    """Varias linhas de texto pequeno, uma embaixo da outra."""
    return "".join(texto(x, y + i * passo, s, 12, cor, peso, ancora) for i, s in enumerate(textos))


def legenda(x, y, cor, grossura, tracejada, s) -> str:
    """Uma entrada de legenda: a amostra do traco e o que ele quer dizer."""
    return seta([(x, y - 4), (x + 34, y - 4)], cor, grossura, tracejada) + texto(x + 44, y, s, 12, CINZA, 500)


# --------------------------------------------------------------------------
# Figura 1: alto nivel
# --------------------------------------------------------------------------

ALTO_NIVEL = {
    "pt": {
        "voz": "Voz",
        "som": "Alto-falante",
        "dispositivo": "Dispositivo",
        "dispositivo_sub": "em casa · funciona offline · PC hoje, Raspberry Pi depois",
        "gateway": "Gateway",
        "gateway_sub": "PC hoje · contêiner na VPS depois",
        "ouvir": ("Ouvir", "wake word · VAD · STT"),
        "nivel0": ("Nível 0, local", "timers · alarmes · hora"),
        "falar": ("Falar", "Piper · voz própria"),
        "rosto": ("Rosto", "página web"),
        "sessao": ("Sessão", "conversa · chamadas ao LLM"),
        "ferramentas": ("Ferramentas", "música · busca · agenda"),
        "financas": ("App de finanças", "servidor MCP · em breve"),
        "llm": ("LLM", "Ollama hoje · nuvem depois"),
        "spotify": ("Spotify", "Web API"),
        "busca": ("Busca na internet", "DuckDuckGo · Brave"),
        "conhecido": "comando conhecido",
        "sobe": "todo o resto, em texto",
        "desce": "resposta, em texto",
        "ws": ("WebSocket", "só texto"),
        "fora": "Serviços externos",
        "alt": "Arquitetura do Ideraldinho em alto nível",
    },
    "en": {
        "voz": "Voice",
        "som": "Speaker",
        "dispositivo": "Device",
        "dispositivo_sub": "at home · works offline · PC today, Raspberry Pi next",
        "gateway": "Gateway",
        "gateway_sub": "PC today · container on a VPS next",
        "ouvir": ("Listen", "wake word · VAD · STT"),
        "nivel0": ("Local skills", "timers · alarms · clock"),
        "falar": ("Speak", "Piper · own voice"),
        "rosto": ("Face", "web page"),
        "sessao": ("Session", "conversation · LLM calls"),
        "ferramentas": ("Tools", "music · search · schedules"),
        "financas": ("Finance app", "MCP server · coming soon"),
        "llm": ("LLM", "Ollama today · cloud next"),
        "spotify": ("Spotify", "Web API"),
        "busca": ("Web search", "DuckDuckGo · Brave"),
        "conhecido": "known command",
        "sobe": "everything else, as text",
        "desce": "answer, as text",
        "ws": ("WebSocket", "text only"),
        "fora": "External services",
        "alt": "Ideraldinho high-level architecture",
    },
}


def alto_nivel(lingua: str) -> str:
    t = ALTO_NIVEL[lingua]
    L, A = 1360, 500
    p = [
        *abrir(L, A, t["alt"]),
        # zonas
        zona(150, 30, 460, 440, "raspberrypi", t["dispositivo"], t["dispositivo_sub"]),
        zona(760, 30, 280, 440, "docker", t["gateway"], t["gateway_sub"]),
        texto(1212, 57, t["fora"], 12, CINZA, 600, "middle"),
        # o que cruza a rede (por baixo dos cartoes e da pilula)
        seta([(385, 125), (780, 125)], REDE, 2.5),
        seta([(780, 150), (685, 150), (685, 387), (385, 387)], REDE, 2.5),
        rotulo(505, 119, t["sobe"], REDE),
        rotulo(530, 381, t["desce"], REDE),
        # a pilula do WebSocket, no vao entre os dois processos
        pilula_ws(685, 138, *t["ws"]),
        # dispositivo
        cartao(170, 100, 215, "mic", *t["ouvir"]),
        cartao(170, 225, 215, "alarm-clock", *t["nivel0"]),
        cartao(170, 350, 215, "volume-2", *t["falar"]),
        cartao(410, 225, 180, "smile", *t["rosto"]),
        seta([(277, 174), (277, 225)]),
        seta([(277, 299), (277, 350)]),
        texto(289, 204, t["conhecido"], 12, CINZA, 500),
        # gateway
        cartao(780, 100, 240, "fastapi", *t["sessao"]),
        cartao(780, 296, 240, "wrench", *t["ferramentas"]),
        seta([(900, 174), (900, 296)]),
        # fora
        cartao(1095, 100, 235, "ollama", *t["llm"]),
        cartao(1095, 210, 235, "spotify", *t["spotify"]),
        cartao(1095, 296, 235, "duckduckgo", *t["busca"]),
        seta([(1020, 137), (1095, 137)]),
        seta([(1020, 318), (1050, 318), (1050, 247), (1095, 247)]),
        seta([(1020, 333), (1095, 333)]),
        # o MCP: a unica ligacao que ainda nao esta de ponta a ponta, por isso tracejada
        seta([(1020, 348), (1050, 348), (1050, 419), (1095, 419)], MCP, 2.25, tracejada=True),
        cartao(1095, 382, 235, "wallet", *t["financas"], tracejado=True),
        selo(1252, 370, 66, "modelcontextprotocol", "MCP", MCP, "#fbefff"),
        # entrada e saida
        circulo(70, 137, "user", t["voz"]),
        circulo(70, 387, "volume-2", t["som"]),
        seta([(96, 137), (170, 137)]),
        seta([(170, 387), (96, 387)]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------
# Figura 2: o processo do dispositivo, modulo por modulo
# --------------------------------------------------------------------------

# O subtitulo de cada cartao e o caminho do arquivo, relativo a `device/`.
DISPOSITIVO = {
    "pt": {
        "alt": "Os módulos do processo do dispositivo e como se ligam",
        "zona": "Processo 1 · dispositivo",
        "zona_sub": "python -m device.main · caminhos relativos a device/ · tudo aqui funciona sem rede",
        "voz": "Voz",
        "som": "Alto-falante",
        "mic": ("Microfone + VAD", "audio/capture.py"),
        "wake": ("Wake word", "activation/wake.py"),
        "stt": ("STT faster-whisper", "stt/faster_whisper.py"),
        "roteador": ("Roteador nível 0", "router/intents.py"),
        "servicos": ("Serviços locais", "local/service.py"),
        "sqlite": ("SQLite", "local/store.py"),
        "agendador": ("Agendador", "local/scheduler.py"),
        "cliente": ("GatewayClient", "ws_client.py"),
        "tts": ("TTS Piper", "tts/piper_voice.py"),
        "saida": ("Alto-falante", "audio/playback.py"),
        "estados": ("Máquina de estados", "state.py"),
        "rosto": ("Servidor do rosto", "rosto/server.py"),
        "pagina": ("Rosto", "no navegador"),
        "gateway": ("Gateway", "api/session.py"),
        "gateway_nota": "o outro processo",
        "atalho": "sem wake word (o padrão), ou por 8 s depois de uma pergunta",
        "casou": "casou",
        "nao_casou": "não casou",
        "venceu": "venceu",
        "frase": "frase a falar",
        "frases": "frases da resposta",
        "ws_local": "WebSocket local · state + emotion",
        "ws": ("WebSocket", "só texto"),
        "sobe": ["session_start", "utterance · tool_result"],
        "desce": ["state · transcript", "tool_call · error"],
    },
    "en": {
        "alt": "The modules of the device process and how they connect",
        "zona": "Process 1 · device",
        "zona_sub": "python -m device.main · paths relative to device/ · all of it works with no network",
        "voz": "Voice",
        "som": "Speaker",
        "mic": ("Microphone + VAD", "audio/capture.py"),
        "wake": ("Wake word", "activation/wake.py"),
        "stt": ("STT faster-whisper", "stt/faster_whisper.py"),
        "roteador": ("Level-0 router", "router/intents.py"),
        "servicos": ("Local services", "local/service.py"),
        "sqlite": ("SQLite", "local/store.py"),
        "agendador": ("Scheduler", "local/scheduler.py"),
        "cliente": ("GatewayClient", "ws_client.py"),
        "tts": ("TTS Piper", "tts/piper_voice.py"),
        "saida": ("Speaker", "audio/playback.py"),
        "estados": ("State machine", "state.py"),
        "rosto": ("Face server", "rosto/server.py"),
        "pagina": ("Face", "in the browser"),
        "gateway": ("Gateway", "api/session.py"),
        "gateway_nota": "the other process",
        "atalho": "no wake word (the default), or for 8 s after a question",
        "casou": "match",
        "nao_casou": "no match",
        "venceu": "due",
        "frase": "sentence to speak",
        "frases": "reply sentences",
        "ws_local": "local WebSocket · state + emotion",
        "ws": ("WebSocket", "text only"),
        "sobe": ["session_start", "utterance · tool_result"],
        "desce": ["state · transcript", "tool_call · error"],
    },
}


def dispositivo(lingua: str) -> str:
    t = DISPOSITIVO[lingua]
    # Uma grade de 3 colunas por 5 linhas dentro da zona. O vao de 60 px entre
    # colunas e o que deixa uma seta horizontal ter ponta e um rotulo curto.
    w = 225
    c1, c2, c3 = 140, 425, 710
    r1, r2, r3, r4, r5 = 130, 255, 380, 505, 630
    zx, zw = 120, 835                    # a zona vai de 120 a 955
    gx, gw = 1125, 165                   # a coluna de fora: gateway e navegador
    L, A = gx + gw + 20, r5 + H + 50
    meio = (zx + zw + gx) / 2            # o vao onde mora a pilula
    p = [
        *abrir(L, A, t["alt"]),
        zona(zx, 30, zw, A - 60, "python", t["zona"], t["zona_sub"]),
        # o WebSocket: as duas setas azuis sao o unico fio para fora do processo
        seta([(c3 + w, r3 + 25), (gx, r3 + 25)], REDE, 2.5),
        seta([(gx, r3 + 50), (c3 + w, r3 + 50)], REDE, 2.5),
        pilula_ws(meio, r3 + M, *t["ws"]),
        linhas(meio, r3 - 15, t["sobe"], REDE),
        linhas(meio, r3 + 82, t["desce"], REDE),
        # o atalho por cima do wake word: e o caminho padrao (WAKE_ENABLED=0) e
        # tambem a janela de continuacao, que reabre o microfone sem o nome
        seta([(c1 + 112, r1), (c1 + 112, 104), (c3 + 112, 104), (c3 + 112, r1)]),
        rotulo((c1 + c3) / 2 + 112, 108, t["atalho"]),
        # linha 1: ouvir
        cartao(c1, r1, w, "mic", *t["mic"]),
        cartao(c2, r1, w, "ear", *t["wake"]),
        cartao(c3, r1, w, "captions", *t["stt"]),
        seta([(c1 + w, r1 + M), (c2, r1 + M)]),
        seta([(c2 + w, r1 + M), (c3, r1 + M)]),
        # linha 2: decidir, e resolver em casa o que der
        cartao(c1, r2, w, "sqlite", *t["sqlite"]),
        cartao(c2, r2, w, "alarm-clock", *t["servicos"]),
        cartao(c3, r2, w, "split", *t["roteador"]),
        seta([(c3 + 112, r1 + H), (c3 + 112, r2)]),
        seta([(c3, r2 + 25), (c2 + w, r2 + 25)]),
        texto(c3 - 30, r2 + 17, t["casou"], 12, CINZA, 500, "middle"),
        seta([(c2, r2 + M), (c1 + w, r2 + M)], dupla=True),
        # linha 3: o agendador e a porta para o gateway
        cartao(c1, r3, w, "calendar-clock", *t["agendador"]),
        cartao(c3, r3, w, "plug", *t["cliente"]),
        seta([(c1 + 112, r2 + H), (c1 + 112, r3)], dupla=True),
        seta([(c3 + 112, r2 + H), (c3 + 112, r3)]),
        texto(c3 + 124, r2 + H + 30, t["nao_casou"], 12, CINZA, 500),
        # o tool_call volta pelo mesmo fio e termina no mesmo codigo do regex (D18)
        seta([(c3, r3 + M), (c3 - 30, r3 + M), (c3 - 30, r2 + 52), (c2 + w, r2 + 52)]),
        texto(c3 - 40, r2 + H + 30, "tool_call", 12, CINZA, 500, "end"),
        # linha 4: falar. Tres origens, um Piper so.
        cartao(c1, r4, w, "volume-2", *t["saida"]),
        cartao(c2, r4, w, "audio-lines", *t["tts"]),
        seta([(c1 + w, r3 + M), (c2 + 50, r3 + M), (c2 + 50, r4)]),
        texto(c1 + w + 30, r3 + M - 8, t["venceu"], 12, CINZA, 500, "middle"),
        seta([(c2 + 130, r2 + H), (c2 + 130, r4)]),
        texto(c2 + 142, r3 + M + 4, t["frase"], 12, CINZA, 500),
        seta([(c3 + 112, r3 + H), (c3 + 112, r4 + M), (c2 + w, r4 + M)]),
        rotulo(c2 + w + 97, r4 + M + 4, t["frases"]),
        seta([(c2, r4 + M), (c1 + w, r4 + M)]),
        # linha 5: o rosto assina a maquina de estados; nao esta no caminho do turno
        cartao(c1, r5, w, "workflow", *t["estados"]),
        cartao(c2, r5, w, "smile", *t["rosto"]),
        seta([(c1 + w, r5 + M), (c2, r5 + M)]),
        seta([(c2 + w, r5 + M), (gx, r5 + M)]),
        rotulo(c3 + 112, r5 + M + 4, t["ws_local"]),
        # fora do processo
        cartao(gx, r3, gw, "fastapi", *t["gateway"]),
        texto(gx + gw / 2, r3 + H + 20, t["gateway_nota"], 12, CINZA, 500, "middle"),
        cartao(gx, r5, gw, "app-window", *t["pagina"]),
        # entrada e saida
        circulo(60, r1 + M, "user", t["voz"]),
        circulo(60, r4 + M, "volume-2", t["som"]),
        seta([(86, r1 + M), (c1, r1 + M)]),
        seta([(c1, r4 + M), (86, r4 + M)]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------
# Figura 3: o processo do gateway, modulo por modulo
# --------------------------------------------------------------------------

# O subtitulo de cada cartao e o caminho do arquivo, relativo a `gateway/`.
GATEWAY = {
    "pt": {
        "alt": "Os módulos do processo do gateway e os serviços que ele alcança",
        "zona": "Processo 2 · gateway",
        "zona_sub": "uvicorn gateway.main:app · caminhos relativos a gateway/ · os segredos moram aqui",
        "dispositivo": "Dispositivo",
        "ws": ("WebSocket", "só texto"),
        "sobe": ["session_start", "utterance · tool_result"],
        "desce": ["state · transcript", "tool_call · error"],
        "sessao": ("/ws + Session", "main.py · api/session.py"),
        "conversa": ("Conversation", "conversation/history.py"),
        "tempos": ("Tempos do turno", "timing.py"),
        "provedor": ("LLM + reserva", "llm/base.py · fallback.py"),
        "nuvem": ("Cliente de nuvem", "llm/openai_compat.py"),
        "ollama": ("Cliente Ollama", "llm/ollama.py"),
        "agenda": ("Agenda: só declara", "tools/device_tools.py"),
        "musica": ("Música", "tools/spotify.py"),
        "busca": ("Busca", "tools/search.py"),
        "dinheiro": ("Dinheiro falado", "tools/dinheiro.py"),
        "mcp": ("Cliente MCP", "tools/mcp_client.py"),
        "grupo": "Gastos por voz · escritos e testados, ainda não ligados à Session",
        "token": "gateway/data/",
        "fora": "Fora do processo",
        "x_nuvem": ("LLM na nuvem", "API · a escolher"),
        "x_ollama": ("Ollama", "qwen3:8b · local hoje"),
        "x_spotify": ("Spotify", "Web API"),
        "x_busca": ("Busca na internet", "DuckDuckGo · Brave"),
        "x_financas": ("App de finanças", "servidor MCP"),
        "leg_rede": "cruza a rede",
        "leg_falta": "ainda não ligado",
        "leg_mcp": "MCP, ainda não ligado",
    },
    "en": {
        "alt": "The modules of the gateway process and the services it reaches",
        "zona": "Process 2 · gateway",
        "zona_sub": "uvicorn gateway.main:app · paths relative to gateway/ · the secrets live here",
        "dispositivo": "Device",
        "ws": ("WebSocket", "text only"),
        "sobe": ["session_start", "utterance · tool_result"],
        "desce": ["state · transcript", "tool_call · error"],
        "sessao": ("/ws + Session", "main.py · api/session.py"),
        "conversa": ("Conversation", "conversation/history.py"),
        "tempos": ("Turn timings", "timing.py"),
        "provedor": ("LLM + fallback", "llm/base.py · fallback.py"),
        "nuvem": ("Cloud client", "llm/openai_compat.py"),
        "ollama": ("Ollama client", "llm/ollama.py"),
        "agenda": ("Schedules: declared", "tools/device_tools.py"),
        "musica": ("Music", "tools/spotify.py"),
        "busca": ("Search", "tools/search.py"),
        "dinheiro": ("Spoken money", "tools/dinheiro.py"),
        "mcp": ("MCP client", "tools/mcp_client.py"),
        "grupo": "Expenses by voice · written and tested, not wired into Session yet",
        "token": "gateway/data/",
        "fora": "Outside the process",
        "x_nuvem": ("Cloud LLM", "API · not chosen yet"),
        "x_ollama": ("Ollama", "qwen3:8b · local today"),
        "x_spotify": ("Spotify", "Web API"),
        "x_busca": ("Web search", "DuckDuckGo · Brave"),
        "x_financas": ("Finance app", "MCP server"),
        "leg_rede": "crosses the network",
        "leg_falta": "not wired yet",
        "leg_mcp": "MCP, not wired yet",
    },
}


def gateway(lingua: str) -> str:
    t = GATEWAY[lingua]
    # Duas colunas dentro da zona: a esquerda o que a Session usa sem sair do
    # processo, a direita o que fala com alguem de fora -- e cada cartao da
    # direita fica na mesma linha do servico que alcanca, para a seta ser reta.
    w = 235
    ca, cb, cx = 290, 585, 920
    r1, r2, r3, r4, r5, r6 = 100, 210, 320, 430, 540, 662
    zx, zw = 270, 570                    # a zona vai de 270 a 840
    tronco = (ca + w + cb) / 2           # o vao entre as colunas, por onde desce a seta das ferramentas
    L, A = cx + w + 20, r6 + H + 58
    ws = (86 + zx) / 2
    p = [
        *abrir(L, A, t["alt"]),
        zona(zx, 30, zw, A - 60, "fastapi", t["zona"], t["zona_sub"]),
        texto(cx + w / 2, 57, t["fora"], 12, CINZA, 600, "middle"),
        # o WebSocket
        seta([(84, r2 + 25), (ca, r2 + 25)], REDE, 2.5),
        seta([(ca, r2 + 50), (84, r2 + 50)], REDE, 2.5),
        pilula_ws(ws, r2 + M, *t["ws"]),
        linhas(ws, r2 - 15, t["sobe"], REDE),
        linhas(ws, r2 + 82, t["desce"], REDE),
        circulo(60, r2 + M, "mic", t["dispositivo"]),
        # o grupo dos gastos por voz: existe, tem teste, e a Session ainda nao o chama
        f'<rect x="{ca - 12}" y="{r6 - 32}" width="{cb + w - ca + 24}" height="{H + 44}" rx="12" fill="#ffffff" fill-opacity="0.6" stroke="{MCP}" stroke-width="1.5" stroke-dasharray="6 4"/>',
        texto(ca, r6 - 12, t["grupo"], 12, MCP, 600),
        # a Session e o que ela usa em casa
        cartao(ca, r1, w, "history", *t["conversa"]),
        cartao(ca, r2, w, "messages-square", *t["sessao"]),
        cartao(ca, r3, w, "timer", *t["tempos"]),
        seta([(ca + 117, r2), (ca + 117, r1 + H)]),
        seta([(ca + 117, r2 + H), (ca + 117, r3)]),
        # o LLM: um provedor, ou dois em fila quando ha reserva configurado
        cartao(cb, r1, w, "cloud-cog", *t["nuvem"]),
        cartao(cb, r2, w, "split", *t["provedor"]),
        cartao(cb, r3, w, "cpu", *t["ollama"]),
        seta([(ca + w, r2 + 25), (cb, r2 + 25)]),
        seta([(cb + 117, r2), (cb + 117, r1 + H)]),
        seta([(cb + 117, r2 + H), (cb + 117, r3)]),
        # as ferramentas: um tronco que desce pelo vao e abre para os dois lados.
        # A esquerda fica o que nao precisa de ninguem de fora.
        cartao(ca, r4, w, "alarm-clock", *t["agenda"]),
        cartao(cb, r4, w, "music", *t["musica"]),
        cartao(cb, r5, w, "search", *t["busca"]),
        seta([(ca + w, r2 + 52), (tronco, r2 + 52), (tronco, r4 + M), (ca + w, r4 + M)]),
        seta([(ca + w, r2 + 52), (tronco, r2 + 52), (tronco, r4 + M), (cb, r4 + M)]),
        seta([(ca + w, r2 + 52), (tronco, r2 + 52), (tronco, r5 + M), (cb, r5 + M)]),
        seta([(tronco, r5 + M - 10), (tronco, r6 - 32)], MCP, 2.25, tracejada=True),
        # o refresh token do Spotify mora em disco, do lado de ca (D21)
        selo(cb + w - 136, r4 - 12, 124, "key-round", t["token"], CINZA, "#ffffff"),
        cartao(ca, r6, w, "banknote", *t["dinheiro"], tracejado=True),
        cartao(cb, r6, w, "modelcontextprotocol", *t["mcp"], tracejado=True),
        # fora do processo
        cartao(cx, r1, w, "cloud", *t["x_nuvem"], tracejado=True),
        cartao(cx, r3, w, "ollama", *t["x_ollama"]),
        cartao(cx, r4, w, "spotify", *t["x_spotify"]),
        cartao(cx, r5, w, "duckduckgo", *t["x_busca"]),
        cartao(cx, r6, w, "wallet", *t["x_financas"], tracejado=True),
        selo(cx + w - 78, r6 - 12, 66, "modelcontextprotocol", "MCP", MCP, "#fbefff"),
        # o cliente de nuvem existe e tem teste; o provedor do outro lado nao foi escolhido
        seta([(cb + w, r1 + M), (cx, r1 + M)], tracejada=True),
        seta([(cb + w, r3 + M), (cx, r3 + M)]),
        seta([(cb + w, r4 + M), (cx, r4 + M)]),
        seta([(cb + w, r5 + M), (cx, r5 + M)]),
        seta([(cb + w, r6 + M), (cx, r6 + M)], MCP, 2.25, tracejada=True),
        texto(880, r1 + M - 8, "HTTPS", 12, CINZA, 500, "middle"),
        texto(880, r3 + M - 8, "HTTP", 12, CINZA, 500, "middle"),
        texto(880, r4 + M - 8, "HTTPS", 12, CINZA, 500, "middle"),
        texto(880, r5 + M - 8, "HTTPS", 12, CINZA, 500, "middle"),
        # legenda, no canto que sobrou
        legenda(24, A - 96, REDE, 2.5, False, t["leg_rede"]),
        legenda(24, A - 70, LOCAL, 1.75, True, t["leg_falta"]),
        legenda(24, A - 44, MCP, 2.25, True, t["leg_mcp"]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------
# Figura 4: implantacao hoje
# --------------------------------------------------------------------------

HOJE = {
    "pt": {
        "alt": "Implantação hoje: dispositivo, gateway e Ollama no mesmo PC",
        "zona": "Um PC com Windows",
        "zona_sub": "os dois processos e o modelo na mesma máquina · sem contêiner",
        "device": ("device", "python -m device.main"),
        "gateway": ("gateway", "uvicorn, sem contêiner"),
        "rosto": ("Rosto", "aba do navegador"),
        "ollama": ("Ollama local", "qwen3:8b"),
        "ws": ("WebSocket", "localhost:8000"),
        "busca": ("Busca na internet", "DuckDuckGo · Brave"),
        "spotify": ("Spotify", "Web API"),
        "connect": ("Spotify Connect", "PC, celular ou Echo Dot"),
        "musica": "a música sai aqui",
        "nota": "só texto: é este fio que passa a cruzar a rede no alvo",
    },
    "en": {
        "alt": "Deployment today: device, gateway and Ollama on the same PC",
        "zona": "One Windows PC",
        "zona_sub": "both processes and the model on the same machine · no container",
        "device": ("device", "python -m device.main"),
        "gateway": ("gateway", "uvicorn, no container"),
        "rosto": ("Face", "browser tab"),
        "ollama": ("Local Ollama", "qwen3:8b"),
        "ws": ("WebSocket", "localhost:8000"),
        "busca": ("Web search", "DuckDuckGo · Brave"),
        "spotify": ("Spotify", "Web API"),
        "connect": ("Spotify Connect", "PC, phone or Echo Dot"),
        "musica": "music comes out here",
        "nota": "text only: this is the wire that crosses the network in the target",
    },
}


def implantacao_hoje(lingua: str) -> str:
    t = HOJE[lingua]
    w = 225
    c1, c2, cx = 40, 415, 740
    r1, r2 = 100, 225
    e1, e2, e3 = 100, 196, 320           # a coluna de fora tem o seu proprio passo
    zx, zw, zh = 20, 640, r2 + H + 20 - 30
    L, A = cx + w + 20, e3 + H + 30
    pista = 700                          # por onde desce a seta que nao sai reta
    p = [
        *abrir(L, A, t["alt"]),
        # Sem logotipo do Windows: o Simple Icons nao o distribui mais, e
        # desenhar a marca de alguem a mao nao e opcao.
        zona(zx, 30, zw, zh, "monitor", t["zona"], t["zona_sub"]),
        seta([(c1 + w, r1 + 25), (c2, r1 + 25)], REDE, 2.5),
        seta([(c2, r1 + 50), (c1 + w, r1 + 50)], REDE, 2.5),
        pilula_ws((c1 + w + c2) / 2, r1 + M, *t["ws"]),
        cartao(c1, r1, w, "python", *t["device"]),
        cartao(c2, r1, w, "fastapi", *t["gateway"]),
        cartao(c1, r2, w, "smile", *t["rosto"]),
        cartao(c2, r2, w, "ollama", *t["ollama"]),
        seta([(c1 + 112, r1 + H), (c1 + 112, r2)]),
        texto(c1 + 124, r1 + H + 30, "127.0.0.1:8080", 12, CINZA, 500),
        seta([(c2 + 112, r1 + H), (c2 + 112, r2)]),
        texto(c2 + 124, r1 + H + 30, "localhost:11434", 12, CINZA, 500),
        # fora do PC
        cartao(cx, e1, w, "duckduckgo", *t["busca"]),
        cartao(cx, e2, w, "spotify", *t["spotify"]),
        cartao(cx, e3, w, "speaker", *t["connect"]),
        seta([(c2 + w, r1 + 25), (cx, r1 + 25)]),
        seta([(c2 + w, r1 + 50), (pista, r1 + 50), (pista, e2 + M), (cx, e2 + M)]),
        texto(pista, r1 + 17, "HTTPS", 12, CINZA, 500, "middle"),
        # O Spotify nao toca no Ideraldinho: toca no aparelho Connect que
        # estiver ativo na conta (D23).
        seta([(cx + 112, e2 + H), (cx + 112, e3)]),
        texto(cx + 100, e2 + H + 30, t["musica"], 12, CINZA, 500, "end"),
        legenda(zx + 4, A - 40, REDE, 2.5, False, t["nota"]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------
# Figura 5: implantacao alvo
# --------------------------------------------------------------------------

ALVO = {
    "pt": {
        "alt": "Implantação alvo: dispositivo na Raspberry Pi, gateway em contêiner numa VPS, LLM na nuvem",
        "pi": "Raspberry Pi 5",
        "pi_sub": "em casa · planejada",
        "vps": "VPS",
        "vps_sub": "planejada",
        "device": ("device", "o mesmo código"),
        "rosto": ("Rosto", "Chromium em kiosk"),
        "raspotify": ("raspotify", "aparece como Ideraldinho"),
        "falta": ["Ainda por escrever:", "botão GPIO · modo tradutor · ducking", "volume · embeddings no roteador"],
        "ws": ("WebSocket", "wss · só texto"),
        "proxy": ("Proxy TLS", "termina o wss://"),
        "gateway": ("gateway em contêiner", "docker compose"),
        "volume": ("Volume gateway-data", "tokens · fora da imagem"),
        "nuvem": ("API de LLM na nuvem", "principal · a escolher"),
        "ollama": ("Ollama de casa", "reserva · por túnel"),
        "busca": ("Busca na internet", "DuckDuckGo · Brave"),
        "financas": ("App de finanças", "gastos por voz"),
        "spotify": ("Spotify", "Web API"),
        "connect": "Spotify Connect · SPOTIFY_DEVICE casa pelo nome",
        "leg_falta": "tracejado = planejado, ainda não existe",
        "leg_existe": "linha cheia = já funciona hoje",
    },
    "en": {
        "alt": "Target deployment: device on a Raspberry Pi, gateway container on a VPS, cloud LLM",
        "pi": "Raspberry Pi 5",
        "pi_sub": "at home · planned",
        "vps": "VPS",
        "vps_sub": "planned",
        "device": ("device", "the same code"),
        "rosto": ("Face", "Chromium in kiosk mode"),
        "raspotify": ("raspotify", "shows up as Ideraldinho"),
        "falta": ["Still to be written:", "GPIO button · translator mode · ducking", "volume · embeddings in the router"],
        "ws": ("WebSocket", "wss · text only"),
        "proxy": ("TLS proxy", "terminates wss://"),
        "gateway": ("gateway container", "docker compose"),
        "volume": ("gateway-data volume", "tokens · not in the image"),
        "nuvem": ("Cloud LLM API", "primary · not chosen yet"),
        "ollama": ("Home Ollama", "fallback · through a tunnel"),
        "busca": ("Web search", "DuckDuckGo · Brave"),
        "financas": ("Finance app", "expenses by voice"),
        "spotify": ("Spotify", "Web API"),
        "connect": "Spotify Connect · SPOTIFY_DEVICE matches by name",
        "leg_falta": "dashed = planned, does not exist yet",
        "leg_existe": "solid = already works today",
    },
}


def implantacao_alvo(lingua: str) -> str:
    t = ALVO[lingua]
    w = 235
    c1, c2, cx = 40, 465, 820
    r1, r2, r3 = 110, 235, 360           # a Pi
    v1, v2, v3 = 110, 280, 412           # a VPS; v2 alinha com o meio da coluna de fora
    e1, e2, e3, e4, e5 = 96, 188, 280, 372, 464
    pz, vz = (20, 275), (445, 275)       # x e largura de cada zona
    fundo = 520                          # onde as duas zonas terminam
    baixo = 556                          # a linha que da a volta por baixo, do Spotify ate a Pi
    L, A = cx + w + 20, 610
    vao = (pz[0] + pz[1] + vz[0]) / 2
    # Cinco setas saem do mesmo cartao. Duas pistas verticais bastam para elas
    # nao se cruzarem: as de fora (e1, e5) viram na pista de dentro.
    dentro, fora = 745, 775
    gy = v2 + M
    p = [
        *abrir(L, A, t["alt"]),
        zona(pz[0], 30, pz[1], fundo - 30, "raspberrypi", t["pi"], t["pi_sub"], tracejada=True),
        zona(vz[0], 30, vz[1], fundo - 30, "server", t["vps"], t["vps_sub"], tracejada=True),
        # o WebSocket: o fio ja existe, o que muda e o endereco (GATEWAY_URL)
        seta([(c1 + w, r1 + 25), (c2, r1 + 25)], REDE, 2.5),
        seta([(c2, r1 + 50), (c1 + w, r1 + 50)], REDE, 2.5),
        pilula_ws(vao, r1 + M, *t["ws"]),
        # a Pi
        cartao(c1, r1, w, "python", *t["device"], tracejado=True),
        cartao(c1, r2, w, "smile", *t["rosto"], tracejado=True),
        cartao(c1, r3, w, "speaker", *t["raspotify"], tracejado=True),
        seta([(c1 + 117, r1 + H), (c1 + 117, r2)]),
        linhas(c1, r3 + H + 28, t["falta"][:1], CINZA, "start", peso=600),
        linhas(c1, r3 + H + 44, t["falta"][1:], CINZA, "start", 16, 400),
        # a VPS
        cartao(c2, v1, w, "lock", *t["proxy"], tracejado=True),
        cartao(c2, v2, w, "docker", *t["gateway"], tracejado=True),
        cartao(c2, v3, w, "hard-drive", *t["volume"], tracejado=True),
        seta([(c2 + 117, v1 + H), (c2 + 117, v2)], tracejada=True),
        seta([(c2 + 117, v2 + H), (c2 + 117, v3)], dupla=True),
        # fora
        cartao(cx, e1, w, "cloud", *t["nuvem"], tracejado=True),
        cartao(cx, e2, w, "ollama", *t["ollama"]),
        cartao(cx, e3, w, "duckduckgo", *t["busca"]),
        cartao(cx, e4, w, "wallet", *t["financas"], tracejado=True),
        selo(cx + w - 78, e4 - 12, 66, "modelcontextprotocol", "MCP", MCP, "#fbefff"),
        cartao(cx, e5, w, "spotify", *t["spotify"]),
        seta([(c2 + w, gy - 30), (dentro, gy - 30), (dentro, e1 + M), (cx, e1 + M)], tracejada=True),
        seta([(c2 + w, gy - 15), (fora, gy - 15), (fora, e2 + M), (cx, e2 + M)], tracejada=True),
        seta([(c2 + w, gy), (cx, gy)]),
        seta([(c2 + w, gy + 15), (fora, gy + 15), (fora, e4 + M), (cx, e4 + M)], MCP, 2.25, tracejada=True),
        seta([(c2 + w, gy + 30), (dentro, gy + 30), (dentro, e5 + M), (cx, e5 + M)]),
        # O Spotify manda tocar no aparelho Connect chamado Ideraldinho, que so
        # passa a existir quando o raspotify estiver na Pi (D23).
        seta([(cx + 117, e5 + H), (cx + 117, baixo), (vao, baixo), (vao, r3 + M), (c1 + w, r3 + M)], tracejada=True),
        rotulo((vao + cx + 117) / 2, baixo + 4, t["connect"]),
        legenda(24, A - 20, LOCAL, 1.75, True, t["leg_falta"]),
        legenda(360, A - 20, LOCAL, 1.75, False, t["leg_existe"]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


# --------------------------------------------------------------------------

# Nome do arquivo (sem a lingua e a extensao) -> quem desenha.
FIGURAS = {
    "arquitetura-alto-nivel": alto_nivel,
    "arquitetura-dispositivo": dispositivo,
    "arquitetura-gateway": gateway,
    "implantacao-hoje": implantacao_hoje,
    "implantacao-alvo": implantacao_alvo,
}


def main() -> None:
    SAIDA.mkdir(exist_ok=True)
    for nome, desenhar in FIGURAS.items():
        for lingua in ("pt", "en"):
            destino = SAIDA / f"{nome}.{lingua}.svg"
            destino.write_text(desenhar(lingua), encoding="utf-8")
            print(destino.relative_to(AQUI.parent.parent))


if __name__ == "__main__":
    main()
