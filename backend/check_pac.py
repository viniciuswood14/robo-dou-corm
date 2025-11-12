# Nome do arquivo: check_pac.py
#
# Módulo para monitorar dotações do Novo PAC
# e alertar sobre mudanças.

import json
import os
import asyncio
from typing import Dict, Set, List, Any, Optional
from datetime import datetime

# Importa o sender do Telegram
from telegram import send_telegram_message

# Importa a biblioteca do SIOP
try:
    from orcamentobr import despesa_detalhada
except ImportError:
    print("ERRO: Biblioteca 'orcamentobr' não encontrada.")
    print("Por favor, adicione 'orcamentobr' ao seu requirements.txt")
    raise

# --- 1. Mapeamento dos Programas e Ações (O "Cérebro") ---
# Copiado do seu app.py (dashboard)
PROGRAMAS_ACOES = {
    'PROSUB': {
        '123G': 'IMPLANTACAO DE ESTALEIRO E BASE NAVAL',
        '123H': 'CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR',
        '123I': 'CONSTRUCAODE SUBMARINOS CONVENCIONAIS'
    },
    'PNM': {
        '14T7': 'DESENVOLVIMENTO DE TECNOLOGIA NUCLEAR'
    },
    'PRONAPA': {
        '1N47': 'CONSTRUCAO DE NAVIOS-PATRULHA 500T'
    }
}

# --- 2. Configuração do Estado ---
# Um arquivo separado para guardar os últimos valores vistos
STATE_FILE_PATH = os.environ.get("PAC_STATE_FILE_PATH", "/dados/pac_state.json")


def load_pac_state() -> Dict[str, Dict[str, float]]:
    """
    Carrega o estado (últimos valores por ano).
    Formato: { "2024": {"123G": 100.0, "123H": 200.0}, "2025": {...} }
    """
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Garante que os valores internos sejam floats
            return {
                ano: {acao: float(val) for acao, val in acoes.items()}
                for ano, acoes in data.items()
            }
    except (FileNotFoundError, json.JSONDecodeError):
        return {} # Retorna um dict vazio se não existir ou for inválido

def save_pac_state(state: Dict[str, Dict[str, float]]):
    """Salva o estado atual (valores por ano)."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar estado do PAC: {e}")


# --- 3. Função de Busca de Dados (Copiada do app.py corrigido) ---
async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    """
    Busca os dados de UMA ação, totalizados.
    Retorna uma "linha" (dict) com os totais.
    """
    print(f"[PAC] Buscando dados totais para {ano}, Ação {acao_cod}...")
    try:
        # 1. Busca os dados detalhados (pode retornar múltiplas linhas)
        df_detalhado = despesa_detalhada(
            exercicio=ano,
            acao=acao_cod, # Filtro da ação
            inclui_descricoes=True,
            ignore_secure_certificate=True
        )
        
        if df_detalhado.empty:
            print(f"[PAC] Nenhum dado encontrado para {acao_cod} em {ano}.")
            return None
            
        # 2. Soma os valores para obter o TOTAL da ação
        colunas_numericas = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago']
        colunas_para_somar = [col for col in colunas_numericas if col in df_detalhado.columns]
        
        if not colunas_para_somar:
            return None
            
        # .sum() cria uma "Series" (basicamente uma linha de totais)
        totais_acao = df_detalhado[colunas_para_somar].sum()
        
        # Converte a "Series" do Pandas para um dicionário Python
        return totais_acao.to_dict()

    except Exception as e:
        print(f"Erro ao consultar o SIOP (PAC) para a ação {acao_cod}: {e}")
        # Envia um alerta de falha na busca
        await send_telegram_message(f"⚠️ Erro no Robô PAC:\nFalha ao consultar SIOP para Ação {acao_cod} (Ano {ano}).\nErro: {e}")
        return None


# --- 4. Função Principal de Verificação ---

def formatar_moeda(valor):
    """Formata um número como R$ 1.234,56"""
    if valor is None:
        return "R$ 0,00"
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

async def check_and_process_pac(ano_exercicio: str):
    """
    Função chamada pelo AGENDADOR (run_check.py).
    Compara a dotação atual com a dotação salva e alerta sobre mudanças.
    """
    print(f"--- Iniciando verificação do PAC (Dotações) para o ano: {ano_exercicio} ---")
    
    # 1. Carrega o estado (valores antigos)
    full_state = load_pac_state()
    previous_values = full_state.get(ano_exercicio, {})
    
    # 2. Busca os valores atuais
    current_values: Dict[str, float] = {}
    changes_found: List[str] = []
    
    try:
        ano_int = int(ano_exercicio)
    except ValueError:
        print(f"Ano inválido para o PAC: {ano_exercicio}")
        return

    # Loop por todos os programas e ações que queremos monitorar
    for programa, acoes in PROGRAMAS_ACOES.items():
        for acao_cod, acao_desc in acoes.items():
            
            dados_linha = await buscar_dados_acao_pac(ano_int, acao_cod)
            
            # Pausa para não sobrecarregar a API
            await asyncio.sleep(1) 
            
            if dados_linha is None:
                # Falha na busca, o erro já foi logado e enviado ao Telegram
                continue

            # Pegamos a Dotação Atual (loa_mais_credito)
            dotacao_atual = float(dados_linha.get('loa_mais_credito', 0.0))
            current_values[acao_cod] = dotacao_atual
            
            # 3. Compara com o valor anterior
            valor_antigo = float(previous_values.get(acao_cod, 0.0))
            
            if dotacao_atual != valor_antigo:
                print(f"[PAC] MUDANÇA DETECTADA: {acao_cod}")
                
                # Monta a mensagem da mudança
                change_msg = (
                    f"*{acao_cod} - {acao_desc.upper()}*\n"
                    f"Valor Anterior: {formatar_moeda(valor_antigo)}\n"
                    f"Valor Novo: *{formatar_moeda(dotacao_atual)}*\n"
                )
                changes_found.append(change_msg)

    # 4. Envia o alerta se houver mudanças
    if changes_found:
        print("Enviando alerta de mudança de dotação do PAC...")
        
        agora = datetime.now().strftime('%d/%m/%Y às %H:%M')
        header = f"🔔 *Alerta de Alteração Orçamentária - Novo PAC* 🔔\n(Exercício: {ano_exercicio} - Verificado em: {agora})\n\n"
        
        message = header + "\n".join(changes_found)
        
        await send_telegram_message(message)
    
    else:
        print("Nenhuma alteração nas dotações do PAC detectada.")

    # 5. Salva o estado atual (mesmo que não haja mudanças)
    full_state[ano_exercicio] = current_values
    save_pac_state(full_state)
    
    print(f"--- Verificação do PAC (Dotações) finalizada ---")
