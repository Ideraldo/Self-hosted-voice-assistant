"""O LLM fora do Ollama: o cliente do formato da OpenAI e a queda para o reserva.

Nada aqui toca a rede. O "provedor" é um `httpx.MockTransport` que devolve o
fluxo SSE que uma API de verdade devolveria e guarda o corpo de cada pedido --
porque metade do que importa neste cliente é o que ele **manda**: um histórico
que a API recusa não é um turno ruim, é todos os turnos seguintes ruins.

O que estes testes não provam é que cada provedor real se comporta como o
falso. Os formatos vêm da documentação e da memória de quem escreveu; a prova
é a bancada, com chave de verdade.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from gateway.config import LLMConfig
from gateway.conversation.history import Conversation
from gateway.llm.base import Delta, Tool
from gateway.llm.fallback import FallbackProvider
from gateway.llm.ollama import OllamaProvider
from gateway.llm.openai_compat import OpenAICompatProvider, _SemPensamento
from tests.test_historico_valido import Aparelho, Busca, conferir

CHAVE = "sk-segredo-que-nao-pode-vazar"

TIMER = Tool(
    name="criar_timer",
    description="Cria um timer.",
    parameters={"type": "object", "properties": {"segundos": {"type": "integer"}}},
)


def pedaco(delta: dict | None = None, fim: str | None = None, **resto) -> dict:
    """Um evento do fluxo, no formato `chat.completion.chunk`."""
    return {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": fim}], **resto}


def texto(conteudo) -> dict:
    return pedaco({"content": conteudo})


def chamada(index, argumentos="", call_id=None, nome=None, **resto) -> dict:
    """Um fragmento de chamada: só o primeiro de cada `index` traz id e nome."""
    funcao = {"arguments": argumentos} | ({"name": nome} if nome else {})
    corpo = {"index": index, "function": funcao, **resto}
    if call_id:
        corpo |= {"id": call_id, "type": "function"}
    return pedaco({"tool_calls": [corpo]})


def sse(*eventos: dict, done: bool = True) -> httpx.Response:
    linhas = [f"data: {json.dumps(e)}\n\n" for e in eventos]
    if done:
        linhas.append("data: [DONE]\n\n")
    return httpx.Response(
        200, content="".join(linhas).encode(), headers={"content-type": "text/event-stream"}
    )


class Servidor:
    """Uma API compatível de mentira: uma resposta do roteiro por pedido."""

    def __init__(self, *respostas: httpx.Response) -> None:
        self._respostas = list(respostas)
        self.corpos: list[dict] = []
        self.pedidos: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        self.corpos.append(json.loads(request.content))
        return self._respostas.pop(0)


def provedor(servidor, **opcoes) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        base_url="https://api.exemplo.com/v1/",
        model="modelo-x",
        api_key=CHAVE,
        transport=httpx.MockTransport(servidor),
        **opcoes,
    )


def conversa(*frases: str) -> Conversation:
    c = Conversation()
    for frase in frases:
        c.add_user(frase)
    return c


async def colher(fluxo) -> list[Delta]:
    return [d async for d in fluxo]


def falado(deltas: list[Delta]) -> str:
    return "".join(d.text or "" for d in deltas)


# --- o cliente ------------------------------------------------------------


async def test_texto_sai_pedaco_a_pedaco():
    servidor = Servidor(
        sse(pedaco({"role": "assistant", "content": ""}), texto("Bom"), texto(" dia."), pedaco(fim="stop"))
    )
    deltas = await colher(provedor(servidor).respond(conversa("oi").prompt(), []))

    # Um Delta por evento: é o que deixa a sessão falar a primeira frase
    # enquanto o modelo escreve a terceira.
    assert [d.text for d in deltas] == ["Bom", " dia."]


async def test_o_pedido_que_sai():
    servidor = Servidor(sse(texto("Oi."), pedaco(fim="stop")))
    await colher(provedor(servidor).respond(conversa("oi").prompt(), [TIMER]))

    pedido = servidor.pedidos[0]
    assert str(pedido.url) == "https://api.exemplo.com/v1/chat/completions"
    assert pedido.headers["authorization"] == f"Bearer {CHAVE}"
    corpo = servidor.corpos[0]
    assert corpo["model"] == "modelo-x"
    assert corpo["stream"] is True
    assert [m["role"] for m in corpo["messages"]] == ["system", "user"]
    assert corpo["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "criar_timer",
                "description": "Cria um timer.",
                "parameters": TIMER.parameters,
            },
        }
    ]
    # Nada do que é opcional vai sem ter sido pedido: provedor que não conhece
    # o campo devolve 400, e aí o turno inteiro cai por excesso de zelo.
    assert set(corpo) == {"model", "messages", "stream", "tools"}


async def test_sem_ferramenta_e_sem_chave_o_pedido_nao_inventa_campo():
    servidor = Servidor(sse(texto("Oi."), pedaco(fim="stop")))
    llm = OpenAICompatProvider(
        "http://localhost:11434/v1", "qwen3:8b", transport=httpx.MockTransport(servidor)
    )
    await colher(llm.respond(conversa("oi").prompt(), []))

    assert "authorization" not in servidor.pedidos[0].headers
    assert "tools" not in servidor.corpos[0]


async def test_opcoes_so_vao_quando_configuradas():
    servidor = Servidor(sse(texto("Oi."), pedaco(fim="stop")))
    llm = provedor(
        servidor,
        reasoning_effort="none",
        temperature=0.0,
        extra_body={"parallel_tool_calls": False},
    )
    await colher(llm.respond(conversa("oi").prompt(), []))

    corpo = servidor.corpos[0]
    assert corpo["reasoning_effort"] == "none"
    assert corpo["temperature"] == 0.0  # zero é valor, não ausência
    assert corpo["parallel_tool_calls"] is False


async def test_chamada_fragmentada_so_sai_inteira():
    servidor = Servidor(
        sse(
            chamada(0, "", call_id="call_abc", nome="criar_timer"),
            chamada(0, '{"segun'),
            chamada(0, 'dos": 3'),
            chamada(0, "00}"),
            pedaco(fim="tool_calls"),
        )
    )
    deltas = await colher(provedor(servidor).respond(conversa("timer").prompt(), [TIMER]))

    assert deltas == [
        Delta(tool_name="criar_timer", tool_args={"segundos": 300}, tool_call_id="call_abc")
    ]


async def test_chamada_inteira_num_pedaco_so_e_sem_index():
    """Há servidor compatível que não fragmenta, não numera e manda id vazio."""
    evento = pedaco(
        {"tool_calls": [{"id": "", "function": {"name": "criar_timer", "arguments": '{"segundos": 5}'}}]},
        fim="tool_calls",
    )
    deltas = await colher(provedor(Servidor(sse(evento))).respond(conversa("timer").prompt(), []))

    # Id vazio vira None: é o sinal para a sessão gerar um.
    assert deltas == [Delta(tool_name="criar_timer", tool_args={"segundos": 5}, tool_call_id=None)]


async def test_ferramenta_sem_argumento_nenhum():
    servidor = Servidor(sse(chamada(0, "", call_id="c1", nome="pausar_musica"), pedaco(fim="tool_calls")))
    deltas = await colher(provedor(servidor).respond(conversa("pausa").prompt(), []))

    assert deltas == [Delta(tool_name="pausar_musica", tool_args={}, tool_call_id="c1")]


async def test_texto_antes_da_chamada_e_falado_antes():
    servidor = Servidor(
        sse(
            texto("Vou ver."),
            chamada(0, '{"segundos": 60}', call_id="c1", nome="criar_timer"),
            pedaco(fim="tool_calls"),
        )
    )
    deltas = await colher(provedor(servidor).respond(conversa("timer").prompt(), []))

    assert [d.text or d.tool_name for d in deltas] == ["Vou ver.", "criar_timer"]


async def test_chamadas_paralelas_so_a_primeira_e_entregue(caplog):
    """A sessão executa uma ferramenta por rodada.

    Entregar as duas não adiantaria -- ela para na primeira -- e gravar duas
    chamadas com um resultado só é um histórico que a API recusa.
    """
    servidor = Servidor(
        sse(
            chamada(0, "", call_id="call_a", nome="criar_timer"),
            chamada(1, "", call_id="call_b", nome="pausar_musica"),
            chamada(0, '{"segundos": 60}'),
            chamada(1, "{}"),
            pedaco(fim="tool_calls"),
        )
    )
    with caplog.at_level("WARNING"):
        deltas = await colher(provedor(servidor).respond(conversa("x").prompt(), []))

    assert deltas == [Delta(tool_name="criar_timer", tool_args={"segundos": 60}, tool_call_id="call_a")]
    assert "pausar_musica" in caplog.text  # a que ficou para trás aparece no log


async def test_o_id_da_chamada_volta_igual_no_pedido_seguinte():
    """O par chamada/resultado, como a API precisa receber de volta."""
    servidor = Servidor(
        sse(chamada(0, '{"segundos": 300}', call_id="call_abc", nome="criar_timer"), pedaco(fim="tool_calls")),
        sse(texto("Marcado."), pedaco(fim="stop")),
    )
    llm = provedor(servidor)
    c = conversa("marca cinco minutos")

    (pedida,) = await colher(llm.respond(c.prompt(), [TIMER]))
    c.add_tool_call(pedida.tool_name, pedida.tool_args, pedida.tool_call_id)
    c.add_tool_result(pedida.tool_name, "Timer de 5 minutos marcado.", pedida.tool_call_id)
    await colher(llm.respond(c.prompt(), [TIMER]))

    assert servidor.corpos[1]["messages"][1:] == [
        {"role": "user", "content": "marca cinco minutos"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc",
                    "type": "function",
                    # Texto JSON, e não objeto: é o que o formato exige.
                    "function": {"name": "criar_timer", "arguments": '{"segundos": 300}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc",
            "content": "criar_timer: Timer de 5 minutos marcado.",
        },
    ]


async def test_o_que_o_provedor_pendura_na_chamada_volta_com_ela():
    """O Gemini manda a assinatura do raciocínio em `extra_content` e a cobra de volta."""
    assinatura = {"google": {"thought_signature": "opaco=="}}
    servidor = Servidor(
        sse(
            chamada(0, "{}", call_id="call_g", nome="pausar_musica", extra_content=assinatura),
            pedaco(fim="tool_calls"),
        ),
        sse(texto("Pausei."), pedaco(fim="stop")),
    )
    llm = provedor(servidor)
    c = conversa("pausa")
    (pedida,) = await colher(llm.respond(c.prompt(), []))
    c.add_tool_call(pedida.tool_name, pedida.tool_args, pedida.tool_call_id)
    c.add_tool_result(pedida.tool_name, "Pausado.", pedida.tool_call_id)
    await colher(llm.respond(c.prompt(), []))

    assert servidor.corpos[1]["messages"][2]["tool_calls"][0]["extra_content"] == assinatura


@pytest.mark.parametrize(
    "eventos",
    [
        # Em campo separado, com `content` nulo ou vazio enquanto pensa.
        [
            pedaco({"reasoning_content": "Okay, the user is asking", "content": None}),
            pedaco({"reasoning": "about the time.", "content": ""}),
            pedaco({"thinking": "Let me answer."}),
            texto("Sao sete horas."),
        ],
        # Dentro do conteúdo, entre marcas -- e com as marcas partidas.
        [texto("<thi"), texto("nk>Okay, the user"), texto(" is asking</th"), texto("ink>Sao sete horas.")],
        # Conteúdo em partes tipadas.
        [
            texto([{"type": "thinking", "thinking": [{"type": "text", "text": "Okay"}]}]),
            texto([{"type": "text", "text": "Sao sete horas."}]),
        ],
    ],
    ids=["campo_separado", "marcas_no_texto", "partes_tipadas"],
)
async def test_raciocinio_nunca_vira_fala(eventos):
    servidor = Servidor(sse(*eventos, pedaco(fim="stop")))
    deltas = await colher(provedor(servidor).respond(conversa("que horas sao?").prompt(), []))

    assert falado(deltas) == "Sao sete horas."


def test_sinal_de_menor_que_nao_abre_marca_continua_sendo_texto():
    filtro = _SemPensamento()
    assert filtro("2 <") + filtro(" 3 e a <t") + filtro("abela") + filtro.fim() == "2 < 3 e a <tabela"
    assert filtro("x <th") + filtro.fim() == "x <th"


async def test_eventos_sem_choices_e_comentarios_sao_ignorados():
    corpo = (
        ": keep-alive\n\n"
        'data: {"choices": []}\n\n'
        f"data: {json.dumps(texto('Oi.'))}\n\n"
        f"data:{json.dumps(pedaco(fim='stop'))}\n\n"
        'data: {"choices": [], "usage": {"total_tokens": 9}}\n\n'
        "data: [DONE]\n\n"
    )
    servidor = Servidor(httpx.Response(200, content=corpo.encode()))
    deltas = await colher(provedor(servidor).respond(conversa("oi").prompt(), []))

    assert falado(deltas) == "Oi."


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_erro_http_levanta_com_status_e_trecho_do_corpo(status):
    corpo = {"error": {"message": f"Incorrect API key provided: {CHAVE}. Veja a doc."}}
    servidor = Servidor(httpx.Response(status, json=corpo))

    with pytest.raises(RuntimeError) as erro:
        await colher(provedor(servidor).respond(conversa("oi").prompt(), []))

    mensagem = str(erro.value)
    assert str(status) in mensagem and "Incorrect API key" in mensagem
    # Há API que ecoa a chave no erro, e o erro vai para o log.
    assert CHAVE not in mensagem and CHAVE not in repr(erro.value)


async def test_erro_dentro_do_fluxo_levanta():
    servidor = Servidor(sse(texto("Entao"), {"error": {"message": "overloaded", "code": 503}}))

    with pytest.raises(RuntimeError, match="overloaded"):
        await colher(provedor(servidor).respond(conversa("oi").prompt(), []))


async def test_conexao_cortada_no_meio_levanta_depois_do_que_ja_saiu():
    async def corpo():
        yield f"data: {json.dumps(texto('A Lua fica'))}\n\n".encode()
        raise httpx.ReadError("conexao caiu")

    llm = provedor(Servidor(httpx.Response(200, content=corpo())))
    recebidos = []
    with pytest.raises(httpx.ReadError):
        async for delta in llm.respond(conversa("lua").prompt(), []):
            recebidos.append(delta.text)

    assert recebidos == ["A Lua fica"]


async def test_fluxo_que_acaba_sem_avisar_e_corte_e_nao_resposta():
    """Um proxy que fecha a conexão com educação no meio de uma chamada.

    Sem `finish_reason` nem `[DONE]` não dá para saber se os argumentos estão
    completos -- e `{"segundos": 3` cortado de `{"segundos": 300}` é JSON
    inválido por sorte, não por garantia.
    """
    servidor = Servidor(
        sse(chamada(0, '{"segundos": 3', call_id="c1", nome="criar_timer"), done=False)
    )

    with pytest.raises(RuntimeError, match="antes do fim"):
        await colher(provedor(servidor).respond(conversa("timer").prompt(), []))


@pytest.mark.parametrize("argumentos", ['{"segundos": ', "[1, 2]", "trezentos"])
async def test_argumentos_malformados_levantam_em_vez_de_executar(argumentos):
    servidor = Servidor(
        sse(chamada(0, argumentos, call_id="c1", nome="criar_timer"), pedaco(fim="tool_calls"))
    )

    with pytest.raises(ValueError, match="criar_timer"):
        await colher(provedor(servidor).respond(conversa("timer").prompt(), []))


# --- a queda para o reserva ------------------------------------------------


class Roteiro:
    """Um provedor que segue um roteiro por chamada: Deltas, exceções, esperas."""

    def __init__(self, *chamadas: list) -> None:
        self._chamadas = list(chamadas)
        self.vezes = 0

    async def respond(self, history, tools):
        self.vezes += 1
        for item in self._chamadas.pop(0):
            if isinstance(item, Exception):
                raise item
            if isinstance(item, float):
                await asyncio.sleep(item)
                continue
            yield item


def fila(principal, reserva, **opcoes) -> FallbackProvider:
    return FallbackProvider([("principal", principal), ("reserva", reserva)], **opcoes)


async def test_principal_fora_do_ar_o_reserva_responde(caplog):
    principal = Roteiro([httpx.ConnectError("recusou")])
    reserva = Roteiro([Delta(text="Oi."), Delta(text=" Tudo bem?")])

    with caplog.at_level("WARNING"):
        deltas = await colher(fila(principal, reserva).respond([], []))

    assert falado(deltas) == "Oi. Tudo bem?"
    assert "principal" in caplog.text and "recusou" in caplog.text


async def test_principal_que_demora_a_comecar_perde_a_vez():
    principal = Roteiro([30.0, Delta(text="tarde demais")])
    reserva = Roteiro([Delta(text="Oi.")])

    deltas = await colher(fila(principal, reserva, primeiro_delta=0.01).respond([], []))

    assert falado(deltas) == "Oi."


async def test_o_ultimo_da_fila_nao_tem_pressa():
    """Um Ollama de casa carregando o modelo demora, e não há para quem passar."""
    principal = Roteiro([RuntimeError("HTTP 500")])
    reserva = Roteiro([0.05, Delta(text="Oi.")])

    deltas = await colher(fila(principal, reserva, primeiro_delta=0.01).respond([], []))

    assert falado(deltas) == "Oi."


async def test_falha_depois_de_comecar_a_responder_sobe():
    """O que já saiu pode já ter sido falado: trocar de modelo agora emendaria
    duas respostas. A sessão sabe encerrar esse turno."""
    principal = Roteiro([Delta(text="A Lua fica"), httpx.ReadError("caiu")])
    reserva = Roteiro([Delta(text="outra resposta")])

    recebidos = []
    with pytest.raises(httpx.ReadError):
        async for delta in fila(principal, reserva).respond([], []):
            recebidos.append(delta.text)

    assert recebidos == ["A Lua fica"]
    assert reserva.vezes == 0


async def test_todos_fora_do_ar_levanta_dizendo_quem_e_por_que():
    principal = Roteiro([RuntimeError("HTTP 429")])
    reserva = Roteiro([httpx.ConnectError("tunel caido")])

    with pytest.raises(RuntimeError) as erro:
        await colher(fila(principal, reserva).respond([], []))

    assert "HTTP 429" in str(erro.value) and "tunel caido" in str(erro.value)


async def test_resposta_vazia_nao_e_queda():
    principal, reserva = Roteiro([]), Roteiro([Delta(text="nao era para chegar aqui")])

    assert await colher(fila(principal, reserva).respond([], [])) == []
    assert reserva.vezes == 0


async def test_quem_caiu_descansa_e_depois_volta():
    """Sem o descanso, principal fora do ar custaria a espera inteira por turno."""
    principal = Roteiro([RuntimeError("caiu")], [Delta(text="voltei")])
    reserva = Roteiro([Delta(text="um")], [Delta(text="dois")])
    llm = fila(principal, reserva, descanso=0.05)

    assert falado(await colher(llm.respond([], []))) == "um"
    assert falado(await colher(llm.respond([], []))) == "dois"
    assert principal.vezes == 1, "o principal foi tentado de novo antes do descanso acabar"

    await asyncio.sleep(0.06)
    assert falado(await colher(llm.respond([], []))) == "voltei"


async def test_todos_descansando_tenta_todos_mesmo_assim():
    principal = Roteiro([RuntimeError("a")], [Delta(text="voltei")])
    reserva = Roteiro([RuntimeError("b")])
    llm = fila(principal, reserva, descanso=60)

    with pytest.raises(RuntimeError):
        await colher(llm.respond([], []))
    assert falado(await colher(llm.respond([], []))) == "voltei"


# --- a montagem ------------------------------------------------------------


def _config(**campos) -> LLMConfig:
    padrao = dict(
        provider="ollama",
        model="qwen3:8b",
        base_url=None,
        api_key=None,
        reasoning_effort=None,
        temperature=None,
        extra_body={},
    )
    return LLMConfig(**(padrao | campos))


def test_o_padrao_continua_sendo_o_ollama_local(monkeypatch):
    from gateway import config as modulo
    from gateway.main import build_llm, descrever_llm

    for nome in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API_KEY"):
        monkeypatch.delenv(nome, raising=False)
    limpo = modulo._llm("LLM_")

    assert limpo == _config()
    assert isinstance(build_llm(limpo, None), OllamaProvider)
    assert descrever_llm(limpo) == "ollama:qwen3:8b"  # o que o /health sempre disse


def test_nuvem_com_o_ollama_de_casa_de_reserva(monkeypatch):
    from gateway import config as modulo
    from gateway.main import build_llm, descrever_llm

    ambiente = {
        "LLM_PROVIDER": "openai-compat",
        "LLM_BASE_URL": "https://usuario:senha@api.exemplo.com/v1?key=" + CHAVE,
        "LLM_API_KEY": CHAVE,
        "LLM_MODEL": "modelo-x",
        "LLM_REASONING_EFFORT": "none",
        "LLM_TEMPERATURE": "0.3",
        "LLM_EXTRA_BODY": '{"parallel_tool_calls": false}',
        "LLM_FALLBACK_PROVIDER": "ollama",
        "LLM_FALLBACK_BASE_URL": "http://100.64.0.2:11434",
    }
    for nome, valor in ambiente.items():
        monkeypatch.setenv(nome, valor)
    principal, reserva = modulo._llm("LLM_"), modulo._llm("LLM_FALLBACK_")

    assert principal.temperature == 0.3
    assert principal.extra_body == {"parallel_tool_calls": False}
    # O reserva não herda nada do principal: chave de um não serve ao outro.
    assert reserva == _config(base_url="http://100.64.0.2:11434")

    llm = build_llm(principal, reserva)
    assert isinstance(llm, FallbackProvider)
    primeiro, segundo = (p for _, p in llm._provedores)
    assert isinstance(primeiro, OpenAICompatProvider) and isinstance(segundo, OllamaProvider)
    assert segundo._url == "http://100.64.0.2:11434/api/chat"

    # O que vai para o /health e para o log diz com quem se fala, e só isso.
    descricao = descrever_llm(principal)
    assert descricao == "openai-compat:modelo-x@api.exemplo.com"
    assert CHAVE not in descricao and "senha" not in descricao


def test_nuvem_sem_endereco_falha_na_subida_e_nao_no_primeiro_turno():
    from gateway.main import build_llm

    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        build_llm(_config(provider="openai-compat"), None)
    with pytest.raises(ValueError, match="unknown"):
        build_llm(_config(provider="anthropic"), None)


# --- de ponta a ponta, pela sessão ------------------------------------------


class AparelhoQueOuve(Aparelho):
    """O aparelho de `test_historico_valido`, guardando o que o gateway mandou."""

    def __init__(self, frases: list[str]) -> None:
        super().__init__(frases)
        self.recebido: list[dict] = []
        self.fechado = False

    async def send_text(self, raw: str) -> None:
        self.recebido.append(json.loads(raw))
        await super().send_text(raw)

    async def close(self, code: int = 1000) -> None:
        self.fechado = True

    def falas(self) -> list[str]:
        return [
            m["text"]
            for m in self.recebido
            if m["type"] == "transcript" and m["role"] == "assistant" and not m["final"]
        ]


def sessao(llm, frases):
    from gateway.api.session import Session

    aparelho = AparelhoQueOuve(frases)
    return Session(websocket=aparelho, llm=llm, expected_token="t", search=Busca()), aparelho


async def test_dois_turnos_inteiros_pela_sessao_com_o_cliente_de_nuvem():
    """Ferramenta do dispositivo no primeiro turno, busca e resposta no segundo.

    São três pedidos à API. O último carrega a conversa inteira, e é ele que
    uma API de verdade recusaria se um id não batesse.
    """
    servidor = Servidor(
        sse(
            chamada(0, "", call_id="call_timer", nome="criar_timer"),
            chamada(0, '{"segundos"'),
            chamada(0, ": 300}"),
            pedaco(fim="tool_calls"),
        ),
        sse(
            chamada(0, '{"consulta": "distancia da lua"}', call_id="call_busca", nome="buscar_na_internet"),
            pedaco(fim="tool_calls"),
        ),
        sse(
            pedaco({"reasoning_content": "The user wants the distance."}),
            texto("A Lua fica a 384"),
            texto(".400 quilometros."),
            pedaco(fim="stop"),
        ),
    )
    session, aparelho = sessao(provedor(servidor), ["marca cinco minutos", "qual a distancia da lua?"])
    await session.run()

    # O aparelho executou o timer e falou as duas respostas -- e nada do rascunho.
    pedidos_ao_aparelho = [m for m in aparelho.recebido if m["type"] == "tool_call"]
    assert [(m["name"], m["args"]) for m in pedidos_ao_aparelho] == [("criar_timer", {"segundos": 300})]
    assert aparelho.falas() == ["Timer de 5 minutos marcado.", "A Lua fica a 384.400 quilometros."]

    assert len(servidor.corpos) == 3
    mensagens = servidor.corpos[2]["messages"][1:]
    resultado_da_busca = mensagens[6].pop("content")
    assert resultado_da_busca.startswith("buscar_na_internet: ")
    assert mensagens == [
        {"role": "user", "content": "marca cinco minutos"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_timer",
                    "type": "function",
                    "function": {"name": "criar_timer", "arguments": '{"segundos": 300}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_timer",
            "content": "criar_timer: Timer de 5 minutos marcado.",
        },
        {"role": "assistant", "content": "Timer de 5 minutos marcado."},
        {"role": "user", "content": "qual a distancia da lua?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_busca",
                    "type": "function",
                    "function": {
                        "name": "buscar_na_internet",
                        "arguments": '{"consulta": "distancia da lua"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_busca"},
    ]
    conferir(session._conversation.prompt())


async def test_com_a_nuvem_fora_do_ar_o_reserva_responde_sem_derrubar_a_sessao():
    """O critério de pronto do A2: principal derrubado, turno respondido, socket de pé."""
    from gateway.api.session import LLM_FORA_DO_AR

    servidor = Servidor(httpx.Response(503, text="upstream connect error"))
    reserva = Roteiro([Delta(text="Oi, tudo certo.")], [Delta(text="De nada.")])
    llm = FallbackProvider([("nuvem", provedor(servidor)), ("casa", reserva)])
    session, aparelho = sessao(llm, ["oi", "valeu"])
    await session.run()

    assert aparelho.falas() == ["Oi, tudo certo.", "De nada."]
    assert LLM_FORA_DO_AR not in aparelho.falas()
    assert not aparelho.fechado
    # O segundo turno nem bateu na nuvem: ela está descansando.
    assert len(servidor.corpos) == 1
