// 👉 Troque para a URL do seu backend no Render:
const API_BASE = "https://robo-dou-corm.onrender.com";

const el = (id) => document.getElementById(id);
const btnProcessar = el("btnProcessar");
const btnProcessarIA = el("btnProcessarIA"); // Novo botão
const btnCopiar = el("btnCopiar");
const preview = el("preview");

// Valor padrão: hoje
(function initDate() {
  const today = new Date().toISOString().slice(0, 10);
  el("data").value = today;
})();

// Função central de processamento
async function handleProcessing(endpoint) {
  const data = el("data").value.trim();
  const sections = el("sections").value.trim() || "DO1,DO2";
  const keywords = el("keywords").value.trim();

  if (!data) {
    preview.textContent = "Informe a data (YYYY-MM-DD).";
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

  // Desabilita todos os botões
  btnProcessar.disabled = true;
  btnProcessarIA.disabled = true;
  btnCopiar.disabled = true;
  preview.classList.add("loading");
  
  if (endpoint.includes("-ia")) {
    preview.textContent = "Processando com IA no INLABS. Isso pode levar até 2 minutos, aguarde…";
  } else {
    preview.textContent = "Processando (Rápido) no INLABS, aguarde…";
  }

  try {
    const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST", body: fd }); 
    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      preview.textContent = body?.detail
        ? `Erro: ${body.detail}`
        : `Erro HTTP ${res.status}`;
      return;
    }

    const texto = body?.whatsapp_text || "(Sem resultados)";
    preview.textContent = texto;
    btnCopiar.disabled = !texto || texto === "(Sem resultados)";
  } catch (err) {
    preview.textContent = `Falha na requisição: ${err.message || err}`;
  } finally {
    // Reabilita os botões
    btnProcessar.disabled = false;
    btnProcessarIA.disabled = false;
    preview.classList.remove("loading");
  }
}

// Listeners dos botões
btnProcessar.addEventListener("click", () => handleProcessing("/processar-inlabs"));
btnProcessarIA.addEventListener("click", () => handleProcessing("/processar-inlabs-ia")); // Novo listener

// Botão Copiar (sem alteração)
btnCopiar.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.textContent || "");
    btnCopiar.textContent = "Copiado!";
    setTimeout(() => (btnCopiar.textContent = "Copiar Relatório"), 1200);
  } catch (err) {
    alert("Falha ao copiar para a área de transferência.");
  }
});
