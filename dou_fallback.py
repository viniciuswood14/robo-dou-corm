# Nome do arquivo: dou_fallback.py
# Módulo de Redundância - Scraper do DOU Público (in.gov.br)

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
from typing import List, Dict

# URL de Busca Oficial do DOU
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_dou_publico(termo: str, data_pt: str, secao: str = "do1") -> List[Dict]:
    """
    Busca um termo específico no site in.gov.br para uma data específica.
    """
    results = []
    
    params = {
        "q": f'"{termo}"', # Aspas para busca exata
        "s": secao,
        "exact": "true",
        "dt": data_pt,
        "dtEnd": data_pt,
        "sortType": "0"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }

    # print(f"[Fallback DOU] Buscando '{termo}' em {data_pt} ({secao})...") # Debug opcional

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        try:
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            if resp.status_code != 200:
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            script_results = soup.find_all("h5", class_="title-marker")

            for item in script_results:
                a_tag = item.find("a")
                if not a_tag: continue
                
                link_rel = a_tag.get("href")
                full_link = f"https://www.in.gov.br{link_rel}"
                title = a_tag.get_text(strip=True)
                
                abstract = ""
                parent = item.find_parent("div")
                if parent:
                    p_tag = parent.find("p", class_="abstract-marker")
                    if p_tag: abstract = p_tag.get_text(strip=True)

                results.append({
                    "organ": "DOU Público (Fallback)",
                    "type": "Resultado de Busca",
                    "summary": title,
                    "raw": f"{title}\n{abstract}\nLink: {full_link}",
                    "relevance_reason": f"Encontrado via busca de redundância pelo termo: '{termo}'",
                    "section": secao.upper(),
                    "link": full_link
                })
                
        except Exception as e:
            print(f"[Fallback] Erro na busca de '{termo}': {e}")

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Orquestrador da Redundância.
    Converte a data e dispara buscas paralelas para todas as keywords críticas.
    """
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista CORRIGIDA de termos (apenas strings)
    termos_criticos = [
        # Termos Estratégicos Principais
        "Marinha do Brasil",
        "Comando da Marinha",
        "Orçamento Fiscal",
        "Crédito Suplementar",
        "Remanejamento",
        "Ministério da Defesa",
        "PROSUB",
        "Amazul",
        "Forças Armadas",
        "Autoridade Marítima",
        "Empresa Gerencial de Projetos Navais",
        "Programa Nuclear Brasileiro",
        "Amazônia Azul",

        # Códigos de UGs (Busca pelo número é muito efetiva no DOU)
        "52131", # Comando da Marinha
        "52133", # SECIRM
        "52232", # CCCPM
        "52233", # AMAZUL
        "52931", # Fundo Naval
        "52932", # Fundo D.E.P.M.
        "52000", # MD

        # Instrumentos Orçamentários e Siglas
        "Fundos Públicos",
        "Relatório de Avaliação de Receitas e Despesas Primárias",
        "RARDP",
        "Programação Orçamentária e Financeira",
        "Decreto de Programação Orçamentária e Financeira",
        "DPOF",
        "Lei Orçamentária Anual",
        "Lei de Diretrizes Orçamentárias",
        "Lei Orçamentária",
        "Plano Plurianual",
        
        # Gatilhos de Ação Orçamentária
        "Altera grupos de natureza de despesa",
        "Limites de movimentação",
        "Limites de pagamento",
        "Fontes de recursos",
        "Movimentação e empenho"
    ]
    
    # Junta com as keywords personalizadas do usuário e remove duplicatas
    lista_busca = list(set(termos_criticos + keywords))
    
    print(f"[Fallback] Iniciando busca paralela para {len(lista_busca)} termos...")

    tasks = []
    for kw in lista_busca:
        # Busca na Seção 1 (Atos Normativos/Orçamento) que é a mais crítica
        tasks.append(buscar_dou_publico(kw, data_pt, "do1")) 
    
    # Executa tudo em paralelo
    resultados_matrix = await asyncio.gather(*tasks)
    
    # Organiza e deduplica os resultados
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
                
    return final_pubs
