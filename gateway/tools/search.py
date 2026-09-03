"""Busca na internet: a ferramenta que existe para o modelo parar de inventar.

Esta é a resposta ao defeito medido em [D20]: o qwen3:8b atribuiu *Dom Casmurro*
ao Mario Quintana, com confiança total. Um modelo de 8 bilhões de parâmetros
sabe conversar; **fundamentar é outra coisa**, e não se resolve com um modelo
maior nem com mais raciocínio (D20 mediu os dois). Resolve-se dando texto de
verdade para ele ler antes de responder.

É a **primeira ferramenta não-terminal** do projeto. As de agenda e as do
Spotify devolvem uma frase pronta que vai ao ar como está (D18); aqui não existe
frase pronta -- existem cinco trechos de páginas, e a resposta falada tem que ser
sintetizada a partir deles. É o caminho de segunda rodada de LLM, que até agora
nenhuma ferramenta usava.

O provedor é trocável pelo mesmo motivo que o LLM é (`LLMProvider`, seção 6 do
plano): o padrão não exige chave nem conta de ninguém, e quem quiser qualidade
melhor troca por variável de ambiente.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import unquote

import httpx

from gateway.llm.base import Tool

log = logging.getLogger("ideraldinho.busca")

#: Quantos resultados pedir. Cinco é o suficiente para o modelo cruzar duas
#: fontes e pouco o bastante para não inchar o prompt -- e prompt inchado, num
#: modelo local, é latência direta.
RESULTADOS = 5

#: O que dizer ao servidor. Medido em 03/09/2026 contra cinco sites: httpx,
#: Firefox, Chrome e um nome honesto deram exatamente o mesmo resultado -- os
#: quatro entraram nos mesmos quatro sites e levaram 403 do mesmo. Ou seja: o
#: User-Agent nao e o que abre porta, e nao vale fingir ser navegador para
#: conseguir nada. Fica o nome do aparelho, que e o que a politica de robo da
#: Wikimedia pede de quem chega pela API.
#: A URL nao e enfeite: sem um contato aqui dentro, a API da Wikimedia devolve
#: 403 -- medido, o mesmo pedido passou a 200 so de acrescenta-la.
USER_AGENT = "IderaldinhoAssistente/0.1 (+https://github.com/Ideraldo/Marcos-AI)"

#: A Wikipedia responde 403 a leitura direta de `/wiki/...` -- com qualquer
#: User-Agent, medido -- e no corpo do erro manda usar a API. E o que fazemos:
#: e o primeiro resultado mais comum de pergunta de conhecimento, justamente o
#: caso que a busca existe para resolver (D20), e raspar quem pediu para nao
#: ser raspado seria errado alem de fragil.
WIKI_API = "/w/api.php"

#: Trecho de cada resultado. Textos longos empurram o modelo para copiar em vez
#: de responder, e a resposta vai ser **falada**: uma ou duas frases.
MAX_TRECHO = 400


@dataclass(frozen=True)
class Resultado:
    titulo: str
    trecho: str
    url: str


class SearchProvider(Protocol):
    """Uma fonte de resultados. Trocável, como o `LLMProvider`."""

    nome: str

    async def buscar(self, consulta: str, limite: int) -> list[Resultado]: ...


class DuckDuckGo:
    """Padrão: sem chave, sem conta, sem cadastro.

    A biblioteca é síncrona e faz rede, então roda numa thread -- se rodasse no
    laço de eventos, travaria o WebSocket do dispositivo durante a busca, e é
    exatamente durante a busca que o aparelho deveria continuar respondendo.
    """

    nome = "duckduckgo"

    def __init__(self, regiao: str = "br-pt", timeout: float = 12.0) -> None:
        self._regiao = regiao
        self._timeout = timeout

    async def buscar(self, consulta: str, limite: int) -> list[Resultado]:
        from ddgs import DDGS

        def _sincrono() -> list[dict]:
            with DDGS(timeout=self._timeout) as ddgs:
                return list(ddgs.text(consulta, region=self._regiao, max_results=limite))

        bruto = await asyncio.to_thread(_sincrono)
        return [
            Resultado(
                titulo=(r.get("title") or "").strip(),
                trecho=(r.get("body") or "").strip()[:MAX_TRECHO],
                url=(r.get("href") or "").strip(),
            )
            for r in bruto
            if r.get("body")
        ]


class Brave:
    """Alternativa com chave: resultados mais estáveis, cota gratuita mensal.

    Existe porque o DuckDuckGo sem chave é o primeiro a sofrer quando o
    provedor aperta o cerco a automação -- e aí a busca cai sem aviso. Trocar é
    uma variável de ambiente.
    """

    nome = "brave"
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, pais: str = "br", timeout: float = 12.0) -> None:
        self._key = api_key
        self._pais = pais
        self._timeout = timeout

    async def buscar(self, consulta: str, limite: int) -> list[Resultado]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(
                self.URL,
                params={"q": consulta, "count": limite, "country": self._pais},
                headers={"X-Subscription-Token": self._key, "Accept": "application/json"},
            )
        r.raise_for_status()
        itens = (r.json().get("web") or {}).get("results", []) or []
        return [
            Resultado(
                titulo=(i.get("title") or "").strip(),
                trecho=(i.get("description") or "").strip()[:MAX_TRECHO],
                url=(i.get("url") or "").strip(),
            )
            for i in itens
        ]


#: Quanto texto da página trazer. O trecho do buscador tem 400 caracteres; a
#: página inteira teria dezenas de milhares. 2000 é um chute com critério, e
#: não um número medido: o que manda aqui é o prompt de um modelo local, onde
#: cada mil caracteres a mais é latência que o usuário escuta como silêncio.
#: Vale medir quando houver log de uso real.
MAX_PAGINA = 2000

#: A página é um bônus, não a resposta. Se ela demora mais que isso, a busca
#: responde com os trechos e segue -- que é exatamente o que ela fazia antes.
LEITURA_TIMEOUT = 5.0

#: Teto de bytes baixados. Sem ele, um PDF disfarçado de HTML ou uma página de
#: 10 MB seguraria o turno até o timeout, baixando coisa que vai ser jogada
#: fora depois do corte em MAX_PAGINA de qualquer jeito.
LEITURA_MAX_BYTES = 400_000

#: Tags cujo conteúdo não é texto da página. `script` e `style` são o essencial;
#: o resto é menu, rodapé e cabeçalho, que em português dão parágrafos inteiros
#: de "Assine a newsletter" no meio do material que o modelo vai ler.
IGNORAR = {"script", "style", "nav", "header", "footer", "aside", "noscript", "form"}

#: Onde termina um parágrafo. Sem isso o texto extraído vira uma frase só, de
#: dois mil caracteres, e o modelo perde a separação entre um assunto e outro.
QUEBRA = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}


class _Texto(HTMLParser):
    """Extrai o texto visível. É a alternativa de 30 linhas ao BeautifulSoup.

    Não é um extrator de artigo -- não sabe distinguir o corpo da matéria de uma
    lista de links relacionados. Sabe o suficiente: tirar marcação, pular
    script e menu, e preservar quebra de parágrafo. Se um dia isso não bastar,
    o lugar de melhorar é aqui, e continua sem dependência nova (D25).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        # Pilha, e não booleano: `<nav>` com `<div>` dentro fecharia o div
        # primeiro e voltaria a coletar o menu.
        self._ignorando = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in IGNORAR:
            self._ignorando += 1
        elif tag in QUEBRA:
            self._partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in IGNORAR and self._ignorando:
            self._ignorando -= 1
        elif tag in QUEBRA:
            self._partes.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignorando:
            self._partes.append(data)

    @property
    def texto(self) -> str:
        bruto = "".join(self._partes)
        # Espaço em HTML é decorativo: indentação, tabulação e a quebra de linha
        # do próprio arquivo. Colapsar deixa o que sobra parecido com o que uma
        # pessoa leria na tela.
        linhas = (" ".join(linha.split()) for linha in bruto.split("\n"))
        return "\n".join(linha for linha in linhas if linha)


def extrair_texto(html: str, limite: int = MAX_PAGINA) -> str:
    parser = _Texto()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 -- HTML torto é o caso comum, não o raro
        pass
    return parser.texto[:limite].strip()


class Leitor:
    """Abre o primeiro resultado e devolve o texto dele.

    Existe porque o trecho do buscador responde "quando foi" e não responde
    "por quê": duas linhas de resumo bastam para uma data e não bastam para
    explicar nada. A alternativa era um framework de RAG; isto é uma requisição
    HTTP e um parser da biblioteca padrão (D25).

    **Nada aqui pode falhar para fora.** A leitura é um acréscimo ao que a
    busca já entregava; quando ela não dá certo -- e vai não dar, porque metade
    da web responde 403 para quem não é navegador --, o turno continua com os
    trechos.
    """

    def __init__(self, timeout: float = LEITURA_TIMEOUT, limite: int = MAX_PAGINA) -> None:
        self._timeout = timeout
        self._limite = limite

    async def ler(self, url: str) -> str | None:
        if not url.startswith(("http://", "https://")):
            return None
        if ".wikipedia.org/wiki/" in url:
            return await self._ler_wikipedia(url)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                # Sem User-Agent de navegador, uma parte grande da web devolve
                # 403 -- e aí a leitura nunca acrescentaria nada.
                headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
            ) as client:
                async with client.stream("GET", url) as r:
                    r.raise_for_status()
                    tipo = r.headers.get("content-type", "")
                    if "html" not in tipo and "text/plain" not in tipo:
                        # PDF, imagem, zip: baixar isso é gastar o orçamento do
                        # turno para não conseguir extrair texto no fim.
                        log.info("leitura ignorada, content-type %r: %s", tipo, url)
                        return None
                    pedacos: list[bytes] = []
                    total = 0
                    async for pedaco in r.aiter_bytes():
                        pedacos.append(pedaco)
                        total += len(pedaco)
                        if total >= LEITURA_MAX_BYTES:
                            break
                    bruto = b"".join(pedacos).decode(r.encoding or "utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 -- ver o docstring da classe
            log.info("nao consegui ler %s: %s", url, exc)
            return None

        texto = extrair_texto(bruto, self._limite)
        # Página que sobrou em nada -- muro de paywall, app em JavaScript, erro
        # renderizado -- é pior que nada: ocupa prompt e não informa.
        return texto if len(texto) >= 200 else None

    async def _ler_wikipedia(self, url: str) -> str | None:
        """O texto do artigo pela API, que é o caminho que eles pedem.

        `explaintext` devolve o artigo já sem marcação -- não passa pelo
        `extrair_texto`, e é mais limpo do que qualquer coisa que ele faria.
        """
        try:
            titulo = url.split("/wiki/", 1)[1].split("#")[0].split("?")[0]
            titulo = unquote(titulo).replace("_", " ")
            base = url.split("/wiki/", 1)[0]
            async with httpx.AsyncClient(
                timeout=self._timeout, headers={"User-Agent": USER_AGENT}
            ) as client:
                r = await client.get(
                    base + WIKI_API,
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "explaintext": "1",
                        # A URL do buscador costuma ser um redirecionamento
                        # ("Final da Copa..." -> o artigo de verdade).
                        "redirects": "1",
                        "format": "json",
                        "titles": titulo,
                    },
                )
                r.raise_for_status()
                paginas = (r.json().get("query") or {}).get("pages") or {}
        except Exception as exc:  # noqa: BLE001 -- ver o docstring da classe
            log.info("nao consegui ler a wikipedia %s: %s", url, exc)
            return None

        for pagina in paginas.values():
            texto = (pagina.get("extract") or "").strip()
            if len(texto) >= 200:
                return texto[: self._limite]
        return None


SEARCH_TOOLS: list[Tool] = [
    Tool(
        name="buscar_na_internet",
        description=(
            "Procura na internet e devolve trechos de paginas. Use SEMPRE que a "
            "resposta depender de fato que voce nao tem certeza absoluta, de algo "
            "recente, de preco, noticia, resultado, horario de funcionamento, ou "
            "de qualquer numero especifico. Melhor buscar do que arriscar errar. "
            "Nao use para timer, alarme ou musica, que tem ferramentas proprias."
        ),
        parameters={
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": (
                        "O que procurar, como se digitaria num buscador: poucas "
                        "palavras, sem 'por favor' e sem frase inteira."
                    ),
                }
            },
            "required": ["consulta"],
        },
    ),
]


def formatar(resultados: list[Resultado], pagina: str | None = None) -> str:
    """Os resultados como o modelo vai lê-los.

    Numerado e com o domínio à vista: o modelo precisa poder dizer "segundo a
    Wikipédia" sem ler uma URL inteira em voz alta, o que seria insuportável.

    O texto da página vai **depois** dos trechos e amarrado ao `[1]`. Dois mil
    caracteres soltos no fim do prompt, sem dono, é o formato que um modelo
    pequeno confunde com instrução; presos a uma fonte numerada, eles voltam a
    ser o que são -- mais material do primeiro resultado.
    """
    if not resultados:
        return "A busca nao devolveu nada."
    linhas = []
    for i, r in enumerate(resultados, 1):
        dominio = r.url.split("/")[2] if "//" in r.url else r.url
        linhas.append(f"[{i}] {r.titulo} ({dominio})\n{r.trecho}")
    texto = "\n\n".join(linhas)
    if pagina:
        primeiro = resultados[0].url
        dominio = primeiro.split("/")[2] if "//" in primeiro else "[1]"
        texto += f"\n\nTexto da pagina [1] ({dominio}):\n{pagina}"
    return texto


async def executar_busca(
    provider: SearchProvider | None,
    name: str,
    args: dict,
    leitor: Leitor | None = None,
) -> str:
    """Executa a busca e devolve o material bruto para o modelo sintetizar.

    Diferente das outras ferramentas, o que volta daqui **não** é a resposta: é
    o que a resposta deve usar. Quem redige a frase falada é o modelo, na rodada
    seguinte.

    Com `leitor`, o primeiro resultado é aberto e lido. Só o primeiro: abrir os
    cinco multiplicaria por cinco o pedaço mais caro do turno, e o buscador já
    ordenou -- se a resposta não está no primeiro, ela provavelmente também não
    estava no quarto.
    """
    if name != "buscar_na_internet":
        return f"falhou: ferramenta desconhecida {name}"
    if provider is None:
        return "falhou: a busca na internet nao esta configurada"

    consulta = str(args.get("consulta") or "").strip()
    if not consulta:
        return "falhou: nao disseram o que procurar"

    try:
        resultados = await provider.buscar(consulta, RESULTADOS)
    except Exception as exc:  # noqa: BLE001 -- a busca não pode derrubar o turno
        # Rede, cota, bloqueio, mudança de HTML do provedor: tudo dá no mesmo
        # para quem está falando com o aparelho, e nada disso pode virar
        # traceback no meio de um turno de voz.
        log.warning("busca falhou (%s): %s", provider.nome, exc)
        return "falhou: nao consegui buscar na internet agora"

    pagina = None
    if leitor is not None and resultados:
        pagina = await leitor.ler(resultados[0].url)

    log.info(
        "busca %r (%s): %d resultados, pagina %s",
        consulta,
        provider.nome,
        len(resultados),
        f"{len(pagina)} chars" if pagina else "nao lida",
    )
    return formatar(resultados, pagina)
