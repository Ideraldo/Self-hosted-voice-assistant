"""O gateway manda texto, nunca áudio.

É a decisão D1 virada em teste: depois que a síntese passou para o dispositivo,
qualquer `send_bytes` no gateway é regressão — significa que voltamos a gastar
250 KB por interação e que o aparelho deixou de conseguir falar offline.

O teste também fixa o comportamento de streaming: as frases saem conforme
completam, não de uma vez no fim. Sem isso a latência percebida dobra, e é o
tipo de coisa que se perde numa refatoração sem ninguém notar.
"""

from __future__ import annotations

import json

import pytest

from gateway.api.session import Session
from gateway.llm.base import Delta


class FakeLLM:
    """Devolve a resposta em pedaços, como um provedor real faria."""

    def __init__(self, pedacos: list[str]) -> None:
        self._pedacos = pedacos

    async def respond(self, history, tools):
        for pedaco in self._pedacos:
            yield Delta(text=pedaco)


class FakeSocket:
    """Registra o que foi enviado, separando texto de binário."""

    def __init__(self) -> None:
        self.textos: list[dict] = []
        self.binarios: list[bytes] = []

    async def send_text(self, raw: str) -> None:
        self.textos.append(json.loads(raw))

    async def send_bytes(self, data: bytes) -> None:
        self.binarios.append(data)


def build(pedacos: list[str]) -> tuple[Session, FakeSocket]:
    socket = FakeSocket()
    session = Session(
        websocket=socket,
        llm=FakeLLM(pedacos),
        expected_token="t",
    )
    return session, socket


@pytest.mark.asyncio
async def test_nao_manda_audio_pela_rede():
    from gateway.timing import Turn

    session, socket = build(["Oi. ", "Tudo bem?"])
    await session._speak_response(Turn())

    assert socket.binarios == [], "o gateway voltou a mandar audio pela rede"


@pytest.mark.asyncio
async def test_manda_uma_frase_por_vez():
    from gateway.timing import Turn

    session, socket = build(["Bom dia.", " Ja sao sete horas.", " Quer levantar?"])
    await session._speak_response(Turn())

    parciais = [m["text"] for m in socket.textos if m["type"] == "transcript" and not m["final"]]
    assert parciais == ["Bom dia.", "Ja sao sete horas.", "Quer levantar?"]


@pytest.mark.asyncio
async def test_manda_a_resposta_inteira_no_fim():
    from gateway.timing import Turn

    session, socket = build(["Bom dia.", " Tudo certo."])
    resposta = await session._speak_response(Turn())

    finais = [m["text"] for m in socket.textos if m["type"] == "transcript" and m["final"]]
    assert finais == ["Bom dia. Tudo certo."]
    assert resposta == "Bom dia. Tudo certo."


@pytest.mark.asyncio
async def test_anuncia_que_esta_falando_antes_da_primeira_frase():
    from gateway.timing import Turn

    session, socket = build(["Oi."])
    await session._speak_response(Turn())

    tipos = [(m["type"], m.get("value") or m.get("final")) for m in socket.textos]
    assert tipos[0] == ("state", "speaking"), "o rosto precisa saber antes do audio comecar"


class FakeReceiveSocket(FakeSocket):
    """Entrega pacotes já no formato do Starlette, um por chamada."""

    def __init__(self, pacotes: list[dict]) -> None:
        super().__init__()
        self._pacotes = list(pacotes)

    async def receive(self) -> dict:
        return self._pacotes.pop(0)


@pytest.mark.asyncio
async def test_recebe_frase_transcrita_pelo_dispositivo():
    """Depois de D1 o gateway lê texto: o STT rodou antes do fio."""
    socket = FakeReceiveSocket([{"text": '{"type": "utterance", "text": " que horas sao? "}'}])
    session = Session(websocket=socket, llm=FakeLLM([]), expected_token="t")

    assert await session._receive_utterance() == "que horas sao?"


@pytest.mark.asyncio
async def test_recusa_audio_subindo_pelo_fio():
    """Áudio do dispositivo para o gateway é regressão de D1, não um formato aceito."""
    socket = FakeReceiveSocket(
        [{"bytes": b"\x00\x01"}, {"text": '{"type": "utterance", "text": "oi"}'}]
    )
    session = Session(websocket=socket, llm=FakeLLM([]), expected_token="t")

    assert await session._receive_utterance() == "oi"
    assert [m["type"] for m in socket.textos] == ["error"]


class TestQuebraDeFrase:
    """Onde o gateway decide que ja da para o dispositivo comecar a falar.

    O ponto entre digitos e separador, nao fim de frase. Isto so apareceu
    quando a busca na internet trouxe numeros formatados: "384.400 quilometros"
    virava duas falas, "aproximadamente 384." e "400 quilometros."
    """

    import pytest as _pytest

    @_pytest.mark.parametrize(
        "texto, quebra",
        [
            ("Sao 20 horas.", True),
            ("Quem escreveu?", True),
            ("Nao sei!", True),
            ("Ainda escrevendo", False),
            ("aproximadamente 384.", False),   # separador de milhar
            ("O ano foi 1899.", False),        # ponto depois de digito: segura
            ("384.400 quilometros.", True),    # termina em letra, entao quebra
            ("", False),
        ],
    )
    def test_decide_certo(self, texto, quebra):
        from gateway.api.session import _fim_de_frase

        assert _fim_de_frase(texto) is quebra


class LLMQueCai:
    """Um provedor que cai, como o Ollama desligado ou uma API de nuvem fora.

    ``falhas`` diz em quais chamadas cair (0 é a primeira). ``antes`` é o texto
    que ainda sai antes da queda -- vazio para a conexão recusada, com conteúdo
    para o stream que morre no meio da resposta. Guarda o histórico que recebeu
    em cada chamada, porque é nele que um turno perdido deixaria sujeira.
    """

    def __init__(self, falhas: set[int], antes: list[str] | None = None) -> None:
        self._falhas = falhas
        self._antes = antes or []
        self.historicos: list[list] = []

    async def respond(self, history, tools):
        chamada = len(self.historicos)
        self.historicos.append(list(history))
        if chamada in self._falhas:
            for pedaco in self._antes:
                yield Delta(text=pedaco)
            import httpx

            raise httpx.ConnectError("All connection attempts failed")
        yield Delta(text="Tudo certo.")


class FakeDeviceSocket(FakeReceiveSocket):
    """O dispositivo inteiro, do aperto de mão à desconexão."""

    def __init__(self, frases: list[str]) -> None:
        super().__init__(
            [{"text": json.dumps({"type": "session_start", "device_id": "d", "token": "t"})}]
            + [{"text": json.dumps({"type": "utterance", "text": f})} for f in frases]
            + [{"type": "websocket.disconnect", "code": 1000}]
        )
        self.fechado = False

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        return (await self.receive())["text"]

    async def close(self, code: int = 1000) -> None:
        self.fechado = True


class TestLLMForaDoAr:
    """O modelo cair não é o gateway cair, e o aparelho precisa saber a diferença.

    Antes destes testes a exceção do provedor subia até o FastAPI, que fechava
    o socket; o dispositivo via a conexão morrer e dizia "não consigo falar com
    o servidor" -- com o servidor de pé. Com o LLM indo para uma API de nuvem,
    provedor fora do ar deixa de ser acidente e vira rotina.
    """

    @pytest.mark.asyncio
    async def test_a_sessao_sobrevive_e_o_turno_seguinte_funciona(self):
        llm = LLMQueCai(falhas={0})
        socket = FakeDeviceSocket(["primeira", "segunda"])
        session = Session(websocket=socket, llm=llm, expected_token="t")

        await session.run()  # antes da correção: httpx.ConnectError sai daqui

        assert not socket.fechado
        falas = [
            m["text"]
            for m in socket.textos
            if m["type"] == "transcript" and m["role"] == "assistant" and not m["final"]
        ]
        assert falas[-1] == "Tudo certo."

    @pytest.mark.asyncio
    async def test_falha_antes_de_qualquer_texto_e_dita_em_voz_alta(self):
        from gateway.api.session import LLM_FORA_DO_AR

        socket = FakeDeviceSocket(["oi"])
        session = Session(websocket=socket, llm=LLMQueCai(falhas={0}), expected_token="t")

        await session.run()

        # Depois do `idle` do aperto de mão, o turno inteiro. A frase vai como
        # transcript parcial do assistente porque é a única coisa que o
        # dispositivo fala; um `error` ele só imprime.
        turno = [(m["type"], m.get("value") or m.get("text")) for m in socket.textos[1:]]
        assert turno == [
            ("state", "thinking"),
            ("transcript", "oi"),
            ("state", "speaking"),
            ("transcript", LLM_FORA_DO_AR),
            ("state", "idle"),
        ]
        assert socket.textos[-2]["role"] == "assistant"
        assert socket.textos[-2]["final"] is False
        assert "error" not in [m["type"] for m in socket.textos]

    @pytest.mark.asyncio
    async def test_falha_no_meio_da_resposta_avisa_e_termina_o_turno(self):
        from gateway.api.session import LLM_FORA_DO_AR

        llm = LLMQueCai(falhas={0}, antes=["A Lua fica longe.", " Sao cerca de"])
        socket = FakeDeviceSocket(["qual a distancia da lua?"])
        session = Session(websocket=socket, llm=llm, expected_token="t")

        await session.run()

        falas = [
            m["text"]
            for m in socket.textos
            if m["type"] == "transcript" and m["role"] == "assistant"
        ]
        # A frase que já tinha saído foi falada e não se desfala; o pedaço pela
        # metade é descartado, porque meia frase seguida de um aviso soa pior
        # que o aviso sozinho.
        assert falas == ["A Lua fica longe.", LLM_FORA_DO_AR]
        estados = [m["value"] for m in socket.textos if m["type"] == "state"]
        assert estados == ["idle", "thinking", "speaking", "idle"], "speaking so uma vez"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("antes", [[], ["A Lua fica longe.", " Sao cerca de"]])
    async def test_o_turno_perdido_nao_fica_no_historico(self, antes):
        """Uma pergunta sem resposta no histórico envenena o turno seguinte.

        O modelo veria duas mensagens `user` seguidas e responderia às duas --
        ou, pior, a meia resposta de antes viraria contexto.
        """
        llm = LLMQueCai(falhas={0}, antes=antes)
        socket = FakeDeviceSocket(["primeira", "segunda"])
        session = Session(websocket=socket, llm=llm, expected_token="t")

        await session.run()

        segundo = [(m.role, m.content) for m in llm.historicos[1] if m.role != "system"]
        assert segundo == [("user", "segunda")]

    @pytest.mark.asyncio
    async def test_falha_depois_da_busca_nao_deixa_chamada_orfa(self):
        """A segunda rodada cai depois de a busca ter ido para o histórico.

        O par `tool_call` + `tool` (D22) sem a resposta que o fecha é um
        histórico que nenhum modelo foi treinado a ver.
        """

        class BuscaEDepoisCai(LLMQueCai):
            async def respond(self, history, tools):
                if not self.historicos:
                    self.historicos.append(list(history))
                    yield Delta(tool_name="buscar_na_internet", tool_args={"consulta": "lua"})
                    return
                async for delta in super().respond(history, tools):
                    yield delta

        class Busca:
            nome = "fake"

            async def buscar(self, consulta, quantos):
                return []

        llm = BuscaEDepoisCai(falhas={1})
        socket = FakeDeviceSocket(["primeira", "segunda"])
        session = Session(websocket=socket, llm=llm, expected_token="t", search=Busca())

        await session.run()

        assert len(llm.historicos) == 3
        terceiro = [(m.role, m.content) for m in llm.historicos[2] if m.role != "system"]
        assert terceiro == [("user", "segunda")]

    @pytest.mark.asyncio
    async def test_a_falha_vai_para_o_log(self, caplog):
        socket = FakeDeviceSocket(["oi"])
        session = Session(websocket=socket, llm=LLMQueCai(falhas={0}), expected_token="t")

        with caplog.at_level("ERROR", logger="ideraldinho.session"):
            await session.run()

        assert "ConnectError" in caplog.text

    @pytest.mark.asyncio
    async def test_o_dispositivo_fala_o_aviso_e_volta_a_ouvir(self):
        """As duas pontas juntas: o que o gateway manda é o que o aparelho fala."""
        import asyncio

        from common.messages import State
        from common.serialization import decode
        from device.main import handle_incoming
        from device.state import StateMachine
        from gateway.api.session import LLM_FORA_DO_AR

        socket = FakeDeviceSocket(["oi"])
        session = Session(websocket=socket, llm=LLMQueCai(falhas={0}), expected_token="t")
        await session.run()

        class Cliente:
            async def receive(self):
                for m in socket.textos[1:]:  # o primeiro idle é do aperto de mão
                    yield decode(json.dumps(m))

        class Voz:
            def synthesize(self, text):
                return text

        class AltoFalante:
            def __init__(self):
                self.falado = []

            def play(self, pcm):
                self.falado.append(pcm)

        machine = StateMachine()
        machine.transition(State.LISTENING)
        alto_falante = AltoFalante()
        await handle_incoming(Cliente(), machine, Voz(), alto_falante, asyncio.Lock(), None)

        assert alto_falante.falado == [LLM_FORA_DO_AR]
        assert machine.state is State.IDLE
