# Nome do arquivo: dou_fallback.py
# Versão: 8.0 (Estilo Ro-DOU - Busca Pública Bruta)

import httpx
from bs4 import BeautifulSoup
from datetime import datetime
import asyncio
import random
import re
from typing import List, Dict

# URL que o Ro-DOU e o site oficial usam para pesquisar
SEARCH_URL = "https://www.in.gov.br/consulta/-/buscar/dou"

async def buscar_termo_bruto(termo: str, data_pt: str, secao_param: str) -> List[Dict]:
    """
    Realiza a busca no portal e extrai links usando regex e soup.
    """
    results = []
    
    # Parâmetros exatos da busca avançada do DOU
    params = {
        "q": f'"{termo}"', # Aspas para exatidão (opcional, mas ajuda a filtrar lixo)
        "s": secao_param,  # do1, do2, doe
        "exact": "true",
        "dt": data_pt,     # Data inicial
        "dtEnd": data_pt,  # Data final
        "sortType": "0"    # Relevância
    }
    
    # Headers de navegador padrão
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            # Pequeno delay aleatório para não parecer ataque DDoS
            await asyncio.sleep(random.uniform(0.2, 0.5))
            
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ [ERRO HTTP] Busca '{termo}': {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # --- ESTRATÉGIA ROBUSTA DE EXTRAÇÃO ---
            # 1. Tenta encontrar links de matérias pelo padrão de URL
            # Padrão: /web/dou/-/titulo-da-materia-id
            links_encontrados = soup.find_all("a", href=re.compile(r"/web/dou/-/"))
            
            processed_urls = set()

            for tag in links_encontrados:
                href = tag.get("href")
                if not href: continue
                
                # Monta URL absoluta
                full_link = f"https://www.in.gov.br{href}" if href.startswith("/") else href
                
                # Remove duplicatas
                if full_link in processed_urls: continue
                processed_urls.add(full_link)

                # Extrai Título
                # Limpa espaços e quebras de linha
                title = " ".join(tag.get_text().split())
                
                # Validação básica de título (evita links de ícones vazios)
                if len(title) < 5:
                    title = tag.get("title", "")
                    if len(title) < 5: continue

                # Tenta extrair um Resumo (Snippet)
                # Geralmente o resumo está num <p> ou <div> próximo ao link
                abstract = ""
                # Procura um container pai próximo (ex: o card do resultado)
                card = tag.find_parent("div", class_=re.compile(r"(result|item|search)"))
                if card:
                    # Pega o texto do card, remove o título para sobrar o resumo
                    full_text = " ".join(card.get_text().split())
                    abstract = full_text.replace(title, "").strip()[:400] # Pega 400 chars
                
                # Se não achou card, usa o próprio título como resumo
                if not abstract: abstract = "Conteúdo obtido via busca pública."

                results.append({
                    "organ": "DOU Público (Busca)",
                    "type": "Resultado",
                    "summary": title,
                    "raw": f"{title}\n{abstract}\nLink: {full_link}",
                    "relevance_reason": f"Termo encontrado: '{termo}'",
                    "section": secao_param.upper(),
                    "link": full_link
                })
            
            if results:
                print(f"   ✅ '{termo}': {len(results)} resultados.", flush=True)

        except Exception as e:
            print(f"❌ [EXCEÇÃO] '{termo}': {e}", flush=True)

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    # 1. Configura Data
    try:
        dt = datetime.strptime(data_iso.strip(), "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y") # Busca exige DD-MM-YYYY
    except Exception as e:
        print(f"❌ [FALLBACK] Erro data: {e}", flush=True)
        return []

    # 2. Lista de Keywords (Focada e Otimizada para Busca)
    # Na busca, menos é mais. Termos muito genéricos como "Lei" trazem lixo.
    # Focamos nas UGs e Termos Compostos.
    termos_criticos = [
        '"Marinha do Brasil"', # Aspas forçam frase exata
        '"Comando da Marinha"',
        '"PROSUB"',
        '"Amazul"',
        "52131", # UG MB
        "52000", # UG MD
        '"Orçamento Fiscal"',
        '"Crédito Suplementar"',
        '"Remanejamento"',
        '"Ministério da Defesa"',
        '"Forças Armadas"',
        '"Autoridade Marítima"',
        '"Programa Nuclear"',
        '"Amazônia Azul"',
        '"Plano Plurianual"',
        '"Movimentação e empenho"'
    ]
    
    # Adiciona keywords do usuário (aspas se tiver espaço)
    for k in keywords:
        k = k.strip()
        if " " in k and '"' not in k:
            termos_criticos.append(f'"{k}"')
        else:
            termos_criticos.append(k)
            
    # Remove duplicatas
    lista_busca = list(set(termos_criticos))

    print(f"--- [FALLBACK v8.0] Iniciando BUSCA PÚBLICA para {data_pt} ---", flush=True)
    print(f"    Termos a pesquisar: {len(lista_busca)}", flush=True)

    # 3. Dispara as buscas
    # Seção 1 (do1) é a prioritária para atos normativos e orçamento
    # Seção 2 (do2) é pessoal (opcional, pode descomentar se quiser)
    secoes = ["do1"] 
    
    all_tasks = []
    for kw in lista_busca:
        for sec in secoes:
            all_tasks.append(buscar_termo_bruto(kw, data_pt, sec))
    
    # Executa em paralelo
    # (O in.gov.br aguenta bem, mas o render pode ter limite de conexões)
    resultados_matrix = await asyncio.gather(*all_tasks)
    
    # 4. Consolida e Deduplica
    final_pubs = []
    seen_links = set()
    
    for lista in resultados_matrix:
        for item in lista:
            if item['link'] not in seen_links:
                final_pubs.append(item)
                seen_links.add(item['link'])
    
    print(f"📊 [FIM FALLBACK] Total Final: {len(final_pubs)} matérias únicas.", flush=True)
    return final_pubs
