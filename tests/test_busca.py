"""A busca, e a primeira página aberta.

A busca subiu para produção sem teste nenhum -- o que se testava era o modelo
usando ela, à mão, com rede de verdade. Isso pega a alucinação e não pega
nenhum dos casos abaixo, que são os que a web serve todo dia: 403 para quem não
parece navegador, PDF no primeiro resultado, paywall que renderiza uma frase,
timeout.

Nada aqui toca a rede. O que se testa é a regra que importa mais que as outras
nesta camada: **a leitura da página não pode derrubar o turno.** Ela é um
acréscimo -- quando falha, a busca responde o que já respondia antes.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.tools.search import (
    MAX_PAGINA,
    Leitor,
    Resultado,
    executar_busca,
    extrair_texto,
    formatar,
)

PAGINA = """
<html><head><title>t</title><style>body{color:red}</style></head>
<body>
  <nav><a href="/">Inicio</a><div>Assine a newsletter</div></nav>
  <h1>Dom Casmurro</h1>
  <p>Romance de Machado de Assis,   publicado em 1899.</p>
  <script>var x = "isso nao e texto";</script>
  <p>Bentinho narra a historia.</p>
  <footer>Todos os direitos reservados</footer>
</body></html>
"""

#: O primeiro **não** é a Wikipedia de propósito: ela tem caminho próprio
#: (TestWikipedia), e deixá-la aqui faria estes testes exercitarem a API sem
#: querer.
RESULTADOS = [
    Resultado("Dom Casmurro", "Romance de 1899.", "https://livros.exemplo.com/dom-casmurro"),
    Resultado("Machado", "Escritor.", "https://outro.exemplo.com/machado"),
]


class FakeProvider:
    nome = "fake"

    def __init__(self, resultados=None, erro=None):
        self._resultados = resultados or []
        self._erro = erro
        self.consultas: list[str] = []

    async def buscar(self, consulta, limite):
        self.consultas.append(consulta)
        if self._erro:
            raise self._erro
        return self._resultados[:limite]


def leitor_falso(monkeypatch, handler) -> Leitor:
    """Um Leitor com a rede trocada por `handler`."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = transport
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched)
    return Leitor(timeout=1.0)


def html(corpo: str, tipo: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(200, content=corpo.encode("utf-8"), headers={"content-type": tipo})


class TestExtrairTexto:
    def test_tira_marcacao_e_deixa_o_texto(self):
        texto = extrair_texto(PAGINA)
        assert "Dom Casmurro" in texto
        assert "publicado em 1899" in texto

    def test_pula_script_e_style(self):
        texto = extrair_texto(PAGINA)
        assert "isso nao e texto" not in texto
        assert "color:red" not in texto

    def test_pula_menu_e_rodape(self):
        # O `<div>` está DENTRO do `<nav>`: com um booleano em vez de pilha, o
        # fim do div religaria a coleta e o menu entraria no prompt.
        texto = extrair_texto(PAGINA)
        assert "newsletter" not in texto
        assert "direitos reservados" not in texto

    def test_colapsa_espaco_e_separa_paragrafo(self):
        texto = extrair_texto(PAGINA)
        assert "Machado de Assis, publicado" in texto
        assert "1899.\nBentinho" in texto

    def test_corta_no_limite(self):
        assert len(extrair_texto("<p>" + "a" * 9000 + "</p>", limite=100)) == 100

    def test_html_torto_nao_levanta(self):
        assert extrair_texto("<p>oi<div><span>tchau") == "oi\ntchau"


class TestLeitor:
    async def test_le_a_pagina(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: html(PAGINA * 4))
        texto = await leitor.ler("https://exemplo.com/x")
        assert texto is not None
        assert "Bentinho" in texto

    async def test_403_nao_levanta(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: httpx.Response(403))
        assert await leitor.ler("https://exemplo.com/x") is None

    async def test_timeout_nao_levanta(self, monkeypatch):
        def estoura(request):
            raise httpx.ReadTimeout("demorou", request=request)

        leitor = leitor_falso(monkeypatch, estoura)
        assert await leitor.ler("https://exemplo.com/x") is None

    async def test_pdf_nao_vira_texto(self, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                content=b"%PDF-1.4" + b"x" * 5000,
                headers={"content-type": "application/pdf"},
            )

        leitor = leitor_falso(monkeypatch, handler)
        assert await leitor.ler("https://exemplo.com/a.pdf") is None

    async def test_pagina_quase_vazia_vale_menos_que_nada(self, monkeypatch):
        # Paywall, app em JavaScript, erro renderizado: ocupa prompt e não
        # informa. Melhor devolver None e deixar os trechos falarem.
        leitor = leitor_falso(monkeypatch, lambda r: html("<p>Assine para ler.</p>"))
        assert await leitor.ler("https://exemplo.com/x") is None

    async def test_url_que_nao_e_http_nem_tenta(self, monkeypatch):
        chamou = []

        def handler(request):
            chamou.append(1)
            return html(PAGINA)

        leitor = leitor_falso(monkeypatch, handler)
        assert await leitor.ler("file:///etc/passwd") is None
        assert chamou == []

    async def test_se_identifica_em_vez_de_fingir_ser_navegador(self, monkeypatch):
        # Medido em 03/09/2026: httpx, Firefox, Chrome e um nome honesto abriram
        # exatamente os mesmos sites. Fingir navegador não comprava nada, e a
        # API da Wikipedia ainda exige o contrário -- um nome com contato.
        vistos = []

        def handler(request):
            vistos.append(request.headers.get("user-agent", ""))
            return html(PAGINA * 4)

        leitor = leitor_falso(monkeypatch, handler)
        await leitor.ler("https://exemplo.com/x")
        assert "Mozilla" not in vistos[0]
        assert "Marcos" in vistos[0] and "https://" in vistos[0]

    async def test_corta_pagina_gigante(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: html("<p>" + "palavra " * 50_000 + "</p>"))
        texto = await leitor.ler("https://exemplo.com/x")
        assert texto is not None and len(texto) <= MAX_PAGINA


class TestFormatar:
    def test_numera_e_mostra_o_dominio(self):
        saida = formatar(RESULTADOS)
        assert "[1] Dom Casmurro (livros.exemplo.com)" in saida
        assert "[2] Machado (outro.exemplo.com)" in saida

    def test_sem_resultado_diz_que_nao_achou(self):
        assert formatar([]) == "A busca nao devolveu nada."

    def test_a_pagina_vem_depois_e_amarrada_ao_primeiro(self):
        saida = formatar(RESULTADOS, "O texto inteiro da pagina.")
        assert saida.index("[2] Machado") < saida.index("O texto inteiro")
        assert "Texto da pagina [1] (livros.exemplo.com):" in saida


class TestExecutarBusca:
    async def test_sem_provedor_diz_que_nao_esta_configurada(self):
        assert "falhou" in await executar_busca(None, "buscar_na_internet", {"consulta": "x"})

    async def test_ferramenta_desconhecida(self):
        assert "falhou" in await executar_busca(FakeProvider(), "outra", {})

    async def test_consulta_vazia(self):
        assert "falhou" in await executar_busca(FakeProvider(), "buscar_na_internet", {})

    async def test_provedor_que_explode_nao_derruba_o_turno(self):
        p = FakeProvider(erro=RuntimeError("cota estourada"))
        saida = await executar_busca(p, "buscar_na_internet", {"consulta": "x"})
        assert saida == "falhou: nao consegui buscar na internet agora"

    async def test_sem_leitor_devolve_so_os_trechos(self):
        p = FakeProvider(RESULTADOS)
        saida = await executar_busca(p, "buscar_na_internet", {"consulta": "dom casmurro"})
        assert "Texto da pagina" not in saida

    async def test_com_leitor_acrescenta_a_pagina(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: html(PAGINA * 4))
        p = FakeProvider(RESULTADOS)
        saida = await executar_busca(p, "buscar_na_internet", {"consulta": "x"}, leitor)
        assert "Texto da pagina [1]" in saida
        assert "Bentinho" in saida

    async def test_leitura_falhando_devolve_o_que_devolvia_antes(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: httpx.Response(500))
        p = FakeProvider(RESULTADOS)
        saida = await executar_busca(p, "buscar_na_internet", {"consulta": "x"}, leitor)
        assert "[1] Dom Casmurro" in saida
        assert "Texto da pagina" not in saida

    async def test_le_so_o_primeiro_resultado(self, monkeypatch):
        pedidas = []

        def handler(request):
            pedidas.append(str(request.url))
            return html(PAGINA * 4)

        leitor = leitor_falso(monkeypatch, handler)
        await executar_busca(
            FakeProvider(RESULTADOS), "buscar_na_internet", {"consulta": "x"}, leitor
        )
        assert pedidas == ["https://livros.exemplo.com/dom-casmurro"]


class TestWikipedia:
    """O primeiro resultado mais comum de pergunta de conhecimento.

    A Wikipedia responde 403 a quem raspa `/wiki/...` -- com qualquer
    User-Agent, medido em 03/09/2026 -- e manda usar a API. Sem este caminho a
    leitura falhava justamente nas perguntas para as quais a busca existe.
    """

    def api(self, extract: str, monkeypatch, vistos: list | None = None) -> Leitor:
        def handler(request):
            if vistos is not None:
                vistos.append(request)
            assert "/w/api.php" in request.url.path
            return httpx.Response(
                200, json={"query": {"pages": {"1": {"title": "t", "extract": extract}}}}
            )

        return leitor_falso(monkeypatch, handler)

    async def test_usa_a_api_e_nao_raspa(self, monkeypatch):
        vistos = []
        leitor = self.api("Romance de Machado de Assis. " * 20, monkeypatch, vistos)
        texto = await leitor.ler("https://pt.wikipedia.org/wiki/Dom_Casmurro")
        assert texto is not None and "Machado" in texto
        assert vistos[0].url.params["titles"] == "Dom Casmurro"
        # `redirects`: a URL que o buscador devolve costuma ser um redirecionamento.
        assert vistos[0].url.params["redirects"] == "1"

    async def test_desfaz_o_escape_do_titulo(self, monkeypatch):
        vistos = []
        leitor = self.api("a" * 500, monkeypatch, vistos)
        await leitor.ler("https://pt.wikipedia.org/wiki/Cora%C3%A7%C3%A3o#Anatomia")
        assert vistos[0].url.params["titles"] == "Coração"

    async def test_manda_contato_no_user_agent(self, monkeypatch):
        # Sem uma URL de contato aqui a Wikimedia devolve 403: o mesmo pedido
        # passou a 200 só de acrescentá-la. Não é estilo, é o que abre a porta.
        vistos = []
        leitor = self.api("a" * 500, monkeypatch, vistos)
        await leitor.ler("https://pt.wikipedia.org/wiki/X")
        assert "https://" in vistos[0].headers["user-agent"]

    async def test_artigo_inexistente_devolve_nada(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, json={"query": {"pages": {"-1": {"missing": ""}}}})

        leitor = leitor_falso(monkeypatch, handler)
        assert await leitor.ler("https://pt.wikipedia.org/wiki/Nao_existe") is None

    async def test_api_fora_do_ar_nao_levanta(self, monkeypatch):
        leitor = leitor_falso(monkeypatch, lambda r: httpx.Response(503))
        assert await leitor.ler("https://en.wikipedia.org/wiki/X") is None
