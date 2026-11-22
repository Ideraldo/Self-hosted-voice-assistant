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
import time
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

log = logging.getLogger("ideraldinho.session")

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

#: O que o aparelho diz quando o modelo não responde. É outra frase, de
#: propósito, que a do dispositivo para o gateway fora do ar ("nao consigo falar
#: com o servidor"): aqui o servidor está de pé e a conversa continua, quem
#: sumiu foi o provedor do LLM. Sem acento, como toda frase que vai para o Piper.
LLM_FORA_DO_AR = "Nao consegui falar com o modelo agora. Tenta de novo daqui a pouco."


#: O modelo respondeu, e respondeu nada: texto vazio, ou só pedidos de
#: ferramenta até estourar as rodadas. Não é a frase de cima de propósito -- o
#: provedor está de pé, e mandar a pessoa esperar "um pouco" seria mentira; o
#: que resolve é perguntar de novo, talvez de outro jeito.
SEM_RESPOSTA = "Nao consegui responder a isso. Tenta de novo."

#: Uma ferramenta levantou exceção que ninguém previa: defeito nosso, ou uma
#: API que mudou de forma. A terceira frase, pela mesma razão das outras duas:
#: quem ouve precisa saber que foi **a ação** que não saiu, e não o modelo nem o
#: servidor. O detalhe fica no log, com traceback.
FERRAMENTA_QUEBROU = "Deu um erro aqui e eu nao consegui fazer isso."

#: A retentativa do pedido ao LLM (`llm_retries`). A pausa é curta porque o
#: caso que ela cobre -- conexão ociosa que o provedor fechou, um 5xx solto --
#: some sozinho na conexão seguinte; esperar mais é silêncio a troco de nada.
RETRY_PAUSA = 0.3

#: Só se repete o que falhou depressa. Conexão recusada ou derrubada e 5xx
#: chegam em menos de um segundo; o que demorou mais que isto para falhar é
#: tempo esgotado, e repetir dobraria o silêncio de quem já esperou demais.
RETRY_JANELA = 3.0


class LLMIndisponivel(Exception):
    """O provedor do LLM falhou: fora do ar, tempo esgotado, erro HTTP.

    Existe para separar esta falha de todas as outras do turno. Um `except
    Exception` em volta do turno inteiro também pegaria defeito nosso -- e
    diria ao usuário que o modelo caiu quando quem quebrou foi o gateway.

    `falando` diz se o `state speaking` deste turno já saiu, para o aviso não
    anunciar o mesmo estado duas vezes quando a queda é no meio da resposta.
    """

    def __init__(self, falando: bool) -> None:
        super().__init__("o provedor do LLM falhou")
        self.falando = falando


class TurnoAbortado(Exception):
    """O turno acabou num `error`, e depois dele nada mais vai pelo fio.

    No protocolo, `error` encerra o turno dos dois lados: o dispositivo volta
    para IDLE ao recebê-lo e para de ler. Seguir com o turno depois de avisar
    -- que era o que a espera da ferramenta fazia -- deixava `speaking`, as
    frases e o `idle` na fila do socket, para o turno **seguinte** ler como se
    fossem dele.

    É uma classe à parte, e não um `except Exception`, pela mesma razão de
    `LLMIndisponivel`: cada falha tem a sua frase, e esta é a do dispositivo.
    """


def _falha_definitiva(exc: BaseException) -> bool:
    """O provedor **recusou** o pedido (4xx), em vez de falhar em atendê-lo?

    Chave errada, histórico inválido, cota estourada: o segundo pedido recebe a
    mesma recusa. O status é procurado nos dois lugares em que os provedores
    daqui o deixam -- `ErroHTTP.status` e a resposta do `httpx.HTTPStatusError`.
    """
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


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
        llm_retries: int = 0,
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
        # Ver `_pedir_resposta`. Vale por um turno só: `_turn` zera no começo.
        self._expects_reply = False
        # Quantas vezes repetir o pedido ao LLM que falhou antes de começar a
        # responder. Zero aqui; quem liga é o `gateway.main`, e só sem reserva
        # -- com reserva, a retentativa é o outro provedor.
        self._llm_retries = llm_retries

    def _pedir_resposta(self) -> None:
        """Marca que este turno termina numa pergunta ao usuário.

        Quem chama é o caminho de código que **sabe** que perguntou -- hoje o
        cancelamento ambíguo, amanhã a confirmação antes de lançar uma despesa.
        O `idle` do fim do turno leva o aviso e o dispositivo reabre o
        microfone sem exigir o nome.

        É explícito de propósito. Adivinhar pelo "?" no texto do modelo abriria
        o microfone a cada "Posso ajudar em mais alguma coisa?", e microfone
        aberto sem wake word é exatamente o que a televisão aproveita.
        """
        self._expects_reply = True

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
        self._expects_reply = False
        await self._send(StateMessage(value=State.THINKING))
        # Eco do que foi entendido: o rosto mostra a transcrição, e quem lê o log
        # do gateway vê a frase que gerou a resposta.
        await self._send(Transcript(text=text, role="user"))

        antes = self._conversation.checkpoint()
        self._conversation.add_user(text)
        try:
            answer = await self._speak_response(turn)
        except LLMIndisponivel as exc:
            # Sem isto a exceção subia até o FastAPI, que fechava o socket, e o
            # dispositivo dizia "nao consigo falar com o servidor" com o
            # servidor de pé. Provedor fora do ar é rotina, ainda mais com o
            # LLM numa API de nuvem: o turno termina, a sessão não.
            log.error("LLM indisponivel; turno abandonado: %r", exc.__cause__)
            self._conversation.rollback(antes)
            # O aviso vai como frase do assistente, e não como `error`: o
            # dispositivo fala transcript e só imprime erro, e aqui o que
            # importa é a pessoa ouvir. O que já tinha sido falado fica falado;
            # a meia frase que estava pendente é descartada.
            if not exc.falando:
                await self._send(StateMessage(value=State.SPEAKING))
            await self._send(Transcript(text=LLM_FORA_DO_AR, role="assistant", final=False))
            answer = ""
        except TurnoAbortado as exc:
            log.warning("turno abortado: %s", exc)
            self._conversation.rollback(antes)
            # O `error` é a última mensagem deste turno, e não vem `idle`
            # depois: o dispositivo sai do turno ao lê-lo, e um `idle` sobrando
            # na fila encerraria o turno seguinte antes de ele começar. A frase
            # falada é a do dispositivo -- aqui não vai transcript, para a
            # mesma falha não ser dita duas vezes.
            await self._send(Error(message=str(exc)))
            return
        else:
            if not answer:
                # O modelo devolveu vazio, ou pediu ferramenta até estourar as
                # rodadas sem dizer nada. Sem isto o aparelho ia de `thinking`
                # a `idle` calado, e silêncio parece microfone com defeito.
                # Resposta vazia quer dizer que nada foi falado -- toda frase
                # enviada entra em `full` --, então o `speaking` ainda não saiu.
                log.warning("turno sem resposta do modelo")
                await self._send(StateMessage(value=State.SPEAKING))
                await self._send(Transcript(text=SEM_RESPOSTA, role="assistant", final=False))
        if answer:
            self._conversation.add_assistant(answer)
        else:
            # Turno sem resposta nenhuma -- o modelo devolveu vazio, ou só pediu
            # ferramenta até estourar as rodadas. Deixar a pergunta no histórico
            # faria o turno seguinte começar com dois `user` seguidos; o aviso
            # falado acima também não entra, porque não é resposta a nada.
            # Ferramenta terminal não cai aqui: a frase dela é a resposta.
            self._conversation.rollback(antes)

        # `None`, e não `False`, quando ninguém perguntou: o campo nem vai para
        # o fio, e o turno comum continua idêntico ao de antes dele existir.
        # Turno sem resposta (o modelo caiu no meio) também não pergunta nada,
        # mesmo que uma ferramenta tenha pedido antes da queda.
        perguntou = self._expects_reply and bool(answer)
        await self._send(StateMessage(value=State.IDLE, expects_reply=True if perguntou else None))
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

        retentativas = self._llm_retries

        async def stream():
            # Só a falha do provedor vira `LLMIndisponivel`. O que quebra no
            # corpo do laço abaixo -- o socket do dispositivo caindo no meio de
            # um `flush`, por exemplo -- não passa por aqui e continua sendo o
            # que era.
            nonlocal retentativas
            while True:
                entregou = False
                inicio = time.monotonic()
                try:
                    async for delta in self._llm.respond(
                        self._conversation.prompt(), tools=self._tools
                    ):
                        entregou = True
                        yield delta
                    return
                except Exception as exc:  # noqa: BLE001 -- cada provedor falha do seu jeito
                    # Repete-se só o **pedido**, e só enquanto nada saiu dele:
                    # um `Delta` entregue pode já ter sido falado, ou ser uma
                    # chamada de ferramenta já executada. As ferramentas das
                    # rodadas anteriores estão no histórico e não rodam de novo.
                    # A conta é por turno, não por rodada: uma retentativa é o
                    # que cabe numa conversa falada.
                    if (
                        entregou
                        or retentativas <= 0
                        or time.monotonic() - inicio > RETRY_JANELA
                        or _falha_definitiva(exc)
                    ):
                        raise LLMIndisponivel(falando=spoken) from exc
                    retentativas -= 1
                    log.warning("llm falhou antes de responder (%r); tentando de novo", exc)
                    await asyncio.sleep(RETRY_PAUSA)

        # Uma rodada por chamada de ferramenta: o modelo pede, o dispositivo
        # executa, o resultado volta para o histórico e o modelo continua. Sem
        # este laço a chamada saía pelo fio e ninguém esperava a resposta -- que
        # era o estado do código até aqui.
        for rodada in range(MAX_TOOL_ROUNDS + 1):
            chamada: tuple[str, dict, str | None] | None = None

            async for delta in stream():
                if delta.tool_name:
                    # Quem executa é decidido abaixo, pelo nome: busca e Spotify
                    # aqui no gateway, o resto no dispositivo (seção 5, regra 3).
                    chamada = (delta.tool_name, delta.tool_args or {}, delta.tool_call_id)
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

            nome, args, call_id = chamada
            if rodada == MAX_TOOL_ROUNDS:
                # A chamada que não foi executada não entra no histórico. Isto
                # gravava só o resultado ("falhou: numero maximo..."), sem a
                # chamada: um `tool` órfão, que o Ollama tolera e uma API de
                # nuvem recusa.
                log.warning("modelo insistiu em ferramenta apos %d rodadas", rodada)
                break

            quebrou = False
            try:
                resultado = await self._executar(nome, args)
            except (WebSocketDisconnect, TurnoAbortado):
                # Não são defeito de ferramenta: o socket caiu, ou o turno já
                # foi encerrado com `error`. Cada um tem quem o trate acima.
                raise
            except Exception:  # noqa: BLE001 -- ver o comentário abaixo
                # As ferramentas viram frase o erro que conhecem; o que chega
                # aqui é o que ninguém previu -- um `KeyError` num JSON que
                # mudou de forma, um defeito nosso. Subindo, fechava o socket e
                # o dispositivo dizia que o servidor caiu, com o servidor de pé.
                # O `except` largo cobre **só** a execução da ferramenta: em
                # volta do turno ele também engoliria defeito do laço e do
                # histórico, e diria a frase errada para eles.
                log.exception("ferramenta %s %s levantou excecao", nome, args)
                resultado = "falhou: erro interno ao executar a ferramenta"
                quebrou = True
            turn.mark(f"ferramenta:{nome}")

            # O par pedido/resultado vai para o histórico **sempre**, inclusive
            # nas ferramentas terminais. Sem ele o modelo para de chamar
            # ferramenta no turno seguinte e passa a narrar ações que não fez
            # (D22).
            # O id do provedor é mantido quando existe e ainda não foi usado
            # nesta conversa. Senão é gerado aqui, num formato que cabe nas
            # duas famílias de API: só letras, dígitos e `_`, até 40 caracteres.
            if not call_id or self._conversation.has_tool_call(call_id):
                call_id = f"call_{uuid.uuid4().hex[:24]}"
            self._conversation.add_tool_call(nome, args, call_id)
            self._conversation.add_tool_result(nome, resultado, call_id)

            if quebrou:
                # O par acima fica no histórico com a falha, e a frase fixa o
                # fecha como a de uma ferramenta terminal faria. Não há rodada
                # extra para o modelo comentar: ele só poderia inventar um
                # motivo, e a ferramenta que acabou de quebrar seria a primeira
                # que ele pediria de novo.
                await flush(FERRAMENTA_QUEBROU)
                full += FERRAMENTA_QUEBROU
                break

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

    async def _executar(self, nome: str, args: dict) -> str:
        """Entrega a chamada a quem a executa, pelo nome (seção 5, regra 3)."""
        if nome in SEARCH_TOOL_NAMES:
            return await executar_busca(self._search, nome, args, self._leitor)
        if nome in SPOTIFY_TOOL_NAMES:
            # O gateway executa: o segredo mora aqui.
            return await executar_spotify(self._spotify, nome, args)
        return await self._run_device_tool(nome, args)

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
        if result.expects_reply:
            # A frase que o dispositivo redigiu é uma pergunta ("Qual eu
            # cancelo?"). Só ele sabe disso; aqui o aviso só muda de mão.
            self._pedir_resposta()
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
                # Avisar e continuar esperando -- o que isto fazia -- deixava as
                # duas pontas em turnos diferentes: o dispositivo sai do turno
                # no `error`. Quem manda o `error` é `_turn`, por último.
                raise TurnoAbortado(str(exc)) from exc
            if isinstance(message, ToolResult) and message.id == call_id:
                return message
            log.debug("ignoring %s while waiting for tool_result", getattr(message, "type", "?"))

    async def _send(self, message: object) -> None:
        await self._ws.send_text(encode(message))
