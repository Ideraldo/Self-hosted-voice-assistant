"""Ferramentas que o LLM pode chamar (plano, seção 3).

Dois grupos, e a diferença entre eles é onde a execução acontece:

- **`device_tools`** — timer, alarme, agenda. O gateway declara e transporta; a
  execução é do dispositivo, sempre (plano, seção 5, regra 3). É o que faz o
  despertador não depender do servidor.
- **`spotify`** — o gateway executa, porque aqui há segredo, e segredo não desce
  pelo fio.

O que os dois têm em comum: o resultado já vem redigido para ser falado, e vai
ao ar sem uma segunda rodada de LLM. Ver D18 -- o modelo reescrevendo isso perde
item e custa segundos.
"""

from gateway.tools.device_tools import DEVICE_TOOLS, TERMINAL_TOOLS, TOOL_TO_INTENT
from gateway.tools.search import (
    SEARCH_TOOLS,
    Brave,
    DuckDuckGo,
    Leitor,
    SearchProvider,
    executar_busca,
)
from gateway.tools.spotify import (
    SPOTIFY_TOOLS,
    SpotifyClient,
    SpotifyError,
    executar_spotify,
)

#: Toda ferramenta do Spotify também é terminal: a frase que o cliente devolve
#: ("Tocando Construcao, de Chico Buarque.") é a resposta.
SPOTIFY_TOOL_NAMES = frozenset(t.name for t in SPOTIFY_TOOLS)

#: A busca é a única ferramenta **não-terminal**: o que ela devolve é material
#: para o modelo ler, não a frase a ser falada. Por isso ela não entra em
#: nenhuma lista de terminais -- o turno segue para uma segunda rodada de LLM.
SEARCH_TOOL_NAMES = frozenset(t.name for t in SEARCH_TOOLS)

__all__ = [
    "SEARCH_TOOLS",
    "SEARCH_TOOL_NAMES",
    "SearchProvider",
    "Leitor",
    "DuckDuckGo",
    "Brave",
    "executar_busca",
    "DEVICE_TOOLS",
    "TERMINAL_TOOLS",
    "TOOL_TO_INTENT",
    "SPOTIFY_TOOLS",
    "SPOTIFY_TOOL_NAMES",
    "SpotifyClient",
    "SpotifyError",
    "executar_spotify",
]
