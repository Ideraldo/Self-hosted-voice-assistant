"""Sending audio out of the device.

Deliberately thin: a stream you open once and push chunks into. Opening the
device per utterance costs hundreds of milliseconds on some drivers, which is a
large slice of a budget measured in hundreds of milliseconds total.

The speaker is chosen by configuration, never hardcoded — the PC has a headset
and the Pi will have a USB speakerphone (plan section 2, rule 5).
"""

from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger("ideraldinho.playback")


class Speaker:
    def __init__(self, sample_rate: int, device: str | int | None = None) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stream = None

    def __enter__(self) -> "Speaker":
        import sounddevice as sd

        self._stream = sd.RawOutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            device=self.device,
        )
        self._stream.start()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def play(self, chunks: Iterable[bytes]) -> int:
        """Push chunks as they arrive. Returns how many bytes were spoken."""
        written = 0
        for chunk in chunks:
            self._stream.write(chunk)
            written += len(chunk)
        return written
