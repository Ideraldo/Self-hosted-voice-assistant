"""The scoring must measure recognition, not formatting habits."""

import pytest

from lab.metrics import cer, normalize, wer
from lab.numbers import spell


@pytest.mark.parametrize(
    "number,expected",
    [
        (0, "zero"), (7, "sete"), (10, "dez"), (15, "quinze"), (17, "dezessete"),
        (23, "vinte e tres"), (30, "trinta"), (100, "cem"), (133, "cento e trinta e tres"),
        (249, "duzentos e quarenta e nove"), (1000, "mil"), (1249, "mil e duzentos e quarenta e nove"),
    ],
)
def test_spell(number, expected):
    assert spell(number) == expected


def test_digits_and_words_score_the_same():
    assert wer("Timer de dez minutos.", "Timer de 10 minutos.") == 0.0
    assert cer("Timer de dez minutos.", "Timer de 10 minutos.") == 0.0


def test_case_accents_and_punctuation_are_ignored():
    assert normalize("Já são QUINZE, para as sete!") == "ja sao quinze para as sete"


def test_real_error_is_still_counted():
    assert wer("acende a luz do quarto", "apaga a luz do quarto") == pytest.approx(0.2)


def test_hour_marker_matches_spoken_form():
    """"18h45" e "dezoito e quarenta e cinco" sao a mesma resposta."""
    assert wer(
        "Faltam vinte e sete minutos para as dezoito e quarenta e cinco.",
        "Faltam 27 minutos para as 18h45.",
    ) == 0.0


def test_thousands_separator_is_not_two_numbers():
    assert normalize("R$ 2.300").endswith("dois mil e trezentos")
    assert "dois trezentos" not in normalize("R$ 2.300")


def test_decimal_comma_becomes_virgula():
    assert "virgula" in normalize("1249,90")


def test_untrained_keys_exclui_as_frases_do_corpus():
    """A bancada tem duas frases que viraram material de treino da voz propria."""
    from lab.phrases import PHRASES
    from lab.run_tts import untrained_keys

    keys = untrained_keys()
    assert "curta" not in keys and "acentos" not in keys
    assert set(keys) < set(PHRASES) and keys


def test_sintese_deterministica_e_reprodutivel():
    """Sem isso, comparar duas epocas mede ruido em vez de qualidade."""
    import hashlib

    # A voz e um binario fora do git (D12): onde ela nao existe -- o CI, uma
    # maquina recem-clonada -- o teste nao tem o que medir, e pular e mais
    # honesto do que falhar por um motivo que nao e o dele.
    pytest.importorskip("piper")
    from lab.tts.piper import MODEL_DIR, PiperTTS

    if not (MODEL_DIR / "pt_BR-ideraldo-medium.onnx").exists():
        pytest.skip("voz pt_BR-ideraldo-medium nao esta em lab/models/piper")

    engine = PiperTTS("pt_BR-ideraldo-medium", deterministic=True)
    digests = {
        hashlib.md5(engine.synthesize("frase de controle")[0].tobytes()).hexdigest()
        for _ in range(2)
    }
    assert len(digests) == 1, "a sintese de medicao voltou a variar entre rodadas"
