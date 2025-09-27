(function checkPassword() {
  const savedPassword = sessionStorage.getItem('dou_robot_auth');
  const correctPassword = "marinha"; // <-- TROQUE AQUI

  if (savedPassword === correctPassword) {
    return; // Já autenticado nesta sessão
  }

  const inputPassword = prompt("Por favor, insira a senha de acesso:");
  if (inputPassword === correctPassword) {
    sessionStorage.setItem('dou_robot_auth', inputPassword);
  } else {
    document.body.innerHTML = "<h1>Acesso Negado</h1>";
    throw new Error("Senha incorreta.");
  }
})();

// O resto do seu código app.js vem depois...

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

  if (!data) {
    preview.textContent = "Informe a data (YYYY-MM-DD).";
    return;
  }

  const fd = new FormData();
  fd.append("data", data);
  fd.append("sections", sections);

  btnProcessar.disabled = true;
  btnCopiar.disabled = true;
  preview.classList.add("loading");
  preview.textContent = "Processando no INLABS, aguarde…";

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
    preview.classList.remove("loading");
  }
});

btnCopiar.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.textContent || "");
    btnCopiar.textContent = "Copiado!";
    setTimeout(() => (btnCopiar.textContent = "Copiar Relatório"), 1200);
  } catch (err) {
    alert("Falha ao copiar para a área de transferência.");
  }
});
