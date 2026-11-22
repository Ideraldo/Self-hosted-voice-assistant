"""Gateway process entrypoint: uvicorn gateway.main:app"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()  # before the config dataclasses read the environment

from fastapi import FastAPI, WebSocket  # noqa: E402

from gateway.api.session import Session  # noqa: E402
from gateway.config import LLMConfig, config  # noqa: E402
from gateway.llm.base import LLMProvider  # noqa: E402
from gateway.llm.fallback import FallbackProvider  # noqa: E402
from gateway.llm.ollama import OllamaProvider  # noqa: E402
from gateway.llm.openai_compat import OpenAICompatProvider  # noqa: E402
from gateway.tools import (  # noqa: E402
    Brave,
    DuckDuckGo,
    Leitor,
    SearchProvider,
    SpotifyClient,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="Ideraldinho gateway")


def descrever_llm(c: LLMConfig) -> str:
    """Quem responde, para o log e o `/health`: provedor, modelo e host.

    O host entra porque `openai-compat` sozinho não diz com quem se fala. Sai
    só o nome da máquina, nunca a URL inteira nem a chave: há quem ponha
    credencial no endereço.
    """
    if c.provider == "ollama":
        return f"ollama:{c.model}"
    return f"{c.provider}:{c.model}@{urlsplit(c.base_url or '').hostname}"


def _um_llm(c: LLMConfig) -> LLMProvider:
    if c.provider == "ollama":
        return OllamaProvider(
            base_url=c.base_url or config.ollama_url,
            model=c.model,
            timeout=config.llm_timeout,
            think=config.llm_think,
        )
    if c.provider == "openai-compat":
        if not c.base_url:
            raise ValueError("LLM_PROVIDER=openai-compat precisa de LLM_BASE_URL")
        return OpenAICompatProvider(
            base_url=c.base_url,
            model=c.model,
            api_key=c.api_key,
            timeout=config.llm_timeout,
            reasoning_effort=c.reasoning_effort,
            temperature=c.temperature,
            extra_body=c.extra_body,
        )
    raise ValueError(f"unknown LLM_PROVIDER {c.provider!r}")


def build_llm(
    principal: LLMConfig = config.llm, reserva: LLMConfig | None = config.llm_fallback
) -> LLMProvider:
    """The only place that knows which provider is active (plan section 6)."""
    if reserva is None:
        return _um_llm(principal)
    return FallbackProvider(
        [(descrever_llm(c), _um_llm(c)) for c in (principal, reserva)],
        primeiro_delta=config.llm_first_delta_timeout,
    )


def build_spotify() -> SpotifyClient | None:
    """O cliente do Spotify, ou None se não houver como usá-lo.

    Duas condições, e as duas importam: credenciais no `.env` **e** o refresh
    token em disco (`python -m gateway.tools.spotify_auth`). Faltando qualquer
    uma, as ferramentas de música não são declaradas ao modelo -- oferecer uma
    ferramenta que vai falhar é pior que não ter ferramenta.
    """
    if not (config.spotify_client_id and config.spotify_client_secret):
        logging.info("spotify: sem credenciais no .env; ferramentas de musica desligadas")
        return None
    client = SpotifyClient(
        client_id=config.spotify_client_id,
        client_secret=config.spotify_client_secret,
        token_path=config.spotify_token_path,
        market=config.spotify_market,
        preferido=config.spotify_device,
    )
    if not client.authorized:
        logging.warning(
            "spotify: falta autorizar -- rode `python -m gateway.tools.spotify_auth`"
        )
        return None
    logging.info("spotify: ferramentas de musica ligadas")
    return client


def build_search() -> SearchProvider | None:
    """O provedor de busca, ou None se não houver como usá-lo.

    O padrão (`duckduckgo`) não precisa de nada: é o que garante que a
    fundamentação funcione numa instalação limpa. `brave` precisa de chave, e
    sem ela a ferramenta é desligada em vez de falhar em uso -- oferecer uma
    ferramenta que vai dar erro é pior que não ter ferramenta (D19).
    """
    escolhido = config.search_provider.lower()
    if escolhido in ("none", "off", ""):
        logging.info("busca: desligada por configuracao")
        return None
    if escolhido == "brave":
        if not config.search_api_key:
            logging.warning("busca: SEARCH_PROVIDER=brave sem SEARCH_API_KEY; desligada")
            return None
        logging.info("busca: brave")
        return Brave(api_key=config.search_api_key)
    if escolhido == "duckduckgo":
        try:
            import ddgs  # noqa: F401
        except ImportError:
            logging.warning("busca: falta `pip install ddgs`; desligada")
            return None
        logging.info("busca: duckduckgo")
        return DuckDuckGo(regiao=config.search_region)
    logging.warning("busca: SEARCH_PROVIDER=%r desconhecido; desligada", escolhido)
    return None


def build_leitor() -> Leitor | None:
    """O leitor da primeira página, ou None se ninguém pediu.

    Separado do provedor de propósito: ler a página é ortogonal a quem buscou, e
    quem precisar cortar segundos do turno desliga a leitura sem perder a busca.
    """
    if not config.search_read_page:
        logging.info("busca: leitura da primeira pagina desligada")
        return None
    logging.info("busca: leitura da primeira pagina ligada")
    return Leitor()


# Um provedor para o processo inteiro, e não um por conexão: configuração errada
# aparece na subida em vez de no primeiro "oi", a conexão HTTP com a API é
# reaproveitada, e o reserva lembra entre uma sessão e outra que o principal
# caiu há pouco.
llm = build_llm()
logging.info(
    "llm: %s", " -> ".join(descrever_llm(c) for c in (config.llm, config.llm_fallback) if c)
)
spotify = build_spotify()
search = build_search()
leitor = build_leitor() if search is not None else None


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "llm": descrever_llm(config.llm),
        "llm_reserva": descrever_llm(config.llm_fallback) if config.llm_fallback else "off",
        "spotify": "on" if spotify is not None else "off",
        "busca": getattr(search, "nome", "off"),
        "leitura": "on" if leitor is not None else "off",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    session = Session(
        websocket=websocket,
        llm=llm,
        expected_token=config.device_token,
        spotify=spotify,
        search=search,
        leitor=leitor,
        # Com reserva, quem tenta de novo é o `FallbackProvider`, no outro
        # provedor; repetir a fila inteira aqui pagaria as duas esperas em dobro.
        llm_retries=config.llm_retries if config.llm_fallback is None else 0,
    )
    await session.run()
