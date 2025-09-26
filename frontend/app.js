const API_BASE = "https://robo-dou-corm.onrender.com"; // altere para sua URL Render

async function processarXML() {
  const data = new Date().toISOString().slice(0,10);
  const xmlUrl = document.querySelector('#xmlUrl').value.trim();
  const body = new FormData();
  body.append('data', data);
  body.append('xml_url', xmlUrl);
  body.append('secao', '1');
  body.append('keywords_json', JSON.stringify(["PRONAPA","PNM","PCFT","Fundo Naval","Comando da Marinha"]));

  const preview = document.querySelector('#preview');
  preview.textContent = "Processando...";

  try {
    const res = await fetch(API_BASE + "/processar-xml", {method:"POST", body});
    const json = await res.json();
    preview.textContent = json.whatsapp_text;
  } catch(e) {
    preview.textContent = "Erro: " + e.message;
  }
}
