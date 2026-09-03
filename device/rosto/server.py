"""O rosto: uma página servida localmente, e o estado indo para ela por WebSocket.

Por que uma página, e não um app nativo desenhando no framebuffer: a tela não
vai ficar só no rosto. O plano é ela ganhar transcrição, timer correndo, capa do
álbum -- e isso é interface, que é onde HTML custa uma linha e um canvas custa
uma tarde. A conta de CPU do navegador é real, mas ela se paga do segundo uso da
tela em diante.

Um servidor só, numa porta só, sem dependência nova: o `websockets` que o
`ws_client` já usa aceita responder HTTP no mesmo socket através do
`process_request`. Não vale a pena subir um uvicorn ao lado do processo do
dispositivo para entregar três arquivos estáticos.

**Regra desta camada: ela não pode derrubar o aparelho.** O rosto é vitrine; o
timer é função. Toda falha aqui vira log e segue.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from common.messages import Emotion, State

log = logging.getLogger("ideraldinho.rosto")

STATIC = Path(__file__).parent / "static"

#: O caminho do WebSocket. Qualquer outro caminho é arquivo estático.
WS_PATH = "/ws"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class FaceServer:
    """Serve a página e transmite `{state, emotion}` para quem estiver olhando.

    Instância única por processo, assinada no `StateMachine`. Nada aqui sabe o
    que é um turno: recebe estado, empurra JSON.
    """

    def __init__(
        self,
        port: int,
        host: str = "127.0.0.1",
        static: Path = STATIC,
        tema: str = "minimo",
    ) -> None:
        self.port = port
        self.host = host
        self.static = static
        # Qual rosto a raiz serve. Os dois arquivos continuam alcançáveis pelo
        # nome, o que deixa comparar um com o outro sem reiniciar nada.
        self.tema = tema
        self._server = None
        self._clients: set[ServerConnection] = set()
        # O último estado publicado. Existe para o navegador que abre depois --
        # ou que recarrega -- não ficar olhando uma tela vazia até a próxima
        # transição, que pode demorar minutos.
        self._latest = json.dumps({"state": State.IDLE.value, "emotion": Emotion.NEUTRAL.value})

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def start(self) -> bool:
        """Sobe o servidor. Devolve se conseguiu -- nunca levanta.

        A porta ocupada é o caso comum (uma execução anterior que não morreu, ou
        o próprio Chromium segurando), e ela não é motivo para o aparelho não
        ouvir.
        """
        try:
            self._server = await serve(
                self._handle, self.host, self.port, process_request=self._http
            )
        except OSError as exc:
            log.warning("rosto nao subiu na porta %s: %s", self.port, exc)
            return False
        return True

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    def publish(self, state: State, emotion: Emotion) -> None:
        """O observador do `StateMachine`. Síncrono, e tem que continuar sendo.

        Ele é chamado de dentro do laço do turno: qualquer espera aqui é espera
        entre o usuário calar a boca e o Whisper começar. O `broadcast` do
        `websockets` escreve no buffer e volta na hora, sem `await`.
        """
        self._latest = json.dumps({"state": state.value, "emotion": emotion.value})
        if self._clients:
            broadcast(self._clients, self._latest)

    async def _handle(self, connection: ServerConnection) -> None:
        """Uma aba aberta. Manda o estado atual e fica quieto até ela fechar."""
        self._clients.add(connection)
        try:
            await connection.send(self._latest)
            # A página não fala, só escuta. Ficar aqui é o que mantém a conexão
            # de pé; sair do handler a fecharia.
            await connection.wait_closed()
        finally:
            self._clients.discard(connection)

    def _http(self, connection: ServerConnection, request: Request) -> Response | None:
        """Responde arquivo estático; devolve None para deixar o WebSocket passar."""
        if request.path == WS_PATH:
            return None

        name = request.path.split("?")[0].lstrip("/") or f"{self.tema}.html"
        path = (self.static / name).resolve()
        # `..` no caminho sairia do diretório. É um servidor de localhost, mas o
        # custo de fechar isso é uma linha.
        if not path.is_relative_to(self.static.resolve()) or not path.is_file():
            return connection.respond(404, "nao existe\n")

        body = path.read_bytes()
        headers = Headers(
            {
                "Content-Type": CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                "Content-Length": str(len(body)),
                # A página muda a cada `git pull`; cache aqui só rende confusão.
                "Cache-Control": "no-store",
            }
        )
        return Response(200, "OK", headers, body)


async def start_face(port: int, tema: str = "minimo") -> FaceServer | None:
    """Sobe o rosto, ou devolve None e segue a vida."""
    server = FaceServer(port, tema=tema)
    if not await server.start():
        return None
    return server
