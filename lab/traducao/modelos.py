"""Os candidatos a tradutor, e o que cada um sabe sobre português do Brasil.

Como em ``lab/registry.py``, o metadado mora aqui, ao lado do nome: listar o que
existe não pode importar ctranslate2 só para imprimir uma tabela.

O que foi conferido no Hugging Face em 2026-11 (não confie na memória -- os
nomes "óbvios" não existem):

  NÃO existem   opus-mt-pt-en, opus-mt-en-pt, opus-mt-tc-big-pt-en
  existem       opus-mt-ROMANCE-en, opus-mt-roa-en, opus-mt-tc-bible-big-roa-en
                opus-mt-en-ROMANCE, opus-mt-en-roa, opus-mt-tc-big-en-pt

``opus-mt-roa-en`` não está na lista abaixo porque é o **mesmo arquivo** que o
``opus-mt-ROMANCE-en``: o ``model.bin`` convertido tem o mesmo md5 e as 62
saídas são idênticas. Medir os dois é medir um duas vezes. (O par inverso,
``en-roa`` x ``en-ROMANCE``, é de fato dois modelos diferentes.)

Ou seja: não há Marian dedicado ao par pt<->en na geração "base". O português
entra sempre como um idioma dentro de um modelo românico, e o único modelo só
de português é o ``tc-big-en-pt`` (en->pt, 3x maior).

A etiqueta de idioma-alvo é o que separa pt-BR de pt-PT, e ela só existe nos
modelos de saída múltipla (en->pt). Vai como primeiro token da frase de origem:

  opus-mt-en-ROMANCE     >>pt_BR<<  >>pt_br<<  >>pt<<  >>pt_PT<<
  opus-mt-en-roa         >>por<<               (sem distinção de variante)
  opus-mt-tc-big-en-pt   >>pob<<  >>por<<      (pob = brasileiro)

No ``en-ROMANCE`` as quatro etiquetas não são sinônimos -- medido aqui:

  >>pt_BR<<   brasileiro, literal; mantém as duas orações de uma entrada dupla
  >>pt_br<<   brasileiro com cara de legenda; enxuga e costuma cortar a 2a oração
  >>pt<<      português europeu ("casa de banho", "sumo", "dás-me")
  >>pt_PT<<   QUEBRADA: sai uma mistura de espanhol, italiano e português

No pt->en não há etiqueta: o modelo aceita qualquer português na entrada.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Códigos do NLLB (FLORES-200). O NLLB tem um só "por_Latn" -- não distingue
#: variante, e isso aparece nas saídas.
NLLB_LANG = {"pt": "por_Latn", "en": "eng_Latn", "zh": "zho_Hans"}


@dataclass(frozen=True)
class Modelo:
    repo: str  # id no Hugging Face
    pasta: str  # nome da pasta convertida dentro de --models-dir
    familia: str  # marian | nllb
    sentidos: tuple[str, ...]  # "pt-en", "en-pt"
    #: Etiquetas de idioma-alvo aceitas no en->pt; a primeira é o padrão.
    etiquetas: tuple[str, ...] = ()
    candidato: bool = True  # False = só teto de comparação, não vai para a Pi
    nota: str = ""
    arquivos: tuple[str, ...] = field(default=("source.spm", "target.spm", "vocab.json"))


MODELOS: dict[str, Modelo] = {
    # --- pt -> en -------------------------------------------------------
    "romance-en": Modelo(
        "Helsinki-NLP/opus-mt-ROMANCE-en", "opus-mt-ROMANCE-en-int8", "marian",
        ("pt-en",), nota="base 2020; romanicas na entrada (= opus-mt-roa-en)",
    ),
    "big-roa-en": Modelo(
        "Helsinki-NLP/opus-mt-tc-bible-big-roa-en", "opus-mt-tc-bible-big-roa-en-int8",
        "marian", ("pt-en",), nota="transformer-big 2024; 3x o tamanho",
    ),
    # --- en -> pt -------------------------------------------------------
    "en-romance": Modelo(
        "Helsinki-NLP/opus-mt-en-ROMANCE", "opus-mt-en-ROMANCE-int8", "marian",
        ("en-pt",), etiquetas=("pt_BR", "pt_br", "pt", "pt_PT"),
        nota="base 2020; a etiqueta escolhe a variante (e o estilo)",
    ),
    "en-roa": Modelo(
        "Helsinki-NLP/opus-mt-en-roa", "opus-mt-en-roa-int8", "marian",
        ("en-pt",), etiquetas=("por",),
        nota="base 2020; so >>por<<, sem pt-BR",
    ),
    "big-en-pt": Modelo(
        "Helsinki-NLP/opus-mt-tc-big-en-pt", "opus-mt-tc-big-en-pt-int8", "marian",
        ("en-pt",), etiquetas=("pob", "por"),
        nota="transformer-big 2022, so portugues; >>pob<< = brasileiro",
    ),
    # --- teto de comparação ---------------------------------------------
    "nllb-600m": Modelo(
        "facebook/nllb-200-distilled-600M", "nllb-200-distilled-600M-int8", "nllb",
        ("pt-en", "en-pt"), candidato=False,
        nota="TETO, nao-candidato na Pi; licenca CC-BY-NC (nao comercial)",
        arquivos=("sentencepiece.bpe.model", "tokenizer.json"),
    ),
}
