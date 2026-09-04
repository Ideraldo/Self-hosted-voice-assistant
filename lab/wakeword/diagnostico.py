"""Quais negativos enganam o modelo, e quais palavras.

    python -m lab.wakeword.diagnostico

`medir.py` diz **quanto** o modelo erra; este diz **onde**. A diferença decidiu
o projeto uma vez: o modelo v1 tinha 71-101 falsos positivos por hora, e o
número sozinho não dizia nada além de "está ruim". A quebra por palavra disse
tudo -- "Everaldinho" acordava o aparelho em 83% das vezes e "Ideraldo", que é o
nome do dono da casa e divide o começo inteiro, em 0%. O modelo tinha aprendido
`-aldinho` e ignorava o começo da palavra.

Nenhum ajuste de threshold conserta isso, e mais épocas de treino também não. O
conserto foi mudar o que se ensina, e foi este script que apontou onde.

Ele também separa o número **realista** do número da fita adversária. A fita é
propositalmente patológica -- centenas de "Everaldinho" e "Reginaldinho" em
sequência --, e nenhuma sala se parece com ela. O que se parece é fala comum
mais ruído, e é esse recorte que vale contra o critério da Fase 7.
"""

from __future__ import annotations

import argparse
import collections
import wave
from pathlib import Path

from device.activation import WakeWord
from lab.wakeword.gerar import ADVERSARIAS

DADOS = Path("lab/wakeword/dados/negativo")
POSITIVOS = Path("lab/wakeword/dados/positivo")
FRAME = 960  # 30 ms em bytes, como a captura entrega


def ler(caminho: Path) -> bytes:
    with wave.open(str(caminho), "rb") as w:
        return w.readframes(w.getnframes())


def dispara(wake: WakeWord, audio: bytes) -> bool:
    wake.reset()
    return any(wake.feed(audio[i : i + FRAME]) for i in range(0, len(audio) - FRAME, FRAME))


def main() -> None:
    p = argparse.ArgumentParser(description="onde o wake word erra")
    p.add_argument("--modelo", default="lab/models/wakeword/ideraldinho.onnx")
    p.add_argument("--threshold", type=float, default=0.5)
    args = p.parse_args()

    wake = WakeWord(args.modelo, threshold=args.threshold)
    if not wake.carregar():
        raise SystemExit(f"nao carregou: {args.modelo}")

    positivos = sorted(POSITIVOS.glob("*.wav"))
    acordou = sum(dispara(wake, ler(c)) for c in positivos)
    print(f"threshold {args.threshold}\n")
    print(f"acorda quando chamado: {acordou}/{len(positivos)} = {acordou / len(positivos):.1%}\n")

    disp, tot = collections.Counter(), collections.Counter()
    for c in sorted(DADOS.glob("adv_*.wav")):
        palavra = ADVERSARIAS[int(c.stem.split("_")[-1]) % len(ADVERSARIAS)]
        tot[palavra] += 1
        disp[palavra] += dispara(wake, ler(c))

    print("acorda sem ser chamado, por palavra:")
    for palavra, n in sorted(tot.items(), key=lambda kv: -disp[kv[0]] / kv[1]):
        if disp[palavra]:
            print(f"  {palavra:16} {disp[palavra]:4d}/{n:4d}  {disp[palavra] / n:6.1%}")
    mudas = [p for p in tot if not disp[p]]
    print(f"  ({len(mudas)} palavras nunca acordaram: {', '.join(sorted(mudas))})")

    # O recorte realista: sem a fita adversaria, que nenhuma sala reproduz.
    reais = sorted(DADOS.glob("real_*.wav")) + sorted(DADOS.glob("ruido_*.wav"))
    fp = sum(dispara(wake, ler(c)) for c in reais)
    horas = len(reais) * 2 / 3600
    print(f"\nfala comum + ruido: {fp} em {horas * 60:.0f} min = "
          f"{fp / horas:.2f} por hora   (criterio da Fase 7: < 1)")


if __name__ == "__main__":
    main()
