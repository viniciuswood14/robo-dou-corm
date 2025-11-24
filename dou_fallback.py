# Nome do arquivo: dou_fallback.py
# Versão: DEBUG EXTREMO (Para descobrir por que retorna 0)

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
    Busca com logs agressivos para diagnosticar bloqueios ou erro de HTML.
    """
    results = []
    
    params = {
        "q": f'"{termo}"',
        "s": secao,
        "exact": "true",
        "dt": data_pt,
        "dtEnd": data_pt,
        "sortType": "0"
    }
    
    # Headers reforçados para parecer um Chrome real
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }

    print(f"--- [DEBUG DEEP] Tentando buscar: '{termo}' ---")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        try:
            resp = await client.get(SEARCH_URL, params=params, headers=headers)
            
            print(f"📡 STATUS CODE: {resp.status_code}")
            print(f"📡 URL FINAL: {resp.url}")
            
            if resp.status_code != 200:
                print(f"❌ Erro HTTP: {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # --- VERIFICAÇÃO DO CONTEÚDO HTML ---
            page_title = soup.title.string if soup.title else "Sem Titulo"
            print(f"📄 Título da Página retornada: {page_title}")
            
            # Verifica se tem algum texto de erro comum
            if "captcha" in resp.text.lower() or "acesso negado" in resp.text.lower():
                print("⛔ BLOQUEIO DETECTADO (Captcha ou WAF)!")
                return []

            # Tenta encontrar os cards de resultado
            # Vamos testar seletores alternativos caso o title-marker tenha mudado
            script_results = soup.find_all("h5", class_="title-marker")
            
            if not script_results:
                print("⚠️ AVISO: Nenhum elemento 'h5.title-marker' encontrado.")
                print("   -> Verificando se existem 'div.result-item'...")
                div_results = soup.find_all("div", class_="result-item")
                print(f"   -> Encontrados {len(div_results)} divs 'result-item'.")
                
                # Se não achou nada, imprime um pedaço do HTML para analisarmos
                print("   -> DUMP DO HTML (Primeiros 1000 caracteres):")
                print(resp.text[:1000])
                print("------------------------------------------------")

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
                
                results.append({
                    "organ": "DOU Público (Fallback)",
                    "type": "Resultado",
                    "summary": title,
                    "raw": f"{title}\n{abstract}",
                    "relevance_reason": f"Busca: '{termo}'",
                    "section": secao.upper(),
                    "link": full_link
                })
                count += 1
            
            print(f"📊 Itens extraídos com sucesso: {count}")

        except Exception as e:
            print(f"❌ EXCEÇÃO CRÍTICA: {e}")

    return results

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Versão simplificada para teste. Busca APENAS UM termo para não poluir o log.
    """
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # VAMOS TESTAR APENAS UM TERMO FORTE
    termo_teste = "Defesa"
    
    print(f"=== INICIANDO TESTE ÚNICO DE DIAGNÓSTICO ===")
    print(f"Data: {data_pt} | Termo: {termo_teste}")

    # Executa apenas uma busca
    resultado = await buscar_dou_publico(termo_teste, data_pt, "do1")
    
    print(f"=== FIM DO TESTE ÚNICO. Retornou {len(resultado)} itens. ===")
    return resultado
