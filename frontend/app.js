// 👉 Troque para a URL do seu backend no Render:
const API_BASE = "https://robo-dou-corm.onrender.com";

const el = (id) => document.getElementById(id);
const btnProcessar = el("btnProcessar");
const btnCopiar = el("btnCopiar");
const preview = el("preview");

// Valor padrão: hoje
(function initDate() {
  const today = new Date().toISOString().slice(0, 10);
  el("data").value = today;
})();

btnProcessar.addEventListener("click", async () => {
  const data = el("data").value.trim();
  const sections = el("sections").value.trim() || "DO1";
  const selectedSource = document.querySelector('input[name="source"]:checked').value;

  if (!data) {
    preview.textContent = "Informe a data (YYYY-MM-DD).";
    return;
  }

  const fd = new FormData();
  fd.append("data", data);
  fd.append("sections", sections);
  fd.append("source", selectedSource); // Envia a fonte selecionada

  btnProcessar.disabled = true;
  btnCopiar.disabled = true;
  preview.classList.add("loading");
  preview.textContent = `Processando via ${selectedSource}, aguarde…`;

  try {
    const res = await fetch(`${API_BASE}/processar`, { method: "POST", body: fd }); // Nova rota /processar
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
    btnProcessar.disabled = false;
    preview.classList.remove("loading");
  }
});

// ... (função de copiar inalterada) ...

btnCopiar.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.textContent || "");
    btnCopiar.textContent = "Copiado!";
    setTimeout(() => (btnCopiar.textContent = "Copiar Relatório"), 1200);
  } catch (err) {
    alert("Falha ao copiar para a área de transferência.");
  }
});
