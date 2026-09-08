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

#: O que pedimos ao usuário na autorização. Os dois primeiros movem o player; os
#: quatro seguintes existem só por causa das playlists. Continua não havendo
#: acesso a e-mail, histórico de escuta nem biblioteca salva.
#:
#: **Mudar esta linha não basta.** O escopo é gravado no refresh token no
#: momento da autorização; um token antigo continua valendo com o escopo antigo
#: e as chamadas novas voltam 403. Depois de alterar aqui: apague o
#: `spotify_token.json` e rode `python -m gateway.tools.spotify_auth` de novo.
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "playlist-read-private playlist-read-collaborative "
    "playlist-modify-private playlist-modify-public"
)

#: O escopo de antes das playlists. Serve para interpretar um token gravado por
#: uma versão anterior, que não registrava em disco o que tinha sido concedido.
SCOPES_ANTIGOS = "user-read-playback-state user-modify-playback-state"

#: Escopos exigidos por operação, e o que dizer quando faltam.
ESCOPO_LER_PLAYLIST = "playlist-read-private"
ESCOPO_CRIAR_PLAYLIST = "playlist-modify-private"

#: Quantos resultados pedir para usar **um**. Ver `_buscar_um`: com `limit=1` o
#: Spotify devolve um item diferente e pior do que o primeiro de `limit=2`. Três
#: dá folga para descartar os buracos que a busca de playlist devolve, sem virar
#: uma lista para o usuário escolher -- ele continua ouvindo só o primeiro.
LIMITE_BUSCA = 3

#: O `tipo` que o modelo escolhe -> o `type` da busca do Spotify. Este de/para é
#: pequeno e fechado de propósito: são as quatro coisas que dá para tocar. Ele
#: não sabe nada sobre nomes de música ou de playlist, que mudam toda semana.
TIPOS_BUSCA: dict[str, str] = {
    "musica": "track",
    "album": "album",
    "artista": "artist",
    "playlist": "playlist",
}

#: Palavras que denunciam o tipo dentro da própria frase. Rede de segurança para
#: quando o modelo deixa `tipo` no padrão: "toca minha playlist de treino" com
#: `tipo=musica` procurava uma **faixa** chamada "minha playlist de treino",
#: achava qualquer coisa e anunciava com toda a confiança que tinha acertado.
#: Errar calado é o pior modo de falhar num aparelho que só fala.
PALAVRAS_DE_TIPO: dict[str, tuple[str, ...]] = {
    "playlist": ("playlist", "playlists", "lista"),
    "album": ("album", "albuns", "disco"),
    "artista": ("banda", "cantor", "cantora", "artista"),
}


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


#: Artigos e possessivos que aparecem **antes** da palavra de tipo -- "a
#: playlist", "minha playlist". Só são removidos nessa posição: depois dela são
#: parte do nome, e "o disco A Noite" não pode virar "Noite".
_ANTES_DO_TIPO = frozenset({"minha", "minhas", "meu", "meus", "a", "o", "as", "os", "um", "uma"})


def _tipo_falado(busca: str, tipo: str) -> tuple[str, str]:
    """Corrige o tipo quando a própria frase diz qual é, e limpa a busca.

    Só age quando o modelo mandou o padrão (`musica`): se ele escolheu
    explicitamente, a escolha dele ganha -- ele viu a frase inteira, e aqui só
    há substrings.

    A palavra encontrada sai da busca. Deixá-la dentro faz "toca a playlist
    esquenta" procurar por uma playlist literalmente chamada "playlist
    esquenta", que não existe.
    """
    if tipo != "musica":
        return busca, tipo

    originais = busca.split()
    normalizadas = [_sem_acento(p) for p in originais]

    for candidato, marcas in PALAVRAS_DE_TIPO.items():
        posicao = next((i for i, p in enumerate(normalizadas) if p in marcas), None)
        if posicao is None:
            continue

        limpa: list[str] = []
        pular_ligacao = False
        for i, (original, normal) in enumerate(zip(originais, normalizadas)):
            if i == posicao:
                # "playlist de treino" -> "treino". A preposição só cai quando
                # vem colada na palavra removida; fora daí ela é do nome ("Chico
                # Buarque de Hollanda") e apagá-la estraga a busca.
                pular_ligacao = True
                continue
            if i < posicao and normal in _ANTES_DO_TIPO:
                continue
            if pular_ligacao and normal in ("de", "do", "da", "dos", "das"):
                pular_ligacao = False
                continue
            pular_ligacao = False
            limpa.append(original)
        return (" ".join(limpa).strip() or busca), candidato
    return busca, tipo


#: Artigo por tipo, para a frase de "não achei" sair falável. Sem isto sai "não
#: achei album Clube da Esquina", que soa como telegrama.
_ARTIGO = {"musica": "a musica", "album": "o album", "artista": "o artista", "playlist": "a playlist"}


def _parece_a_mesma(pedido: str, achado: str) -> bool:
    """O nome achado corresponde ao pedido? Ambos já sem acento e em minúscula.

    Critério: ou o pedido inteiro está no nome achado, ou **todas** as palavras
    de peso do pedido aparecem nele. "esquenta sertanejo" casa com "Esquenta
    Sertanejo 2026"; "playlist que nao existe 12345" não casa com "123445".

    Todas, e não a maioria: com duas ou três palavras, "a maioria" é uma só, e
    uma palavra em comum é o que faz qualquer playlist casar com qualquer nome.
    """
    if not pedido or not achado:
        return False
    if pedido in achado:
        return True
    palavras = [p for p in pedido.split() if len(p) > 2]
    return bool(palavras) and all(p in achado for p in palavras)


def _artistas(item: dict) -> str:
    return ", ".join(a["name"] for a in item.get("artists", []) if a.get("name"))


def _como_tocar(item: dict, tipo: str) -> tuple[dict, str]:
    """Monta o corpo do `/play` e a frase a falar, por tipo.

    Cada tipo tem uma regra própria, e nenhuma delas é intercambiável:

    - **música**: toca no contexto do álbum, com `offset` na faixa. Com `uris` a
      fila teria exatamente um item, e o primeiro "próxima" acabava em silêncio
      -- medido, com o aparelho ainda dizendo "Próxima".
    - **álbum**: contexto do álbum, sem `offset`. Quem pede o disco quer do
      começo.
    - **artista**: contexto do artista e mais nada. A documentação é explícita:
      `offset` só vale para álbum e playlist. Mandar `offset` aqui é 400.
    """
    if tipo == "album":
        return {"context_uri": item["uri"]}, f"Tocando o album {item['name']}, de {_artistas(item)}."

    if tipo == "artista":
        # Sem `Get Artist's Top Tracks`, removido em fevereiro de 2026: o player
        # resolve sozinho o que tocar de um artista, e o resultado é melhor que
        # uma lista montada por nós.
        return {"context_uri": item["uri"]}, f"Tocando {item['name']}."

    album = (item.get("album") or {}).get("uri")
    corpo = (
        {"context_uri": album, "offset": {"uri": item["uri"]}} if album else {"uris": [item["uri"]]}
    )
    return corpo, f"Tocando {item['name']}, de {_artistas(item)}."


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

    def _dados_token(self) -> dict:
        try:
            return json.loads(self._token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SpotifyError("nao estou conectado ao Spotify") from None

    def _refresh_token(self) -> str:
        token = self._dados_token().get("refresh_token")
        if not token:
            raise SpotifyError("nao estou conectado ao Spotify")
        return token

    def _exigir_escopo(self, escopo: str, para_que: str) -> None:
        """Barra a chamada quando o token em disco não tem o escopo necessário.

        Existe por causa de um modo de falha que não se explica sozinho: quem
        autorizou antes das playlists tem um refresh token válido, com escopo
        antigo. As chamadas de playlist voltariam 403, que este código traduz
        como "precisa de Premium" ou "o Spotify recusou" -- duas mentiras, ditas
        em voz alta, para quem só precisa reautorizar.

        Token gravado por uma versão antiga não tem o campo `scope`. Nesse caso
        assume-se o escopo antigo, que é o que ele de fato tem.
        """
        concedidos = str(self._dados_token().get("scope") or SCOPES_ANTIGOS).split()
        if escopo not in concedidos:
            raise SpotifyError(
                f"preciso ser autorizado de novo no Spotify para {para_que}"
            )

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
        # Reescreve preservando o resto do arquivo -- hoje, o `scope`. Sobrescrever
        # o arquivo inteiro faria a primeira renovação de token apagar o registro
        # do escopo, e a partir dali toda chamada de playlist seria barrada por
        # `_exigir_escopo` sem nenhum motivo visível.
        dados = {}
        if self._token_path.exists():
            try:
                dados = json.loads(self._token_path.read_text(encoding="utf-8"))
            except ValueError:
                dados = {}
        dados["refresh_token"] = token
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(json.dumps(dados, indent=2), encoding="utf-8")

    # -- chamadas ----------------------------------------------------------

    async def _call(
        self, method: str, path: str, nao_achou: str | None = None, **kwargs: Any
    ) -> httpx.Response:
        token = await self._access_token()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.request(
                method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs
            )
        if r.status_code == 403:
            raise _erro_403(r)
        if r.status_code == 404:
            # O 404 do player significa "nenhum aparelho", mas o das playlists
            # significa "essa playlist não existe". A mesma frase para os dois
            # manda a pessoa procurar defeito no lugar errado.
            raise SpotifyError(nao_achou or "nao encontrei nenhum aparelho tocando Spotify")
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

    async def tocar(
        self, busca: str, tipo: str = "musica", aparelho: str | None = None
    ) -> str:
        """Toca uma música, um álbum, um artista ou uma playlist.

        O `tipo` é o que separa "toca Chico Buarque" de "toca Construção". Antes
        dele tudo era `type=track`: pedir um artista achava uma faixa qualquer
        dele e tocava o **álbum daquela faixa**, que podia ser um ao vivo de
        1999. Funcionava por acidente, e o acidente era visível no resultado.
        """
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

        busca, tipo = _tipo_falado(busca, tipo if tipo in TIPOS_BUSCA else "musica")

        if tipo == "playlist":
            return await self._tocar_playlist(busca, aparelho)

        achado = await self._buscar_um(busca, tipo)
        if achado is None:
            # Nomear o tipo procurado importa: "não achei o álbum X" diz à
            # pessoa que o nome pode estar certo e o tipo errado. "Não achei
            # nada" não diz nada.
            return f"Nao achei {_ARTIGO[tipo]} {busca} no Spotify."

        device = await self._device_id(aparelho)
        corpo, frase = _como_tocar(achado, tipo)

        await self._call(
            "PUT",
            "/me/player/play",
            params={"device_id": device} if device else None,
            json=corpo,
        )
        return frase

    async def _buscar_um(self, busca: str, tipo: str) -> dict | None:
        """Primeiro resultado da busca, ou None. Quem escolhe continua sendo o Spotify.

        Um assistente de voz que oferece cinco opções para "toca Construção" é
        pior que um que erra e aceita "não, a outra" -- então usamos só o
        primeiro item. Mas **pedimos mais que um**, e a diferença não é
        cosmética: com `limit=1` o Spotify devolve um item *diferente* do
        primeiro item de `limit=2`, e pior. Medido com conta real em 08/09/2026:

            'Pink Floyd'        limit=1: Guns N' Roses  | limit=2: Pink Floyd
            'Clube da Esquina'  limit=1: Construção     | limit=2: Clube Da Esquina

        Ou seja: "toca Pink Floyd" tocava Guns N' Roses anunciando que acertou --
        o mesmo defeito que o D32 existe para eliminar, sobrevivendo num
        parâmetro. Nenhum teste sobre HTTP falso podia pegar isto, porque lá a
        lista devolvida é a que o teste escreveu.

        O máximo do `limit` caiu de 50 para 10 na revisão de fevereiro de 2026.
        """
        chave = TIPOS_BUSCA[tipo]
        r = await self._call(
            "GET",
            "/search",
            params={"q": busca, "type": chave, "limit": LIMITE_BUSCA, "market": self._market},
        )
        itens = (r.json().get(f"{chave}s") or {}).get("items") or []
        # A busca de playlist devolve buracos: posições nulas no meio da lista,
        # que quebram tudo que acessa `["uri"]` logo depois.
        return next((i for i in itens if i), None)

    # -- playlists ---------------------------------------------------------

    async def _minhas_playlists(self) -> list[dict]:
        """As playlists da conta. `/me/playlists`, nunca `/users/{id}/playlists`.

        A variante por id de usuário foi removida na revisão de fevereiro de
        2026, junto com `Get User's Profile`. A `/me` sobreviveu, e é a única
        que enxerga playlist privada -- daí o escopo `playlist-read-private`.
        """
        self._exigir_escopo(ESCOPO_LER_PLAYLIST, "ver as suas playlists")
        r = await self._call("GET", "/me/playlists", params={"limit": 50})
        return [p for p in (r.json().get("items") or []) if p]

    async def _achar_playlist(self, nome: str) -> tuple[dict | None, bool]:
        """Acha uma playlist pelo nome falado. Devolve (playlist, era_sua).

        As **suas** vêm primeiro, e essa ordem é a funcionalidade inteira: existem
        milhares de playlists públicas chamadas "Treino", e nenhuma delas é a
        sua. Só se nenhuma casar é que a busca pública entra -- que é o que
        atende "toca a playlist Esquenta Sertanejo".
        """
        alvo = _sem_acento(nome)
        try:
            minhas = await self._minhas_playlists()
        except SpotifyError:
            # Sem escopo de leitura ainda dá para tocar playlist pública. Falhar
            # aqui tiraria uma funcionalidade que não depende do escopo.
            minhas = []

        for p in minhas:
            if _sem_acento(p.get("name", "")) == alvo:
                return p, True
        for p in minhas:
            if alvo and alvo in _sem_acento(p.get("name", "")):
                return p, True

        # A busca pública **nunca** devolve vazio: pedir uma playlist que não
        # existe traz a menos ruim que ela achou. Com conta real, "playlist que
        # nao existe 12345" voltou uma playlist chamada "123445" -- e o aparelho
        # diria "tocando a playlist 123445", que é errar calado outra vez.
        # Então o resultado público só passa se o nome dito aparecer no nome
        # dela. É estrito de propósito: aqui o custo de recusar é a pessoa
        # repetir, e o de aceitar é tocar a playlist de um estranho.
        publica = await self._buscar_um(nome, "playlist")
        if publica and _parece_a_mesma(alvo, _sem_acento(publica.get("name", ""))):
            return publica, False
        return None, False

    async def _tocar_playlist(self, nome: str, aparelho: str | None) -> str:
        playlist, era_sua = await self._achar_playlist(nome)
        if playlist is None:
            return f"Nao achei nenhuma playlist chamada {nome}."

        device = await self._device_id(aparelho)
        # Embaralhar **antes** do play. A documentação avisa que a ordem entre
        # chamadas do player não é garantida, mas nesta ordem o pior caso é a
        # playlist começar na primeira música e embaralhar depois; na ordem
        # inversa o shuffle pode cair na faixa errada e pular o que acabou de
        # começar.
        await self._embaralhar(True, device)
        await self._call(
            "PUT",
            "/me/player/play",
            params={"device_id": device} if device else None,
            json={"context_uri": playlist["uri"]},
        )
        dono = "sua playlist" if era_sua else "a playlist"
        return f"Tocando {dono} {playlist['name']}."

    async def _embaralhar(self, ativar: bool, device: str | None = None) -> None:
        """Liga ou desliga o modo aleatório, sem deixar isso derrubar a música.

        Falha aqui é engolida de propósito: shuffle é preferência, tocar é o
        pedido. Um aparelho que se recusa a tocar porque não conseguiu
        embaralhar trocou o essencial pelo acessório.
        """
        params: dict[str, Any] = {"state": "true" if ativar else "false"}
        if device:
            params["device_id"] = device
        try:
            await self._call("PUT", "/me/player/shuffle", params=params)
        except (SpotifyError, httpx.HTTPError) as exc:
            log.info("shuffle recusado, seguindo assim mesmo: %s", exc)

    async def listar_playlists(self) -> str:
        playlists = await self._minhas_playlists()
        if not playlists:
            return "Voce nao tem nenhuma playlist."
        nomes = [p.get("name", "sem nome") for p in playlists]
        # Ler quarenta nomes em voz alta é castigo. Diz os primeiros e o total.
        if len(nomes) > 6:
            return f"Voce tem {len(nomes)} playlists. As primeiras: " + ", ".join(nomes[:6]) + "."
        if len(nomes) == 1:
            return f"So a {nomes[0]}."
        return "Tem " + ", ".join(nomes[:-1]) + f" e {nomes[-1]}."

    async def embaralhar(self, ativar: bool = True) -> str:
        """Versão falada do shuffle. Aqui o erro **sobe**: foi o pedido em si."""
        params = {"state": "true" if ativar else "false"}
        await self._call("PUT", "/me/player/shuffle", params=params)
        return "Modo aleatorio ligado." if ativar else "Modo aleatorio desligado."

    async def criar_playlist(self, nome: str, adicionar_atual: bool = False) -> str:
        """Cria uma playlist privada, opcionalmente já com a música que toca.

        Privada por padrão, e isso é decisão, não descuido: a API cria pública se
        ninguém disser nada, e publicar no perfil de alguém por omissão é o tipo
        de padrão que ninguém escolheria se fosse perguntado.

        A playlist nasce vazia -- criar e popular são chamadas separadas. Por
        isso `adicionar_atual`: uma playlist vazia criada por voz é uma gaveta
        que a pessoa vai ter que abrir no celular de qualquer jeito, e o pedido
        real quase sempre é "guarda essa música".
        """
        self._exigir_escopo(ESCOPO_CRIAR_PLAYLIST, "criar playlists")

        r = await self._call(
            "POST",
            "/me/playlists",
            json={"name": nome, "public": False, "description": "Criada pelo Ideraldinho."},
        )
        playlist = r.json()

        if not adicionar_atual:
            return f"Criei a playlist {nome}, vazia."

        faixa = await self._faixa_atual()
        if faixa is None:
            return f"Criei a playlist {nome}, mas nao tinha nada tocando para guardar nela."

        await self._call(
            # `/tracks` virou `/items` em fevereiro de 2026; o antigo ainda
            # responde, mas está marcado como obsoleto.
            "POST",
            f"/playlists/{playlist['id']}/items",
            nao_achou="nao achei a playlist que acabei de criar",
            json={"uris": [faixa["uri"]]},
        )
        return f"Criei a playlist {nome} com {faixa['name']}."

    async def _faixa_atual(self) -> dict | None:
        r = await self._call("GET", "/me/player/currently-playing", params={"market": self._market})
        if r.status_code == 204 or not r.content:
            return None
        faixa = r.json().get("item")
        return faixa if faixa and faixa.get("uri") else None

    # -- transporte --------------------------------------------------------

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
        # O 204 sem corpo -- ninguém tocando nada -- é tratado em `_faixa_atual`,
        # onde `r.json()` explodiria.
        faixa = await self._faixa_atual()
        if faixa is None:
            return "Nao tem nada tocando."
        return f"{faixa['name']}, de {_artistas(faixa)}."


#: As ferramentas como o modelo as vê. Cada descrição diz **quando não** usar:
#: sem isso, "toca um alarme pra mim" vira uma busca no Spotify.
SPOTIFY_TOOLS: list[Tool] = [
    Tool(
        name="tocar_musica",
        description=(
            "Toca musica no Spotify: uma musica, um album, um artista ou uma "
            "playlist. Use para 'toca Chico Buarque', 'poe Construcao', 'toca o "
            "disco Clube da Esquina', 'toca minha playlist de treino'. "
            "SEMPRE preencha o campo tipo com o que a pessoa pediu -- e o tipo "
            "que decide se toca a musica certa ou uma faixa aleatoria com nome "
            "parecido. Nao use para timer, alarme ou lembrete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "busca": {
                    "type": "string",
                    "description": (
                        "SO o nome do que tocar, sem a palavra do tipo e sem "
                        "possessivo. Para 'toca minha playlist de treino', mande "
                        "'treino'. Para 'toca o disco Construcao', mande "
                        "'Construcao'."
                    ),
                },
                "tipo": {
                    "type": "string",
                    "enum": ["musica", "album", "artista", "playlist"],
                    "description": (
                        "O que o nome designa. 'musica' para uma faixa; 'album' "
                        "para disco; 'artista' para banda ou cantor, quando a "
                        "pessoa nao nomeia musica nenhuma ('toca Chico Buarque'); "
                        "'playlist' sempre que ela disser playlist ou lista. Na "
                        "duvida entre musica e artista, escolha artista quando o "
                        "nome for de gente ou banda."
                    ),
                },
                "aparelho": {
                    "type": "string",
                    "description": (
                        "Onde tocar, SO se a pessoa disser ('toca no echo dot'). "
                        "Omita quando ela nao disser: o aparelho padrao e escolhido "
                        "sozinho. NUNCA ponha nome de artista aqui."
                    ),
                },
            },
            "required": ["busca", "tipo"],
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

SPOTIFY_TOOLS += [
    Tool(
        name="listar_playlists",
        description=(
            "Diz quais playlists a pessoa tem na conta dela. Use para 'quais "
            "playlists eu tenho', 'que listas tem ai' -- nunca responda isso de "
            "cabeca. Para TOCAR uma playlist use tocar_musica com tipo=playlist, "
            "sem listar antes."
        ),
        parameters={"type": "object", "properties": {}},
    ),
    Tool(
        name="embaralhar_musica",
        description=(
            "Liga ou desliga o modo aleatorio do Spotify. Use para 'poe no "
            "aleatorio', 'embaralha', 'para de embaralhar', 'toca em ordem'."
        ),
        parameters={
            "type": "object",
            "properties": {
                "ativar": {
                    "type": "boolean",
                    "description": "true para embaralhar, false para voltar a ordem normal.",
                }
            },
            "required": ["ativar"],
        },
    ),
    Tool(
        name="criar_playlist",
        description=(
            "Cria uma playlist nova e privada na conta da pessoa. Use para 'cria "
            "uma playlist chamada X', 'guarda essa musica numa playlist X'. Ela "
            "nasce vazia; marque adicionar_atual quando a pessoa quiser guardar "
            "nela a musica que esta tocando. Nao use para tocar nem para procurar "
            "playlist que ja existe."
        ),
        parameters={
            "type": "object",
            "properties": {
                "nome": {
                    "type": "string",
                    "description": "O nome da playlist, exatamente como a pessoa falou.",
                },
                "adicionar_atual": {
                    "type": "boolean",
                    "description": (
                        "true SO quando a pessoa se referir a musica que esta "
                        "tocando ('guarda essa', 'poe essa musica nela')."
                    ),
                },
            },
            "required": ["nome"],
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
    "listar_playlists": "listar_playlists",
    "embaralhar_musica": "embaralhar",
    "criar_playlist": "criar_playlist",
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
            # Tipo inventado pelo modelo ("faixa", "song") cai no padrão em vez
            # de virar `type=faixa` e 400 do Spotify.
            tipo = str(args.get("tipo") or "musica").strip().lower()
            aparelho = str(args.get("aparelho") or "").strip() or None
            return await client.tocar(busca, tipo, aparelho)
        if metodo == "criar_playlist":
            nome = str(args.get("nome") or "").strip()
            if not nome:
                return "falhou: nao disseram o nome da playlist"
            return await client.criar_playlist(nome, bool(args.get("adicionar_atual")))
        if metodo == "embaralhar":
            # `ativar` ausente vira ligar: "embaralha" é o pedido comum, e
            # desligar a pessoa sempre diz com todas as letras.
            return await client.embaralhar(bool(args.get("ativar", True)))
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
