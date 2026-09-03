"""Ouvir o usuário: microfone + VAD, até a frase terminar.

Simétrico ao `Speaker`: o stream abre uma vez e fica aberto. Abrir o
dispositivo por frase custa centenas de milissegundos em alguns drivers, e o
orçamento do turno inteiro é medido em centenas de milissegundos (seção 11).

A gravação para sozinha quando você para de falar. Duração fixa erra nos dois
sentidos — corta a frase longa e enche a curta de ruído de sala, e é justamente
esse ruído que faz o Whisper alucinar palavra que ninguém disse.

O webrtcvad sozinho não basta: num microfone sensível ele chama o ruído de sala
de fala e a gravação nunca começa. Por isso os primeiros 300 ms medem o piso de
ruído, e um frame só conta como voz quando o VAD concorda *e* ele está
sensivelmente acima desse piso. É a mesma lógica já validada na bancada
(`lab/audio.py`), trazida para produção.
"""

from __future__ import annotations

import logging

import numpy as np

from common.messages import CHANNELS, SAMPLE_RATE

log = logging.getLogger("ideraldinho.capture")

#: 10, 20 ou 30 ms é tudo que o webrtcvad aceita. 30 ms é o mais barato.
FRAME_MS = 30


def _rms(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0


class Microphone:
    """Fonte de fala do dispositivo. Use como context manager."""

    def __init__(
        self,
        device: str | int | None = None,
        rate: int = SAMPLE_RATE,
        aggressiveness: int = 3,
        silence_ms: int = 700,
        max_seconds: float = 20.0,
        start_timeout: float = 30.0,
        preroll_ms: int = 300,
    ) -> None:
        if rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"webrtcvad nao aceita {rate} Hz")
        self.rate = rate
        self.device = device
        self.silence_ms = silence_ms
        self.max_seconds = max_seconds
        self.start_timeout = start_timeout
        self.preroll_ms = preroll_ms
        self._aggressiveness = aggressiveness
        self._frame_samples = int(rate * FRAME_MS / 1000)
        self._stream = None
        self._vad = None

    def __enter__(self) -> "Microphone":
        import sounddevice as sd
        import webrtcvad

        self._vad = webrtcvad.Vad(self._aggressiveness)
        self._stream = sd.RawInputStream(
            samplerate=self.rate,
            blocksize=self._frame_samples,
            device=self.device,
            dtype="int16",
            channels=CHANNELS,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _read(self) -> bytes:
        frame, overflowed = self._stream.read(self._frame_samples)
        if overflowed:
            log.debug("overflow na captura")
        return bytes(frame)

    def _flush(self) -> None:
        """Descarta o que entrou enquanto o Ideraldinho falava.

        Sem isso, a própria resposta dele volta pelo microfone e vira a próxima
        pergunta. Barge-in de verdade vai querer esse áudio; hoje ele só atrapalha.
        """
        while self._stream.read_available >= self._frame_samples:
            self._read()

    def listen(self, on_start=None) -> bytes:
        """Grava uma fala e devolve o PCM.

        Devolve vazio se ninguém falar dentro de ``start_timeout``. Esperar para
        sempre seria mais simples, mas a captura roda numa thread e um Ctrl+C
        preso nela não encerra o processo -- o tempo limite é o que devolve o
        controle ao laço.
        """
        self._flush()

        needed_silence = self.silence_ms // FRAME_MS
        needed_speech = 3  # frames de voz seguidos antes de comprometer
        # Guarda o áudio logo antes do gatilho: a primeira consoante cai antes do
        # primeiro frame sonoro, e cortá-la custa uma palavra inteira.
        preroll: list[bytes] = [b""] * (self.preroll_ms // FRAME_MS)

        floor = 0.0
        calibration = 10
        for _ in range(calibration):
            floor += _rms(self._read()) / calibration
        # Quatro vezes o piso de ruído, mas nunca tão baixo que o zumbido passe
        # por voz, nem tão alto que ignore quem fala baixo.
        threshold = min(max(floor * 4, 250.0), 1500.0)

        collected: list[bytes] = []
        voiced = silent = 0
        started = False
        elapsed = 0.0

        while True:
            frame = self._read()
            elapsed += FRAME_MS / 1000

            if self._vad.is_speech(frame, self.rate) and _rms(frame) > threshold:
                voiced += 1
                silent = 0
            else:
                silent += 1
                voiced = 0

            if not started:
                preroll.append(frame)
                preroll.pop(0)
                if voiced >= needed_speech:
                    started = True
                    collected.extend(f for f in preroll if f)
                    if on_start is not None:
                        on_start()
                elif elapsed > self.start_timeout:
                    return b""
                continue

            collected.append(frame)
            if silent >= needed_silence or elapsed > self.max_seconds:
                break

        return b"".join(collected)
