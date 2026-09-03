"""The device's only outbound connection: one WebSocket to the gateway.

Rule of thumb from the plan: the device knows exactly one endpoint. Direct
function calls between device and gateway code are forbidden -- migrating to the
VPS must be nothing more than changing GATEWAY_URL.

O link cai. Na mesa isso quase nunca acontece; numa Pi na prateleira, com o
gateway do outro lado do Wi-Fi, é questão de quando. Por isso o cliente
reconecta sozinho, com espera crescente, e o aparelho volta a ouvir em vez de
morrer com traceback.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, AsyncIterator

import websockets
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from common.messages import Error, SessionStart, State, StateMessage, Utterance
from common.serialization import decode, encode
from device.config import config

log = logging.getLogger("ideraldinho.ws")

#: Espera entre tentativas: dobra a cada falha, até o teto. O primeiro passo é
#: curto porque a queda mais comum é o gateway reiniciando, e ele volta em
#: segundos; o teto existe para não martelar um servidor que caiu de vez.
BACKOFF_START = 0.5
BACKOFF_CAP = 30.0

#: Quanto tempo insistir quando há alguém esperando resposta. Depois da Fase 2 o
#: aparelho não precisa mais do gateway para existir -- timer, alarme e hora são
#: locais --, então ficar preso tentando reconectar é pior que dizer "não deu" e
#: voltar a ouvir. A retentativa sem fim continua valendo para o que roda ao
#: fundo, não para o turno de alguém que está parado esperando (D14).
CONNECT_BUDGET = 8.0


class AuthRejected(Exception):
    """O gateway recusou o session_start.

    Não herda de OSError de propósito: herdando, ela cairia no `except` da
    retentativa e um token errado viraria uma espera infinita em vez de um erro.
    """


class ConnectionLost(Exception):
    """A conexão caiu no meio de um turno.

    Reconectar devolve o socket, mas não o turno: o histórico da conversa vive
    na `Session` do gateway, que morreu junto com a conexão. Quem chama precisa
    saber que a resposta não vem, para voltar a ouvir em vez de esperar.
    """


class GatewayClient:
    """Thin wrapper over the socket, with the simulated link delay built in.

    The delay is not cosmetic: designing against localhost's zero latency means
    designing for a network that will never exist (plan section 2, rule 3).
    """

    def __init__(self, url: str | None = None, latency_ms: int | None = None) -> None:
        self._url = url or config.gateway_url
        self._latency = (latency_ms if latency_ms is not None else config.simulated_latency_ms) / 1000
        self._ws: Any = None

    async def __aenter__(self) -> "GatewayClient":
        """Tenta conectar uma vez, e segue mesmo se não conseguir.

        Não bloqueia de propósito: o critério de aceite da Fase 2 é o timer
        funcionar com o gateway desligado, e um aparelho que nem liga sem o
        servidor nunca cumpriria isso. Sem conexão ele sobe em modo local, e a
        primeira frase que precisar do LLM tenta de novo.
        """
        try:
            await self._connect(budget=0)
        except ConnectionLost:
            log.warning("gateway fora do ar; subindo em modo local")
        return self

    @property
    def online(self) -> bool:
        return self._ws is not None

    async def __aexit__(self, *exc: object) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    # -- conexão -----------------------------------------------------------

    async def _connect(self, budget: float | None = None) -> None:
        """Conecta e faz o aperto de mão.

        `budget` é quanto tempo insistir: `None` é para sempre (o aparelho de
        prateleira que não pode depender de alguém para religá-lo), e um número
        é para quando há uma pessoa esperando do outro lado. Estourando o
        orçamento levanta `ConnectionLost`, e quem chamou decide o que dizer.
        """
        delay = BACKOFF_START
        attempt = 0
        deadline = None if budget is None else time.monotonic() + budget
        while True:
            attempt += 1
            try:
                self._ws = await websockets.connect(self._url, max_size=None)
                await self.send(SessionStart(device_id=config.device_id, token=config.token))
                await self._await_ready()
                if attempt > 1:
                    log.info("reconectado ao gateway na tentativa %d", attempt)
                return
            except (OSError, ConnectionClosed, InvalidHandshake, ConnectionError) as exc:
                self._ws = None
                # Meio segundo de dispersão: sem isso, vários dispositivos
                # voltando do mesmo apagão batem no gateway no mesmo instante.
                wait = min(delay, BACKOFF_CAP) + random.uniform(0, 0.5)
                if deadline is not None and time.monotonic() + wait > deadline:
                    raise ConnectionLost(f"gateway inacessivel: {exc}") from exc
                log.warning(
                    "gateway inacessivel (%s); nova tentativa em %.1fs", exc, wait
                )
                await asyncio.sleep(wait)
                delay = min(delay * 2, BACKOFF_CAP)

    async def _await_ready(self) -> None:
        """Consome o IDLE que o gateway manda como aceite do session_start.

        Ele faz parte do aperto de mão, não de nenhum turno. Deixá-lo na fila
        fazia o primeiro turno lê-lo no meio do caminho e voltar para IDLE
        justamente quando o gateway ia anunciar THINKING -- transição ilegal.
        """
        reply = decode(await self._ws.recv())
        if isinstance(reply, Error):
            # Token errado não melhora com espera; insistir só esconde o erro.
            raise AuthRejected(f"gateway recusou a sessao: {reply.message}")
        if not (isinstance(reply, StateMessage) and reply.value is State.IDLE):
            raise ConnectionError(f"aperto de mao inesperado: {reply!r}")

    # -- tráfego -----------------------------------------------------------

    async def send(self, message: object) -> None:
        await self._delay()
        await self._ws.send(encode(message))

    async def _send_reconnecting(self, message: object) -> None:
        """Manda, reconectando se preciso.

        A reconexão mora aqui, e não em `receive`, de propósito: enquanto o
        gateway está fora do ar o aparelho continua ouvindo em vez de ficar
        preso num laço de retentativa. A espera só acontece quando há algo de
        verdade para entregar.
        """
        if self._ws is None:
            await self._connect(budget=CONNECT_BUDGET)
            return await self.send(message)
        try:
            await self.send(message)
        except ConnectionClosed:
            log.warning("socket caiu antes de enviar; reconectando")
            await self._connect(budget=CONNECT_BUDGET)
            await self.send(message)

    async def send_utterance(self, text: str) -> None:
        """Manda a frase já transcrita. Nenhum áudio sobe pelo fio (D1/D7).

        Se o socket morreu enquanto o aparelho ouvia -- e ouvir é o que ele mais
        faz --, reconecta antes de mandar. A frase acabou de ser dita e ainda
        vale; é o único ponto onde a reconexão é invisível para quem falou.
        """
        await self._send_reconnecting(Utterance(text=text))

    async def receive(self) -> AsyncIterator[Any]:
        """Yield decoded control messages as they arrive.

        Frames binários não deveriam mais existir em nenhuma direção; se algum
        chegar, é gateway de versão antiga, e passar adiante deixa isso visível.
        """
        try:
            async for packet in self._ws:
                await self._delay()
                if isinstance(packet, bytes):
                    yield packet
                    continue
                try:
                    yield decode(packet)
                except ValueError as exc:
                    log.warning("dropping malformed message: %s", exc)
        except ConnectionClosed as exc:
            self._ws = None
            raise ConnectionLost("a resposta se perdeu com a conexao") from exc

        # O gateway fechou de forma limpa (reinício, por exemplo). Para o turno
        # o efeito é o mesmo de uma queda: a resposta não vem.
        self._ws = None
        raise ConnectionLost("o gateway encerrou a conexao")

    async def _delay(self) -> None:
        if self._latency:
            await asyncio.sleep(self._latency)
