"""Gateway configuration. Secrets live here and never leave the VPS."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    """Um provedor de LLM: o principal (`LLM_*`) ou o reserva (`LLM_FALLBACK_*`)."""

    provider: str  # "ollama" | "openai-compat"
    model: str
    # Para `ollama`, vazio cai em OLLAMA_URL. Para `openai-compat` e obrigatorio
    # e vai ate o `/v1` (ou equivalente): o cliente so acrescenta
    # `/chat/completions`.
    base_url: str | None
    api_key: str | None
    # Os tres abaixo so entram no pedido quando preenchidos, e so valem para
    # `openai-compat`. Cada provedor desliga o raciocinio com um campo e um
    # valor diferentes, e varios devolvem 400 para campo que nao conhecem --
    # por isso nao ha padrao: quem escolhe o provedor escolhe o valor (D20).
    reasoning_effort: str | None
    temperature: float | None
    # Um objeto JSON somado ao corpo do pedido, para o que nao tem variavel
    # propria: `{"parallel_tool_calls": false}`, `{"reasoning_format": "hidden"}`.
    extra_body: dict[str, Any]


def _llm(prefixo: str) -> LLMConfig:
    """Le um provedor do ambiente. O mesmo codigo serve ao principal e ao reserva."""

    def ler(nome: str) -> str | None:
        return os.getenv(prefixo + nome) or None

    temperatura = ler("TEMPERATURE")
    return LLMConfig(
        provider=(ler("PROVIDER") or "ollama").lower(),
        model=ler("MODEL") or "qwen3:8b",
        base_url=ler("BASE_URL"),
        api_key=ler("API_KEY"),
        reasoning_effort=ler("REASONING_EFFORT"),
        temperature=float(temperatura) if temperatura else None,
        extra_body=json.loads(ler("EXTRA_BODY") or "{}"),
    )


@dataclass(frozen=True)
class GatewayConfig:
    host: str = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port: int = int(os.getenv("GATEWAY_PORT", "8000"))
    device_token: str = os.getenv("DEVICE_TOKEN", "dev-token")
    # O modelo principal e, se houver, o reserva -- quem responde quando o
    # principal nao responde. O caso de uso e o da VPS: principal numa API de
    # nuvem, reserva no Ollama de casa por tunel. Sem LLM_FALLBACK_PROVIDER nao
    # ha reserva, e o padrao continua sendo um Ollama local sozinho.
    llm: LLMConfig = _llm("LLM_")
    llm_fallback: LLMConfig | None = (
        _llm("LLM_FALLBACK_") if os.getenv("LLM_FALLBACK_PROVIDER") else None
    )
    # Quanto esperar o principal comecar a responder antes de passar ao
    # reserva. Nao e o LLM_TIMEOUT: aquele pergunta se o modelo morreu, este se
    # a pessoa ainda esta esperando. So tem efeito com reserva configurado.
    llm_first_delta_timeout: float = float(os.getenv("LLM_FIRST_DELTA_TIMEOUT", "8"))
    # Quantas vezes repetir o pedido ao LLM que falhou depressa, antes de
    # comecar a responder (conexao derrubada, 5xx). O padrao e 1: a conexao
    # ociosa que o provedor fechou entre duas perguntas e a falha mais comum de
    # um aparelho que fala de vez em quando, e a segunda tentativa custa ~0,3 s
    # contra a pessoa ter que repetir a frase inteira. Mais que 1 nao cabe num
    # turno falado. 0 desliga. So vale sem reserva: com LLM_FALLBACK_PROVIDER,
    # a retentativa e o reserva.
    llm_retries: int = int(os.getenv("LLM_RETRIES", "1"))
    # Raciocinar antes de responder custa latencia e pode vazar para a fala.
    # Medido em D20: com isso ligado o turno passa de 2,8s para 10,3s.
    llm_think: bool = os.getenv("LLM_THINK", "false").lower() in ("1", "true", "sim")
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    # Um modelo local sem GPU livre responde em minutos, nao em segundos. O
    # limite existe para nao pendurar a sessao para sempre, mas ele precisa ser
    # ajustavel: o valor certo depende de onde o modelo esta rodando.
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "120"))
    # Spotify: segredos moram aqui e nunca descem para o dispositivo (plano,
    # secao 3). Sem eles, as ferramentas de musica simplesmente nao existem --
    # o modelo nao ve o que nao pode usar.
    spotify_client_id: str | None = os.getenv("SPOTIFY_CLIENT_ID")
    spotify_client_secret: str | None = os.getenv("SPOTIFY_CLIENT_SECRET")
    # 127.0.0.1 e nao localhost: o Spotify recusa `localhost` desde 2025.
    spotify_redirect_uri: str = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
    spotify_token_path: str = os.getenv("SPOTIFY_TOKEN_PATH", "gateway/data/spotify_token.json")
    spotify_market: str = os.getenv("SPOTIFY_MARKET", "BR")
    # Onde tocar quando ninguem disser onde. O padrao aponta para a propria Pi
    # rodando raspotify/librespot: e o que faz "toca Chico Buarque" sair no
    # alto-falante do Ideraldinho, como sairia numa Alexa, em vez de num PC que pode
    # estar em outro comodo. Enquanto a Pi nao existe, o nome nao casa com nada
    # e a escolha cai para o aparelho ativo, como antes.
    spotify_device: str = os.getenv("SPOTIFY_DEVICE", "Ideraldinho")
    # Busca na internet. O padrao nao exige chave nem conta: e a resposta a um
    # modelo de 8B que alucina (D20), e cobrar cadastro para isso funcionar
    # seria trocar um problema por outro. `brave` existe para quem quiser
    # resultado mais estavel -- ai sim com chave.
    search_provider: str = os.getenv("SEARCH_PROVIDER", "duckduckgo")
    search_api_key: str | None = os.getenv("SEARCH_API_KEY")
    search_region: str = os.getenv("SEARCH_REGION", "br-pt")
    # Abrir o primeiro resultado e ler o texto dele, alem do trecho do
    # buscador. **Desligado por padrao, e o motivo e medicao, nao preguica**
    # (D28): em 4 perguntas medidas em 03/09/2026 o trecho ja respondia todas,
    # e a pagina custou de 1 a 3 s num turno que ja e o mais lento do aparelho.
    # A leitura funciona e esta testada; falta a pergunta que a justifique.
    # Ligar e SEARCH_READ_PAGE=1.
    search_read_page: bool = os.getenv("SEARCH_READ_PAGE", "0").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    # App de financas, por MCP (gateway/tools/mcp_client.py). Tudo vazio por
    # padrao: sem a URL ou sem o arquivo de token o cliente nao e construido e
    # nenhuma ferramenta de dinheiro e declarada -- como a musica sem Spotify.
    # O token nasce de `python -m gateway.tools.financas_auth`, no computador.
    # CLIENT_ID so para servidor sem registro dinamico; SCOPE so se ele exigir.
    financas_mcp_url: str = os.getenv("FINANCAS_MCP_URL", "")
    financas_token_path: str = os.getenv("FINANCAS_TOKEN_PATH", "gateway/data/financas_token.json")
    financas_client_id: str = os.getenv("FINANCAS_CLIENT_ID", "")
    financas_scope: str = os.getenv("FINANCAS_SCOPE", "")
    financas_redirect_uri: str = os.getenv(
        "FINANCAS_REDIRECT_URI", "http://127.0.0.1:8765/callback"
    )
    stt_api_key: str | None = os.getenv("STT_API_KEY")
    tts_api_key: str | None = os.getenv("TTS_API_KEY")


config = GatewayConfig()
