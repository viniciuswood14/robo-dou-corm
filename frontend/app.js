document.addEventListener("DOMContentLoaded", function() {

  // 👉 Troque para a URL do seu backend no Render:
  const API_BASE = "https://robo-dou-corm-backend.onrender.com";

  const el = (id) => document.getElementById(id);
  const btnProcessar = el("btnProcessar");
  const btnProcessarIA = el("btnProcessarIA");
  const btnCopiar = el("btnCopiar");
  const preview = el("preview");

  // Valor padrão: hoje
  (function initDate() {
    const today = new Date().toISOString().slice(0, 10);
    if (el("data")) {
      el("data").value = today;
    }
  })();

  // Função central de processamento
  async function handleProcessing(endpoint) {
    const data = el("data").value.trim();
    const sections = el("sections").value.trim() || "DO1,DO2";
    const keywords = el("keywords").value.trim();

    if (!data) {
      if(preview) preview.textContent = "Informe a data (YYYY-MM-DD).";
      return;
    }

    const fd = new FormData();
    fd.append("data", data);
    fd.append("sections", sections);
    
    if (keywords) {
      const keywordsList = keywords.split(',')
        .map(k => k.trim())
        .filter(k => k.length > 0);
        
      if (keywordsList.length > 0) {
        fd.append("keywords_json", JSON.stringify(keywordsList));
      }
    }

    // --- CORREÇÃO v13.5 ---
    // Adiciona verificação antes de desabilitar
    if (btnProcessar) btnProcessar.disabled = true;
    if (btnProcessarIA) btnProcessarIA.disabled = true;
    if (btnCopiar) btnCopiar.disabled = true;
    // ---------------------

    if (preview) {
      preview.classList.add("loading");
      if (endpoint.includes("-ia")) {
        preview.textContent = "Processando com IA no INLABS. Isso pode levar até 2 minutos, aguarde…";
      } else {
        preview.textContent = "Processando (Rápido) no INLABS, aguarde…";
      }
    }

    try {
      const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: fd }); 
      const body = await res.json().catch(() => ({}));

      if (!res.ok) {
        if(preview) preview.textContent = body?.detail
          ? `Erro: ${body.detail}`
          : `Erro HTTP ${res.status}`;
        return;
      }

      const texto = body?.whatsapp_text || "(Sem resultados)";
      if (preview) preview.textContent = texto;

      // --- CORREÇÃO v13.5 ---
      // Adiciona verificação antes de habilitar/desabilitar
      if (btnCopiar) {
        btnCopiar.disabled = !texto || texto === "(Sem resultados)";
      }
      // ---------------------

    } catch (err) {
      if (preview) preview.textContent = `Falha na requisição: ${err.message || err}`;
    } finally {
      
      // --- CORREÇÃO v13.5 ---
      // Adiciona verificação antes de re-habilitar
      if (btnProcessar) btnProcessar.disabled = false;
      if (btnProcessarIA) btnProcessarIA.disabled = false;
      if (preview) preview.classList.remove("loading");
      // ---------------------
    }
  }

  // Listeners dos botões
  if (btnProcessar) {
    btnProcessar.addEventListener("click", () => handleProcessing("/processar-inlabs"));
  }
  if (btnProcessarIA) {
    btnProcessarIA.addEventListener("click", () => handleProcessing("/processar-inlabs-ia"));
  }

  // Botão Copiar
  if (btnCopiar) {
    btnCopiar.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(preview.textContent || "");
        btnCopiar.textContent = "Copiado!";
        setTimeout(() => (btnCopiar.textContent = "Copiar Relatório"), 1200);
      } catch (err) {
        alert("Falha ao copiar para a área de transferência.");
      }
    });
  }

});
