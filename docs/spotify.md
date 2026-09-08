# Spotify

Onze ferramentas de música, e as primeiras que o **gateway executa** em vez de
encaminhar: o `client_secret` e o refresh token moram no servidor e nunca descem
pelo fio (D21).

Decisões relacionadas: [D21](decisions.md), [D23](decisions.md),
[D32](decisions.md).

---

## Setup, uma vez só

```powershell
# 1. https://developer.spotify.com/dashboard -> Create app
#    O Redirect URI tem que ser exatamente http://127.0.0.1:8888/callback
#    (o Spotify recusa "localhost"; só 127.0.0.1)
# 2. Ponha o client id e o secret no .env
# 3. Autorize uma vez; o refresh token cai em gateway/data/ (fora do git)
.\.venv\Scripts\python.exe -m gateway.tools.spotify_auth
```

Controle de playback exige **Spotify Premium** — a API responde 403 sem ele, e o
cliente transforma isso numa frase falada em vez de num traceback.

Sem credenciais configuradas, as ferramentas de música **não são declaradas ao
modelo**, e o `/health` reporta `"spotify": "off"`. Isso vem da D19: um modelo
pequeno que vê uma ferramenta indisponível tenta usar assim mesmo. O que ele faz
em vez disso é admitir que não sabe:

```
voce> toca chico buarque
ideraldinho> Nao sei tocar Chico Buarque.
        Posso ajudar com timers, alarmes ou listar/agendar coisas?
```

### Reautorizar depois de mudar o escopo

Mudar a constante `SCOPES` **não basta**. O escopo congela no refresh token no
momento da autorização: um token antigo continua válido, com o escopo antigo, e
as chamadas novas voltam 403 — que este código traduzia como "precisa de
Premium", dito em voz alta para quem tem Premium.

```powershell
del gateway\data\spotify_token.json
.\.venv\Scripts\python.exe -m gateway.tools.spotify_auth
```

O arquivo de token agora registra o escopo concedido, e o cliente confere antes
de chamar. Token gravado por uma versão anterior não tem esse campo, e aí
assume-se o escopo antigo — que é o que ele de fato tem.

---

## O que se pode tocar: o campo `tipo`

`tocar_musica` exige um `tipo` — `musica`, `album`, `artista` ou `playlist` — e é
esse campo que decide se toca a coisa certa.

Antes dele toda busca era `type=track`. Pedir um **artista** achava uma faixa
qualquer dele e tocava o álbum *daquela* faixa, que podia ser um ao vivo de 1999.
Funcionava por acidente, e o acidente era audível.

Cada tipo toca de um jeito, e nenhum dos três corpos é intercambiável:

| Tipo | Corpo do `/play` | Por quê |
|---|---|---|
| `musica` | `context_uri` do álbum + `offset` na faixa | com `uris` a fila tem um item só, e o primeiro "próxima" acaba em silêncio — medido |
| `album` | `context_uri` do álbum, sem `offset` | quem pede o disco quer do começo |
| `artista` | `context_uri` do artista, e nada mais | a doc é explícita: `offset` só vale para álbum e playlist |

Tocar artista por `context_uri` é também como o projeto sobrevive à remoção do
`Get Artist's Top Tracks` na revisão de fevereiro de 2026: o player escolhe o que
tocar, e escolhe melhor do que nós escolheríamos.

### Quando o modelo não preenche o tipo

`_tipo_falado()` recupera o tipo das palavras da própria frase ("playlist",
"disco", "banda") e as remove da busca. "toca minha playlist de treino" vira
`("treino", "playlist")`.

Este de/para é **fechado**: quatro palavras de tipo, do mesmo jeito que o
`TIPOS_FALADOS` dos aparelhos é fechado porque só existem seis tipos de aparelho.
A tabela que deliberadamente **não** existe é a de nomes de playlist — conjunto
aberto, muda toda semana, apodrece em silêncio.

O que isso conserta: "toca minha playlist de treino" procurava uma **faixa**
chamada "minha playlist de treino", achava alguma coisa e anunciava sucesso.
Errar calado é o pior modo de falhar num aparelho que só fala.

---

## Playlists

`tipo=playlist` procura nas **suas** playlists primeiro (`/me/playlists` — a
variante `/users/{id}` foi removida em fevereiro de 2026, junto com
`Get User's Profile`) e só então cai na busca pública.

Essa ordem é a funcionalidade inteira: existem milhares de playlists públicas
chamadas "Treino" e nenhuma delas é a sua.

E o resultado público precisa **parecer** com o que foi pedido para ser aceito: o
nome dito tem que estar no nome dela, ou todas as suas palavras de peso têm que
aparecer. Sem esse filtro, "toca a playlist que não existe 12345" volta uma
playlist chamada "123445" — medido com conta real —, porque a busca pública nunca
devolve vazio. Não achar e dizer que não achou é melhor que tocar a playlist de
um estranho.

Playlist começa embaralhada. A chamada de shuffle é solta e o erro dela é
engolido: embaralhar é preferência, tocar é o pedido.

`criar_playlist` cria uma playlist **privada** — a API cria pública quando
ninguém diz nada, e publicar no perfil de alguém por omissão não é um padrão que
se escolheria se alguém perguntasse. Ela pode já guardar a música que está
tocando (`adicionar_atual`), porque uma playlist criada por voz e deixada vazia é
uma gaveta que a pessoa vai ter que abrir no celular de qualquer jeito.

Ressalva registrada na D32: criar playlist por voz continua sendo interação ruim.
Entrou porque foi pedido depois dessa ressalva.

---

## O que não existe, e não pode existir

Rádio, estação, "toca algo parecido com isso": **impossível por esta via**. Em
27/11/2024 a Spotify desligou `recommendations`, `related-artists`,
`audio-features` e `audio-analysis` para apps novos — 403 no mesmo dia, sem fila
de espera, sem caminho de exceção. Este app é novo.

Fica escrito para que a ideia não volte à mesa daqui a seis meses como se fosse
trabalho pendente.

---

## Onde a música toca

`SPOTIFY_DEVICE` (padrão `Ideraldinho`) nomeia o aparelho a usar quando ninguém
diz onde. A ordem é: o aparelho nomeado na frase → esse preferido → o que já está
ativo → o primeiro da lista.

Ele aponta para a Pi de propósito: com o `raspotify` (um pacote do `librespot`)
se anunciando como "Ideraldinho", pedir uma música toca no alto-falante do próprio
assistente em vez de num PC em outro cômodo — que é a diferença entre **ser** a
caixa de som e ser um controle remoto de uma (D23). Enquanto a Pi não existe, o
nome não casa com nada e o fallback mantém o comportamento de antes.

`listar_aparelhos` e `trocar_aparelho` cobrem "onde dá para tocar" e "passa pro
echo dot".

Aparelho casa por nome exato, depois por trecho do nome, depois por **tipo
falado** — ninguém diz "toca no iPhone", diz "toca no celular", então `celular`,
`caixa de som` e `computador` mapeiam no `type` que a API reporta. Verificado
tocando em cada um: celular, Echo Dot e PC.

Um aparelho só aparece na lista depois que o app do Spotify dele está aberto e
ativo — mais um argumento para o raspotify na Pi, que se anuncia o dia inteiro.

---

## Verificado contra conta real

A primeira execução com conta de verdade achou três coisas que os testes com mock
não achariam:

- o Spotify responde 403 tanto para "sem Premium" quanto para "comando inválido
  agora", então a mensagem não culpa mais o Premium às cegas;
- tocar uma faixa nua deixa uma fila de um item só, daí o contexto do álbum;
- o modelo dizia ter pausado sem chamar a ferramenta — causado por um histórico
  que guardava só a frase falada, corrigido na D22.

```
voce> toca construcao do chico buarque  -> Tocando Construcao, de Chico Buarque.
voce> que musica e essa                 -> Construcao, de Chico Buarque.
voce> pula essa                         -> Proxima.
voce> pausa a musica                    -> Pausado.
```

A segunda, depois da D32, achou mais duas — e as duas eram a mesma doença de
sempre, o aparelho errando calado:

- **`limit=1` devolve um item diferente e pior** do que o primeiro de `limit=2`.
  "Toca Pink Floyd" tocava Guns N' Roses. A busca pede três e usa o primeiro;
  o teste que trava isso confere o parâmetro, porque sobre HTTP falso a lista
  devolvida é a que o teste escreveu.
- **a busca pública de playlist nunca diz "não achei"** — daí o filtro de
  semelhança descrito acima.

Verificado, com a faixa conferida a cada passo:

| pedido | o que tocou |
|---|---|
| artista "Pink Floyd" | Wish You Were Here |
| álbum "Abbey Road" | Come Together — a primeira faixa |
| a minha playlist "A NATA" | Ascensão Sonora |
| playlist inventada | recusou, e não trocou a música |
| criar playlist com a atual | criada, com a faixa que tocava |

**O que ainda não foi medido com conta real:** se o qwen3:8b preenche `tipo` em
**fala espontânea** — a bancada testa o código, não o modelo —, e se o shuffle
antes do `/play` sobrevive à ordem não garantida entre chamadas do player que a
própria documentação avisa existir.
