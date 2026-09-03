"""Ativacao: como o aparelho sai do IDLE.

Wake word na tomada, botao na bateria (plano, secao 5). Hoje so o primeiro
existe; o botao GPIO espera hardware.
"""

from device.activation.wake import CHUNK_SAMPLES, WakeWord

__all__ = ["WakeWord", "CHUNK_SAMPLES"]
