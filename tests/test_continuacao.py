"""A janela de continuação: o assistente pergunta e ouve a resposta.

Antes dela o turno terminava em IDLE e pronto. Com a palavra de ativação
ligada, responder "sim" exigia dizer o nome de novo; e um "cancela" dito em
resposta a "Confirma?" era lido pelo roteador de nível 0 como "cancela o timer".

Não há microfone aqui, e não precisa: o que estes testes fixam é a ordem das
decisões do laço -- quando ele espera o nome, por quanto tempo ouve, e para
onde manda a frase. O microfone, o STT e o wake word são substituídos por
roteiros; o serviço local e a máquina de estados são os de verdade, porque é
neles que o defeito apareceria (um timer cancelado, uma transição ilegal).
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from common.messages import State, StateMessage, ToolCall, Transcript
from common.serialization import decode, encode
from device import main as device_main
from device.local.service import (
    QUAL_CANCELAR,
    LocalServices,
    pede_resposta,
    responder_qual_cancelar,
)
from device.local.store import ScheduleStore
from device.state import StateMachine
from gateway.api.session import Session
from gateway.llm.base import Delta

JANELA = device_main.config.reply_window_s


class Fim(Exception):
    """O roteiro acabou. É o que tira o teste de dentro do `while True`."""


class Microfone:
    """Cada `listen` consome uma fala do roteiro; `None` é silêncio."""

    def __init__(self, falas: list[str | None], eventos: list) -> None:
        self._falas = list(falas)
        self._eventos = eventos

    def read_frame(self) -> bytes:
        return b""

    def listen(self, on_start=None, start_timeout=None) -> bytes:
        if not self._falas:
            raise Fim
        self._eventos.append(("ouve", start_timeout))
        fala = self._falas.pop(0)
        return fala.encode() if fala else b""


class STT:
    def transcribe(self, pcm: bytes) -> str:
        return pcm.decode()


class Wake:
    """Acorda no primeiro frame, e anota que alguém teve que dizer o nome."""

    def __init__(self, eventos: list) -> None:
        self._eventos = eventos

    def reset(self) -> None:
        pass

    def feed(self, frame: bytes) -> bool:
        self._eventos.append("nome")
        return True


class Cliente:
    """O gateway visto do dispositivo: um turno combinado por `utterance`."""

    def __init__(self, turnos: list[list]) -> None:
        self._turnos = list(turnos)
        self.enviadas: list[str] = []

    async def send_utterance(self, text: str) -> None:
        self.enviadas.append(text)
        self._mensagens = self._turnos.pop(0)

    async def receive(self):
        for mensagem in self._mensagens:
            yield mensagem


class Voz:
    def synthesize(self, text):
        return text


class AltoFalante:
    def __init__(self) -> None:
        self.falado: list[str] = []

    def play(self, pcm) -> None:
        self.falado.append(pcm)


def turno(frase: str, pergunta: bool | None = None) -> list:
    """O que o gateway manda num turno; `pergunta` vira `expects_reply`."""
    return [
        StateMessage(value=State.THINKING),
        StateMessage(value=State.SPEAKING),
        Transcript(text=frase, role="assistant", final=False),
        Transcript(text=frase, role="assistant", final=True),
        StateMessage(value=State.IDLE, expects_reply=pergunta),
    ]


@pytest.fixture
def store(tmp_path):
    s = ScheduleStore(tmp_path / "schedules.db")
    yield s
    s.close()


class Bancada:
    """Tudo o que `voice_loop` pede, com os registros à mão."""

    def __init__(self, store, falas, turnos, com_wake=True) -> None:
        self.eventos: list = []
        self.cliente = Cliente(turnos)
        self.machine = StateMachine()
        self.estados: list[State] = []
        self.machine.subscribe(lambda estado, _: self.estados.append(estado))
        self.alto_falante = AltoFalante()
        self.store = store
        self.services = LocalServices(store)
        self._microfone = Microfone(falas, self.eventos)
        self._wake = Wake(self.eventos) if com_wake else None

    async def rodar(self) -> None:
        with pytest.raises(Fim):
            await device_main.voice_loop(
                self.cliente, self.machine, self._microfone, STT(), Voz(),
                self.alto_falante, self.services, asyncio.Lock(), self._wake,
            )
        # O roteiro acaba dentro de um `listen`, com a máquina já em LISTENING.
        # Esse último passo é do andaime, não do que está sendo testado.
        assert self.estados.pop() is State.LISTENING


class TestPerguntaDoGateway:
    async def test_a_resposta_nao_exige_o_nome(self, store):
        b = Bancada(
            store,
            falas=["lanca oitenta e sete reais no outback", "sim"],
            turnos=[turno("Vou lancar. Confirma?", pergunta=True), turno("Lancado.")],
        )
        await b.rodar()

        # Um "nome" só antes da primeira frase. A resposta entra pela janela
        # curta; depois do turno que não pergunta nada, o nome volta a valer.
        assert b.eventos == ["nome", ("ouve", None), ("ouve", JANELA), "nome"]
        assert b.cliente.enviadas == ["lanca oitenta e sete reais no outback", "sim"]
        assert b.alto_falante.falado == ["Vou lancar. Confirma?", "Lancado."]
        assert b.estados[-1] is State.IDLE

    async def test_cancela_vai_para_o_gateway_e_o_timer_fica(self, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        b = Bancada(
            store,
            falas=["lanca oitenta e sete reais no outback", "cancela"],
            turnos=[turno("Vou lancar. Confirma?", pergunta=True), turno("Deixa pra la.")],
        )
        await b.rodar()

        assert b.cliente.enviadas[-1] == "cancela"
        assert len(store.pending()) == 1, "o 'cancela' da resposta cancelou o timer"
        assert "Timer cancelado." not in b.alto_falante.falado

    async def test_sem_resposta_a_janela_fecha_em_silencio(self, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        b = Bancada(
            store,
            falas=["lanca oitenta e sete reais no outback", None, "cancela"],
            turnos=[turno("Vou lancar. Confirma?", pergunta=True)],
        )
        await b.rodar()

        # Janela vazia: nada é dito, nada sobe, e o aparelho volta a esperar o
        # nome. O "cancela" que vem depois já é um comando comum de nível 0.
        assert b.eventos == [
            "nome", ("ouve", None), ("ouve", JANELA), "nome", ("ouve", None), "nome",
        ]
        assert b.cliente.enviadas == ["lanca oitenta e sete reais no outback"]
        assert b.alto_falante.falado == ["Vou lancar. Confirma?", "Timer cancelado."]
        assert store.pending() == []

    async def test_a_janela_vazia_passa_por_listening_e_volta_a_idle(self, store):
        b = Bancada(
            store,
            falas=["quanto eu gastei", None],
            turnos=[turno("Em que mes?", pergunta=True)],
        )
        await b.rodar()

        # A máquina de verdade está no laço: transição ilegal teria estourado.
        assert b.estados[-2:] == [State.LISTENING, State.IDLE]

    async def test_perguntas_em_sequencia_mantem_a_janela(self, store):
        b = Bancada(
            store,
            falas=["quanto eu gastei", "em marco", "no cartao"],
            turnos=[
                turno("Em que mes?", pergunta=True),
                turno("No cartao ou no total?", pergunta=True),
                turno("Mil reais."),
            ],
        )
        await b.rodar()

        assert b.eventos.count("nome") == 2  # antes da primeira, e depois do fim
        assert b.cliente.enviadas == ["quanto eu gastei", "em marco", "no cartao"]

    async def test_sem_wake_word_a_janela_tambem_caduca(self, store):
        """Sem o nome não há o que pular, mas a pergunta ainda tem prazo."""
        store.add("timer", fire_at=time.time() + 600, label=None)
        b = Bancada(
            store,
            falas=["lanca oitenta e sete reais no outback", None, "cancela"],
            turnos=[turno("Vou lancar. Confirma?", pergunta=True)],
            com_wake=False,
        )
        await b.rodar()

        assert b.eventos == [("ouve", None), ("ouve", JANELA), ("ouve", None)]
        assert store.pending() == []


class TestSemOAviso:
    """Gateway que não manda `expects_reply`: nada muda."""

    @pytest.mark.parametrize("pergunta", [None, False])
    async def test_todo_turno_exige_o_nome(self, store, pergunta):
        b = Bancada(
            store,
            falas=["quem descobriu o brasil", "e a america"],
            # A frase termina em "?" de propósito: o dispositivo não adivinha.
            turnos=[turno("Foi Cabral. Quer saber mais?", pergunta), turno("Colombo.", pergunta)],
        )
        await b.rodar()

        assert b.eventos == ["nome", ("ouve", None), "nome", ("ouve", None), "nome"]

    async def test_cancela_continua_sendo_nivel_zero(self, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        b = Bancada(
            store,
            falas=["quem descobriu o brasil", "cancela"],
            turnos=[turno("Foi Cabral.")],
        )
        await b.rodar()

        assert b.cliente.enviadas == ["quem descobriu o brasil"]
        assert store.pending() == []

    async def test_mensagem_de_gateway_antigo(self, store):
        """O `idle` sem o campo, como sai do fio de um gateway anterior."""
        antigo = turno("Foi Cabral.")[:-1] + [decode('{"type": "state", "value": "idle"}')]
        b = Bancada(store, falas=["quem descobriu o brasil"], turnos=[antigo])
        await b.rodar()

        assert b.eventos == ["nome", ("ouve", None), "nome"]


class TestPerguntaLocal:
    """O cancelamento ambíguo de nível 0 pergunta sem passar pelo gateway.

    Por isso a resposta também não pode ir para lá: o LLM receberia "o timer"
    sem nunca ter visto a pergunta. E tem que funcionar com a rede fora.
    """

    @pytest.fixture
    def dois(self, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        store.add("alarme", fire_at=time.time() + 3600, label=None)
        return store

    async def test_a_resposta_cancela_o_que_foi_dito_sem_nome_e_sem_rede(self, dois):
        b = Bancada(dois, falas=["cancela", "o timer"], turnos=[])
        await b.rodar()

        assert b.eventos == ["nome", ("ouve", None), ("ouve", JANELA), "nome"]
        assert b.alto_falante.falado == [QUAL_CANCELAR, "Timer cancelado."]
        assert b.cliente.enviadas == []
        assert [item.kind for item in dois.pending()] == ["alarme"]

    async def test_o_que_nao_e_resposta_vira_um_turno_comum(self, dois):
        b = Bancada(
            dois, falas=["cancela", "quem descobriu o brasil"], turnos=[turno("Foi Cabral.")]
        )
        await b.rodar()

        assert b.cliente.enviadas == ["quem descobriu o brasil"]
        assert len(dois.pending()) == 2

    async def test_sem_resposta_nada_e_cancelado(self, dois):
        b = Bancada(dois, falas=["cancela", None], turnos=[])
        await b.rodar()

        assert b.alto_falante.falado == [QUAL_CANCELAR]
        assert len(dois.pending()) == 2

    @pytest.mark.parametrize(
        "frase, alvo",
        [
            ("o timer", "timer"),
            ("O alarme.", "alarme"),
            ("cancela o cronômetro", "timer"),
            ("os alarmes", "alarme"),
            ("sim", None),
            ("o timer e o alarme", None),  # os dois: escolher seria chutar
        ],
    )
    def test_leitura_da_resposta(self, frase, alvo):
        intent = responder_qual_cancelar(frase)
        assert (intent.slots["alvo"] if intent else None) == alvo

    def test_so_a_pergunta_pede_resposta(self):
        assert pede_resposta(QUAL_CANCELAR)
        assert not pede_resposta("Timer cancelado.")
        assert not pede_resposta(None)


class TestModoTexto:
    """No teclado a janela é "a próxima linha é a resposta"."""

    async def _rodar(self, monkeypatch, store, linhas, turnos):
        linhas = list(linhas)
        prompts: list[str] = []

        async def read_line(prompt: str) -> str:
            if not linhas:
                raise Fim
            prompts.append(prompt)
            return linhas.pop(0)

        monkeypatch.setattr(device_main, "read_line", read_line)
        cliente, alto_falante = Cliente(turnos), AltoFalante()
        with pytest.raises(Fim):
            await device_main.text_loop(
                cliente, StateMachine(), Voz(), alto_falante, LocalServices(store), asyncio.Lock()
            )
        return cliente, prompts

    async def test_a_linha_seguinte_e_a_resposta(self, monkeypatch, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        cliente, prompts = await self._rodar(
            monkeypatch, store,
            ["lanca oitenta e sete reais no outback", "cancela", "cancela"],
            [turno("Confirma?", pergunta=True), turno("Deixa pra la.")],
        )

        # O primeiro "cancela" responde a pergunta; o segundo é o comando.
        assert cliente.enviadas == ["lanca oitenta e sete reais no outback", "cancela"]
        assert prompts == ["voce: ", "resposta: ", "voce: "]
        assert store.pending() == []

    async def test_linha_vazia_desiste_da_pergunta(self, monkeypatch, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        cliente, prompts = await self._rodar(
            monkeypatch, store,
            ["lanca oitenta e sete reais no outback", "", "cancela"],
            [turno("Confirma?", pergunta=True)],
        )

        assert cliente.enviadas == ["lanca oitenta e sete reais no outback"]
        assert prompts == ["voce: ", "resposta: ", "voce: "]
        assert store.pending() == []


# ---------------------------------------------------------------------------
# O lado do gateway
# ---------------------------------------------------------------------------


class LLMRoteirizado:
    """Uma lista de deltas por chamada, na ordem em que o modelo é consultado."""

    def __init__(self, chamadas: list[list[Delta]]) -> None:
        self._chamadas = list(chamadas)

    async def respond(self, history, tools):
        for delta in self._chamadas.pop(0):
            yield delta


class SocketComDispositivo:
    """O dispositivo do outro lado do fio, executando ferramenta de verdade.

    As `tool_call` que o gateway manda são respondidas pelo mesmo
    `executar_ferramenta` que roda no aparelho -- é o que faz deste um teste
    das duas pontas, e não de um JSON escrito à mão.
    """

    def __init__(self, frases: list[str], services) -> None:
        self._entrada = [
            json.dumps({"type": "session_start", "device_id": "d", "token": "t"})
        ] + [json.dumps({"type": "utterance", "text": f}) for f in frases]
        self._services = services
        self.textos: list[dict] = []

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        pass

    async def send_text(self, raw: str) -> None:
        self.textos.append(json.loads(raw))
        mensagem = decode(raw)
        if isinstance(mensagem, ToolCall):
            resultado = device_main.executar_ferramenta(self._services, mensagem)
            self._entrada.insert(0, encode(resultado))

    async def receive(self) -> dict:
        if not self._entrada:
            return {"type": "websocket.disconnect", "code": 1000}
        return {"text": self._entrada.pop(0)}

    async def receive_text(self) -> str:
        return (await self.receive())["text"]

    def idles(self) -> list[dict]:
        """Os `idle` que encerram turno (o primeiro é o do aperto de mão)."""
        return [m for m in self.textos if m["type"] == "state" and m["value"] == "idle"][1:]


class TestGateway:
    async def test_turno_comum_nao_manda_o_campo(self, store):
        socket = SocketComDispositivo(["oi"], LocalServices(store))
        llm = LLMRoteirizado([[Delta(text="Oi.")]])
        await Session(websocket=socket, llm=llm, expected_token="t").run()

        # Igual, chave por chave, ao `idle` de antes do campo existir.
        assert socket.idles() == [{"type": "state", "value": "idle"}]

    async def test_interrogacao_no_texto_do_modelo_nao_abre_a_janela(self, store):
        """Só pergunta quem sabe que perguntou; "?" no texto não conta."""
        socket = SocketComDispositivo(["oi"], LocalServices(store))
        llm = LLMRoteirizado([[Delta(text="Oi. Posso ajudar em mais alguma coisa?")]])
        await Session(websocket=socket, llm=llm, expected_token="t").run()

        assert socket.idles() == [{"type": "state", "value": "idle"}]

    async def test_cancelamento_ambiguo_pede_resposta_e_o_turno_seguinte_nao(self, store):
        store.add("timer", fire_at=time.time() + 600, label=None)
        store.add("alarme", fire_at=time.time() + 3600, label=None)
        socket = SocketComDispositivo(["desmarca aquilo", "o timer"], LocalServices(store))
        llm = LLMRoteirizado(
            [
                [Delta(tool_name="cancelar_agendamento", tool_args={})],
                [Delta(tool_name="cancelar_agendamento", tool_args={"alvo": "timer"})],
            ]
        )
        await Session(websocket=socket, llm=llm, expected_token="t").run()

        primeiro, segundo = socket.idles()
        assert primeiro == {"type": "state", "value": "idle", "expects_reply": True}
        # O aviso vale por um turno: sem zerar, toda resposta depois da
        # primeira pergunta deixaria o microfone aberto.
        assert segundo == {"type": "state", "value": "idle"}
        falas = [
            m["text"] for m in socket.textos if m["type"] == "transcript" and not m["final"]
        ]
        assert falas == [QUAL_CANCELAR, "Timer cancelado."]
        assert [item.kind for item in store.pending()] == ["alarme"]

    async def test_ferramenta_que_nao_pergunta_nao_pede_resposta(self, store):
        socket = SocketComDispositivo(["me avisa em dez minutos"], LocalServices(store))
        llm = LLMRoteirizado([[Delta(tool_name="criar_timer", tool_args={"segundos": 600})]])
        await Session(websocket=socket, llm=llm, expected_token="t").run()

        assert socket.idles() == [{"type": "state", "value": "idle"}]

    async def test_o_gancho_serve_a_quem_pergunta_do_lado_do_gateway(self, store):
        """A confirmação de despesa vai chamar `_pedir_resposta` de dentro do turno."""

        class LLMQuePergunta:
            async def respond(self, history, tools):
                session._pedir_resposta()
                yield Delta(text="Vou lancar oitenta e sete reais no Outback. Confirma?")

        socket = SocketComDispositivo(["gastei 87 no outback"], LocalServices(store))
        session = Session(websocket=socket, llm=LLMQuePergunta(), expected_token="t")
        await session.run()

        assert socket.idles() == [{"type": "state", "value": "idle", "expects_reply": True}]
