// frontend/app.js

document.addEventListener('DOMContentLoaded', () => {
    // Definir data de hoje no input
    const today = new Date().toISOString().split('T')[0];
    const dateInput = document.getElementById('dateInput');
    if (dateInput) dateInput.value = today;

    // Elementos
    const btnProcessar = document.getElementById('btnProcessar');
    const btnIA = document.getElementById('btnIA');
    const btnFallback = document.getElementById('btnFallback');
    const btnGuruPopup = document.getElementById('btnGuruPopup'); // Botão do Guru
    
    const loadingDiv = document.getElementById('loading');
    const resultsArea = document.getElementById('results-area');

    // Funções de UI
    const showLoading = () => {
        if(loadingDiv) loadingDiv.classList.remove('hidden');
        if(resultsArea) resultsArea.innerHTML = '';
    };

    const hideLoading = () => {
        if(loadingDiv) loadingDiv.classList.add('hidden');
    };

    const renderResults = (data) => {
        if (!resultsArea) return;
        resultsArea.innerHTML = '';

        if (!data.publications || data.publications.length === 0) {
            resultsArea.innerHTML = '<div class="result-card"><p>Nenhuma publicação encontrada.</p></div>';
            return;
        }

        // Exibir texto para WhatsApp (Botão Copiar)
        const zapCard = document.createElement('div');
        zapCard.className = 'result-card';
        zapCard.style.borderLeftColor = '#25D366'; // Cor WhatsApp
        zapCard.innerHTML = `
            <div class="card-header">
                <span>Resumo para Mensageria</span>
                <span>${data.date}</span>
            </div>
            <div class="card-body">
                <h3>Relatório Pronto</h3>
                <textarea style="width:100%; height:150px; margin-bottom:10px;">${data.whatsapp_text}</textarea>
                <button onclick="navigator.clipboard.writeText(this.previousElementSibling.value); alert('Copiado!')" style="background:#25D366; width:auto; display:inline-flex;">
                    <i class="fas fa-copy"></i> Copiar Texto
                </button>
            </div>
        `;
        resultsArea.appendChild(zapCard);

        // Renderizar Cards Individuais
        data.publications.forEach(pub => {
            const div = document.createElement('div');
            
            // Define classe baseada na análise
            let cardClass = 'result-card';
            if (pub.relevance_reason && pub.relevance_reason.toLowerCase().includes('atenção')) {
                cardClass += ' atencao';
            } else {
                cardClass += ' relevante';
            }

            div.className = cardClass;
            div.innerHTML = `
                <div class="card-header">
                    <span class="organ">${pub.organ || 'Órgão Desconhecido'}</span>
                    <span class="section">${pub.section || 'DOU'}</span>
                </div>
                <div class="card-body">
                    <h3>${pub.type || 'Ato'}</h3>
                    <div class="summary">${pub.summary || 'Sem resumo disponível.'}</div>
                    
                    <div class="ia-analysis">
                        <strong>⚓ Análise:</strong> ${pub.relevance_reason || 'Aguardando análise...'}
                    </div>
                    
                    <div style="margin-top:10px;">
                        <a href="https://www.in.gov.br/web/dou/-/${encodeURIComponent(pub.type)}-${new Date().getTime()}" target="_blank" style="color:#0077b6; text-decoration:none; font-size:0.9rem;">
                            <i class="fas fa-external-link-alt"></i> Ver no DOU (Busca)
                        </a>
                    </div>
                </div>
            `;
            resultsArea.appendChild(div);
        });
    };

    // Função Genérica de Fetch
    const triggerProcess = async (endpoint) => {
        showLoading();
        
        const formData = new FormData();
        formData.append('data', dateInput.value);
        
        // Seções
        const sections = [];
        if(document.getElementById('sec1').checked) sections.push('DO1');
        if(document.getElementById('sec2').checked) sections.push('DO2');
        if(document.getElementById('sec3').checked) sections.push('DO3');
        formData.append('sections', sections.join(','));

        // Keywords
        const kwInput = document.getElementById('keywordsInput');
        if(kwInput && kwInput.value) {
            const kwList = kwInput.value.split(',').map(s => s.trim()).filter(s => s);
            formData.append('keywords_json', JSON.stringify(kwList));
        }

        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) throw new Error(`Erro HTTP: ${response.status}`);
            
            const data = await response.json();
            renderResults(data);
            
        } catch (error) {
            resultsArea.innerHTML = `<div class="result-card atencao"><p>Erro ao processar: ${error.message}</p></div>`;
            console.error(error);
        } finally {
            hideLoading();
        }
    };

    // Listeners dos Botões Principais
    if(btnProcessar) btnProcessar.addEventListener('click', () => triggerProcess('/processar-inlabs'));
    if(btnIA) btnIA.addEventListener('click', () => triggerProcess('/processar-dou-ia'));
    if(btnFallback) btnFallback.addEventListener('click', () => triggerProcess('/teste-fallback'));

    // --- NOVO: Listener do Botão GURU (Pop-up) ---
    if(btnGuruPopup) {
        btnGuruPopup.addEventListener('click', (e) => {
            e.preventDefault();
            const url = "https://chatgpt.com/g/g-694097bd22ac819192af5884a4e7f223-analise-do-dou";
            // Abre janela flutuante de 500px de largura
            window.open(url, 'GuruOrcamentario', 'width=500,height=750,scrollbars=yes,resizable=yes');
        });
    }
});
