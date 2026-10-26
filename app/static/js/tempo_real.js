// Página Tempo Real: pergunta ao servidor o estado das salas a cada 2 s e desenha um cartão por sala.
// A tela só mostra o que o ESP32 informou. Depois de um comando, os botões ficam travados
// até o próximo estado do ESP32 confirmar a mudança.
const listaSalas = document.getElementById('salas');
const msgVazio = document.getElementById('vazio');
const msgErro = document.getElementById('aviso');
const statusMqtt = document.getElementById('mqtt-status');
let ultimoJson = '';

function criar(tag, classe, texto) {
  const e = document.createElement(tag);
  if (classe) e.className = classe;
  if (texto !== undefined) e.textContent = texto;
  return e;
}

function caixaEquipamento(nome, ligado, ehAr) {
  const caixa = criar('div', 'eq' + (ligado ? ' on' : '') + (ehAr ? ' ar' : ''));
  caixa.append(criar('span', 'dot'), criar('span', '', nome + ': ' + (ligado ? 'LIGADO' : 'desligado')));
  return caixa;
}

function botao(texto, classe, desativado, sala, equipamento, acao) {
  const b = criar('button', 'btn btn-sm ' + classe, texto);
  b.disabled = desativado;
  b.addEventListener('click', () => enviarComando(sala, equipamento, acao, b));
  return b;
}

async function enviarComando(sala, equipamento, acao, b) {
  b.disabled = true;
  const url = `/api/salas/${encodeURIComponent(sala.predio)}/${encodeURIComponent(sala.sala)}/comando`;
  const resposta = await apiPost(url, {equipamento, acao});

  if (!resposta.ok) {
    msgErro.textContent = resposta.data.erro || 'Não foi possível enviar o comando.';
    msgErro.classList.remove('d-none');
    setTimeout(() => msgErro.classList.add('d-none'), 6000);
  }
  atualizar();
}

function cartaoSala(sala) {
  const offline = !sala.online;
  const esperando = sala.comandos_pendentes.length > 0;
  const travar = offline || esperando;

  let situacao = 'ok';
  if (offline) situacao = 'offline';
  else if (sala.acao_necessaria) situacao = 'alert';

  const coluna = criar('div', 'col-4');
  const cartao = criar('div', 'se-card room ' + situacao);

  // título e situação
  let textoBadge = 'Online';
  if (sala.aguardando_primeira_mensagem) textoBadge = 'Aguardando';
  else if (offline) textoBadge = 'Offline';

  const titulo = criar('div', 'd-flex justify-content-between align-items-center mb-2');
  titulo.append(
    criar('h2', 'mb-0', `Bloco ${sala.predio} · Sala ${sala.sala}`),
    criar('span', 'badge-se ' + (offline ? 'b-sim' : 'b-normal'), textoBadge)
  );
  cartao.append(titulo);

  // avisos de desperdício
  if (sala.acao_necessaria) {
    cartao.append(criar('div', 'alert alert-danger py-2 small',
      `Possível desperdício na sala ${sala.sala}: desligue os equipamentos.`));
  } else if (sala.ocorrencia_aberta || sala.alerta) {
    cartao.append(criar('div', 'alert alert-warning py-2 small',
      'Alerta registrado, equipamentos já desligados.'));
  }

  if (sala.descricao) cartao.append(criar('div', 'small text-muted mb-2', sala.descricao));

  // estado dos equipamentos
  const estado = criar('div', 'd-grid gap-2 mb-3');
  estado.append(
    caixaEquipamento('Iluminação', sala.iluminacao, false),
    caixaEquipamento('Ar-condicionado', sala.ar_condicionado, true),
    criar('div', 'small text-muted', sala.movimento ? '● Movimento detectado' : '○ Sem movimento')
  );
  cartao.append(estado);

  // botões
  const botoes = criar('div', 'd-flex flex-wrap gap-2');
  botoes.append(
    botao(sala.iluminacao ? 'Desligar luz' : 'Ligar luz', 'btn-outline-primary', travar,
          sala, 'iluminacao', sala.iluminacao ? 'desligar' : 'ligar'),
    botao(sala.ar_condicionado ? 'Desligar ar' : 'Ligar ar', 'btn-outline-primary', travar,
          sala, 'ar_condicionado', sala.ar_condicionado ? 'desligar' : 'ligar'),
    botao('Desligar tudo', 'btn-danger', travar || (!sala.iluminacao && !sala.ar_condicionado),
          sala, 'todos', 'desligar')
  );
  cartao.append(botoes);

  if (esperando) cartao.append(criar('div', 'small text-primary mt-2', 'Aguardando confirmação do ESP32...'));

  const rodape = sala.aguardando_primeira_mensagem
    ? 'Aguardando a primeira mensagem do ESP32...'
    : 'Última mensagem há ' + sala.segundos_desde_ultimo + ' s';
  cartao.append(criar('div', 'small text-muted mt-2', rodape));

  coluna.append(cartao);
  return coluna;
}

function mostrarStatusMqtt(mqtt) {
  if (mqtt.desativado) {
    statusMqtt.textContent = 'MQTT desativado';
  } else {
    statusMqtt.textContent = mqtt.conectado ? '● MQTT conectado' : '○ MQTT desconectado';
  }
  statusMqtt.className = 'small ' + (mqtt.conectado ? 'text-success' : 'text-danger');
}

async function atualizar() {
  try {
    const respSalas = await fetch('/api/salas', {credentials: 'same-origin'});
    if (respSalas.status === 401) {  // sessão expirou
      location.href = '/login';
      return;
    }
    const respStatus = await fetch('/api/status', {credentials: 'same-origin'});
    const salas = (await respSalas.json()).salas;
    mostrarStatusMqtt((await respStatus.json()).mqtt);

    const online = salas.filter(s => s.online).length;
    const abertas = salas.filter(s => s.ocorrencia_aberta).length;
    document.getElementById('kpi-online').textContent = online;
    document.getElementById('kpi-total').textContent = 'de ' + salas.length + ' cadastrada(s)';
    document.getElementById('kpi-pend').textContent = abertas;

    // só redesenha se algo mudou, para não piscar a tela
    const json = JSON.stringify(salas);
    if (json === ultimoJson) return;
    ultimoJson = json;

    listaSalas.replaceChildren(...salas.map(cartaoSala));
    msgVazio.classList.toggle('d-none', salas.length > 0);
  } catch (erro) {
    statusMqtt.textContent = 'Servidor sem resposta';
    statusMqtt.className = 'small text-danger';
  }
}

atualizar();
setInterval(atualizar, 2000);
