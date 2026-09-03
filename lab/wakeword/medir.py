"""Mede o wake word como ele roda de verdade: em fluxo, e não por janela.

    python -m lab.wakeword.medir

**Por que isto existe separado do treino.** O treino reporta precisão sobre
janelas de 2 s, e esse número engana em uma ordem de grandeza. Em uso, o modelo
não vê 390 janelas: ele vê uma janela nova a cada 80 ms, ou seja **45 mil por
hora**. Uma precisão de 96,7% por janela, se as janelas fossem independentes,
seria mais de mil despertares por hora.

Elas não são independentes -- e é por isso que o número tem que ser medido em
fluxo, com o refratário ligado, exatamente como o `device/` roda. O critério de
aceite da Fase 7 é *falso positivo por hora*, e este script é a única coisa
aqui que fala essa língua.

O que ele **não** substitui: o microfone. Este é áudio sintético tocado em
memória. O número real sai de `python -m scripts.wake --segundos 3600` com a
televisão ligada.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

from device.activation import WakeWord

DADOS = Path("lab/wakeword/dados")
SR = 16000
FRAME = 480  # 30 ms, como a captura entrega


def ler(caminho: Path) -> bytes:
    with wave.open(str(caminho), "rb") as w:
        return w.readframes(w.getnframes())


def correr(wake: WakeWord, audio: bytes) -> tuple[int, float]:
    """Passa o áudio como fluxo e conta ativações. Devolve (acertos, segundos)."""
    wake.reset()
    n = 0
    for i in range(0, len(audio) - FRAME * 2, FRAME * 2):
        if wake.feed(audio[i : i + FRAME * 2]):
            n += 1
    return n, len(audio) / 2 / SR


def main() -> None:
    p = argparse.ArgumentParser(description="falso positivo por hora, em fluxo")
    p.add_argument("--modelo", default="lab/models/wakeword/ideraldinho.onnx")
    p.add_argument("--thresholds", default="0.3,0.5,0.7,0.9,0.95")
    args = p.parse_args()

    positivos = sorted((DADOS / "positivo").glob("*.wav"))
    negativos = sorted((DADOS / "negativo").glob("*.wav"))
    if not positivos:
        raise SystemExit("sem dados -- rode `python -m lab.wakeword.gerar`")

    # Os negativos viram uma fita só: é assim que o aparelho os ouviria, um
    # depois do outro, sem alguém separando em arquivos.
    fita = b"".join(ler(c) for c in negativos)
    horas = len(fita) / 2 / SR / 3600
    print(f"fita de negativos: {horas * 60:.1f} min "
          f"({len(negativos)} trechos)\n")

    print(f"{'thr':>5}  {'acorda':>7}  {'FP/hora':>8}")
    for t in [float(x) for x in args.thresholds.split(",")]:
        wake = WakeWord(args.modelo, threshold=t)
        if not wake.carregar():
            raise SystemExit(f"nao carregou: {args.modelo}")

        acertos = sum(correr(wake, ler(c))[0] > 0 for c in positivos)
        fp, _ = correr(wake, fita)
        print(f"{t:5.2f}  {acertos / len(positivos):6.1%}  {fp / horas:8.1f}")


if __name__ == "__main__":
    main()
