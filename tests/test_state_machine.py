import pytest

from common.messages import State
from device.state import StateMachine


def test_happy_path():
    sm = StateMachine()
    assert sm.state is State.IDLE
    for target in (State.LISTENING, State.THINKING, State.SPEAKING, State.IDLE):
        assert sm.transition(target) is target


def test_barge_in():
    sm = StateMachine()
    sm.transition(State.LISTENING)
    sm.transition(State.THINKING)
    sm.transition(State.SPEAKING)
    assert sm.transition(State.LISTENING) is State.LISTENING


def test_illegal_transition():
    sm = StateMachine()
    with pytest.raises(ValueError):
        sm.transition(State.SPEAKING)


class TestErroDoGatewayNoMeioDoTurno:
    """Um `error` encerra o turno, e encerrar o turno é voltar para IDLE.

    `handle_incoming` saía no `error` deixando a máquina onde estava. Nada
    quebrava na hora: quebrava na frase seguinte, quando o laço tentava
    `-> LISTENING` de um estado que não permite, e o `ValueError` derrubava o
    aparelho por causa de um erro que já tinha passado.
    """

    @staticmethod
    def _pecas(turnos):
        import asyncio

        class Cliente:
            """Entrega um turno combinado por `utterance`, como o gateway faria."""

            def __init__(self):
                self._turnos = list(turnos)

            async def send_utterance(self, text):
                self._mensagens = self._turnos.pop(0)

            async def receive(self):
                for mensagem in self._mensagens:
                    yield mensagem

        class Voz:
            def synthesize(self, text):
                return text

        class AltoFalante:
            def __init__(self):
                self.falado = []

            def play(self, pcm):
                self.falado.append(pcm)

        return Cliente(), Voz(), AltoFalante(), asyncio.Lock()

    @pytest.mark.parametrize(
        "antes_do_erro",
        [
            [],  # frase recusada: o gateway nem chegou a anunciar THINKING
            [State.THINKING],
            [State.THINKING, State.SPEAKING],
        ],
    )
    async def test_volta_para_idle(self, antes_do_erro):
        from common.messages import Error, StateMessage
        from device.main import handle_incoming

        mensagens = [StateMessage(value=s) for s in antes_do_erro] + [Error(message="x")]
        cliente, voz, alto_falante, falando = self._pecas([mensagens])
        await cliente.send_utterance("oi")

        sm = StateMachine()
        sm.transition(State.LISTENING)
        await handle_incoming(cliente, sm, voz, alto_falante, falando, None)

        assert sm.state is State.IDLE

    async def test_a_frase_seguinte_funciona(self):
        """O laço de texto inteiro: um turno com erro, e outro normal depois."""
        from common.messages import Error, StateMessage, Transcript
        from device.main import answer

        cliente, voz, alto_falante, falando = self._pecas(
            [
                [StateMessage(value=State.THINKING), Error(message="x")],
                [
                    StateMessage(value=State.THINKING),
                    StateMessage(value=State.SPEAKING),
                    Transcript(text="Foi Cabral.", role="assistant", final=False),
                    StateMessage(value=State.IDLE),
                ],
            ]
        )
        sm = StateMachine()

        for _ in range(2):
            # É o que `text_loop` e `listen_and_transcribe` fazem antes de
            # cada turno -- e onde o ValueError aparecia.
            sm.transition(State.LISTENING)
            await answer(
                cliente, sm, voz, alto_falante, None, falando, "quem descobriu o brasil"
            )

        assert alto_falante.falado == ["Foi Cabral."]
        assert sm.state is State.IDLE
