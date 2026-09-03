"""A palavra de ativação, sem microfone e sem modelo.

O que **não** se testa aqui: se o modelo acerta. Isso é taxa de falso positivo
por hora, depende da sala, do microfone e da televisão ligada, e só se mede com
o hardware final (plano, Fase 7).

O que se testa é tudo o que fica em volta e quebra calado: juntar frames de
30 ms em blocos de 80 ms sem perder amostra, não disparar três vezes na mesma
palavra, e -- a regra que vale mais que as outras -- **não derrubar o aparelho**
quando o modelo não carrega ou explode no meio.
"""

from __future__ import annotations

import numpy as np

from device.activation.wake import CHUNK_SAMPLES, WakeWord

#: Um frame de captura: 30 ms a 16 kHz, em bytes.
FRAME = b"\x00\x00" * 480


class FakeModel:
    """O openWakeWord de mentira. Devolve a pontuação que o teste mandar."""

    def __init__(self, pontuacoes=None, erro=None):
        self.pontuacoes = list(pontuacoes or [])
        self.erro = erro
        self.blocos: list[np.ndarray] = []
        self.resets = 0

    def predict(self, amostras):
        self.blocos.append(amostras)
        if self.erro:
            raise self.erro
        p = self.pontuacoes.pop(0) if self.pontuacoes else 0.0
        return {"ideraldinho": p}

    def reset(self):
        self.resets += 1


def detector(pontuacoes=None, erro=None, threshold=0.5, refratario_s=2.0):
    w = WakeWord("ideraldinho", threshold=threshold, refratario_s=refratario_s)
    w._model = FakeModel(pontuacoes, erro)
    return w


def alimentar(w: WakeWord, frames: int) -> int:
    """Entrega N frames e conta quantas ativações saíram."""
    return sum(bool(w.feed(FRAME)) for _ in range(frames))


class TestBlocos:
    def test_junta_frames_de_30ms_em_blocos_de_80ms(self):
        w = detector()
        # 480 amostras por frame; o modelo só pode ser chamado a cada 1280.
        w.feed(FRAME)
        w.feed(FRAME)
        assert w._model.blocos == []
        w.feed(FRAME)  # 1440 >= 1280
        assert len(w._model.blocos) == 1
        assert len(w._model.blocos[0]) == CHUNK_SAMPLES

    def test_nao_perde_amostra_entre_blocos(self):
        w = detector()
        # 16 frames = 7680 amostras = 6 blocos de 1280, sem sobra.
        alimentar(w, 16)
        assert len(w._model.blocos) == 6

    def test_um_frame_grande_vira_varios_blocos(self):
        w = detector()
        w.feed(b"\x00\x00" * 5000)
        assert len(w._model.blocos) == 3  # 5000 // 1280

    def test_o_bloco_chega_como_int16(self):
        w = detector()
        alimentar(w, 3)
        assert w._model.blocos[0].dtype == np.int16


class TestAtivacao:
    def test_dispara_acima_do_threshold(self):
        w = detector([0.9])
        assert alimentar(w, 3) == 1

    def test_nao_dispara_abaixo(self):
        w = detector([0.49])
        assert alimentar(w, 3) == 0

    def test_guarda_a_ultima_pontuacao_mesmo_sem_disparar(self):
        # É o número que `scripts/wake.py` mostra para achar o threshold: sem
        # ele, procurar o valor certo seria adivinhar.
        w = detector([0.31])
        alimentar(w, 3)
        assert w.ultima_pontuacao == 0.31

    def test_uma_palavra_nao_acorda_o_aparelho_tres_vezes(self):
        # A mesma palavra pontua alto em blocos seguidos. Sem o refratário, o
        # aparelho abriria três turnos para uma chamada só.
        w = detector([0.9] * 30)
        assert alimentar(w, 40) == 1

    def test_depois_do_refratario_dispara_de_novo(self):
        # 0.2 s de refratário = 2 blocos. 0.9 no bloco 1, silêncio, 0.9 depois.
        w = detector([0.9, 0.0, 0.0, 0.0, 0.9], refratario_s=0.2)
        assert alimentar(w, 14) == 2

    def test_threshold_configuravel(self):
        assert alimentar(detector([0.6], threshold=0.8), 3) == 0
        assert alimentar(detector([0.6], threshold=0.4), 3) == 1


class TestNaoDerruba:
    """A regra da camada: um wake word quebrado é um aparelho sem wake word,
    nunca um aparelho que não sobe."""

    def test_sem_modelo_carregado_nunca_dispara(self):
        w = WakeWord("ideraldinho")
        assert w.disponivel is False
        assert alimentar(w, 20) == 0

    def test_modelo_que_nao_existe_devolve_falso_em_vez_de_levantar(self):
        w = WakeWord("modelo-que-nao-existe-em-lugar-nenhum")
        assert w.carregar() is False
        assert w.disponivel is False

    def test_predict_que_explode_nao_levanta(self):
        w = detector(erro=RuntimeError("onnxruntime morreu"))
        assert alimentar(w, 10) == 0

    def test_reset_que_explode_nao_levanta(self):
        w = detector()

        def explode():
            raise RuntimeError("nao")

        w._model.reset = explode
        w.reset()  # não levanta

    def test_pontuacao_vazia_nao_quebra(self):
        w = detector()
        w._model.predict = lambda a: {}
        assert alimentar(w, 10) == 0


class TestReset:
    def test_limpa_o_buffer_e_o_modelo(self):
        w = detector()
        w.feed(FRAME)  # sobra meio bloco no buffer
        w.reset()
        assert len(w._buffer) == 0
        assert w._model.resets == 1

    def test_reset_libera_o_refratario(self):
        # Depois de um turno inteiro, esperar o refratário terminar seria fazer
        # o usuário chamar duas vezes.
        w = detector([0.9] * 10)
        alimentar(w, 4)
        w.reset()
        w._model.pontuacoes = [0.9]
        assert alimentar(w, 3) == 1
