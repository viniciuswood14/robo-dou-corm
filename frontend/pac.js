document.addEventListener("DOMContentLoaded", function() {

  // 👉 Aponte para a URL do seu backend no Render:
  const API_BASE = "https://robo-dou-corm-backend.onrender.com";

  const el = (id) => document.getElementById(id);
  const btnConsultar = el("btnConsultarPAC");
  const inputAno = el("ano");
  const tableHeader = el("table-header");
  const tableBody = el("table-body");
  const tableContainer = el("table-container");
  const loadingText = el("loading-text");
  const errorText = el("error-text");

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

  // --- Função Principal ---
  async function fetchAndRenderTable() {
    const ano = inputAno.value;
    if (!ano) {
      errorText.textContent = "Por favor, insira um ano.";
      errorText.style.display = "block";
      tableContainer.style.display = "none";
      return;
    }

    btnConsultar.disabled = true;
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
        // Adiciona classe para alinhar números
        if (headerText !== 'PROGRAMA' && headerText !== 'AÇÃO') {
            th.classList.add("num");
        }
        tableHeader.appendChild(th);
      });

      // 3. Preenche as linhas da tabela
      data.forEach(rowData => {
        const tr = document.createElement("tr");

        // Aplica estilos de linha
        if (rowData.PROGRAMA === 'Total Geral') {
            tr.classList.add("row-total");
        } else if (rowData.AÇÃO === null) {
            tr.classList.add("row-programa");
        } else {
            tr.classList.add("row-acao");
        }

        // Cria as células
        headers.forEach(header => {
            const td = document.createElement("td");
            let value = rowData[header];

            // Formata os valores
            if (header === 'PROGRAMA' || header === 'AÇÃO') {
                td.textContent = value || ""; // Deixa em branco se for nulo
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

      // Exibe a tabela
      tableContainer.style.display = "block";

    } catch (err) {
      errorText.textContent = `Erro ao consultar: ${err.message}`;
      errorText.style.display = "block";
    } finally {
      btnConsultar.disabled = false;
      loadingText.style.display = "none";
    }
  }

  btnConsultar.addEventListener("click", fetchAndRenderTable);
  
  // Opcional: Consultar ao carregar a página
  // fetchAndRenderTable(); 
});
