"""The wire contract: what one side encodes, the other must decode identically.

These are cheap tests guarding an expensive failure -- a mismatch here shows up
on the Pi, not on the PC (repository-structure section 2).
"""

import json

import pytest

from common.messages import (
    SessionStart,
    State,
    StateMessage,
    ToolCall,
    ToolResult,
    Transcript,
    Utterance,
)
from common.serialization import decode, encode


@pytest.mark.parametrize(
    "message",
    [
        SessionStart(device_id="ideraldinho-01", token="t"),
        Utterance(text="que horas sao?"),
        StateMessage(value=State.THINKING),
        Transcript(text="que horas sao?", role="user"),
        ToolCall(id="1", name="criar_alarme", args={"hora": "07:00"}),
    ],
)
def test_round_trip(message):
    assert decode(encode(message)) == message


def test_state_arrives_as_enum():
    assert decode(encode(StateMessage(value=State.SPEAKING))).value is State.SPEAKING


class TestCampoOpcional:
    """`expects_reply` é opcional, e opcional vazio não vai para o fio.

    O `decode` recusa campo desconhecido. Se o campo novo viajasse como `null`
    em toda mensagem, um dispositivo de versão anterior passaria a descartar
    todo `state` do gateway -- por causa de um campo que ninguém preencheu.
    """

    def test_expects_reply_atravessa_o_fio(self):
        original = StateMessage(value=State.IDLE, expects_reply=True)
        assert decode(encode(original)) == original
        assert decode(encode(original)).expects_reply is True

    def test_no_tool_result_tambem(self):
        original = ToolResult(id="1", ok=True, value="Qual eu cancelo?", expects_reply=True)
        assert decode(encode(original)) == original

    def test_ausente_fica_fora_do_json(self):
        assert json.loads(encode(StateMessage(value=State.IDLE))) == {
            "type": "state",
            "value": "idle",
        }
        assert "expects_reply" not in json.loads(encode(ToolResult(id="1", ok=True)))

    def test_mensagem_sem_o_campo_e_o_comportamento_antigo(self):
        # O que um gateway de antes do campo manda, byte a byte.
        assert decode('{"type": "state", "value": "idle"}').expects_reply is None

    def test_false_explicito_nao_e_engolido(self):
        # Só o `None` é omitido: `final=False` e `ok=False` são informação.
        assert json.loads(encode(Transcript(text="oi", final=False)))["final"] is False
        assert json.loads(encode(ToolResult(id="1", ok=False)))["ok"] is False


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match="unknown message type"):
        decode('{"type": "end_audio"}')


def test_unexpected_field_is_rejected():
    with pytest.raises(ValueError, match="unexpected field"):
        decode('{"type": "utterance", "text": "oi", "duration": 3}')


def test_missing_type_is_rejected():
    with pytest.raises(ValueError, match="no 'type' field"):
        decode('{"text": "oi"}')


def test_campo_obrigatorio_ausente_e_valueerror():
    """Quem chama o `decode` so sabe tratar `ValueError`."""
    with pytest.raises(ValueError, match="utterance"):
        decode('{"type": "utterance"}')

