"""O parser de dinheiro contra tabelas.

Não há modelo, rede nem relógio aqui: `gateway/tools/dinheiro.py` é feito de
funções puras, e o que se testa é a tabela de formatos que o plano exige a 100%
(docs/proximos-passos.md, "O que medir antes de confiar").

As tabelas misturam o que as pessoas falam com o que o Whisper escreve -- ele
põe maiúscula, ponto final, "R$" e dígitos onde a pessoa disse palavras. Cada
caminho de dúvida tem a sua linha: um parser de dinheiro se julga mais pelo que
ele se recusa a responder do que pelo que responde.

O `HOJE` é fixo (D26): sexta, 20 de novembro de 2026.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from gateway.tools.dinheiro import (
    LIMITE,
    Conferencia,
    Confirmacao,
    StatusData,
    StatusValor,
    ValorExtraido,
    classificar_confirmacao,
    conferir_valor,
    data_por_extenso,
    extrair_valor,
    resolver_data,
    valor_por_extenso,
)

HOJE = date(2026, 11, 20)
assert HOJE.weekday() == 4  # sexta; várias linhas abaixo dependem disso


# ---------------------------------------------------------------------------
# 1. extrair_valor
# ---------------------------------------------------------------------------

VALORES = [
    # -- dígitos, como o Whisper costuma escrever --------------------------
    ("50 reais", "50"),
    ("R$ 50", "50"),
    ("R$50", "50"),
    ("R$50,00", "50"),
    ("R$ 87,50.", "87.50"),
    ("Gastei R$ 87,50 no Outback.", "87.50"),
    ("O valor deu R$ 1.249,90.", "1249.90"),
    ("1.200", "1200"),
    ("1.200 reais", "1200"),
    ("R$ 2.300", "2300"),
    ("25.000 reais", "25000"),
    ("999.999,99", "999999.99"),
    ("1,5", "1.50"),
    ("12,90", "12.90"),
    ("87,5 reais", "87.50"),
    ("0,90", "0.90"),
    ("87.50 reais", "87.50"),  # o Whisper às vezes usa ponto decimal
    ("1200", "1200"),
    ("Anota 1200 no cartão.", "1200"),
    ("87 e 50", "87.50"),
    ("87 reais e 50", "87.50"),
    ("87 reais e 50 centavos", "87.50"),
    ("87 e 05", "87.05"),
    ("2 mil", "2000"),
    ("25 mil reais", "25000"),
    ("2 mil e 500", "2500"),
    ("2,5 mil", "2500"),
    ("50 reais e 5 centavos", "50.05"),
    ("90 centavos", "0.90"),
    ("1 real", "1"),
    ("R$ 50 reais", "50"),
    # -- por extenso -------------------------------------------------------
    ("cinquenta reais", "50"),
    ("mil e duzentos", "1200"),
    ("mil duzentos e cinquenta", "1250"),
    ("mil e duzentos e cinquenta reais", "1250"),
    ("mil e cinquenta", "1050"),
    ("mil e um reais", "1001"),
    ("dois mil", "2000"),
    ("dois mil reais", "2000"),
    ("vinte e cinco mil", "25000"),
    ("cem mil reais", "100000"),
    ("cento e um reais", "101"),
    ("cem reais", "100"),
    ("quinhentos e trinta e dois reais", "532"),
    ("oitenta e sete e cinquenta", "87.50"),
    ("oitenta e sete reais e cinquenta centavos", "87.50"),
    ("oitenta e sete reais e cinquenta", "87.50"),
    ("dezenove e noventa", "19.90"),
    ("um real", "1"),
    ("um real e cinquenta centavos", "1.50"),
    ("um e cinquenta", "1.50"),
    ("noventa centavos", "0.90"),
    ("um centavo", "0.01"),
    ("vinte e cinco centavos", "0.25"),
    ("cem reais e cinquenta centavos", "100.50"),
    ("dois mil reais e cinquenta centavos", "2000.50"),
    ("dois mil e cinquenta centavos", "2000.50"),
    ("quatorze reais", "14"),
    ("catorze reais", "14"),
    ("três reais", "3"),
    ("Três reais.", "3"),
    ("dois mil e 500", "2500"),
    ("87 e cinquenta", "87.50"),
    # -- gíria -------------------------------------------------------------
    ("cinquenta conto", "50"),
    ("cinquenta contos", "50"),
    ("cinquentão", "50"),
    ("Um cinquentão no posto.", "50"),
    ("duzentos pila", "200"),
    ("mil pau", "1000"),
    ("50 reis", "50"),
    # -- "meia" ------------------------------------------------------------
    ("sete e meia", "7.50"),
    ("sete reais e meia", "7.50"),
    ("um e meia", "1.50"),
    # -- frases inteiras: o preço entre os outros números -------------------
    ("anota oitenta e sete reais no Outback, no cartão, ontem", "87"),
    ("Anota 87 no Outback.", "87"),
    ("gastei 50 reais dia 19", "50"),
    ("no dia dois gastei cinquenta reais", "50"),
    ("no dia 19 50 reais de gasolina", "50"),
    ("dia dezenove, mil reais de aluguel", "1000"),
    ("19 de novembro, 50 reais", "50"),
    ("50 reais em 19 de novembro de 2026", "50"),
    ("cinquenta reais no dia dois de novembro de dois mil e vinte e seis", "50"),
    ("paguei 50 no dia 19/11", "50"),
    ("19/11/2026 gastei 120", "120"),
    ("às 8 gastei 30 reais na padaria", "30"),
    ("gastei 30 reais às 8 e 30", "30"),
    ("gastei 30 reais às oito e meia", "30"),
    ("30 reais as 18h45", "30"),
    ("30 reais as 8:30", "30"),
    ("30 reais às 8h", "30"),
    ("trinta reais oito da noite", "30"),
    ("trinta reais sete e meia da noite", "30"),
    ("mil e duzentos em 3 vezes", "1200"),
    ("1.200 em 3x no cartão", "1200"),
    ("1200 em 12 x", "1200"),
    ("600 reais parcelado em 3", "600"),
    ("seiscentos reais em três vezes", "600"),
    ("dois mil tres vezes", "2000"),
    ("600 reais em 3 parcelas", "600"),
    ("2 pizzas por 50 reais", "50"),
    ("comprei 2 pizzas, 50 reais", "50"),
    ("comprei 2 por 50", "50"),
    ("duas pizzas, cinquenta reais", "50"),
    ("anota um gasto de 50 reais", "50"),
    ("anota um gasto de cinquenta no mercado", "50"),
    ("uma pizza de quarenta e cinco", "45"),
    ("3 dias de hotel, 900 reais", "900"),
    ("2 quilos de carne 80 reais", "80"),
    ("paguei 100 reais com 10 por cento de desconto", "100"),
    ("100 reais com 10% de desconto", "100"),
    ("50 reais, isso, 50", "50"),  # o mesmo valor duas vezes não é dúvida
    ("cinquenta reais, 50 reais", "50"),
    ("gastei 87 mercado", "87"),
    ("anota 8 mercado", "8"),  # só há este número: vale como reserva
    ("o valor é cinquenta", "50"),
    ("50 reais semana passada", "50"),
    ("segunda vez que pago 30 reais", "30"),
    ("comprei por cento e vinte reais", "120"),
    ("paguei cento e vinte com dez por cento de desconto", "120"),
    ("Vou lancar oitenta e sete reais no Outback, no cartao, ontem, quinta, dia dezenove.", "87"),
]


@pytest.mark.parametrize(("frase", "esperado"), VALORES)
def test_extrai_o_valor(frase: str, esperado: str) -> None:
    resultado = extrair_valor(frase)
    assert resultado.status is StatusValor.OK, resultado
    assert resultado.valor == Decimal(esperado)
    # Sempre duas casas, e sempre Decimal: é o que vai ser comparado e gravado.
    assert isinstance(resultado.valor, Decimal)
    assert resultado.valor.as_tuple().exponent == -2


@pytest.mark.parametrize(("frase", "esperado"), VALORES)
def test_maiusculas_e_ponto_final_nao_mudam_o_valor(frase: str, esperado: str) -> None:
    # O Whisper decide sozinho a caixa e a pontuação do fim.
    for variante in (frase.upper(), frase.capitalize() + ".", f"  {frase} "):
        resultado = extrair_valor(variante)
        assert resultado.status is StatusValor.OK, (variante, resultado)
        assert resultado.valor == Decimal(esperado), variante


AMBIGUAS = [
    # dois valores diferentes
    "50 reais no mercado e 30 reais na farmácia",
    "50 reais e 30 reais",
    "gastei 50 no mercado e 30 na farmácia",
    "cinquenta ou quinze reais",
    "87 e 150",
    "87,50 e 20",
    "anota 50, não, 60 reais",
    "CEP 14538-133",
    "gastei cinquenta almoçando e quinze reais de estacionamento",
    "vinte cinco reais",
    "24 cervejas por 80 reais",
    "oitenta e sete vírgula cinquenta",
    "50 a 60 reais",
    "de 50 para 40 reais",
    "posto 7, 200 reais",
    # a vírgula separa: mil e duzentos? mil, e depois duzentos e cinquenta?
    "Mil, duzentos e cinquenta reais",
    # data ou dinheiro
    "dia dois mil reais",
    "dia 19 mil reais",
    "dia vinte e cinco reais",
    # centavos que não se decidem
    "oitenta e sete e cinco",
    "cem e cinquenta",
    "mil e meia",
    "duzentos e cinquenta centavos",
    # formato de dígitos que não se decide
    "1,200",
    "1.2.3 reais",
    "12,345 reais",
    "1.200 mil",
]


@pytest.mark.parametrize("frase", AMBIGUAS)
def test_na_duvida_nao_escolhe(frase: str) -> None:
    resultado = extrair_valor(frase)
    assert resultado.status is StatusValor.AMBIGUO, resultado
    assert resultado.valor is None


def test_ambiguidade_devolve_os_candidatos_na_ordem_da_frase() -> None:
    resultado = extrair_valor("50 reais no mercado e 30 reais na farmácia")
    assert resultado.candidatos == (Decimal(50), Decimal(30))


SEM_VALOR = [
    "",
    "anota um gasto",
    "anota um gasto no Outback, no cartão, ontem",
    "gastei no mercado dia 19",
    "no dia dois",
    "às 8",
    "às oito e meia",
    "em 3 vezes",
    "3x no cartão",
    "19/11",
    "19 de novembro de 2026",
    "alguns reais",
    "meia",
    "dez por cento",
    "10%",
    "uma pizza e duas cervejas",
    "Obrigado.",
    "Legendas pela comunidade Amara.org",
    "18h45",
    "o CEP é 04538",
    "05 de novembro",
]


@pytest.mark.parametrize("frase", SEM_VALOR)
def test_frase_sem_valor(frase: str) -> None:
    assert extrair_valor(frase) == ValorExtraido(StatusValor.NAO_ACHOU)


INVALIDAS = [
    "0 reais",
    "zero reais",
    "R$ 0,00",
    "menos 50 reais",
    "-50 reais",
    "R$ -50",
    "1.000.000 reais",
    "1000000",
    "um milhão de reais",
    "dois milhões",
    "2 milhões de reais",
    "999.999,999",
    "1.000.000,00",
]


@pytest.mark.parametrize("frase", INVALIDAS)
def test_zero_negativo_e_absurdo_nao_passam(frase: str) -> None:
    resultado = extrair_valor(frase)
    # AMBIGUO também serve para "999.999,999" e "mil mil": o que não pode é OK.
    assert resultado.status in (StatusValor.INVALIDO, StatusValor.AMBIGUO), resultado
    assert resultado.valor is None


@pytest.mark.parametrize("frase", ["0 reais", "menos 50 reais", "-50 reais", "um milhão de reais"])
def test_invalido_e_invalido_e_nao_ambiguo(frase: str) -> None:
    assert extrair_valor(frase).status is StatusValor.INVALIDO


def test_o_teto_e_o_do_servico() -> None:
    assert extrair_valor("999.999,99").valor == LIMITE
    assert extrair_valor("1.000.000").status is StatusValor.INVALIDO


# ---------------------------------------------------------------------------
# 2. conferir_valor
# ---------------------------------------------------------------------------

CONFERENCIAS = [
    # frase, proposto pelo modelo, resultado
    ("oitenta e sete e cinquenta", Decimal("87.50"), Conferencia.CONFERE),
    ("oitenta e sete e cinquenta", Decimal("87.5"), Conferencia.CONFERE),
    ("oitenta e sete e cinquenta", 87.5, Conferencia.CONFERE),  # float do JSON
    ("oitenta e sete e cinquenta", "87.50", Conferencia.CONFERE),
    ("cinquenta reais", 50, Conferencia.CONFERE),
    ("cinquenta reais", 50.0, Conferencia.CONFERE),
    ("19,90", 19.9, Conferencia.CONFERE),
    # o caso para o qual isto existe: o STT ou o modelo trocou o número
    ("cinquenta reais", 15, Conferencia.DIVERGE),
    ("cinquenta reais", Decimal("50.01"), Conferencia.DIVERGE),
    ("oitenta e sete e cinquenta", 87, Conferencia.DIVERGE),
    ("mil e duzentos", 1250, Conferencia.DIVERGE),
    # parser em dúvida: o modelo escolher um dos dois não resolve
    ("50 reais no mercado e 30 na farmácia", 50, Conferencia.DIVERGE),
    ("dia dois mil reais", 1000, Conferencia.DIVERGE),
    ("um milhão de reais", 1000000, Conferencia.DIVERGE),
    # parser sem número: segue, apoiado na leitura de volta
    ("anota um gasto no Outback", 87, Conferencia.SEM_PARSER),
    ("", Decimal("12.90"), Conferencia.SEM_PARSER),
    # valor do modelo que não presta diverge mesmo sem parser
    ("anota um gasto no Outback", 0, Conferencia.DIVERGE),
    ("anota um gasto no Outback", -5, Conferencia.DIVERGE),
    ("anota um gasto no Outback", 1000000, Conferencia.DIVERGE),
    ("anota um gasto no Outback", Decimal("12.345"), Conferencia.DIVERGE),
    ("anota um gasto no Outback", 0.1 + 0.2, Conferencia.DIVERGE),
    ("anota um gasto no Outback", "cinquenta", Conferencia.DIVERGE),
    ("anota um gasto no Outback", "", Conferencia.DIVERGE),
    ("anota um gasto no Outback", None, Conferencia.DIVERGE),
    ("anota um gasto no Outback", True, Conferencia.DIVERGE),
    ("anota um gasto no Outback", float("nan"), Conferencia.DIVERGE),
    ("anota um gasto no Outback", float("inf"), Conferencia.DIVERGE),
    ("anota um gasto no Outback", "1e999999", Conferencia.DIVERGE),
    ("cinquenta reais", None, Conferencia.DIVERGE),
]


@pytest.mark.parametrize(("frase", "proposto", "esperado"), CONFERENCIAS)
def test_confere_com_o_modelo(frase: str, proposto: object, esperado: Conferencia) -> None:
    assert conferir_valor(extrair_valor(frase), proposto) is esperado  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. valor_por_extenso
# ---------------------------------------------------------------------------

POR_EXTENSO = [
    ("87.50", "oitenta e sete reais e cinquenta centavos"),
    ("87", "oitenta e sete reais"),
    ("1", "um real"),
    ("1.01", "um real e um centavo"),
    ("2", "dois reais"),
    ("0.90", "noventa centavos"),
    ("0.01", "um centavo"),
    ("0.02", "dois centavos"),
    ("19.90", "dezenove reais e noventa centavos"),
    ("100", "cem reais"),
    ("101", "cento e um reais"),
    ("110", "cento e dez reais"),
    ("200", "duzentos reais"),
    ("999", "novecentos e noventa e nove reais"),
    ("1000", "mil reais"),
    ("1001", "mil e um reais"),
    ("1050", "mil e cinquenta reais"),
    ("1100", "mil e cem reais"),
    ("1200", "mil e duzentos reais"),
    ("1250", "mil duzentos e cinquenta reais"),
    ("1101", "mil cento e um reais"),
    ("2000", "dois mil reais"),
    ("2300", "dois mil e trezentos reais"),
    ("21000", "vinte e um mil reais"),
    ("25000", "vinte e cinco mil reais"),
    ("100000", "cem mil reais"),
    ("101000", "cento e um mil reais"),
    ("100100", "cem mil e cem reais"),
    ("1249.90", "mil duzentos e quarenta e nove reais e noventa centavos"),
    (
        "999999.99",
        "novecentos e noventa e nove mil novecentos e noventa e nove reais"
        " e noventa e nove centavos",
    ),
]


@pytest.mark.parametrize(("valor", "esperado"), POR_EXTENSO)
def test_escreve_o_valor_por_extenso(valor: str, esperado: str) -> None:
    assert valor_por_extenso(Decimal(valor)) == esperado


def test_extenso_aceita_int_e_decimal_com_zeros() -> None:
    assert valor_por_extenso(50) == "cinquenta reais"
    assert valor_por_extenso(Decimal("50.0")) == "cinquenta reais"
    assert valor_por_extenso(Decimal("50.000")) == "cinquenta reais"


@pytest.mark.parametrize(
    "valor", ["0", "-1", "1000000", "999999.991", "0.001", "12.345", "NaN", "Infinity"]
)
def test_extenso_recusa_o_que_nao_se_grava(valor: str) -> None:
    with pytest.raises(ValueError):
        valor_por_extenso(Decimal(valor))


@pytest.mark.parametrize("valor", [87.5, "87.50", None, True])
def test_extenso_recusa_o_que_nao_e_decimal(valor: object) -> None:
    with pytest.raises(TypeError):
        valor_por_extenso(valor)  # type: ignore[arg-type]


def _sem_simbolo(texto: str) -> bool:
    """Só letras minúsculas sem acento, espaço e vírgula: o que o Piper lê bem."""
    return all(c in "abcdefghijklmnopqrstuvwxyz ," for c in texto)


# ---------------------------------------------------------------------------
# Ida e volta: o que o código escreve, o código lê
# ---------------------------------------------------------------------------


def _amostra() -> list[Decimal]:
    """Alguns milhares de valores, com semente fixa, mais todas as bordas."""
    sorteio = random.Random(20261120)
    centavos = {1, 2, 9, 10, 11, 99, 100, 101, 110, 199, 1000, 10000, 100000, 99999999}
    centavos |= {n * 100 for n in range(1, 1300)}  # todo inteiro até 1.299
    centavos |= {n for n in range(1, 300)}  # todo valor até 2,99
    centavos |= {sorteio.randint(1, 99999999) for _ in range(3000)}
    centavos |= {sorteio.randint(1, 500000) for _ in range(2000)}  # o dia a dia
    centavos |= {sorteio.randint(1, 999) * 100000 for _ in range(300)}  # milhares redondos
    return [Decimal(c) / 100 for c in sorted(centavos)]


AMOSTRA = _amostra()


def test_ida_e_volta_do_valor() -> None:
    for valor in AMOSTRA:
        texto = valor_por_extenso(valor)
        assert _sem_simbolo(texto) and "," not in texto, texto
        resultado = extrair_valor(texto)
        assert resultado.status is StatusValor.OK, (valor, texto, resultado)
        assert resultado.valor == valor, (valor, texto)


# Frases com data, hora e parcela em volta do valor, do jeito que se fala.
MOLDES = [
    "anota {v} no Outback, no cartão, ontem",
    "Gastei {v} no mercado dia 19.",
    "{v} em 3 vezes no cartão",
    "{v} em 3x no dia 19",
    "no dia dois de novembro paguei {v} de aluguel",
    "às 8 gastei {v} na padaria",
    "ontem, quinta, dia dezenove, {v} no posto",
    "19/11, {v}, parcelado em 10 vezes",
    "comprei 2 pizzas por {v} às 20h30",
    "Vou lancar {v} no Outback, no cartao, ontem, quinta, dia dezenove. Confirma?",
    "paguei {v} sexta passada, em doze vezes",
    "dia 19 de novembro de 2026: {v}",
]


def test_ida_e_volta_dentro_de_frases() -> None:
    sorteio = random.Random(87)
    for valor in AMOSTRA:
        molde = sorteio.choice(MOLDES)
        frase = molde.format(v=valor_por_extenso(valor))
        resultado = extrair_valor(frase)
        assert resultado.status is StatusValor.OK, (frase, resultado)
        assert resultado.valor == valor, frase


def test_ida_e_volta_com_digitos_brasileiros() -> None:
    # "R$ 1.249,90", como o Whisper escreve quando resolve usar dígitos.
    sorteio = random.Random(1249)
    for valor in AMOSTRA:
        reais, centavos = divmod(int(valor * 100), 100)
        escrito = f"{reais:,}".replace(",", ".") + f",{centavos:02d}"
        molde = sorteio.choice(MOLDES)
        for frase in (molde.format(v=f"R$ {escrito}"), molde.format(v=f"{escrito} reais")):
            resultado = extrair_valor(frase)
            assert resultado.status is StatusValor.OK, (frase, resultado)
            assert resultado.valor == valor, frase


# ---------------------------------------------------------------------------
# 4. resolver_data
# ---------------------------------------------------------------------------

DATAS = [
    # frase, data esperada -- HOJE é sexta, 20/11/2026
    ("hoje", date(2026, 11, 20)),
    ("Hoje.", date(2026, 11, 20)),
    ("ontem", date(2026, 11, 19)),
    ("Ontem à noite.", date(2026, 11, 19)),
    ("anteontem", date(2026, 11, 18)),
    ("antes de ontem", date(2026, 11, 18)),
    # dia da semana: o mais recente antes de hoje
    ("quinta", date(2026, 11, 19)),
    ("na quinta-feira", date(2026, 11, 19)),
    ("Quinta-feira.", date(2026, 11, 19)),
    ("segunda", date(2026, 11, 16)),
    ("na segunda passada", date(2026, 11, 16)),
    ("terça", date(2026, 11, 17)),
    ("sábado", date(2026, 11, 14)),
    ("no sábado passado", date(2026, 11, 14)),
    ("domingo", date(2026, 11, 15)),
    ("sexta", date(2026, 11, 13)),  # dito numa sexta: a de sete dias atrás
    ("sexta passada", date(2026, 11, 13)),
    ("na última sexta-feira", date(2026, 11, 13)),
    ("sexta-feira passada", date(2026, 11, 13)),
    ("quinta passada, dia 12", date(2026, 11, 12)),
    # dia do mês
    ("dia 19", date(2026, 11, 19)),
    ("no dia dezenove", date(2026, 11, 19)),
    ("dia 20", date(2026, 11, 20)),
    ("dia 2", date(2026, 11, 2)),
    ("no dia dois", date(2026, 11, 2)),
    ("dia primeiro", date(2026, 11, 1)),
    ("dia 1º", date(2026, 11, 1)),
    ("dia 21", date(2026, 10, 21)),  # ainda não chegou: é o do mês passado
    ("dia 31", date(2026, 10, 31)),
    ("dia vinte e cinco", date(2026, 10, 25)),
    ("dia trinta e um", date(2026, 10, 31)),
    # dia e mês
    ("19 de novembro", date(2026, 11, 19)),
    ("dia 19 de novembro", date(2026, 11, 19)),
    ("dezenove de novembro", date(2026, 11, 19)),
    ("dois de novembro", date(2026, 11, 2)),
    ("primeiro de março", date(2026, 3, 1)),
    ("1º de maio", date(2026, 5, 1)),
    ("25 de dezembro", date(2025, 12, 25)),  # sem ano, e não pode ser futuro
    ("19 de novembro de 2026", date(2026, 11, 19)),
    ("19 de novembro de 2025", date(2025, 11, 19)),
    ("dia dois de novembro de dois mil e vinte e cinco", date(2025, 11, 2)),
    ("19/11", date(2026, 11, 19)),
    ("19/11/2026", date(2026, 11, 19)),
    ("19/11/26", date(2026, 11, 19)),
    ("05/03", date(2026, 3, 5)),
    ("25/12", date(2025, 12, 25)),
    ("29/02/2024", date(2024, 2, 29)),
    # várias menções do mesmo dia
    ("ontem, quinta, dia dezenove", date(2026, 11, 19)),
    ("hoje, sexta", date(2026, 11, 20)),
    ("quinta, dia 19", date(2026, 11, 19)),
    ("sexta, dia 13", date(2026, 11, 13)),
    ("ontem, dia 19/11", date(2026, 11, 19)),
    # a data no meio do resto da frase
    ("anota oitenta e sete reais no Outback, no cartão, ontem", date(2026, 11, 19)),
    ("gastei 50 reais dia 19 em 3 vezes", date(2026, 11, 19)),
    ("R$ 87,50 na quarta", date(2026, 11, 18)),
    ("dia dois mil reais", date(2026, 11, 2)),
    # "segunda" que não é dia
    ("é a segunda vez que pago isso hoje", date(2026, 11, 20)),
    ("a segunda parcela, ontem", date(2026, 11, 19)),
]


@pytest.mark.parametrize(("frase", "esperada"), DATAS)
def test_resolve_a_data(frase: str, esperada: date) -> None:
    resultado = resolver_data(frase, HOJE)
    assert resultado.status is StatusData.OK, resultado
    assert resultado.data == esperada
    assert resultado.dita is True


@pytest.mark.parametrize(
    "frase",
    [
        "",
        "anota 50 reais no Outback",
        "87,50 no cartão",
        "em 3 vezes",
        "às 8 da noite",
        "1.200 reais",
        "a segunda parcela",
        "cinquenta de gasolina",
    ],
)
def test_sem_data_dita_e_hoje(frase: str) -> None:
    resultado = resolver_data(frase, HOJE)
    assert (resultado.status, resultado.data, resultado.dita) == (StatusData.OK, HOJE, False)


@pytest.mark.parametrize(
    "frase",
    [
        "semana passada",
        "na semana passada",
        "segunda da semana passada",
        "no fim de semana",
        "essa semana",
        "mês passado",
        "noite passada",
        "em novembro",
        "no fim do mês",
        "no feriado",
        "no natal",
        "paguei hoje a conta de ontem",
        "ano passado",
        "há três dias",
        "faz 2 dias",
        "três dias atrás",
        "outro dia",
        "esses dias",
        "segunda retrasada",
        "ontem ou anteontem",
        "ontem, dia 18",  # não é o mesmo dia
        "ontem, segunda",  # ontem foi quinta
        "sexta, dia 19",  # dia 19 foi quinta
        "hoje, quinta",
        "dia 19 e dia 18",
        "segunda ou terça",
    ],
)
def test_data_ambigua_pergunta(frase: str) -> None:
    resultado = resolver_data(frase, HOJE)
    assert resultado.status is StatusData.AMBIGUA, resultado
    assert resultado.data is None


@pytest.mark.parametrize(
    "frase",
    [
        "amanhã",
        "depois de amanhã",
        "sexta que vem",
        "na próxima segunda",
        "segunda-feira que vem",
        "21/11/2026",
        "19/11/2027",
        "25 de dezembro de 2026",
        "dia 19 de novembro de dois mil e vinte e sete",
    ],
)
def test_data_futura_e_recusada(frase: str) -> None:
    resultado = resolver_data(frase, HOJE)
    assert resultado.status is StatusData.FUTURA, resultado
    assert resultado.data is None


@pytest.mark.parametrize(
    "frase",
    [
        "30 de fevereiro", "31/04", "dia 45", "32 de março", "19/13", "29/02/2026", "dia 0",
        "dia 19.11",
    ],
)
def test_data_que_nao_existe(frase: str) -> None:
    assert resolver_data(frase, HOJE).status is StatusData.INVALIDA


@pytest.mark.parametrize(
    ("hoje", "frase", "esperada"),
    [
        # as bordas de mês e de ano, com o "hoje" fixo de cada uma (D26)
        (date(2026, 1, 2), "dia 30", date(2025, 12, 30)),
        (date(2026, 1, 2), "30 de dezembro", date(2025, 12, 30)),
        (date(2026, 1, 1), "ontem", date(2025, 12, 31)),
        (date(2026, 3, 1), "anteontem", date(2026, 2, 27)),
        (date(2024, 3, 1), "ontem", date(2024, 2, 29)),
        (date(2026, 11, 30), "dia 30", date(2026, 11, 30)),
        (date(2026, 11, 19), "quinta", date(2026, 11, 12)),
        (date(2026, 11, 21), "sexta", date(2026, 11, 20)),
    ],
)
def test_bordas_de_mes_e_de_ano(hoje: date, frase: str, esperada: date) -> None:
    assert resolver_data(frase, hoje).data == esperada


def test_dia_que_o_mes_passado_nao_tem() -> None:
    # 1º de março, "dia 30": fevereiro não tem dia 30. Não se inventa janeiro.
    assert resolver_data("dia 30", date(2026, 3, 1)).status is StatusData.INVALIDA


# ---------------------------------------------------------------------------
# 5. data_por_extenso
# ---------------------------------------------------------------------------

DATAS_POR_EXTENSO = [
    (date(2026, 11, 20), "hoje"),
    (date(2026, 11, 19), "ontem, quinta, dia dezenove"),
    (date(2026, 11, 18), "anteontem, quarta, dia dezoito"),
    (date(2026, 11, 17), "terca, dia dezessete"),
    (date(2026, 11, 14), "sabado, dia quatorze"),
    (date(2026, 11, 13), "dia treze de novembro"),
    (date(2026, 11, 2), "dia dois de novembro"),
    (date(2026, 11, 1), "dia primeiro de novembro"),
    (date(2026, 10, 31), "dia trinta e um de outubro"),
    (date(2026, 3, 21), "dia vinte e um de marco"),
    (date(2025, 12, 30), "dia trinta de dezembro de dois mil e vinte e cinco"),
]


@pytest.mark.parametrize(("data", "esperado"), DATAS_POR_EXTENSO)
def test_escreve_a_data_por_extenso(data: date, esperado: str) -> None:
    assert data_por_extenso(data, HOJE) == esperado


def test_extenso_da_data_na_virada_do_mes_e_do_ano() -> None:
    assert data_por_extenso(date(2026, 10, 31), date(2026, 11, 1)) == (
        "ontem, sabado, dia trinta e um de outubro"
    )
    assert data_por_extenso(date(2025, 12, 31), date(2026, 1, 1)) == (
        "ontem, quarta, dia trinta e um de dezembro"
    )
    assert data_por_extenso(date(2026, 11, 1), date(2026, 11, 3)) == (
        "anteontem, domingo, dia primeiro"
    )


def test_extenso_recusa_data_futura() -> None:
    with pytest.raises(ValueError):
        data_por_extenso(date(2026, 11, 21), HOJE)


def test_ida_e_volta_da_data() -> None:
    # Dois anos de "hoje" diferentes (pega bissexto e toda virada), e para cada
    # um todos os dias dos 400 anteriores: o que é lido de volta resolve igual.
    inicio = date(2023, 12, 25)
    for passo in range(0, 800, 7):
        hoje = inicio + timedelta(days=passo + passo % 5)
        for atras in list(range(70)) + list(range(70, 400, 13)):
            data = hoje - timedelta(days=atras)
            texto = data_por_extenso(data, hoje)
            assert _sem_simbolo(texto), texto
            resultado = resolver_data(texto, hoje)
            assert resultado.status is StatusData.OK, (hoje, data, texto, resultado)
            assert resultado.data == data, (hoje, texto)
            # E a data lida de volta não é confundida com dinheiro.
            assert extrair_valor(texto).status is StatusValor.NAO_ACHOU, texto


def test_leitura_de_volta_inteira() -> None:
    # A frase que o Piper vai falar, lida pelo mesmo código que a escreveu.
    valor, data = Decimal("87.50"), date(2026, 11, 19)
    frase = (
        f"Vou lancar {valor_por_extenso(valor)} no Outback, no cartao, "
        f"{data_por_extenso(data, HOJE)}. Confirma?"
    )
    assert frase == (
        "Vou lancar oitenta e sete reais e cinquenta centavos no Outback, no cartao, "
        "ontem, quinta, dia dezenove. Confirma?"
    )
    assert extrair_valor(frase).valor == valor
    assert resolver_data(frase, HOJE).data == data


# ---------------------------------------------------------------------------
# 6. classificar_confirmacao
# ---------------------------------------------------------------------------

SIM = [
    "sim",
    "Sim.",
    "SIM!",
    "Sim, sim.",
    "isso",
    "Isso.",
    "isso mesmo",
    "Isso aí.",
    "é isso",
    "É isso mesmo.",
    "confirma",
    "Confirma.",
    "confirmo",
    "confirmado",
    "pode",
    "Pode.",
    "pode sim",
    "pode lançar",
    "Pode lançar.",
    "Sim, pode lançar.",
    "pode gravar",
    "pode confirmar",
    "tá certo",
    "Tá certo.",
    "está certo",
    "certo",
    "correto",
    "exato",
    "claro",
    "Claro!",
    "claro que sim",
    "com certeza",
    "ok",
    "Ok.",
    "beleza",
    "tá bom",
    "isso, pode lançar",
    "Sim, por favor.",
    "pode lançar, por favor",
    "Isso, confirma.",
    "manda ver",
    "perfeito",
]

NAO = [
    "não",
    "Não.",
    "NÃO!",
    "Não, não.",
    "nao",
    "cancela",
    "Cancela.",
    "cancelar",
    "pode cancelar",
    "esquece",
    "Esquece.",
    "esqueça",
    "deixa",
    "Deixa.",
    "deixa pra lá",
    "Deixa quieto.",
    "errado",
    "tá errado",
    "Está errado.",
    "não é isso",
    "Não, não é isso.",
    "não, cancela",
    "Não, esquece.",
    "não quero",
    "não precisa",
    "negativo",
    "nada disso",
    "claro que não",
    "de jeito nenhum",
    "Não, por favor.",
    "não tá certo",
    "Para.",
]

# Correção, dúvida, mistura ou ruído. Nenhuma destas pode gravar.
OUTRA = [
    "",
    "   ",
    "...",
    # misturadas: sem pontuação confiável, não se sabe o que é
    "não, pode lançar",
    "Não pode lançar.",
    "não pode",
    "pode não",
    "sim, não",
    "não, sim",
    "isso não",
    "tá certo não",
    "não confirma",
    "certo, cancela",
    "sim, cancela",
    "pode deixar",
    # correções: voltam ao modelo
    "sim, mas foi cinquenta",
    "Sim, mas foi 50.",
    "não, foi cinquenta",
    "Não, foram 50 reais.",
    "foi cinquenta",
    "50",
    "sim, 50",
    "sim, cinquenta reais",
    "isso, no débito",
    "pode, mas no crédito",
    "sim, só que foi ontem",
    "é no cartão",
    "confirma o valor",
    "pode lançar no débito",
    # dúvida
    "não sei",
    "Não sei.",
    "acho que sim",
    "acho que não",
    "talvez",
    "sim, eu acho",
    "sei lá",
    "Sim?",
    "Sim...",
    "Certo...",
    "Pode…",
    "Pode?",
    "confirma?",
    "Não?",
    "como assim",
    "o quê",
    "repete",
    "espera",
    "quanto",
    # o que o Whisper escreve a partir de ruído
    "Obrigado.",
    "Obrigada.",
    "Sim, obrigado.",
    "Legendas pela comunidade Amara.org",
    "Legendas pela comunidade...",
    "Tchau.",
    "E aí.",
    "É.",
    "Tá.",
    "Uhum.",
    "Aham.",
    "Hã?",
    "Música",
    "por favor",
    "Ideraldinho",
    # outros comandos que caem na janela de confirmação
    "anota cinquenta reais",
    "toca Chico Buarque",
    "que horas são",
    "simplesmente",
    "simão",
    "podemos",
    "nao sim nao sim",
]


@pytest.mark.parametrize("frase", SIM)
def test_sim(frase: str) -> None:
    assert classificar_confirmacao(frase) is Confirmacao.SIM


@pytest.mark.parametrize("frase", NAO)
def test_nao(frase: str) -> None:
    assert classificar_confirmacao(frase) is Confirmacao.NAO


@pytest.mark.parametrize("frase", OUTRA)
def test_outra(frase: str) -> None:
    assert classificar_confirmacao(frase) is Confirmacao.OUTRA


def test_nenhum_nao_vira_sim() -> None:
    # O critério do plano, dito de uma vez: sobre tudo o que não é um sim
    # limpo, em qualquer caixa e com qualquer pontuação no fim, zero SIM.
    for frase in NAO + OUTRA:
        for fim in ("", ".", "!", ","):
            variante = frase + fim
            assert classificar_confirmacao(variante) is not Confirmacao.SIM, variante
            assert classificar_confirmacao(variante.upper()) is not Confirmacao.SIM, variante


def test_qualquer_palavra_a_mais_derruba_o_sim() -> None:
    # Um sim seguido ou precedido de qualquer coisa fora das listas deixa de
    # ser sim -- inclusive de um "não" e de um número.
    for frase in SIM:
        for extra in ("não", "mas", "cinquenta", "50", "acho", "obrigado", "talvez"):
            for variante in (f"{frase} {extra}", f"{extra} {frase}", f"{frase}, {extra}."):
                assert classificar_confirmacao(variante) is not Confirmacao.SIM, variante
