"""Nenhuma falha do turno pode ser muda, e nenhuma pode derrubar a conexão.

Com o LLM numa API de nuvem, provedor e ferramenta falhando deixam de ser
acidente. O que estes testes fixam é a regra para quem está na frente do
aparelho, que não tem tela: toda falha é dita em voz alta **uma** vez, com uma
frase que diz qual foi -- e a sessão continua de pé para a frase seguinte.

Onde dá, as duas pontas rodam juntas: o que o gateway manda é entregue ao
`handle_incoming` de verdade, porque metade destes defeitos só existe na
combinação (o gateway continua o turno que o dispositivo já encerrou).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from common.messages import State
from common.serialization import decode
from device.main import ERRO_DO_GATEWAY, handle_incoming
from device.state import StateMachine
from gateway.api.session import (
    FERRAMENTA_QUEBROU,
    LLM_FORA_DO_AR,
    MAX_TOOL_ROUNDS,
    SEM_RESPOSTA,
    Session,
)
from gateway.llm.base import Delta
from gateway.tools.search import Resultado


class LLM:
    """Um passo do roteiro por chamada; exceção no lugar em que o provedor cai."""

    def __init__(self, *roteiro: list) -> None:
        self.roteiro = list(roteiro)
        self.historicos: list[list] = []

    async def respond(self, history, tools):
        self.historicos.append(list(history))
        for item in self.roteiro.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


class Aparelho:
    """O socket visto do gateway. `ao_pedir` decide o que responder a um `tool_call`."""

    def __init__(self, frases: list[str], ao_pedir=None) -> None:
        self._entrada = [
            json.dumps({"type": "session_start", "device_id": "d", "token": "t"})
        ] + [json.dumps({"type": "utterance", "text": f}) for f in frases]
        self._ao_pedir = ao_pedir or (
            lambda m: [{"type": "tool_result", "id": m["id"], "ok": True, "value": "Timer marcado."}]
        )
        self.recebido: list[dict] = []
        self.fechado = False

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        self.fechado = True

    async def send_text(self, raw: str) -> None:
        mensagem = json.loads(raw)
        self.recebido.append(mensagem)
        if mensagem["type"] == "tool_call":
            respostas = self._ao_pedir(mensagem)
            self._entrada[0:0] = [r if isinstance(r, str) else json.dumps(r) for r in respostas]

    async def receive(self) -> dict:
        if not self._entrada:
            return {"type": "websocket.disconnect", "code": 1000}
        return {"text": self._entrada.pop(0)}

    async def receive_text(self) -> str:
        return (await self.receive())["text"]

    # -- leitura do que saiu --

    @property
    def turnos(self) -> list[list[tuple]]:
        """O que o gateway mandou depois do aperto de mão, um turno por `thinking`."""
        turnos: list[list[tuple]] = []
        for m in self.recebido[1:]:
            if m["type"] == "state" and m["value"] == "thinking":
                turnos.append([])
            turnos[-1].append((m["type"], m.get("value") or m.get("text") or m.get("name")))
        return turnos

    @property
    def falas(self) -> list[str]:
        return [
            m["text"]
            for m in self.recebido
            if m["type"] == "transcript" and m["role"] == "assistant" and not m["final"]
        ]


class Voz:
    def synthesize(self, text):
        return text


class AltoFalante:
    def __init__(self) -> None:
        self.falado: list[str] = []

    def play(self, pcm) -> None:
        self.falado.append(pcm)


class Servicos:
    def handle(self, intent):
        return "Timer marcado."


async def ouvir(aparelho: Aparelho, turnos: int) -> tuple[list[list[str]], StateMachine]:
    """Entrega ao dispositivo de verdade tudo o que o gateway mandou.

    Uma fila só para todos os turnos, como o socket: o que um turno deixar sem
    ler é a primeira coisa que o seguinte lê. É isso que mostra a dessincronia.
    """
    fila = iter(aparelho.recebido[1:])  # o primeiro idle é do aperto de mão

    class Cliente:
        async def receive(self):
            for m in fila:
                yield decode(json.dumps(m))

        async def send(self, message) -> None:
            pass

    machine = StateMachine()
    por_turno = []
    for _ in range(turnos):
        machine.transition(State.LISTENING)
        alto_falante = AltoFalante()
        await handle_incoming(
            Cliente(), machine, Voz(), alto_falante, asyncio.Lock(), Servicos()
        )
        por_turno.append(alto_falante.falado)
    return por_turno, machine


def _timer() -> Delta:
    return Delta(tool_name="criar_timer", tool_args={"segundos": 300})


def _busca() -> Delta:
    return Delta(tool_name="buscar_na_internet", tool_args={"consulta": "lua"})


def _pausa() -> Delta:
    return Delta(tool_name="pausar_musica", tool_args={})


class Busca:
    nome = "fake"

    async def buscar(self, consulta, quantos):
        return [Resultado(titulo="Lua", trecho="A Lua.", url="https://exemplo.com/lua")]


class LeitorQueQuebra:
    async def ler(self, url):
        raise RuntimeError("defeito no leitor")


class SpotifyQueQuebra:
    async def pausar(self) -> str:
        # O que uma mudança no JSON da API produz: nem `SpotifyError`, nem
        # `httpx.HTTPError` -- os dois que `executar_spotify` sabe virar frase.
        raise KeyError("device")


def sessao(llm, aparelho, **extras) -> Session:
    return Session(websocket=aparelho, llm=llm, expected_token="t", **extras)


def conversa(llm, n: int) -> list[tuple[str, str]]:
    """O que o provedor viu na chamada `n`, sem o prompt de sistema."""
    return [(m.role, m.content) for m in llm.historicos[n] if m.role != "system"]


# --- item 1: o turno vazio ---------------------------------------------------


class TestTurnoVazio:
    """O modelo não disse nada, e o aparelho ia de `thinking` a `idle` calado."""

    async def test_resposta_vazia_e_dita_em_voz_alta(self):
        aparelho = Aparelho(["oi"])
        await sessao(LLM([]), aparelho).run()

        assert aparelho.turnos == [
            [
                ("state", "thinking"),
                ("transcript", "oi"),
                ("state", "speaking"),
                ("transcript", SEM_RESPOSTA),
                ("state", "idle"),
            ]
        ]
        assert SEM_RESPOSTA != LLM_FORA_DO_AR, "o modelo respondeu vazio; nao esta fora do ar"
        assert "error" not in [m["type"] for m in aparelho.recebido]

    async def test_estouro_de_rodadas_sem_fala_e_dito_em_voz_alta(self):
        aparelho = Aparelho(["pesquisa"])
        llm = LLM(*[[_busca()]] * (MAX_TOOL_ROUNDS + 1))
        await sessao(llm, aparelho, search=Busca()).run()

        assert aparelho.falas == [SEM_RESPOSTA]

    async def test_o_aviso_nao_entra_no_historico(self):
        aparelho = Aparelho(["primeira", "segunda"])
        llm = LLM([], [Delta(text="Oi.")])
        await sessao(llm, aparelho).run()

        assert conversa(llm, 1) == [("user", "segunda")]

    async def test_ferramenta_terminal_nao_e_turno_vazio(self):
        """O modelo não escreveu texto, mas houve resposta: a frase da ferramenta."""
        aparelho = Aparelho(["marca cinco minutos"])
        await sessao(LLM([_timer()]), aparelho).run()

        assert aparelho.falas == ["Timer marcado."]

    async def test_o_dispositivo_fala_e_volta_a_ouvir(self):
        aparelho = Aparelho(["oi"])
        await sessao(LLM([]), aparelho).run()

        (falado,), machine = await ouvir(aparelho, 1)

        assert falado == [SEM_RESPOSTA]
        assert machine.state is State.IDLE


# --- item 2: a ferramenta que quebra -----------------------------------------


class TestFerramentaQuebra:
    """Exceção inesperada numa ferramenta fechava o socket com o gateway de pé.

    `executar_spotify` vira frase o que conhece (`SpotifyError`, erro HTTP) e
    `executar_busca` protege a chamada ao provedor -- mas um `KeyError` num
    JSON que mudou de forma, ou um defeito depois da busca, passavam direto.
    """

    CASOS = {
        "spotify": ([_pausa()], {"spotify": SpotifyQueQuebra()}, None),
        "busca": ([_busca()], {"search": Busca(), "leitor": LeitorQueQuebra()}, None),
        # A ida e volta ao dispositivo saiu daqui: um `tool_result` sem o campo
        # obrigatório `ok` fazia o `decode` levantar `TypeError` e caía nesta
        # rede. O `decode` agora devolve `ValueError` para isso, e mensagem
        # malformada na espera da ferramenta é o caso do `error` que encerra o
        # turno, testado mais abaixo.
    }

    @pytest.fixture(params=sorted(CASOS))
    def caso(self, request):
        passo, extras, ao_pedir = self.CASOS[request.param]
        aparelho = Aparelho(["primeira", "segunda"], ao_pedir)
        llm = LLM(passo, [Delta(text="Tudo certo.")])
        return llm, aparelho, sessao(llm, aparelho, **extras)

    async def test_a_sessao_sobrevive_e_a_pessoa_ouve(self, caso):
        llm, aparelho, session = caso

        await session.run()  # antes da correção: a exceção da ferramenta sai daqui

        assert not aparelho.fechado
        assert aparelho.falas == [FERRAMENTA_QUEBROU, "Tudo certo."]
        assert aparelho.turnos[0][-1] == ("state", "idle")
        assert "error" not in [m["type"] for m in aparelho.recebido]

    async def test_o_historico_guarda_o_par_com_a_falha(self, caso):
        llm, aparelho, session = caso
        await session.run()

        papeis = [m.role for m in llm.historicos[1] if m.role != "system"]
        assert papeis == ["user", "assistant", "tool", "assistant", "user"]
        _, _, chamada, resultado, fala, _ = llm.historicos[1]
        assert resultado.tool_call_id == chamada.tool_calls[0]["id"]
        assert "falhou" in resultado.content
        assert fala.content == FERRAMENTA_QUEBROU

    async def test_o_traceback_vai_para_o_log(self, caso, caplog):
        llm, aparelho, session = caso
        with caplog.at_level("ERROR", logger="ideraldinho.session"):
            await session.run()

        assert any(r.exc_info for r in caplog.records), "sem traceback, o defeito some"

    async def test_nao_e_confundida_com_o_modelo_fora_do_ar(self, caso):
        llm, aparelho, session = caso
        await session.run()

        assert LLM_FORA_DO_AR not in aparelho.falas

    async def test_desconexao_na_espera_da_ferramenta_encerra_a_sessao(self, caplog):
        """Socket caindo não é ferramenta quebrando: a sessão acaba, sem frase."""
        aparelho = Aparelho(["marca cinco minutos"], lambda m: [])
        with caplog.at_level("INFO", logger="ideraldinho.session"):
            await sessao(LLM([_timer()]), aparelho).run()

        assert "device disconnected" in caplog.text
        assert aparelho.falas == []

    async def test_o_dispositivo_fala_uma_vez_so(self):
        aparelho = Aparelho(["pausa"])
        await sessao(LLM([_pausa()]), aparelho, spotify=SpotifyQueQuebra()).run()

        (falado,), machine = await ouvir(aparelho, 1)

        assert falado == [FERRAMENTA_QUEBROU]
        assert machine.state is State.IDLE


# --- itens 3 e 4: o `error` no meio do turno ----------------------------------


class TestErroNaEsperaDaFerramenta:
    """O gateway avisava o erro e seguia o turno; o dispositivo já tinha saído.

    O resto do turno ficava na fila do socket e era lido pelo turno seguinte:
    a resposta de uma pergunta falada como se fosse a da outra, dali em diante.
    """

    @staticmethod
    def _lixo_e_depois_o_resultado(m):
        return ["{isto nao e json", {"type": "tool_result", "id": m["id"], "ok": True,
                                     "value": "Timer marcado."}]

    def _rodar(self):
        aparelho = Aparelho(["marca cinco minutos", "que dia e hoje"],
                            self._lixo_e_depois_o_resultado)
        llm = LLM([_timer()], [Delta(text="Hoje e domingo.")])
        return llm, aparelho, sessao(llm, aparelho)

    async def test_o_turno_seguinte_fala_a_resposta_dele(self):
        llm, aparelho, session = self._rodar()
        await session.run()

        (primeiro, segundo), machine = await ouvir(aparelho, 2)

        # Antes da correção: primeiro == [] e segundo == ["Timer marcado."].
        assert segundo == ["Hoje e domingo."]
        assert primeiro == [ERRO_DO_GATEWAY]
        assert machine.state is State.IDLE

    async def test_o_error_e_a_ultima_mensagem_do_turno(self):
        """Depois do `error` o dispositivo não lê mais nada; então nada vai."""
        llm, aparelho, session = self._rodar()
        await session.run()

        assert [tipo for tipo, _ in aparelho.turnos[0]] == [
            "state", "transcript", "tool_call", "error",
        ]

    async def test_o_turno_abortado_nao_fica_no_historico(self):
        llm, aparelho, session = self._rodar()
        await session.run()

        assert conversa(llm, 1) == [("user", "que dia e hoje")]


class TestErroDoGatewayEFalado:
    """Num aparelho sem tela, um `error` só impresso é silêncio."""

    @staticmethod
    async def _turno(mensagens):
        class Cliente:
            async def receive(self):
                for m in mensagens:
                    yield m

        machine = StateMachine()
        machine.transition(State.LISTENING)
        alto_falante = AltoFalante()
        await handle_incoming(Cliente(), machine, Voz(), alto_falante, asyncio.Lock(), None)
        return alto_falante.falado, machine

    @pytest.mark.parametrize("antes", [[], [State.THINKING], [State.THINKING, State.SPEAKING]])
    async def test_fala_a_frase_fixa_uma_vez_e_volta_a_idle(self, antes):
        from common.messages import Error, StateMessage

        falado, machine = await self._turno(
            [StateMessage(value=s) for s in antes]
            + [Error(message="tool_result: unexpected field(s) ['x']")]
        )

        # A frase é fixa: o texto do erro é para o log, não para o alto-falante.
        assert falado == [ERRO_DO_GATEWAY]
        assert machine.state is State.IDLE

    async def test_frase_recusada_pelo_gateway_tambem_e_falada(self):
        """O `error` de fora do turno (frase malformada) cai no mesmo lugar."""
        aparelho = Aparelho([])
        aparelho._entrada.append('{"type": "utterance", "text": "oi", "campo_novo": 1}')
        await sessao(LLM(), aparelho).run()

        (falado,), machine = await ouvir(aparelho, 1)

        assert falado == [ERRO_DO_GATEWAY]

    def test_as_frases_de_falha_sao_distintas_e_sem_acento(self):
        frases = [SEM_RESPOSTA, FERRAMENTA_QUEBROU, LLM_FORA_DO_AR, ERRO_DO_GATEWAY]
        assert len(set(frases)) == len(frases)
        for frase in frases:
            assert frase.isascii(), frase


# --- item 5: uma retentativa antes de desistir do modelo ----------------------


class TestRetentativa:
    """Sem reserva, uma falha passageira do provedor custava o turno inteiro."""

    @pytest.fixture(autouse=True)
    def _sem_pausa(self, monkeypatch):
        monkeypatch.setattr("gateway.api.session.RETRY_PAUSA", 0)

    async def test_falha_rapida_antes_do_primeiro_delta_e_tentada_de_novo(self, caplog):
        aparelho = Aparelho(["oi"])
        llm = LLM([httpx.RemoteProtocolError("Server disconnected")], [Delta(text="Oi.")])
        with caplog.at_level("WARNING", logger="ideraldinho.session"):
            await sessao(llm, aparelho, llm_retries=1).run()

        assert aparelho.falas == ["Oi."]
        assert "RemoteProtocolError" in caplog.text
        # As duas tentativas viram o mesmo histórico: a pergunta, uma vez só.
        assert conversa(llm, 0) == conversa(llm, 1) == [("user", "oi")]

    async def test_e_uma_so_por_turno(self):
        aparelho = Aparelho(["oi"])
        llm = LLM([httpx.ConnectError("recusou")], [httpx.ConnectError("recusou")])
        await sessao(llm, aparelho, llm_retries=1).run()

        assert aparelho.falas == [LLM_FORA_DO_AR]
        assert len(llm.historicos) == 2

    async def test_nunca_depois_de_um_delta(self):
        """O que saiu pode ter sido falado; repetir emendaria duas respostas."""
        aparelho = Aparelho(["oi"])
        llm = LLM([Delta(text="A Lua fica longe."), httpx.ReadError("caiu")], [Delta(text="x")])
        await sessao(llm, aparelho, llm_retries=1).run()

        assert aparelho.falas == ["A Lua fica longe.", LLM_FORA_DO_AR]
        assert len(llm.historicos) == 1

    async def test_nao_executa_a_ferramenta_de_novo(self):
        """Só o pedido ao LLM se repete. A busca da primeira rodada já rodou."""

        class BuscaContada(Busca):
            vezes = 0

            async def buscar(self, consulta, quantos):
                BuscaContada.vezes += 1
                return []

        aparelho = Aparelho(["lua"])
        llm = LLM([_busca()], [RuntimeError("HTTP 503")], [Delta(text="Achei.")])
        await sessao(llm, aparelho, llm_retries=1, search=BuscaContada()).run()

        assert aparelho.falas == ["Achei."]
        assert BuscaContada.vezes == 1

    async def test_falha_demorada_nao_e_repetida(self, monkeypatch):
        """Quem levou o tempo todo para falhar levaria de novo: é silêncio em dobro."""
        monkeypatch.setattr("gateway.api.session.RETRY_JANELA", 0.0)
        aparelho = Aparelho(["oi"])
        llm = LLM([httpx.ReadTimeout("demorou")], [Delta(text="x")])
        await sessao(llm, aparelho, llm_retries=1).run()

        assert aparelho.falas == [LLM_FORA_DO_AR]
        assert len(llm.historicos) == 1

    async def test_recusa_do_provedor_nao_e_repetida(self):
        """4xx é chave errada, histórico recusado, cota: repetir dá o mesmo."""
        from gateway.llm.openai_compat import ErroHTTP

        aparelho = Aparelho(["oi"])
        llm = LLM([ErroHTTP(401, "HTTP 401 de x: chave invalida")], [Delta(text="x")])
        await sessao(llm, aparelho, llm_retries=1).run()

        assert aparelho.falas == [LLM_FORA_DO_AR]
        assert len(llm.historicos) == 1

    async def test_desligada_por_padrao_na_sessao_e_ligada_na_configuracao(self):
        from gateway.config import GatewayConfig

        aparelho = Aparelho(["oi"])
        llm = LLM([httpx.ConnectError("recusou")], [Delta(text="x")])
        await sessao(llm, aparelho).run()

        assert aparelho.falas == [LLM_FORA_DO_AR]
        assert GatewayConfig.llm_retries == 1
