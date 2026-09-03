"""Sobe só o rosto, sem microfone, sem STT, sem gateway.

    python -m scripts.rosto            # e digite: idle, listening, happy, ...

Existe porque desenhar o rosto pelo caminho normal significaria falar com o
aparelho a cada ajuste de CSS -- carregar o Whisper, gravar uma frase e esperar
o turno, para ver uma pálpebra. Aqui o estado vem do teclado e a página é a
mesma. Também é o que vai medir o custo da animação na Pi, quando houver Pi:
o rosto rodando sozinho, sem nada disputando os núcleos.
"""

from __future__ import annotations

import asyncio
import sys

from common.messages import Emotion, State
from device.config import config
from device.rosto import start_face
from device.state import StateMachine

#: O ciclo do turno, em ordem. Serve para caminhar até um estado qualquer sem
#: violar o grafo de `device/state.py` -- que não tem atalhos de propósito, e
#: cuja única saída de SPEAKING para IDLE é seguindo em frente.
CICLO = {
    State.IDLE: State.LISTENING,
    State.LISTENING: State.THINKING,
    State.THINKING: State.SPEAKING,
    State.SPEAKING: State.IDLE,
}

ESTADOS = {e.value: e for e in State}
EMOCOES = {e.value: e for e in Emotion}


async def main() -> None:
    face = await start_face(config.face_port)
    if face is None:
        sys.exit(f"a porta {config.face_port} esta ocupada")

    machine = StateMachine()
    machine.subscribe(face.publish)
    print(f"rosto: {face.url}\n")
    print(f"estados: {', '.join(ESTADOS)}")
    print(f"emocoes: {', '.join(EMOCOES)}")
    print("ctrl+c para sair\n")

    try:
        while True:
            palavra = (await asyncio.to_thread(input, "> ")).strip().lower()
            if palavra in EMOCOES:
                machine.set_emotion(EMOCOES[palavra])
            elif palavra in ESTADOS:
                alvo = ESTADOS[palavra]
                # Aqui a gente quer ver o rosto, não validar o ciclo -- então
                # anda pelo ciclo até chegar no alvo, em vez de pular direto e
                # levar o ValueError que a máquina deve mesmo levantar.
                while machine.state is not alvo:
                    machine.transition(CICLO[machine.state])
            elif palavra:
                print("  nao conheco")
    finally:
        await face.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\ntchau.")
