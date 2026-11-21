"""Conversation history and prompt assembly.

Deliberately outside ``llm/``: the system prompt and the history do not change
when the provider does. The system prompt is built once and always sits first,
because prefix caching weighs more on the bill than the model choice
(plan section 6).
"""

from __future__ import annotations

from gateway.llm.base import Message

SYSTEM_PROMPT = (
    "Voce e o Ideraldinho, um assistente de voz pessoal que responde em portugues do Brasil. "
    "Suas respostas sao lidas em voz alta, entao seja curto e direto: uma ou duas "
    "frases, sem listas, sem markdown, sem emoji. Se nao souber, diga que nao sabe. "
    # A regra abaixo nasceu falando so de timer e alarme, e por isso vazou: com
    # o Spotify ligado, "pausa a musica" recebeu "Pausando a musica." sem
    # nenhuma chamada de ferramenta -- e a musica continuou tocando. Um
    # assistente que diz ter feito o que nao fez e pior que um que recusa.
    "Toda acao -- marcar timer, marcar alarme, cancelar, tocar musica, pausar, "
    "pular, saber o que esta tocando -- so acontece se voce chamar a ferramenta "
    "correspondente. NUNCA diga que fez algo sem ter chamado a ferramenta, e "
    "nunca responda sobre o que esta tocando agora pelo que foi dito antes na "
    "conversa: consulte a ferramenta, porque a musica pode ter mudado. "
    # Sem esta regra o modelo deixava `tipo` no padrão e "toca minha playlist de
    # treino" virava busca por uma **faixa** chamada "minha playlist de treino".
    # O Spotify sempre devolve alguma coisa, então o aparelho tocava a música
    # errada anunciando que tinha acertado. Errar calado é o pior modo de falhar
    # num aparelho que só fala.
    "Ao tocar musica, diga sempre o tipo do que foi pedido -- musica, album, "
    "artista ou playlist -- e mande no nome apenas o nome, sem as palavras "
    "'playlist', 'album', 'disco' nem 'minha'. Se a pessoa disser playlist, o "
    "tipo e playlist, mesmo que voce nao conheca o nome dela. "
    # A busca devolve trechos de paginas, e nao uma frase pronta -- e a unica
    # ferramenta cujo resultado o modelo precisa mesmo redigir. Sem esta
    # instrucao ele le a lista numerada em voz alta, com URL e tudo.
    "Quando a busca na internet devolver trechos, responda em UMA ou DUAS frases "
    "faladas usando o que os trechos dizem. Nunca leia a lista, nunca leia "
    "endereco de site, e diga a fonte pelo nome quando ela importar. Se os "
    "trechos nao responderem, diga que nao achou."
)


class Conversation:
    """A single device's rolling history, trimmed by whole turns."""

    def __init__(self, max_turns: int = 12) -> None:
        self._max_turns = max_turns
        self._turns: list[Message] = []

    def add_user(self, text: str) -> None:
        self._turns.append(Message(role="user", content=text))
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._turns.append(Message(role="assistant", content=text))
        self._trim()

    def add_tool_call(self, name: str, args: dict, call_id: str) -> None:
        """Registra que o assistente **pediu** a ferramenta.

        Junto com `add_tool_result`, forma o par que os modelos foram treinados
        a ver. Guardar só a frase falada -- que foi o que este código fez até a
        D22 -- ensina o modelo que, nesta conversa, ação se responde com prosa:
        no turno seguinte ele dizia "Pausando a música." sem pausar nada.

        O `call_id` é o que amarra o par fora do Ollama: a API da OpenAI e a da
        Anthropic recusam um resultado que não aponta para a sua chamada.
        """
        self._turns.append(
            Message(
                role="assistant",
                content="",
                tool_calls=[{"id": call_id, "function": {"name": name, "arguments": args}}],
            )
        )
        self._trim()

    def has_tool_call(self, call_id: str) -> bool:
        """Já existe uma chamada com este id no histórico?

        Há provedor que repete id entre respostas (`call_0` em todas). Dentro de
        uma resposta isso não confunde ninguém; num histórico de vários turnos,
        são dois resultados apontando para a mesma chamada.
        """
        return any(
            chamada.get("id") == call_id
            for mensagem in self._turns
            for chamada in mensagem.tool_calls or []
        )

    def add_tool_result(self, name: str, result: str, call_id: str) -> None:
        """O que a ferramenta devolveu, para o modelo formular a resposta.

        Entra como papel `tool` e não como `user`: o modelo precisa saber que
        isto é resultado de uma ação que ele pediu, não uma coisa nova que a
        pessoa disse. Confundir os dois faz o modelo responder ao resultado como
        se fosse uma pergunta.
        """
        self._turns.append(
            Message(role="tool", content=f"{name}: {result}", tool_call_id=call_id)
        )
        self._trim()

    def checkpoint(self) -> list[Message]:
        """O histórico como está agora, para poder voltar a ele.

        É uma cópia e não um índice de propósito: `_trim` apaga do começo, então
        "as últimas N mensagens" deixa de apontar para o mesmo lugar no meio de
        um turno longo.
        """
        return list(self._turns)

    def rollback(self, checkpoint: list[Message]) -> None:
        """Desfaz o turno que não terminou.

        Um turno pela metade é pior que turno nenhum: a pergunta sem resposta
        vira duas mensagens `user` seguidas, e o par `tool_call` + `tool` sem a
        frase que o fecha é um histórico que o modelo nunca viu (D22). Nos dois
        casos quem paga é o turno seguinte, que não tem nada a ver com a falha.
        """
        self._turns = list(checkpoint)

    def prompt(self) -> list[Message]:
        return [Message(role="system", content=SYSTEM_PROMPT), *self._turns]

    def _trim(self) -> None:
        """Apara o começo do histórico, sempre na fronteira de um turno.

        O orçamento continua sendo `max_turns * 2` mensagens -- a conta de
        quando todo turno era pergunta e resposta. O que mudou é onde o corte
        cai: contando mensagens, um turno com ferramenta (quatro ou mais) saía
        pela metade, e o histórico passava a começar num `tool` sem a chamada,
        ou numa resposta sem a pergunta. O Ollama engole isso; uma API de nuvem
        devolve 400 em todos os turnos seguintes, até o pedaço sair sozinho.

        Por isso o corte anda de `user` em `user`, e o turno em andamento nunca
        sai: se ele sozinho passa do orçamento, o histórico fica só com ele.
        """
        limite = self._max_turns * 2
        if len(self._turns) <= limite:
            return
        inicios = [i for i, m in enumerate(self._turns) if m.role == "user"]
        if not inicios:
            return
        corte = next((i for i in inicios if len(self._turns) - i <= limite), inicios[-1])
        del self._turns[:corte]
