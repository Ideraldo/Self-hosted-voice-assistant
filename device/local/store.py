"""Persistência dos agendamentos locais: SQLite, no disco do dispositivo.

Por que disco e não memória: um alarme tem que sobreviver ao processo. A Pi
reinicia, o processo morre, alguém tira da tomada — e o despertador continua
sendo a razão de o aparelho existir. Guardar em memória seria trocar a única
função que não pode falhar pela mais fácil de implementar.

O horário é gravado como epoch (float, UTC). Guardar "07:30" exigiria decidir
07:30 de qual dia, em qual fuso, e essa decisão pertence a quem cria o
agendamento, não ao armazenamento.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["timer", "alarme", "lembrete"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id         TEXT PRIMARY KEY,
    kind       TEXT    NOT NULL,
    label      TEXT,
    fire_at    REAL    NOT NULL,
    created_at REAL    NOT NULL,
    fired_at   REAL,
    cancelled  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pending ON schedules (fire_at)
    WHERE fired_at IS NULL AND cancelled = 0;
"""


@dataclass(frozen=True)
class Schedule:
    """Um agendamento. Imutável: cancelar e disparar viram escritas, não mutação."""

    id: str
    kind: Kind
    label: str | None
    fire_at: float
    created_at: float
    fired_at: float | None = None
    cancelled: bool = False

    @property
    def pending(self) -> bool:
        return self.fired_at is None and not self.cancelled

    def seconds_left(self, now: float | None = None) -> float:
        return self.fire_at - (now if now is not None else time.time())


class ScheduleStore:
    """Acesso ao banco, com um lock único em volta de cada operação.

    Hoje o agendador e o turno de voz batem aqui da mesma thread: o agendador é
    uma tarefa asyncio no laço do dispositivo, não uma thread. O lock é
    precaução para o dia em que alguma chamada sair do laço -- as operações são
    curtas o bastante para ele ser mais barato, e muito mais fácil de conferir,
    que uma conexão por thread.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False é folga, não necessidade: o agendador é uma
        # tarefa asyncio no mesmo laço (e na mesma thread) do turno. Se um dia
        # alguma chamada for para outra thread, o lock abaixo é o que serializa.
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- escrita -----------------------------------------------------------

    def add(self, kind: Kind, fire_at: float, label: str | None = None) -> Schedule:
        item = Schedule(
            id=uuid.uuid4().hex[:8],
            kind=kind,
            label=label,
            fire_at=fire_at,
            created_at=time.time(),
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO schedules (id, kind, label, fire_at, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (item.id, item.kind, item.label, item.fire_at, item.created_at),
            )
            self._db.commit()
        return item

    def mark_fired(self, schedule_id: str, when: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE schedules SET fired_at = ? WHERE id = ? AND fired_at IS NULL",
                (when if when is not None else time.time(), schedule_id),
            )
            self._db.commit()

    def cancel(self, schedule_id: str) -> bool:
        """Cancela um pendente. Devolve se havia algo para cancelar."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE schedules SET cancelled = 1"
                " WHERE id = ? AND cancelled = 0 AND fired_at IS NULL",
                (schedule_id,),
            )
            self._db.commit()
            return cur.rowcount > 0

    # -- leitura -----------------------------------------------------------

    def pending(self, kind: Kind | None = None) -> list[Schedule]:
        """Os que ainda vão disparar, do mais próximo para o mais distante."""
        sql = (
            "SELECT * FROM schedules WHERE fired_at IS NULL AND cancelled = 0"
            + (" AND kind = ?" if kind else "")
            + " ORDER BY fire_at"
        )
        with self._lock:
            rows = self._db.execute(sql, (kind,) if kind else ()).fetchall()
        return [_row_to_schedule(r) for r in rows]

    def get(self, schedule_id: str) -> Schedule | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return _row_to_schedule(row) if row else None


def _row_to_schedule(row: sqlite3.Row) -> Schedule:
    return Schedule(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        fire_at=row["fire_at"],
        created_at=row["created_at"],
        fired_at=row["fired_at"],
        cancelled=bool(row["cancelled"]),
    )
