"""The wire contract: what one side encodes, the other must decode identically.

These are cheap tests guarding an expensive failure -- a mismatch here shows up
on the Pi, not on the PC (repository-structure section 2).
"""

import pytest

from common.messages import SessionStart, State, StateMessage, ToolCall, Transcript, Utterance
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


def test_unknown_type_is_rejected():
    with pytest.raises(ValueError, match="unknown message type"):
        decode('{"type": "end_audio"}')


def test_unexpected_field_is_rejected():
    with pytest.raises(ValueError, match="unexpected field"):
        decode('{"type": "utterance", "text": "oi", "duration": 3}')


def test_missing_type_is_rejected():
    with pytest.raises(ValueError, match="no 'type' field"):
        decode('{"text": "oi"}')
