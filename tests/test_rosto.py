"""O rosto (Fase 4): o gancho na máquina de estados e o servidor local.

O que estes testes protegem, em ordem de importância:

1. **O rosto não pode derrubar o aparelho.** Um observador que levanta exceção,
   um servidor que não sobe -- nada disso pode impedir alguém de pedir um timer.
2. O gancho vê *todas* as transições, porque ele está dentro do `transition`.
3. A página é servida com o tipo certo, e o WebSocket recebe o estado atual
   assim que abre -- senão a aba que recarrega fica escura.
"""

from __future__ import annotations

import asyncio
import http.client
import json

import pytest
import websockets

from common.messages import Emotion, State, StateMessage
from common.serialization import decode, encode
from device.rosto.server import FaceServer
from device.state import StateMachine


# ---------- o gancho ----------


def test_observador_recebe_o_estado_atual_ao_assinar():
    sm = StateMachine()
    vistos = []
    sm.subscribe(lambda estado, emocao: vistos.append((estado, emocao)))
    # Sem esta entrega imediata a tela fica em branco até a próxima transição,
    # que no ocioso pode demorar minutos.
    assert vistos == [(State.IDLE, Emotion.NEUTRAL)]


def test_observador_ve_todas_as_transicoes():
    sm = StateMachine()
    vistos = []
    sm.subscribe(lambda estado, _: vistos.append(estado))
    vistos.clear()

    sm.transition(State.LISTENING)
    sm.transition(State.THINKING)
    sm.transition(State.SPEAKING)
    assert vistos == [State.LISTENING, State.THINKING, State.SPEAKING]


def test_transicao_ilegal_nao_notifica():
    sm = StateMachine()
    vistos = []
    sm.subscribe(lambda estado, _: vistos.append(estado))
    vistos.clear()

    with pytest.raises(ValueError):
        sm.transition(State.SPEAKING)
    # O rosto mostraria SPEAKING para um aparelho que está em IDLE.
    assert vistos == []


def test_emocao_nao_mexe_no_estado():
    sm = StateMachine()
    sm.transition(State.LISTENING)
    sm.set_emotion(Emotion.HAPPY)
    assert sm.state is State.LISTENING
    assert sm.emotion is Emotion.HAPPY


def test_observador_quebrado_nao_derruba_a_maquina():
    """A regra da camada: a tela é vitrine, o timer é função."""
    sm = StateMachine()
    sobreviventes = []

    def explode(estado, emocao):
        raise RuntimeError("o navegador morreu")

    sm.subscribe(explode)
    sm.subscribe(lambda estado, _: sobreviventes.append(estado))

    assert sm.transition(State.LISTENING) is State.LISTENING
    # E o observador seguinte continua sendo chamado.
    assert State.LISTENING in sobreviventes


# ---------- o protocolo ----------


def test_emocao_e_opcional_no_fio():
    """Ausente quer dizer 'não mudou'. O gateway de hoje não manda emoção, e
    nem por isso as mensagens dele podem quebrar."""
    assert decode('{"type": "state", "value": "idle"}').emotion is None

    msg = decode(encode(StateMessage(State.SPEAKING, Emotion.HAPPY)))
    assert msg.value is State.SPEAKING
    assert msg.emotion is Emotion.HAPPY


# ---------- o servidor ----------


@pytest.fixture
async def face():
    # Porta 0: o SO escolhe uma livre. Fixar porta em teste é o jeito mais rápido
    # de ver a suíte quebrar na máquina de outra pessoa.
    server = FaceServer(port=0)
    assert await server.start()
    server.port = server._server.sockets[0].getsockname()[1]
    yield server
    await server.stop()


def _get(host: str, port: int, path: str) -> tuple[int, str, str]:
    """GET síncrono, para rodar numa thread.

    O `http.client` bloqueia, e o servidor está no mesmo laço de eventos do
    teste: chamá-lo direto trava os dois esperando um ao outro. Foi assim que
    esta suíte travou na primeira execução.
    """
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", path)
        resposta = conn.getresponse()
        corpo = resposta.read().decode("utf-8", "replace")
        return resposta.status, resposta.getheader("Content-Type") or "", corpo
    finally:
        conn.close()


async def test_serve_a_pagina_com_o_tipo_certo(face):
    status, tipo, corpo = await asyncio.to_thread(_get, face.host, face.port, "/")
    assert status == 200
    assert tipo.startswith("text/html")
    assert "face.js" in corpo


async def test_a_raiz_segue_o_tema(face):
    """O tema é só a camada de desenho: os dois rostos falam o mesmo protocolo
    e usam o mesmo `face.js`. Trocar de rosto não pode virar trocar de código."""
    face.tema = "anime"
    _, _, anime = await asyncio.to_thread(_get, face.host, face.port, "/")
    assert "anime.css" in anime

    face.tema = "minimo"
    _, _, minimo = await asyncio.to_thread(_get, face.host, face.port, "/")
    assert "face.css" in minimo

    # E os dois continuam alcançáveis pelo nome, para comparar um com o outro
    # sem reiniciar o aparelho.
    for arquivo in ("minimo.html", "anime.html"):
        status, _, corpo = await asyncio.to_thread(_get, face.host, face.port, f"/{arquivo}")
        assert status == 200
        assert "face.js" in corpo


async def test_nao_serve_fora_do_diretorio(face):
    status, _, _ = await asyncio.to_thread(_get, face.host, face.port, "/../../config.py")
    assert status == 404


async def test_a_aba_recebe_o_estado_atual_ao_abrir(face):
    face.publish(State.THINKING, Emotion.CURIOUS)
    async with websockets.connect(f"ws://{face.host}:{face.port}/ws") as ws:
        # Quem abre depois -- ou dá F5 -- precisa saber onde a máquina está.
        assert json.loads(await ws.recv()) == {"state": "thinking", "emotion": "curious"}


async def test_transmite_a_transicao_para_a_aba_aberta(face):
    sm = StateMachine()
    sm.subscribe(face.publish)

    async with websockets.connect(f"ws://{face.host}:{face.port}/ws") as ws:
        await ws.recv()  # o estado inicial
        sm.transition(State.LISTENING)
        assert json.loads(await ws.recv()) == {"state": "listening", "emotion": "neutral"}


async def test_servidor_que_nao_sobe_nao_levanta(monkeypatch):
    """O caso comum é a porta ocupada por uma execução anterior que não morreu.

    O erro entra por injeção, e não ocupando a porta de verdade, porque o
    Windows deixa dois sockets ligarem na mesma porta e o teste passaria por
    engano lá e valeria só no Linux. O que importa aqui é o nosso tratamento:
    devolver False em vez de derrubar o aparelho antes do primeiro timer.
    """

    def recusa(*args, **kwargs):
        raise OSError("address already in use")

    monkeypatch.setattr("device.rosto.server.serve", recusa)
    assert await FaceServer(port=0).start() is False
