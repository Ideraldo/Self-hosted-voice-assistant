"""Ouve o microfone e mostra a pontuação do wake word, ao vivo.

    python -m scripts.wake                    # o modelo do .env
    python -m scripts.wake --modelo alexa     # outro, para comparar
    python -m scripts.wake --segundos 3600    # uma hora parada: falso positivo

Existe por dois motivos, e o segundo é o que importa.

O primeiro é achar o threshold. Ele não é um número do código: depende do
microfone, da distância, da sala e de quem fala. A única forma de escolher é ver
a pontuação subir quando você diz o nome e não subir quando você diz outra
coisa.

O segundo é medir o **falso positivo por hora**, que é o critério de aceite da
Fase 7. Deixe rodando com a televisão ligada e ninguém chamando o aparelho: o
que ele contar aqui é o que ele faria de madrugada.
"""

from __future__ import annotations

import argparse
import time

from device.activation import WakeWord
from device.audio.capture import Microphone
from device.config import config


def main() -> None:
    p = argparse.ArgumentParser(description="ouvir o wake word sem subir o aparelho")
    p.add_argument("--modelo", default=config.wake_model)
    p.add_argument("--threshold", type=float, default=config.wake_threshold)
    p.add_argument("--segundos", type=float, default=0.0, help="0 = ate o Ctrl+C")
    args = p.parse_args()

    wake = WakeWord(args.modelo, threshold=args.threshold)
    if not wake.carregar():
        raise SystemExit(f"o modelo {args.modelo!r} nao carregou")

    print(f"modelo: {args.modelo}   threshold: {args.threshold}")
    print("fale o nome. Ctrl+C para sair.\n")

    ativacoes = 0
    inicio = time.monotonic()
    try:
        with Microphone(device=config.input_device) as microphone:
            while True:
                if wake.feed(microphone.read_frame()):
                    ativacoes += 1
                    minutos = (time.monotonic() - inicio) / 60
                    print(f"\n  ATIVOU  ({wake.ultima_pontuacao:.3f})  "
                          f"#{ativacoes} em {minutos:.1f} min")
                else:
                    # Uma barra de 40 casas: dá para ver a pontuação subir
                    # enquanto você fala, que é o que ensina onde pôr o corte.
                    n = int(wake.ultima_pontuacao * 40)
                    print(f"\r  {wake.ultima_pontuacao:5.3f} |{'#' * n:<40}|", end="", flush=True)

                if args.segundos and time.monotonic() - inicio >= args.segundos:
                    break
    except KeyboardInterrupt:
        pass

    horas = (time.monotonic() - inicio) / 3600
    print(f"\n\n{ativacoes} ativacoes em {horas * 60:.1f} min", end="")
    if horas > 0:
        print(f"  =  {ativacoes / horas:.2f} por hora")
    else:
        print()


if __name__ == "__main__":
    main()
