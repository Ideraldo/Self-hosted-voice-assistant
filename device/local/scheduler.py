"""O que faz o alarme tocar. Roda no laço de eventos do dispositivo.

Duas decisões que valem explicação:

**Dorme até o próximo, não a cada segundo.** Um laço que acorda de segundo em
segundo para conferir é código mais simples e uma Pi que nunca entra em estado
ocioso. Aqui a tarefa dorme exatamente até o próximo disparo e é acordada por
evento quando alguém agenda algo mais cedo.

**O relógio não é confiável.** A Pi pode não ter RTC com bateria: ela acorda em
1970 e corrige quando o NTP responde. Um salto no relógio entre dois disparos
faria um `sleep` longo demais ou curto demais, então a espera é sempre
recalculada a partir do banco, em fatias, e não computada uma vez só.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from device.local.store import Schedule, ScheduleStore

log = logging.getLogger("ideraldinho.scheduler")

#: Teto de cada soneca. Sem isso, um agendamento para daqui a 8 horas viraria um
#: `sleep(28800)` que não percebe o relógio sendo corrigido no meio.
MAX_SLEEP = 30.0

#: Um agendamento que venceu enquanto o aparelho estava desligado ainda vale a
#: pena anunciar se o atraso for pequeno -- é o caso do processo reiniciando.
#: Muito depois disso, avisar "seu timer de 10 minutos acabou" às três da manhã
#: é pior que ficar calado.
GRACE = 120.0


class Scheduler:
    """Vigia o banco e chama de volta quando chega a hora."""

    def __init__(
        self,
        store: ScheduleStore,
        on_fire: Callable[[Schedule], Awaitable[None]],
    ) -> None:
        self._store = store
        self._on_fire = on_fire
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def notify(self) -> None:
        """Acorda o laço: alguém agendou ou cancelou algo."""
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # O agendador não pode morrer por causa de um disparo com
                # defeito: quem depende dele é o despertador.
                log.exception("falha no agendador; seguindo")
                await asyncio.sleep(1.0)

    async def _tick(self) -> None:
        now = time.time()
        pending = self._store.pending()

        due = [s for s in pending if s.fire_at <= now]
        for item in due:
            atraso = now - item.fire_at
            self._store.mark_fired(item.id)
            if atraso > GRACE:
                log.warning(
                    "%s %s venceu ha %.0fs com o aparelho fora do ar; nao vou anunciar",
                    item.kind, item.id, atraso,
                )
                continue
            await self._on_fire(item)

        if due:
            return  # relê o banco: o disparo pode ter criado ou cancelado coisas

        upcoming = [s for s in pending if s.fire_at > now]
        wait = min(min(s.fire_at for s in upcoming) - now, MAX_SLEEP) if upcoming else MAX_SLEEP

        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(wait, 0.01))
        except asyncio.TimeoutError:
            pass  # chegou a hora de reavaliar
