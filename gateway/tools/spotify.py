"""Spotify: as únicas ferramentas que o **gateway** executa, e não o dispositivo.

A diferença com `device_tools.py` é a razão de o gateway existir: aqui há
segredo. O `client_secret` e o refresh token ficam no servidor e nunca descem
pelo fio -- é a seção 3 do plano ("Ferramentas · Segredos · Histórico") sendo
levada a sério. O dispositivo só ouve "Tocando tal música".

Exige **Spotify Premium**: todos os endpoints de controle de playback
(`/me/player/play`, `/pause`, `/next`) respondem 403 para conta gratuita. Ler o
que está tocando funciona sem Premium.

Autorização: Authorization Code com refresh token, obtido uma única vez por
`python -m gateway.tools.spotify_auth`. O access token dura uma hora e é
renovado aqui dentro, sem intervenção.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from gateway.llm.base import Tool

log = logging.getLogger("ideraldinho.spotify")

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

#: O que pedimos ao usuário na autorização. Nada além disto: `playback-state`
#: para saber o que toca e em que aparelho, `modify-playback-state` para mandar
#: tocar. Sem acesso a playlists, biblioteca, e-mail ou histórico.
SCOPES = "user-read-playback-state user-modify-playback-state"


class SpotifyError(Exception):
    """Falha que o usuário precisa ouvir, já em português."""


def _sem_acento(texto: str) -> str:
    texto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").strip()


#: Como as pessoas chamam os tipos de aparelho, em português falado. Ninguém diz
#: "toca no iPhone": diz "toca no celular". O `type` que a API devolve
#: (Computer, Smartphone, Speaker, TV) é o que permite casar isso sem saber o
#: nome que o dono deu ao aparelho.
TIPOS_FALADOS: dict[str, tuple[str, ...]] = {
    "Smartphone": ("celular", "telefone", "fone", "iphone", "android"),
    "Computer": ("computador", "pc", "notebook", "laptop", "maquina"),
    "Speaker": ("caixa", "caixinha", "caixa de som", "som", "alto falante", "speaker"),
    "TV": ("tv", "televisao", "televisor"),
    "AVR": ("receiver", "aparelho de som"),
    "CastVideo": ("chromecast", "cast"),
}


def _casar_aparelho(devices: list[dict], nome: str) -> dict | None:
    """Acha o aparelho do jeito que alguém falaria.

    Três tentativas, nesta ordem:

    1. **Nome exato** -- para "Ideraldinho" não perder para "Ideraldinho (quarto)" quando
       os dois existirem.
    2. **Trecho do nome** -- ninguém diz "Echo Dot de Ideraldo" inteiro, diz
       "echo".
    3. **Tipo falado** -- "celular" não é trecho de "iPhone". Sem esta etapa,
       pedir para tocar no celular mandava a palavra "celular" para dentro da
       busca, e o aparelho tocava outra gravação da mesma música.

    O tipo vem por último de propósito: se alguém batizou uma caixa de som de
    "Computador", o nome que a pessoa deu ganha do rótulo da API.
    """
    alvo = _sem_acento(nome)
    if not alvo:
        return None

    for d in devices:
        if _sem_acento(d.get("name", "")) == alvo:
            return d
    for d in devices:
        if alvo in _sem_acento(d.get("name", "")):
            return d

    tipos = {t for t, palavras in TIPOS_FALADOS.items() if alvo in palavras}
    if tipos:
        # Entre vários do mesmo tipo, o que já está tocando; senão, o primeiro.
        candidatos = [d for d in devices if d.get("type") in tipos]
        if candidatos:
            return next((d for d in candidatos if d.get("is_active")), candidatos[0])
    return None


def _erro_403(r: httpx.Response) -> SpotifyError:
    """Traduz um 403, que no Spotify significa duas coisas muito diferentes.

    Isto começou errado e foi corrigido pela primeira execução com conta real: eu
    traduzia **todo** 403 como "precisa de Premium". Mas o Spotify também
    responde 403 para um comando que é inválido no estado atual -- pausar o que
    já está pausado, voltar quando não há faixa anterior --, com
    `"Player command failed: Restriction violated"`. Dizer "compre Premium" a
    quem tem Premium é pior que não dizer nada.
    """
    reason, message = "", ""
    try:
        erro = r.json().get("error", {})
        reason = str(erro.get("reason") or "")
        message = str(erro.get("message") or "")
    except (ValueError, AttributeError):
        pass

    if reason == "PREMIUM_REQUIRED" or "premium" in message.lower():
        return SpotifyError("o controle de musica precisa de Spotify Premium")
    if "restriction violated" in message.lower():
        # Não é erro do usuário nem do código: é o estado do player.
        return SpotifyError("nao da pra fazer isso agora")
    if reason == "NO_ACTIVE_DEVICE":
        return SpotifyError("abra o Spotify em algum aparelho primeiro")
    return SpotifyError(message or "o Spotify recusou o comando")


class SpotifyClient:
    """Cliente mínimo: buscar, tocar, pausar, pular, e dizer o que toca."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_path: str | Path,
        market: str = "BR",
        timeout: float = 10.0,
        preferido: str | None = None,
    ) -> None:
        self._id = client_id
        self._secret = client_secret
        self._token_path = Path(token_path)
        self._market = market
        self._timeout = timeout
        # O nome do aparelho onde tocar quando ninguém disser onde. A ideia é
        # que seja a própria Pi, rodando raspotify/librespot: aí "toca Chico
        # Buarque" sai no alto-falante do Ideraldinho, como sairia numa Alexa, em vez
        # de num PC que pode estar em outro cômodo.
        self._preferido = preferido or None
        self._access: str | None = None
        self._expires_at = 0.0

    # -- credenciais -------------------------------------------------------

    @property
    def authorized(self) -> bool:
        """Se existe refresh token em disco. Sem ele não há o que oferecer."""
        return self._token_path.exists()

    def _refresh_token(self) -> str:
        data = json.loads(self._token_path.read_text(encoding="utf-8"))
        token = data.get("refresh_token")
        if not token:
            raise SpotifyError("nao estou conectado ao Spotify")
        return token

    async def _access_token(self) -> str:
        """Devolve um access token válido, renovando se faltar pouco.

        A margem de 60 s existe porque o token pode vencer entre a checagem e a
        chamada -- e o custo de renovar cedo demais é uma requisição, enquanto o
        de renovar tarde é a música não tocar.
        """
        if self._access and time.time() < self._expires_at - 60:
            return self._access

        auth = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(
                TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token()},
                headers={"Authorization": f"Basic {auth}"},
            )
        if r.status_code != 200:
            raise SpotifyError(f"o Spotify recusou a renovacao do acesso ({r.status_code})")

        payload = r.json()
        self._access = payload["access_token"]
        self._expires_at = time.time() + payload.get("expires_in", 3600)
        # O Spotify às vezes devolve um refresh token novo. Ignorar isso faz a
        # conexão morrer semanas depois, longe da causa.
        if payload.get("refresh_token"):
            self._save_refresh(payload["refresh_token"])
        return self._access

    def _save_refresh(self, token: str) -> None:
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(
            json.dumps({"refresh_token": token}, indent=2), encoding="utf-8"
        )

    # -- chamadas ----------------------------------------------------------

    async def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.request(
                method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        if r.status_code == 403:
            raise _erro_403(r)
        if r.status_code == 404:
            raise SpotifyError("nao encontrei nenhum aparelho tocando Spotify")
        if r.status_code == 429:
            # O Spotify limita por janela; insistir piora.
            raise SpotifyError("o Spotify pediu para eu esperar um pouco")
        if r.status_code >= 400:
            raise SpotifyError(f"o Spotify respondeu {r.status_code}")
        return r

    async def aparelhos(self) -> list[dict]:
        r = await self._call("GET", "/me/player/devices")
        devices = r.json().get("devices", [])
        if not devices:
            # Sem isto, mandar tocar com o Spotify fechado em todo lugar devolve
            # 404 e nada acontece -- o caso mais comum da vida real.
            raise SpotifyError("abra o Spotify em algum aparelho primeiro")
        return devices

    def _parece_aparelho(self, nome: str) -> bool:
        """O nome se refere a um aparelho, mesmo que ele não esteja na lista?

        Duas evidências bastam, e nenhuma delas depende de adivinhação: é o nome
        configurado em `SPOTIFY_DEVICE` (a Pi, que pode estar desligada), ou é
        uma das palavras que designam um **tipo** de aparelho ("celular",
        "caixa de som"). Nos dois casos a pessoa está falando de onde tocar, e
        merece ouvir que aquilo não está disponível em vez de ouvir a música
        errada.
        """
        alvo = _sem_acento(nome)
        if self._preferido and alvo == _sem_acento(self._preferido):
            return True
        return any(alvo in palavras for palavras in TIPOS_FALADOS.values())

    async def _existe_aparelho(self, nome: str) -> bool:
        try:
            return _casar_aparelho(await self.aparelhos(), nome) is not None
        except SpotifyError:
            # Sem nenhum aparelho aberto não dá para afirmar que o nome é
            # inválido; deixa o fluxo normal levantar o erro certo depois.
            return True

    async def _device_id(self, nome: str | None = None) -> str | None:
        """Onde tocar, em ordem de preferência.

        1. O aparelho que a pessoa nomeou na frase, se ela nomeou.
        2. O **preferido** da configuração -- a ideia é que seja a própria Pi.
        3. O que já está ativo.
        4. O primeiro da lista.

        A ordem tem uma consequência de projeto: com a Pi na lista, "toca Chico
        Buarque" sai nela por padrão, e é isso que faz o aparelho ser uma caixa
        de som em vez de um controle remoto do PC. Enquanto a Pi não existe, o
        item 2 não casa e o comportamento é o de antes.
        """
        devices = await self.aparelhos()

        if nome:
            achado = _casar_aparelho(devices, nome)
            if achado is None:
                nomes = ", ".join(d.get("name", "?") for d in devices)
                raise SpotifyError(f"nao achei o aparelho {nome}. Tem: {nomes}")
            return achado.get("id")

        if self._preferido:
            achado = _casar_aparelho(devices, self._preferido)
            if achado is not None:
                return achado.get("id")

        ativo = next((d for d in devices if d.get("is_active")), None)
        return (ativo or devices[0]).get("id")

    async def listar_aparelhos(self) -> str:
        devices = await self.aparelhos()
        partes = []
        for d in devices:
            nome = d.get("name", "sem nome")
            partes.append(f"{nome} (tocando agora)" if d.get("is_active") else nome)
        if len(partes) == 1:
            return f"So o {partes[0]}."
        return "Tem " + ", ".join(partes[:-1]) + f" e {partes[-1]}."

    async def trocar_aparelho(self, nome: str) -> str:
        """Passa o que está tocando para outro aparelho, sem parar a música."""
        devices = await self.aparelhos()
        alvo = _casar_aparelho(devices, nome)
        if alvo is None:
            nomes = ", ".join(d.get("name", "?") for d in devices)
            raise SpotifyError(f"nao achei o aparelho {nome}. Tem: {nomes}")
        # `play: true` continua tocando do ponto em que estava, em vez de
        # transferir pausado -- que é o que a pessoa quer ao dizer "passa pro X".
        await self._call(
            "PUT", "/me/player", json={"device_ids": [alvo.get("id")], "play": True}
        )
        return f"Passando para {alvo.get('name')}."

    async def tocar(self, busca: str, aparelho: str | None = None) -> str:
        # O modelo confunde os dois campos de texto: pedindo "toca Construção do
        # Chico Buarque" ele mandou {busca: "Construcao", aparelho: "Chico
        # Buarque"}, e o aparelho tocou a gravação errada. Se o que veio em
        # `aparelho` não é aparelho nenhum, ele era parte do pedido.
        #
        # Mas nem tudo que não casa é engano do modelo: pedir para tocar num
        # aparelho que existe e está desligado é um pedido legítimo, e nesse
        # caso jogar o nome na busca esconde o problema -- foi o que aconteceu
        # com "toca no ideraldinho" antes de a Pi existir, que virou uma busca por
        # "Construção Ideraldinho" e tocou outra versão da música.
        if aparelho and not await self._existe_aparelho(aparelho):
            if self._parece_aparelho(aparelho):
                # Sem artigo: "o tv" e "o caixa de som" saem errados, e a
                # frase vai ser falada em voz alta.
                raise SpotifyError(f"nao achei {aparelho} entre os aparelhos ligados")
            log.info("%r nao e aparelho; tratando como parte da busca", aparelho)
            if _sem_acento(aparelho) not in _sem_acento(busca):
                busca = f"{busca} {aparelho}".strip()
            aparelho = None

        r = await self._call(
            "GET", "/search", params={"q": busca, "type": "track", "limit": 1, "market": self._market}
        )
        itens = r.json().get("tracks", {}).get("items", [])
        if not itens:
            return f"Nao achei nada no Spotify para {busca}."

        faixa = itens[0]
        device = await self._device_id(aparelho)

        # Tocar no contexto do álbum, e não a faixa solta. Com `uris` a fila
        # tem exatamente um item: a música toca, e o primeiro "próxima" acaba
        # com ela em silêncio -- medido, e o aparelho ainda dizia "Próxima".
        # Com `context_uri` + `offset`, pedir uma música começa nela e segue no
        # álbum, que é o que qualquer assistente de voz faz.
        album = (faixa.get("album") or {}).get("uri")
        if album:
            corpo = {"context_uri": album, "offset": {"uri": faixa["uri"]}}
        else:
            corpo = {"uris": [faixa["uri"]]}

        await self._call(
            "PUT",
            "/me/player/play",
            params={"device_id": device} if device else None,
            json=corpo,
        )
        artistas = ", ".join(a["name"] for a in faixa.get("artists", []))
        return f"Tocando {faixa['name']}, de {artistas}."

    async def pausar(self) -> str:
        await self._call("PUT", "/me/player/pause")
        return "Pausado."

    async def retomar(self) -> str:
        await self._call("PUT", "/me/player/play")
        return "Voltando a tocar."

    async def proxima(self) -> str:
        await self._call("POST", "/me/player/next")
        return "Proxima."

    async def anterior(self) -> str:
        await self._call("POST", "/me/player/previous")
        return "Voltando uma."

    async def tocando_agora(self) -> str:
        r = await self._call("GET", "/me/player/currently-playing", params={"market": self._market})
        # 204: ninguém está tocando nada. Sem corpo, e `r.json()` explodiria.
        if r.status_code == 204 or not r.content:
            return "Nao tem nada tocando."
        faixa = r.json().get("item")
        if not faixa:
            return "Nao tem nada tocando."
        artistas = ", ".join(a["name"] for a in faixa.get("artists", []))
        return f"{faixa['name']}, de {artistas}."


#: As ferramentas como o modelo as vê. Cada descrição diz **quando não** usar:
#: sem isso, "toca um alarme pra mim" vira uma busca no Spotify.
SPOTIFY_TOOLS: list[Tool] = [
    Tool(
        name="tocar_musica",
        description=(
            "Toca uma musica, artista ou album no Spotify. Use para 'toca Chico "
            "Buarque', 'poe Construcao'. Nao use para timer, alarme ou lembrete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "busca": {
                    "type": "string",
                    "description": "O que procurar: nome da musica, do artista, ou os dois.",
                },
                "aparelho": {
                    "type": "string",
                    "description": (
                        "Onde tocar, SO se a pessoa disser ('toca no echo dot'). "
                        "Omita quando ela nao disser: o aparelho padrao e escolhido "
                        "sozinho."
                    ),
                },
            },
            "required": ["busca"],
        },
    ),
    Tool(
        name="pausar_musica",
        description="Pausa o que esta tocando no Spotify. Use para 'pausa', 'para a musica'.",
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="retomar_musica",
        description="Volta a tocar o que estava pausado no Spotify. Use para 'continua', 'volta a tocar'.",
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="proxima_musica",
        description="Pula para a proxima musica da fila do Spotify.",
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="musica_anterior",
        description="Volta para a musica anterior do Spotify.",
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="musica_tocando",
        description=(
            "Diz qual musica esta tocando agora no Spotify e de quem e. Use para "
            "'que musica e essa', 'quem canta isso'."
        ),
        parameters={"type": "object", "properties": {}},
    ),
]

SPOTIFY_TOOLS += [
    Tool(
        name="listar_aparelhos",
        description=(
            "Diz em quais aparelhos da pra tocar musica agora: celular, computador, "
            "caixa de som. Use SEMPRE que perguntarem onde da pra tocar, quais "
            "aparelhos existem, ou em que caixa de som da pra ouvir -- nunca "
            "responda isso de cabeca, porque a lista muda o tempo todo."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="trocar_aparelho",
        description=(
            "Passa a musica que ja esta tocando para outro aparelho, sem parar. "
            "Use para 'passa pro echo dot', 'joga no computador'. Para comecar "
            "uma musica nova num aparelho, use tocar_musica com o campo aparelho."
        ),
        parameters={
            "type": "object",
            "properties": {
                "aparelho": {
                    "type": "string",
                    "description": "Nome, ou parte do nome, do aparelho de destino.",
                }
            },
            "required": ["aparelho"],
        },
    ),
]

#: Nome da ferramenta -> método do cliente. Explícito, e não `getattr`, para que
#: um nome inventado pelo modelo não vire chamada de método arbitrário.
SPOTIFY_DISPATCH: dict[str, str] = {
    "tocar_musica": "tocar",
    "pausar_musica": "pausar",
    "retomar_musica": "retomar",
    "proxima_musica": "proxima",
    "musica_anterior": "anterior",
    "musica_tocando": "tocando_agora",
    "listar_aparelhos": "listar_aparelhos",
    "trocar_aparelho": "trocar_aparelho",
}


async def executar_spotify(client: SpotifyClient, name: str, args: dict) -> str:
    """Executa uma ferramenta do Spotify e devolve a frase a falar.

    Erro nunca sobe como exceção: vira frase. Um assistente de voz que engasga
    porque o Premium venceu é pior que um que diz que o Premium venceu.
    """
    metodo = SPOTIFY_DISPATCH.get(name)
    if metodo is None:
        return f"falhou: ferramenta desconhecida {name}"
    try:
        if metodo == "tocar":
            busca = str(args.get("busca") or "").strip()
            if not busca:
                return "falhou: nao disseram o que tocar"
            aparelho = str(args.get("aparelho") or "").strip() or None
            return await client.tocar(busca, aparelho)
        if metodo == "trocar_aparelho":
            aparelho = str(args.get("aparelho") or "").strip()
            if not aparelho:
                return "falhou: nao disseram para qual aparelho"
            return await client.trocar_aparelho(aparelho)
        return await getattr(client, metodo)()
    except SpotifyError as exc:
        return str(exc).capitalize() + "."
    except httpx.HTTPError as exc:
        log.warning("spotify inacessivel: %s", exc)
        return "Nao consegui falar com o Spotify agora."
