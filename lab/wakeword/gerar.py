"""Gera o material de treino do wake word "Ideraldinho", sem baixar nada.

    python -m lab.wakeword.gerar

A receita oficial do openWakeWord manda sintetizar dezenas de milhares de
positivos com o `piper-sample-generator` (um checkpoint LibriTTS, em inglês) e
misturar com dezenas de GB de ruído e fala negativa baixados do HuggingFace.

Aqui nada disso é preciso, e por um motivo específico: **este repositório já tem
sete vozes pt-BR do Piper baixadas, mais as épocas do fine-tune da voz do dono**
-- e 315 gravações reais dele, do dataset do treino da voz (D4). É material
melhor que o da receita, porque:

1. **A pronúncia é a certa.** O checkpoint em inglês diria "Ideraldinho" com
   fonemas ingleses, e o modelo aprenderia a palavra errada. Conferido: o
   espeak-ng em pt-br devolve `ˌideɾaʊdʒˈiɲʊ`, que é como se fala.
2. **Os negativos são a mesma voz que vai usar o aparelho.** 315 frases do dono
   falando outras coisas é o negativo mais difícil que existe para este caso --
   e é exatamente o negativo que importa, porque é ele quem fala perto do
   microfone o dia inteiro.

O que **falta** em relação à receita oficial, e não dá para esconder: ruído de
sala real. O que há aqui é ruído sintético, e ele não soa como uma cozinha às
sete da noite. É a maior fraqueza deste dataset, e é o que a medição com o
microfone vai cobrar.
"""

from __future__ import annotations

import argparse
import random
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

SR = 16000

#: 2,0 s é o que o extrator do openWakeWord converte em (16, 96) -- exatamente
#: a entrada que o classificador espera. Não é escolha de gosto.
JANELA_S = 2.0
JANELA = int(SR * JANELA_S)

RAIZ = Path("lab/models/piper")
SAIDA = Path("lab/wakeword/dados")

PALAVRA = "Ideraldinho"

#: As vozes-base. As épocas do fine-tune entram à parte, porque entre elas a
#: diferença é de timbre e não de locutor -- vale como variação, não como voz
#: nova, e enfiar as 18 aqui desequilibraria o conjunto para a voz do dono.
VOZES = [
    "pt_BR-cadu-medium",
    "pt_BR-dii-high",
    "pt_BR-edresson-low",
    "pt_BR-faber-medium",
    "pt_BR-jeff-medium",
    "pt_BR-miro-high",
    "pt_BR-ideraldo-medium",
]

#: Três épocas do fine-tune, espaçadas. A 996 é a voz oficial (D5); as outras
#: duas soam como ela mal treinada, o que aqui é uma vantagem de graça.
EPOCAS = [
    "pt_BR-ideraldoep214-medium",
    "pt_BR-ideraldoep640-medium",
    "pt_BR-ideraldoep996-medium",
]

#: O que o aparelho **não** pode atender.
#:
#: A primeira versão desta lista tinha seis nomes parecidos e doze frases
#: comuns, e o modelo treinado com ela **aprendeu "-aldinho", não
#: "Ideraldinho"**: disparava em 83% dos "Everaldinho" e em 0% dos "Ideraldo".
#: Ou seja, ele decidia pelo fim da palavra e ignorava o começo -- o que faz
#: sentido para uma rede pequena, porque o fim é onde a janela termina.
#:
#: Daí o desequilíbrio proposital abaixo: a família `-aldinho` ocupa a maior
#: parte da lista. Não é para o dataset parecer a vida real; é para a fronteira
#: que o modelo precisa aprender ficar densa justamente onde ele errava.
ADVERSARIAS = [
    # a familia que enganou o primeiro modelo
    "Everaldinho",
    "Reginaldinho",
    "Geraldinho",
    "Osvaldinho",
    "Ubaldinho",
    "Aldinho",
    "Naldinho",
    "Edvaldinho",
    "Grinaldinho",
    "Arnaldinho",
    "Ronaldinho",
    "Ideraudinho",
    "Iderauzinho",
    "Iderandinho",
    "Iberaldinho",
    "Inderaldinho",
    # o comeco certo, o fim errado -- o outro lado da mesma fronteira
    "Ideraldo",
    "Ideraldão",
    "Ideraldina",
    "Ideral",
    # fala comum
    "ideal",
    "identidade",
    "e daí então",
    "a ideia dele",
    "vira e mexe",
    "o Ivo viu a uva",
    "amanhã de manhã",
    "liga a televisão",
    "que horas são",
    "põe uma música",
    "cadê você",
    "deixa quieto",
]

#: Variação de prosódia. `length_scale` é velocidade (1.0 = normal, maior = mais
#: devagar) e `noise_scale` é quanto o modelo varia a entonação.
PROSODIA = [
    (0.85, 0.667, 0.8),
    (1.0, 0.667, 0.8),
    (1.15, 0.667, 0.8),
    (1.0, 0.9, 1.0),
    (0.95, 0.5, 0.6),
]


def carregar_voz(nome: str):
    from piper import PiperVoice

    return PiperVoice.load(str(RAIZ / f"{nome}.onnx"))


def sintetizar(voz, texto: str, length: float, noise: float, noise_w: float) -> np.ndarray:
    from piper import SynthesisConfig

    cfg = SynthesisConfig(length_scale=length, noise_scale=noise, noise_w_scale=noise_w)
    bruto = b"".join(c.audio_int16_bytes for c in voz.synthesize(texto, syn_config=cfg))
    audio = np.frombuffer(bruto, dtype=np.int16).astype(np.float32) / 32768.0
    origem = voz.config.sample_rate
    if origem != SR:
        # As vozes vêm em 22050 ou 16000; o extrator só fala 16 kHz.
        audio = resample_poly(audio, SR, origem)
    return audio.astype(np.float32)


def ruido(n: int, cor: str, rng: random.Random) -> np.ndarray:
    """Ruído sintético. É o ponto fraco do dataset, e está declarado.

    Branco é o chiado do microfone; rosa se parece mais com ruído de ambiente;
    o zumbido é a rede elétrica, que é o que uma fonte barata injeta.
    """
    r = np.random.default_rng(rng.randrange(2**31))
    if cor == "branco":
        return r.normal(0, 1, n).astype(np.float32)
    if cor == "rosa":
        # Ruído branco filtrado: a energia cai com a frequência, como o de sala.
        branco = r.normal(0, 1, n)
        espectro = np.fft.rfft(branco)
        f = np.arange(len(espectro))
        f[0] = 1
        return np.fft.irfft(espectro / np.sqrt(f), n).astype(np.float32)
    # zumbido de 60 Hz com harmônica
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * 60 * t) + 0.3 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)


def encaixar(palavra: np.ndarray, rng: random.Random) -> np.ndarray:
    """Põe a palavra numa janela de 2 s, terminando perto do fim.

    **Perto do fim, e não no meio.** Em uso, a janela que o modelo avalia é
    sempre a que acabou de passar: quando alguém termina de dizer o nome, a
    palavra está no fim do buffer. Treinar com ela centralizada ensinaria o
    modelo a esperar meio segundo de silêncio depois do nome, que é meio segundo
    a mais para o aparelho acordar.
    """
    janela = np.zeros(JANELA, dtype=np.float32)
    palavra = palavra[:JANELA]
    # A folga depois da palavra: de 0 a 300 ms.
    folga = rng.randint(0, int(0.3 * SR))
    fim = JANELA - folga
    inicio = max(0, fim - len(palavra))
    janela[inicio:fim] = palavra[-(fim - inicio):]
    return janela


def temperar(janela: np.ndarray, rng: random.Random) -> np.ndarray:
    """Ganho, ruído e um pouco de eco. O que o microfone faz com o som."""
    janela = janela * rng.uniform(0.25, 1.0)

    if rng.random() < 0.85:
        cor = rng.choice(["branco", "rosa", "rosa", "zumbido"])
        n = ruido(JANELA, cor, rng)
        n = n / (np.abs(n).max() + 1e-9) * rng.uniform(0.005, 0.08)
        janela = janela + n

    if rng.random() < 0.35:
        # Um eco curto: a parede da sala. Não é um impulso de sala de verdade,
        # mas tira o som da condição de estúdio, que é onde a síntese nasce.
        atraso = rng.randint(int(0.01 * SR), int(0.06 * SR))
        eco = np.zeros_like(janela)
        eco[atraso:] = janela[:-atraso] * rng.uniform(0.1, 0.35)
        janela = janela + eco

    pico = np.abs(janela).max()
    if pico > 0.99:
        janela = janela / pico * 0.99
    return janela


def salvar(caminho: Path, janela: np.ndarray) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(janela, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def gerar_positivos(rng: random.Random, por_voz: int) -> int:
    destino = SAIDA / "positivo"
    n = 0
    for nome in VOZES + EPOCAS:
        caminho = RAIZ / f"{nome}.onnx"
        if not caminho.exists():
            print(f"  (pulando {nome}: nao existe)")
            continue
        voz = carregar_voz(nome)
        for i in range(por_voz):
            length, noise, noise_w = PROSODIA[i % len(PROSODIA)]
            length *= rng.uniform(0.93, 1.07)
            audio = sintetizar(voz, PALAVRA, length, noise, noise_w)
            salvar(destino / f"{nome}_{i:03d}.wav", temperar(encaixar(audio, rng), rng))
            n += 1
        print(f"  {nome}: {por_voz}")
    return n


def gerar_adversarios(rng: random.Random, por_voz: int) -> int:
    """As palavras parecidas. É o negativo que decide o falso positivo."""
    destino = SAIDA / "negativo"
    n = 0
    for nome in VOZES:
        voz = carregar_voz(nome)
        for i in range(por_voz):
            texto = ADVERSARIAS[i % len(ADVERSARIAS)]
            length, noise, noise_w = PROSODIA[i % len(PROSODIA)]
            audio = sintetizar(voz, texto, length * rng.uniform(0.93, 1.07), noise, noise_w)
            salvar(destino / f"adv_{nome}_{i:03d}.wav", temperar(encaixar(audio, rng), rng))
            n += 1
        print(f"  adversarias em {nome}: {por_voz}")
    return n


def gerar_fala_real(rng: random.Random, por_arquivo: int) -> int:
    """Os negativos de verdade: o dono da voz falando outra coisa.

    São as 315 gravações do dataset do fine-tune (D4). Voz real, microfone real,
    sala real -- a única parte deste conjunto que não é sintética.
    """
    origem = Path("lab/finetune/dataset/wav")
    if not origem.exists():
        print("  (dataset do finetune nao encontrado -- sem negativo de voz real)")
        return 0

    destino = SAIDA / "negativo"
    n = 0
    for wav in sorted(origem.glob("*.wav")):
        with wave.open(str(wav), "rb") as w:
            taxa = w.getframerate()
            audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        audio = audio.astype(np.float32) / 32768.0
        if taxa != SR:
            audio = resample_poly(audio, SR, taxa).astype(np.float32)
        if len(audio) < JANELA:
            continue
        for k in range(por_arquivo):
            i = rng.randint(0, len(audio) - JANELA)
            salvar(destino / f"real_{wav.stem}_{k}.wav", temperar(audio[i : i + JANELA].copy(), rng))
            n += 1
    print(f"  fala real do dono: {n}")
    return n


def gerar_silencio(rng: random.Random, quantos: int) -> int:
    """Só ruído. O aparelho passa quase todo o tempo ouvindo exatamente isto."""
    destino = SAIDA / "negativo"
    for i in range(quantos):
        salvar(destino / f"ruido_{i:04d}.wav", temperar(np.zeros(JANELA, dtype=np.float32), rng))
    print(f"  ruido puro: {quantos}")
    return quantos


def main() -> None:
    p = argparse.ArgumentParser(description="gera o dataset do wake word")
    p.add_argument("--positivos-por-voz", type=int, default=120)
    p.add_argument("--adversarios-por-voz", type=int, default=90)
    p.add_argument("--trechos-por-gravacao", type=int, default=3)
    p.add_argument("--ruidos", type=int, default=400)
    p.add_argument("--semente", type=int, default=7)
    args = p.parse_args()

    rng = random.Random(args.semente)
    print(f"gerando em {SAIDA}/ ...")
    pos = gerar_positivos(rng, args.positivos_por_voz)
    neg = gerar_adversarios(rng, args.adversarios_por_voz)
    neg += gerar_fala_real(rng, args.trechos_por_gravacao)
    neg += gerar_silencio(rng, args.ruidos)
    print(f"\npositivos: {pos}   negativos: {neg}   razao 1:{neg / max(pos, 1):.1f}")


if __name__ == "__main__":
    main()
