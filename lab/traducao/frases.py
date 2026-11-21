"""O conjunto fixo de tradução pt-BR <-> en. Todo modelo traduz exatamente isto.

Mesmo princípio de ``lab/phrases.py``: comparar modelos só significa alguma
coisa se a entrada for idêntica. Aqui cada item é um par, e o mesmo par serve
aos dois sentidos -- no pt->en a referência é o inglês, no en->pt é o português.

As frases são o que um tradutor de bolso ouve: curtas, faladas, de viagem.
Cada categoria cutuca um ponto onde tradução automática costuma quebrar:

  viagem / restaurante / direcoes / compras / saude
              o grosso do uso real; frases de uma linha
  conversa    registro informal, "você" x "tu", tratamento
  numeros     preço, hora, data, telefone -- erro aqui custa dinheiro
  nomes       nome próprio e lugar: tem que passar intacto
  longa       duas ou três orações; mede se o modelo perde o fio
  coloquial   pt-BR de verdade ("tô", "a gente", "beleza"). É onde um modelo
              treinado em legenda e ata de parlamento se entrega
  bancada     as sete frases de ``lab/phrases.py``, para ligar esta medição
              às de STT e TTS

As referências foram escritas à mão, uma por frase. chrF contra **uma**
referência pune tradução certa com outras palavras ("restroom" x "bathroom");
serve para ranquear modelos entre si, não como nota absoluta. Por isso o runner
imprime as saídas: o olho decide.

O português das referências é **brasileiro** de propósito ("ônibus", "banheiro",
"café da manhã", "celular", "você"). Um modelo que devolve "autocarro", "casa de
banho" e "pequeno-almoço" perde ponto aqui -- e deve perder.
"""

from __future__ import annotations

from dataclasses import dataclass

from lab.phrases import PHRASES


@dataclass(frozen=True)
class Par:
    chave: str
    categoria: str
    pt: str
    en: str


def _p(chave: str, categoria: str, pt: str, en: str) -> Par:
    return Par(chave, categoria, pt, en)


PARES: list[Par] = [
    # --- viagem ---------------------------------------------------------
    _p("banheiro", "viagem", "Onde fica o banheiro?", "Where is the bathroom?"),
    _p("reserva", "viagem", "Eu tenho uma reserva no nome de Silva.",
       "I have a reservation under the name Silva."),
    _p("mala", "viagem", "Minha mala não chegou.", "My suitcase didn't arrive."),
    _p("onibus", "viagem", "Que horas sai o próximo ônibus para o aeroporto?",
       "What time does the next bus to the airport leave?"),
    _p("wifi", "viagem", "Qual é a senha do wi-fi?", "What is the wi-fi password?"),
    _p("checkout", "viagem", "Até que horas é o check-out?",
       "What time is check-out?"),
    _p("passagem", "viagem", "Quero duas passagens de ida e volta, por favor.",
       "I'd like two round-trip tickets, please."),
    _p("cafe_manha", "viagem", "O café da manhã está incluído?",
       "Is breakfast included?"),
    # --- restaurante ----------------------------------------------------
    _p("mesa", "restaurante", "Uma mesa para duas pessoas, por favor.",
       "A table for two, please."),
    _p("conta", "restaurante", "A conta, por favor.", "The check, please."),
    _p("alergia", "restaurante", "Eu sou alérgico a amendoim.",
       "I'm allergic to peanuts."),
    _p("sem_carne", "restaurante", "Esse prato tem carne? Eu sou vegetariana.",
       "Does this dish have meat? I'm a vegetarian."),
    _p("agua", "restaurante", "Me vê uma água sem gás e um suco de laranja.",
       "Can I get a still water and an orange juice?"),
    _p("recomenda", "restaurante", "O que você recomenda?",
       "What do you recommend?"),
    _p("cartao", "restaurante", "Vocês aceitam cartão de crédito?",
       "Do you accept credit cards?"),
    # --- direções -------------------------------------------------------
    _p("metro", "direcoes", "Como eu chego na estação de metrô mais próxima?",
       "How do I get to the nearest subway station?"),
    _p("esquerda", "direcoes",
       "Vire à esquerda no segundo semáforo e siga em frente.",
       "Turn left at the second traffic light and go straight ahead."),
    _p("longe", "direcoes", "É muito longe daqui? Dá para ir a pé?",
       "Is it very far from here? Can I walk there?"),
    _p("perdido", "direcoes", "Estou perdido. Você pode me mostrar no mapa?",
       "I'm lost. Can you show me on the map?"),
    _p("taxi", "direcoes", "Pode me levar para este endereço?",
       "Can you take me to this address?"),
    # --- compras --------------------------------------------------------
    _p("quanto", "compras", "Quanto custa isso?", "How much does this cost?"),
    _p("caro", "compras", "Está muito caro. Tem desconto?",
       "It's too expensive. Is there a discount?"),
    _p("tamanho", "compras", "Você tem esta camisa em um tamanho maior?",
       "Do you have this shirt in a larger size?"),
    _p("olhando", "compras", "Só estou olhando, obrigado.",
       "I'm just looking, thank you."),
    # --- saúde / emergência ---------------------------------------------
    _p("medico", "saude", "Preciso de um médico.", "I need a doctor."),
    _p("farmacia", "saude", "Tem uma farmácia aqui perto?",
       "Is there a pharmacy near here?"),
    _p("dor", "saude", "Estou com dor de cabeça e febre desde ontem.",
       "I've had a headache and a fever since yesterday."),
    _p("celular", "saude", "Roubaram meu celular e minha carteira.",
       "My cell phone and my wallet were stolen."),
    # --- conversa -------------------------------------------------------
    _p("prazer", "conversa", "Prazer em conhecer você.", "Nice to meet you."),
    _p("de_onde", "conversa", "De onde você é?", "Where are you from?"),
    _p("brasil", "conversa", "Eu sou do Brasil e estou aqui de férias.",
       "I'm from Brazil and I'm here on vacation."),
    _p("devagar", "conversa", "Você pode falar mais devagar, por favor?",
       "Can you speak more slowly, please?"),
    _p("nao_entendi", "conversa", "Desculpa, eu não entendi.",
       "Sorry, I didn't understand."),
    _p("trabalho", "conversa", "Eu trabalho com programação.",
       "I work in programming."),
    _p("ajuda", "conversa", "Muito obrigado pela ajuda!",
       "Thank you very much for your help!"),
    # --- números, preços, horas -----------------------------------------
    _p("preco", "numeros", "São trinta e cinco dólares e cinquenta centavos.",
       "That's thirty-five dollars and fifty cents."),
    _p("horario", "numeros", "O trem sai às dezoito e quarenta e cinco.",
       "The train leaves at six forty-five p.m."),
    _p("data", "numeros", "Meu voo é no dia 14 de março, às 7h30 da manhã.",
       "My flight is on March 14th at 7:30 in the morning."),
    _p("telefone", "numeros", "Meu número é 11 98765-4321.",
       "My number is 11 98765-4321."),
    _p("quarto", "numeros", "Estou no quarto 312, no terceiro andar.",
       "I'm in room 312, on the third floor."),
    _p("troco", "numeros", "Dei uma nota de cem e o troco veio errado.",
       "I gave a hundred-dollar bill and the change was wrong."),
    # --- nomes próprios -------------------------------------------------
    _p("nome", "nomes", "Meu nome é Ideraldo e esta é a minha esposa, Fernanda.",
       "My name is Ideraldo and this is my wife, Fernanda."),
    _p("cidade", "nomes", "Nós moramos em Belo Horizonte, em Minas Gerais.",
       "We live in Belo Horizonte, in Minas Gerais."),
    _p("rua", "nomes", "O hotel fica na Rua Augusta, perto da Avenida Paulista.",
       "The hotel is on Rua Augusta, near Avenida Paulista."),
    # --- frases longas --------------------------------------------------
    _p("longa_voo", "longa",
       "Meu voo foi cancelado por causa do mau tempo e eu preciso remarcar "
       "para amanhã de manhã, de preferência antes das dez.",
       "My flight was canceled because of the bad weather and I need to "
       "rebook it for tomorrow morning, preferably before ten."),
    _p("longa_hotel", "longa",
       "O ar-condicionado do meu quarto não está funcionando e o chuveiro só "
       "tem água fria. Vocês podem mandar alguém para consertar ou me trocar "
       "de quarto?",
       "The air conditioning in my room isn't working and the shower only "
       "has cold water. Can you send someone to fix it or move me to another "
       "room?"),
    _p("longa_remedio", "longa",
       "Eu tomo este remédio todos os dias para pressão alta, mas esqueci a "
       "caixa em casa. Preciso de receita para comprar aqui?",
       "I take this medicine every day for high blood pressure, but I left "
       "the box at home. Do I need a prescription to buy it here?"),
    # --- coloquialismos brasileiros (os difíceis) -------------------------
    _p("to_fome", "coloquial", "Tô morrendo de fome, bora comer alguma coisa?",
       "I'm starving, let's go eat something?"),
    _p("a_gente", "coloquial", "A gente vai pegar um Uber porque tá chovendo.",
       "We're going to take an Uber because it's raining."),
    _p("beleza", "coloquial", "E aí, beleza? Tudo certo com você?",
       "Hey, what's up? Is everything all right with you?"),
    _p("legal", "coloquial", "Nossa, que legal! Valeu mesmo, cara.",
       "Wow, that's so cool! Thanks a lot, man."),
    _p("saudade", "coloquial", "Tô com saudade de casa.", "I miss home."),
    _p("fica_frio", "coloquial", "Fica tranquilo, não tem problema nenhum.",
       "Don't worry, it's no problem at all."),
    _p("pegadinha", "coloquial", "Ele me deu um bolo e eu fiquei de cara.",
       "He stood me up and I was shocked."),
    _p("troco_miudo", "coloquial", "Você tem um trocado aí pra me emprestar?",
       "Do you have some spare change you could lend me?"),
    # --- as frases da bancada (lab/phrases.py) --------------------------
    _p("b_curta", "bancada", PHRASES["curta"], "Ten-minute timer."),
    _p("b_hora", "bancada", PHRASES["hora"],
       "It's already a quarter to seven, time to wake up."),
    _p("b_numeros", "bancada", PHRASES["numeros"],
       "The ZIP code is 04538-133 and the total came to R$ 1,249.90."),
    _p("b_estrangeirismo", "bancada", PHRASES["estrangeirismo"],
       "Play the Discover Weekly playlist on the living room Spotify."),
    _p("b_pergunta", "bancada", PHRASES["pergunta"],
       "Can you turn on the bedroom light and turn down the volume, please?"),
    _p("b_longa", "bancada", PHRASES["longa"],
       "According to the forecast, it will rain tomorrow afternoon in São "
       "Paulo, with a high of twenty-three degrees and a low of seventeen. "
       "Do you want me to set the alarm earlier?"),
    _p("b_acentos", "bancada", PHRASES["acentos"],
       "José's grandfather puts sugar on his bread at three in the morning."),
]

#: Aquecimento: curta e sem ambiguidade, igual ao WARMUP de ``lab/phrases.py``.
AQUECIMENTO = "banheiro"

#: Marcas de português europeu numa saída en->pt. Não é dicionário, é detector
#: barato: basta uma para a frase soar errada na boca de um aparelho brasileiro.
#: Só entra aqui o que é inequívoco -- "bilhete" e "gelado" existem no Brasil,
#: então ficam de fora. São expressões regulares, aplicadas em minúsculas.
MARCAS_PT_PT: dict[str, str] = {
    # vocabulário
    r"\bautocarros?\b": "autocarro (ônibus)",
    r"\bcomboios?\b": "comboio (trem)",
    r"\bcasa de banho\b": "casa de banho (banheiro)",
    r"\bpequeno[- ]almoço\b": "pequeno-almoço (café da manhã)",
    r"\btelemóve(l|is)\b": "telemóvel (celular)",
    r"\bsumos?\b": "sumo (suco)",
    r"\bparagem\b": "paragem (ponto/parada)",
    r"\bementa\b": "ementa (cardápio)",
    r"\becrã\b": "ecrã (tela)",
    # só "estação de metro": sozinho, "metro" é a unidade de medida
    r"\b(estação|linha) d[eo] metro\b": "metro (metrô)",
    r"\bduche\b": "duche (chuveiro)",
    r"\bmoradas?\b": "morada (endereço)",
    r"\bfixe\b": "fixe (legal)",
    r"\bgajo\b|\bpá\b": "gajo/pá (cara)",
    # ortografia
    r"\bequipa\b|\bregisto\b|\bcontacto\b|\bfacto\b": "grafia pt-PT",
    r"\b(direcção|acção|óptimo|eléctric\w+|actual\w*)\b": "grafia pré-acordo",
    r"\b(quilómetros?|económic\w+|bebé|António)\b": "acento pt-PT",
    # gramática: "estou a fazer" em vez de "estou fazendo"
    r"\best(ou|á|ás|amos|ão|ava|avam)( só| mesmo)? a \w+(ar|er|ir)\b": "estar a + infinitivo",
    # tratamento por "tu" com a conjugação europeia
    r"\b(tens|podes|queres|estás|és|sabes|falas|recomendas|aceitas)\b": "2a pessoa (tu)",
    r"\b(teu|tua|contigo)\b": "tratamento por tu",
}
