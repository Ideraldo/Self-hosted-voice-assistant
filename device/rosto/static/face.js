/*
  A página só escuta. Ela não manda nada para o dispositivo e não guarda estado
  próprio: o que ela sabe é o último JSON que chegou.

  O fio cai -- e aqui cai mais do que no gateway, porque basta reiniciar o
  `python -m device.main` para o servidor sumir por alguns segundos. Então vale
  a mesma regra do `ws_client.py`: reconectar sozinho, com espera crescente, em
  vez de precisar de F5 numa tela sem teclado.
*/

const ESPERA_INICIAL = 400;
const ESPERA_MAXIMA = 5000;

const rosto = document.getElementById("rosto");
const legenda = document.getElementById("legenda");
const debug = new URLSearchParams(location.search).has("debug");

let espera = ESPERA_INICIAL;

function aplicar(estado, emocao) {
  rosto.dataset.state = estado;
  rosto.dataset.emotion = emocao || "neutral";
  if (debug) {
    legenda.textContent = `${estado} · ${rosto.dataset.emotion}`;
    legenda.dataset.visivel = "1";
  }
}

function conectar() {
  const ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    espera = ESPERA_INICIAL;
    rosto.dataset.conexao = "online";
  };

  ws.onmessage = (evento) => {
    let dados;
    try {
      dados = JSON.parse(evento.data);
    } catch {
      return; // mensagem torta não apaga o rosto
    }
    aplicar(dados.state, dados.emotion);
  };

  ws.onclose = () => {
    // Apagar em vez de congelar: um rosto parado no "ouvindo" com o processo
    // morto é pior que um rosto escuro, porque parece que o aparelho está
    // gravando quando não está.
    rosto.dataset.conexao = "offline";
    setTimeout(conectar, espera);
    espera = Math.min(espera * 2, ESPERA_MAXIMA);
  };

  // Um erro sempre é seguido de um close; deixar o close cuidar da retentativa
  // evita agendar duas.
  ws.onerror = () => ws.close();
}

conectar();

/*
  Modo demonstração: `?demo` percorre os quatro estados e as cinco emoções sem
  o dispositivo rodando. Existe para desenhar o rosto sem falar com o
  microfone -- e, honestamente, para gravar a tela sem depender de um turno
  sair certo na primeira tomada.
*/
if (new URLSearchParams(location.search).has("demo")) {
  const estados = ["idle", "listening", "thinking", "speaking"];
  const emocoes = ["neutral", "happy", "curious", "confused", "sorry"];
  let i = 0;
  legenda.dataset.visivel = "1";
  setInterval(() => {
    const estado = estados[i % estados.length];
    const emocao = emocoes[Math.floor(i / estados.length) % emocoes.length];
    rosto.dataset.conexao = "online";
    aplicar(estado, emocao);
    legenda.textContent = `${estado} · ${emocao}`;
    i += 1;
  }, 1800);
}
