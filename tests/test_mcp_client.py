"""O cliente MCP e a autorização, sem rede.

O servidor falso abaixo é inventado: ferramentas `somar` e `anotar`, endereços
em example.com. O que se testa é o contrato do protocolo e, acima de tudo, o
que acontece quando algo falha no meio de uma escrita.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import stat
import sys
import urllib.parse

import httpx
import pytest

from gateway.tools import financas_auth as auth
from gateway.tools import mcp_client
from gateway.tools.mcp_client import (
    McpClient,
    McpErroServidor,
    McpNaoAutorizado,
    McpNaoEnviado,
    McpRespostaInvalida,
    McpResultadoDesconhecido,
    TokenStore,
    build_mcp_client,
    salvar_token,
)

MCP_URL = "https://app.example.com/mcp"
TOKEN_URL = "https://auth.example.com/token"

FERRAMENTAS = [
    {"name": "somar", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "anotar", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}},
]


class ServidorFalso:
    """Um servidor MCP sem estado e o servidor de autorização dele, num só handler."""

    def __init__(self) -> None:
        self.sse = False
        self.sessao: str | None = None
        self.tokens_validos = {"acesso-1"}
        self.refresh_valido = "refresh-1"
        self.renovacoes = 0
        self.chamadas: list[dict] = []  # corpos JSON-RPC recebidos com token válido
        self.cabecalhos: list[httpx.Headers] = []
        self.no_call = None  # função(request, corpo) -> Response | None, para sabotar
        self.paginas: list[list[dict]] = [FERRAMENTAS]

    # -- contagens ---------------------------------------------------------

    def quantas(self, metodo: str) -> int:
        return sum(1 for c in self.chamadas if c.get("method") == metodo)

    # -- handler -----------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return self._token(request)
        assert str(request.url) == MCP_URL
        if (
            request.headers.get("authorization", "").removeprefix("Bearer ")
            not in self.tokens_validos
        ):
            return httpx.Response(
                401,
                json={"error": "invalid_token"},
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        # O servidor de verdade recusa quem não aceita os dois formatos.
        aceita = request.headers.get("accept", "")
        if "application/json" not in aceita or "text/event-stream" not in aceita:
            return httpx.Response(
                406,
                json={
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": "Not Acceptable"},
                    "id": None,
                },
            )

        corpo = json.loads(request.content)
        self.chamadas.append(corpo)
        self.cabecalhos.append(request.headers)
        metodo = corpo["method"]

        if "id" not in corpo:
            return httpx.Response(202)
        if metodo == "initialize":
            resultado = {
                "protocolVersion": corpo["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "falso", "version": "0"},
                "instructions": "use com cuidado",
            }
            extras = {"Mcp-Session-Id": self.sessao} if self.sessao else {}
            return self._responder(corpo, resultado, extras)
        if metodo == "tools/list":
            cursor = corpo.get("params", {}).get("cursor")
            i = int(cursor) if cursor else 0
            resultado = {"tools": self.paginas[i]}
            if i + 1 < len(self.paginas):
                resultado["nextCursor"] = str(i + 1)
            return self._responder(corpo, resultado)
        if metodo == "tools/call":
            if self.no_call is not None:
                sabotagem = self.no_call(request, corpo)
                if sabotagem is not None:
                    return sabotagem
            nome = corpo["params"]["name"]
            args = corpo["params"]["arguments"]
            if nome == "somar":
                total = args["a"] + args["b"]
                return self._responder(
                    corpo,
                    {
                        "content": [{"type": "text", "text": f"deu {total}"}],
                        "structuredContent": {"total": total},
                    },
                )
            if nome == "anotar":
                return self._responder(
                    corpo,
                    {
                        "content": [{"type": "text", "text": "anotado"}],
                        "structuredContent": {"id": "n1"},
                    },
                )
            if nome == "recusar":
                return self._responder(
                    corpo, {"content": [{"type": "text", "text": "nao pode"}], "isError": True}
                )
            return self._erro(corpo, -32602, f"Tool {nome} not found")
        return self._erro(corpo, -32601, "Method not found")

    def _responder(
        self, corpo: dict, resultado: dict, extras: dict | None = None
    ) -> httpx.Response:
        mensagem = {"jsonrpc": "2.0", "id": corpo["id"], "result": resultado}
        if not self.sse:
            return httpx.Response(200, json=mensagem, headers=extras or {})
        # Como um servidor de verdade escreve: comentário de keep-alive, um
        # evento vazio, uma notificação no meio, e só então a resposta.
        notificacao = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "info"},
        }
        texto = (
            ": keepalive\n\n"
            "id: 1\ndata: \n\n"
            f"event: message\ndata: {json.dumps(notificacao)}\n\n"
            f"event: message\nid: 2\ndata: {json.dumps(mensagem)}\n\n"
        )
        return httpx.Response(
            200,
            content=texto.encode(),
            headers={"Content-Type": "text/event-stream", **(extras or {})},
        )

    @staticmethod
    def _erro(corpo: dict, codigo: int, mensagem: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": corpo["id"],
                "error": {"code": codigo, "message": mensagem},
            },
        )

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.renovacoes += 1
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        assert form["grant_type"] == "refresh_token"
        assert form["client_id"] == "cliente-1"
        if form["refresh_token"] != self.refresh_valido:
            return httpx.Response(400, json={"error": "invalid_grant"})
        # O refresh token gira: o antigo morre aqui.
        n = self.renovacoes + 1
        self.refresh_valido = f"refresh-{n}"
        self.tokens_validos = {f"acesso-{n}"}
        return httpx.Response(
            200,
            json={
                "access_token": f"acesso-{n}",
                "refresh_token": f"refresh-{n}",
                "expires_in": 3600,
            },
        )


def _arquivo(tmp_path, **mudancas) -> dict:
    dados = {
        "access_token": "acesso-1",
        "refresh_token": "refresh-1",
        "expires_at": 0,
        "token_endpoint": TOKEN_URL,
        "client_id": "cliente-1",
        "resource": MCP_URL,
        "scope": "exemplo",
    }
    dados.update(mudancas)
    salvar_token(tmp_path / "token.json", dados)
    return dados


@pytest.fixture
def servidor() -> ServidorFalso:
    return ServidorFalso()


@pytest.fixture
def cliente(servidor, tmp_path) -> McpClient:
    _arquivo(tmp_path)
    transporte = httpx.MockTransport(servidor.handler)
    tokens = TokenStore(tmp_path / "token.json", transport=transporte)
    return McpClient(MCP_URL, tokens, transport=transporte)


def _no_disco(tmp_path) -> dict:
    return json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))


# -- o caminho feliz, em JSON e em SSE ----------------------------------------


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
async def test_initialize_list_e_call(servidor, cliente, sse):
    servidor.sse = sse

    info = await cliente.initialize()
    assert info["serverInfo"]["name"] == "falso"
    assert info["instructions"] == "use com cuidado"

    ferramentas = await cliente.list_tools()
    assert [f["name"] for f in ferramentas] == ["somar", "anotar"]
    assert ferramentas[1]["annotations"]["readOnlyHint"] is False

    r = await cliente.call_tool("somar", {"a": 2, "b": 3})
    assert r.texto == "deu 5"
    assert r.estruturado == {"total": 5}
    assert r.erro is False

    metodos = [c["method"] for c in servidor.chamadas]
    assert metodos == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    # ids JSON-RPC distintos, e nenhum na notificação
    ids = [c.get("id") for c in servidor.chamadas]
    assert ids[1] is None and len({ids[0], ids[2], ids[3]}) == 3


async def test_cabecalhos_do_protocolo(servidor, cliente):
    await cliente.call_tool("somar", {"a": 1, "b": 1})
    init, _, call = servidor.cabecalhos
    assert "application/json" in call["accept"] and "text/event-stream" in call["accept"]
    assert call["content-type"] == "application/json"
    # A versão vai no corpo do initialize e, daí em diante, no cabeçalho.
    assert "mcp-protocol-version" not in init
    assert call["mcp-protocol-version"] == mcp_client.VERSAO_PROTOCOLO
    # Servidor sem estado não emitiu sessão; nenhuma é inventada.
    assert "mcp-session-id" not in call


async def test_sessao_emitida_e_devolvida(servidor, cliente):
    servidor.sessao = "sessao-abc"
    await cliente.list_tools()
    assert "mcp-session-id" not in servidor.cabecalhos[0]
    assert servidor.cabecalhos[-1]["mcp-session-id"] == "sessao-abc"


async def test_call_inicializa_sozinho_e_so_uma_vez(servidor, cliente):
    await cliente.call_tool("somar", {"a": 1, "b": 1})
    await cliente.call_tool("somar", {"a": 1, "b": 1})
    assert servidor.quantas("initialize") == 1


async def test_tools_list_segue_o_cursor(servidor, cliente):
    servidor.paginas = [[FERRAMENTAS[0]], [FERRAMENTAS[1]]]
    assert [f["name"] for f in await cliente.list_tools()] == ["somar", "anotar"]
    assert servidor.quantas("tools/list") == 2


async def test_is_error_e_resultado_e_nao_excecao(servidor, cliente):
    r = await cliente.call_tool("recusar")
    assert r.erro is True
    assert r.texto == "nao pode"
    assert r.estruturado is None


async def test_versao_desconhecida_e_recusada(servidor, cliente):
    original = servidor._responder

    def responder(corpo, resultado, extras=None):
        if "protocolVersion" in resultado:
            resultado = {**resultado, "protocolVersion": "2099-01-01"}
        return original(corpo, resultado, extras)

    servidor._responder = responder
    with pytest.raises(McpErroServidor):
        await cliente.initialize()


# -- erros do servidor -------------------------------------------------------


async def test_erro_json_rpc(servidor, cliente):
    with pytest.raises(McpErroServidor) as e:
        await cliente.call_tool("inexistente")
    assert e.value.codigo == -32602
    assert "inexistente" in str(e.value)
    # Parâmetro inválido pode vir de depois da execução: não se garante nada.
    assert e.value.nao_executou is False
    assert servidor.quantas("tools/call") == 1


async def test_recusa_http_4xx_nao_executou(servidor, cliente):
    await cliente.initialize()
    servidor.no_call = lambda req, corpo: httpx.Response(
        429, json={"jsonrpc": "2.0", "error": {"code": -32000, "message": "devagar"}, "id": None}
    )
    with pytest.raises(McpErroServidor) as e:
        await cliente.call_tool("anotar", {"texto": "x"})
    assert e.value.status == 429 and e.value.nao_executou is True
    assert "devagar" in str(e.value)


async def test_http_5xx_numa_escrita_e_desconhecido(servidor, cliente):
    await cliente.initialize()
    servidor.no_call = lambda req, corpo: httpx.Response(502, text="bad gateway")
    with pytest.raises(McpResultadoDesconhecido):
        await cliente.call_tool("anotar", {"texto": "x"})
    assert servidor.quantas("tools/call") == 1


# -- o que não pode acontecer com uma escrita ----------------------------------


async def test_timeout_depois_de_enviar_e_desconhecido_e_nao_repete(servidor, cliente):
    await cliente.initialize()

    def cai(request, corpo):
        raise httpx.ReadTimeout("sem resposta", request=request)

    servidor.no_call = cai
    with pytest.raises(McpResultadoDesconhecido) as e:
        await cliente.call_tool("anotar", {"texto": "x"})
    assert not isinstance(e.value, McpNaoEnviado)
    assert servidor.quantas("tools/call") == 1  # nenhuma segunda tentativa


async def test_conexao_cortada_no_meio_tambem_e_desconhecido(servidor, cliente):
    await cliente.initialize()

    def cai(request, corpo):
        raise httpx.RemoteProtocolError("servidor desligou", request=request)

    servidor.no_call = cai
    with pytest.raises(McpResultadoDesconhecido):
        await cliente.call_tool("anotar", {"texto": "x"})
    assert servidor.quantas("tools/call") == 1


async def test_falha_ao_conectar_e_nao_enviado_e_nao_repete(servidor, tmp_path):
    _arquivo(tmp_path)
    tentativas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tentativas
        corpo = json.loads(request.content)
        if corpo.get("method") == "tools/call":
            tentativas += 1
            raise httpx.ConnectError("recusada", request=request)
        return servidor.handler(request)

    transporte = httpx.MockTransport(handler)
    cliente = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    with pytest.raises(McpNaoEnviado):
        await cliente.call_tool("anotar", {"texto": "x"})
    # Seria seguro repetir, mas quem decide é quem chamou.
    assert tentativas == 1


async def test_leitura_de_descoberta_repete_uma_vez(servidor, tmp_path):
    _arquivo(tmp_path)
    falhas = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal falhas
        if json.loads(request.content).get("method") == "tools/list" and falhas == 0:
            falhas += 1
            raise httpx.ReadTimeout("lento", request=request)
        return servidor.handler(request)

    transporte = httpx.MockTransport(handler)
    cliente = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    assert len(await cliente.list_tools()) == 2
    assert falhas == 1


# -- SSE mal formado ------------------------------------------------------------


def _sse(texto: str) -> httpx.Response:
    return httpx.Response(
        200, content=texto.encode(), headers={"Content-Type": "text/event-stream"}
    )


async def test_sse_com_json_quebrado(servidor, cliente):
    await cliente.initialize()
    servidor.no_call = lambda req, corpo: _sse('event: message\ndata: {"jsonrpc": "2.0", "id":\n\n')
    with pytest.raises(McpRespostaInvalida):
        await cliente.call_tool("anotar", {"texto": "x"})
    # Resposta ilegível a um pedido que chegou: é "não sei se gravou".
    assert issubclass(McpRespostaInvalida, McpResultadoDesconhecido)
    assert servidor.quantas("tools/call") == 1


async def test_sse_que_termina_sem_a_resposta(servidor, cliente):
    await cliente.initialize()
    outro = {"jsonrpc": "2.0", "id": 999, "result": {}}
    servidor.no_call = lambda req, corpo: _sse(f": oi\n\ndata: {json.dumps(outro)}\n\n")
    with pytest.raises(McpRespostaInvalida):
        await cliente.call_tool("anotar", {"texto": "x"})


async def test_sse_com_data_em_varias_linhas_e_sem_linha_final(servidor, cliente):
    await cliente.initialize()

    def partido(request, corpo):
        resultado = {"content": [{"type": "text", "text": "ok"}]}
        return _sse(
            'data: {"jsonrpc": "2.0",\r\n'
            f'data: "id": {corpo["id"]},\r\n'
            f'data: "result": {json.dumps(resultado)}}}'  # sem a linha em branco final
        )

    servidor.no_call = partido
    assert (await cliente.call_tool("anotar")).texto == "ok"


async def test_tipo_de_resposta_inesperado(servidor, cliente):
    await cliente.initialize()
    servidor.no_call = lambda req, corpo: httpx.Response(
        200, text="<html>", headers={"Content-Type": "text/html"}
    )
    with pytest.raises(McpRespostaInvalida):
        await cliente.call_tool("anotar")


# -- token: 401, renovação, rotação ------------------------------------------


async def test_401_renova_e_repete(servidor, cliente, tmp_path):
    servidor.tokens_validos = {"outro"}  # o token em disco já não vale
    r = await cliente.call_tool("anotar", {"texto": "x"})
    assert r.estruturado == {"id": "n1"}
    assert servidor.renovacoes == 1
    # Só a tentativa com token válido chegou a executar.
    assert servidor.quantas("tools/call") == 1

    disco = _no_disco(tmp_path)
    assert disco["access_token"] == "acesso-2"
    assert disco["refresh_token"] == "refresh-2"  # o que girou foi gravado
    assert disco["expires_at"] > 0
    # O resto do arquivo sobrevive à renovação.
    assert disco["client_id"] == "cliente-1" and disco["scope"] == "exemplo"


async def test_token_perto_de_vencer_renova_antes_de_chamar(servidor, tmp_path):
    _arquivo(tmp_path, expires_at=1)  # venceu em 1970
    transporte = httpx.MockTransport(servidor.handler)
    cliente = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    await cliente.initialize()
    assert servidor.renovacoes == 1
    assert servidor.cabecalhos[0]["authorization"] == "Bearer acesso-2"


async def test_renovacao_recusada_pede_nova_autorizacao(servidor, cliente):
    servidor.tokens_validos = {"outro"}
    servidor.refresh_valido = "revogado"
    with pytest.raises(McpNaoAutorizado):
        await cliente.call_tool("anotar", {"texto": "x"})
    assert servidor.quantas("tools/call") == 0


async def test_401_mesmo_com_token_novo_pede_nova_autorizacao(servidor, cliente):
    original = servidor._token

    def renova_mas_nao_adianta(request):
        resposta = original(request)
        servidor.tokens_validos = set()
        return resposta

    servidor._token = renova_mas_nao_adianta
    servidor.tokens_validos = set()
    with pytest.raises(McpNaoAutorizado):
        await cliente.call_tool("anotar", {"texto": "x"})
    assert servidor.renovacoes == 1  # uma renovação só, sem laço


async def test_servidor_de_autorizacao_fora_do_ar_nao_e_nao_autorizado(servidor, tmp_path):
    _arquivo(tmp_path)
    servidor.tokens_validos = {"outro"}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            raise httpx.ConnectError("fora do ar", request=request)
        return servidor.handler(request)

    transporte = httpx.MockTransport(handler)
    cliente = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    # "Tenta daqui a pouco", e não "autorize de novo": o token pode estar bom.
    with pytest.raises(McpNaoEnviado):
        await cliente.call_tool("anotar", {"texto": "x"})


async def test_sem_arquivo_ou_arquivo_incompleto(servidor, tmp_path):
    transporte = httpx.MockTransport(servidor.handler)
    sem = McpClient(
        MCP_URL,
        TokenStore(tmp_path / "nao_existe.json", transport=transporte),
        transport=transporte,
    )
    with pytest.raises(McpNaoAutorizado):
        await sem.initialize()

    _arquivo(tmp_path, refresh_token="")
    servidor.tokens_validos = {"outro"}
    incompleto = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    with pytest.raises(McpNaoAutorizado):
        await incompleto.initialize()
    assert servidor.renovacoes == 0


async def test_renovacoes_simultaneas_viram_uma(servidor, tmp_path):
    _arquivo(tmp_path)

    async def devagar(request: httpx.Request) -> httpx.Response:
        # Cede o laço a cada requisição: sem isso as cinco chamadas rodariam em
        # fila e o teste passaria mesmo sem cadeado nenhum.
        await asyncio.sleep(0.01)
        return servidor.handler(request)

    transporte = httpx.MockTransport(devagar)
    cliente = McpClient(
        MCP_URL, TokenStore(tmp_path / "token.json", transport=transporte), transport=transporte
    )
    await cliente.initialize()
    servidor.tokens_validos = {"outro"}
    resultados = await asyncio.gather(
        *(cliente.call_tool("somar", {"a": i, "b": 1}) for i in range(5))
    )
    assert [r.estruturado["total"] for r in resultados] == [1, 2, 3, 4, 5]
    # Com refresh token que gira, a segunda renovação usaria um token morto.
    assert servidor.renovacoes == 1


async def test_falha_ao_gravar_preserva_o_arquivo_antigo(servidor, cliente, tmp_path, monkeypatch):
    antes = (tmp_path / "token.json").read_bytes()
    servidor.tokens_validos = {"outro"}

    def disco_cheio(origem, destino):
        raise OSError("disco cheio")

    monkeypatch.setattr(mcp_client.os, "replace", disco_cheio)
    # A chamada dá certo mesmo assim: o token novo está na memória.
    r = await cliente.call_tool("anotar", {"texto": "x"})
    assert r.texto == "anotado"
    assert (await cliente.call_tool("somar", {"a": 1, "b": 1})).estruturado == {"total": 2}
    assert servidor.renovacoes == 1

    # O arquivo antigo está intacto, byte a byte, e não sobrou temporário.
    assert (tmp_path / "token.json").read_bytes() == antes
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]


def test_salvar_token_e_atomico_e_privado(tmp_path, monkeypatch):
    destino = tmp_path / "sub" / "token.json"
    salvar_token(destino, {"access_token": "a"})
    assert json.loads(destino.read_text(encoding="utf-8")) == {"access_token": "a"}
    if sys.platform != "win32":
        assert stat.S_IMODE(destino.stat().st_mode) == 0o600

    # Morrer no meio da escrita não toca no arquivo que já existia.
    def quebra(*args, **kwargs):
        raise OSError("caiu")

    monkeypatch.setattr(mcp_client.json, "dump", quebra)
    with pytest.raises(OSError):
        salvar_token(destino, {"access_token": "b"})
    monkeypatch.undo()
    assert json.loads(destino.read_text(encoding="utf-8")) == {"access_token": "a"}
    assert [p.name for p in destino.parent.iterdir()] == ["token.json"]


async def test_arquivo_trocado_em_disco_e_relido(servidor, cliente, tmp_path):
    await cliente.initialize()
    # Alguém autorizou de novo e copiou o arquivo novo para o volume.
    servidor.tokens_validos = {"acesso-novo"}
    _arquivo(tmp_path, access_token="acesso-novo")
    os.utime(tmp_path / "token.json", ns=(1, 1))  # garante mtime diferente
    await cliente.call_tool("somar", {"a": 1, "b": 1})
    assert servidor.renovacoes == 0


def test_build_sem_url_ou_sem_arquivo(tmp_path):
    assert build_mcp_client("", tmp_path / "token.json") is None
    assert build_mcp_client(MCP_URL, "") is None
    assert build_mcp_client(MCP_URL, tmp_path / "token.json") is None  # arquivo não existe
    _arquivo(tmp_path)
    assert isinstance(build_mcp_client(MCP_URL, tmp_path / "token.json"), McpClient)


def test_config_vem_vazia_por_padrao():
    # Nada do app pode estar escrito no repositório: sem .env, não há URL.
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    fonte = (raiz / "gateway" / "config.py").read_text(encoding="utf-8")
    exemplo = (raiz / ".env.example").read_text(encoding="utf-8").splitlines()
    for nome in ("FINANCAS_MCP_URL", "FINANCAS_CLIENT_ID", "FINANCAS_SCOPE"):
        assert f'os.getenv("{nome}", "")' in fonte
        assert f"{nome}=" in exemplo


# -- o script de autorização: só as partes puras e a rede falsa ----------------


def test_pkce_s256():
    verifier, challenge = auth.gerar_pkce()
    assert 43 <= len(verifier) <= 128
    esperado = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert challenge == esperado and "=" not in challenge
    assert auth.gerar_pkce()[0] != verifier


def test_urls_de_descoberta():
    assert auth.urls_metadata_recurso("https://app.example.com/servico/mcp") == [
        "https://app.example.com/.well-known/oauth-protected-resource/servico/mcp",
        "https://app.example.com/.well-known/oauth-protected-resource",
    ]
    assert auth.urls_metadata_recurso("https://app.example.com") == [
        "https://app.example.com/.well-known/oauth-protected-resource"
    ]
    # Issuer com caminho: o .well-known entra ANTES do caminho (RFC 8414).
    assert auth.urls_metadata_autorizacao("https://auth.example.com/t/v1")[0] == (
        "https://auth.example.com/.well-known/oauth-authorization-server/t/v1"
    )
    assert auth.urls_metadata_autorizacao("https://auth.example.com/") == [
        "https://auth.example.com/.well-known/oauth-authorization-server",
        "https://auth.example.com/.well-known/openid-configuration",
    ]


def test_extrair_resource_metadata():
    cabecalho = 'Bearer error="invalid_token", error_description="x", resource_metadata="https://app.example.com/.well-known/oauth-protected-resource/mcp"'
    assert auth.extrair_resource_metadata(cabecalho).endswith("/oauth-protected-resource/mcp")
    assert auth.extrair_resource_metadata('Bearer realm="x"') is None
    assert auth.extrair_resource_metadata(None) is None


def test_validar_metadata_recurso():
    meta = {"resource": MCP_URL + "/", "authorization_servers": ["https://auth.example.com/t/v1"]}
    assert auth.validar_metadata_recurso(meta, MCP_URL) == "https://auth.example.com/t/v1"
    with pytest.raises(auth.AuthError):  # descreve outro recurso
        auth.validar_metadata_recurso(
            {**meta, "resource": "https://outro.example.com/mcp"}, MCP_URL
        )
    with pytest.raises(auth.AuthError):
        auth.validar_metadata_recurso({"resource": MCP_URL, "authorization_servers": []}, MCP_URL)
    with pytest.raises(auth.AuthError):  # sem HTTPS
        auth.validar_metadata_recurso(
            {"authorization_servers": ["http://auth.example.com"]}, MCP_URL
        )


def test_validar_metadata_autorizacao():
    issuer = "https://auth.example.com/t/v1"
    meta = {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token",
        "code_challenge_methods_supported": ["S256"],
    }
    assert auth.validar_metadata_autorizacao(meta, issuer) is meta
    with pytest.raises(auth.AuthError):  # só `plain`: sem PKCE de verdade
        auth.validar_metadata_autorizacao(
            {**meta, "code_challenge_methods_supported": ["plain"]}, issuer
        )
    with pytest.raises(auth.AuthError):  # outro issuer
        auth.validar_metadata_autorizacao({**meta, "issuer": "https://falso.example.com"}, issuer)
    with pytest.raises(auth.AuthError):  # token endpoint sem HTTPS
        auth.validar_metadata_autorizacao(
            {**meta, "token_endpoint": "http://auth.example.com/token"}, issuer
        )


def test_url_de_autorizacao():
    url = auth.montar_url_autorizacao(
        "https://auth.example.com/authorize?tenant=a",
        client_id="cliente-1",
        redirect_uri="http://127.0.0.1:8765/callback",
        state="estado",
        code_challenge="desafio",
        resource=MCP_URL,
    )
    partes = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(partes.query))
    assert partes.path == "/authorize" and q["tenant"] == "a"
    assert q["response_type"] == "code"
    assert q["client_id"] == "cliente-1"
    assert q["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert q["state"] == "estado"
    assert q["code_challenge"] == "desafio" and q["code_challenge_method"] == "S256"
    assert q["resource"] == MCP_URL
    assert "scope" not in q


def test_retorno_com_state_diferente_e_rejeitado():
    assert auth.conferir_retorno({"code": "c", "state": "certo"}, "certo") == "c"
    with pytest.raises(auth.AuthError):
        auth.conferir_retorno({"code": "c", "state": "errado"}, "certo")
    with pytest.raises(auth.AuthError):  # sem state nenhum
        auth.conferir_retorno({"code": "c"}, "certo")
    with pytest.raises(auth.AuthError) as e:
        auth.conferir_retorno({"error": "access_denied", "state": "certo"}, "certo")
    assert "access_denied" in str(e.value)
    with pytest.raises(auth.AuthError):
        auth.conferir_retorno({"state": "certo"}, "certo")


def test_registro_e_arquivo_de_token():
    corpo = auth.corpo_registro("http://127.0.0.1:8765/callback")
    assert corpo["redirect_uris"] == ["http://127.0.0.1:8765/callback"]
    assert corpo["token_endpoint_auth_method"] == "none"
    assert "refresh_token" in corpo["grant_types"]

    dados = auth.montar_arquivo_token(
        {"access_token": "a", "refresh_token": "r", "expires_in": 3600},
        token_endpoint=TOKEN_URL,
        client_id="cliente-1",
        resource=MCP_URL,
        agora=1000,
    )
    assert dados == {
        "access_token": "a",
        "refresh_token": "r",
        "expires_at": 4600,
        "token_endpoint": TOKEN_URL,
        "client_id": "cliente-1",
        "resource": MCP_URL,
    }
    with pytest.raises(auth.AuthError):  # sem refresh token o acesso morre em uma hora
        auth.montar_arquivo_token(
            {"access_token": "a"}, token_endpoint=TOKEN_URL, client_id="c", resource=MCP_URL
        )


def test_sem_navegador_e_comando_de_copia():
    assert auth.tem_navegador({}, "linux") is False
    assert auth.tem_navegador({"DISPLAY": ":0"}, "linux") is True
    assert auth.tem_navegador({}, "win32") is True
    comando = auth.comando_de_copia("gateway/data/financas_token.json")
    assert "exec -T gateway" in comando and "cat > gateway/data/financas_token.json" in comando
    assert "docker cp" not in comando


def test_main_sem_url_ou_sem_display_explica(monkeypatch, capsys):
    monkeypatch.setattr(auth, "config", type("C", (), {"financas_mcp_url": ""})())
    assert auth.main([]) == 1
    assert "FINANCAS_MCP_URL" in capsys.readouterr().out

    monkeypatch.setattr(auth, "config", type("C", (), {"financas_mcp_url": MCP_URL})())
    monkeypatch.setattr(auth, "tem_navegador", lambda ambiente, plataforma: False)
    assert auth.main([]) == 1
    saida = capsys.readouterr().out
    assert "navegador" in saida and "python -m gateway.tools.financas_auth" in saida


def test_descoberta_registro_e_troca_com_rede_falsa():
    issuer = "https://auth.example.com/t/v1"
    vistos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        vistos.append(f"{request.method} {url}")
        if url == MCP_URL:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer error="invalid_token", '
                    'resource_metadata="https://app.example.com/.well-known/oauth-protected-resource/mcp"'
                },
            )
        if url == "https://app.example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200, json={"resource": MCP_URL, "authorization_servers": [issuer]}
            )
        if url == "https://auth.example.com/.well-known/oauth-authorization-server/t/v1":
            return httpx.Response(
                200,
                json={
                    "issuer": issuer,
                    "authorization_endpoint": issuer + "/authorize",
                    "token_endpoint": issuer + "/token",
                    "registration_endpoint": issuer + "/register",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if url == issuer + "/register":
            assert json.loads(request.content)["redirect_uris"] == [
                "http://127.0.0.1:8765/callback"
            ]
            return httpx.Response(201, json={"client_id": "cliente-novo"})
        if url == issuer + "/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            assert form["grant_type"] == "authorization_code"
            assert form["code"] == "codigo" and form["code_verifier"] == "verificador"
            assert form["client_id"] == "cliente-novo" and form["resource"] == MCP_URL
            assert "client_secret" not in form
            return httpx.Response(
                200, json={"access_token": "a", "refresh_token": "r", "expires_in": 60}
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert auth.descobrir_issuer(http, MCP_URL) == issuer
        meta = auth.descobrir_autorizacao(http, issuer)
        registro = auth.registrar_cliente(
            http, meta["registration_endpoint"], "http://127.0.0.1:8765/callback"
        )
        payload = auth.trocar_codigo(
            http,
            meta["token_endpoint"],
            code="codigo",
            code_verifier="verificador",
            client_id=registro["client_id"],
            redirect_uri="http://127.0.0.1:8765/callback",
            resource=MCP_URL,
        )
    assert payload["refresh_token"] == "r"
    assert vistos[0] == f"POST {MCP_URL}"  # a sonda sem token vem primeiro


def test_resource_metadata_de_outro_host_e_ignorada():
    pedidos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        pedidos.append(url)
        if url == MCP_URL:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": 'Bearer resource_metadata="https://malicioso.example.net/meta"'
                },
            )
        if url == "https://app.example.com/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={"resource": MCP_URL, "authorization_servers": ["https://auth.example.com"]},
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert auth.descobrir_issuer(http, MCP_URL) == "https://auth.example.com"
    assert not any("malicioso" in p for p in pedidos)
