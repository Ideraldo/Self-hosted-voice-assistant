"""Device <-> gateway wire protocol (plan section 4).

Single source of truth: neither side defines messages on its own.
Control travels as JSON. Audio does not travel at all: depois das decisões D1 e
D7 o dispositivo transcreve e sintetiza sozinho, e o que cruza a rede é sempre
texto. O formato PCM continua aqui porque é o contrato interno do dispositivo --
microfone, VAD e STT falam nele.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

# Audio format spoken inside the device (mic -> VAD -> STT).
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes (16-bit)


class State(str, Enum):
    """Device state machine (plan section 1)."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Emotion(str, Enum):
    """O segundo eixo do rosto, e é de propósito que ele não é um estado.

    `State` é sobre o ciclo do turno: `SPEAKING` é `SPEAKING` tanto para "sao
    tres da tarde" quanto para uma piada. O humor é ortogonal a isso -- o mesmo
    estado pode ser dito de vários jeitos -- e quem sabe qual é ele é o gateway,
    que viu o conteúdo da resposta; o dispositivo só o repassa para a tela.

    Fica declarado agora porque encaixar um eixo novo no protocolo depois é
    caro, e porque um campo opcional não custa nada enquanto ninguém o preenche.
    Nenhuma parte do gateway o envia ainda: hoje o rosto é sempre NEUTRAL.
    """

    NEUTRAL = "neutral"
    HAPPY = "happy"
    CURIOUS = "curious"
    CONFUSED = "confused"
    SORRY = "sorry"


# ---------- device -> gateway ----------


@dataclass
class SessionStart:
    device_id: str
    token: str
    type: Literal["session_start"] = "session_start"


@dataclass
class Utterance:
    """O que o usuário disse, já transcrito no dispositivo (D1).

    Substitui os frames binários + ``audio_end`` do plano original: o STT roda
    antes do fio, então o gateway recebe a frase pronta.
    """

    text: str
    type: Literal["utterance"] = "utterance"


@dataclass
class ToolResult:
    """O que a execução local produziu, de volta para o gateway.

    `value` existe porque nem toda ferramenta é só "deu certo": "quais alarmes
    eu tenho" precisa devolver a lista para o LLM formular a resposta. Continua
    sendo texto -- o gateway não conhece as estruturas do dispositivo, e não
    deve conhecer.
    """

    id: str
    ok: bool
    value: str | None = None
    error: str | None = None
    type: Literal["tool_result"] = "tool_result"


# ---------- gateway -> device ----------


@dataclass
class StateMessage:
    value: State
    #: Opcional: ausente quer dizer "não mudou", e não "neutro". Quem não manda
    #: nada deixa o rosto como está, que é o que todo o gateway faz hoje.
    emotion: Emotion | None = None
    type: Literal["state"] = "state"

    def __post_init__(self) -> None:
        # Arrives off the wire as a plain string; the rest of the code compares
        # against the enum, so normalise once, here.
        self.value = State(self.value)
        if self.emotion is not None:
            self.emotion = Emotion(self.emotion)


@dataclass
class Transcript:
    text: str
    final: bool = True
    role: Literal["user", "assistant"] = "user"
    type: Literal["transcript"] = "transcript"


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    type: Literal["tool_call"] = "tool_call"


@dataclass
class Error:
    message: str
    type: Literal["error"] = "error"


#: Every control message, keyed by its wire ``type``. ``serialization.decode``
#: uses this to rebuild the dataclass, so adding a message means adding it here.
MESSAGES: dict[str, type] = {
    "session_start": SessionStart,
    "utterance": Utterance,
    "tool_result": ToolResult,
    "state": StateMessage,
    "transcript": Transcript,
    "tool_call": ToolCall,
    "error": Error,
}
