"""Spotify sem Spotify: o cliente contra um HTTP falso.

Não há conta nem rede aqui. O que se testa é o que costuma quebrar de verdade
num cliente de API: o token que vence, o 403 do Premium, o 204 sem corpo, e o
aparelho que ninguém abriu. São exatamente os casos que só aparecem em uso, e
sempre no pior momento.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from gateway.tools.spotify import (
    SPOTIFY_DISPATCH,
    SPOTIFY_TOOLS,
    SpotifyClient,
    SpotifyError,
    executar_spotify,
)

FAIXA = {
    "uri": "spotify:track:1",
    "name": "Construcao",
    "artists": [{"name": "Chico Buarque"}],
    "album": {"uri": "spotify:album:9", "name": "Construcao"},
}


class FakeSpotify:
    """Um Spotify de mentira. Registra o que recebeu, para o teste conferir."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str]] = []
        self.devices = [{"id": "dev1", "is_active": True, "name": "Quarto"}]
        self.busca = [FAIXA]
        self.tocando = FAIXA
        self.status = 200
        self.erro_body: dict | None = None
        self.tokens_emitidos = 0
        #: corpo do ultimo PUT /play, para conferir COMO mandamos tocar
        self.corpo_play: dict = {}
        #: ultimo `q` mandado ao /search, para conferir o que foi procurado
        self.ultima_busca: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.chamadas.append((request.method, path))

        if "accounts.spotify.com" in request.url.host:
            self.tokens_emitidos += 1
            return httpx.Response(
                200,
                json={"access_token": f"tok{self.tokens_emitidos}", "expires_in": 3600},
            )
        if path.endswith("/me/player/play") and request.content:
            self.corpo_play = json.loads(request.content)
        if self.status != 200:
            return httpx.Response(self.status, json=self.erro_body or {"error": {}})
        if path.endswith("/me/player/devices"):
            return httpx.Response(200, json={"devices": self.devices})
        if path.endswith("/search"):
            self.ultima_busca = request.url.params.get("q")
            return httpx.Response(200, json={"tracks": {"items": self.busca}})
        if path.endswith("/currently-playing"):
            if self.tocando is None:
                return httpx.Response(204)
            return httpx.Response(200, json={"item": self.tocando})
        return httpx.Response(200, json={})


@pytest.fixture
def fake(monkeypatch, tmp_path):
    api = FakeSpotify()
    transport = httpx.MockTransport(api.handler)
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)

    token_path = tmp_path / "spotify_token.json"
    token_path.write_text(json.dumps({"refresh_token": "refresh-abc"}), encoding="utf-8")
    return api, SpotifyClient("id", "secret", token_path)


class TestAutorizacao:
    def test_sem_token_em_disco_nao_esta_autorizado(self, tmp_path):
        assert SpotifyClient("id", "secret", tmp_path / "nao-existe.json").authorized is False

    def test_com_token_esta_autorizado(self, fake):
        _, client = fake
        assert client.authorized is True

    @pytest.mark.asyncio
    async def test_reaproveita_o_access_token(self, fake):
        api, client = fake
        await client.pausar()
        await client.pausar()
        # Duas ações, um único token pedido: renovar a cada chamada seria uma
        # ida de rede a mais em todo comando de voz.
        assert api.tokens_emitidos == 1

    @pytest.mark.asyncio
    async def test_renova_quando_o_token_vence(self, fake):
        api, client = fake
        await client.pausar()
        client._expires_at = time.time()  # vencido agora
        await client.pausar()
        assert api.tokens_emitidos == 2


class TestTocar:
    @pytest.mark.asyncio
    async def test_busca_e_toca(self, fake):
        api, client = fake
        assert await client.tocar("construcao") == "Tocando Construcao, de Chico Buarque."
        assert ("GET", "/v1/search") in api.chamadas
        assert ("PUT", "/v1/me/player/play") in api.chamadas

    @pytest.mark.asyncio
    async def test_toca_no_contexto_do_album(self, fake):
        # Com `uris` a fila tem um item so, e o primeiro "proxima" acaba com a
        # musica em silencio -- aconteceu de verdade, com o aparelho ainda
        # dizendo "Proxima". Com o album como contexto, pedir uma musica comeca
        # nela e segue no disco.
        api, client = fake
        await client.tocar("construcao")
        assert api.corpo_play.get("context_uri") == "spotify:album:9"
        assert api.corpo_play.get("offset") == {"uri": "spotify:track:1"}
        assert "uris" not in api.corpo_play

    @pytest.mark.asyncio
    async def test_sem_album_cai_para_faixa_solta(self, fake):
        # Podcast e episodio nao tem album; a busca ainda pode devolver algo sem
        # esse campo, e ai tocar a faixa avulsa e melhor que nao tocar nada.
        api, client = fake
        api.busca = [{"uri": "spotify:track:2", "name": "X", "artists": [{"name": "Y"}]}]
        assert await client.tocar("x") == "Tocando X, de Y."
        assert api.corpo_play.get("uris") == ["spotify:track:2"]

    @pytest.mark.asyncio
    async def test_busca_sem_resultado_nao_toca_nada(self, fake):
        api, client = fake
        api.busca = []
        resposta = await client.tocar("musica que nao existe")
        assert "Nao achei" in resposta
        assert ("PUT", "/v1/me/player/play") not in api.chamadas

    @pytest.mark.asyncio
    async def test_sem_aparelho_aberto(self, fake):
        api, client = fake
        api.devices = []
        with pytest.raises(SpotifyError, match="abra o Spotify"):
            await client.tocar("construcao")

    @pytest.mark.asyncio
    async def test_prefere_o_aparelho_ativo(self, fake):
        api, client = fake
        api.devices = [
            {"id": "parado", "is_active": False},
            {"id": "tocando", "is_active": True},
        ]
        assert await client._device_id() == "tocando"

    @pytest.mark.asyncio
    async def test_sem_nenhum_ativo_usa_o_primeiro(self, fake):
        api, client = fake
        api.devices = [{"id": "a", "is_active": False}, {"id": "b", "is_active": False}]
        assert await client._device_id() == "a"


class TestEscolhaDeAparelho:
    """Onde tocar quando ninguem diz onde -- e o que faz o Ideraldinho ser a caixa de
    som em vez de um controle remoto do PC."""

    @pytest.mark.asyncio
    async def test_preferido_ganha_do_ativo(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": True, "name": "RUIPC"},
            {"id": "pi", "is_active": False, "name": "Ideraldinho"},
        ]
        client._preferido = "Ideraldinho"
        assert await client._device_id() == "pi"

    @pytest.mark.asyncio
    async def test_sem_o_preferido_na_lista_cai_para_o_ativo(self, fake):
        # O caso de hoje: a Pi ainda nao existe. O comportamento tem que ser o
        # de antes, e nao um erro.
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": False, "name": "RUIPC"},
            {"id": "echo", "is_active": True, "name": "Echo Dot de Ideraldo"},
        ]
        client._preferido = "Ideraldinho"
        assert await client._device_id() == "echo"

    @pytest.mark.asyncio
    async def test_o_que_a_pessoa_pediu_ganha_do_preferido(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pi", "is_active": True, "name": "Ideraldinho"},
            {"id": "echo", "is_active": False, "name": "Echo Dot de Ideraldo"},
        ]
        client._preferido = "Ideraldinho"
        assert await client._device_id("echo dot") == "echo"

    @pytest.mark.asyncio
    async def test_casa_por_trecho_e_sem_acento(self, fake):
        api, client = fake
        api.devices = [{"id": "sala", "is_active": False, "name": "Caixa da Sala"}]
        assert await client._device_id("sala") == "sala"
        assert await client._device_id("CAIXA") == "sala"

    @pytest.mark.asyncio
    async def test_nome_exato_ganha_de_trecho(self, fake):
        api, client = fake
        api.devices = [
            {"id": "quarto", "is_active": False, "name": "Ideraldinho (quarto)"},
            {"id": "pi", "is_active": False, "name": "Ideraldinho"},
        ]
        assert await client._device_id("Ideraldinho") == "pi"

    @pytest.mark.asyncio
    async def test_aparelho_inexistente_diz_quais_existem(self, fake):
        api, client = fake
        api.devices = [{"id": "pc", "is_active": True, "name": "RUIPC"}]
        with pytest.raises(SpotifyError, match="RUIPC"):
            await client._device_id("geladeira")

    @pytest.mark.asyncio
    async def test_tocar_no_aparelho_pedido(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pi", "is_active": True, "name": "Ideraldinho"},
            {"id": "echo", "is_active": False, "name": "Echo Dot de Ideraldo"},
        ]
        await executar_spotify(client, "tocar_musica", {"busca": "x", "aparelho": "echo"})
        assert ("PUT", "/v1/me/player/play") in api.chamadas


class TestFalarOTipoEmVezDoNome:
    """"Toca no celular", e nao "toca no iPhone".

    Sem isto, "celular" nao casava com nada, caia no fallback e virava parte da
    busca: pedir Construcao no celular tocou a versao do Ney Matogrosso.
    """

    @pytest.fixture
    def parque(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": True, "name": "RUIPC", "type": "Computer"},
            {"id": "fone", "is_active": False, "name": "iPhone", "type": "Smartphone"},
            {"id": "echo", "is_active": False, "name": "Echo Dot de Ideraldo", "type": "Speaker"},
        ]
        return api, client

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "falado, esperado",
        [
            ("celular", "fone"),
            ("telefone", "fone"),
            ("computador", "pc"),
            ("pc", "pc"),
            ("notebook", "pc"),
            ("caixa de som", "echo"),
            ("caixinha", "echo"),
            ("televisao", None),  # nao existe TV no parque
        ],
    )
    async def test_casa_pelo_tipo(self, parque, falado, esperado):
        api, client = parque
        if esperado is None:
            with pytest.raises(SpotifyError):
                await client._device_id(falado)
        else:
            assert await client._device_id(falado) == esperado

    @pytest.mark.asyncio
    async def test_nome_ganha_do_tipo(self, fake):
        # Se alguem batizou a caixa de som de "Computador", o nome que a pessoa
        # deu vale mais que o rotulo da API.
        api, client = fake
        api.devices = [
            {"id": "verdadeiro", "is_active": True, "name": "RUIPC", "type": "Computer"},
            {"id": "batizado", "is_active": False, "name": "Computador", "type": "Speaker"},
        ]
        assert await client._device_id("computador") == "batizado"

    @pytest.mark.asyncio
    async def test_entre_dois_do_mesmo_tipo_prefere_o_ativo(self, fake):
        api, client = fake
        api.devices = [
            {"id": "parado", "is_active": False, "name": "PC velho", "type": "Computer"},
            {"id": "tocando", "is_active": True, "name": "RUIPC", "type": "Computer"},
        ]
        assert await client._device_id("computador") == "tocando"

    @pytest.mark.asyncio
    async def test_tocar_no_celular_nao_suja_a_busca(self, parque):
        api, client = parque
        await client.tocar("Construcao Chico Buarque", aparelho="celular")
        assert api.ultima_busca == "Construcao Chico Buarque"


class TestQuandoOModeloConfundeOsCampos:
    """`busca` e `aparelho` sao dois campos de texto livre lado a lado, e o
    modelo divide errado: "toca Construcao do Chico Buarque" virou
    {busca: "Construcao", aparelho: "Chico Buarque"} -- e tocou a musica errada.
    """

    @pytest.mark.asyncio
    async def test_aparelho_que_nao_existe_volta_para_a_busca(self, fake):
        api, client = fake
        api.devices = [{"id": "pc", "is_active": True, "name": "RUIPC"}]
        await client.tocar("Construcao", aparelho="Chico Buarque")
        busca = [c for c in api.chamadas if c[1].endswith("/search")]
        assert busca, "deveria ter buscado"
        # a busca precisa ter recebido as duas partes
        assert api.ultima_busca == "Construcao Chico Buarque"

    @pytest.mark.asyncio
    async def test_nao_duplica_o_que_ja_estava_na_busca(self, fake):
        api, client = fake
        api.devices = [{"id": "pc", "is_active": True, "name": "RUIPC"}]
        await client.tocar("Construcao Chico Buarque", aparelho="chico buarque")
        assert api.ultima_busca == "Construcao Chico Buarque"

    @pytest.mark.asyncio
    async def test_o_preferido_desligado_avisa_em_vez_de_buscar(self, fake):
        # "toca no ideraldinho" com a Pi desligada nao pode virar uma busca por
        # "Construcao Ideraldinho" -- foi o que aconteceu, e tocou outra versao.
        api, client = fake
        api.devices = [{"id": "pc", "is_active": True, "name": "RUIPC", "type": "Computer"}]
        client._preferido = "Ideraldinho"
        with pytest.raises(SpotifyError, match="nao achei"):
            await client.tocar("Construcao", aparelho="ideraldinho")
        assert api.ultima_busca is None  # nem chegou a buscar

    @pytest.mark.asyncio
    async def test_tipo_ausente_avisa_em_vez_de_buscar(self, fake):
        api, client = fake
        api.devices = [{"id": "pc", "is_active": True, "name": "RUIPC", "type": "Computer"}]
        with pytest.raises(SpotifyError, match="nao achei"):
            await client.tocar("Construcao", aparelho="caixa de som")

    @pytest.mark.asyncio
    async def test_aparelho_de_verdade_continua_valendo(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": True, "name": "RUIPC"},
            {"id": "echo", "is_active": False, "name": "Echo Dot de Ideraldo"},
        ]
        await client.tocar("Construcao", aparelho="echo")
        assert api.ultima_busca == "Construcao"


class TestTrocarDeAparelho:
    @pytest.mark.asyncio
    async def test_transfere_tocando(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": True, "name": "RUIPC"},
            {"id": "echo", "is_active": False, "name": "Echo Dot de Ideraldo"},
        ]
        assert "Echo Dot" in await client.trocar_aparelho("echo")
        assert ("PUT", "/v1/me/player") in api.chamadas

    @pytest.mark.asyncio
    async def test_sem_dizer_para_onde(self, fake):
        _, client = fake
        assert "falhou" in await executar_spotify(client, "trocar_aparelho", {})

    @pytest.mark.asyncio
    async def test_listar_marca_o_que_esta_tocando(self, fake):
        api, client = fake
        api.devices = [
            {"id": "pc", "is_active": True, "name": "RUIPC"},
            {"id": "echo", "is_active": False, "name": "Echo Dot"},
        ]
        resposta = await client.listar_aparelhos()
        assert "RUIPC (tocando agora)" in resposta
        assert "Echo Dot" in resposta


class TestTocandoAgora:
    @pytest.mark.asyncio
    async def test_diz_o_que_toca(self, fake):
        _, client = fake
        assert await client.tocando_agora() == "Construcao, de Chico Buarque."

    @pytest.mark.asyncio
    async def test_204_sem_corpo_nao_explode(self, fake):
        # O 204 é o caso que derruba cliente ingênuo: `r.json()` sem corpo.
        api, client = fake
        api.tocando = None
        assert await client.tocando_agora() == "Nao tem nada tocando."


class TestOs403QueSignificamCoisasDiferentes:
    """O Spotify usa 403 para falta de Premium **e** para comando invalido.

    Isto nasceu errado: todo 403 virava "precisa de Premium". A primeira
    execucao com conta real pausou o que ja estava pausado, levou 403
    "Restriction violated", e o aparelho disse a quem tem Premium que precisava
    comprar Premium.
    """

    @pytest.mark.asyncio
    async def test_premium_pelo_reason(self, fake):
        api, client = fake
        api.status = 403
        api.erro_body = {"error": {"status": 403, "reason": "PREMIUM_REQUIRED", "message": "x"}}
        with pytest.raises(SpotifyError, match="Premium"):
            await client.pausar()

    @pytest.mark.asyncio
    async def test_premium_pela_mensagem(self, fake):
        api, client = fake
        api.status = 403
        api.erro_body = {"error": {"status": 403, "message": "Premium required"}}
        with pytest.raises(SpotifyError, match="Premium"):
            await client.pausar()

    @pytest.mark.asyncio
    async def test_restricao_nao_fala_em_premium(self, fake):
        # O caso real: pausar o que ja esta pausado.
        api, client = fake
        api.status = 403
        api.erro_body = {
            "error": {
                "status": 403,
                "message": "Player command failed: Restriction violated",
                "reason": "UNKNOWN",
            }
        }
        with pytest.raises(SpotifyError) as exc:
            await client.pausar()
        assert "premium" not in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_sem_aparelho_ativo_pelo_reason(self, fake):
        api, client = fake
        api.status = 403
        api.erro_body = {"error": {"status": 403, "reason": "NO_ACTIVE_DEVICE"}}
        with pytest.raises(SpotifyError, match="abra o Spotify"):
            await client.pausar()

    @pytest.mark.asyncio
    async def test_403_sem_corpo_util_nao_inventa_motivo(self, fake):
        api, client = fake
        api.status = 403
        api.erro_body = {"error": {}}
        with pytest.raises(SpotifyError, match="recusou o comando"):
            await client.pausar()


class TestErrosQueOUsuarioOuve:
    @pytest.mark.asyncio
    async def test_429_pede_para_esperar(self, fake):
        api, client = fake
        api.status = 429
        with pytest.raises(SpotifyError, match="esperar"):
            await client.pausar()

    @pytest.mark.asyncio
    async def test_erro_nunca_sobe_como_excecao_pelo_despacho(self, fake):
        api, client = fake
        api.status = 403
        api.erro_body = {"error": {"reason": "PREMIUM_REQUIRED"}}
        resposta = await executar_spotify(client, "pausar_musica", {})
        # Vira frase, e em português: o turno de voz não pode morrer aqui.
        assert "premium" in resposta.lower()
        assert resposta.endswith(".")

    @pytest.mark.asyncio
    async def test_ferramenta_inventada(self, fake):
        _, client = fake
        assert "desconhecida" in await executar_spotify(client, "tocar_video", {})

    @pytest.mark.asyncio
    async def test_tocar_sem_dizer_o_que(self, fake):
        _, client = fake
        assert "falhou" in await executar_spotify(client, "tocar_musica", {"busca": "  "})


class TestDeclaracao:
    def test_toda_ferramenta_declarada_tem_despacho(self):
        assert {t.name for t in SPOTIFY_TOOLS} == set(SPOTIFY_DISPATCH)

    def test_descricoes_dizem_quando_nao_usar(self):
        # "toca um alarme pra mim" não pode virar busca no Spotify.
        tocar = next(t for t in SPOTIFY_TOOLS if t.name == "tocar_musica")
        assert "alarme" in tocar.description.lower()

    def test_despacho_nao_expoe_metodo_arbitrario(self):
        # Se o despacho fosse getattr(client, nome), um nome inventado pelo
        # modelo viraria chamada de método qualquer do cliente.
        assert "_access_token" not in SPOTIFY_DISPATCH.values()
        assert "_save_refresh" not in SPOTIFY_DISPATCH.values()


class TestSessaoSemSpotify:
    def test_ferramentas_de_musica_nao_sao_declaradas(self):
        from gateway.api.session import Session

        sessao = Session(websocket=None, llm=None, expected_token="x", spotify=None)
        nomes = {t.name for t in sessao._tools}
        assert "tocar_musica" not in nomes
        assert "criar_timer" in nomes  # as do dispositivo continuam

    def test_com_spotify_as_duas_familias_aparecem(self, fake):
        from gateway.api.session import Session

        _, client = fake
        sessao = Session(websocket=None, llm=None, expected_token="x", spotify=client)
        nomes = {t.name for t in sessao._tools}
        assert {"tocar_musica", "criar_timer"} <= nomes
