"""O aparelho sobrevive à queda do gateway.

Na mesa o link nunca cai e nada disso aparece. Numa Pi na prateleira, com o
gateway do outro lado do Wi-Fi, cair é rotina -- e antes destes testes o
sintoma era o processo do dispositivo morrer com traceback e ficar mudo até
alguém notar.

Os testes sobem um servidor WebSocket de verdade, porque é o fechamento do
socket que precisa ser exercitado; um duble de conexão testaria o duble.
"""

from __future__ import annotations

import asyncio

import pytest
import websockets

from common.messages import State, StateMessage, Transcript
from common.serialization import decode, encode
from device.ws_client import AuthRejected, ConnectionLost, GatewayClient


class FakeGateway:
    """Gateway mínimo: aceita o session_start, responde IDLE, e faz o combinado.

    ``derruba`` diz o que fazer quando a primeira `utterance` chegar: fechar o
    socket no meio do turno, ou responder normalmente.
    """

    def __init__(self, derruba_no_primeiro_turno: bool = False) -> None:
        self.derruba = derruba_no_primeiro_turno
        self.sessoes = 0
        self.frases: list[str] = []
        self._server = None
        self.url = ""

    async def __aenter__(self) -> "FakeGateway":
        self._server = await websockets.serve(self._handle, "127.0.0.1", 0)
        porta = self._server.sockets[0].getsockname()[1]
        self.url = f"ws://127.0.0.1:{porta}"
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, ws) -> None:
        self.sessoes += 1
        primeira = self.sessoes == 1
        decode(await ws.recv())  # session_start
        await ws.send(encode(StateMessage(value=State.IDLE)))
        async for raw in ws:
            self.frases.append(decode(raw).text)
            if primeira and self.derruba:
                await ws.close()
                return
            await ws.send(encode(StateMessage(value=State.THINKING)))
            await ws.send(encode(Transcript(text="pronto", role="assistant")))
            await ws.send(encode(StateMessage(value=State.IDLE)))


async def _turno(client: GatewayClient, texto: str) -> list:
    await client.send_utterance(texto)
    recebidas = []
    async for m in client.receive():
        recebidas.append(m)
        if isinstance(m, StateMessage) and m.value is State.IDLE:
            break
    return recebidas


@pytest.mark.asyncio
async def test_queda_no_meio_do_turno_vira_excecao_e_reconecta():
    """Cair no meio do turno não pode matar o processo.

    A resposta daquele turno se perde -- o histórico vive na Session do gateway,
    que morreu junto. O que não pode se perder é o aparelho.
    """
    async with FakeGateway(derruba_no_primeiro_turno=True) as gw:
        async with GatewayClient(url=gw.url, latency_ms=0) as client:
            with pytest.raises(ConnectionLost):
                await _turno(client, "primeira")

            # Já reconectado: o turno seguinte funciona sem ninguém religar nada.
            recebidas = await _turno(client, "segunda")

    assert gw.sessoes == 2, "deveria ter aberto uma sessao nova"
    assert gw.frases == ["primeira", "segunda"]
    assert any(isinstance(m, Transcript) for m in recebidas)


@pytest.mark.asyncio
async def test_espera_o_gateway_subir_em_vez_de_desistir():
    """O gateway pode estar reiniciando quando o aparelho liga.

    Desistir na primeira tentativa exigiria alguém para religar o aparelho --
    e é justamente quando ninguém está olhando que o link cai.
    """
    gw = FakeGateway()
    # Uma porta livre, pedida ao sistema: a 8765 fixa que estava aqui falhava
    # com qualquer coisa ouvindo nela -- e é a do redirect da autenticação das
    # finanças. O socket é fechado antes de o cliente tentar, porque o teste
    # precisa justamente de ninguém atendendo na primeira tentativa.
    import socket

    with socket.socket() as reserva:
        reserva.bind(("127.0.0.1", 0))
        porta = reserva.getsockname()[1]
    gw.url = f"ws://127.0.0.1:{porta}"

    async def sobe_o_gateway_depois():
        await asyncio.sleep(1.2)  # mais que o BACKOFF_START, para forçar retentativa
        gw._server = await websockets.serve(gw._handle, "127.0.0.1", porta)

    tarefa = asyncio.create_task(sobe_o_gateway_depois())
    async with GatewayClient(url=gw.url, latency_ms=0) as client:
        recebidas = await _turno(client, "ola")
    await tarefa
    gw._server.close()
    await gw._server.wait_closed()

    assert any(isinstance(m, Transcript) for m in recebidas)


@pytest.mark.asyncio
async def test_token_recusado_nao_entra_em_retentativa():
    """Token errado não melhora com espera; insistir esconderia o erro."""
    from common.messages import Error

    async def recusa(ws):
        await ws.recv()
        await ws.send(encode(Error(message="invalid session_start or token")))

    server = await websockets.serve(recusa, "127.0.0.1", 0)
    porta = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(AuthRejected):
            async with GatewayClient(url=f"ws://127.0.0.1:{porta}", latency_ms=0):
                pass
    finally:
        server.close()
        await server.wait_closed()
