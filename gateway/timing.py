"""Per-stage timestamps, wired in from day one (plan section 11).

The latency budget is a product metric, not an optimisation to do later, so
every turn logs where its milliseconds went.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("ideraldinho.timing")


@dataclass
class Turn:
    """Marks named instants within one interaction and reports the gaps."""

    start: float = field(default_factory=time.perf_counter)
    marks: list[tuple[str, float]] = field(default_factory=list)

    def mark(self, name: str) -> None:
        self.marks.append((name, time.perf_counter()))

    def report(self) -> str:
        previous = self.start
        parts = []
        for name, at in self.marks:
            parts.append(f"{name}={(at - previous) * 1000:.0f}ms")
            previous = at
        total = (previous - self.start) * 1000
        line = " ".join(parts) + f" total={total:.0f}ms"
        log.info("turn %s", line)
        return line
