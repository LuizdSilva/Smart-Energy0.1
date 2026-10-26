// Aviso de desperdício: aparece no canto da tela em QUALQUER página do sistema.
// A cada 3 s pergunta ao servidor se alguma sala teve ocorrência recente e mostra um aviso
// para cada ocorrência nova (o id fica guardado para o aviso não repetir ao trocar de página).
if (location.pathname !== '/login') {
  const caixaAvisos = document.createElement('div');
  caixaAvisos.style.cssText = 'position: fixed; top: 16px; right: 16px; z-index: 2000; width: 380px';
  document.body.appendChild(caixaAvisos);

  // ids das ocorrências que já foram mostradas
  let jaMostradas = [];
  try {
    jaMostradas = JSON.parse(sessionStorage.getItem('avisos_mostrados') || '[]');
  } catch (erro) {
    jaMostradas = [];
  }

  function lembrar(id) {
    jaMostradas.push(id);
    try {
      sessionStorage.setItem('avisos_mostrados', JSON.stringify(jaMostradas.slice(-200)));
    } catch (erro) {
      // sem sessionStorage: o aviso só pode repetir depois de recarregar a página
    }
  }

  function mostrarAviso(sala) {
    const aviso = document.createElement('div');
    aviso.className = 'alert alert-' + (sala.acao_necessaria ? 'danger' : 'warning') + ' alert-dismissible shadow';

    const titulo = document.createElement('b');
    titulo.textContent = `Possível desperdício: bloco ${sala.predio}, sala ${sala.sala}`;

    const texto = document.createElement('div');
    texto.className = 'small';
    texto.textContent = sala.acao_necessaria
      ? 'Ainda há equipamentos ligados. É preciso desligar.'
      : 'A sala ficou vazia com equipamentos ligados, que já foram desligados.';

    const fechar = document.createElement('button');
    fechar.type = 'button';
    fechar.className = 'btn-close';
    fechar.addEventListener('click', () => aviso.remove());

    aviso.append(titulo, texto, fechar);
    caixaAvisos.appendChild(aviso);

    // se não precisa de ação, o aviso some sozinho depois de 20 s
    if (!sala.acao_necessaria) setTimeout(() => aviso.remove(), 20000);
  }

  async function verificarSalas() {
    try {
      const resposta = await fetch('/api/salas', {credentials: 'same-origin'});
      if (!resposta.ok) return;
      const salas = (await resposta.json()).salas;
      for (const sala of salas) {
        const ocorrencia = sala.ocorrencia_recente;
        if (ocorrencia && !jaMostradas.includes(ocorrencia.id)) {
          lembrar(ocorrencia.id);
          mostrarAviso(sala);
        }
      }
    } catch (erro) {
      // servidor fora do ar: tenta de novo na próxima volta
    }
  }

  verificarSalas();
  setInterval(verificarSalas, 3000);
}
