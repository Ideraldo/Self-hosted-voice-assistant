"""Device process entrypoint. Run alongside the gateway: python -m device.main

O turno inteiro do lado do usuário mora aqui: o microfone ouve, o VAD decide
onde a frase acabou, o STT transcreve **no dispositivo** (D1) e só então a frase
sobe para o gateway. A resposta volta como texto, uma frase por vez, e o Piper
fala. Nenhum áudio cruza a rede em nenhuma direção.

    python -m device.main              # microfone
    python -m device.main --text       # digitando, sem carregar o STT

O modo texto continua existindo porque ele isola: se a resposta está errada com
o texto digitado, o problema não é o microfone.

Desde a Fase 2 o turno tem dois caminhos. O roteador de intenções olha a frase
primeiro: se ela é nível 0 -- timer, alarme, hora, cancelar -- o próprio
dispositivo resolve, sem rede e sem LLM. Só o que ele não reconhece sobe para o
gateway. É por isso que o aparelho liga e funciona com o gateway desligado.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from common.messages import (  # noqa: E402
    Error,
    State,
    StateMessage,
    ToolCall,
    ToolResult,
    Transcript,
)
from device.audio.capture import Microphone  # noqa: E402
from device.audio.playback import Speaker  # noqa: E402
from device.config import config, voice_path  # noqa: E402
from device.local import LocalServices, Scheduler, ScheduleStore  # noqa: E402
from device.rosto import start_face  # noqa: E402
from device.router import match as match_intent  # noqa: E402
from device.router.intents import Intent  # noqa: E402
from device.state import StateMachine  # noqa: E402
from device.tts import PiperVoiceEngine  # noqa: E402
from device.ws_client import ConnectionLost, GatewayClient  # noqa: E402

log = logging.getLogger("ideraldinho.device")


async def read_line(prompt: str) -> str:
    """Read stdin without blocking the event loop (and thus the socket)."""
    return await asyncio.to_thread(input, prompt)


async def speak(voice, speaker, text: str) -> float:
    """Sintetiza e toca, fora do laço de eventos.

    Piper e a placa de som bloqueiam a thread. Rodar isso direto no laço travaria
    o WebSocket enquanto o aparelho fala — e é justamente enquanto ele fala que
    precisa continuar ouvindo, porque o barge-in depende disso (plan section 1).
    """
    started = time.perf_counter()
    await asyncio.to_thread(speaker.play, voice.synthesize(text))
    return time.perf_counter() - started


async def listen_and_transcribe(microphone: Microphone, stt, machine: StateMachine) -> str:
    """Uma fala: grava até o silêncio, transcreve, devolve o texto.

    As duas etapas bloqueiam e vão para threads pela mesma razão da síntese: o
    laço de eventos tem que continuar atendendo o socket.
    """
    machine.transition(State.LISTENING)
    print("  [fale]", end="", flush=True)
    pcm = await asyncio.to_thread(
        microphone.listen, lambda: print("\r  [ouvindo...]", end="", flush=True)
    )
    if not pcm:
        print("\r  [silencio]      ")
        machine.transition(State.IDLE)
        return ""

    started = time.perf_counter()
    text = await asyncio.to_thread(stt.transcribe, pcm)
    print(f"\r  [transcrito em {time.perf_counter() - started:.2f}s]")
    if not text:
        machine.transition(State.IDLE)
    return text


async def esperar_ativacao(microphone: Microphone, wake) -> None:
    """Fica ouvindo até alguém dizer o nome. Bloqueia o turno, e é o ponto.

    Roda numa thread pela mesma razão que a captura e a síntese: o laço de
    eventos tem que continuar atendendo o socket enquanto o aparelho espera --
    inclusive porque um alarme pode disparar no meio da espera, e ele não pede
    licença ao wake word.
    """

    def _ouvir() -> None:
        while not wake.feed(microphone.read_frame()):
            pass

    print("  [esperando o nome...]", end="", flush=True)
    await asyncio.to_thread(_ouvir)
    print("\r                        \r", end="", flush=True)


async def handle_incoming(
    client: GatewayClient, machine: StateMachine, voice, speaker, falando, services
) -> None:
    """Aplica o que o gateway manda: estado, texto para falar, chamadas de ferramenta."""
    spoke_at = None

    async for message in client.receive():
        if isinstance(message, bytes):
            # Depois da decisão D1 o gateway não manda mais áudio. Se chegar,
            # é versão antiga do outro lado -- melhor dizer do que ignorar.
            print("  [aviso: recebi audio do gateway; a sintese agora e local]")
            continue

        if isinstance(message, StateMessage):
            if message.value != machine.state:
                machine.transition(message.value)
            if message.value is State.IDLE:
                return  # turno terminou; volta para o prompt

        elif isinstance(message, Transcript):
            if message.role == "user":
                print(f"  voce> {message.text}")
                continue

            # Resposta do assistente. As parciais são as frases conforme saem do
            # LLM -- falar cada uma na hora é o que evita esperar a resposta
            # inteira. A final é a mesma coisa junta, só para a tela.
            if message.final:
                continue
            print(f"  ideraldinho> {message.text}")
            async with falando:
                elapsed = await speak(voice, speaker, message.text)
            if spoke_at is None:
                spoke_at = elapsed
                print(f"        [primeira fala em {elapsed:.2f}s]")

        elif isinstance(message, ToolCall):
            # A execução é sempre local, venha a intenção de onde vier (plano,
            # seção 5, regra 3). O LLM entendeu a frase que o regex não pegou --
            # mas quem grava e dispara continua sendo este processo.
            print(f"  [ferramenta {message.name} {message.args}]")
            await client.send(executar_ferramenta(services, message))

        elif isinstance(message, Error):
            print(f"  [erro do gateway: {message.message}]")
            return


async def run(text_mode: bool, abrir_rosto: bool = False) -> None:
    machine = StateMachine()

    # O rosto sobe antes de tudo e é o primeiro a assinar a máquina: assim ele
    # já mostra o carregamento do STT em vez de aparecer com o aparelho pronto.
    # Se não subir, o aparelho continua -- a tela é vitrine, o turno é função.
    face = await start_face(config.face_port, config.face_theme) if config.face_enabled else None
    if face is not None:
        machine.subscribe(face.publish)
        print(f"rosto: {face.url}")
        if abrir_rosto:
            import webbrowser

            webbrowser.open(face.url)

    voice = PiperVoiceEngine(voice_path())
    print(f"voz: {voice.name} ({voice.sample_rate} Hz)")

    # Timers e alarmes vivem em disco e sobem antes de tudo: eles não dependem
    # nem do STT nem do gateway, e são o que tem que funcionar sempre.
    store = ScheduleStore(config.schedules_db)
    services = LocalServices(store)  # o aviso ao agendador é ligado abaixo

    stt = None
    if not text_mode:
        from device.stt import FasterWhisperSTT

        # Carregar aqui, antes do primeiro turno: são segundos de carga que não
        # podem cair em cima da primeira pergunta.
        print("carregando o STT...", end="", flush=True)
        stt = FasterWhisperSTT(
            size=config.stt_model,
            compute_type=config.stt_compute_type,
            model_dir=config.stt_model_dir,
        )
        print(f"\rstt: {stt.name}      ")

    # A palavra de ativação é opcional em dois níveis: desligada por
    # configuração, ou ligada e o modelo não carrega. Nos dois casos o aparelho
    # sobe e ouve direto, como sempre fez -- ele só deixa de esperar o nome.
    wake = None
    if not text_mode and config.wake_enabled:
        from device.activation import WakeWord

        candidato = WakeWord(config.wake_model, threshold=config.wake_threshold)
        if candidato.carregar():
            wake = candidato
            print(f"wake word: {config.wake_model} (threshold {config.wake_threshold})")
        else:
            print("wake word: nao carregou -- ouvindo direto")

    with Speaker(voice.sample_rate, config.output_device) as speaker:
        # Um alarme não espera o turno acabar, mas também não fala por cima da
        # resposta: o cadeado serializa a placa de som entre o agendador e o
        # laço do usuário.
        falando = asyncio.Lock()

        async def anunciar(item) -> None:
            async with falando:
                texto = services.anunciar(item)
                print(f"\n  ideraldinho> {texto}   [{item.kind}]")
                await speak(voice, speaker, texto)

        scheduler = Scheduler(store, anunciar)
        # Fecha o ciclo: criar ou cancelar acorda o agendador na hora, em vez de
        # esperar ele reavaliar por conta própria.
        services.on_change = scheduler.notify
        scheduler.start()
        if store.pending():
            print(f"agendado: {services.listar({})}")

        try:
            async with GatewayClient() as client:
                if not client.online:
                    print("gateway: fora do ar -- timer, alarme e hora continuam")
                if text_mode:
                    print("Ideraldinho -- modo texto. Ctrl+C para sair.\n")
                    await text_loop(client, machine, voice, speaker, services, falando)
                else:
                    print("Ideraldinho -- fale quando quiser. Ctrl+C para sair.\n")
                    with Microphone(
                        device=config.input_device,
                        silence_ms=config.vad_silence_ms,
                        aggressiveness=config.vad_aggressiveness,
                    ) as microphone:
                        await voice_loop(
                            client, machine, microphone, stt, voice, speaker,
                            services, falando, wake,
                        )
        finally:
            await scheduler.stop()
            store.close()
            if face is not None:
                await face.stop()


#: Nome da ferramenta (como o LLM a conhece) -> intenção que `device/local/` já
#: executa. É a mesma tabela do gateway, do lado de cá: os dois precisam
#: concordar, e `tests/test_tools.py` é o que garante que concordam.
FERRAMENTAS = {
    "criar_timer": "criar_timer",
    "criar_alarme": "criar_alarme",
    "listar_agendamentos": "listar",
    "cancelar_agendamento": "cancelar",
}


def executar_ferramenta(services, call: ToolCall) -> ToolResult:
    """Executa uma chamada do LLM no mesmo código que o roteador usa.

    A frase entra por um caminho diferente -- regex aqui, modelo lá -- e termina
    no mesmo `LocalServices`, com os mesmos slots. Se estes dois caminhos
    divergirem, o aparelho passa a ter dois comportamentos para a mesma frase,
    dependendo de a internet estar de pé.
    """
    intent_name = FERRAMENTAS.get(call.name)
    if intent_name is None:
        # Modelo inventando ferramenta que não existe é o modo de falha que o
        # critério de aceite da Fase 3 nomeia. Dizer que não existe é melhor que
        # tentar adivinhar o que ele queria.
        return ToolResult(id=call.id, ok=False, error=f"ferramenta desconhecida: {call.name}")

    try:
        resposta = services.handle(Intent(name=intent_name, slots=dict(call.args)))
    except (KeyError, TypeError, ValueError) as exc:
        # Argumento faltando ou com tipo errado: o modelo erra isso, e o
        # resultado tem que voltar como falha para ele poder corrigir.
        return ToolResult(id=call.id, ok=False, error=f"argumentos invalidos: {exc}")

    if resposta is None:
        return ToolResult(id=call.id, ok=False, error="intencao nao reconhecida")
    return ToolResult(id=call.id, ok=True, value=resposta)


def _ate_falar(machine: StateMachine) -> None:
    """Leva a máquina até SPEAKING pelo caminho que ela permite.

    Sem isto, o atalho óbvio (`IDLE -> THINKING`) levanta `ValueError`: as
    transições legais são as do plano, e IDLE só vai para LISTENING. A máquina
    está certa e o atalho é que estava errado -- foi assim que este caminho
    quebrou na primeira execução com o gateway desligado.
    """
    if machine.state is State.IDLE:
        machine.transition(State.LISTENING)
    if machine.state is State.LISTENING:
        machine.transition(State.THINKING)
    if machine.state is State.THINKING:
        machine.transition(State.SPEAKING)


async def answer(client, machine, voice, speaker, services, falando, text: str) -> None:
    """Conduz o turno: nível 0 aqui, o resto no gateway.

    A ordem importa. Tentar o roteador antes da rede é o que dá ao timer a
    latência que o plano orça (< 200 ms) e o que faz o alarme continuar
    existindo quando o Wi-Fi não existe.

    Se a conexão cair no meio, o cliente já reconectou sozinho -- mas a resposta
    daquele turno se perdeu com a `Session` do gateway. Dizer isso em voz alta e
    voltar a ouvir é melhor que morrer com traceback numa prateleira.
    """
    intent = match_intent(text)
    if intent is not None:
        resposta = services.handle(intent)
        if resposta is not None:
            print(f"  ideraldinho> {resposta}   [nivel 0, local]")
            _ate_falar(machine)
            async with falando:
                await speak(voice, speaker, resposta)
            machine.transition(State.IDLE)
            return

    # Nível 2: o que o roteador não reconheceu. Registrar a frase é o que, em um
    # mês, diz quais intenções merecem virar nível 0 (plano, seção 5, regra 4).
    log.info("nivel 2 (subiu para o LLM): %r", text)
    try:
        await client.send_utterance(text)
        await handle_incoming(client, machine, voice, speaker, falando, services)
    except ConnectionLost as exc:
        log.warning("turno perdido: %s", exc)
        # Com o gateway fora, o nível 0 continua de pé -- e dizer isso é melhor
        # que um silêncio que parece defeito do microfone.
        _ate_falar(machine)
        async with falando:
            await speak(
                voice, speaker,
                "Nao consigo falar com o servidor agora. Timer e alarme continuam funcionando.",
            )
        machine.transition(State.IDLE)


async def text_loop(client, machine, voice, speaker, services, falando) -> None:
    while True:
        text = (await read_line("voce: ")).strip()
        if not text:
            continue
        # O aparelho esteve ouvindo o tempo todo -- aqui foi o teclado, mas o
        # estado é o mesmo, e é o que mantém a máquina válida nos dois modos.
        machine.transition(State.LISTENING)
        await answer(client, machine, voice, speaker, services, falando, text)


async def voice_loop(
    client, machine, microphone, stt, voice, speaker, services, falando, wake=None
) -> None:
    while True:
        if wake is not None:
            # O reset é antes da espera, e não depois do turno: o modelo guarda
            # estado entre blocos, e o que sobrou ali é a resposta que o próprio
            # aparelho acabou de falar.
            wake.reset()
            await esperar_ativacao(microphone, wake)
        text = await listen_and_transcribe(microphone, stt, machine)
        if not text:
            continue
        await answer(client, machine, voice, speaker, services, falando, text)


def main() -> None:
    parser = argparse.ArgumentParser(description="dispositivo do Ideraldinho")
    parser.add_argument(
        "--text",
        action="store_true",
        help="digitar em vez de falar; nao carrega o STT",
    )
    parser.add_argument("--verbose", action="store_true", help="log do STT e da captura")
    parser.add_argument(
        "--rosto",
        action="store_true",
        help="abre o rosto no navegador ao subir (o servidor sobe de qualquer jeito)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(name)s %(message)s",
    )
    try:
        asyncio.run(run(args.text, args.rosto))
    except (KeyboardInterrupt, EOFError):
        print("\ntchau.")
        sys.exit(0)


if __name__ == "__main__":
    main()
