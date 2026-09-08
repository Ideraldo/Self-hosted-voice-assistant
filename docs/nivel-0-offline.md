# Nível 0: o que funciona com a internet caída

Timer, alarme, lembrete e a hora rodam **no dispositivo**, sem rede e sem LLM. É
a parte que precisa sobreviver a uma queda de internet, e a razão de a Fase 2 vir
antes do rosto e do wake word.

Decisões relacionadas: [D1](decisions.md), [D17](decisions.md),
[D26](decisions.md).

```powershell
# sem gateway nenhum rodando
.\.venv\Scripts\python.exe -m device.main --text
```

```
gateway: fora do ar -- timer, alarme e hora continuam
voce:   ideraldinho> Sao 20 horas e 2 minutos.   [nivel 0, local]
voce:   ideraldinho> Timer de 5 segundos.        [nivel 0, local]
  ideraldinho> Seu timer acabou.                 [timer]
```

O roteador olha a frase antes de qualquer coisa tocar a rede: se é nível 0 — um
timer, um alarme, a hora, "cancela", "o que eu tenho" — o dispositivo responde
sozinho. O que ele não reconhece sobe para o gateway (D17).

Os agendamentos vivem em SQLite (`SCHEDULES_DB`) porque um alarme tem que
sobreviver ao processo: a Pi reinicia, e acordar alguém é o único trabalho que
não pode depender de mais nada estar vivo.

**O roteador deliberadamente não adivinha.** Frase que não casa vai para o LLM,
porque um roteador que chuta é pior que roteador nenhum (plano, seção 5, regra 1).

## O que falta nesta fase, de propósito

- **Similaridade por embedding** para paráfrases, esperando um log real de nível
  2 dizer quais paráfrases as pessoas usam de verdade.
- **Controle de volume**, que é mixer do sistema operacional e específico de
  plataforma.
