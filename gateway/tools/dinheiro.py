"""Dinheiro falado: o núcleo determinístico dos gastos por voz.

O princípio da feature é "o modelo propõe, o código grava". Este arquivo é a
metade do código: nada aqui chama LLM, rede, relógio ou disco. São seis funções
puras, e é por serem puras que dá para confiar dinheiro a elas -- cada uma tem
uma tabela de casos em `tests/test_dinheiro.py`.

    extrair_valor            a frase transcrita     -> o valor dito (ou a dúvida)
    conferir_valor           parser x modelo        -> confere / diverge / sem parser
    valor_por_extenso        Decimal                -> "oitenta e sete reais e ..."
    resolver_data            a frase + `hoje`       -> a data do gasto
    data_por_extenso         date + `hoje`          -> "ontem, quinta, dia dezenove"
    classificar_confirmacao  a resposta ao "Confirma?" -> sim / nao / outra

Três regras atravessam tudo:

1. **Na dúvida, não chuta.** Duas leituras possíveis viram `AMBIGUO`, e quem
   chama pergunta de novo. Um parser de dinheiro que adivinha é pior que
   parser nenhum, porque a checagem cruzada passa a concordar com o erro.
2. **`Decimal`, nunca `float`.** 87,50 não tem representação exata em binário,
   e a comparação com o valor do modelo precisa ser de igualdade.
3. **O "hoje" vem de quem chama** (D26). Nenhuma função aqui lê o relógio; o
   fuso é problema da sessão, que sabe onde o dono mora.

As frases de saída vão sem acento, como todas as outras que o gateway manda
para o Piper (`spotify.py`, `session.py`).

Biblioteca padrão apenas. O gateway não importa `device/` nem `lab/` -- as
tabelas de números abaixo repetem de propósito as de `device/router/intents.py`
e `lab/numbers.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import NamedTuple

#: O teto do serviço de finanças. Acima disto ele recusa; recusar aqui antes
#: evita ler em voz alta um valor que nunca seria gravado.
LIMITE = Decimal("999999.99")

_CENTAVO = Decimal("0.01")


# ---------------------------------------------------------------------------
# Vocabulário
# ---------------------------------------------------------------------------

# Só as formas masculinas: dinheiro é "dois reais", nunca "duas". Deixar "uma" e
# "duas" de fora tira do caminho, de graça, "uma pizza" e "duas vezes".
_UNIDADES = {
    "um": 1, "dois": 2, "tres": 3, "quatro": 4, "cinco": 5,
    "seis": 6, "sete": 7, "oito": 8, "nove": 9,
}
_DEZ_A_DEZENOVE = {
    "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14,
    "catorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17,
    "dezoito": 18, "dezenove": 19,
}
_DEZENAS = {
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50,
    "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90,
}
_CENTENAS = {
    "cento": 100, "duzentos": 200, "trezentos": 300, "quatrocentos": 400,
    "quinhentos": 500, "seiscentos": 600, "setecentos": 700,
    "oitocentos": 800, "novecentos": 900,
}
_PALAVRAS_DE_NUMERO = frozenset(
    {"zero", "cem", "mil", *_UNIDADES, *_DEZ_A_DEZENOVE, *_DEZENAS, *_CENTENAS}
)
#: "Cinquentão" já é dinheiro por si só: ninguém diz isso de pizza.
_GIRIAS = {"vintao": 20, "trintao": 30, "quarentao": 40, "cinquentao": 50}

#: Palavras que, depois de um número, dizem que ele é dinheiro. "reis" e
#: "50 real" entram porque é assim que o Whisper às vezes escreve "reais".
_MOEDA = frozenset(
    "real reais reis conto contos pila pilas pau paus mango mangos".split()
)
_CENTAVOS = frozenset({"centavo", "centavos"})
_MILHOES = frozenset({"milhao", "milhoes", "bilhao", "bilhoes"})

#: Depois de um número, estas palavras dizem que ele NÃO é dinheiro: parcelas,
#: horas, pesos. O plural "dias" está aqui e o singular não -- "3 dias" é
#: duração, mas "50 dia 19" é um valor seguido de uma data.
_UNIDADES_FORA = frozenset(
    """vezes vez x parcela parcelas prestacao prestacoes
    hora horas h hs minuto minutos segundo segundos
    dias semanas meses ano anos
    quilo quilos kg grama gramas litro litros km quilometro quilometros""".split()
)

#: O que pode vir depois de um valor dito sem "reais" ("anota 87 no Outback").
#: Qualquer outra palavra depois de um número solto faz dele uma quantidade
#: provável ("2 pizzas") se for pequeno -- ver `_MAIOR_QUANTIDADE`.
#: "por" fica de fora de propósito: em "2 por 50" o 2 não é o preço.
_NEUTRAS = frozenset(
    """e ou no na nos nas em de do da dos das com pro pra para ao a la ali aqui
    hoje ontem anteontem antes dia semana
    segunda terca quarta quinta sexta sabado domingo
    cartao credito debito pix dinheiro especie boleto parcelado dividido
    mesmo so foi agora""".split()
)

#: Até uma dúzia, um número seguido de substantivo é tratado como quantidade.
_MAIOR_QUANTIDADE = 12

_PERIODOS = frozenset({"manha", "tarde", "noite", "madrugada"})

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}
_NOME_DO_MES = {numero: nome for nome, numero in _MESES.items()}

#: Na ordem de `date.weekday()`: segunda é 0.
_NOME_DO_DIA = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")
_DIAS_DA_SEMANA = {nome: i for i, nome in enumerate(_NOME_DO_DIA)}

#: Falam de tempo sem dizer o dia.
_TEMPO_VAGO = frozenset(
    "semana semanas mes atras natal feriado vespera reveillon pascoa carnaval".split()
)

#: "segunda vez", "terceira parcela": aqui "segunda" é ordinal, não dia.
_DEPOIS_DE_ORDINAL = frozenset(
    "vez parcela prestacao via compra unidade loja mao opcao".split()
)


# ---------------------------------------------------------------------------
# Normalização e tokens
# ---------------------------------------------------------------------------


def _sem_acento(texto: str) -> str:
    """Minúsculas e sem acento, como em `spotify.py` e no roteador do aparelho.

    O Whisper acentua bem, mas nem sempre igual; "três" e "tres" não podem ser
    números diferentes. Repare que "é" vira "e" e "às" vira "as" -- as regras
    abaixo foram escritas sabendo disso.
    """
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


class _Tok(NamedTuple):
    tipo: str  # data, hora, vezes, pct, ordinal, moeda, menos, num, palavra, pausa, fim
    texto: str


_FIM = _Tok("fim", "")

# A ordem das alternativas importa: "19/11", "18h45" e "3x" precisam ser
# reconhecidos inteiros ANTES de o "19", o "18" e o "3" virarem números soltos
# e candidatos a preço. A pontuação vira "pausa" em vez de sumir, porque uma
# vírgula separa números: em "50, dia 19" e em "mil, duzentos" não se soma nada.
_TOKEN = re.compile(
    r"""
      (?P<data>\d{1,2}/\d{1,2}(?:/\d{4}|/\d{2})?(?![\d/]))
    | (?P<hora>\d{1,2}(?::\d{2}|h\d{2}|\s?hs?(?![a-z0-9])))
    | (?P<vezes>\d{1,2}\s?x(?![a-z0-9]))
    | (?P<pct>\d+(?:[.,]\d+)?\s?%)
    | (?P<ordinal>\d{1,2}\s?[ºª°])
    | (?P<moeda>r\$)
    | (?P<menos>(?<![a-z0-9])-(?=\d))
    | (?P<num>\d+(?:[.,]\d+)*)
    | (?P<palavra>[a-z]+)
    | (?P<pausa>[,.;:!?])
    """,
    re.VERBOSE,
)


def _tokenizar(frase: str) -> list[_Tok]:
    return [_Tok(m.lastgroup or "", m.group()) for m in _TOKEN.finditer(_sem_acento(frase))]


def _t(toks: list[_Tok], i: int) -> _Tok:
    """O token na posição `i`, ou um sentinela fora da frase."""
    return toks[i] if 0 <= i < len(toks) else _FIM


def _palavra(toks: list[_Tok], i: int) -> str:
    """O texto do token se ele for palavra; senão vazio (número, pausa, fim)."""
    tok = _t(toks, i)
    return tok.texto if tok.tipo == "palavra" else ""


# ---------------------------------------------------------------------------
# Números: dígitos e palavras
# ---------------------------------------------------------------------------

_SO_DIGITOS = re.compile(r"\d+")
_COM_MILHAR = re.compile(r"\d{1,3}(?:\.\d{3})+")
_COM_MILHAR_E_CENTAVOS = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{1,2}")
_COM_VIRGULA = re.compile(r"\d+,\d{1,2}")
_COM_PONTO = re.compile(r"\d+\.\d{1,2}")


def _ler_digitos(texto: str) -> Decimal | None:
    """ "1.200" -> 1200, "12,90" -> 12.90. `None` quando o formato não se decide.

    A convenção é a brasileira: ponto com três dígitos depois é milhar, vírgula
    é decimal. "87.50" (ponto com dois dígitos) só pode ser decimal, e o Whisper
    às vezes escreve assim. Já "1,200" não é nem uma coisa nem outra -- é o
    milhar à americana ou um decimal de três casas -- e fica sem resposta.
    """
    if _SO_DIGITOS.fullmatch(texto):
        return Decimal(texto)
    if _COM_MILHAR.fullmatch(texto):
        return Decimal(texto.replace(".", ""))
    if _COM_MILHAR_E_CENTAVOS.fullmatch(texto):
        return Decimal(texto.replace(".", "").replace(",", "."))
    if _COM_VIRGULA.fullmatch(texto):
        return Decimal(texto.replace(",", "."))
    if _COM_PONTO.fullmatch(texto):
        return Decimal(texto)
    return None


def _eh_numero(tok: _Tok) -> bool:
    return tok.tipo == "num" or tok.texto in _PALAVRAS_DE_NUMERO


def _grupo(toks: list[_Tok], i: int) -> tuple[int, int] | None:
    """Lê de 1 a 999 por extenso a partir de `i`. Devolve (valor, próximo índice).

    Dentro do grupo o "e" é obrigatório ("cento e vinte e um"), como na fala.
    Sem ele são dois números: "vinte cinquenta" não é 70.
    """
    palavra = _palavra(toks, i)
    if palavra == "cem":
        return 100, i + 1
    if palavra == "cento" and _palavra(toks, i - 1) == "por" and _eh_numero(_t(toks, i - 2)):
        # "dez por cento" não é cem reais. Só com número antes do "por": em
        # "comprei por cento e vinte reais" o "cento" é do preço.
        return None

    valor = 0
    if palavra in _CENTENAS:
        valor = _CENTENAS[palavra]
        i += 1
        seguinte = _palavra(toks, i + 1)
        tem_resto = _palavra(toks, i) == "e" and (
            seguinte in _DEZENAS or seguinte in _DEZ_A_DEZENOVE or seguinte in _UNIDADES
        )
        if not tem_resto:
            return valor, i
        i += 1
        palavra = seguinte

    if palavra in _DEZ_A_DEZENOVE:
        return valor + _DEZ_A_DEZENOVE[palavra], i + 1
    if palavra in _DEZENAS:
        valor += _DEZENAS[palavra]
        if _palavra(toks, i + 1) == "e" and _palavra(toks, i + 2) in _UNIDADES:
            return valor + _UNIDADES[_palavra(toks, i + 2)], i + 3
        return valor, i + 1
    if palavra in _UNIDADES:
        return valor + _UNIDADES[palavra], i + 1
    return None


def _resto_do_milhar(toks: list[_Tok], i: int) -> tuple[int, int]:
    """O que vem depois de "mil": "e duzentos", "duzentos e cinquenta", "e 500".

    Devolve (0, i) se não houver resto. Dois cuidados:

    - se o que vem depois termina em "centavos" ou numa unidade ("dois mil e
      cinquenta centavos", "dois mil tres vezes"), não é resto do milhar;
    - resto em dígitos só com o "e" ("2 mil e 500"). Sem ele, "2 mil 3" são
      dois números.
    """
    tem_e = _palavra(toks, i) == "e"
    inicio = i + 1 if tem_e else i
    lido = _grupo(toks, inicio)
    if lido is None and tem_e:
        tok = _t(toks, inicio)
        if tok.tipo == "num" and re.fullmatch(r"[1-9]\d{0,2}", tok.texto):
            lido = int(tok.texto), inicio + 1
    if lido is None:
        return 0, i
    valor, fim = lido
    if _palavra(toks, fim) in _CENTAVOS | _UNIDADES_FORA:
        return 0, i
    return valor, fim


def _inteiro_por_palavras(toks: list[_Tok], i: int) -> tuple[int, int] | None:
    """Lê de 1 a 999.999 por extenso: "mil e duzentos", "vinte e cinco mil"."""
    if _palavra(toks, i) == "zero":
        return 0, i + 1
    if _palavra(toks, i) == "mil":
        milhares, fim = 1, i + 1
    else:
        lido = _grupo(toks, i)
        if lido is None:
            return None
        if _palavra(toks, lido[1]) != "mil":
            return lido
        milhares, fim = lido[0], lido[1] + 1
    resto, fim = _resto_do_milhar(toks, fim)
    return milhares * 1000 + resto, fim


@dataclass
class _Numero:
    """Um número achado na frase, antes de se saber se é dinheiro."""

    valor: Decimal | None  # None: formato de dígitos que não se decide
    ini: int
    fim: int  # os tokens do número são toks[ini:fim]
    giria: bool = False
    papel: str = ""  # preenchido por `_classificar`

    @property
    def inteiro(self) -> bool:
        return self.valor is not None and self.valor == self.valor.to_integral_value()


def _ler_numero(toks: list[_Tok], i: int) -> _Numero | None:
    tok = _t(toks, i)
    if tok.tipo == "num":
        # Zero à esquerda é identificador (CEP, código), não quantia. Com dois
        # dígitos passa, porque é assim que se escrevem centavos: "87 e 05".
        if _SO_DIGITOS.fullmatch(tok.texto) and len(tok.texto) > 2 and tok.texto[0] == "0":
            return None
        valor = _ler_digitos(tok.texto)
        fim = i + 1
        if _palavra(toks, fim) == "mil":  # "2 mil", "25 mil", "2,5 mil"
            if valor is not None and "." in tok.texto:
                valor = None  # "1.200 mil" não quer dizer nada
            resto, fim = _resto_do_milhar(toks, fim + 1)
            if valor is not None:
                valor = valor * 1000 + resto
        numero = _Numero(valor, i, fim)
    elif tok.tipo == "palavra":
        if tok.texto in _GIRIAS:
            numero = _Numero(Decimal(_GIRIAS[tok.texto]), i, i + 1, giria=True)
        else:
            lido = _inteiro_por_palavras(toks, i)
            if lido is None:
                return None
            numero = _Numero(Decimal(lido[0]), i, lido[1])
    else:
        return None

    # "dois milhões": fora do teto de qualquer jeito. Multiplicar basta para a
    # checagem de limite recusar; o que vier depois de "milhões" nem importa.
    if _palavra(toks, numero.fim) in _MILHOES:
        numero.fim += 1
        if numero.valor is not None:
            numero.valor *= 1_000_000
    return numero


def _achar_numeros(toks: list[_Tok]) -> list[_Numero]:
    numeros: list[_Numero] = []
    i = 0
    while i < len(toks):
        numero = _ler_numero(toks, i)
        if numero is None:
            i += 1
        else:
            numeros.append(numero)
            i = numero.fim
    return numeros


# ---------------------------------------------------------------------------
# 1. Extrair o valor
# ---------------------------------------------------------------------------


class StatusValor(str, Enum):
    OK = "ok"
    #: Nenhum número com cara de dinheiro na frase.
    NAO_ACHOU = "nao_achou"
    #: Dois valores diferentes, ou um trecho com duas leituras.
    AMBIGUO = "ambiguo"
    #: Achou um só, mas é zero, negativo ou acima de `LIMITE`.
    INVALIDO = "invalido"


@dataclass(frozen=True)
class ValorExtraido:
    status: StatusValor
    #: Preenchido só quando `status` é OK, sempre com duas casas.
    valor: Decimal | None = None
    #: O que foi lido, para o log e para a pergunta ("cinquenta ou trinta?").
    candidatos: tuple[Decimal, ...] = ()


# Os papéis de um número na frase.
_DINHEIRO = "dinheiro"
_DUVIDA = "duvida"  # pode ser dinheiro, pode ser outra coisa: força AMBIGUO
_RESERVA = "reserva"  # quantidade provável ("2 pizzas"); só vale se não houver mais nada
_FORA = "fora"  # data, hora, parcela, unidade


def _tem_moeda(toks: list[_Tok], n: _Numero) -> bool:
    """O número vem marcado como dinheiro: "R$ 50", "50 reais", "cinquentão"."""
    return n.giria or _t(toks, n.ini - 1).tipo == "moeda" or _palavra(toks, n.fim) in _MOEDA


def _eh_centavos(toks: list[_Tok], n: _Numero) -> bool:
    return _palavra(toks, n.fim) in _CENTAVOS


def _classificar(toks: list[_Tok], n: _Numero, minutos: set[int]) -> str:
    """Decide o que um número é pelo que vem antes e depois dele.

    `minutos` recebe a posição do número que vem depois de "às oito e": ele é
    minuto, e não pode ser lido como centavos nem como um segundo valor.
    """
    antes, antes2 = _palavra(toks, n.ini - 1), _palavra(toks, n.ini - 2)
    depois, depois2 = _palavra(toks, n.fim), _palavra(toks, n.fim + 1)
    marcado = _tem_moeda(toks, n) or _eh_centavos(toks, n)
    valor = n.valor

    if n.ini in minutos and not marcado:
        return _FORA

    # -- data -------------------------------------------------------------
    if antes == "dia":
        # "dia 19" é data. Mas "dia vinte e cinco reais" e "dia dois mil reais"
        # têm duas leituras (dia 2 e mil reais? dois mil?), e nenhuma é segura.
        if not marcado and n.inteiro and valor is not None and 1 <= valor <= 31:
            return _FORA
        return _DUVIDA
    if n.inteiro and valor is not None and not marcado:
        if depois == "de" and depois2 in _MESES and 1 <= valor <= 31:
            return _FORA  # "19 de novembro"
        if antes == "de" and antes2 in _MESES and 1900 <= valor <= 2100:
            return _FORA  # "... de novembro de 2026"

    # -- hora -------------------------------------------------------------
    if n.inteiro and valor is not None and not marcado and valor <= 24:
        # Pula o "e meia" para enxergar o "da noite" em "sete e meia da noite".
        k = n.fim
        if _palavra(toks, k) == "e" and _palavra(toks, k + 1) == "meia":
            k += 2
        periodo = _palavra(toks, k) in ("da", "de") and _palavra(toks, k + 1) in _PERIODOS
        if antes in ("as", "pelas") or periodo:
            if _palavra(toks, n.fim) == "e":
                minutos.add(n.fim + 1)
            return _FORA

    # -- parcelas e outras unidades ---------------------------------------
    if marcado:
        return _DINHEIRO
    if antes == "em" and antes2 in ("parcelado", "parcelei", "dividido", "dividi"):
        return _FORA
    if depois in _UNIDADES_FORA or (depois == "por" and depois2 == "cento"):
        return _FORA
    # "anota um gasto", "comprei um no mercado": "um" solto é artigo. Só não é
    # quando puxa centavos: "um e cinquenta", "um e meia".
    if n.fim - n.ini == 1 and _palavra(toks, n.ini) == "um":
        puxa_centavos = depois == "e" and (
            depois2 == "meia" or _ler_numero(toks, n.fim + 1) is not None
        )
        if not puxa_centavos:
            return _FORA
    # "oitenta e sete vírgula cinquenta": ninguém fala assim com frequência, e
    # ler só um dos lados seria ler o valor errado.
    if "virgula" in (antes, depois):
        return _DUVIDA
    # Número seguido de substantivo. Pequeno, é quantidade provável ("2
    # pizzas") e sai do caminho do preço. Grande, não se descarta calado: em
    # "cinquenta almoçando e quinze reais de estacionamento" há dois gastos, e
    # os dois têm de aparecer para a frase dar AMBIGUO.
    seguido_de_substantivo = (
        depois and depois not in _NEUTRAS and _ler_numero(toks, n.fim) is None
    )
    if seguido_de_substantivo and valor is not None and valor <= _MAIOR_QUANTIDADE:
        return _RESERVA
    return _DINHEIRO


def _juntar_centavos(
    toks: list[_Tok], n: _Numero, por_inicio: dict[int, _Numero], usados: set[int]
) -> tuple[Decimal, bool]:
    """Soma a `n` os centavos ditos depois dele. Devolve (valor, ficou em dúvida).

    As formas: "87 reais e 50 centavos", "87 reais e 50", "87 e 50",
    "sete e meia". O número dos centavos entra em `usados` para não ser contado
    de novo como um segundo valor.
    """
    assert n.valor is not None
    valor = n.valor
    k = n.fim
    disse_moeda = _palavra(toks, k) in _MOEDA
    if disse_moeda:
        k += 1
    if _palavra(toks, k) != "e" or not n.inteiro:
        return valor, False

    if _palavra(toks, k + 1) == "meia":
        depois = _palavra(toks, k + 2)
        if depois and depois not in _NEUTRAS and depois not in _MOEDA:
            return valor, False  # "e meia duzia": não é preço
        # "sete e meia" é 7,50. "mil e meia" já pode ser 1.500: não se decide.
        return valor + Decimal("0.50"), valor >= 100

    b = por_inicio.get(k + 1)
    if b is None or b.papel != _DINHEIRO or b.valor is None or not b.inteiro:
        return valor, False
    centavos = b.valor

    if _eh_centavos(toks, b):
        usados.add(b.ini)
        return valor + centavos / 100, not 1 <= centavos <= 99
    if _tem_moeda(toks, b):
        return valor, False  # "50 reais e 30 reais": são dois valores

    # Centavos sem a palavra "centavos": "87 e 50", "dezenove e noventa".
    if centavos >= 100:
        return valor, False  # dois valores; a ambiguidade aparece adiante
    usados.add(b.ini)
    escrito = toks[b.ini].texto if b.fim - b.ini == 1 else ""
    if centavos < 10 and not re.fullmatch(r"0\d", escrito):
        # "oitenta e sete e cinco": 87,05 ou 87,50? Quem fala assim quer dizer
        # qualquer uma das duas.
        return valor + centavos / 100, True
    # "cem e cinquenta" sem "reais" no meio: 100,50, ou um "cento e cinquenta"
    # mal dito? Não se decide.
    cem_solto = not disse_moeda and n.fim - n.ini == 1 and toks[n.ini].texto == "cem"
    return valor + centavos / 100, cem_solto


def _distintos(valores: list[Decimal]) -> tuple[Decimal, ...]:
    vistos: list[Decimal] = []
    for v in valores:
        if v not in vistos:
            vistos.append(v)
    return tuple(vistos)


def extrair_valor(frase: str) -> ValorExtraido:
    """Acha o valor em reais dito na frase transcrita.

    Lê o que o Whisper escreve ("R$ 87,50.", "1.200", "50 reais") e o que as
    pessoas falam ("mil e duzentos", "oitenta e sete e cinquenta", "cinquenta
    conto"). Datas, horas, parcelas e quantidades são reconhecidas para serem
    descartadas. Se sobrarem dois valores diferentes, a resposta é `AMBIGUO`:
    esta função nunca escolhe entre dois números.

    O mesmo valor dito duas vezes ("50 reais, isso, 50") não é ambiguidade.
    """
    toks = _tokenizar(frase)
    numeros = _achar_numeros(toks)
    minutos: set[int] = set()
    for n in numeros:
        n.papel = _classificar(toks, n, minutos)
    por_inicio = {n.ini: n for n in numeros}

    valores: list[Decimal] = []
    duvida = False
    usados: set[int] = set()
    for n in numeros:
        if n.ini in usados or n.papel not in (_DINHEIRO, _DUVIDA):
            continue
        if n.papel == _DUVIDA or n.valor is None:
            duvida = True
            if n.valor is not None:
                valores.append(n.valor)
            continue
        if _eh_centavos(toks, n):  # "noventa centavos", sozinho
            duvida = duvida or not (n.inteiro and 1 <= n.valor <= 99)
            valor = n.valor / 100
        else:
            valor, incerto = _juntar_centavos(toks, n, por_inicio, usados)
            duvida = duvida or incerto
        # "menos 50", "-50": um gasto não é negativo. Fica negativo de propósito,
        # para cair em INVALIDO em vez de ser lido como 50.
        if _t(toks, n.ini - 1).tipo == "menos" or _palavra(toks, n.ini - 1) == "menos":
            valor = -valor
        valores.append(valor)

    candidatos = _distintos(valores)
    if not candidatos and not duvida:
        # Só sobrou número pequeno seguido de substantivo ("anota 8 mercado").
        # Sem outro candidato, é melhor conferir esse do que não conferir nada.
        reservas = [n for n in numeros if n.papel == _RESERVA]
        duvida = any(n.valor is None for n in reservas)
        candidatos = _distintos([n.valor for n in reservas if n.valor is not None])

    if duvida or len(candidatos) > 1:
        return ValorExtraido(StatusValor.AMBIGUO, candidatos=candidatos)
    if not candidatos:
        return ValorExtraido(StatusValor.NAO_ACHOU)
    valor = candidatos[0]
    if valor <= 0 or valor > LIMITE:
        return ValorExtraido(StatusValor.INVALIDO, candidatos=candidatos)
    return ValorExtraido(StatusValor.OK, valor.quantize(_CENTAVO), candidatos)


# ---------------------------------------------------------------------------
# 2. Conferir com o valor do modelo
# ---------------------------------------------------------------------------


class Conferencia(str, Enum):
    #: Parser e modelo dizem o mesmo valor: segue para a leitura de volta.
    CONFERE = "confere"
    #: Não batem (ou o parser ficou em dúvida, ou o valor do modelo não presta):
    #: pergunta o valor de novo, não lê proposta nenhuma.
    DIVERGE = "diverge"
    #: O parser não achou número na frase. Segue, mas a única proteção passa a
    #: ser a leitura de volta -- quem chama deve registrar isso no diário.
    SEM_PARSER = "sem_parser"


def _para_decimal(proposto: object) -> Decimal | None:
    """O valor que veio do modelo, como Decimal de até duas casas, ou None.

    O JSON da chamada de ferramenta entrega `float`. Converter pelo `repr`
    devolve o que foi escrito (87.5), e não a expansão binária; um float com
    lixo depois da segunda casa é recusado em vez de arredondado.
    """
    if isinstance(proposto, bool):  # bool é int em Python; True não é 1 real
        return None
    if isinstance(proposto, float):
        proposto = repr(proposto)
    if not isinstance(proposto, (Decimal, int, str)):
        return None
    try:
        valor = Decimal(proposto.strip() if isinstance(proposto, str) else proposto)
        if not valor.is_finite() or valor != valor.quantize(_CENTAVO):
            return None
        return valor.quantize(_CENTAVO)
    except InvalidOperation:  # texto que não é número, ou número grande demais
        return None


def conferir_valor(extraido: ValorExtraido, proposto: Decimal | int | str | float) -> Conferencia:
    """Compara o que o parser leu na frase com o que o modelo propôs.

    A ordem das checagens é a da cautela: um valor proposto inválido (zero,
    negativo, acima do teto, mais de duas casas, texto que não é número)
    diverge sempre, mesmo que o parser não tenha achado nada -- `SEM_PARSER`
    quer dizer "confio no modelo e na leitura de volta", e num valor desses não
    se confia.

    `AMBIGUO` e `INVALIDO` do parser também divergem: se a frase tinha dois
    valores, o modelo escolher um deles não resolve a dúvida, só a esconde.
    """
    valor = _para_decimal(proposto)
    if valor is None or valor <= 0 or valor > LIMITE:
        return Conferencia.DIVERGE
    if extraido.status is StatusValor.NAO_ACHOU:
        return Conferencia.SEM_PARSER
    if extraido.status is StatusValor.OK and extraido.valor == valor:
        return Conferencia.CONFERE
    return Conferencia.DIVERGE


# ---------------------------------------------------------------------------
# 3. Valor por extenso
# ---------------------------------------------------------------------------

_EXT_UNIDADES = "zero um dois tres quatro cinco seis sete oito nove".split()
_EXT_DEZ_A_DEZENOVE = (
    "dez onze doze treze quatorze quinze dezesseis dezessete dezoito dezenove".split()
)
_EXT_DEZENAS = "_ _ vinte trinta quarenta cinquenta sessenta setenta oitenta noventa".split()
_EXT_CENTENAS = (
    "_ cento duzentos trezentos quatrocentos quinhentos "
    "seiscentos setecentos oitocentos novecentos"
).split()


def _ate_999(n: int) -> str:
    if n < 10:
        return _EXT_UNIDADES[n]
    if n < 20:
        return _EXT_DEZ_A_DEZENOVE[n - 10]
    if n < 100:
        dezena, unidade = divmod(n, 10)
        return _EXT_DEZENAS[dezena] + (f" e {_EXT_UNIDADES[unidade]}" if unidade else "")
    if n == 100:
        return "cem"
    centena, resto = divmod(n, 100)
    return _EXT_CENTENAS[centena] + (f" e {_ate_999(resto)}" if resto else "")


def _inteiro_por_extenso(n: int) -> str:
    """De 0 a 999.999, com o "e" onde o português falado põe.

    Depois de "mil" o "e" só aparece quando o resto é menor que cem ou é uma
    centena redonda: "mil e duzentos", "mil e cinquenta", mas "mil duzentos e
    cinquenta". (`lab/numbers.py` põe o "e" sempre, e por isso não serve aqui.)
    """
    if not 0 <= n <= 999_999:
        raise ValueError(f"fora do alcance: {n}")
    if n < 1000:
        return _ate_999(n)
    milhares, resto = divmod(n, 1000)
    texto = "mil" if milhares == 1 else f"{_ate_999(milhares)} mil"
    if not resto:
        return texto
    liga = " e " if resto < 100 or resto % 100 == 0 else " "
    return texto + liga + _ate_999(resto)


def valor_por_extenso(valor: Decimal | int) -> str:
    """ "oitenta e sete reais e cinquenta centavos". Sem dígito e sem "R$".

    O Piper lê símbolo mal; aqui ele só recebe palavras. Levanta `ValueError`
    para zero, negativo, acima de `LIMITE` ou com mais de duas casas: arredondar
    calado seria ler em voz alta um valor diferente do que vai ser gravado.
    `float` é recusado pelo mesmo motivo.
    """
    if isinstance(valor, bool) or not isinstance(valor, (Decimal, int)):
        raise TypeError("valor em Decimal ou int; float nao entra em conta de dinheiro")
    valor = Decimal(valor)
    if not valor.is_finite() or valor <= 0 or valor > LIMITE:
        raise ValueError(f"valor fora do intervalo: {valor}")
    if valor != valor.quantize(_CENTAVO):
        raise ValueError(f"valor com mais de duas casas: {valor}")

    reais, centavos = divmod(int(valor * 100), 100)
    partes = []
    if reais:
        partes.append(_inteiro_por_extenso(reais) + (" real" if reais == 1 else " reais"))
    if centavos:
        partes.append(_ate_999(centavos) + (" centavo" if centavos == 1 else " centavos"))
    return " e ".join(partes)


# ---------------------------------------------------------------------------
# 4. Resolver a data
# ---------------------------------------------------------------------------


class StatusData(str, Enum):
    OK = "ok"
    #: "semana passada", duas datas diferentes, dia da semana que não bate.
    AMBIGUA = "ambigua"
    #: "amanhã", "sexta que vem", 25/12 de um ano que ainda não chegou.
    FUTURA = "futura"
    #: "30 de fevereiro", "dia 45".
    INVALIDA = "invalida"


@dataclass(frozen=True)
class DataResolvida:
    status: StatusData
    #: Preenchida só quando `status` é OK.
    data: date | None = None
    #: False quando a frase não falava de data e o resultado é o `hoje` padrão.
    #: Serve para a leitura de volta dizer a data mesmo assim.
    dita: bool = False


def _data_ou_none(ano: int, mes: int, dia: int) -> date | None:
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _ler_dia(toks: list[_Tok], i: int) -> tuple[int, int] | None:
    """Um dia do mês em `i`: "19", "1º", "primeiro", "dezenove"."""
    tok = _t(toks, i)
    if tok.tipo == "ordinal":
        return int(re.match(r"\d+", tok.texto).group()), i + 1  # type: ignore[union-attr]
    if tok.tipo == "num" and re.fullmatch(r"\d{1,2}", tok.texto):
        return int(tok.texto), i + 1
    if tok.texto == "primeiro":
        return 1, i + 1
    # `_grupo`, e não o leitor completo: em "dia dois mil reais" o dia é dois.
    return _grupo(toks, i)


def _ler_ano(toks: list[_Tok], i: int) -> tuple[int, int] | None:
    tok = _t(toks, i)
    if tok.tipo == "num" and re.fullmatch(r"\d{4}", tok.texto):
        lido: tuple[int, int] | None = (int(tok.texto), i + 1)
    else:
        lido = _inteiro_por_palavras(toks, i)
    return lido if lido and 1900 <= lido[0] <= 2100 else None


def _dia_e_mes(dia: int, mes: int, ano: int | None, hoje: date) -> date | None:
    """ "19 de novembro" sem ano: o mais recente que não seja futuro.

    Em 2 de janeiro, "30 de dezembro" é do ano passado -- e a leitura de volta
    diz o ano quando ele não é o corrente, para isso nunca passar calado.
    """
    if ano is not None:
        return _data_ou_none(ano, mes, dia)
    este_ano = _data_ou_none(hoje.year, mes, dia)
    if este_ano is not None and este_ano <= hoje:
        return este_ano
    return _data_ou_none(hoje.year - 1, mes, dia)


def _dia_solto(dia: int, hoje: date) -> date | None:
    """ "dia 19": deste mês se já passou (ou é hoje); senão, do mês anterior."""
    if dia <= hoje.day:
        return _data_ou_none(hoje.year, hoje.month, dia)
    ano, mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
    return _data_ou_none(ano, mes, dia)


def resolver_data(frase: str, hoje: date) -> DataResolvida:
    """A data do gasto dita na frase, relativa a `hoje`.

    Um gasto nunca é no futuro; toda expressão é resolvida para trás.

    - nada dito -> `hoje`, com `dita=False`;
    - "hoje", "ontem", "anteontem" / "antes de ontem";
    - "sexta", "na sexta passada", "sexta-feira" -> a sexta mais recente
      **antes de hoje**. Dito numa sexta, "sexta" é a de sete dias atrás: para
      hoje as pessoas dizem "hoje". (A leitura de volta fala o dia do mês, então
      um engano aqui é ouvido antes de ser gravado.)
    - "dia 19" -> deste mês, ou do anterior se o dia 19 ainda não chegou;
    - "19 de novembro", "19/11", com ou sem ano;
    - "ontem, quinta, dia dezenove": várias menções valem se apontarem para o
      mesmo dia. O dia da semana, junto de outra menção, é só conferência -- se
      não bater, `AMBIGUA`;
    - "semana passada", "mês passado", "noite passada", "em novembro", "há três
      dias", "outro dia", "no feriado" -> `AMBIGUA`: quem chama pergunta o dia;
    - "amanhã", "sexta que vem", data adiante -> `FUTURA`;
    - "30 de fevereiro", "dia 45" -> `INVALIDA`.
    """
    toks = _tokenizar(frase)
    datas: list[date] = []
    dias_da_semana: list[int] = []
    vaga = invalida = False

    def anotar(d: date | None) -> None:
        nonlocal invalida
        if d is None:
            invalida = True
        else:
            datas.append(d)

    i = 0
    while i < len(toks):
        tok = toks[i]
        p, p1, p2 = _palavra(toks, i), _palavra(toks, i + 1), _palavra(toks, i + 2)

        if tok.tipo == "data":  # 19/11, 19/11/26, 19/11/2026
            partes = [int(x) for x in tok.texto.split("/")]
            ano = None if len(partes) == 2 else partes[2] + (2000 if partes[2] < 100 else 0)
            anotar(_dia_e_mes(partes[0], partes[1], ano, hoje) if 1 <= partes[1] <= 12 else None)
            i += 1
            continue

        # "dia 19", "dia 19 de novembro", "19 de novembro de 2026"
        lido = _ler_dia(toks, i + 1 if p == "dia" else i)
        if lido is not None:
            dia, j = lido
            if _palavra(toks, j) == "de" and _palavra(toks, j + 1) in _MESES:
                mes = _MESES[_palavra(toks, j + 1)]
                j += 2
                ano_lido = _ler_ano(toks, j + 1) if _palavra(toks, j) == "de" else None
                if ano_lido is not None:
                    j = ano_lido[1]
                anotar(_dia_e_mes(dia, mes, ano_lido[0] if ano_lido else None, hoje))
                i = j
                continue
            if p == "dia":
                anotar(_dia_solto(dia, hoje) if 1 <= dia <= 31 else None)
                i = j
                continue
        elif p == "dia" and _t(toks, i + 1).tipo == "num":
            invalida = True  # "dia 19.11": tem data ali, mas não neste formato

        if p == "hoje":
            datas.append(hoje)
        elif p == "ontem":
            datas.append(hoje - timedelta(days=1))
        elif p == "anteontem":
            datas.append(hoje - timedelta(days=2))
        elif p == "antes" and p1 == "de" and p2 == "ontem":
            datas.append(hoje - timedelta(days=2))
            i += 2
        elif p == "depois" and p1 == "de" and p2 == "amanha":
            datas.append(hoje + timedelta(days=2))
            i += 2
        elif p == "amanha":
            datas.append(hoje + timedelta(days=1))
        elif p in _DIAS_DA_SEMANA:
            j = i + 2 if p1 == "feira" else i + 1
            depois, depois2 = _palavra(toks, j), _palavra(toks, j + 1)
            if p1 != "feira" and p1 in _DEPOIS_DE_ORDINAL:
                pass  # "segunda vez", "segunda parcela"
            elif depois.startswith("retrasad"):
                vaga = True
            elif (depois, depois2) == ("que", "vem") or _palavra(toks, i - 1).startswith("proxim"):
                adiante = (_DIAS_DA_SEMANA[p] - hoje.weekday()) % 7 or 7
                datas.append(hoje + timedelta(days=adiante))
            else:
                dias_da_semana.append(_DIAS_DA_SEMANA[p])
                if depois.startswith("passad"):
                    i = j  # o "passada" de "sexta passada" não é vago
        # O que fala de tempo sem dizer o dia. Calar aqui faria a função
        # responder "hoje" para "semana passada", que é o pior erro possível.
        elif p in _TEMPO_VAGO or p in _MESES or p.startswith(("retrasad", "passad")):
            # Um mês solto ("em novembro") e um "passada" que não veio depois de
            # um dia da semana ("noite passada", "ano passado") caem aqui.
            vaga = True
        elif (p, p1) in (("outro", "dia"), ("esses", "dias")):
            vaga = True
        elif p in ("ha", "faz") and any(
            _palavra(toks, i + k) in ("dia", "dias", "mes", "meses", "ano", "anos")
            for k in (1, 2, 3, 4)
        ):
            vaga = True
        i += 1

    if invalida:
        return DataResolvida(StatusData.INVALIDA, dita=True)
    if vaga:
        return DataResolvida(StatusData.AMBIGUA, dita=True)

    if datas:
        # Com outra menção, o dia da semana só confere ("ontem, quinta").
        if any(d.weekday() not in dias_da_semana for d in datas if dias_da_semana):
            return DataResolvida(StatusData.AMBIGUA, dita=True)
    else:
        for dia_da_semana in dias_da_semana:
            atras = (hoje.weekday() - dia_da_semana) % 7 or 7
            datas.append(hoje - timedelta(days=atras))

    if not datas:
        return DataResolvida(StatusData.OK, hoje, dita=False)
    if len(set(datas)) > 1:
        return DataResolvida(StatusData.AMBIGUA, dita=True)
    if datas[0] > hoje:
        return DataResolvida(StatusData.FUTURA, dita=True)
    return DataResolvida(StatusData.OK, datas[0], dita=True)


# ---------------------------------------------------------------------------
# 5. Data por extenso
# ---------------------------------------------------------------------------


def data_por_extenso(data: date, hoje: date) -> str:
    """A data como se fala, para a leitura de volta.

        hoje                 "hoje"
        ontem                "ontem, quinta, dia dezenove"
        anteontem            "anteontem, quarta, dia dezoito"
        até seis dias atrás  "segunda, dia dezesseis"
        mais antigo          "dia dois de novembro"
        de outro ano         "dia trinta de dezembro de dois mil e vinte e cinco"

    O dia do mês vai sempre: é ele que deixa o dono perceber que "sexta" foi
    entendida como a sexta errada. O mês entra quando não é o corrente, e o ano
    quando não é o corrente. O que sai daqui volta igual por `resolver_data`.
    """
    atras = (hoje - data).days
    if atras < 0:
        raise ValueError(f"data no futuro: {data} (hoje e {hoje})")
    if atras == 0:
        return "hoje"

    dia = "primeiro" if data.day == 1 else _ate_999(data.day)
    texto = f"dia {dia}"
    if atras >= 7 or data.month != hoje.month:
        texto += f" de {_NOME_DO_MES[data.month]}"
    if atras >= 7 and data.year != hoje.year:
        texto += f" de {_inteiro_por_extenso(data.year)}"
    if atras >= 7:
        return texto
    texto = f"{_NOME_DO_DIA[data.weekday()]}, {texto}"
    if atras == 1:
        return f"ontem, {texto}"
    if atras == 2:
        return f"anteontem, {texto}"
    return texto


# ---------------------------------------------------------------------------
# 6. Classificar a confirmação
# ---------------------------------------------------------------------------


class Confirmacao(str, Enum):
    SIM = "sim"
    NAO = "nao"
    #: Qualquer outra coisa: correção, dúvida, ruído. Volta ao modelo, e nada
    #: é gravado.
    OUTRA = "outra"


# As duas listas são FECHADAS e a frase inteira tem de ser feita delas. Não é
# busca de palavra: "sim, mas foi cinquenta" contém "sim" e não é um sim.
#
# De fora, de propósito: "é", "tá", "uhum", "aham" sozinhos. São curtos demais --
# é o tipo de coisa que o Whisper escreve a partir de um ruído, e um "sim"
# inventado grava dinheiro.
_FRASES_SIM = frozenset(
    tuple(f.split())
    for f in """sim | isso | isso mesmo | isso ai | isso sim | e isso | e isso mesmo
    | e isso ai | e sim | sim senhor | claro | claro que sim | com certeza
    | confirma | confirmo | confirmado | confirmar | pode confirmar
    | pode | pode sim | pode ser | pode lancar | pode gravar | pode anotar
    | pode salvar | pode mandar | lanca | grava | manda | manda ver | manda bala
    | certo | ta certo | esta certo | tudo certo | correto | ta correto
    | esta correto | ta bom | ta otimo | exato | exatamente | perfeito
    | ok | okay | beleza | positivo | fechou""".split("|")
)
_FRASES_NAO = frozenset(
    tuple(f.split())
    for f in """nao | nao e isso | nao e isso nao | nao quero | nao precisa
    | nao ta certo | nao esta certo | cancela | cancelar | cancele | pode cancelar
    | esquece | esqueca | pode esquecer | deixa | deixa pra la | deixa para la
    | deixa quieto | errado | ta errado | esta errado | incorreto | negativo
    | nada disso | nada a ver | de jeito nenhum | nem pensar | claro que nao
    | apaga | pode apagar | descarta | pode descartar | desisto | desiste
    | para | pare""".split("|")
)
_CORTESIA = frozenset({("por", "favor")})
_MAIOR_FRASE = max(len(f) for f in _FRASES_SIM | _FRASES_NAO)


def classificar_confirmacao(frase: str) -> Confirmacao:
    """Lê a resposta ao "Confirma?" sem passar pelo LLM.

    O critério é assimétrico: um "não" lido como "sim" grava dinheiro errado;
    um "sim" lido como `OUTRA` custa só uma pergunta a mais. Por isso:

    - a frase **inteira** precisa ser feita de expressões conhecidas. Uma
      palavra fora das listas -- um número, um "mas", um "acho", um "Obrigado."
      alucinado -- e a resposta é `OUTRA`;
    - sim e não misturados ("não, pode lançar", "pode não") dão `OUTRA`. Sem
      pontuação confiável, "não, pode lançar" e "não pode lançar" são a mesma
      transcrição, com sentidos opostos;
    - um ponto de interrogação ("Sim?", "Pode?") dá `OUTRA`: é dúvida, não
      ordem. Reticências também ("Sim..."): é como o Whisper marca fala
      cortada, e o que vinha depois podia ser um "mas".
    """
    if "?" in frase or "..." in frase or "…" in frase:
        return Confirmacao.OUTRA
    palavras = re.sub(r"[^a-z0-9]+", " ", _sem_acento(frase)).split()

    sim = nao = False
    i = 0
    while i < len(palavras):
        # A maior expressão primeiro: "nao e isso" antes de "nao" + "e isso".
        for tamanho in range(min(_MAIOR_FRASE, len(palavras) - i), 0, -1):
            trecho = tuple(palavras[i : i + tamanho])
            if trecho in _FRASES_NAO:
                nao = True
            elif trecho in _FRASES_SIM:
                sim = True
            elif trecho not in _CORTESIA:
                continue
            i += tamanho
            break
        else:
            return Confirmacao.OUTRA  # sobrou palavra que não é sim nem não

    if sim and not nao:
        return Confirmacao.SIM
    if nao and not sim:
        return Confirmacao.NAO
    return Confirmacao.OUTRA
