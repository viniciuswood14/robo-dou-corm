# Nome do arquivo: dou_fallback.py
# Versão: 3.0 (Crawler de Links - Método Blindado)

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
import random
import re
from typing import List, Dict

# URL de Busca Oficial do DOU
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_dou_publico(termo: str, data_pt: str, secao: str = "do1") -> List[Dict]:
    """
    Busca resiliente: Procura por padrões de LINK em vez de classes CSS específicas.
    """
    results = []
    
    # Parâmetros de busca
    params = {
        "q": f'"{termo}"',
        "s": secao,
        "exact": "true",
        "dt": data_pt,
        "dtEnd": data_pt,
        "sortType": "0"
    }
    
    # Headers simplificados para evitar bloqueio por excesso de especificidade
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    # print(f"[DEBUG] Buscando: '{termo}' em {data_pt}...")

    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        try:
            # Delay aleatório para não sobrecarregar o servidor
            await asyncio.sleep(random.uniform(0.3, 0.8))
            
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ [ERRO HTTP] Termo: '{termo}' | Status: {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # --- LÓGICA "TRATOR" (BUSCA POR LINKS) ---
            # Em vez de procurar classes CSS que mudam, procuramos o padrão do link
            # Padrão de link do DOU: /web/dou/-/titulo-da-materia-ID
            
            found_links = soup.find_all("a", href=re.compile(r"/web/dou/-/"))
            
            # Se não achou links diretos, tenta verificar se está dentro de scripts (JSON oculto)
            if not found_links:
                 # Fallback do Fallback: Tenta regex no texto bruto caso seja renderizado via JS
                 raw_links = re.findall(r'href="(/web/dou/-/[^"]+)"', resp.text)
                 # (Implementação simplificada: se achou via regex, teríamos que limpar o título manualmente)
                 # Por enquanto, confiamos no Beautifulsoup

            processed_urls = set()

            for tag in found_links:
                href = tag.get("href")
                if not href: continue
                
                # Garante URL completa
                full_link = f"https://www.in.gov.br{href}" if href.startswith("/") else href
                
                # Evita duplicatas (título e imagem costumam ter o mesmo link)
                if full_link in processed_urls:
                    continue
                processed_urls.add(full_link)

                # Extrai o Título (texto do link ou title attribute)
                title = tag.get_text(strip=True)
                if not title:
                    title = tag.get("title", "")
                
                if len(title) < 5: # Ignora links quebrados ou ícones sem texto
                    continue

                # Tenta achar o "Resumo" (geralmente está num parágrafo próximo ou irmão)
                # Estrutura comum: <div> <a>Titulo</a> <p>Resumo</p> </div>
                abstract = ""
                parent = tag.find_parent("div")
                if parent:
                    # Pega todo o texto do container pai, removendo o título
                    full_text = parent.get_text(" ", strip=True)
                    abstract = full_text.replace(title, "").strip()[:300] + "..." # Limita tamanho

                # Monta o objeto
                item_final = {
                    "organ": "DOU Público (Fallback)",
                    "type": "Resultado de Busca",
                    "summary": title,
                    "raw": f"{title}\n{abstract}",
                    "relevance_reason": f"Busca Pública: '{termo}'",
                    "section": secao.upper(),
                    "link": full_link
                }
                
                results.append(item_final)
            
            if results:
                print(f"✅ [SUCESSO] '{termo}': {len(results)} encontrados.")

        except Exception as e:
            print(f"❌ [EXCEÇÃO] Erro ao buscar '{termo}': {e}")

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Orquestrador da Redundância.
    """
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista de termos
    termos_criticos = [
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
        "52131", "52133", "52232", "52233", "52931", "52932", "52000",
        "Fundos Públicos",
        "RARDP",
        "Programação Orçamentária e Financeira",
        "DPOF",
        "Lei Orçamentária",
        "Plano Plurianual",
        "Movimentação e empenho"
    ]
    
    lista_busca = list(set(termos_criticos + keywords))
    
    print(f"--- INICIANDO FALLBACK V3 (Links) ---")
    print(f"Data: {data_pt} | Termos: {len(lista_busca)}")

    tasks = []
    for kw in lista_busca:
        tasks.append(buscar_dou_publico(kw, data_pt, "do1")) 
    
    resultados_matrix = await asyncio.gather(*tasks)
    
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
    
    print(f"--- FIM DO FALLBACK: {len(final_pubs)} itens únicos encontrados ---")
    return final_pubs
