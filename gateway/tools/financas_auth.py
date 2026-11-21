"""Autorização do app de finanças. Roda uma vez, num computador com navegador:

    python -m gateway.tools.financas_auth

O servidor MCP do app exige OAuth. Este script descobre sozinho onde autorizar
(a partir de `FINANCAS_MCP_URL`), abre a página de consentimento, recebe o
retorno num servidor local efêmero, troca o código pelos tokens e guarda em
disco. Depois disso o gateway renova o acesso sem ninguém ver.

Nada do servidor está escrito aqui. O caminho é o que as especificações mandam:

1. **Onde autorizar** -- o servidor MCP publica quem é o seu servidor de
   autorização (RFC 9728, `/.well-known/oauth-protected-resource`).
2. **Como autorizar** -- o servidor de autorização publica os seus endereços
   (RFC 8414, ou a descoberta do OpenID Connect).
3. **Quem está pedindo** -- se `FINANCAS_CLIENT_ID` estiver vazio e o servidor
   aceitar, o script se registra na hora (RFC 7591).
4. **O pedido** -- authorization code com PKCE (S256), `state`, e retorno em
   `127.0.0.1`.

Tem que rodar **no computador**, não na VPS: a página de consentimento pede que
você esteja logado no app, e o retorno volta para `127.0.0.1` da máquina onde o
navegador está. O arquivo gerado é que viaja para o servidor -- o script imprime
o comando no final.

O arquivo é a credencial de longo prazo e dá acesso às finanças inteiras, não
só ao que o assistente usa. Ele **não** vai para o git (`.gitignore`), não entra
na imagem (`.dockerignore`) e não desce para o dispositivo.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import os
import re
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Mapping

import httpx
from dotenv import load_dotenv

load_dotenv()

from gateway.config import config  # noqa: E402
from gateway.tools.mcp_client import ACCEPT, salvar_token  # noqa: E402

#: Quanto esperar pelo clique em "Permitir" antes de desistir.
ESPERA_CONSENTIMENTO_S = 300


class AuthError(Exception):
    """Algo que impede a autorização, já escrito para quem está no terminal."""


# -- partes puras: nada aqui abre navegador nem fala com a rede -------------


def gerar_pkce() -> tuple[str, str]:
    """`(code_verifier, code_challenge)` para o método S256.

    O PKCE é o que substitui o client secret num cliente que não tem onde
    guardar segredo: quem interceptar o código no retorno não consegue trocá-lo
    sem o verifier, que nunca saiu desta máquina.
    """
    verifier = secrets.token_urlsafe(64)  # 86 caracteres; a RFC 7636 pede 43 a 128
    resumo = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(resumo).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _origem(url: str) -> str:
    partes = urllib.parse.urlsplit(url)
    return f"{partes.scheme}://{partes.netloc}"


def _igual(a: str, b: str) -> bool:
    """Compara duas URLs ignorando a barra final, que cada servidor trata de um jeito."""
    return a.rstrip("/") == b.rstrip("/")


def _url_segura(url: str) -> bool:
    """HTTPS, ou HTTP só para a própria máquina (servidor de desenvolvimento)."""
    partes = urllib.parse.urlsplit(url)
    if partes.scheme == "https":
        return bool(partes.netloc)
    return partes.scheme == "http" and partes.hostname in ("127.0.0.1", "localhost", "::1")


def urls_metadata_recurso(mcp_url: str) -> list[str]:
    """Onde procurar a metadata do recurso protegido (RFC 9728), em ordem.

    Primeiro o endereço com o caminho do recurso como sufixo, depois o da raiz:
    um mesmo host pode abrigar mais de um recurso, e o específico ganha.
    """
    partes = urllib.parse.urlsplit(mcp_url)
    base = f"{partes.scheme}://{partes.netloc}/.well-known/oauth-protected-resource"
    caminho = partes.path.rstrip("/")
    return [base + caminho, base] if caminho else [base]


def extrair_resource_metadata(www_authenticate: str | None) -> str | None:
    """O `resource_metadata="..."` do cabeçalho de um 401, se vier."""
    if not www_authenticate:
        return None
    achado = re.search(r'resource_metadata\s*=\s*"([^"]+)"', www_authenticate)
    return achado.group(1) if achado else None


def urls_metadata_autorizacao(issuer: str) -> list[str]:
    """Onde procurar a metadata do servidor de autorização, em ordem.

    Quando o issuer tem caminho, a RFC 8414 manda inserir o `.well-known`
    **antes** dele, e não no fim -- é o detalhe que mais se erra. As variantes
    do OpenID Connect vêm depois porque muito servidor só publica essas.
    """
    partes = urllib.parse.urlsplit(issuer)
    origem = f"{partes.scheme}://{partes.netloc}"
    caminho = partes.path.rstrip("/")
    if not caminho:
        return [
            f"{origem}/.well-known/oauth-authorization-server",
            f"{origem}/.well-known/openid-configuration",
        ]
    return [
        f"{origem}/.well-known/oauth-authorization-server{caminho}",
        f"{origem}/.well-known/openid-configuration{caminho}",
        f"{origem}{caminho}/.well-known/openid-configuration",
    ]


def validar_metadata_recurso(meta: Any, mcp_url: str) -> str:
    """Confere a metadata do recurso e devolve o issuer do servidor de autorização."""
    if not isinstance(meta, dict):
        raise AuthError("a metadata do servidor MCP nao e um objeto JSON")
    recurso = meta.get("resource")
    # A RFC 9728 manda conferir: uma metadata que descreve outro recurso faria o
    # token ser pedido para o lugar errado.
    if isinstance(recurso, str) and not _igual(recurso, mcp_url):
        raise AuthError(
            f"a metadata descreve o recurso {recurso!r}, mas FINANCAS_MCP_URL e {mcp_url!r}. "
            "Confira a URL no .env (inclusive o caminho)."
        )
    servidores = meta.get("authorization_servers")
    if not isinstance(servidores, list) or not servidores or not isinstance(servidores[0], str):
        raise AuthError("o servidor MCP nao informa nenhum servidor de autorizacao")
    if not _url_segura(servidores[0]):
        raise AuthError(f"servidor de autorizacao sem HTTPS: {servidores[0]!r}")
    return servidores[0]


def validar_metadata_autorizacao(meta: Any, issuer: str) -> dict[str, Any]:
    """Confere a metadata do servidor de autorização antes de confiar nela."""
    if not isinstance(meta, dict):
        raise AuthError("a metadata do servidor de autorizacao nao e um objeto JSON")
    if isinstance(meta.get("issuer"), str) and not _igual(meta["issuer"], issuer):
        raise AuthError(
            f"o servidor de autorizacao diz ser {meta['issuer']!r}, mas foi anunciado "
            f"como {issuer!r}"
        )
    for campo in ("authorization_endpoint", "token_endpoint"):
        valor = meta.get(campo)
        if not isinstance(valor, str) or not _url_segura(valor):
            raise AuthError(f"o servidor de autorizacao nao informa um `{campo}` com HTTPS")
    metodos = meta.get("code_challenge_methods_supported")
    # Lista presente e sem S256 é recusa: sem PKCE o código do retorno vale
    # sozinho. Lista ausente é comum em descoberta OpenID; segue-se com S256 e,
    # se o servidor não entender, é ele quem recusa.
    if isinstance(metodos, list) and "S256" not in metodos:
        raise AuthError("o servidor de autorizacao nao aceita PKCE com S256")
    return meta


def corpo_registro(redirect_uri: str) -> dict[str, Any]:
    """O pedido de registro dinâmico (RFC 7591).

    `token_endpoint_auth_method: none` declara um cliente público: um script no
    computador de alguém não tem como guardar um client secret, e fingir que
    tem é pior que não ter.
    """
    return {
        "client_name": "Ideraldinho",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def montar_url_autorizacao(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    resource: str,
    scope: str = "",
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # RFC 8707: diz ao servidor para qual recurso o token vai servir.
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    # O endpoint pode já trazer query; acrescenta-se com o separador certo.
    separador = "&" if urllib.parse.urlsplit(authorization_endpoint).query else "?"
    return authorization_endpoint + separador + urllib.parse.urlencode(params)


def conferir_retorno(params: Mapping[str, str | None], state_esperado: str) -> str:
    """Valida o que voltou no redirect e devolve o código.

    O `state` é conferido **antes** de qualquer outra coisa, e com comparação de
    tempo constante: ele é a prova de que este retorno responde ao pedido que
    este script fez, e não a um link que alguém mandou você abrir.
    """
    recebido = params.get("state") or ""
    if not secrets.compare_digest(recebido.encode(), state_esperado.encode()):
        raise AuthError("o `state` voltou diferente do que foi enviado; abortando")
    if params.get("error"):
        detalhe = params.get("error_description") or ""
        raise AuthError(f"o servidor recusou: {params['error']} {detalhe}".strip())
    codigo = params.get("code")
    if not codigo:
        raise AuthError("o retorno veio sem o codigo de autorizacao")
    return codigo


def montar_arquivo_token(
    payload: Mapping[str, Any],
    *,
    token_endpoint: str,
    client_id: str,
    resource: str,
    client_secret: str | None = None,
    agora: float | None = None,
) -> dict[str, Any]:
    """O conteúdo do arquivo de token, a partir da resposta da troca do código.

    Vai junto tudo o que a renovação precisa (`token_endpoint`, `client_id`):
    o gateway não refaz a descoberta, usa o que ficou registrado aqui. Um
    cliente registrado dinamicamente só existe neste arquivo -- perder o
    `client_id` é perder a capacidade de renovar.
    """
    if not payload.get("access_token"):
        raise AuthError("o servidor nao devolveu access_token")
    if not payload.get("refresh_token"):
        # Sem refresh token o acesso morreria em uma hora, sem aviso. Melhor
        # falhar agora, com a pessoa na frente do terminal.
        raise AuthError("o servidor nao devolveu refresh_token; sem ele o acesso nao se renova")
    vida = payload.get("expires_in")
    agora = time.time() if agora is None else agora
    dados: dict[str, Any] = {
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": int(agora + float(vida)) if vida else 0,
        "token_endpoint": token_endpoint,
        "client_id": client_id,
        "resource": resource,
    }
    if client_secret:
        dados["client_secret"] = client_secret
    if payload.get("scope"):
        dados["scope"] = payload["scope"]
    return dados


def tem_navegador(ambiente: Mapping[str, str], plataforma: str) -> bool:
    """Se dá para abrir um navegador nesta máquina.

    Em Linux, sem `DISPLAY` nem `WAYLAND_DISPLAY` é um servidor ou um contêiner
    -- exatamente onde este script não deve rodar. Windows e macOS sempre têm.
    """
    if plataforma.startswith(("win", "darwin")):
        return True
    return bool(ambiente.get("DISPLAY") or ambiente.get("WAYLAND_DISPLAY"))


def comando_de_copia(token_path: str | Path) -> str:
    """O comando que leva o arquivo para dentro do volume do gateway.

    Pelo `exec ... cat >`, e não pelo `docker cp`: o `cp` grava como root, e o
    gateway (que não é root) não conseguiria regravar o arquivo na primeira
    renovação. O `umask 077` faz o arquivo nascer 0600 também lá dentro.
    """
    caminho = Path(token_path).as_posix()
    return (
        "docker compose -f gateway/docker-compose.yml exec -T gateway \\\n"
        f"    sh -c 'umask 077; cat > {caminho}' \\\n"
        f"    < {caminho}"
    )


# -- rede: descoberta, registro e troca -------------------------------------


def _get_json(http: httpx.Client, url: str) -> Any | None:
    try:
        r = http.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def descobrir_issuer(http: httpx.Client, mcp_url: str) -> str:
    """Do endereço do servidor MCP ao issuer do servidor de autorização."""
    candidatas: list[str] = []
    # O 401 do próprio servidor é a fonte preferida: ele aponta a metadata
    # mesmo quando ela mora num caminho fora do padrão.
    try:
        sonda = http.post(
            mcp_url,
            json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
            headers={"Accept": ACCEPT},
        )
    except httpx.HTTPError as e:
        raise AuthError(f"nao consegui falar com {mcp_url} ({type(e).__name__})") from e
    apontada = extrair_resource_metadata(sonda.headers.get("www-authenticate"))
    # Só vale se for do mesmo host: um cabeçalho não pode mandar o script
    # buscar "quem autoriza" num endereço que não é o do servidor configurado.
    if apontada and _origem(apontada) == _origem(mcp_url):
        candidatas.append(apontada)
    candidatas += [u for u in urls_metadata_recurso(mcp_url) if u not in candidatas]

    for url in candidatas:
        meta = _get_json(http, url)
        if meta is not None:
            return validar_metadata_recurso(meta, mcp_url)
    raise AuthError(
        "o servidor nao publica a metadata de recurso protegido (RFC 9728). "
        "Confira FINANCAS_MCP_URL."
    )


def descobrir_autorizacao(http: httpx.Client, issuer: str) -> dict[str, Any]:
    for url in urls_metadata_autorizacao(issuer):
        meta = _get_json(http, url)
        if meta is not None:
            return validar_metadata_autorizacao(meta, issuer)
    raise AuthError(f"nao achei a metadata do servidor de autorizacao {issuer}")


def registrar_cliente(http: httpx.Client, registration_endpoint: str, redirect_uri: str) -> dict:
    if not _url_segura(registration_endpoint):
        raise AuthError("o endpoint de registro nao usa HTTPS")
    r = http.post(registration_endpoint, json=corpo_registro(redirect_uri))
    if r.status_code not in (200, 201):
        raise AuthError(f"o registro do cliente falhou ({r.status_code}): {r.text[:200]}")
    dados = r.json()
    if not isinstance(dados, dict) or not dados.get("client_id"):
        raise AuthError("o registro nao devolveu client_id")
    return dados


def trocar_codigo(
    http: httpx.Client,
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    resource: str,
    client_secret: str | None = None,
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
        "resource": resource,
    }
    if client_secret:
        form["client_secret"] = client_secret
    r = http.post(token_endpoint, data=form, headers={"Accept": "application/json"})
    if r.status_code != 200:
        raise AuthError(f"a troca do codigo falhou ({r.status_code}): {r.text[:200]}")
    return r.json()


# -- o retorno no navegador ---------------------------------------------------


class _Callback(http.server.BaseHTTPRequestHandler):
    """Recebe o retorno do servidor de autorização e nada mais."""

    caminho = "/callback"
    params: dict[str, str | None] | None = None

    def do_GET(self) -> None:  # noqa: N802  (assinatura da stdlib)
        partes = urllib.parse.urlsplit(self.path)
        # O navegador também pede /favicon.ico; só o caminho do retorno conta.
        if partes.path != _Callback.caminho:
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(partes.query)
        _Callback.params = {
            k: (query.get(k) or [None])[0]
            for k in ("code", "state", "error", "error_description")
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        recado = "Pode fechar esta aba e voltar ao terminal."
        self.wfile.write(f"<html><body><h2>{recado}</h2></body></html>".encode())

    def log_message(self, *args: object) -> None:
        pass  # sem ruído no terminal -- e sem o código de autorização no log


def _esperar_retorno(server: http.server.HTTPServer) -> dict[str, str | None]:
    limite = time.monotonic() + ESPERA_CONSENTIMENTO_S
    server.timeout = 1
    while _Callback.params is None:
        if time.monotonic() > limite:
            raise AuthError("ninguem autorizou em cinco minutos; rode de novo")
        server.handle_request()
    return _Callback.params


# -- o script -----------------------------------------------------------------


def autorizar(abrir_navegador: bool) -> Path:
    mcp_url = config.financas_mcp_url
    redirect = config.financas_redirect_uri
    partes = urllib.parse.urlsplit(redirect)
    # Loopback por IP: `localhost` pode resolver para outra coisa, e vários
    # servidores de autorização só aceitam 127.0.0.1 em redirect sem HTTPS.
    if partes.scheme != "http" or partes.hostname != "127.0.0.1" or not partes.port:
        raise AuthError("FINANCAS_REDIRECT_URI tem que ser http://127.0.0.1:<porta>/<caminho>")

    with httpx.Client(timeout=15) as cliente_http:
        issuer = descobrir_issuer(cliente_http, mcp_url)
        meta = descobrir_autorizacao(cliente_http, issuer)
        print(f"Servidor de autorizacao: {issuer}")

        client_id = config.financas_client_id
        client_secret: str | None = None
        if not client_id:
            if not meta.get("registration_endpoint"):
                raise AuthError(
                    "o servidor nao aceita registro dinamico de cliente. Cadastre um cliente "
                    f"OAuth publico com o redirect {redirect} e ponha o id em FINANCAS_CLIENT_ID."
                )
            registro = registrar_cliente(cliente_http, meta["registration_endpoint"], redirect)
            client_id = registro["client_id"]
            client_secret = registro.get("client_secret") or None
            print("Cliente registrado.")

        verifier, challenge = gerar_pkce()
        state = secrets.token_urlsafe(24)
        url = montar_url_autorizacao(
            meta["authorization_endpoint"],
            client_id=client_id,
            redirect_uri=redirect,
            state=state,
            code_challenge=challenge,
            resource=mcp_url,
            scope=config.financas_scope,
        )

        _Callback.caminho = partes.path or "/"
        _Callback.params = None
        try:
            server = http.server.HTTPServer(("127.0.0.1", partes.port), _Callback)
        except OSError as e:
            raise AuthError(
                f"a porta {partes.port} esta ocupada ({e}). Feche o que a usa, ou mude "
                "FINANCAS_REDIRECT_URI."
            ) from e
        try:
            print("\nAbra esta pagina para autorizar (voce precisa estar logado no app):\n")
            print(url, "\n")
            if abrir_navegador:
                webbrowser.open(url)
            codigo = conferir_retorno(_esperar_retorno(server), state)
        finally:
            server.server_close()

        payload = trocar_codigo(
            cliente_http,
            meta["token_endpoint"],
            code=codigo,
            code_verifier=verifier,
            client_id=client_id,
            redirect_uri=redirect,
            resource=mcp_url,
            client_secret=client_secret,
        )

    destino = Path(config.financas_token_path)
    salvar_token(
        destino,
        montar_arquivo_token(
            payload,
            token_endpoint=meta["token_endpoint"],
            client_id=client_id,
            resource=mcp_url,
            client_secret=client_secret,
        ),
    )
    return destino


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sem_navegador = "--sem-navegador" in argv

    if not config.financas_mcp_url:
        print("Falta FINANCAS_MCP_URL no .env: o endereco do servidor MCP do app de financas.")
        return 1

    if not sem_navegador and not tem_navegador(os.environ, sys.platform):
        print("Esta maquina nao tem navegador (sem DISPLAY): parece um servidor ou um conteiner.")
        print("Este script roda no seu computador, nao na VPS: o consentimento exige que voce")
        print("esteja logado no app, e o retorno volta para 127.0.0.1 de onde o navegador esta.")
        print("\nO que fazer: no computador, com o mesmo .env, rode")
        print("    python -m gateway.tools.financas_auth")
        print("e depois leve o arquivo gerado para o servidor com o comando que ele imprime.")
        print("\n(Se voce sabe o que esta fazendo -- por exemplo, um tunel SSH da porta do")
        print("redirect -- `--sem-navegador` so imprime a URL e espera o retorno.)")
        return 1

    try:
        destino = autorizar(abrir_navegador=not sem_navegador)
    except AuthError as e:
        print(f"\nNao deu: {e}")
        return 1
    except httpx.HTTPError as e:
        print(f"\nFalha de rede durante a autorizacao: {type(e).__name__}: {e}")
        return 1

    print(f"Pronto. Token guardado em {destino} (fora do git, modo 0600).")
    print("\nPara o gateway local, basta subir de novo. Para levar ao gateway em Docker,")
    print("rode na maquina onde ele esta, a partir da raiz do projeto, com o arquivo ja la:\n")
    print(comando_de_copia(destino))
    # O refresh token gira: quem renovar primeiro invalida a cópia do outro, e
    # um servidor que detecta reuso pode revogar tudo. Daí o aviso.
    print("\nUM gateway por arquivo: o token muda a cada renovacao, entao a copia que ficar")
    print("para tras para de valer. Nao use o mesmo arquivo no gateway local e no da VPS;")
    print("depois de copiar, apague a daqui. O arquivo da acesso as financas inteiras.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
