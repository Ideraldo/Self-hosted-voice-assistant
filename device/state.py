"""Device state machine (plan section 1): IDLE -> LISTENING -> THINKING -> SPEAKING."""

from __future__ import annotations

import logging
from typing import Callable

from common.messages import Emotion, State

log = logging.getLogger("marcos.state")

ALLOWED: dict[State, set[State]] = {
    State.IDLE: {State.LISTENING},
    State.LISTENING: {State.THINKING, State.IDLE},  # IDLE on timeout
    State.THINKING: {State.SPEAKING, State.IDLE},
    State.SPEAKING: {State.IDLE, State.LISTENING},  # LISTENING on barge-in
}

#: Quem quer saber que o rosto mudou. Recebe (estado, emoção) já validados.
Observer = Callable[[State, Emotion], None]


class StateMachine:
    """A máquina, e a única fonte do que a tela mostra.

    O gancho de observador existe para o rosto (Fase 4). Ele fica *aqui*, e não
    espalhado pelas ~8 chamadas de `transition` do `main.py`, porque todas elas
    já passam por este método: um ponto só, e nenhuma chance de esquecer uma.

    É também a fronteira trocável. O rosto de hoje é uma página em Chromium
    ouvindo um WebSocket local; se ele virar outra coisa, o que muda é quem
    assina aqui -- não o laço do turno.
    """

    def __init__(self) -> None:
        self._state = State.IDLE
        self._emotion = Emotion.NEUTRAL
        self._observers: list[Observer] = []

    @property
    def state(self) -> State:
        return self._state

    @property
    def emotion(self) -> Emotion:
        return self._emotion

    def subscribe(self, observer: Observer) -> None:
        """Registra quem quer acompanhar, e já entrega o estado atual.

        A entrega imediata evita a tela em branco: quem assina no meio da
        execução precisa saber onde a máquina está, não só para onde ela vai.
        """
        self._observers.append(observer)
        self._notify()

    def set_emotion(self, emotion: Emotion) -> Emotion:
        """Muda só o humor, sem mexer no ciclo do turno.

        Não há transição ilegal aqui de propósito: emoção não tem grafo, qualquer
        uma pode suceder qualquer uma.
        """
        self._emotion = emotion
        self._notify()
        return self._emotion

    def transition(self, target: State) -> State:
        if target not in ALLOWED[self._state]:
            raise ValueError(f"illegal transition {self._state} -> {target}")
        self._state = target
        self._notify()
        return self._state

    def _notify(self) -> None:
        for observer in self._observers:
            try:
                observer(self._state, self._emotion)
            except Exception:
                # Um rosto quebrado não pode derrubar o aparelho: a tela é
                # vitrine, o timer é função. Falhar aqui em silêncio seria pior
                # ainda, então fica no log.
                log.exception("observador do estado falhou")
