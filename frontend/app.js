// 👉 Troque para a URL do seu backend no Render:
const API_BASE = "https://robo-dou-corm.onrender.com";

const el = (id) => document.getElementById(id);
const btnProcessar = el("btnProcessar");
const btnCopiar = el("btnCopiar");
const preview = el("preview");

// valor padrão: hoje
(function initDate() {
  const today = new Date().toISOString().slice(0, 10);
  el("data").value = today;
})();

btnProcessar.addEventListener("click", async () => {
  const data = el("data").value.trim();
  const sections = el("sections").value.trim() || "DO1";
  const keywordsRaw = el("keywords").value.trim();

  if (!data) {
    preview.textContent = "Informe a data (YYYY-MM-DD).";
    return;
  }

  const fd = new FormData();
  fd.append("data", data);
  fd.append("sections", sections);
  if (keywordsRaw) {
    // valida JSON minimamente
    try {
      JSON.parse(keywordsRaw);
      fd.append("keywords_json", keywordsRaw);
    } catch {
      preview.textContent = "keywords_json inválido. Exemplo: [\"Marinha\",\"Fundo Naval\"]";
      return;
    }
  }

  btnProcessar.disabled = true;
  btnCopiar.disabled = true;
  preview.textContent = "Coletando no INLABS e processando…";

  try {
    const res = await fetch(`${API_BASE}/processar-inlabs`, { method: "POST", body: fd });
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
  }
});

btnCopiar.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.textContent || "");
    btnCopiar.textContent = "Copiado!";
    setTimeout(() => (btnCopiar.textContent = "Copiar texto"), 1200);
  } catch {
    // fallback
    const ta = document.createElement("textarea");
    ta.value = preview.textContent || "";
    document.body.appendChild(ta);
    ta.select(); document.execCommand("copy");
    ta.remove();
  }
});
