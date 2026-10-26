// Gráficos da página Consumo (os dados vêm do próprio HTML, em #dados-cons)
const dadosEl = document.getElementById('dados-cons');

if (dadosEl) {
  const dados = JSON.parse(dadosEl.textContent);
  const meses = dados.meses.map(m => m.nome);

  new Chart(document.getElementById('chart-consumo'), {
    type: 'bar',
    data: {
      labels: meses,
      datasets: [
        {label: 'Consumo (kWh)', data: dados.meses.map(m => m.kwh), backgroundColor: '#2b6be0'},
        {type: 'line', label: 'Média mensal', data: meses.map(() => dados.media),
         borderColor: '#f5a400', borderDash: [6, 4], pointRadius: 0},
      ],
    },
    options: {
      maintainAspectRatio: false,
      scales: {y: {beginAtZero: true, title: {display: true, text: 'Energia (kWh)'}}},
    },
  });

  new Chart(document.getElementById('chart-desvio'), {
    type: 'bar',
    data: {
      labels: meses,
      datasets: [{label: 'Desvio (%)', data: dados.meses.map(m => Math.round(m.desvio_pct * 10) / 10),
                  backgroundColor: '#7fa7e8'}],
    },
    options: {
      maintainAspectRatio: false,
      plugins: {legend: {display: false}},
      scales: {y: {title: {display: true, text: '% em relação à média'}}},
    },
  });
}
