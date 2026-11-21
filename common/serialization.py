"""Encoding/decoding of control messages as JSON.

``decode`` returns the dataclass, not a dict: a typo in a field name fails here,
on the wire boundary, instead of silently three layers deeper on the Pi.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from typing import Any

from common.messages import MESSAGES


def encode(message: Any) -> str:
    if not is_dataclass(message):
        raise TypeError(f"expected a message dataclass, got {type(message)!r}")
    # Campo opcional vazio não vai para o fio. O `decode` do outro lado recusa
    # campo que não conhece, então mandar `"expects_reply": null` em toda
    # mensagem quebraria um par de versão anterior mesmo quando ninguém usa o
    # campo. Omitindo, quem nunca preenche o opcional fala exatamente o
    # protocolo antigo -- e quem recebe reconstrói o `None` pelo default.
    opcionais = {f.name for f in fields(message) if f.default is None}
    payload = {
        k: v for k, v in asdict(message).items() if not (v is None and k in opcionais)
    }
    return json.dumps(payload, ensure_ascii=False)


def decode(raw: str | bytes) -> Any:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("control message must be a JSON object")

    kind = payload.get("type")
    if kind is None:
        raise ValueError("message has no 'type' field")

    cls = MESSAGES.get(kind)
    if cls is None:
        raise ValueError(f"unknown message type {kind!r}")

    known = {f.name for f in fields(cls)} - {"type"}
    unknown = set(payload) - known - {"type"}
    if unknown:
        raise ValueError(f"{kind}: unexpected field(s) {sorted(unknown)}")

    return cls(**{k: v for k, v in payload.items() if k in known})
