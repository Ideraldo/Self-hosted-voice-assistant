from gateway.conversation.history import SYSTEM_PROMPT, Conversation


def test_system_prompt_is_always_first():
    """Prefix caching only works if the system prompt never moves."""
    conversation = Conversation()
    conversation.add_user("oi")
    conversation.add_assistant("oi!")
    prompt = conversation.prompt()
    assert prompt[0].role == "system"
    assert prompt[0].content == SYSTEM_PROMPT


def test_history_is_trimmed():
    conversation = Conversation(max_turns=2)
    for i in range(5):
        conversation.add_user(f"pergunta {i}")
        conversation.add_assistant(f"resposta {i}")
    turns = conversation.prompt()[1:]
    assert len(turns) == 4
    assert turns[0].content == "pergunta 3"


def test_rollback_desfaz_o_turno_que_nao_terminou():
    # O provedor do LLM caiu depois de a busca já ter ido para o histórico: o
    # turno inteiro sai, e não só a pergunta.
    conversation = Conversation()
    conversation.add_user("oi")
    conversation.add_assistant("Oi.")
    antes = conversation.checkpoint()

    conversation.add_user("qual a distancia da lua?")
    conversation.add_tool_call("buscar_na_internet", {"consulta": "lua"}, "call_1")
    conversation.add_tool_result("buscar_na_internet", "384.400 km", "call_1")
    conversation.rollback(antes)

    assert [m.content for m in conversation.prompt()[1:]] == ["oi", "Oi."]


def test_chamada_e_resultado_carregam_o_mesmo_id():
    # O Ollama casa o resultado com a chamada pela posição; as APIs de nuvem
    # casam pelo id, e recusam o histórico em que ele falta.
    conversation = Conversation()
    conversation.add_user("pausa a musica")
    conversation.add_tool_call("pausar_musica", {}, "call_1")
    conversation.add_tool_result("pausar_musica", "Pausado.", "call_1")

    _, chamada, resultado = conversation.prompt()[1:]
    assert chamada.tool_calls[0]["id"] == "call_1"
    assert resultado.tool_call_id == "call_1"


def _turno_com_busca(conversation: Conversation, i: int) -> None:
    conversation.add_user(f"pergunta {i}")
    conversation.add_tool_call("buscar_na_internet", {"consulta": str(i)}, f"call_{i}")
    conversation.add_tool_result("buscar_na_internet", f"trechos {i}", f"call_{i}")
    conversation.add_assistant(f"resposta {i}")


def test_o_corte_nunca_separa_a_chamada_do_resultado():
    # Cortando por contagem de mensagens, um turno de quatro mensagens saía pela
    # metade: o histórico passava a começar em `tool_calls`, `tool` ou
    # `assistant`, que é o que uma API de nuvem recusa.
    for max_turns in range(1, 8):
        conversation = Conversation(max_turns=max_turns)
        for i in range(12):
            _turno_com_busca(conversation, i)
            # Um turno simples no meio desalinha a conta de propósito.
            conversation.add_user(f"oi {i}")
            conversation.add_assistant(f"oi! {i}")
            mensagens = conversation.prompt()[1:]
            assert mensagens[0].role == "user", (max_turns, i)
            for anterior, atual in zip(mensagens, mensagens[1:]):
                if atual.role == "tool":
                    assert anterior.tool_calls, (max_turns, i)
                    assert anterior.tool_calls[0]["id"] == atual.tool_call_id


def test_o_corte_mantem_o_orcamento_de_mensagens():
    conversation = Conversation(max_turns=4)
    for i in range(10):
        _turno_com_busca(conversation, i)
    mensagens = conversation.prompt()[1:]
    # Oito mensagens de teto, turnos de quatro: cabem os dois últimos, inteiros.
    assert [m.content for m in mensagens if m.role == "user"] == ["pergunta 8", "pergunta 9"]


def test_o_turno_em_andamento_nunca_e_cortado():
    # Um turno maior que o orçamento inteiro (a busca que insiste) não pode
    # perder a própria pergunta: o histórico ficaria começando em `tool_calls`.
    conversation = Conversation(max_turns=1)
    conversation.add_user("oi")
    conversation.add_assistant("Oi.")
    conversation.add_user("pesquisa isso")
    for i in range(3):
        conversation.add_tool_call("buscar_na_internet", {"consulta": str(i)}, f"c{i}")
        conversation.add_tool_result("buscar_na_internet", "nada", f"c{i}")
    mensagens = conversation.prompt()[1:]
    assert mensagens[0].content == "pesquisa isso"
    assert len(mensagens) == 7
