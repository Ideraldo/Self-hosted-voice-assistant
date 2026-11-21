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
    conversation.add_tool_call("buscar_na_internet", {"consulta": "lua"})
    conversation.add_tool_result("buscar_na_internet", "384.400 km")
    conversation.rollback(antes)

    assert [m.content for m in conversation.prompt()[1:]] == ["oi", "Oi."]
