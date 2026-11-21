"""Nível 0: o que o aparelho resolve sozinho, sem rede e sem LLM.

A regra 3 do plano é o motivo de este arquivo existir no dispositivo e não no
gateway: **a execução é sempre local, venha a intenção de onde vier.** O gateway
nunca é responsável por te acordar.

Cada método devolve a frase a falar. Nenhum deles fala: quem fala é o laço do
dispositivo, que é quem tem o Piper e a placa de som.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

from device.local.store import Schedule, ScheduleStore
from device.router.intents import Intent, normalize

#: Frases fixas. A regra 2 do plano manda pré-sintetizar as respostas do nível 0
#: para o aparelho parecer instantâneo. Com o Piper em RTF 0,05 isso ainda não
#: dói, mas é aqui que o cache entra quando doer -- por isso elas são constantes
#: e não f-strings espalhadas.
NADA_PARA_CANCELAR = "Nao tem nada marcado."
QUAL_CANCELAR = "Voce tem mais de um. Qual eu cancelo?"

#: As frases acima que são perguntas. Depois de falar uma delas o aparelho
#: reabre o microfone para ouvir a resposta (janela de continuação), e é por
#: esta lista -- e não procurando "?" no texto -- que ele sabe que perguntou.
PERGUNTAS: frozenset[str] = frozenset({QUAL_CANCELAR})


def pede_resposta(frase: str | None) -> bool:
    """A frase que o serviço devolveu é uma pergunta ao usuário?"""
    return frase in PERGUNTAS


def responder_qual_cancelar(texto: str) -> Intent | None:
    """Lê a resposta a "Qual eu cancelo?" e devolve o cancelamento com alvo.

    A resposta natural é "o timer", e isso não é comando nenhum: o roteador não
    casa, a frase subiria para o LLM, e o LLM nunca viu a pergunta -- o diálogo
    de nível 0 não passa pelo gateway. Então quem perguntou é quem interpreta.

    `None` é "não entendi como resposta": o chamador trata a frase como um
    turno novo qualquer.
    """
    palavras = set(normalize(texto).split())
    quer_timer = bool(palavras & {"timer", "timers", "cronometro"})
    quer_alarme = bool(palavras & {"alarme", "alarmes"})
    if quer_timer == quer_alarme:
        # Nenhum dos dois, ou os dois: escolher seria o chute que a pergunta
        # existiu para evitar.
        return None
    return Intent(name="cancelar", slots={"alvo": "timer" if quer_timer else "alarme"}, text=texto)


class LocalServices:
    """Timers, alarmes e a hora. Tudo o que precisa funcionar com a rede fora."""

    def __init__(
        self, store: ScheduleStore, on_change: Callable[[], None] | None = None
    ) -> None:
        self._store = store
        # Agendar não basta: o agendador está dormindo até o próximo disparo
        # conhecido, e um timer novo pode ser mais cedo que isso. Sem este aviso
        # ele só perceberia na próxima soneca -- foi exatamente o que aconteceu
        # na primeira execução de verdade: o timer de 5 segundos ficou pendente
        # porque o laço dormia 30.
        self.on_change: Callable[[], None] = on_change or (lambda: None)

    # -- despacho ----------------------------------------------------------

    def handle(self, intent: Intent) -> str | None:
        """Executa a intenção e devolve o que falar.

        `None` significa "não é minha" — o chamador manda para o gateway. É o
        caminho de escape que mantém a regra 1: na dúvida, sobe.
        """
        handler = {
            "criar_timer": self.criar_timer,
            "criar_alarme": self.criar_alarme,
            "cancelar": self.cancelar,
            "listar": self.listar,
            "que_horas": self.que_horas,
        }.get(intent.name)
        return handler(intent.slots) if handler else None

    # -- ações -------------------------------------------------------------

    def criar_timer(self, slots: dict) -> str:
        segundos = int(slots["segundos"])
        if segundos <= 0:
            raise ValueError("duracao tem que ser positiva")
        self._store.add("timer", fire_at=time.time() + segundos, label=None)
        self.on_change()
        # O regex já sabe a unidade que a pessoa falou ("dez minutos") e a
        # preserva; o LLM manda só `segundos`, e aí a unidade é deduzida. As
        # duas entradas terminam na mesma frase falada.
        if "quantidade" in slots and "unidade" in slots:
            duracao = _duracao_por_extenso(slots["quantidade"], slots["unidade"])
        else:
            duracao = _segundos_por_extenso(segundos)
        return f"Timer de {duracao}."

    def criar_alarme(self, slots: dict) -> str:
        hora, minuto = int(slots["hora"]), int(slots.get("minuto", 0))
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            raise ValueError(f"horario invalido: {hora}:{minuto}")
        slots = {"hora": hora, "minuto": minuto}
        alvo = _proxima_ocorrencia(slots["hora"], slots["minuto"])
        self._store.add("alarme", fire_at=alvo.timestamp(), label=None)
        self.on_change()
        quando = "amanha" if alvo.date() > datetime.now().date() else "hoje"
        return f"Alarme para as {_hora_por_extenso(slots['hora'], slots['minuto'])}, {quando}."

    def cancelar(self, slots: dict) -> str:
        alvo = slots.get("alvo")
        pendentes = self._store.pending(alvo) if alvo else self._store.pending()
        if not pendentes:
            return NADA_PARA_CANCELAR
        if len(pendentes) > 1 and not alvo:
            # Cancelar o errado é pior que perguntar. "Apaga a luz da sala"
            # virando "apaga tudo" é o exemplo do plano, e vale igual aqui.
            return QUAL_CANCELAR
        item = pendentes[0]
        self._store.cancel(item.id)
        self.on_change()
        return f"{item.kind.capitalize()} cancelado."

    def listar(self, slots: dict) -> str:
        pendentes = self._store.pending()
        if not pendentes:
            return "Nao tem nada marcado."
        partes = [_descrever(item) for item in pendentes]
        # "Voce tem" na frente porque esta frase vai ao ar como está: desde que
        # as ferramentas terminais existem, o LLM não a reescreve mais, e uma
        # lista solta ("um timer com 5 minutos restantes.") soa truncada.
        if len(partes) == 1:
            return f"Voce tem {partes[0]}."
        return "Voce tem " + ", ".join(partes[:-1]) + f" e {partes[-1]}."

    def que_horas(self, slots: dict) -> str:
        agora = datetime.now()
        return f"Sao {_hora_por_extenso(agora.hour, agora.minute)}."

    # -- disparo -----------------------------------------------------------

    def anunciar(self, item: Schedule) -> str:
        if item.kind == "timer":
            return "Seu timer acabou."
        if item.kind == "alarme":
            return "Hora de acordar."
        return f"Lembrete: {item.label}." if item.label else "Voce pediu para lembrar."


# -- formatação em português -----------------------------------------------
#
# O aparelho fala; não escreve. "07:05" é lido como "zero sete zero cinco" por
# qualquer TTS, então o texto que vai para o Piper já sai por extenso.


def _duracao_por_extenso(quantidade: int, unidade: str) -> str:
    plural = "s" if quantidade != 1 else ""
    return f"{quantidade} {unidade}{plural}"


def _segundos_por_extenso(segundos: int) -> str:
    """A duração dita como se fala, a partir de segundos crus.

    O LLM manda 5400; ninguém diz "timer de cinco mil e quatrocentos segundos".
    """
    if segundos % 3600 == 0:
        horas = segundos // 3600
        return f"{horas} hora" + ("s" if horas != 1 else "")
    if segundos >= 3600:
        horas, resto = divmod(segundos, 3600)
        minutos = resto // 60
        plural = "s" if horas != 1 else ""
        if minutos == 30:
            return f"{horas} hora{plural} e meia"
        return f"{horas} hora{plural} e {minutos} minutos"
    if segundos % 60 == 0:
        minutos = segundos // 60
        return f"{minutos} minuto" + ("s" if minutos != 1 else "")
    return f"{segundos} segundo" + ("s" if segundos != 1 else "")


def _hora_por_extenso(hora: int, minuto: int) -> str:
    """Como se fala a hora, não como se escreve.

    "20 e 1" é o que sai da forma ingênua, e soa errado dito em voz alta. Com
    minuto abaixo de dez a pessoa diz a unidade inteira: "vinte horas e um
    minuto". Foi ouvindo o aparelho falar "São 20 e 1" que isto apareceu.
    """
    if minuto == 0:
        return f"{hora} em ponto" if hora else "meia noite"
    if minuto == 30:
        return f"{hora} e meia"
    if minuto < 10:
        return f"{hora} horas e {minuto} minuto" + ("s" if minuto != 1 else "")
    return f"{hora} e {minuto}"


def _descrever(item: Schedule) -> str:
    if item.kind == "timer":
        restante = max(0, int(round(item.seconds_left())))
        if restante >= 60:
            return f"um timer com {restante // 60} minutos restantes"
        return f"um timer com {restante} segundos restantes"
    alvo = datetime.fromtimestamp(item.fire_at)
    return f"um alarme para as {_hora_por_extenso(alvo.hour, alvo.minute)}"


def _proxima_ocorrencia(hora: int, minuto: int, agora: datetime | None = None) -> datetime:
    """O próximo HH:MM. Hoje se ainda não passou, amanhã se já passou.

    É o que qualquer pessoa quer dizer com "me acorda às 7" às onze da noite, e
    é a diferença entre um despertador e um alarme que já nasce vencido.

    `agora` é injetável por causa do teste, não por elegância: a versão que lia
    o relógio por dentro só podia ser testada com horas relativas ao momento da
    execução, e isso quebrou à meia-noite e dois — "duas horas atrás" virou 22h,
    que àquela hora ainda estava no futuro. O código estava certo e o teste é
    que era refém do relógio.
    """
    agora = agora or datetime.now()
    alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return alvo + timedelta(days=1) if alvo <= agora else alvo
