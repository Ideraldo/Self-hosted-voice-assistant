"""Treina o classificador do wake word e exporta o .onnx que o aparelho carrega.

    python -m lab.wakeword.treinar

O openWakeWord é dois modelos em série, e só o segundo é nosso: um extrator
congelado transforma áudio em embeddings de (16, 96), e em cima disso roda um
classificador pequeno que diz "é o nome" ou "não é". Treinar o wake word é
treinar **só o segundo** -- que é por isso que dá para fazer isto numa tarde,
com 5 mil exemplos, em vez das dezenas de milhares que a receita oficial pede.

A saída é um `.onnx` de entrada (1, 16, 96) e saída (1, 1), que é exatamente o
formato que o `openwakeword.model.Model` carrega por caminho de arquivo. Nada no
`device/` precisa saber que este modelo é caseiro.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np
import torch
from torch import nn

DADOS = Path("lab/wakeword/dados")
SAIDA = Path("lab/models/wakeword")

#: A forma que o extrator devolve para 2 s de áudio, e que o classificador
#: consome. Ver `lab/wakeword/gerar.py`.
FORMA = (16, 96)


class Classificador(nn.Module):
    """A mesma forma dos modelos que vêm com o openWakeWord: achatar e duas
    camadas densas. Rede pequena não é economia, é a decisão certa -- ela vai
    rodar a cada 80 ms para sempre, num aparelho que também transcreve."""

    def __init__(self, dim: int = 96) -> None:
        super().__init__()
        self.rede = nn.Sequential(
            nn.Flatten(),
            nn.Linear(FORMA[0] * FORMA[1], dim),
            nn.LayerNorm(dim),
            nn.ReLU(),
            nn.Linear(dim, dim),
            nn.LayerNorm(dim),
            nn.ReLU(),
            nn.Linear(dim, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return self.rede(x)


def ler_wav(caminho: Path) -> np.ndarray:
    with wave.open(str(caminho), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def embutir(caminhos: list[Path], af, lote: int = 128) -> np.ndarray:
    """Áudio -> embeddings, em lotes. É a parte cara, e roda uma vez só."""
    clipes = np.stack([ler_wav(c) for c in caminhos])
    return np.array(af.embed_clips(clipes, batch_size=lote))


def carregar(af, cache: Path, refazer: bool) -> tuple[np.ndarray, np.ndarray]:
    """Os embeddings de tudo, com cache em disco.

    O cache existe porque extrair é 10 vezes mais caro que treinar: sem ele,
    mexer numa camada da rede custaria minutos de extração de novo.
    """
    if cache.exists() and not refazer:
        d = np.load(cache)
        print(f"cache: {cache}")
        return d["X"], d["y"]

    pos = sorted((DADOS / "positivo").glob("*.wav"))
    neg = sorted((DADOS / "negativo").glob("*.wav"))
    if not pos or not neg:
        raise SystemExit("sem dados -- rode `python -m lab.wakeword.gerar` antes")

    print(f"extraindo embeddings de {len(pos)} positivos e {len(neg)} negativos...")
    Xp, Xn = embutir(pos, af), embutir(neg, af)
    X = np.concatenate([Xp, Xn]).astype(np.float32)
    y = np.concatenate([np.ones(len(Xp)), np.zeros(len(Xn))]).astype(np.float32)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, X=X, y=y)
    return X, y


def separar(X, y, semente: int, fracao: float = 0.2):
    """Divide em treino e teste, estratificado.

    Estratificado importa aqui mais que de costume: os negativos são 10 vezes
    mais numerosos, e uma divisão aleatória pode deixar o teste quase sem
    positivo -- e aí a métrica de recall vira ruído.
    """
    rng = np.random.default_rng(semente)
    tr, te = [], []
    for classe in (0.0, 1.0):
        idx = np.flatnonzero(y == classe)
        rng.shuffle(idx)
        corte = int(len(idx) * fracao)
        te.extend(idx[:corte])
        tr.extend(idx[corte:])
    return np.array(tr), np.array(te)


def avaliar(modelo, X, y, threshold: float) -> dict:
    modelo.eval()
    with torch.no_grad():
        p = modelo(torch.from_numpy(X)).numpy().ravel()
    prev = p >= threshold
    vp = int(((prev == 1) & (y == 1)).sum())
    fp = int(((prev == 1) & (y == 0)).sum())
    fn = int(((prev == 0) & (y == 1)).sum())
    vn = int(((prev == 0) & (y == 0)).sum())
    return {
        "recall": vp / max(vp + fn, 1),
        "precisao": vp / max(vp + fp, 1),
        "falsos_positivos": fp,
        "negativos": fp + vn,
    }


def exportar(modelo, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    modelo.eval()
    torch.onnx.export(
        modelo,
        torch.zeros(1, *FORMA),
        str(destino),
        input_names=["x"],
        output_names=["y"],
        opset_version=13,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="treina o wake word")
    p.add_argument("--epocas", type=int, default=120)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--semente", type=int, default=7)
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--nome", default="ideraldinho")
    p.add_argument("--refazer-cache", action="store_true")
    args = p.parse_args()

    torch.manual_seed(args.semente)
    from openwakeword.utils import AudioFeatures

    X, y = carregar(AudioFeatures(), DADOS / "embeddings.npz", args.refazer_cache)
    tr, te = separar(X, y, args.semente)
    print(f"treino: {len(tr)}   teste: {len(te)}   positivos no teste: {int(y[te].sum())}")

    Xtr = torch.from_numpy(X[tr])
    ytr = torch.from_numpy(y[tr]).unsqueeze(1)
    # Os negativos são ~10x mais numerosos. Sem peso, a rede aprende a dizer
    # "não" sempre e acerta 90% -- que é a métrica mentindo, não o modelo
    # funcionando.
    peso = float((y[tr] == 0).sum() / max((y[tr] == 1).sum(), 1))
    print(f"peso do positivo: {peso:.1f}")

    modelo = Classificador()
    opt = torch.optim.AdamW(modelo.parameters(), lr=args.lr)
    perda = nn.BCELoss(reduction="none")

    for e in range(args.epocas):
        modelo.train()
        opt.zero_grad()
        saida = modelo(Xtr)
        pesos = torch.where(ytr > 0.5, peso, 1.0)
        custo = (perda(saida, ytr) * pesos).mean()
        custo.backward()
        opt.step()
        if (e + 1) % 20 == 0:
            m = avaliar(modelo, X[te], y[te], args.threshold)
            print(f"  epoca {e + 1:3d}  custo {custo.item():.4f}  "
                  f"recall {m['recall']:.3f}  precisao {m['precisao']:.3f}  "
                  f"FP {m['falsos_positivos']}/{m['negativos']}")

    print("\n-- no conjunto de teste, por threshold --")
    for t in (0.3, 0.5, 0.7, 0.9):
        m = avaliar(modelo, X[te], y[te], t)
        print(f"  {t:.1f}  recall {m['recall']:.3f}  precisao {m['precisao']:.3f}  "
              f"falso positivo {m['falsos_positivos']}/{m['negativos']}")

    destino = SAIDA / f"{args.nome}.onnx"
    exportar(modelo, destino)
    print(f"\nsalvo: {destino}")
    print(f"para usar:  WAKE_ENABLED=1  WAKE_MODEL={destino}")


if __name__ == "__main__":
    main()
