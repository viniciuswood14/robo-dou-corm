# Nome do arquivo: dou_fallback.py
# Versão: 6.1 (Correção Crítica de Parâmetros: do1 -> dou1)

import httpx
import asyncio
import json
import unicodedata
from datetime import datetime
from typing import List, Dict, Any

# Endpoint da árvore JSON
BASE_URL = "https://www.in.gov.br"
LEITURA_API = "https://www.in.gov.br/leitura/-/leitura/dou"

def normalizar_texto(texto: str) -> str:
    if not texto: return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower()

def buscar_recursiva(no: Any, keywords_norm: List[str], resultados: List[Dict], secao: str):
    """ Percorre a árvore JSON procurando títulos. """
    if isinstance(no, list):
        for item in no:
            buscar_recursiva(item, keywords_norm, resultados, secao)
        return

    if isinstance(no, dict):
        titulo = no.get("text") or no.get("name") or ""
        url_title = no.get("urlTitle")
        file_id = no.get("fileId")
        
        if titulo and (url_title or file_id):
            titulo_norm = normalizar_texto(titulo)
            for kw in keywords_norm:
                if kw in titulo_norm:
                    if url_title:
                        link = f"https://www.in.gov.br/web/dou/-/{url_title}"
                    else:
                        continue 

                    resultados.append({
                        "organ": "DOU (Fallback JSON)",
                        "type": "Matéria",
                        "summary": titulo,
                        "raw": f"{titulo}\nLink: {link}",
                        "relevance_reason": f"Encontrado na árvore (v6.1) pelo termo: '{kw}'",
                        "section": secao.upper(),
                        "link": link
                    })
                    break
        
        children = no.get("children") or no.get("subordinados")
        if children:
            buscar_recursiva(children, keywords_norm, resultados, secao)

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    # 1. Prepara múltiplas versões da data
    try:
        dt = datetime.strptime(data_iso.strip(), "%Y-%m-%d")
        data_traco = dt.strftime("%d-%m-%Y") # 21-11-2025
        data_barra = dt.strftime("%d/%m/%Y") # 21/11/2025
    except Exception as e:
        print(f"❌ [FALLBACK] Erro na data: {e}", flush=True)
        return []

    # 2. Keywords
    termos_criticos = [
        "marinha", "defesa", "comando", "almirante", "prosub", "amazul",
        "nuclear", "orcamento", "credito", "decreto", "portaria", "lei",
        "aviso", "extrato", "52131", "52000", "suplementar", "plano plurianual"
    ]
    lista_busca_norm = list(set(termos_criticos + [normalizar_texto(k) for k in keywords]))

    # 3. MATRIZ DE TENTATIVAS (Agora com 'dou1' que é o correto)
    # O site aceita variações dependendo da rota, vamos testar todas
    combinacoes = [
        {"data": data_traco, "secao": "dou1"},  # Padrão descoberto pelo usuário (Traço + dou1)
        {"data": data_traco, "secao": "do1"},   # Padrão antigo (Traço + do1)
        {"data": data_barra, "secao": "dou1"},  # Barra + dou1
        {"data": data_traco, "jornal": "dou1"}, # Parametro 'jornal'
        {"data": data_traco, "jornal": "do1"}
    ]

    print(f"--- [FALLBACK v6.1] Iniciando varredura para {data_traco} ---", flush=True)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.in.gov.br/leitura",
        "X-Requested-With": "XMLHttpRequest"
    }

    resultados = []
    
    # Mapeia as seções para o código correto de busca
    # Se 'dou1' funcionar para a Seção 1, 'dou2' deve ser a Seção 2 e 'doue' a Extra
    secoes_alvo = ["dou1", "dou2", "doue"] 

    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
        try:
            # Aquece a sessão
            await client.get(BASE_URL)

            for sec_cod in secoes_alvo:
                sucesso_secao = False
                
                # Tenta as combinações
                for tentativa in combinacoes:
                    params = {}
                    
                    # Adapta o código da seção para a tentativa atual
                    # Se a tentativa usa 'do1', convertemos 'dou1' -> 'do1'
                    cod_tentativa = sec_cod
                    if "do1" in tentativa.get("secao", "") or "do1" in tentativa.get("jornal", ""):
                        cod_tentativa = sec_cod.replace("dou", "do") # dou1 -> do1
                    
                    if "secao" in tentativa: params["secao"] = cod_tentativa
                    if "jornal" in tentativa: params["jornal"] = cod_tentativa
                    params["data"] = tentativa["data"]

                    try:
                        resp = await client.get(LEITURA_API, params=params)
                        
                        if resp.status_code == 200:
                            arvore = resp.json()
                            if not arvore: continue # JSON vazio
                                
                            print(f"   ✅ SUCESSO na {sec_cod}! (Params: {params})", flush=True)
                            buscar_recursiva(arvore, lista_busca_norm, resultados, sec_cod)
                            sucesso_secao = True
                            break 
                        
                    except json.JSONDecodeError: pass
                    except Exception: pass

                if not sucesso_secao:
                    print(f"   ⚠️ Seção {sec_cod} vazia ou inacessível.", flush=True)

        except Exception as e:
            print(f"❌ [ERRO CRÍTICO]: {e}", flush=True)

    print(f"📊 [FIM] Matérias recuperadas: {len(resultados)}", flush=True)
    return resultados
