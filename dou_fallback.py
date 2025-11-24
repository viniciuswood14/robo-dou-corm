# Nome do arquivo: dou_fallback.py
# Versão: Diagnóstico Completo (Todas as Tags)

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
import random
from typing import List, Dict

# URL de Busca Oficial do DOU
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_dou_publico(termo: str, data_pt: str, secao: str = "do1") -> List[Dict]:
    """
    Busca com logs de diagnóstico para entender falhas.
    """
    results = []
    
    # Parâmetros de busca (exact=false para ser mais abrangente se falhar a exata)
    params = {
        "q": f'"{termo}"', # Aspas para busca exata (pode remover as aspas se quiser resultados mais amplos)
        "s": secao,
        "exact": "true",
        "dt": data_pt,
        "dtEnd": data_pt,
        "sortType": "0"
    }
    
    # Rotação simples de User-Agent para evitar bloqueio
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
    ]

    headers = {
        "User-Agent": random.choice(user_agents)
    }

    # print(f"[DEBUG] Buscando: '{termo}' em {data_pt}...") # Descomente para ver cada termo sendo buscado

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            # Adiciona um pequeno delay aleatório antes da requisição para "humanizar"
            await asyncio.sleep(random.uniform(0.1, 0.5))
            
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ [ERRO HTTP] Termo: '{termo}' | Status: {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Busca os elementos de título que o Liferay (sistema do DOU) gera
            script_results = soup.find_all("h5", class_="title-marker")
            
            if not script_results:
                # Se o HTML voltou OK (200) mas sem resultados, verifica se foi "Nenhum resultado" ou bloqueio
                if "Nenhum resultado encontrado" not in resp.text:
                     # Se não tem a mensagem de "Nenhum resultado", pode ser que a estrutura mudou ou bloqueio soft
                     pass 

            count = 0
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
                
                texto_combinado = f"{title}\n{abstract}"

                results.append({
                    "organ": "DOU Público (Fallback)",
                    "type": "Resultado de Busca",
                    "summary": title,
                    "raw": texto_combinado,
                    "relevance_reason": f"Encontrado via busca pública por: '{termo}'",
                    "section": secao.upper(),
                    "link": full_link
                })
                count += 1
            
            if count > 0:
                print(f"✅ [SUCESSO] '{termo}': {count} encontrados.")

        except Exception as e:
            print(f"❌ [EXCEÇÃO] Erro ao buscar '{termo}': {e}")

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        print("Erro de data no fallback")
        return []

    # --- LISTA COMPLETA DO COMANDANTE ---
    termos_criticos = [
        # Termos Gerais
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

        # UGs (Busca por código é muito precisa)
        "52131", # Comando da Marinha
        "52133", # SECIRM
        "52232", # CCCPM
        "52233", # AMAZUL
        "52931", # Fundo Naval
        "52932", # Fundo D.E.P.M.
        "52000", # Ministério da Defesa

        # Termos Específicos e Siglas
        "Amazônia Azul Tecnologias de Defesa",
        "Caixa de Construções de Casas para o Pessoal da Marinha",
        "Fundos Públicos",
        "Fundo Público",
        "Relatório de Avaliação de Receitas e Despesas Primárias",
        "RARDP",
        "Programação Orçamentária e Financeira",
        "Decreto de Programação Orçamentária e Financeira",
        "DPOF",
        "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
        "Lei Orçamentária Anual",
        "Lei de Diretrizes Orçamentárias",
        "Lei Orçamentária",
        "Plano Plurianual",
        
        # Gatilhos de Ação
        "Altera grupos de natureza de despesa",
        "Limites de movimentação",
        "Limites de pagamento",
        "Fontes de recursos",
        "Movimentação e empenho"
    ]
    
    # Adiciona as keywords manuais (remove duplicatas e normaliza)
    lista_busca = list(set(termos_criticos + keywords))
    
    print(f"--- INICIANDO FALLBACK (FULL) ---")
    print(f"Data: {data_pt} | Total de termos: {len(lista_busca)}")
    print("Isso pode levar alguns segundos devido à quantidade de requisições...")

    tasks = []
    for kw in lista_busca:
        # Busca na Seção 1 (Atos Normativos/Orçamento)
        tasks.append(buscar_dou_publico(kw, data_pt, "do1")) 
    
    # Executa em paralelo (pode gerar muitos logs no console)
    resultados_matrix = await asyncio.gather(*tasks)
    
    # Consolida resultados
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
    
    print(f"--- FIM DO FALLBACK: {len(final_pubs)} itens únicos encontrados ---")
    return final_pubs
