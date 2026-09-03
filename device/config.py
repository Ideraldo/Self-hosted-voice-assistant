"""Device configuration -- audio devices and display mode come from the env,
so the same code runs on the PC (jack + windowed browser) and on the Pi
(USB speakerphone + Chromium kiosk)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _audio_device(name: str) -> str | int | None:
    """Nome ou índice do dispositivo, como o sounddevice espera.

    A variável de ambiente é sempre texto, mas o sounddevice trata texto como
    trecho de nome e número como índice. Sem essa conversão, `AUDIO_INPUT_DEVICE=2`
    viraria uma busca pelo nome "2" e não acharia nada.
    """
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value) if value.lstrip("-").isdigit() else value


@dataclass(frozen=True)
class DeviceConfig:
    device_id: str = os.getenv("DEVICE_ID", "ideraldinho-01")
    token: str = os.getenv("DEVICE_TOKEN", "dev-token")
    gateway_url: str = os.getenv("GATEWAY_URL", "ws://localhost:8000/ws")
    input_device: str | int | None = _audio_device("AUDIO_INPUT_DEVICE")
    output_device: str | int | None = _audio_device("AUDIO_OUTPUT_DEVICE")
    display_mode: str = os.getenv("DISPLAY_MODE", "window")  # window | kiosk
    # O rosto (Fase 4). Porta separada da do gateway porque os dois processos
    # podem estar na mesma maquina, e porque na Pi o gateway nem existe.
    # Escutar so em 127.0.0.1 e decisao do servidor: a tela e local, e uma porta
    # aberta na rede de casa mostrando o estado do microfone nao paga nada.
    face_port: int = int(os.getenv("FACE_PORT", "8080"))
    face_enabled: bool = os.getenv("FACE_ENABLED", "1").strip().lower() not in {"0", "false", ""}
    # Qual rosto: "minimo" (dois retangulos) ou "anime". E so a camada de
    # desenho -- os dois falam o mesmo {state, emotion} (D27).
    face_theme: str = os.getenv("FACE_THEME", "minimo")
    # A voz do assistente. Fica fora do pacote de proposito: no PC ela vem da
    # bancada, na Pi virá de um diretório próprio, e nenhum dos dois deveria
    # estar escrito no código.
    tts_voice: str = os.getenv("TTS_VOICE", "pt_BR-ideraldo-medium")
    tts_voice_dir: str = os.getenv("TTS_VOICE_DIR", "lab/models/piper")
    # STT local (D1). O tamanho segue aberto ate medir na Pi (D9): `small`
    # acerta e `base` cabe, e trocar precisa custar uma variavel de ambiente.
    stt_model: str = os.getenv("STT_MODEL", "small")
    stt_compute_type: str = os.getenv("STT_COMPUTE_TYPE", "int8")
    stt_model_dir: str = os.getenv("STT_MODEL_DIR", "lab/models/faster-whisper")
    # Quanto silencio encerra a fala. A secao 11 orca 200-300 ms; na pratica
    # cortar cedo demais decepa o fim da frase.
    vad_silence_ms: int = int(os.getenv("VAD_SILENCE_MS", "700"))
    vad_aggressiveness: int = int(os.getenv("VAD_AGGRESSIVENESS", "3"))
    simulated_latency_ms: int = int(os.getenv("SIMULATED_LATENCY_MS", "0"))
    # Onde moram timers e alarmes. Em disco porque um alarme tem que sobreviver
    # ao processo -- e a Pi reinicia (plano, secao 5, nivel 0).
    schedules_db: str = os.getenv("SCHEDULES_DB", "device/data/schedules.db")


config = DeviceConfig()


def voice_path() -> Path:
    """Onde está o .onnx da voz, montado a partir da configuração."""
    return Path(config.tts_voice_dir) / f"{config.tts_voice}.onnx"
