# Nome do arquivo: dou_fallback.py
# Versão: 4.0 (API de Leitura JSON - Método Robusto)

import httpx
import asyncio
import json
import unicodedata
from typing import List, Dict, Any

# Endpoint que retorna a ESTRUTURA COMPLETA do jornal do dia em JSON
LEITURA_URL = "https://www.in.gov.br/leitura/-/leitura/dou"

def normalizar_texto(texto: str) -> str:
    """Remove acentos e coloca em minúsculas para comparação."""
    if not texto: return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower()

def buscar_recursiva(no: Any, keywords_norm: List[str], resultados: List[Dict], secao: str):
    """
    Percorre a árvore JSON do DOU procurando matérias que batem com as keywords.
    """
    # Se for uma lista, itera sobre os itens
    if isinstance(no, list):
        for item in no:
            buscar_recursiva(item, keywords_norm, resultados, secao)
        return

    # Se for um dicionário (nó da árvore)
    if isinstance(no, dict):
        # Verifica se é uma folha (matéria) ou nó (pasta)
        # Matérias geralmente tem 'urlTitle' ou 'id'
        
        titulo = no.get("name") or no.get("text") or ""
        url_title = no.get("urlTitle")
        
        # Se tem título e urlTitle, é uma matéria potencial
        if titulo and url_title:
            titulo_norm = normalizar_texto(titulo)
            
            # Verifica se alguma keyword está no título
            for kw in keywords_norm:
                if kw in titulo_norm:
                    link = f"https://www.in.gov.br/web/dou/-/{url_title}"
                    
                    # Adiciona aos resultados
                    resultados.append({
                        "organ": "DOU (API Leitura)",
                        "type": "Matéria",
                        "summary": titulo,
                        "raw": f"{titulo}\nLink: {link}",
                        "relevance_reason": f"Encontrado na árvore de leitura pelo termo: '{kw}'",
                        "section": secao.upper(),
                        "link": link
                    })
                    break # Já achou uma keyword, não precisa testar as outras
        
        # Continua descendo na árvore (filhos)
        children = no.get("children") or no.get("subordinados")
        if children:
            buscar_recursiva(children, keywords_norm, resultados, secao)


async def buscar_dou_api_leitura(data_pt: str, keywords: List[str], secao: str = "do1") -> List[Dict]:
    """
    Baixa o JSON da árvore do jornal e filtra localmente.
    """
    print(f"[Fallback v4] Baixando estrutura do DOU de {data_pt} ({secao})...")
    
    params = {
        "data": data_pt,
        "secao": secao
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest" # Importante para APIs Liferay
    }

    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        try:
            resp = await client.get(LEITURA_URL, params=params, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ [ERRO HTTP] API Leitura: {resp.status_code}")
                return []

            # Tenta processar o JSON
            try:
                arvore_json = resp.json()
            except json.JSONDecodeError:
                print(f"❌ [ERRO JSON] A resposta não é um JSON válido. Pode ser HTML de erro.")
                return []
            
            # Prepara keywords normalizadas para busca rápida
            kw_norm = [normalizar_texto(k) for k in keywords]
            
            resultados = []
            buscar_recursiva(arvore_json, kw_norm, resultados, secao)
            
            return resultados

        except Exception as e:
            print(f"❌ [EXCEÇÃO] Erro na API Leitura: {e}")
            return []

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista de termos (Reduzida para focar no que aparece em Títulos de matérias na árvore)
    # A árvore geralmente traz o título do ato (Ex: "Portaria nº 123"). 
    # Termos genéricos como "Orçamento" podem não aparecer no título, mas as UGs sim.
    termos_criticos = [
        "Marinha",
        "Defesa",
        "PROSUB",
        "Amazul",
        "Nuclear",
        "Orçamento",
        "Crédito",
        "Decreto",
        "Portaria",
        "Lei"
    ]
    
    # Junta com as keywords do usuário
    lista_busca = list(set(termos_criticos + keywords))
    
    print(f"--- INICIANDO FALLBACK V4 (API JSON) ---")
    
    # Busca apenas na Seção 1 (DO1) que é a principal
    # Se quiser DO2 e DO3, teria que fazer mais requisições
    resultados = await buscar_dou_api_leitura(data_pt, lista_busca, "do1")
    
    print(f"--- FIM DO FALLBACK: {len(resultados)} itens encontrados na árvore ---")
    return resultados
