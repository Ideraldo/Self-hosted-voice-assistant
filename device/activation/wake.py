"""A palavra de ativação: ouvir a sala o dia inteiro esperando um nome.

É a Fase 7 do plano, e a primeira coisa do projeto que roda **sem ninguém ter
pedido nada**. Isso muda a natureza do erro: o STT errar custa uma frase
repetida, o wake word errar custa o aparelho acordar sozinho às três da manhã.
O critério de aceite é por isso um número de falso positivo por hora, e não uma
taxa de acerto.

**O modelo é openWakeWord** (plano, seção 9), que roda um classificador pequeno
sobre embeddings de áudio. Medido nesta máquina em 03/09/2026: carrega em 0,15 s
e gasta 2,5 ms por 80 ms de áudio -- RTF 0,031. Sobra folga para a Pi ser cinco
vezes mais lenta e ainda ficar em 0,15, mas quem decide isso é a medição lá.

**Por que "Ideraldinho" e não "Marcos" (D29):** cinco sílabas e uma palavra que
não aparece em mais nada. Nome curto e comum é o pior formato possível para algo
que fica escutando a sala -- ele dispara na conversa alheia e na televisão.

Esta camada não decide nada sobre o turno. Ela responde uma pergunta só:
*ouviram o nome?* Quem transiciona estado é o `main`.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("ideraldinho.wake")

#: O openWakeWord trabalha em blocos de 1280 amostras -- 80 ms a 16 kHz. Não é
#: escolha nossa: é o passo do extrator de features dele. A captura entrega
#: frames de 30 ms (o que o webrtcvad aceita), então alguém tem que juntar os
#: pedaços, e é isto aqui.
CHUNK_SAMPLES = 1280

#: Quanto tempo ignorar o detector depois de um acerto. Sem isso, a mesma
#: palavra dispara em três blocos seguidos e o aparelho acorda três vezes.
REFRATARIO_S = 2.0


class WakeWord:
    """Ouve blocos de áudio e diz quando o nome foi dito.

    Uso: `feed(frame)` com o PCM cru da captura, quantas vezes quiser. Devolve
    `True` uma vez por ativação.

    **Não pode derrubar o aparelho**, pela mesma regra do rosto (D27) e da
    leitura de página (D28): se o modelo não carrega, `disponivel` fica falso e
    o `main` cai para o modo sem wake word. Um aparelho que não acorda pelo nome
    ainda é um aparelho; um aparelho que não sobe não é nada.
    """

    def __init__(
        self,
        modelo: str,
        threshold: float = 0.5,
        refratario_s: float = REFRATARIO_S,
        rate: int = 16000,
    ) -> None:
        self.modelo = modelo
        self.threshold = threshold
        self.rate = rate
        self._refratario_blocos = int(refratario_s * rate / CHUNK_SAMPLES)
        self._silenciado = 0
        self._buffer = bytearray()
        self._model = None
        #: A última pontuação vista. Existe para o `scripts/wake.py` mostrar o
        #: número enquanto se procura o threshold -- que é um valor que só a
        #: sala real define.
        self.ultima_pontuacao = 0.0

    @property
    def disponivel(self) -> bool:
        return self._model is not None

    def carregar(self) -> bool:
        """Carrega o modelo. Devolve se deu certo -- nunca levanta."""
        try:
            from openwakeword.model import Model

            # `onnx` e não `tflite`: o onnxruntime já está no processo por causa
            # do Piper, e trazer um segundo runtime para a Pi por um modelo de
            # 200 KB seria caro pelo motivo errado.
            self._model = Model(wakeword_models=[self.modelo], inference_framework="onnx")
        except Exception as exc:  # noqa: BLE001 -- ver o docstring da classe
            log.warning("wake word indisponivel (%s): %s", self.modelo, exc)
            self._model = None
            return False
        log.info("wake word: %s, threshold %.2f", self.modelo, self.threshold)
        return True

    def feed(self, frame: bytes) -> bool:
        """Entrega um frame de captura. True quando o nome foi ouvido."""
        if self._model is None:
            return False

        self._buffer.extend(frame)
        ativou = False
        while len(self._buffer) >= CHUNK_SAMPLES * 2:
            bloco = bytes(self._buffer[: CHUNK_SAMPLES * 2])
            del self._buffer[: CHUNK_SAMPLES * 2]
            if self._passo(bloco):
                ativou = True
        return ativou

    def _passo(self, bloco: bytes) -> bool:
        amostras = np.frombuffer(bloco, dtype=np.int16)
        try:
            pontuacoes = self._model.predict(amostras)
        except Exception as exc:  # noqa: BLE001
            log.warning("predict falhou: %s", exc)
            return False

        self.ultima_pontuacao = max(pontuacoes.values()) if pontuacoes else 0.0

        if self._silenciado > 0:
            self._silenciado -= 1
            return False
        if self.ultima_pontuacao < self.threshold:
            return False

        log.info("ativou: %.3f", self.ultima_pontuacao)
        self._silenciado = self._refratario_blocos
        return True

    def reset(self) -> None:
        """Esquece o que ouviu. Chamado quando o aparelho volta para IDLE.

        O modelo guarda estado interno entre blocos -- é assim que ele reconhece
        uma palavra que atravessa vários deles. Depois de um turno inteiro, esse
        estado é a resposta do próprio aparelho, e reaproveitá-lo é o caminho
        mais curto para ele acordar com a própria voz.
        """
        self._buffer.clear()
        self._silenciado = 0
        if self._model is not None:
            try:
                self._model.reset()
            except Exception as exc:  # noqa: BLE001
                log.debug("reset do modelo falhou: %s", exc)
