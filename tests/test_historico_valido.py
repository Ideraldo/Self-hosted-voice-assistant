"""O histórico que o gateway manda precisa ser válido para qualquer API de LLM.

O Ollama é leniente: aceita resultado de ferramenta sem chamada, chamada sem
id, histórico começando no meio de um turno. As APIs de nuvem (o formato da
OpenAI e o da Anthropic) recusam tudo isso com um 400 -- e um 400 no meio de uma
conversa não é um turno ruim, é todos os turnos seguintes ruins, até o trecho
inválido sair do histórico.

Por isso o teste principal daqui não fixa um caso: ele sorteia conversas
inteiras e cobra a mesma coisa de todo histórico que chega ao provedor.
"""

from __future__ import annotations

import json
import random

import pytest

from gateway.api.session import MAX_TOOL_ROUNDS, Session
from gateway.conversation.history import Conversation
from gateway.llm.base import Delta, Message
from gateway.llm.ollama import _wire
from gateway.llm.openai_compat import _wire as _wire_openai
from gateway.tools.search import Resultado


def conferir(historico: list[Message]) -> None:
    """As regras que as APIs de nuvem impõem, numa lista de mensagens."""
    assert historico[0].role == "system"
    mensagens = historico[1:]
    if not mensagens:
        return
    papeis = [m.role for m in mensagens]
    assert papeis[0] == "user", f"historico comeca em {papeis[0]}: {papeis}"

    ids: list[str] = []
    for i, mensagem in enumerate(mensagens):
        anterior = mensagens[i - 1] if i else None
        if mensagem.role == "user":
            assert anterior is None or anterior.role != "user", f"dois user seguidos: {papeis}"
        if mensagem.role == "tool":
            assert mensagem.tool_call_id, f"resultado sem tool_call_id: {papeis}"
            assert anterior is not None and anterior.tool_calls, f"resultado orfao: {papeis}"
            assert mensagem.tool_call_id in [c.get("id") for c in anterior.tool_calls]
        for chamada in mensagem.tool_calls or []:
            assert mensagem.role == "assistant"
            assert chamada.get("id"), f"chamada sem id: {papeis}"
            ids.append(chamada["id"])
            # A volta também vale: chamada sem resultado é recusada igual.
            assert i + 1 < len(mensagens), f"chamada sem resultado: {papeis}"
            assert mensagens[i + 1].tool_call_id == chamada["id"]
    assert len(ids) == len(set(ids)), f"ids repetidos: {ids}"
    conferir_no_fio(historico)


def conferir_no_fio(historico: list[Message]) -> None:
    """O mesmo histórico, já serializado para o formato da OpenAI.

    As regras de cima valem para a lista de `Message`; o que a API recebe é o
    que `_wire` faz dela. Um id que se perde na serialização dá o mesmo 400.
    """
    fio = [_wire_openai(m) for m in historico]
    json.dumps(fio)  # tem que caber num corpo de pedido
    for anterior, mensagem in zip(fio, fio[1:]):
        if mensagem["role"] == "tool":
            assert mensagem["tool_call_id"] in [c["id"] for c in anterior.get("tool_calls", [])]
            assert isinstance(mensagem["content"], str)
    for mensagem in fio:
        chamadas = mensagem.get("tool_calls")
        # Sem chamada, o conteúdo é texto e não é vazio; `null` só com chamada.
        assert chamadas or (isinstance(mensagem["content"], str) and mensagem["content"])
        for chamada in chamadas or []:
            assert chamada["type"] == "function" and isinstance(chamada["id"], str)
            assert isinstance(json.loads(chamada["function"]["arguments"]), dict)


class LLMRoteirizado:
    """Cada chamada a `respond` consome o próximo passo do roteiro.

    Um passo é uma lista de `Delta`, com uma exceção no lugar em que o provedor
    cai. Todo histórico recebido é conferido na hora: o que importa não é só
    como a conversa termina, é o que o provedor vê a cada rodada.
    """

    def __init__(self, roteiro: list[list] | None = None) -> None:
        self.roteiro: list[list] = roteiro or []
        self.historicos: list[list[Message]] = []

    async def respond(self, history, tools):
        self.historicos.append(list(history))
        conferir(history)
        for item in self.roteiro.pop(0):
            if isinstance(item, Exception):
                raise item
            yield item


class Aparelho:
    """O outro lado do socket: manda frases e executa o que o gateway pedir."""

    #: O que o aparelho devolve fora do combinado, por ferramenta: lixo no
    #: lugar do JSON (o gateway encerra o turno com `error`) e um resultado sem
    #: o campo obrigatório `ok` (o `decode` levanta `TypeError`, que cai na
    #: rede das ferramentas que quebram).
    FORA_DO_COMBINADO = {
        "listar_agendamentos": lambda m: "{isto nao e json",
        "criar_alarme": lambda m: json.dumps({"type": "tool_result", "id": m["id"]}),
    }

    def __init__(self, frases: list[str], falha: bool = False) -> None:
        self._entrada = [
            json.dumps({"type": "session_start", "device_id": "d", "token": "t"})
        ] + [json.dumps({"type": "utterance", "text": f}) for f in frases]
        self._falha = falha

    async def accept(self) -> None:
        pass

    async def close(self, code: int = 1000) -> None:
        pass

    async def send_text(self, raw: str) -> None:
        mensagem = json.loads(raw)
        if mensagem["type"] == "tool_call" and mensagem["name"] in self.FORA_DO_COMBINADO:
            self._entrada.insert(0, self.FORA_DO_COMBINADO[mensagem["name"]](mensagem))
        elif mensagem["type"] == "tool_call":
            resultado = {"type": "tool_result", "id": mensagem["id"], "ok": not self._falha}
            if self._falha:
                resultado["error"] = "nao deu"
            else:
                resultado["value"] = "Timer de 5 minutos marcado."
            self._entrada.insert(0, json.dumps(resultado))

    async def receive(self) -> dict:
        if not self._entrada:
            return {"type": "websocket.disconnect", "code": 1000}
        return {"text": self._entrada.pop(0)}

    async def receive_text(self) -> str:
        return (await self.receive())["text"]


class Spotify:
    async def pausar(self) -> str:
        return "Pausado."

    async def proxima(self) -> str:
        # Nem `SpotifyError` nem erro HTTP: o que `executar_spotify` não pega.
        raise KeyError("item")


class Busca:
    nome = "fake"

    async def buscar(self, consulta, quantos):
        if consulta == "quebra":
            return [Resultado(titulo="t", trecho="x", url="https://exemplo.com/")]
        return []


class Leitor:
    """Só é chamado quando há resultado -- e aí quebra, depois da busca."""

    async def ler(self, url):
        raise RuntimeError("defeito no leitor")


def _timer(call_id: str | None = None) -> Delta:
    return Delta(tool_name="criar_timer", tool_args={"segundos": 300}, tool_call_id=call_id)


def _pausa(call_id: str | None = None) -> Delta:
    return Delta(tool_name="pausar_musica", tool_args={}, tool_call_id=call_id)


def _busca(call_id: str | None = None) -> Delta:
    return Delta(
        tool_name="buscar_na_internet", tool_args={"consulta": "lua"}, tool_call_id=call_id
    )


def _proxima(call_id: str | None = None) -> Delta:
    return Delta(tool_name="proxima_musica", tool_args={}, tool_call_id=call_id)


def _busca_que_quebra(call_id: str | None = None) -> Delta:
    return Delta(
        tool_name="buscar_na_internet", tool_args={"consulta": "quebra"}, tool_call_id=call_id
    )


def _listar(call_id: str | None = None) -> Delta:
    return Delta(tool_name="listar_agendamentos", tool_args={}, tool_call_id=call_id)


def _alarme(call_id: str | None = None) -> Delta:
    return Delta(tool_name="criar_alarme", tool_args={"hora": "07:00"}, tool_call_id=call_id)


def _caiu() -> Exception:
    return RuntimeError("provedor fora do ar")


#: Os tipos de turno. Cada um devolve os passos do LLM para um turno inteiro --
#: um passo por rodada -- recebendo o id que o provedor "forneceu" para a
#: chamada (None é o caso do Ollama, que não fornece).
TURNOS = {
    "simples": lambda i: [[Delta(text="Oi."), Delta(text=" Tudo bem?")]],
    "vazio": lambda i: [[]],
    "falha": lambda i: [[_caiu()]],
    "falha_no_meio": lambda i: [[Delta(text="Entao."), Delta(text=" Eu"), _caiu()]],
    "dispositivo": lambda i: [[_timer(i)]],
    "gateway": lambda i: [[_pausa(i)]],
    "busca": lambda i: [[_busca(i)], [Delta(text="A Lua fica longe.")]],
    "busca_duas_vezes": lambda i: [[_busca(i)], [_busca(i)], [Delta(text="Achei.")]],
    "busca_e_cai": lambda i: [[_busca(i)], [_caiu()]],
    "busca_e_silencio": lambda i: [[_busca(i)], []],
    "fala_e_busca": lambda i: [[Delta(text="Vou ver."), _busca(i)], [Delta(text="Pronto.")]],
    "estouro": lambda i: [[_busca(i)]] * (MAX_TOOL_ROUNDS + 1),
    "estouro_falando": lambda i: [[Delta(text="Um momento."), _busca(i)]]
    + [[_busca(i)]] * MAX_TOOL_ROUNDS,
    # As falhas de ferramenta. Em todas o turno acaba na primeira rodada: a que
    # quebra é fechada pela frase fixa, a que aborta é desfeita inteira.
    "gateway_quebra": lambda i: [[_proxima(i)]],
    "busca_quebra": lambda i: [[_busca_que_quebra(i)]],
    "busca_e_depois_quebra": lambda i: [[_busca(i)], [Delta(text="Achei."), _proxima(i)]],
    "dispositivo_quebra": lambda i: [[_alarme(i)]],
    "dispositivo_manda_lixo": lambda i: [[_listar(i)]],
    "busca_e_dispositivo_manda_lixo": lambda i: [[_busca(i)], [_listar(i)]],
}


def montar(llm, frases, max_turns=12, falha_no_aparelho=False) -> Session:
    session = Session(
        websocket=Aparelho(frases, falha=falha_no_aparelho),
        llm=llm,
        expected_token="t",
        spotify=Spotify(),
        search=Busca(),
        leitor=Leitor(),
    )
    session._conversation = Conversation(max_turns=max_turns)
    return session


@pytest.mark.parametrize("semente", range(40))
async def test_qualquer_conversa_deixa_um_historico_valido(semente):
    """A propriedade: não importa o que aconteceu, o histórico é aceitável.

    Sorteia o tipo de cada turno, o tamanho do histórico (para o corte agir em
    lugares diferentes) e de onde vem o id da chamada: de ninguém (Ollama), do
    provedor, ou do provedor repetindo sempre o mesmo -- há servidor compatível
    com a OpenAI que devolve `call_0` em toda resposta.
    """
    sorteio = random.Random(semente)
    max_turns = sorteio.choice([1, 2, 3, 5, 12])
    origem_do_id = sorteio.choice(["nenhum", "provedor", "repetido"])
    nomes = [sorteio.choice(sorted(TURNOS)) for _ in range(30)]

    llm = LLMRoteirizado()
    for n, nome in enumerate(nomes):
        call_id = {"nenhum": None, "provedor": f"toolu_{n}", "repetido": "call_0"}[origem_do_id]
        llm.roteiro += TURNOS[nome](call_id)

    session = montar(llm, nomes, max_turns, falha_no_aparelho=sorteio.random() < 0.3)
    await session.run()

    assert not llm.roteiro, "o roteiro nao foi consumido inteiro; o teste esta errado"
    conferir(session._conversation.prompt())
    # O orçamento continua valendo: cortar por turno não pode virar não cortar.
    # Só um turno sozinho passa do teto -- a pergunta, três buscas e a resposta
    # são oito mensagens.
    teto = max(max_turns * 2, 2 + 2 * MAX_TOOL_ROUNDS)
    assert len(session._conversation.prompt()) - 1 <= teto


@pytest.mark.parametrize("nome", sorted(TURNOS))
async def test_cada_tipo_de_turno_sozinho(nome):
    """O mesmo, um tipo por vez: quando a propriedade quebra, diz onde."""
    llm = LLMRoteirizado()
    for _ in range(4):
        llm.roteiro += TURNOS[nome](None)
    session = montar(llm, [nome] * 4, max_turns=2)
    await session.run()

    assert not llm.roteiro
    conferir(session._conversation.prompt())


async def test_estouro_de_rodadas_nao_deixa_resultado_sem_chamada():
    """Na quarta chamada o gateway desistia gravando só o `tool`, sem a chamada."""
    llm = LLMRoteirizado(TURNOS["estouro_falando"](None))
    session = montar(llm, ["pesquisa"])
    await session.run()

    mensagens = session._conversation.prompt()[1:]
    conferir(session._conversation.prompt())
    # As três buscas que rodaram ficam, fechadas pela frase que foi falada; a
    # quarta, que não rodou, não deixa rastro.
    assert [m.role for m in mensagens] == ["user"] + ["assistant", "tool"] * 3 + ["assistant"]
    assert mensagens[-1].content == "Um momento."


async def test_resposta_vazia_nao_deixa_pergunta_pendurada():
    llm = LLMRoteirizado([[], [Delta(text="Oi.")]])
    session = montar(llm, ["primeira", "segunda"])
    await session.run()

    segundo = [(m.role, m.content) for m in llm.historicos[1][1:]]
    assert segundo == [("user", "segunda")]


async def test_ferramenta_terminal_nao_e_turno_vazio():
    """O modelo não escreveu nada, mas o turno teve resposta: a da ferramenta.

    É o caso que não pode ser confundido com o de cima -- desfazer este turno
    apagaria justamente o par que a D22 manda guardar.
    """
    llm = LLMRoteirizado([[_timer()], [_pausa()]])
    session = montar(llm, ["marca cinco minutos", "pausa"])
    await session.run()

    mensagens = session._conversation.prompt()[1:]
    assert [m.role for m in mensagens] == ["user", "assistant", "tool", "assistant"] * 2
    assert mensagens[3].content == "Timer de 5 minutos marcado."
    assert mensagens[7].content == "Pausado."


async def test_id_do_provedor_e_o_que_vai_para_o_historico():
    llm = LLMRoteirizado([[_pausa("toolu_01A")]])
    session = montar(llm, ["pausa"])
    await session.run()

    _, chamada, resultado, _ = session._conversation.prompt()[1:]
    assert chamada.tool_calls[0]["id"] == "toolu_01A"
    assert resultado.tool_call_id == "toolu_01A"


async def test_sem_id_do_provedor_o_gateway_gera_um_que_toda_api_aceita():
    llm = LLMRoteirizado([[_pausa()], [_pausa()]])
    session = montar(llm, ["pausa", "pausa"])
    await session.run()

    ids = [m.tool_call_id for m in session._conversation.prompt() if m.role == "tool"]
    assert len(set(ids)) == 2
    for call_id in ids:
        # Anthropic: ^[a-zA-Z0-9_-]+$. OpenAI: até 40 caracteres.
        assert call_id.replace("_", "").isalnum() and len(call_id) <= 40


def test_o_ollama_continua_recebendo_o_que_recebia():
    """O formato no fio do Ollama é o que a D22 mediu (3/3). Não muda.

    O id é assunto do histórico e das APIs que casam por ele. O Ollama casa
    pela posição; mandar campo novo mudaria o prompt que o modelo vê sem
    ninguém ter medido o efeito.
    """
    conversation = Conversation()
    conversation.add_user("pausa")
    conversation.add_tool_call("pausar_musica", {"a": 1}, "call_1")
    conversation.add_tool_result("pausar_musica", "Pausado.", "call_1")

    assert [_wire(m) for m in conversation.prompt()[1:]] == [
        {"role": "user", "content": "pausa"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "pausar_musica", "arguments": {"a": 1}}}],
        },
        {"role": "tool", "content": "pausar_musica: Pausado."},
    ]
