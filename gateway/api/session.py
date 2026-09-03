"""One WebSocket connection = one device session.

The turn loop: wait for an ``utterance`` -- a frase que o dispositivo já
transcreveu (D1) --, ask the LLM, speak the answer back as it is generated.
State messages drive the face on the device, so they are sent as the state
actually changes, never in a batch at the end.

O gateway não vê áudio em nenhuma direção. Ele recebe texto e devolve texto;
microfone, STT e síntese são assunto do dispositivo.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from fastapi import WebSocket, WebSocketDisconnect

from common.messages import (
    Error,
    SessionStart,
    State,
    StateMessage,
    ToolCall,
    ToolResult,
    Transcript,
    Utterance,
)
from common.serialization import decode, encode
from gateway.conversation.history import Conversation
from gateway.llm.base import LLMProvider
from gateway.timing import Turn
from gateway.tools import (
    DEVICE_TOOLS,
    SEARCH_TOOL_NAMES,
    SEARCH_TOOLS,
    SPOTIFY_TOOL_NAMES,
    SPOTIFY_TOOLS,
    TERMINAL_TOOLS,
    executar_busca,
    executar_spotify,
)

log = logging.getLogger("marcos.session")

#: Sentence enders that are safe places to start speaking (plan section 11:
#: streaming TTS is one of the two biggest latency wins).
BREAKS = ".!?\n"

#: Quantas vezes o modelo pode pedir ferramenta dentro de um turno. O limite não
#: é paranoia: um modelo pequeno que erra os argumentos tende a repetir a mesma
#: chamada para sempre, e sem teto isso vira um turno que nunca termina -- com o
#: aparelho parado em THINKING, esperando uma resposta que não vem.
MAX_TOOL_ROUNDS = 3

#: Quanto esperar o dispositivo executar. É ação local (SQLite e um agendador),
#: então é rápida; o limite existe para o caso de o dispositivo sumir no meio.
TOOL_TIMEOUT = 10.0


def _fim_de_frase(texto: str) -> bool:
    """O texto acumulado termina numa frase que já dá para falar?

    O ponto entre dígitos **não** encerra frase: é separador de milhar ou
    decimal. Sem esta checagem, "384.400 quilômetros" saía em duas falas --
    "aproximadamente 384." e "400 quilômetros." --, o que só apareceu quando a
    busca na internet começou a trazer números formatados de verdade.

    Segurar o ponto de "1899." até a frase seguinte custa uma fala um pouco mais
    tarde; partir um número no meio custa uma resposta que soa quebrada. O que
    sobrar sem quebrar é falado no fim do fluxo, de qualquer forma.
    """
    if not texto or texto[-1] not in BREAKS:
        return False
    if texto[-1] == "." and len(texto) >= 2 and texto[-2].isdigit():
        return False
    return True


class Session:
    def __init__(
        self,
        websocket: WebSocket,
        llm: LLMProvider,
        expected_token: str,
        spotify: object | None = None,
        search: object | None = None,
        leitor: object | None = None,
    ) -> None:
        self._ws = websocket
        self._llm = llm
        self._expected_token = expected_token
        self._conversation = Conversation()
        # Sem Spotify configurado, as ferramentas de música não são declaradas.
        # O modelo não vê o que não pode usar -- e um modelo pequeno que vê uma
        # ferramenta indisponível tenta usar mesmo assim (D19).
        self._spotify = spotify
        self._search = search
        # O leitor só faz sentido com busca: é ela que produz a URL. Sem
        # provedor ele fica pendurado sem nunca ser chamado, e tudo bem.
        self._leitor = leitor
        self._tools = (
            DEVICE_TOOLS
            + (SPOTIFY_TOOLS if spotify is not None else [])
            + (SEARCH_TOOLS if search is not None else [])
        )

    async def run(self) -> None:
        await self._ws.accept()
        if not await self._authenticate():
            return

        try:
            while True:
                await self._turn()
        except WebSocketDisconnect:
            log.info("device disconnected")

    async def _authenticate(self) -> bool:
        raw = await self._ws.receive_text()
        try:
            message = decode(raw)
        except ValueError as exc:
            await self._send(Error(message=str(exc)))
            await self._ws.close(code=1008)
            return False

        if not isinstance(message, SessionStart) or message.token != self._expected_token:
            await self._send(Error(message="invalid session_start or token"))
            await self._ws.close(code=1008)
            return False

        log.info("session started: %s", message.device_id)
        await self._send(StateMessage(value=State.IDLE))
        return True

    async def _turn(self) -> None:
        """Wait for one utterance, answer it, speak the answer."""
        text = await self._receive_utterance()
        if not text:
            return

        turn = Turn()
        await self._send(StateMessage(value=State.THINKING))
        # Eco do que foi entendido: o rosto mostra a transcrição, e quem lê o log
        # do gateway vê a frase que gerou a resposta.
        await self._send(Transcript(text=text, role="user"))

        self._conversation.add_user(text)
        answer = await self._speak_response(turn)
        if answer:
            self._conversation.add_assistant(answer)

        await self._send(StateMessage(value=State.IDLE))
        turn.report()

    async def _receive_utterance(self) -> str:
        """Espera a próxima frase transcrita pelo dispositivo."""
        while True:
            packet = await self._ws.receive()
            if packet.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(packet.get("code", 1000))

            if packet.get("bytes") is not None:
                # Depois de D1 o dispositivo transcreve antes de falar com o
                # gateway. Áudio subindo é versão antiga do outro lado.
                await self._send(Error(message="audio frames are not accepted; send an utterance"))
                continue

            raw = packet.get("text")
            if raw is None:
                continue
            try:
                message = decode(raw)
            except ValueError as exc:
                await self._send(Error(message=str(exc)))
                continue
            if isinstance(message, Utterance):
                return message.text.strip()
            log.debug("ignoring %s while waiting for an utterance", message.type)

    async def _speak_response(self, turn: Turn) -> str:
        """Stream the answer to the device, one sentence at a time.

        Sentences go out as they complete, not at the end. The device starts
        speaking the first while the LLM is still writing the third, which is one
        of the two biggest latency wins the plan names (section 11).

        Only text crosses the network. After decision D1 the synthesis happens on
        the device, so a turn costs a few KB instead of the ~250 KB of PCM the
        original design assumed -- and the device can still speak with the
        connection down.
        """
        spoken = False
        first_sentence = True
        pending = ""
        full = ""
        first_token = True

        async def flush(sentence: str) -> None:
            nonlocal spoken, first_sentence
            if not sentence.strip():
                return
            if not spoken:
                await self._send(StateMessage(value=State.SPEAKING))
                spoken = True
            if first_sentence:
                turn.mark("primeira_frase")
                first_sentence = False
            # final=False: mais frases vêm em seguida nesta mesma resposta.
            await self._send(Transcript(text=sentence.strip(), role="assistant", final=False))

        # Uma rodada por chamada de ferramenta: o modelo pede, o dispositivo
        # executa, o resultado volta para o histórico e o modelo continua. Sem
        # este laço a chamada saía pelo fio e ninguém esperava a resposta -- que
        # era o estado do código até aqui.
        for rodada in range(MAX_TOOL_ROUNDS + 1):
            chamada: tuple[str, dict] | None = None

            async for delta in self._llm.respond(
                self._conversation.prompt(), tools=self._tools
            ):
                if delta.tool_name:
                    # A execução é sempre do dispositivo (plano, seção 5, regra 3).
                    chamada = (delta.tool_name, delta.tool_args or {})
                    break
                if not delta.text:
                    continue

                if first_token:
                    turn.mark("llm_first_token")
                    first_token = False

                pending += delta.text
                full += delta.text
                if _fim_de_frase(pending):
                    await flush(pending)
                    pending = ""

            if chamada is None:
                break

            nome, args = chamada
            if rodada == MAX_TOOL_ROUNDS:
                log.warning("modelo insistiu em ferramenta apos %d rodadas", rodada)
                self._conversation.add_tool_result(
                    nome, "falhou: numero maximo de tentativas atingido"
                )
                break

            if nome in SEARCH_TOOL_NAMES:
                resultado = await executar_busca(self._search, nome, args, self._leitor)
            elif nome in SPOTIFY_TOOL_NAMES:
                # O gateway executa: o segredo mora aqui.
                resultado = await executar_spotify(self._spotify, nome, args)
            else:
                resultado = await self._run_device_tool(nome, args)
            turn.mark(f"ferramenta:{nome}")

            # O par pedido/resultado vai para o histórico **sempre**, inclusive
            # nas ferramentas terminais. Sem ele o modelo para de chamar
            # ferramenta no turno seguinte e passa a narrar ações que não fez
            # (D22).
            self._conversation.add_tool_call(nome, args)
            self._conversation.add_tool_result(nome, resultado)

            if nome in TERMINAL_TOOLS or nome in SPOTIFY_TOOL_NAMES:
                # A frase devolvida já está pronta para ser falada. Ela vai como
                # está: o modelo reescrevendo isso perde item da lista, e a
                # rodada extra custa segundos num turno que já é o mais lento.
                await flush(resultado)
                full += resultado
                break

        await flush(pending)

        if full.strip():
            # A resposta inteira, marcada como final: serve para a tela e para o
            # histórico, e diz ao dispositivo que não vem mais nada.
            await self._send(Transcript(text=full.strip(), role="assistant", final=True))
        turn.mark("resposta")
        return full.strip()

    async def _run_device_tool(self, name: str, args: dict) -> str:
        """Pede ao dispositivo que execute, e espera o resultado.

        O gateway não sabe criar um timer e não deve saber: quem tem o
        agendador, o banco e o alto-falante é o outro lado. Aqui só transita.
        """
        call_id = str(uuid.uuid4())
        log.info("ferramenta %s %s -> dispositivo", name, args)
        await self._send(ToolCall(id=call_id, name=name, args=args))
        try:
            result = await asyncio.wait_for(
                self._receive_tool_result(call_id), timeout=TOOL_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.warning("dispositivo nao respondeu a ferramenta %s", name)
            return "falhou: o aparelho nao respondeu"

        if not result.ok:
            return f"falhou: {result.error or 'erro desconhecido'}"
        return result.value or "feito"

    async def _receive_tool_result(self, call_id: str) -> ToolResult:
        """Espera o `tool_result` daquela chamada, ignorando o resto.

        Casar pelo `id` importa: sem isso, o resultado de uma chamada antiga --
        uma que estourou o tempo e chegou atrasada -- seria lido como resposta
        da chamada atual.
        """
        while True:
            packet = await self._ws.receive()
            if packet.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(packet.get("code", 1000))
            raw = packet.get("text")
            if raw is None:
                continue
            try:
                message = decode(raw)
            except ValueError as exc:
                await self._send(Error(message=str(exc)))
                continue
            if isinstance(message, ToolResult) and message.id == call_id:
                return message
            log.debug("ignoring %s while waiting for tool_result", getattr(message, "type", "?"))

    async def _send(self, message: object) -> None:
        await self._ws.send_text(encode(message))
