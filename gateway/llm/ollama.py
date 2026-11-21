"""Ollama provider: a model running locally, no API key and no network.

Good enough to build the whole pipeline against. The plan's target is a cloud
model behind this same interface (section 6) -- when that arrives, only
``build_provider`` changes.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from gateway.llm.base import Delta, Message, Tool


def _wire(message: Message) -> dict[str, Any]:
    """Uma mensagem do histórico no formato que o Ollama espera.

    A mensagem do assistente que pediu ferramenta carrega `tool_calls` e
    normalmente tem `content` vazio -- é assim que os modelos foram treinados, e
    é o que faz o turno seguinte continuar usando ferramentas.

    O id da chamada fica de fora, nos dois lados do par. O Ollama casa
    resultado e chamada pela posição, e as versões recentes até aceitam `id` e
    `tool_call_id` -- mas campo novo aqui pode mudar o prompt que o template
    monta, e o formato que a D22 mediu (3/3) é este, sem id. Só `function`
    viaja: é o que toda versão do `/api/chat` aceita.
    """
    corpo: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        corpo["tool_calls"] = [{"function": c["function"]} for c in message.tool_calls]
    return corpo


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        think: bool | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/chat"
        self._model = model
        self._timeout = timeout
        # Modelos de raciocínio (o qwen3 é um) pensam por padrão. Para voz isso
        # é ruim duas vezes: multiplica a latência do turno, e o raciocínio pode
        # vazar como texto -- que aqui vira o aparelho **falando** "Okay, the
        # user is asking...". Medido: qwen3:4b vaza; o 8b com `think=False` não.
        self._think = think

    async def respond(
        self,
        history: list[Message],
        tools: list[Tool],
    ) -> AsyncIterator[Delta]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_wire(m) for m in history],
            "stream": True,
        }
        if self._think is not None:
            payload["think"] = self._think
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", self._url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise RuntimeError(f"ollama: {chunk['error']}")

                    message = chunk.get("message") or {}
                    for call in message.get("tool_calls") or []:
                        function = call.get("function") or {}
                        args = function.get("arguments") or {}
                        if isinstance(args, str):
                            args = json.loads(args)
                        # Versões recentes do Ollama mandam `id`; as antigas
                        # não, e aí a sessão gera um.
                        yield Delta(
                            tool_name=function.get("name"),
                            tool_args=args,
                            tool_call_id=call.get("id") or None,
                        )

                    # `thinking` vem em campo separado quando o modelo pensa.
                    # Nunca vira fala: é rascunho, não resposta.
                    text = message.get("content")
                    if text:
                        yield Delta(text=text)

                    if chunk.get("done"):
                        break
