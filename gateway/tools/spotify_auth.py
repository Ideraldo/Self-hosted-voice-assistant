"""Autorização do Spotify. Roda uma vez, no navegador:

    python -m gateway.tools.spotify_auth

Abre a página de consentimento, recebe o retorno num servidor local efêmero,
troca o código por um refresh token e guarda em disco. Depois disso o gateway se
vira sozinho -- o access token dura uma hora e é renovado sem ninguém ver.

O refresh token é a credencial de longo prazo: ele **não** vai para o git
(`.gitignore`), não desce para o dispositivo, e vive só no servidor. Se vazar,
revogue em spotify.com/account/apps.

Antes de rodar, no https://developer.spotify.com/dashboard:

1. *Create app*. Nome e descrição são livres.
2. Em *Redirect URIs*, adicione **exatamente** `http://127.0.0.1:8888/callback`.
   O Spotify exige `127.0.0.1` e recusa `localhost` desde 2025.
3. Copie Client ID e Client Secret para o `.env`.
"""

from __future__ import annotations

import base64
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

from gateway.config import config  # noqa: E402
from gateway.tools.spotify import SCOPES, TOKEN_URL  # noqa: E402

AUTH_URL = "https://accounts.spotify.com/authorize"


class _Callback(http.server.BaseHTTPRequestHandler):
    """Recebe o retorno do Spotify e nada mais."""

    code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802  (assinatura da stdlib)
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _Callback.code = (params.get("code") or [None])[0]
        _Callback.state = (params.get("state") or [None])[0]
        _Callback.error = (params.get("error") or [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        recado = "Pode fechar esta aba." if _Callback.code else f"Deu errado: {_Callback.error}"
        self.wfile.write(f"<html><body><h2>{recado}</h2></body></html>".encode())

    def log_message(self, *args: object) -> None:
        pass  # sem ruído no terminal


def main() -> int:
    if not config.spotify_client_id or not config.spotify_client_secret:
        print("Faltam SPOTIFY_CLIENT_ID e SPOTIFY_CLIENT_SECRET no .env.")
        print("Crie o app em https://developer.spotify.com/dashboard")
        return 1

    redirect = config.spotify_redirect_uri
    porta = urllib.parse.urlparse(redirect).port or 8888
    # O `state` protege contra alguém devolver um código que não foi você que
    # pediu. É barato e a especificação pede.
    state = secrets.token_urlsafe(16)

    server = http.server.HTTPServer(("127.0.0.1", porta), _Callback)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": config.spotify_client_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "scope": SCOPES,
            "state": state,
        }
    )
    print("Abrindo o navegador para autorizar. Se nao abrir, cole isto:\n")
    print(url, "\n")
    webbrowser.open(url)

    server.serve_forever_timeout = 300
    threading.Event().wait(1)
    while _Callback.code is None and _Callback.error is None:
        threading.Event().wait(0.5)

    if _Callback.error:
        print(f"O Spotify recusou: {_Callback.error}")
        return 1
    if _Callback.state != state:
        print("O `state` voltou diferente do que foi enviado; abortando.")
        return 1

    auth = base64.b64encode(
        f"{config.spotify_client_id}:{config.spotify_client_secret}".encode()
    ).decode()
    r = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _Callback.code,
            "redirect_uri": redirect,
        },
        headers={"Authorization": f"Basic {auth}"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"Troca do codigo falhou ({r.status_code}): {r.text[:200]}")
        return 1

    payload = r.json()
    token = payload.get("refresh_token")
    if not token:
        print("O Spotify nao devolveu refresh_token.")
        return 1

    # O escopo vai para o disco junto com o token. Ele fica congelado no momento
    # da autorização, e sem esse registro não há como o gateway distinguir "essa
    # conta não pode" de "esse token é velho" -- ele veria só um 403 e diria à
    # pessoa que ela precisa de Premium, que ela já tem.
    destino = Path(config.spotify_token_path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps({"refresh_token": token, "scope": payload.get("scope") or SCOPES}, indent=2),
        encoding="utf-8",
    )
    print(f"Pronto. Token guardado em {destino} (fora do git).")
    print("Suba o gateway de novo para ele carregar as ferramentas do Spotify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
