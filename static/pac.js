document.addEventListener("DOMContentLoaded", function() {

  // 👉 Aponte para a URL do seu backend no Render:
  const API_BASE = "";

  const el = (id) => document.getElementById(id);
  const btnConsultar = el("btnConsultarPAC");
  const inputAno = el("ano");
  const tableHeader = el("table-header");
  const tableBody = el("table-body");
  const tableContainer = el("table-container");
  const loadingText = el("loading-text");
  const errorText = el("error-text");
  const chartCanvas = el("pacChart");

  let pacChart = null; // Variável global para o gráfico

  // Define o ano padrão para o ano atual
  (function initYear() {
    if(inputAno) {
        inputAno.value = new Date().getFullYear();
    }
  })();

  // --- Helpers de Formatação ---
  function formatCurrency(value) {
    if (typeof value !== 'number') return "R$ 0,00";
    return "R$ " + value.toLocaleString('pt-BR', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function formatPercent(value) {
    if (typeof value !== 'number') return "0,0%";
    return (value * 100).toLocaleString('pt-BR', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1
    }) + "%";
  }

  // --- Função da Tabela (chamada por Ano) ---
  async function fetchAndRenderTable(ano) {
    if (!ano) {
      errorText.textContent = "Ano inválido selecionado.";
      errorText.style.display = "block";
      tableContainer.style.display = "none";
      return;
    }
    
    // Atualiza o input para refletir o ano clicado
    inputAno.value = ano;

    btnConsultar.disabled = true;
    loadingText.textContent = `Consultando SIOP para ${ano}, aguarde...`;
    loadingText.style.display = "block";
    errorText.style.display = "none";
    tableContainer.style.display = "none";

    try {
      const response = await fetch(`${API_BASE}/api/pac-data/${ano}`);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || `Erro HTTP ${response.status}`);
      }

      // 1. Limpa a tabela antiga
      tableHeader.innerHTML = "";
      tableBody.innerHTML = "";

      // 2. Define os cabeçalhos (baseado nas chaves do JSON)
      const headers = [
        'PROGRAMA', 'AÇÃO', 'LOA', 'DOTAÇÃO ATUAL', 
        'EMPENHADO (c)', 'LIQUIDADO', 'PAGO', '% EMP/DOT'
      ];
      
      headers.forEach(headerText => {
        const th = document.createElement("th");
        th.textContent = headerText;
        if (headerText !== 'PROGRAMA' && headerText !== 'AÇÃO') {
            th.classList.add("num");
        }
        tableHeader.appendChild(th);
      });

      // 3. Preenche as linhas da tabela
      data.forEach(rowData => {
        const tr = document.createElement("tr");

        if (rowData.PROGRAMA === 'Total Geral') tr.classList.add("row-total");
        else if (rowData.AÇÃO === null) tr.classList.add("row-programa");
        else tr.classList.add("row-acao");

        headers.forEach(header => {
            const td = document.createElement("td");
            let value = rowData[header];

            if (header === 'PROGRAMA' || header === 'AÇÃO') {
                td.textContent = value || "";
            } else if (header === '% EMP/DOT') {
                td.textContent = formatPercent(value);
                td.classList.add("num");
            } else {
                td.textContent = formatCurrency(value);
                td.classList.add("num");
            }
            tr.appendChild(td);
        });

        tableBody.appendChild(tr);
      });

      tableContainer.style.display = "block"; // Exibe a tabela

    } catch (err) {
      errorText.textContent = `Erro ao consultar ${ano}: ${err.message}`;
      errorText.style.display = "block";
    } finally {
      btnConsultar.disabled = false;
      loadingText.style.display = "none";
    }
  }

  // --- Função do Gráfico ---
  async function fetchAndRenderChart() {
    loadingText.textContent = "Carregando gráfico histórico (2010-2025)...";
    loadingText.style.display = "block";
    errorText.style.display = "none";
    tableContainer.style.display = "none";
    
    try {
      const response = await fetch(`${API_BASE}/api/pac-data/historical-dotacao`);
      const chartData = await response.json(); // Pega o JSON {labels: [], datasets: []}

      if (!response.ok) {
        throw new Error(chartData.detail || `Erro HTTP ${response.status}`);
      }

      // Prepara cores para os 5 datasets
      const colors = [
          'rgba(0, 44, 95, 0.8)',  // Azul Marinho
          'rgba(0, 95, 86, 0.8)',  // Verde Marinho
          'rgba(255, 184, 28, 0.8)', // Amarelo Ouro
          'rgba(60, 120, 216, 0.8)', // Azul Claro
          'rgba(217, 83, 79, 0.8)'   // Vermelho (para destaque, se necessário)
      ];
      
      chartData.datasets.forEach((dataset, index) => {
          dataset.backgroundColor = colors[index % colors.length];
      });

      const ctx = chartCanvas.getContext('2d');
      pacChart = new Chart(ctx, {
        type: 'bar',
        data: chartData,
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            title: {
                display: true,
                text: 'Dotação Autorizada (LOA + Créditos) por Ação e Ano',
                font: { size: 16, weight: '600' },
                color: '#002c5f'
            },
            legend: {
                position: 'top',
            },
            tooltip: {
                callbacks: {
                    label: function(context) {
                        let label = context.dataset.label || '';
                        if (label) {
                            label += ': ';
                        }
                        if (context.parsed.y !== null) {
                            label += formatCurrency(context.parsed.y);
                        }
                        return label;
                    }
                }
            }
          },
          scales: {
            x: {
              stacked: true, // Empilha as ações dentro do ano
              title: { display: true, text: 'Exercício (Ano)' }
            },
            y: {
              stacked: true, // Empilha as ações dentro do ano
              title: { display: true, text: 'Dotação (R$)' },
              ticks: {
                callback: function(value) { return formatCurrency(value); }
              }
            }
          },
          // [AÇÃO PRINCIPAL] Lidar com o clique no gráfico
          onClick: (e) => {
            const activePoints = pacChart.getElementsAtEventForMode(e, 'index', { intersect: true }, true);
            if (activePoints.length > 0) {
                const clickedIndex = activePoints[0].index;
                const clickedYear = chartData.labels[clickedIndex];
                
                // Chama a função que busca a tabela detalhada!
                fetchAndRenderTable(clickedYear);
            }
          }
        }
      });
      
      // Carrega a tabela do ano mais recente por padrão
      const ultimoAno = chartData.labels[chartData.labels.length - 1];
      fetchAndRenderTable(ultimoAno);

    } catch (err) {
        // Se falhar (ex: arquivo ainda não existe), mostra erro amigável
        if (err.message.includes("404")) {
             errorText.textContent = "O robô ainda está compilando os dados históricos. Por favor, aguarde a execução das 05h35 ou consulte a tabela manualmente abaixo.";
        } else {
             errorText.textContent = `Erro ao carregar o gráfico: ${err.message}`;
        }
        errorText.style.display = "block";
        loadingText.style.display = "none";
        
        // Se o gráfico falhar, tenta carregar pelo menos a tabela do ano atual
        const currentYear = new Date().getFullYear();
        fetchAndRenderTable(currentYear);
    }
  }

  // --- Listeners ---
  
  // O botão agora consulta o ano que está no input
  btnConsultar.addEventListener("click", () => fetchAndRenderTable(inputAno.value));
  
  // Carrega o gráfico (que então carregará a tabela) ao iniciar
  fetchAndRenderChart();
});
