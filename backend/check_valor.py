# Nome do arquivo: check_valor.py

import json
import os
import asyncio
from typing import Dict, Set

# Nossas importações
from google_search import perform_google_search, SearchResult
from telegram import send_telegram_message

# Importações do Robô DOU (reutilizando a IA e config)
try:
    from api import (
        get_ai_analysis,
        GEMINI_API_KEY,
        Publicacao  # Usaremos para estruturar
    )
    import google.generativeai as genai
except ImportError:
    print("Erro: Falha ao importar 'api.py'.")
    raise

# --- CONFIGURAÇÃO DO ESTADO ---
STATE_FILE_PATH = os.environ.get("VALOR_STATE_FILE_PATH", "/dados/valor_processed_links.json")

# --- CONFIGURAÇÃO DA BUSCA ---
# Agrupe suas palavras-chave em queries. 
# A API gratuita tem limite de 100/dia, então não exagere.
# 3 queries por dia é excelente.
SEARCH_QUERIES = [
    '"contas publicas" OR "orcamento" OR "politica fiscal"',
    '"fundo publico" OR "fundo privado" OR "economia brasilia"',
    '"defesa" OR "marinha" OR "forças armadas" OR "base industrial de defesa"'
]

# --- PROMPT DA IA PARA O VALOR ---
GEMINI_VALOR_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler o TÍTULO e o RESUMO (snippet) de uma notícia do Valor Econômico e dizer, em uma única frase curta (máximo 2 linhas), qual o impacto ou relevância para a Marinha, Defesa ou para o Orçamento Federal.

- Se for sobre Orçamento Federal, LDO, LOA, Teto de Gastos, Arcabouço Fiscal, etc., diga o impacto.
- Se for sobre Fundos Públicos, analise se afeta a Marinha (Fundo Naval) ou o orçamento.
- Se a notícia for genérica (ex: "fundo privado investe em startup") ou sem impacto claro para o governo/defesa, responda APENAS: "Sem impacto direto."

TÍTULO: {titulo}
RESUMO: {resumo}

Responda só a frase final, sem rodeio.
"""


def load_valor_state() -> Set[str]:
    """Carrega os links já processados."""
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_valor_state(links: Set[str]):
    """Salva os links processados."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(links), f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar estado do Valor: {e}")


async def check_and_process_valor(today_str: str):
    """
    Função principal de verificação do Valor Econômico.
    """
    print(f"--- Iniciando verificação do Valor Econômico para: {today_str} ---")
    
    # 0. Configura a IA
    if not GEMINI_API_KEY:
        print("Erro (Valor): GEMINI_API_KEY não encontrada.")
        return
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        print(f"Falha (Valor) ao inicializar o modelo de IA: {e}")
        return

    # 1. Carrega o estado (links já vistos)
    processed_links = load_valor_state()
    
    # 2. Busca os links de hoje
    all_results: Dict[str, SearchResult] = {} # Usamos um dict para deduplicar links
    
    for query in SEARCH_QUERIES:
        print(f"Buscando query: {query}")
        results = await perform_google_search(query, after_date=today_str)
        for res in results:
            if res.link not in all_results:
                all_results[res.link] = res
        await asyncio.sleep(1) # Pequena pausa para não sobrecarregar a API

    if not all_results:
        print("Nenhuma notícia encontrada no Valor para hoje.")
        return

    # 3. Filtra apenas links novos
    new_results = [res for res in all_results.values() if res.link not in processed_links]

    if not new_results:
        print("Nenhuma notícia *nova* encontrada no Valor.")
        return
    
    print(f"Encontradas {len(new_results)} notícias novas. Analisando com IA...")

    # 4. Analisa com IA
    pubs_finais = []
    links_para_salvar = set(processed_links)

    for res in new_results:
        prompt = GEMINI_VALOR_PROMPT.format(titulo=res.title, resumo=res.snippet)
        
        # Reutiliza a função get_ai_analysis do robô DOU
        ai_reason = await get_ai_analysis(
            clean_text=f"TÍTULO: {res.title}\nSNIPPET: {res.snippet}",
            model=model,
            prompt_template=GEMINI_VALOR_PROMPT
        )
        
        # Adiciona o link ao estado para não processar de novo
        links_para_salvar.add(res.link)

        if ai_reason and "sem impacto direto" not in ai_reason.lower():
            pubs_finais.append({
                "titulo": res.title,
                "link": res.link,
                "analise_ia": ai_reason
            })

    if not pubs_finais:
        print("Análise da IA concluiu que nenhuma notícia nova tem impacto direto.")
        save_valor_state(links_para_salvar) # Salva mesmo assim para não re-analisar
        return

    # 5. Monta e envia a mensagem do Telegram
    lines = [f"Alerta de novas publicações no Valor Econômico ({today_str}):\n"]
    
    for p in pubs_finais:
        lines.append(f"▶️ *Título:* {p['titulo']}")
        lines.append(f"📌 *Link:* {p['link']}")
        lines.append(f"⚓ *Análise IA:* {p['analise_ia']}")
        lines.append("") # Espaçamento

    message = "\n".join(lines)
    await send_telegram_message(message)
    
    # 6. Salva o estado final
    save_valor_state(links_para_salvar)
    print("--- Verificação do Valor finalizada com sucesso. ---")
