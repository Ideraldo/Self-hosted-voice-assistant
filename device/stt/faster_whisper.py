"""faster-whisper rodando no dispositivo (D1 + D9).

O tamanho é a decisão inteira, e ela está aberta até medir na Pi. Na bancada,
com a voz real: `small` faz 9,0% de WER (2,2% em comandos) a RTF 0,43; `base`
faz 24,7% a RTF 0,14. D9 manda começar pelo mais rápido que for aceitável — no
PC isso é o `small`, e a troca é uma variável de ambiente, não um refactor.

O modelo é carregado na construção, de propósito: são ~2 s de carga que não
podem cair em cima do primeiro turno.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from common.messages import SAMPLE_RATE

log = logging.getLogger("ideraldinho.stt")


class FasterWhisperSTT:
    def __init__(
        self,
        size: str = "small",
        compute_type: str = "int8",
        model_dir: str | Path | None = None,
        language: str = "pt",
        beam_size: int = 5,
    ) -> None:
        from faster_whisper import WhisperModel

        self.name = f"faster-whisper:{size}/{compute_type}"
        self.language = language
        self.beam_size = beam_size

        started = time.perf_counter()
        options = dict(
            device="cpu",
            compute_type=compute_type,
            download_root=str(model_dir) if model_dir else None,
        )
        try:
            # Offline primeiro, sempre. Sem isso a carga consulta o Hugging Face
            # mesmo com o modelo em disco -- e um aparelho que não abre o
            # microfone com a internet caída contradiz a razão de D1 existir.
            self._model = WhisperModel(size, local_files_only=True, **options)
        except Exception:
            log.warning("%s nao esta em disco; baixando", self.name)
            self._model = WhisperModel(size, **options)
        log.info("%s carregado em %.1fs", self.name, time.perf_counter() - started)

    def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        # O modelo quer float32 em [-1, 1]; passar o array evita escrever um WAV
        # temporário só para lê-lo de volta.
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            samples,
            language=self.language,
            beam_size=self.beam_size,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        log.info("[%.1fs de audio] %r", len(samples) / SAMPLE_RATE, text)
        return text
