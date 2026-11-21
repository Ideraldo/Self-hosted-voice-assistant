"""Provedor para o formato Chat Completions da OpenAI, em httpx puro.

Um cliente só cobre quase todo mundo: OpenAI, o endpoint compatível do Gemini,
Groq, Cerebras, DeepSeek, Mistral, OpenRouter e o `/v1` do próprio Ollama falam
este mesmo formato. Sem SDK de propósito -- a imagem do gateway não carrega
nenhum (D15), e o que um SDK faria aqui é um POST e um laço sobre linhas `data:`.

O que muda de um provedor para outro não está no código: endereço, chave,
modelo e os campos opcionais do pedido vêm do `.env`. Adivinhar por provedor
qual campo desliga o raciocínio envelheceria em meses; quem configura sabe com
quem está falando.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx

from gateway.llm.base import Delta, Message, Tool

log = logging.getLogger("ideraldinho.llm")


def _wire(message: Message, extras: dict[str, dict] | None = None) -> dict[str, Any]:
    """Uma mensagem do histórico no formato da OpenAI.

    Diferente do Ollama (ver `gateway/llm/ollama.py`), aqui o id viaja nos dois
    lados do par: é por ele que a API casa resultado e chamada, e sem ele o
    pedido inteiro volta 400. `arguments` vai como **texto** JSON, não objeto --
    é assim que o formato define, e é assim que ele chega no fluxo.

    O resultado vai como está guardado, "nome: resultado". O formato não tem
    campo para o nome na mensagem `tool` (quem liga é o id), então o prefixo é
    redundante -- mas é o mesmo texto que a D22 mediu, é o que o reserva vai
    ler se o turno seguinte cair nele, e tirá-lo seria desmontar uma string que
    outro módulo montou. Custa três tokens.
    """
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if not message.tool_calls:
        return {"role": message.role, "content": message.content}
    return {
        "role": "assistant",
        # `null`, e não "": é o que a própria API devolve numa mensagem que só
        # pede ferramenta, e há provedor que recusa texto vazio.
        "content": message.content or None,
        "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {
                    "name": c["function"]["name"],
                    "arguments": json.dumps(c["function"]["arguments"], ensure_ascii=False),
                },
                **(extras or {}).get(c["id"], {}),
            }
            for c in message.tool_calls
        ],
    }


def _texto(content: Any) -> str:
    """O texto de um `delta.content`, que nem sempre é texto.

    Numa resposta que só pede ferramenta ele vem `null`. E há provedor que o
    manda como lista de partes tipadas, com o raciocínio numa parte
    `thinking`: só as partes `text` são resposta.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p["text"]
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)
        )
    return ""


class _SemPensamento:
    """Tira `<think>...</think>` do texto, mesmo com a marca partida em pedaços.

    O raciocínio bem comportado vem em campo separado (`reasoning_content`,
    `reasoning`), e esse o provedor simplesmente não lê. Mas há modelo servido
    com o rascunho **dentro** do conteúdo, entre estas marcas -- e aqui texto
    vira fala: é o aparelho dizendo "Okay, the user is asking..." (D20).
    """

    def __init__(self) -> None:
        self._resto = ""
        self._dentro = False

    def __call__(self, texto: str) -> str:
        self._resto += texto
        saida = ""
        while True:
            marca = "</think>" if self._dentro else "<think>"
            antes, achou, depois = self._resto.partition(marca)
            if not achou:
                break
            if not self._dentro:
                saida += antes
            self._dentro = not self._dentro
            self._resto = depois
        # O fim do pedaço pode ser o começo de uma marca ("<thi"). Esse rabo
        # espera o pedaço seguinte; o resto já pode sair.
        rabo = next(
            (n for n in range(len(marca) - 1, 0, -1) if self._resto.endswith(marca[:n])), 0
        )
        corte = len(self._resto) - rabo
        if not self._dentro:
            saida += self._resto[:corte]
        self._resto = self._resto[corte:]
        return saida

    def fim(self) -> str:
        """O que ficou esperando uma marca que não veio: era texto mesmo."""
        return "" if self._dentro else self._resto


def _juntar(chamadas: dict[int, dict[str, Any]], pedaco: dict[str, Any]) -> None:
    """Soma um fragmento de chamada de ferramenta ao que já chegou dela.

    No fluxo, a chamada vem aos pedaços: o primeiro traz `id` e nome, os
    seguintes só mais um trecho do texto de `arguments`, e o que diz de qual
    chamada é cada pedaço é o `index`. Há servidor compatível que não manda
    `index` e entrega a chamada inteira de uma vez: aí pedaço com nome abre uma
    chamada nova, e pedaço sem nome continua a última.
    """
    funcao = pedaco.get("function") or {}
    indice = pedaco.get("index")
    if indice is None:
        indice = len(chamadas) if funcao.get("name") else max(len(chamadas) - 1, 0)
    chamada = chamadas.setdefault(indice, {"id": None, "name": None, "arguments": "", "extras": {}})
    chamada["id"] = chamada["id"] or pedaco.get("id") or None
    chamada["name"] = chamada["name"] or funcao.get("name") or None
    argumentos = funcao.get("arguments") or ""
    # Fora do padrão, mas existe: `arguments` já como objeto.
    chamada["arguments"] += argumentos if isinstance(argumentos, str) else json.dumps(argumentos)
    if pedaco.get("extra_content"):
        chamada["extras"]["extra_content"] = pedaco["extra_content"]


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        reasoning_effort: str | None = None,
        temperature: float | None = None,
        extra_body: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._api_key = api_key
        # Só vai no pedido o que foi configurado. Os provedores divergem
        # justamente nestes campos, e vários recusam com 400 o que não conhecem:
        # mandar `reasoning_effort` "por via das dúvidas" derruba todo turno.
        self._opcoes: dict[str, Any] = dict(extra_body or {})
        if reasoning_effort:
            self._opcoes["reasoning_effort"] = reasoning_effort
        if temperature is not None:
            self._opcoes["temperature"] = temperature
        # Um cliente para a vida do provedor, e não um por pedido: num turno com
        # busca são dois pedidos em sequência, e o segundo reaproveita a conexão
        # em vez de pagar outro handshake TLS antes da primeira palavra.
        self._client = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        )
        # Campos que o provedor pendura na chamada e exige de volta no pedido
        # seguinte (o Gemini faz isso com a assinatura do raciocínio, em
        # `extra_content`). São opacos e só fazem sentido para quem os emitiu,
        # então ficam aqui, por id, em vez de entrar no histórico comum.
        self._extras: dict[str, dict[str, Any]] = {}

    def _sem_chave(self, texto: str) -> str:
        """Há API que devolve a chave recebida dentro da mensagem de erro."""
        return texto.replace(self._api_key, "***") if self._api_key else texto

    async def respond(
        self,
        history: list[Message],
        tools: list[Tool],
    ) -> AsyncIterator[Delta]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_wire(m, self._extras) for m in history],
            "stream": True,
            **self._opcoes,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                }
                for t in tools
            ]

        chamadas: dict[int, dict[str, Any]] = {}
        sem_pensamento = _SemPensamento()
        terminou = False

        async with self._client.stream("POST", self._url, json=payload) as response:
            if response.status_code >= 400:
                # O corpo diz o que o status não diz: modelo inexistente, campo
                # recusado, cota estourada. Só um trecho, e sem a chave.
                corpo = (await response.aread()).decode("utf-8", "replace")
                trecho = self._sem_chave(" ".join(corpo.split())[:300])
                raise RuntimeError(f"HTTP {response.status_code} de {self._url}: {trecho}")

            async for line in response.aiter_lines():
                # Linha em branco separa eventos; linha começando em ":" é
                # comentário de keep-alive. Só `data:` carrega alguma coisa.
                if not line.startswith("data:"):
                    continue
                dado = line[5:].strip()
                if dado == "[DONE]":
                    terminou = True
                    break
                chunk = json.loads(dado)
                if chunk.get("error"):
                    # Erro depois do 200: o fluxo já tinha começado.
                    raise RuntimeError(self._sem_chave(f"erro no fluxo: {chunk['error']}"[:300]))

                # `choices` vazio é normal: o pedaço de `usage` vem assim.
                escolha = (chunk.get("choices") or [{}])[0]
                delta = escolha.get("delta") or {}
                for pedaco in delta.get("tool_calls") or []:
                    _juntar(chamadas, pedaco)

                # Só `content`. `reasoning_content`, `reasoning`, `thinking` e
                # o que mais inventarem são rascunho, e rascunho não vira fala.
                texto = sem_pensamento(_texto(delta.get("content")))
                if texto:
                    yield Delta(text=texto)

                if escolha.get("finish_reason"):
                    terminou = True

        # Fluxo que acaba sem `finish_reason` nem `[DONE]` foi cortado no meio,
        # ainda que a conexão tenha fechado com educação. Meia chamada de
        # ferramenta não se executa, e meia resposta não se dá por inteira.
        if not terminou:
            raise RuntimeError("o fluxo terminou antes do fim da resposta")

        resto = sem_pensamento.fim()
        if resto:
            yield Delta(text=resto)

        if not chamadas:
            return
        if len(chamadas) > 1:
            # A sessão executa uma ferramenta por rodada (ela para no primeiro
            # Delta com chamada). Entregar só a primeira mantém o histórico
            # válido -- uma chamada, um resultado -- e o modelo, vendo o
            # resultado, pede a seguinte na próxima rodada.
            log.warning(
                "modelo pediu %d ferramentas de uma vez; so a primeira e executada: %s",
                len(chamadas),
                [c["name"] for _, c in sorted(chamadas.items())],
            )
        # Aqui a resposta HTTP já está fechada: a sessão abandona o gerador
        # neste `yield`, e não fica conexão aberta esperando o coletor de lixo.
        yield self._chamada(chamadas[min(chamadas)])

    def _chamada(self, chamada: dict[str, Any]) -> Delta:
        """A chamada completa vira um Delta -- ou um erro, se não fechar.

        Executar com argumentos pela metade é pior que não executar: um timer
        sem duração, uma busca sem consulta. A exceção encerra o turno pelo
        caminho de sempre (`LLMIndisponivel`), e o histórico volta ao que era.
        """
        try:
            args = json.loads(chamada["arguments"] or "{}")
        except ValueError as exc:
            raise ValueError(
                f"argumentos invalidos para {chamada['name']}: {chamada['arguments'][:120]!r}"
            ) from exc
        if not chamada["name"] or not isinstance(args, dict):
            raise ValueError(f"chamada de ferramenta malformada: {chamada!r}"[:200])

        if chamada["id"] and chamada["extras"]:
            self._extras[chamada["id"]] = chamada["extras"]
            # Só as chamadas recentes voltam num pedido; o teto impede que um
            # processo de meses acumule assinaturas de conversas já cortadas.
            while len(self._extras) > 256:
                del self._extras[next(iter(self._extras))]
        return Delta(tool_name=chamada["name"], tool_args=args, tool_call_id=chamada["id"])
