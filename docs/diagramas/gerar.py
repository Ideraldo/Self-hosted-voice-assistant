"""Desenha os diagramas de arquitetura que usam icones, e grava em docs/img/.

    python docs/diagramas/gerar.py

Por que um script e nao Mermaid: o Mermaid do GitHub so desenha caixas, e um
diagrama de arquitetura se le mais rapido quando o Docker parece o Docker e o
Spotify parece o Spotify. Por que um script e nao uma ferramenta de desenho: o
resultado e um SVG de texto, que cabe num diff, e mudar um rotulo e mudar uma
linha aqui. So usa a biblioteca padrao.

Os icones em `icones/` vem de dois conjuntos abertos: os `marca-*.svg` sao do
Simple Icons (CC0), os demais do Lucide (licenca ISC). As marcas continuam
sendo de quem sao; aqui elas so identificam o servico.
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
}

TEXTOS = {
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


def icone(nome: str, x: float, y: float, lado: float, cor: str = TINTA) -> str:
    """O miolo de um SVG de `icones/`, reposicionado e colorido."""
    marca = nome in MARCAS
    bruto = (ICONES / (f"marca-{nome}.svg" if marca else f"{nome}.svg")).read_text(encoding="utf-8")
    miolo = re.search(r"<svg[^>]*>(.*)</svg>", bruto, flags=re.S).group(1)
    miolo = re.sub(r"<title>.*?</title>", "", miolo, flags=re.S).strip()
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


def cartao(x, y, w, nome_icone, titulo, sub, h=74, tracejado=False) -> str:
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


def zona(x, y, w, h, nome_icone, titulo, sub) -> str:
    return "".join(
        [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{ZONA}" stroke="{BORDA}" stroke-width="1.5"/>',
            icone(nome_icone, x + 20, y + 17, 22),
            texto(x + 52, y + 27, titulo, 16, peso=700),
            texto(x + 52, y + 45, sub, 12, CINZA),
        ]
    )


def seta(pontos, cor=LOCAL, grossura=1.75, tracejada=False) -> str:
    """Linha em cotovelos, com cantos arredondados e ponta no fim."""
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
    return f'<path d="{d}" fill="none" stroke="{cor}" stroke-width="{grossura}"{traco} marker-end="url(#ponta-{ponta})"/>'


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


def alto_nivel(lingua: str) -> str:
    t = TEXTOS[lingua]
    L, A = 1360, 500
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {L} {A}" width="{L}" height="{A}" '
        f'font-family="{FONTE}" role="img" aria-label="{escape(t["alt"])}">',
        "<defs>",
        f'<marker id="ponta-local" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LOCAL}"/></marker>',
        f'<marker id="ponta-rede" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{REDE}"/></marker>',
        f'<marker id="ponta-mcp" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MCP}"/></marker>',
        "</defs>",
        f'<rect x="0.75" y="0.75" width="{L - 1.5}" height="{A - 1.5}" rx="16" fill="#ffffff" stroke="{BORDA}" stroke-width="1.5"/>',
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
        f'<rect x="630" y="112" width="110" height="52" rx="26" fill="#ddf4ff" stroke="{REDE}" stroke-width="1.5"/>',
        texto(685, 134, t["ws"][0], 13, REDE, 700, "middle"),
        texto(685, 151, t["ws"][1], 11.5, REDE, 500, "middle"),
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
        # o selo fica montado na borda do cartao, onde o olho bate primeiro
        f'<rect x="1252" y="370" width="66" height="24" rx="12" fill="#fbefff" stroke="{MCP}" stroke-width="1.5"/>',
        icone("modelcontextprotocol", 1261, 375.5, 13, MCP),
        texto(1279, 386.5, "MCP", 12, MCP, 700),
        # entrada e saida
        circulo(70, 137, "user", t["voz"]),
        circulo(70, 387, "volume-2", t["som"]),
        seta([(96, 137), (170, 137)]),
        seta([(170, 387), (96, 387)]),
        "</svg>",
    ]
    return "\n".join(p) + "\n"


def main() -> None:
    SAIDA.mkdir(exist_ok=True)
    for lingua in TEXTOS:
        destino = SAIDA / f"arquitetura-alto-nivel.{lingua}.svg"
        destino.write_text(alto_nivel(lingua), encoding="utf-8")
        print(destino.relative_to(AQUI.parent.parent))


if __name__ == "__main__":
    main()
