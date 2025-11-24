# Nome do arquivo: dou_fallback.py
# Versão: 4.0 (API de Leitura JSON - Método Blindado)

import httpx
import asyncio
import json
import unicodedata
from typing import List, Dict, Any

# Endpoint oficial que retorna a estrutura da edição do dia
LEITURA_URL = "https://www.in.gov.br/leitura/-/leitura/dou"

def normalizar_texto(texto: str) -> str:
    """
    Remove acentos e coloca em minúsculas para comparação robusta.
    Ex: "Orçamento" -> "orcamento"
    """
    if not texto: return ""
    return unicodedata.normalize('NFKD', texto).encode('ASCII', 'ignore').decode('ASCII').lower()

def buscar_recursiva(no: Any, keywords_norm: List[str], resultados: List[Dict], secao: str):
    """
    Percorre a árvore JSON do jornal procurando matérias que batem com as keywords.
    """
    # Se for lista, processa cada item
    if isinstance(no, list):
        for item in no:
            buscar_recursiva(item, keywords_norm, resultados, secao)
        return

    # Se for dicionário (um nó da árvore)
    if isinstance(no, dict):
        # Tenta pegar o título da matéria ou da pasta
        titulo = no.get("text") or no.get("name") or ""
        
        # Se tiver 'urlTitle' ou 'id' e for uma folha (não pasta), é uma matéria
        # As vezes a estrutura varia, mas 'urlTitle' é um forte indício de matéria linkável
        url_title = no.get("urlTitle")
        file_id = no.get("fileId") # ID único da matéria
        
        if titulo and (url_title or file_id):
            titulo_norm = normalizar_texto(titulo)
            
            # Verifica se alguma das nossas keywords está no título
            for kw in keywords_norm:
                if kw in titulo_norm:
                    # Reconstrói o link público
                    # Padrão: https://www.in.gov.br/web/dou/-/titulo-da-materia-id
                    # Se não tiver urlTitle, usamos o ID se possível, mas urlTitle é o padrão
                    if url_title:
                        link = f"https://www.in.gov.br/web/dou/-/{url_title}"
                    else:
                        continue 

                    resultados.append({
                        "organ": "DOU (Fallback JSON)",
                        "type": "Matéria",
                        "summary": titulo,
                        # O JSON da árvore não traz o texto completo, só o título.
                        # A IA vai analisar o título.
                        "raw": f"{titulo}\nLink: {link}",
                        "relevance_reason": f"Encontrado na edição do dia pelo termo: '{kw}'",
                        "section": secao.upper(),
                        "link": link
                    })
                    break # Se já achou uma keyword, pula para o próximo nó

        # Continua descendo na árvore (filhos/children)
        # A API do DOU usa 'children' ou 'subordinados' dependendo da versão
        filhos = no.get("children") or no.get("subordinados")
        if filhos:
            buscar_recursiva(filhos, keywords_norm, resultados, secao)


async def buscar_dou_api_leitura(data_pt: str, keywords: List[str], secao: str = "do1") -> List[Dict]:
    """
    Baixa o JSON da árvore do jornal e filtra localmente.
    """
    print(f"[Fallback v4] Baixando árvore do DOU de {data_pt} ({secao})...")
    
    params = {
        "data": data_pt,
        "secao": secao
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest" # Importante para APIs do portal
    }

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        try:
            resp = await client.get(LEITURA_URL, params=params, headers=headers)
            
            if resp.status_code != 200:
                print(f"❌ [ERRO HTTP] API Leitura: {resp.status_code}")
                return []

            # Tenta ler como JSON
            try:
                arvore_json = resp.json()
            except json.JSONDecodeError:
                print("❌ [ERRO] A resposta não é um JSON válido.")
                return []
            
            # Prepara keywords
            kw_norm = [normalizar_texto(k) for k in keywords]
            
            resultados = []
            # A árvore geralmente vem numa lista raiz
            buscar_recursiva(arvore_json, kw_norm, resultados, secao)
            
            return resultados

        except Exception as e:
            print(f"❌ [EXCEÇÃO] Erro na API Leitura: {e}")
            return []

async def executar_fallback(data_iso: str, keywords: List[str]) -> List[Dict]:
    """
    Orquestrador.
    """
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        data_pt = dt.strftime("%d-%m-%Y")
    except:
        return []

    # Lista de termos essenciais para filtrar os TÍTULOS
    termos_criticos = [
        "Marinha",
        "Defesa",
        "Comando",
        "Almirante",
        "PROSUB",
        "Amazul",
        "Nuclear",
        "Orcamento", # Sem acento propositalmente, mas a normalização resolve
        "Credito",
        "Decreto",
        "Portaria",
        "Lei",
        "Aviso",
        "Extrato"
    ]
    
    # Junta com as keywords do usuário
    lista_busca = list(set(termos_criticos + keywords))
    
    print(f"--- INICIANDO FALLBACK V4 (JSON API) ---")
    
    # Busca apenas na Seção 1 (DO1) que é a principal. 
    # Se precisar da Seção 2 (Pessoal), podemos adicionar outra chamada.
    resultados = await buscar_dou_api_leitura(data_pt, lista_busca, "do1")
    
    print(f"--- FIM DO FALLBACK: {len(resultados)} itens encontrados na árvore ---")
    return resultados
    
    print(f"--- FIM DO FALLBACK: {len(resultados)} itens encontrados na árvore ---")
    return resultados
