"""LLM provider interface (plan section 6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    #: Presente só na mensagem do assistente que **pediu** uma ferramenta.
    #: Guardar isto no histórico não é enfeite: sem ele o modelo relê a conversa,
    #: vê ações viraram prosa, e para de chamar ferramenta no turno seguinte.
    #: Medido: com o registro completo, 3/3 chamadas; sem ele, 0/3 (D22).
    #: Cada entrada é `{"id": ..., "function": {"name": ..., "arguments": {...}}}`.
    tool_calls: list[dict[str, Any]] | None = None
    #: Presente só na mensagem `tool`: o `id` da chamada que este resultado
    #: responde. O Ollama casa os dois pela posição e nunca sentiu falta; as
    #: APIs de nuvem casam pelo id e recusam o histórico inteiro sem ele.
    tool_call_id: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Delta:
    """One streamed chunk: either text or a tool call."""

    text: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    #: O id que o provedor deu à chamada, quando dá. Tem que voltar igual no
    #: histórico: é por ele que a API reconhece a própria chamada. Quem não
    #: fornece (o Ollama, até pouco tempo) deixa `None` e a sessão gera um.
    tool_call_id: str | None = None


class LLMProvider(Protocol):
    def respond(
        self,
        history: list[Message],
        tools: list[Tool],
    ) -> AsyncIterator[Delta]: ...
