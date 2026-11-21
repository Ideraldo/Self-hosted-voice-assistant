"""Uma fila de provedores: se o primeiro não responde, o seguinte responde.

O caso que isto existe para cobrir é o do plano: o modelo principal numa API de
nuvem e, de reserva, o Ollama de casa alcançado por túnel. API de nuvem cai,
estoura cota e bloqueia cartão; nenhum desses é motivo para o aparelho ficar
mudo.

A troca só acontece **antes** do primeiro `Delta`. Depois que alguma coisa foi
entregue ela pode já ter sido falada, e recomeçar a resposta em outro modelo
faria o aparelho dizer duas respostas emendadas. Dali em diante a falha sobe, e
a sessão já sabe o que fazer com ela (`LLMIndisponivel`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from gateway.llm.base import Delta, LLMProvider, Message, Tool

log = logging.getLogger("ideraldinho.llm")


class FallbackProvider:
    def __init__(
        self,
        provedores: list[tuple[str, LLMProvider]],
        primeiro_delta: float = 8.0,
        descanso: float = 60.0,
    ) -> None:
        """`provedores` em ordem de preferência, cada um com o nome que vai ao log.

        `primeiro_delta` é quanto se espera o começo da resposta antes de
        desistir e passar ao seguinte. É separado do timeout do provedor porque
        medem coisas diferentes: aquele é "o modelo morreu?", este é "a pessoa
        ainda está esperando?" -- e numa conversa falada a segunda pergunta
        vence em poucos segundos.

        `descanso` é por quanto tempo quem falhou fica fora da fila. Sem ele,
        com o principal fora do ar, **todo** turno pagaria a espera inteira
        antes de cair no reserva.
        """
        if not provedores:
            raise ValueError("FallbackProvider precisa de ao menos um provedor")
        self._provedores = provedores
        self._primeiro_delta = primeiro_delta
        self._descanso = descanso
        self._fora_ate = [0.0] * len(provedores)

    async def respond(
        self,
        history: list[Message],
        tools: list[Tool],
    ) -> AsyncIterator[Delta]:
        agora = time.monotonic()
        indices = range(len(self._provedores))
        # Todo mundo de castigo é o mesmo que ninguém: tenta-se a fila inteira.
        fila = [i for i in indices if self._fora_ate[i] <= agora] or list(indices)
        falhas: list[str] = []

        for i in fila:
            nome, provedor = self._provedores[i]
            # O último da fila não tem para quem passar a vez, então espera o
            # quanto o próprio provedor aguentar: um Ollama de casa carregando o
            # modelo demora, e demorar ainda é melhor que não responder.
            espera = self._primeiro_delta if i != fila[-1] else None
            fluxo = aiter(provedor.respond(history, tools))
            try:
                primeiro = await asyncio.wait_for(anext(fluxo), timeout=espera)
            except StopAsyncIteration:
                # Resposta vazia é uma resposta, não uma queda.
                return
            except Exception as exc:  # noqa: BLE001 -- cada provedor falha do seu jeito
                esgotou = espera is not None and isinstance(exc, TimeoutError)
                motivo = f"sem resposta em {espera:g}s" if esgotou else repr(exc)
                log.warning("llm %s falhou: %s", nome, motivo)
                falhas.append(f"{nome}: {motivo}")
                self._fora_ate[i] = time.monotonic() + self._descanso
                continue

            self._fora_ate[i] = 0.0
            if falhas:
                log.warning("llm: quem responde este turno e o reserva %s", nome)
            yield primeiro
            async for delta in fluxo:
                yield delta
            return

        raise RuntimeError("nenhum provedor de LLM respondeu -- " + "; ".join(falhas))
