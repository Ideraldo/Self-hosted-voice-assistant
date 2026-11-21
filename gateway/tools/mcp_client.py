"""Cliente MCP (Model Context Protocol) sobre Streamable HTTP, escrito à mão.

Só o transporte e a autorização: `initialize`, `tools/list`, `tools/call`, e o
Bearer token com renovação. Ele não sabe nada sobre o servidor do outro lado --
nomes de ferramenta, esquemas e instruções chegam em tempo de execução, pelo
`tools/list`. Quem dá sentido a isso é o módulo que o usa.

Por que não o SDK `mcp`: a D15 mantém a imagem do gateway com meia dúzia de
dependências, e a D25 já recusou trazer um pacote inteiro para substituir cem
linhas de `httpx`. O que se usa do protocolo aqui é um POST com JSON-RPC.

**O que este módulo promete a quem grava dados** está nas exceções. Uma escrita
que falha tem três desfechos que pedem reações diferentes, e misturá-los é como
se lança um gasto duas vezes:

- `McpNaoAutorizado` -- o token faltou, venceu sem renovar ou foi revogado.
  Nada foi executado. Só `python -m gateway.tools.financas_auth` resolve.
- `McpNaoEnviado` -- a requisição não chegou a sair. Nada foi executado.
- `McpResultadoDesconhecido` -- a requisição saiu e a resposta não voltou
  inteira. **Não se sabe se o servidor gravou.** Quem chama tem que conferir
  antes de tentar de novo.

Por isso `call_tool` **nunca** repete sozinho. A única repetição que ele faz é
depois de um 401, porque o 401 é a prova de que o servidor recusou antes de
executar. As leituras de descoberta (`initialize`, `tools/list`) repetem uma
vez: repetir uma leitura não estraga nada.

Sobre a versão do protocolo: o cliente fala o dialeto de 2025, com `initialize`.
A revisão de 2026 é outro dialeto (sem `initialize`, com um envelope em cada
requisição), e os servidores que falam os dois decidem qual usar pelo cabeçalho
`MCP-Protocol-Version`. Mandar ali uma versão que este código não implementa
faria o servidor esperar um formato que não vai chegar -- então a versão é fixa
e a resposta do servidor é conferida contra a lista do que se conhece.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("ideraldinho.mcp")

#: A versão que este cliente pede. É a mais nova das que ele implementa.
VERSAO_PROTOCOLO = "2025-06-18"

#: O que se aceita de volta. Para o pouco que se usa (três métodos, resposta em
#: JSON ou SSE), estas revisões são iguais no fio.
VERSOES_ACEITAS = ("2025-06-18", "2025-03-26", "2024-11-05")

#: O servidor recusa com 406 quem não declara aceitar **os dois** formatos, mesmo
#: que só vá responder em um.
ACCEPT = "application/json, text/event-stream"

#: Renovar quando falta menos que isto para vencer. O token pode vencer entre a
#: checagem e a chamada; renovar cedo custa uma requisição, renovar tarde custa
#: um 401 no meio de uma frase.
MARGEM_VENCIMENTO_S = 60

#: Teto de páginas do `tools/list`. Um servidor que devolve sempre um cursor
#: novo não pode prender o gateway num laço.
MAX_PAGINAS = 20

#: Falhas do httpx que acontecem **antes** de a requisição sair: não resolveu o
#: nome, não conectou, não havia conexão livre. Tudo o mais que o transporte
#: levanta acontece depois de pelo menos parte da requisição ter ido.
_ANTES_DE_ENVIAR = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
)

#: Erros JSON-RPC que o servidor só devolve sem ter executado nada: não entendeu
#: o JSON, não entendeu a requisição, não conhece o método. Os outros (parâmetro
#: inválido, erro interno) podem vir de depois da execução, e não entram aqui.
_CODIGOS_SEM_EXECUCAO = (-32700, -32600, -32601)


# -- o que pode dar errado -------------------------------------------------


class McpError(Exception):
    """Base. A mensagem é para o log e para quem programa, não para ser falada."""


class McpNaoAutorizado(McpError):
    """Sem token, token vencido que não renova, ou acesso revogado.

    Nada foi executado. Não adianta tentar de novo: é preciso autorizar outra
    vez, no computador, com `python -m gateway.tools.financas_auth`.
    """


class McpNaoEnviado(McpError):
    """A requisição não saiu daqui. Nada foi executado; tentar de novo é seguro."""


class McpResultadoDesconhecido(McpError):
    """A requisição saiu e a resposta não chegou. Pode ter executado, pode não.

    Para uma leitura, tanto faz. Para uma escrita, é o caso que não admite
    palpite: quem chama deve consultar o servidor antes de repetir.
    """


class McpRespostaInvalida(McpResultadoDesconhecido):
    """O servidor respondeu, mas não deu para ler a resposta.

    É subclasse de `McpResultadoDesconhecido` de propósito: um 200 ilegível
    quer dizer que o pedido chegou e foi processado, só não se sabe com que
    resultado.
    """


class McpErroServidor(McpError):
    """O servidor respondeu com um erro, HTTP ou JSON-RPC.

    Não confundir com `ToolResult.erro`: aquele é a ferramenta dizendo "não
    posso fazer isso" (regra de negócio, e a mensagem serve para o usuário);
    este é o protocolo recusando a requisição.
    """

    def __init__(
        self,
        mensagem: str,
        *,
        codigo: int | None = None,
        status: int | None = None,
        dados: Any = None,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.status = status
        self.dados = dados

    @property
    def nao_executou(self) -> bool:
        """Se é **certo** que nada foi executado.

        Conservador: `True` só para recusa HTTP 4xx (o servidor barrou na porta)
        e para os erros JSON-RPC que só existem antes da execução. `False` não
        quer dizer que executou -- quer dizer que este código não garante.
        """
        if self.codigo is not None:
            return self.codigo in _CODIGOS_SEM_EXECUCAO
        return self.status is not None and 400 <= self.status < 500


class _Recusou401(Exception):
    """Interno: o servidor devolveu 401. Vira renovação, nunca sai do módulo."""


# -- o resultado de uma chamada ---------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """O que uma ferramenta devolveu.

    `erro` é o `isError` do protocolo: a ferramenta rodou e recusou. O `texto`
    nesse caso é a explicação dela, escrita para um modelo ler.
    """

    texto: str
    estruturado: dict[str, Any] | None = None
    erro: bool = False
    conteudo: list[dict[str, Any]] = field(default_factory=list)


def _resultado_da_chamada(result: Any) -> ToolResult:
    if not isinstance(result, dict):
        raise McpRespostaInvalida("o resultado da ferramenta nao e um objeto")
    conteudo = [b for b in (result.get("content") or []) if isinstance(b, dict)]
    textos = [str(b.get("text", "")) for b in conteudo if b.get("type") == "text"]
    estruturado = result.get("structuredContent")
    return ToolResult(
        texto="\n".join(t for t in textos if t),
        estruturado=estruturado if isinstance(estruturado, dict) else None,
        erro=bool(result.get("isError")),
        conteudo=conteudo,
    )


# -- o token em disco --------------------------------------------------------


def salvar_token(path: str | Path, dados: dict[str, Any]) -> None:
    """Grava o arquivo de token de forma atômica, com modo 0600.

    Escreve num temporário ao lado e troca com `os.replace`: em qualquer
    instante o arquivo é o antigo inteiro ou o novo inteiro. Isso importa porque
    o refresh token **gira** -- cada renovação invalida o anterior. Um `write`
    direto que morresse no meio deixaria um arquivo truncado: o token velho já
    não vale, o novo não foi salvo, e a única saída é autorizar tudo de novo.

    O temporário fica no mesmo diretório porque `os.replace` só é atômico dentro
    do mesmo sistema de arquivos, e o volume do Docker é um à parte.
    """
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(f"{destino.name}.{os.getpid()}.tmp")
    try:
        # 0600 já na criação: criar aberto e fechar depois deixaria uma janela
        # em que o token está legível por qualquer usuário da máquina.
        fd = os.open(temporario, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, indent=2)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, destino)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporario)
        raise


class TokenStore:
    """Access token e refresh token, em memória e num arquivo JSON.

    O arquivo é escrito por `financas_auth` e traz tudo o que a renovação
    precisa (`token_endpoint`, `client_id`): o gateway não refaz a descoberta
    OAuth, só usa o que ficou registrado na autorização.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._path = Path(path)
        self._timeout = timeout
        self._transport = transport
        self._dados: dict[str, Any] | None = None
        self._lido_em: int | None = None
        # Um cadeado só para toda renovação. Com refresh token que gira, duas
        # renovações simultâneas usam o mesmo token: a segunda é recusada, e
        # alguns servidores tratam o reuso como roubo e revogam tudo.
        self._lock = asyncio.Lock()

    @property
    def existe(self) -> bool:
        return self._path.exists()

    def _carregar(self) -> dict[str, Any]:
        """Os dados atuais, relendo o disco só se o arquivo mudou.

        Reler quando muda é o que permite autorizar de novo e copiar o arquivo
        para dentro do volume sem reiniciar o gateway. Não reler quando não muda
        é o que mantém vivo o token que está só na memória depois de uma
        gravação que falhou (ver `renovar`).
        """
        try:
            marca = self._path.stat().st_mtime_ns
        except OSError:
            raise McpNaoAutorizado("nao ha arquivo de token; autorize de novo") from None
        if self._dados is not None and marca == self._lido_em:
            return self._dados
        try:
            dados = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise McpNaoAutorizado("o arquivo de token esta ilegivel; autorize de novo") from None
        if not isinstance(dados, dict):
            raise McpNaoAutorizado("o arquivo de token esta ilegivel; autorize de novo")
        self._dados, self._lido_em = dados, marca
        return dados

    @staticmethod
    def _vencendo(dados: dict[str, Any]) -> bool:
        # Sem `expires_at` não há como saber: usa-se o token até o servidor
        # recusar, e o 401 dispara a renovação.
        vence = dados.get("expires_at")
        return bool(vence) and time.time() >= float(vence) - MARGEM_VENCIMENTO_S

    async def access_token(self) -> str:
        """Um access token que se espera válido, renovando se estiver no fim."""
        dados = self._carregar()
        token = dados.get("access_token")
        if token and not self._vencendo(dados):
            return token
        return await self.renovar(rejeitado=token)

    async def renovar(self, rejeitado: str | None = None) -> str:
        """Troca o refresh token por um access token novo.

        `rejeitado` é o token que acabou de falhar. Quem ficou esperando o
        cadeado enquanto outra tarefa renovava encontra um token diferente
        daquele e o usa, em vez de gastar o refresh token recém-emitido.

        Levanta `McpNaoAutorizado` quando o servidor **recusa** o refresh token,
        e `McpNaoEnviado` quando não deu para falar com ele. A diferença é o que
        se diz à pessoa: "preciso ser autorizado de novo" ou "tenta daqui a
        pouco".
        """
        async with self._lock:
            dados = self._carregar()
            atual = dados.get("access_token")
            if atual and atual != rejeitado and not self._vencendo(dados):
                return atual

            refresh = dados.get("refresh_token")
            endpoint = dados.get("token_endpoint")
            client_id = dados.get("client_id")
            if not (refresh and endpoint and client_id):
                raise McpNaoAutorizado("o arquivo de token esta incompleto; autorize de novo")

            form = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}
            # Cliente público (PKCE) não tem segredo; se o registro devolveu um,
            # ele acompanha. `resource` amarra o token ao servidor MCP (RFC 8707).
            for campo in ("client_secret", "resource"):
                if dados.get(campo):
                    form[campo] = dados[campo]

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as http:
                    r = await http.post(endpoint, data=form, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                # Se a requisição chegou e a resposta se perdeu, o servidor pode
                # ter girado o refresh token sem que o novo chegasse aqui. Não há
                # o que fazer agora; a próxima tentativa descobre (e vira
                # `McpNaoAutorizado` se o token antigo morreu).
                log.warning("renovacao do token falhou no transporte: %s", type(e).__name__)
                raise McpNaoEnviado("nao consegui falar com o servidor de autorizacao") from e

            if r.status_code in (400, 401):
                # invalid_grant e parentes: o refresh token venceu, foi revogado
                # ou já foi usado. Repetir não muda a resposta.
                raise McpNaoAutorizado(
                    f"o servidor de autorizacao recusou a renovacao ({r.status_code})"
                )
            if r.status_code != 200:
                raise McpNaoEnviado(
                    f"o servidor de autorizacao respondeu {r.status_code} na renovacao"
                )
            try:
                payload = r.json()
                novo = payload["access_token"]
            except (ValueError, KeyError, TypeError):
                raise McpNaoEnviado("resposta ilegivel do servidor de autorizacao") from None

            novos = dict(dados)
            novos["access_token"] = novo
            vida = payload.get("expires_in")
            novos["expires_at"] = int(time.time() + float(vida)) if vida else 0
            if payload.get("refresh_token"):
                novos["refresh_token"] = payload["refresh_token"]

            # Memória primeiro: se o disco falhar, este processo continua
            # funcionando com o token novo, que é o único que ainda vale.
            self._dados = novos
            try:
                salvar_token(self._path, novos)
                self._lido_em = self._path.stat().st_mtime_ns
            except OSError as e:
                # O arquivo antigo continua inteiro, mas o refresh token dentro
                # dele já foi trocado no servidor. Enquanto o processo viver, a
                # memória resolve; se ele reiniciar antes de uma gravação dar
                # certo, será preciso autorizar de novo. Tem que aparecer no log.
                log.error(
                    "o token foi renovado mas NAO foi gravado em %s (%s); "
                    "se o gateway reiniciar agora, sera preciso autorizar de novo",
                    self._path,
                    e,
                )
            return novo


# -- leitura das respostas ---------------------------------------------------


def _e_a_resposta(mensagem: Any, id_esperado: int) -> bool:
    return (
        isinstance(mensagem, dict)
        and mensagem.get("id") == id_esperado
        and ("result" in mensagem or "error" in mensagem)
    )


def _resposta_no_json(corpo: bytes, id_esperado: int) -> dict[str, Any]:
    try:
        dados = json.loads(corpo)
    except ValueError:
        raise McpRespostaInvalida("o servidor devolveu JSON invalido") from None
    # A especificação antiga permitia responder em lote; procurar pelo id cobre
    # os dois formatos sem custo.
    for mensagem in dados if isinstance(dados, list) else [dados]:
        if _e_a_resposta(mensagem, id_esperado):
            return mensagem
    raise McpRespostaInvalida("a resposta nao corresponde a requisicao enviada")


def _mensagem_do_evento(linhas_data: list[str], id_esperado: int) -> dict[str, Any] | None:
    """A resposta dentro de um evento SSE, ou `None` se o evento é outra coisa.

    Outra coisa é normal: evento vazio para manter a conexão, notificação de
    progresso, log. Um evento com JSON quebrado também é ignorado aqui -- se a
    resposta de verdade não vier depois, o fim do stream acusa.
    """
    texto = "\n".join(linhas_data)
    if not texto.strip():
        return None
    try:
        mensagem = json.loads(texto)
    except ValueError:
        return None
    return mensagem if _e_a_resposta(mensagem, id_esperado) else None


async def _resposta_no_sse(r: httpx.Response, id_esperado: int) -> dict[str, Any]:
    """Lê o stream até achar a resposta desta requisição, e para.

    Parar na resposta, em vez de ler até o servidor fechar, é o que impede uma
    chamada que já terminou de esperar o timeout num servidor que mantém o
    stream aberto.
    """
    linhas_data: list[str] = []
    async for linha in r.aiter_lines():
        if linha == "":  # linha em branco fecha o evento
            mensagem = _mensagem_do_evento(linhas_data, id_esperado)
            if mensagem is not None:
                return mensagem
            linhas_data = []
            continue
        if linha.startswith(":"):  # comentário; é como o servidor faz keep-alive
            continue
        campo, _, valor = linha.partition(":")
        if campo == "data":
            linhas_data.append(valor[1:] if valor.startswith(" ") else valor)
    # O stream acabou sem a linha em branco final: o último evento ainda conta.
    mensagem = _mensagem_do_evento(linhas_data, id_esperado)
    if mensagem is not None:
        return mensagem
    raise McpRespostaInvalida("o stream terminou sem a resposta da requisicao")


def _mensagem_de_erro_http(corpo: bytes, status: int) -> str:
    """Tira do corpo de um erro HTTP a frase que o servidor mandou, se houver."""
    try:
        dados = json.loads(corpo)
    except ValueError:
        return f"o servidor respondeu {status}"
    if isinstance(dados, dict):
        erro = dados.get("error")
        if isinstance(erro, dict) and erro.get("message"):
            return f"o servidor respondeu {status}: {erro['message']}"
        if dados.get("error_description"):
            return f"o servidor respondeu {status}: {dados['error_description']}"
    return f"o servidor respondeu {status}"


# -- o cliente ---------------------------------------------------------------


class McpClient:
    """Um servidor MCP, por Streamable HTTP, com Bearer token."""

    def __init__(
        self,
        url: str,
        tokens: TokenStore,
        *,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
        nome_cliente: str = "ideraldinho",
    ) -> None:
        self._url = url
        self._tokens = tokens
        # Conectar tem prazo curto e separado: uma falha de conexão é a única
        # que garante que nada foi enviado, e convém chegar nela depressa.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))
        self._transport = transport
        self._nome_cliente = nome_cliente
        self._proximo_id = 0
        self._versao: str | None = None
        self._sessao: str | None = None
        self._servidor: dict[str, Any] | None = None
        self._init_lock = asyncio.Lock()

    # -- uma requisição ----------------------------------------------------

    def _cabecalhos(self, token: str, e_initialize: bool) -> dict[str, str]:
        cabecalhos = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": ACCEPT,
        }
        # No `initialize` a versão vai no corpo; daí em diante, no cabeçalho.
        if not e_initialize and self._versao:
            cabecalhos["MCP-Protocol-Version"] = self._versao
        # Servidor sem estado não emite sessão, e então não se manda nenhuma.
        if not e_initialize and self._sessao:
            cabecalhos["Mcp-Session-Id"] = self._sessao
        return cabecalhos

    async def _enviar(self, corpo: dict[str, Any], token: str) -> dict[str, Any] | None:
        """Um POST. Devolve a mensagem JSON-RPC de resposta (`None` para notificação).

        Um `AsyncClient` novo por requisição, como em `spotify.py`. Além de
        dispensar ciclo de vida, isso tira de cena a conexão reaproveitada que o
        servidor já fechou -- um erro que acontece "no envio" mas não deixa
        claro se algo foi recebido.
        """
        id_esperado = corpo.get("id")
        e_initialize = corpo.get("method") == "initialize"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
                async with http.stream(
                    "POST", self._url, json=corpo, headers=self._cabecalhos(token, e_initialize)
                ) as r:
                    if r.status_code == 401:
                        raise _Recusou401()
                    if r.status_code >= 500:
                        # Um 502 ou 504 costuma vir de um proxy no caminho, e o
                        # servidor atrás dele pode ter executado.
                        raise McpResultadoDesconhecido(f"o servidor respondeu {r.status_code}")
                    if r.status_code >= 400:
                        if r.status_code == 404 and self._sessao:
                            # Sessão vencida: a próxima chamada abre outra.
                            self._sessao = self._versao = None
                        raise McpErroServidor(
                            _mensagem_de_erro_http(await r.aread(), r.status_code),
                            status=r.status_code,
                        )
                    if id_esperado is None:
                        return None  # notificação: 202 sem corpo
                    if e_initialize:
                        self._sessao = r.headers.get("mcp-session-id") or None
                    tipo = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if tipo == "text/event-stream":
                        return await _resposta_no_sse(r, id_esperado)
                    if tipo == "application/json":
                        return _resposta_no_json(await r.aread(), id_esperado)
                    raise McpRespostaInvalida(f"tipo de resposta inesperado: {tipo or 'nenhum'}")
        except _ANTES_DE_ENVIAR as e:
            raise McpNaoEnviado(f"nao consegui conectar ao servidor ({type(e).__name__})") from e
        except httpx.HTTPError as e:
            # Timeout de leitura, conexão derrubada, resposta cortada: a
            # requisição saiu. O que aconteceu do outro lado não se sabe.
            raise McpResultadoDesconhecido(
                f"a resposta do servidor nao chegou ({type(e).__name__})"
            ) from e

    async def _uma_vez(self, metodo: str, params: dict[str, Any] | None) -> Any:
        self._proximo_id += 1
        corpo: dict[str, Any] = {"jsonrpc": "2.0", "id": self._proximo_id, "method": metodo}
        if params is not None:
            corpo["params"] = params

        token = await self._tokens.access_token()
        try:
            mensagem = await self._enviar(corpo, token)
        except _Recusou401:
            # O 401 vem da checagem do token, antes de qualquer ferramenta
            # rodar. É a única situação em que repetir uma escrita é seguro, e
            # por isso é a única em que se repete.
            token = await self._tokens.renovar(rejeitado=token)
            try:
                mensagem = await self._enviar(corpo, token)
            except _Recusou401:
                raise McpNaoAutorizado(
                    "o servidor recusou um token recem-renovado; autorize de novo"
                ) from None

        assert mensagem is not None  # só notificação devolve None
        if "error" in mensagem:
            erro = mensagem["error"] if isinstance(mensagem["error"], dict) else {}
            raise McpErroServidor(
                str(erro.get("message") or "erro sem mensagem"),
                codigo=erro.get("code") if isinstance(erro.get("code"), int) else None,
                dados=erro.get("data"),
            )
        return mensagem["result"]

    async def _ler(self, metodo: str, params: dict[str, Any] | None) -> Any:
        """Requisição de leitura: repete uma vez se a rede falhou."""
        try:
            return await self._uma_vez(metodo, params)
        except (McpNaoEnviado, McpResultadoDesconhecido) as e:
            log.info("%s falhou (%s); tentando mais uma vez", metodo, e)
            return await self._uma_vez(metodo, params)

    # -- o protocolo -------------------------------------------------------

    async def initialize(self) -> dict[str, Any]:
        """O aperto de mão. Devolve o que o servidor disse de si.

        O resultado traz `serverInfo` e, quando o servidor tem, `instructions`:
        o texto em que ele explica a um modelo como usar as ferramentas.
        """
        async with self._init_lock:
            if self._servidor is not None and self._versao:
                return self._servidor
            resultado = await self._ler(
                "initialize",
                {
                    "protocolVersion": VERSAO_PROTOCOLO,
                    # Nenhuma capacidade: este cliente não responde a pedidos
                    # do servidor (sampling, roots, elicitation).
                    "capabilities": {},
                    "clientInfo": {"name": self._nome_cliente, "version": "1"},
                },
            )
            versao = resultado.get("protocolVersion") if isinstance(resultado, dict) else None
            if versao not in VERSOES_ACEITAS:
                raise McpErroServidor(f"o servidor quer falar a versao {versao!r}, que nao conheco")
            self._versao = versao
            self._servidor = resultado
            await self._avisar_inicializado()
            return resultado

    async def _avisar_inicializado(self) -> None:
        """A notificação que fecha o aperto de mão.

        A especificação pede; um servidor sem estado aceita e esquece. Por isso
        uma falha aqui não derruba nada -- mas um 401 sim, porque ele diria a
        mesma coisa na chamada seguinte.
        """
        corpo = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            token = await self._tokens.access_token()
            await self._enviar(corpo, token)
        except _Recusou401:
            raise McpNaoAutorizado("o servidor recusou o token; autorize de novo") from None
        except McpNaoAutorizado:
            raise
        except McpError as e:
            log.info("notifications/initialized nao foi aceita: %s", e)

    async def _garantir_inicializado(self) -> None:
        if self._servidor is None or not self._versao:
            await self.initialize()

    async def list_tools(self) -> list[dict[str, Any]]:
        """As ferramentas do servidor, como ele as descreve (nome, esquema, anotações)."""
        await self._garantir_inicializado()
        ferramentas: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGINAS):
            resultado = await self._ler("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(resultado, dict):
                raise McpRespostaInvalida("tools/list nao devolveu um objeto")
            ferramentas.extend(t for t in resultado.get("tools") or [] if isinstance(t, dict))
            cursor = resultado.get("nextCursor")
            if not cursor:
                break
        return ferramentas

    async def call_tool(self, nome: str, argumentos: dict[str, Any] | None = None) -> ToolResult:
        """Chama uma ferramenta, **uma vez**.

        Não há repetição automática aqui, nem para ferramenta de leitura: este
        módulo não sabe qual é qual, e errar para o lado de repetir uma escrita
        é o erro caro. Quem chama sabe o que pediu e decide.
        """
        await self._garantir_inicializado()
        resultado = await self._uma_vez("tools/call", {"name": nome, "arguments": argumentos or {}})
        return _resultado_da_chamada(resultado)


def build_mcp_client(
    url: str | None, token_path: str | Path | None, *, timeout: float = 20.0
) -> McpClient | None:
    """O cliente, ou `None` quando falta a URL ou o arquivo de token.

    `None` é o mesmo contrato das ferramentas do Spotify: sem credencial, quem
    monta as ferramentas simplesmente não as declara, e o modelo não vê o que
    não pode usar.
    """
    if not url or not token_path:
        return None
    tokens = TokenStore(token_path)
    if not tokens.existe:
        return None
    return McpClient(url, tokens, timeout=timeout)
