// --- DADOS ESTÁTICOS DO PAC (EXTRAÍDOS DA PLANILHA WOOD) ---
// Período: 2015 a 2026
const PAC_DB = {
  "2026": [
    { "Mes": "JAN/2026", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 },
    { "Mes": "JAN/2026", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 },
    { "Mes": "JAN/2026", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 },
    { "Mes": "JAN/2026", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 },
    { "Mes": "JAN/2026", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 33667477.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 33667477.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2025": [
    { "Mes": "DEZ/2025", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 250000.0, "Provisao": 250000.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 250000.0, "Pago": 0.0 },
    { "Mes": "DEZ/2025", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 332675762.0, "Provisao": 273641399.0, "Destaque": 0.0, "Disponivel": 4122.37, "Empenhado": 332671639.63, "Pago": 113333333.34 },
    { "Mes": "DEZ/2025", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 9324707.0, "Provisao": 8802927.0, "Destaque": 0.0, "Disponivel": 2185.0, "Empenhado": 9322522.0, "Pago": 0.0 },
    { "Mes": "DEZ/2025", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 161822453.0, "Provisao": 149309536.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 161822453.0, "Pago": 12852233.0 },
    { "Mes": "DEZ/2025", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 38118023.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 38118023.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2024": [
    { "Mes": "DEZ/2024", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 38553255.0, "Provisao": 38553255.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 38553255.0, "Pago": 24200787.97 },
    { "Mes": "DEZ/2024", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 401679090.0, "Provisao": 387034503.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 401679090.0, "Pago": 393112679.52 },
    { "Mes": "DEZ/2024", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 14163907.0, "Provisao": 13955214.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 14163907.0, "Pago": 12450558.8 },
    { "Mes": "DEZ/2024", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 254823439.0, "Provisao": 252627055.0, "Destaque": 0.0, "Disponivel": 4543.14, "Empenhado": 254818895.86, "Pago": 247067824.28 },
    { "Mes": "DEZ/2024", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 46560000.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 46560000.0, "Pago": 0.0 }
  ],
  "2023": [
    { "Mes": "DEZ/2023", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 10839846.0, "Provisao": 10839846.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 10839846.0, "Pago": 10328906.59 },
    { "Mes": "DEZ/2023", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 487232231.0, "Provisao": 487232231.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 487232231.0, "Pago": 444391624.4 },
    { "Mes": "DEZ/2023", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 4181966.0, "Provisao": 4181966.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 4181966.0, "Pago": 4165509.77 },
    { "Mes": "DEZ/2023", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 322699318.0, "Provisao": 315995470.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 322699318.0, "Pago": 318534080.31 },
    { "Mes": "DEZ/2023", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 481155.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 481155.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2022": [
    { "Mes": "DEZ/2022", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 17871897.0, "Provisao": 17871897.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 17871897.0, "Pago": 16422206.52 },
    { "Mes": "DEZ/2022", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 395996238.0, "Provisao": 395996238.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 395996238.0, "Pago": 372439369.83 },
    { "Mes": "DEZ/2022", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 10077840.0, "Provisao": 10077840.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 10077840.0, "Pago": 10077840.0 },
    { "Mes": "DEZ/2022", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 259640822.0, "Provisao": 252996962.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 259640822.0, "Pago": 255297121.73 },
    { "Mes": "DEZ/2022", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2021": [
    { "Mes": "DEZ/2021", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 13998965.0, "Provisao": 13998965.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 13998965.0, "Pago": 12850977.29 },
    { "Mes": "DEZ/2021", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 255530722.0, "Provisao": 255530722.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 255530722.0, "Pago": 242044813.06 },
    { "Mes": "DEZ/2021", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 5267156.0, "Provisao": 5267156.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 5267156.0, "Pago": 5267156.0 },
    { "Mes": "DEZ/2021", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 218731175.0, "Provisao": 213791175.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 218731175.0, "Pago": 204068565.48 },
    { "Mes": "DEZ/2021", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2020": [
    { "Mes": "DEZ/2020", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 20297079.0, "Provisao": 20297079.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 20297079.0, "Pago": 18261368.5 },
    { "Mes": "DEZ/2020", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 235166299.0, "Provisao": 235166299.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 235166299.0, "Pago": 230752495.73 },
    { "Mes": "DEZ/2020", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 13350325.0, "Provisao": 13350325.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 13350325.0, "Pago": 13350325.0 },
    { "Mes": "DEZ/2020", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 251877685.0, "Provisao": 250377685.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 251877685.0, "Pago": 249071065.73 },
    { "Mes": "DEZ/2020", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2019": [
    { "Mes": "DEZ/2019", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 52627961.0, "Provisao": 52627961.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 52627961.0, "Pago": 50669273.68 },
    { "Mes": "DEZ/2019", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 300099498.0, "Provisao": 300099498.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 300099498.0, "Pago": 298377700.32 },
    { "Mes": "DEZ/2019", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 250000.0, "Provisao": 250000.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 250000.0, "Pago": 250000.0 },
    { "Mes": "DEZ/2019", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 213192461.0, "Provisao": 213192461.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 213192461.0, "Pago": 212450702.04 },
    { "Mes": "DEZ/2019", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2018": [
    { "Mes": "DEZ/2018", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 161947881.0, "Provisao": 158580228.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 161947881.0, "Pago": 161821896.79 },
    { "Mes": "DEZ/2018", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 527581781.0, "Provisao": 526244675.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 527581781.0, "Pago": 472534575.48 },
    { "Mes": "DEZ/2018", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 48100142.0, "Provisao": 48100142.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 48100142.0, "Pago": 48100142.0 },
    { "Mes": "DEZ/2018", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 302094244.0, "Provisao": 300094244.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 302094244.0, "Pago": 301419777.67 },
    { "Mes": "DEZ/2018", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2017": [
    { "Mes": "DEZ/2017", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 544026366.0, "Provisao": 543326366.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 544026366.0, "Pago": 544026366.0 },
    { "Mes": "DEZ/2017", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 454316278.0, "Provisao": 454316278.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 454316278.0, "Pago": 454316278.0 },
    { "Mes": "DEZ/2017", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 490514107.0, "Provisao": 490514107.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 490514107.0, "Pago": 490514107.0 },
    { "Mes": "DEZ/2017", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 242095908.0, "Provisao": 242095908.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 242095908.0, "Pago": 241951908.0 },
    { "Mes": "DEZ/2017", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2016": [
    { "Mes": "DEZ/2016", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 508107767.0, "Provisao": 508107767.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 508107767.0, "Pago": 508107767.0 },
    { "Mes": "DEZ/2016", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 444390000.0, "Provisao": 444390000.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 444390000.0, "Pago": 444390000.0 },
    { "Mes": "DEZ/2016", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 651666699.0, "Provisao": 651666699.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 651666699.0, "Pago": 651666699.0 },
    { "Mes": "DEZ/2016", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 246533038.0, "Provisao": 246533038.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 246533038.0, "Pago": 246533038.0 },
    { "Mes": "DEZ/2016", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 402206.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 402206.0, "Empenhado": 0.0, "Pago": 0.0 }
  ],
  "2015": [
    { "Mes": "DEZ/2015", "Acao": "123G", "Desc": "IMPLANTACAO DE ESTALEIRO E BASE NAVAL PARA CONSTRUCAO E MANU", "Dotacao": 709970921.0, "Provisao": 709970921.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 709970921.0, "Pago": 625691062.62 },
    { "Mes": "DEZ/2015", "Acao": "123H", "Desc": "CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR", "Dotacao": 255000000.0, "Provisao": 255000000.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 255000000.0, "Pago": 255000000.0 },
    { "Mes": "DEZ/2015", "Acao": "123I", "Desc": "CONSTRUCAO DE SUBMARINOS CONVENCIONAIS", "Dotacao": 286221193.0, "Provisao": 286221193.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 286221193.0, "Pago": 286221193.0 },
    { "Mes": "DEZ/2015", "Acao": "14T7", "Desc": "DESENVOLVIMENTO DE SISTEMAS DE TECNOLOGIA NUCLEAR DA MARINHA", "Dotacao": 273523498.0, "Provisao": 273523498.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 273523498.0, "Pago": 273523498.0 },
    { "Mes": "DEZ/2015", "Acao": "1N47", "Desc": "CONSTRUCAO DE NAVIOS-PATRULHA DE 500 TONELADAS (NPA 500T)", "Dotacao": 0.0, "Provisao": 0.0, "Destaque": 0.0, "Disponivel": 0.0, "Empenhado": 0.0, "Pago": 0.0 }
  ]
};

// Formatação BRL
const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });

// Função Principal de Renderização (MODO ESTÁTICO)
function renderizarTabela(ano) {
    const tbody = document.querySelector("#pac-table tbody");
    if (!tbody) return;
    
    tbody.innerHTML = '<tr><td colspan="9" class="text-center">Carregando dados locais...</td></tr>';

    const dadosAno = PAC_DB[ano];

    tbody.innerHTML = '';
    
    if (!dadosAno || dadosAno.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">Sem dados para o ano selecionado (Base Local).</td></tr>';
        return;
    }

    dadosAno.forEach(d => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td><span class="badge bg-secondary">${d.Mes}</span></td>
            <td><strong>${d.Acao}</strong></td>
            <td><small>${d.Desc}</small></td>
            <td>${brl.format(d.Dotacao)}</td>
            <td>${brl.format(d.Provisao)}</td>
            <td>${brl.format(d.Destaque)}</td>
            <td class="text-primary fw-bold">${brl.format(d.Disponivel)}</td>
            <td>${brl.format(d.Empenhado)}</td>
            <td>${brl.format(d.Pago)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// Inicialização
document.addEventListener("DOMContentLoaded", () => {
    const selectAno = document.getElementById("ano-exercicio") || document.getElementById("ano");
    
    if (selectAno) {
        // Carrega o ano inicial (padrão 2025 ou o que estiver selecionado)
        renderizarTabela(selectAno.value);

        // Evento de troca
        selectAno.addEventListener("change", (e) => {
            const anoSelecionado = e.target.value;
            renderizarTabela(anoSelecionado);
            
            // Mantém a compatibilidade com o gráfico (se existir em outro arquivo)
            if (typeof atualizarGrafico === 'function') {
                atualizarGrafico(anoSelecionado);
            }
        });
    } else {
        console.warn("Select de ano não encontrado. Verifique o ID no HTML.");
    }
});
