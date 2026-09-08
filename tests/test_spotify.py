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
    SCOPES,
    SPOTIFY_DISPATCH,
    SPOTIFY_TOOLS,
    SpotifyClient,
    SpotifyError,
    _parece_a_mesma,
    _tipo_falado,
    executar_spotify,
)

FAIXA = {
    "uri": "spotify:track:1",
    "name": "Construcao",
    "artists": [{"name": "Chico Buarque"}],
    "album": {"uri": "spotify:album:9", "name": "Construcao"},
}

ALBUM = {
    "uri": "spotify:album:9",
    "name": "Construcao",
    "artists": [{"name": "Chico Buarque"}],
}

ARTISTA = {"uri": "spotify:artist:7", "name": "Chico Buarque"}

PLAYLIST_PUBLICA = {"uri": "spotify:playlist:pub", "id": "pub", "name": "Esquenta Sertanejo"}
MINHA_PLAYLIST = {"uri": "spotify:playlist:eu", "id": "eu", "name": "Treino"}


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
        #: ultimo `type` mandado ao /search
        self.ultimo_tipo: str | None = None
        #: ultimo `limit` mandado ao /search -- pedir 1 traz item pior (D32)
        self.ultimo_limite: str | None = None
        #: por tipo de busca, o que o Spotify "tem". None = nao achou nada.
        self.catalogo: dict[str, list] = {
            "track": [FAIXA],
            "album": [ALBUM],
            "artist": [ARTISTA],
            "playlist": [PLAYLIST_PUBLICA],
        }
        #: as playlists da conta, devolvidas por /me/playlists
        self.minhas = [MINHA_PLAYLIST]
        #: estado do shuffle pedido, e o corpo do POST de criacao
        self.shuffle: str | None = None
        self.playlist_criada: dict | None = None
        self.itens_adicionados: list[str] = []

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
        if path.endswith("/me/player/shuffle"):
            self.shuffle = request.url.params.get("state")
            return httpx.Response(204)
        if path.endswith("/me/playlists"):
            if request.method == "POST":
                self.playlist_criada = json.loads(request.content)
                return httpx.Response(201, json={"id": "nova", "uri": "spotify:playlist:nova"})
            return httpx.Response(200, json={"items": self.minhas})
        if path.endswith("/items") and request.method == "POST":
            self.itens_adicionados += json.loads(request.content)["uris"]
            return httpx.Response(201, json={})
        if path.endswith("/search"):
            self.ultima_busca = request.url.params.get("q")
            self.ultimo_limite = request.url.params.get("limit")
            tipo = request.url.params.get("type")
            self.ultimo_tipo = tipo
            # `self.busca` continua mandando nas faixas, para os testes antigos.
            itens = self.busca if tipo == "track" else self.catalogo.get(tipo, [])
            return httpx.Response(200, json={f"{tipo}s": {"items": itens}})
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
    token_path.write_text(
        json.dumps({"refresh_token": "refresh-abc", "scope": SCOPES}), encoding="utf-8"
    )
    return api, SpotifyClient("id", "secret", token_path)


@pytest.fixture
def fake_token_antigo(fake, tmp_path):
    """O mesmo cliente, com o token de quem autorizou antes das playlists."""
    api, client = fake
    (tmp_path / "spotify_token.json").write_text(
        json.dumps({"refresh_token": "refresh-abc"}), encoding="utf-8"
    )
    return api, client


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


class TestTipoFalado:
    """A rede de segurança de quando o modelo não preenche `tipo`.

    Cada caso aqui é uma frase que, antes, virava busca por uma **faixa** com
    aquele nome inteiro -- e o Spotify sempre devolve alguma faixa.
    """

    @pytest.mark.parametrize(
        "frase, esperado",
        [
            ("minha playlist de treino", ("treino", "playlist")),
            ("a playlist esquenta", ("esquenta", "playlist")),
            ("o disco Clube da Esquina", ("Clube da Esquina", "album")),
            ("a banda Los Hermanos", ("Los Hermanos", "artista")),
            ("Construcao", ("Construcao", "musica")),
        ],
    )
    def test_deduz_o_tipo_da_frase(self, frase, esperado):
        assert _tipo_falado(frase, "musica") == esperado

    def test_a_escolha_do_modelo_ganha_da_deducao(self):
        # Ele viu a frase inteira; aqui só há substring.
        assert _tipo_falado("playlist", "artista") == ("playlist", "artista")

    def test_nao_come_preposicao_do_meio_do_nome(self):
        # O "de" só cai colado na palavra removida. Sem essa regra a limpeza
        # devolvia "Chico Buarque Hollanda".
        assert _tipo_falado("a banda Chico Buarque de Hollanda", "musica")[0] == (
            "Chico Buarque de Hollanda"
        )

    def test_artigo_depois_do_tipo_e_parte_do_nome(self):
        assert _tipo_falado("o disco A Noite", "musica") == ("A Noite", "album")


class TestTocarPorTipo:
    @pytest.mark.asyncio
    async def test_artista_toca_no_contexto_do_artista(self, fake):
        api, client = fake
        resposta = await client.tocar("Chico Buarque", "artista")
        assert api.ultimo_tipo == "artist"
        assert api.corpo_play == {"context_uri": "spotify:artist:7"}
        # `offset` só vale para álbum e playlist -- com artista o Spotify
        # responde 400. Este assert existe para que ninguém "unifique" os
        # corpos depois.
        assert "offset" not in api.corpo_play
        assert resposta == "Tocando Chico Buarque."

    @pytest.mark.asyncio
    async def test_album_comeca_do_inicio(self, fake):
        api, client = fake
        resposta = await client.tocar("Construcao", "album")
        assert api.corpo_play == {"context_uri": "spotify:album:9"}
        assert "album Construcao" in resposta

    @pytest.mark.asyncio
    async def test_musica_ainda_toca_no_contexto_do_album(self, fake):
        api, client = fake
        await client.tocar("Construcao", "musica")
        # O comportamento medido que não pode regredir: com `uris` a fila teria
        # um item só e o primeiro "próxima" acabava em silêncio.
        assert api.corpo_play == {
            "context_uri": "spotify:album:9",
            "offset": {"uri": "spotify:track:1"},
        }

    @pytest.mark.asyncio
    async def test_tipo_inventado_pelo_modelo_cai_no_padrao(self, fake):
        api, client = fake
        await executar_spotify(client, "tocar_musica", {"busca": "Construcao", "tipo": "faixa"})
        # `type=faixa` seria 400 do Spotify, e a pessoa ouviria um erro cru.
        assert api.ultimo_tipo == "track"

    @pytest.mark.asyncio
    async def test_nao_achou_diz_o_que_procurou(self, fake):
        api, client = fake
        api.catalogo["album"] = []
        # "Não achei nada" não diz se o nome está errado ou o tipo.
        assert await client.tocar("Xis", "album") == "Nao achei o album Xis no Spotify."


class TestPlaylists:
    @pytest.mark.asyncio
    async def test_a_playlist_da_conta_ganha_da_publica(self, fake):
        api, client = fake
        resposta = await client.tocar("treino", "playlist")
        # Existem mil playlists públicas chamadas "Treino"; a da pessoa é uma.
        assert api.corpo_play == {"context_uri": "spotify:playlist:eu"}
        assert "sua playlist Treino" in resposta

    @pytest.mark.asyncio
    async def test_cai_na_busca_publica_quando_nao_e_sua(self, fake):
        api, client = fake
        resposta = await client.tocar("Esquenta Sertanejo", "playlist")
        assert api.ultimo_tipo == "playlist"
        assert api.corpo_play == {"context_uri": "spotify:playlist:pub"}
        assert "a playlist Esquenta Sertanejo" in resposta

    @pytest.mark.asyncio
    async def test_playlist_comeca_embaralhada(self, fake):
        api, client = fake
        await client.tocar("treino", "playlist")
        assert api.shuffle == "true"

    @pytest.mark.asyncio
    async def test_shuffle_recusado_nao_impede_a_musica(self, fake):
        api, client = fake

        async def recusa(method, path, **kwargs):
            if path.endswith("/shuffle"):
                raise SpotifyError("nao deu")
            return await original(method, path, **kwargs)

        original = client._call
        client._call = recusa
        # Embaralhar é preferência; tocar é o pedido. Trocar um pelo outro seria
        # o aparelho se recusando a tocar por causa de um detalhe.
        assert "Tocando" in await client.tocar("treino", "playlist")

    @pytest.mark.asyncio
    async def test_playlist_que_nao_existe_e_dita_como_playlist(self, fake):
        api, client = fake
        api.minhas = []
        api.catalogo["playlist"] = []
        resposta = await client.tocar("treino", "playlist")
        # O bug antigo: isto tocava uma faixa qualquer chamada "treino" e
        # anunciava sucesso.
        assert resposta == "Nao achei nenhuma playlist chamada treino."

    @pytest.mark.asyncio
    async def test_busca_de_playlist_ignora_buracos_na_lista(self, fake):
        api, client = fake
        api.minhas = []
        api.catalogo["playlist"] = [None, PLAYLIST_PUBLICA]
        # A busca de playlist devolve posições nulas; `["uri"]` nelas explode.
        assert "Esquenta" in await client.tocar("Esquenta", "playlist")

    @pytest.mark.asyncio
    async def test_lista_longa_nao_e_lida_inteira(self, fake):
        api, client = fake
        api.minhas = [{"id": str(i), "uri": f"u{i}", "name": f"Lista {i}"} for i in range(40)]
        resposta = await client.listar_playlists()
        assert "40 playlists" in resposta
        assert "Lista 39" not in resposta


class TestCriarPlaylist:
    @pytest.mark.asyncio
    async def test_nasce_privada(self, fake):
        api, client = fake
        await client.criar_playlist("Domingo")
        # A API cria pública se ninguém disser nada. Publicar no perfil de
        # alguém por omissão não é um padrão que se escolheria.
        assert api.playlist_criada == {
            "name": "Domingo",
            "public": False,
            "description": "Criada pelo Ideraldinho.",
        }

    @pytest.mark.asyncio
    async def test_guarda_a_musica_que_esta_tocando(self, fake):
        api, client = fake
        resposta = await client.criar_playlist("Domingo", adicionar_atual=True)
        assert api.itens_adicionados == ["spotify:track:1"]
        assert "com Construcao" in resposta

    @pytest.mark.asyncio
    async def test_sem_nada_tocando_a_playlist_ainda_e_criada(self, fake):
        api, client = fake
        api.tocando = None
        resposta = await client.criar_playlist("Domingo", adicionar_atual=True)
        assert api.itens_adicionados == []
        assert "nao tinha nada tocando" in resposta

    @pytest.mark.asyncio
    async def test_sem_nome_nao_chama_a_api(self, fake):
        api, client = fake
        assert "falhou" in await executar_spotify(client, "criar_playlist", {"nome": " "})
        assert api.playlist_criada is None


class TestEscopoAntigo:
    """Quem autorizou antes das playlists tem token válido e escopo velho."""

    @pytest.mark.asyncio
    async def test_diz_que_precisa_reautorizar_em_vez_de_403(self, fake_token_antigo):
        _, client = fake_token_antigo
        resposta = await executar_spotify(client, "listar_playlists", {})
        # Sem isto viria um 403, que este código traduziria como "precisa de
        # Premium" -- dito em voz alta a quem já tem Premium.
        assert "autorizado de novo" in resposta

    @pytest.mark.asyncio
    async def test_tocar_musica_continua_funcionando(self, fake_token_antigo):
        _, client = fake_token_antigo
        assert "Tocando" in await client.tocar("Construcao", "musica")

    @pytest.mark.asyncio
    async def test_playlist_publica_ainda_toca(self, fake_token_antigo):
        api, client = fake_token_antigo
        # Ler as suas exige escopo; tocar uma pública, não. A falta de escopo não
        # pode derrubar o que não depende dele.
        assert "Esquenta Sertanejo" in await client.tocar("Esquenta Sertanejo", "playlist")

    def test_renovar_o_token_nao_apaga_o_escopo(self, fake, tmp_path):
        api, client = fake
        client._save_refresh("refresh-novo")
        dados = json.loads((tmp_path / "spotify_token.json").read_text(encoding="utf-8"))
        # Sobrescrever o arquivo inteiro faria a primeira renovação derrubar
        # todas as playlists, semanas depois e longe da causa.
        assert dados == {"refresh_token": "refresh-novo", "scope": SCOPES}


class TestOLimiteDaBusca:
    """Com `limit=1` o Spotify devolve um item diferente e pior.

    Medido com conta real em 08/09/2026: "Pink Floyd" com limit=1 voltava Guns
    N' Roses, e com limit=2 voltava Pink Floyd. O HTTP falso nao reproduz isso --
    aqui a lista e a que o teste escreveu --, entao o que da para travar e o
    parametro, que e onde estava o defeito.
    """

    @pytest.mark.asyncio
    async def test_pede_mais_de_um_resultado(self, fake):
        api, client = fake
        await client.tocar("Pink Floyd", "artista")
        assert api.ultimo_limite is not None
        assert int(api.ultimo_limite) > 1

    @pytest.mark.asyncio
    async def test_mas_so_o_primeiro_vai_ao_ar(self, fake):
        api, client = fake
        api.catalogo["artist"] = [
            {"uri": "spotify:artist:1", "name": "Pink Floyd"},
            {"uri": "spotify:artist:2", "name": "Guns N' Roses"},
        ]
        resposta = await client.tocar("Pink Floyd", "artista")
        # Um assistente de voz que oferece opcoes e pior que um que erra e
        # aceita "nao, a outra".
        assert "Pink Floyd" in resposta
        assert "Guns" not in resposta


class TestPlaylistQueNaoExiste:
    """A busca publica nunca devolve vazio, e por isso nao pode decidir sozinha.

    Com conta real, "playlist que nao existe 12345" voltou uma playlist chamada
    "123445". O aparelho diria "tocando a playlist 123445" -- errar calado.
    """

    @pytest.mark.asyncio
    async def test_nome_sem_relacao_e_recusado(self, fake):
        api, client = fake
        api.catalogo["playlist"] = [{"uri": "spotify:playlist:x", "id": "x", "name": "123445"}]
        resposta = await client.tocar("playlist que nao existe 12345", "playlist")
        assert "Nao achei" in resposta

    @pytest.mark.asyncio
    async def test_nome_parecido_ainda_toca(self, fake):
        api, client = fake
        api.catalogo["playlist"] = [
            {"uri": "spotify:playlist:x", "id": "x", "name": "Esquenta Sertanejo 2026"}
        ]
        # Recusar isto seria trocar um erro por outro: e a playlist certa.
        assert "Esquenta Sertanejo 2026" in await client.tocar("esquenta sertanejo", "playlist")

    @pytest.mark.asyncio
    async def test_a_sua_continua_ganhando_da_publica(self, fake):
        api, client = fake
        assert "sua playlist Treino" in await client.tocar("Treino", "playlist")


class TestPareceAMesma:
    @pytest.mark.parametrize(
        "pedido,achado,esperado",
        [
            ("treino", "treino", True),
            ("esquenta sertanejo", "esquenta sertanejo 2026", True),
            ("playlist que nao existe 12345", "123445", False),
            ("relaxa man", "relaxa", False),  # falta uma palavra de peso
            ("", "qualquer coisa", False),
            ("treino", "", False),
        ],
    )
    def test_criterio(self, pedido, achado, esperado):
        assert _parece_a_mesma(pedido, achado) is esperado
