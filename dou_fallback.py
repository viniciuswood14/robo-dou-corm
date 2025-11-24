# Nome do arquivo: dou_fallback.py
# Versão: 7.0 (Sessão Real + IDs Numéricos + Varredura de Script)

import httpx
import asyncio
import json
import re
import unicodedata
from datetime import datetime
from typing import List, Dict, Any

# URLs Oficiais
BASE_URL = "https://www.in.gov.br"
PAGE_URL = "https://www.in.gov.br/leiturajornal"
API_URL = "https://www.in.gov.br/leitura/-/leitura/dou"

def normalizar_texto(texto: str) -> str:
    if not texto: return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower()

def extrair_materias_da_arvore(no: Any, keywords_norm: List[str], resultados: List[Dict], secao_nome: str):
    """ Percorre a árvore JSON (nós e folhas) procurando matérias. """
    if isinstance(no, list):
        for item in no:
            extrair_materias_da_arvore(item, keywords_norm, resultados, secao_nome)
        return

    if isinstance(no, dict):
        # Tenta identificar título e link
        titulo = no.get("text") or no.get("name") or ""
        url_title = no.get("urlTitle")
        
        # É uma matéria válida?
        if titulo and url_title:
            titulo_norm = normalizar_texto(titulo)
            
            # Verifica keywords
            for kw in keywords_norm:
                if kw in titulo_norm:
                    link = f"https://www.in.gov.br/web/dou/-/{url_title}"
                    
                    # Evita duplicatas na lista final
                    if not any(r['link'] == link for r in resultados):
                        resultados.append({
                            "organ": "DOU (Redundância)",
                            "type": "Matéria",
                            "summary": titulo,
                            "raw": f"{titulo}\nLink: {link}",
                            "relevance_reason": f"[Fallback] Termo encontrado: '{kw}'",
                            "section": secao_nome,
                            "link": link
                        })
                    break 
        
        # Recursão para filhos
        children = no.get("children") or no.get("subordinados")
        if children:
            extrair_materias_da_arvore(children, keywords_norm, resultados, secao_nome)

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    # 1. Configura Data
    try:
        dt = datetime.strptime(data_iso.strip(), "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y") # Formato URL (21-11-2025)
    except Exception as e:
        print(f"❌ [FALLBACK] Erro data: {e}", flush=True)
        return []

    # 2. Lista de Keywords
    termos_criticos = [
        "marinha", "defesa", "comando", "almirante", "prosub", "amazul",
        "nuclear", "orcamento", "credito", "decreto", "portaria", "lei",
        "aviso", "extrato", "52131", "52000", "suplementar", "plano plurianual"
    ]
    lista_busca_norm = list(set(termos_criticos + [normalizar_texto(k) for k in keywords]))

    print(f"--- [FALLBACK v7.0] Iniciando para {data_pt} ---", flush=True)

    # 3. Mapeamento de Seções (Nome -> ID Interno do Liferay)
    # 515 = DO1, 525 = DO2, 529 = DO3, 600 = Extra
    mapa_secoes = [
        {"nome": "DO1", "params": {"secao": "dou1", "data": data_pt}, "api_jornal": 515},
        {"nome": "DO2", "params": {"secao": "dou2", "data": data_pt}, "api_jornal": 525},
        {"nome": "DOE", "params": {"secao": "doue", "data": data_pt}, "api_jornal": 600} 
    ]

    resultados = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers=headers) as client:
        
        # PASSO A: Acessar a Home para pegar Cookies iniciais
        try:
            await client.get(BASE_URL)
        except: pass

        for sec in mapa_secoes:
            nome = sec["nome"]
            print(f"📡 Processando {nome}...", flush=True)
            
            try:
                # PASSO B: Visita a página 'leiturajornal' (Igual navegador)
                # Isso define o cookie de sessão para a data e seção corretas
                resp_page = await client.get(PAGE_URL, params=sec["params"])
                
                arvore_encontrada = None

                # Tenta achar a variável jsonArray no HTML (Método Ninja)
                # O Liferay imprime: var jsonArray = [...];
                match = re.search(r'var\s+jsonArray\s*=\s*(\[.*?\]);', resp_page.text, re.DOTALL)
                if match:
                    try:
                        json_str = match.group(1)
                        arvore_encontrada = json.loads(json_str)
                        # print(f"   ✅ Estrutura JSON extraída do HTML de {nome}!", flush=True)
                    except: pass
                
                # Se não achou no HTML, tenta a API usando o ID numérico (Método Clássico)
                if not arvore_encontrada:
                    # print(f"   Science: Tentando API direta para jornal={sec['api_jornal']}...", flush=True)
                    # Nota: A API usa params ligeiramente diferentes dependendo da versão
                    params_api = {"jornal": sec['api_jornal'], "data": data_pt, "json": "true"}
                    
                    # Headers específicos para simular AJAX
                    headers_api = headers.copy()
                    headers_api["X-Requested-With"] = "XMLHttpRequest"
                    
                    resp_api = await client.get(API_URL, params=params_api, headers=headers_api)
                    if resp_api.status_code == 200:
                        try:
                            arvore_encontrada = resp_api.json()
                        except: pass

                # PASSO C: Processa o que encontrou
                if arvore_encontrada:
                    count_antes = len(resultados)
                    extrair_materias_da_arvore(arvore_encontrada, lista_busca_norm, resultados, nome)
                    delta = len(resultados) - count_antes
                    if delta > 0:
                         print(f"   ✅ {delta} matérias relevantes encontradas na {nome}.", flush=True)
                    else:
                         print(f"   ℹ️ Jornal lido, mas nenhuma keyword encontrada na {nome}.", flush=True)
                else:
                    print(f"   ⚠️ Falha ao ler estrutura da {nome} (HTML ou API vazios).", flush=True)

            except Exception as e:
                print(f"   ❌ Erro na seção {nome}: {e}", flush=True)

    print(f"📊 [FIM FALLBACK] Total Final: {len(resultados)}", flush=True)
    return resultados
