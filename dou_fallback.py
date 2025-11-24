# Nome do arquivo: dou_fallback.py
# Versão: 5.0 (Sessão Persistente + Cookies + Debug Force)

import httpx
import asyncio
import json
import unicodedata
from typing import List, Dict, Any

# Endpoint oficial da Árvore de Leitura
BASE_URL = "https://www.in.gov.br"
LEITURA_API = "https://www.in.gov.br/leitura/-/leitura/dou"

def normalizar_texto(texto: str) -> str:
    if not texto: return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower()

def buscar_recursiva(no: Any, keywords_norm: List[str], resultados: List[Dict], secao: str):
    """ Percorre a árvore JSON procurando títulos compatíveis. """
    if isinstance(no, list):
        for item in no:
            buscar_recursiva(item, keywords_norm, resultados, secao)
        return

    if isinstance(no, dict):
        titulo = no.get("text") or no.get("name") or ""
        url_title = no.get("urlTitle")
        file_id = no.get("fileId")
        
        # Se é uma matéria (tem link)
        if titulo and (url_title or file_id):
            titulo_norm = normalizar_texto(titulo)
            
            # Verifica keywords no título
            for kw in keywords_norm:
                if kw in titulo_norm:
                    # Reconstrói link
                    if url_title:
                        link = f"https://www.in.gov.br/web/dou/-/{url_title}"
                    else:
                        continue 

                    resultados.append({
                        "organ": "DOU (Fallback JSON)",
                        "type": "Matéria",
                        "summary": titulo,
                        "raw": f"{titulo}\nLink: {link}",
                        "relevance_reason": f"Encontrado na edição do dia pelo termo: '{kw}'",
                        "section": secao.upper(),
                        "link": link
                    })
                    break
        
        # Desce para os filhos
        children = no.get("children") or no.get("subordinados")
        if children:
            buscar_recursiva(children, keywords_norm, resultados, secao)

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Orquestrador v5.0: Cria sessão, pega cookies e consulta API.
    """
    # 1. Tratamento da Data
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
        print(f"--- [FALLBACK v5] Data Alvo: {data_pt} ---", flush=True)
    except:
        print("❌ [FALLBACK] Erro no formato da data.", flush=True)
        return []

    # 2. Lista de Keywords (Sua lista estratégica)
    termos_criticos = [
        "marinha", "defesa", "comando", "almirante", "prosub", "amazul",
        "nuclear", "orcamento", "credito", "decreto", "portaria", "lei",
        "aviso", "extrato", "52131", "52000", "suplementar"
    ]
    # Normaliza e unifica
    lista_busca_norm = list(set(termos_criticos + [normalizar_texto(k) for k in keywords]))
    print(f"🔍 Buscando {len(lista_busca_norm)} termos em títulos...", flush=True)

    resultados = []
    secoes = ["do1", "do2"] # Vamos varrer seção 1 e 2 para garantir

    # 3. Inicia Sessão HTTPX (Mantém Cookies entre requisições)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.in.gov.br/leitura",
        "X-Requested-With": "XMLHttpRequest"
    }

    async with httpx.AsyncClient(timeout=45, follow_redirects=True, headers=headers) as client:
        try:
            # PASSO A: Acessar Home para pegar Cookies (CRÍTICO)
            print("🍪 Obtendo cookies de sessão...", flush=True)
            await client.get(BASE_URL)
            
            # PASSO B: Consultar API para cada seção
            for sec in secoes:
                print(f"📡 Baixando árvore da seção {sec}...", flush=True)
                
                params = {"data": data_pt, "secao": sec}
                resp = await client.get(LEITURA_API, params=params)
                
                if resp.status_code != 200:
                    print(f"❌ Erro HTTP {resp.status_code} na seção {sec}", flush=True)
                    continue
                
                try:
                    arvore_json = resp.json()
                    # Debug: verifica se o JSON veio vazio
                    if not arvore_json:
                         print(f"⚠️ JSON vazio para {sec}. (Fim de semana ou data futura?)", flush=True)
                    
                    buscar_recursiva(arvore_json, lista_busca_norm, resultados, sec)
                    
                except json.JSONDecodeError:
                    print(f"❌ Resposta não é JSON válido na seção {sec}.", flush=True)
                    # print(resp.text[:200]) # Descomente se quiser ver o erro HTML
        
        except Exception as e:
            print(f"❌ [EXCEÇÃO GERAL]: {e}", flush=True)

    print(f"✅ [FALLBACK FIM] Total encontrado: {len(resultados)} matérias.", flush=True)
    return resultados
