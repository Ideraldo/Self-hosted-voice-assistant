"""Mede tradutores pt-BR <-> en em CTranslate2, sobre TEXTO.

    # cortar a entrada em orações antes de traduzir (ver Translator.translate)
    python -m lab.traducao.run_traducao --model romance-en --direction pt-en --split

    # um modelo, um sentido, 4 threads e depois 1
    python -m lab.traducao.run_traducao --model romance-en --direction pt-en

    # a comparação inteira
    python -m lab.traducao.run_traducao --model all --direction both --threads 4,1

    # só o resumo, e guardar as saídas para ler depois
    python -m lab.traducao.run_traducao --model big-en-pt --direction en-pt --quiet --save

    # escolher a variante do português na saída
    python -m lab.traducao.run_traducao --model big-en-pt --direction en-pt --tag por

O que isto mede: o motor de tradução sozinho, com a frase já escrita certa. O
que NÃO mede: o erro que o Whisper entrega antes (a entrada real é transcrição,
sem pontuação confiável e com palavra trocada) nem a Pi. Texto limpo elogia todo
modelo, do mesmo jeito que áudio sintético elogia todo STT -- trate como triagem.

Cada combinação (modelo, sentido, threads) roda num processo filho: é o único
jeito de a memória (RSS) de um modelo não contaminar a medida do seguinte.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lab.traducao.frases import AQUECIMENTO, MARCAS_PT_PT, PARES, Par
from lab.traducao.modelos import MODELOS, NLLB_LANG, Modelo

LAB = Path(__file__).resolve().parent.parent
MODELS = LAB / "models" / "traducao"  # fora do git (D12)
OUT = LAB / "out" / "traducao"  # fora do git

DIRECTIONS = ("pt-en", "en-pt")

#: Fim de oração: pontuação final, espaço, e a próxima começa com maiúscula.
#: A maiúscula é o que impede "R$ 1.249,90" e "7h30" de serem cortados.
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý])")


# --------------------------------------------------------------------------
# medidas pequenas
# --------------------------------------------------------------------------


def rss_mb() -> float | None:
    """Memória residente do processo. psutil porque a bancada roda em Windows."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().memory_info().rss / 1e6


def folder_mb(path: Path) -> float:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def percentile(values: list[float], q: float) -> float:
    """Percentil por interpolação linear; com ~60 frases, p90 são as 6 piores."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def pt_pt_marks(text: str) -> list[str]:
    """Quais marcas de português europeu aparecem numa saída en->pt."""
    lowered = text.lower()
    return [label for pattern, label in MARCAS_PT_PT.items() if re.search(pattern, lowered)]


# --------------------------------------------------------------------------
# o tradutor: ctranslate2 + sentencepiece, sem transformers
# --------------------------------------------------------------------------


class Translator:
    """Carrega um modelo convertido e traduz uma frase por vez.

    De propósito só ctranslate2 e sentencepiece: é o que iria para o
    ``device/``. O ``transformers`` (e o torch que ele puxa) só é necessário
    para *converter*, e a conversão acontece no PC.
    """

    def __init__(
        self, model: Modelo, direction: str, directory: Path, threads: int,
        compute_type: str, beam: int, tag: str | None,
    ) -> None:
        import ctranslate2
        import sentencepiece

        self.model = model
        self.beam = beam
        self.source_lang, self.target_lang = direction.split("-")
        self.translator = ctranslate2.Translator(
            str(directory), device="cpu", compute_type=compute_type,
            inter_threads=1, intra_threads=threads,
        )
        if model.familia == "nllb":
            spm = sentencepiece.SentencePieceProcessor(
                model_file=str(directory / "sentencepiece.bpe.model")
            )
            self.encoder = self.decoder = spm
            self.prefix = [NLLB_LANG[self.source_lang]]
            self.target_prefix = [NLLB_LANG[self.target_lang]]
        else:
            self.encoder = sentencepiece.SentencePieceProcessor(
                model_file=str(directory / "source.spm")
            )
            self.decoder = sentencepiece.SentencePieceProcessor(
                model_file=str(directory / "target.spm")
            )
            # A etiqueta >>xx<< é um token inteiro do vocabulário; não passa
            # pelo sentencepiece, que a quebraria em pedaços.
            self.prefix = [f">>{tag}<<"] if tag else []
            self.target_prefix = None

    def translate(self, text: str, split: bool = False) -> tuple[str, int]:
        """Devolve a tradução e quantos tokens o decoder gerou.

        ``split`` corta a entrada em orações e traduz uma por vez. Não é
        enfeite: estes modelos foram treinados com uma oração por linha, e
        diante de duas ("Está caro. Tem desconto?") costumam traduzir uma e
        jogar a outra fora, sem erro nenhum.
        """
        if split:
            parts = [self.translate(part) for part in SENTENCE_END.split(text) if part]
            return " ".join(p[0] for p in parts), sum(p[1] for p in parts)
        tokens = self.prefix + self.encoder.encode(text, out_type=str) + ["</s>"]
        result = self.translator.translate_batch(
            [tokens],
            target_prefix=[self.target_prefix] if self.target_prefix else None,
            beam_size=self.beam,
            max_decoding_length=256,
        )
        output = result[0].hypotheses[0]
        if self.target_prefix:
            output = output[len(self.target_prefix):]
        return self.decoder.decode(output), len(output)


# --------------------------------------------------------------------------
# uma medição = um processo
# --------------------------------------------------------------------------


def select(keys: str) -> list[Par]:
    if keys == "all":
        return list(PARES)
    wanted = keys.split(",")
    found = [p for p in PARES if p.chave in wanted or p.categoria in wanted]
    if not found:
        raise SystemExit(f"nenhuma frase casa com '{keys}' (chave ou categoria)")
    return found


def measure(args: argparse.Namespace) -> dict:
    """Roda dentro do processo filho: carrega, aquece, traduz, pontua."""
    model = MODELOS[args.model]
    directory = Path(args.models_dir) / model.pasta
    threads = int(args.threads)
    source_lang, target_lang = args.direction.split("-")
    pairs = select(args.phrase)

    before = rss_mb()
    started = time.perf_counter()
    translator = Translator(
        model, args.direction, directory, threads, args.compute, args.beam, args.tag
    )
    load = time.perf_counter() - started

    # Aquecimento fora da conta: a primeira chamada paga alocação de buffers.
    warmup = next(p for p in PARES if p.chave == AQUECIMENTO)
    translator.translate(getattr(warmup, source_lang))

    rows = []
    for pair in pairs:
        source, reference = getattr(pair, source_lang), getattr(pair, target_lang)
        times = []
        for _ in range(args.repeat):
            started = time.perf_counter()
            hypothesis, tokens = translator.translate(source, args.split)
            times.append(time.perf_counter() - started)
        rows.append({
            "chave": pair.chave, "categoria": pair.categoria, "origem": source,
            "referencia": reference, "saida": hypothesis, "tokens": tokens,
            # mediana das repetições: tira o soluço do sistema, não o custo real
            "segundos": statistics.median(times),
        })
    after = rss_mb()

    from sacrebleu.metrics import CHRF

    chrf = CHRF()
    for row in rows:
        row["chrf"] = chrf.sentence_score(row["saida"], [row["referencia"]]).score
        row["pt_pt"] = pt_pt_marks(row["saida"]) if target_lang == "pt" else []
    corpus = chrf.corpus_score([r["saida"] for r in rows], [[r["referencia"] for r in rows]])

    seconds = [r["segundos"] for r in rows]
    return {
        "modelo": args.model, "repo": model.repo, "sentido": args.direction,
        "etiqueta": args.tag, "threads": threads, "compute": args.compute,
        "beam": args.beam, "split": args.split, "frases": len(rows),
        "disco_mb": folder_mb(directory),
        "carga_s": load,
        "ram_mb": None if before is None else after - before,
        "mediana_ms": statistics.median(seconds) * 1000,
        "p90_ms": percentile(seconds, 0.9) * 1000,
        "max_ms": max(seconds) * 1000,
        "tokens_s": sum(r["tokens"] for r in rows) / sum(seconds),
        "chrf": corpus.score,
        "frases_pt_pt": sum(1 for r in rows if r["pt_pt"]),
        "linhas": rows,
    }


def run_child(args: argparse.Namespace, model: str, direction: str, threads: int,
              tag: str | None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "resultado.json"
        command = [
            sys.executable, "-m", "lab.traducao.run_traducao", "--worker", str(target),
            "--model", model, "--direction", direction, "--threads", str(threads),
            "--compute", args.compute, "--beam", str(args.beam),
            "--repeat", str(args.repeat), "--phrase", args.phrase,
            "--models-dir", str(args.models_dir),
        ]
        if tag:
            command += ["--tag", tag]
        if args.split:
            command += ["--split"]
        # Sem isto o OpenMP pode ignorar intra_threads e a linha "1 thread" mente.
        env = dict(os.environ, OMP_NUM_THREADS=str(threads))
        subprocess.run(command, check=True, env=env)
        return json.loads(target.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# saída
# --------------------------------------------------------------------------


def label(result: dict) -> str:
    tag = f" >>{result['etiqueta']}<<" if result["etiqueta"] else ""
    return f"{result['modelo']}{tag}"


def show_rows(result: dict) -> None:
    for row in result["linhas"]:
        mark = f"  [pt-PT: {', '.join(row['pt_pt'])}]" if row["pt_pt"] else ""
        print(f"  [{row['chave']:<16}] chrF {row['chrf']:5.1f}  {row['segundos'] * 1000:6.0f} ms{mark}")
        print(f"        origem:     {row['origem']}")
        print(f"        referencia: {row['referencia']}")
        print(f"        saida:      {row['saida']}")


def show_worst(result: dict, count: int) -> None:
    print(f"\n  as {count} piores por chrF:")
    for row in sorted(result["linhas"], key=lambda r: r["chrf"])[:count]:
        print(f"    {row['chrf']:5.1f}  [{row['chave']}] {row['origem']}")
        print(f"           esperado: {row['referencia']}")
        print(f"           saiu:     {row['saida']}")


def summary_line(result: dict) -> str:
    ram = "   n/d" if result["ram_mb"] is None else f"{result['ram_mb']:6.0f}"
    marks = f"{result['frases_pt_pt']:>5}" if result["sentido"] == "en-pt" else "    -"
    return (
        f"{label(result):<24} {result['sentido']:<6} {result['threads']:>2} "
        f"{result['disco_mb']:>6.0f} {result['carga_s']:>6.2f} {ram} "
        f"{result['mediana_ms']:>7.0f} {result['p90_ms']:>7.0f} "
        f"{result['tokens_s']:>7.0f} {result['chrf']:>6.1f} {marks}"
    )


HEADER = (
    f"{'modelo':<24} {'sent.':<6} {'th':>2} {'MB':>6} {'carga':>6} {'RAM MB':>6} "
    f"{'med ms':>7} {'p90 ms':>7} {'tok/s':>7} {'chrF':>6} {'pt-PT':>5}"
)


def save(result: dict) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"_{result['etiqueta']}" if result["etiqueta"] else ""
    split = "_split" if result["split"] else ""
    path = OUT / f"{result['modelo']}{tag}_{result['sentido']}_t{result['threads']}{split}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="bancada de traducao (CTranslate2)")
    parser.add_argument("--model", required=True, help=f"{'|'.join(MODELOS)}|all, comma separated")
    parser.add_argument("--direction", default="both", help="pt-en|en-pt|both")
    parser.add_argument("--threads", default="4,1", help="intra-op threads, comma separated")
    parser.add_argument("--compute", default="int8", help="CTranslate2 compute type")
    parser.add_argument("--beam", type=int, default=4, help="beam size (1 = greedy)")
    parser.add_argument("--split", action="store_true",
                        help="translate one sentence at a time (models drop the 2nd otherwise)")
    parser.add_argument("--tag", help="target-language tag for en->pt (ex: pob, pt_br)")
    parser.add_argument("--repeat", type=int, default=3, help="runs per sentence (median kept)")
    parser.add_argument("--phrase", default="all", help="key(s) or category(ies), comma separated")
    parser.add_argument(
        "--models-dir", default=os.environ.get("LAB_TRADUCAO_MODELS", str(MODELS)),
        help="where the converted models live (default: lab/models/traducao)",
    )
    parser.add_argument("--worst", type=int, default=5, help="how many worst sentences to list")
    parser.add_argument("--quiet", action="store_true", help="only the summary line per run")
    parser.add_argument("--save", action="store_true", help="write outputs to lab/out/traducao")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        result = measure(args)
        Path(args.worker).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return

    names = list(MODELOS) if args.model == "all" else args.model.split(",")
    for name in names:
        if name not in MODELOS:
            raise SystemExit(f"modelo desconhecido: {name}\n  opcoes: {', '.join(MODELOS)}")
    directions = DIRECTIONS if args.direction == "both" else (args.direction,)
    threads = [int(t) for t in args.threads.split(",")]

    print(f"\nfonte: texto (lab/traducao/frases.py)   compute: {args.compute}   beam: {args.beam}"
          f"   oracoes separadas: {'sim' if args.split else 'nao'}")
    print(f"nucleos nesta maquina: {os.cpu_count()}  -- x86 de bancada, NAO e numero de Pi")

    results = []
    for name in names:
        model = MODELOS[name]
        directory = Path(args.models_dir) / model.pasta
        if not directory.exists():
            print(f"\n  {name}: nao encontrei {directory} -- converta antes (lab/traducao/README.md)")
            continue
        for direction in directions:
            if direction not in model.sentidos:
                continue
            tag = None
            if direction == "en-pt" and model.etiquetas:
                tag = args.tag or model.etiquetas[0]
                if tag not in model.etiquetas:
                    raise SystemExit(
                        f"{name} nao tem a etiqueta '{tag}'; tem: {', '.join(model.etiquetas)}"
                    )
            for index, count in enumerate(threads):
                result = run_child(args, name, direction, count, tag)
                results.append(result)
                kind = "candidato" if model.candidato else "TETO, nao-candidato"
                print(f"\n{label(result)}  {direction}  {count} thread(s)  [{kind}]")
                # As traduções não mudam com o número de threads: mostra uma vez.
                if index == 0 and not args.quiet:
                    show_rows(result)
                    show_worst(result, args.worst)
                print(f"  carga {result['carga_s']:.2f}s   mediana {result['mediana_ms']:.0f} ms   "
                      f"p90 {result['p90_ms']:.0f} ms   pior {result['max_ms']:.0f} ms   "
                      f"{result['tokens_s']:.0f} tok/s   chrF {result['chrf']:.1f}")
                if args.save:
                    print(f"  salvo em {save(result)}")

    if results:
        print(f"\n{HEADER}")
        print("-" * len(HEADER))
        for result in results:
            print(summary_line(result))
        print("\npt-PT = frases da saida com marca de portugues europeu (so en-pt)")
    print("\nanote em lab/RESULTS.md\n")


if __name__ == "__main__":
    main()
