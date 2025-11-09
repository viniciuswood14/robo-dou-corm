# Nome do arquivo: google_search.py

import httpx
import os
from typing import List, Dict, Optional

# --------------------------------------------------------------------------
# ATENÇÃO: Configuração necessária
# --------------------------------------------------------------------------
# 1. Obtenha sua API Key:
#    - Vá para: https://console.cloud.google.com/apis/credentials
#    - Crie ou selecione um projeto.
#    - Clique em "+ CREATE CREDENTIALS" -> "API key".
#    - Copie a chave e coloque na sua variável de ambiente "GOOGLE_API_KEY".
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

# 2. Obtenha seu Search Engine ID (CX):
#    - Vá para: https://cse.google.com/cse/all
#    - Clique em "Add" (Adicionar).
#    - Em "Sites to search", coloque: *.valor.globo.com/*
#    - Dê um nome (ex: "RoboValor") e crie.
#    - Na tela de "Edit search engine", copie o "Search engine ID" (CX).
#    - Coloque na sua variável de ambiente "GOOGLE_CX_ID".
GOOGLE_CX_ID = os.environ.get("GOOGLE_CX_ID")
# --------------------------------------------------------------------------


SEARCH_URL = "https://www.googleapis.com/customsearch/v1"

class SearchResult(dict):
    """Helper para facilitar acesso aos campos do resultado"""
    @property
    def title(self) -> str:
        return self.get("title", "")
    
    @property
    def link(self) -> str:
        return self.get("link", "")

    @property
    def snippet(self) -> str:
        return self.get("snippet", "").replace("\n", " ")

async def perform_google_search(query: str, after_date: str) -> List[SearchResult]:
    """
    Busca no Google CSE por uma query, filtrando por data.
    """
    if not GOOGLE_API_KEY or not GOOGLE_CX_ID:
        print("Erro: GOOGLE_API_KEY ou GOOGLE_CX_ID não configurados.")
        return []

    # A API do Google usa o formato 'dateRestrict' d[N] para "últimos N dias".
    # Vamos usar 'sort' que permite 'date:r:YYYYMMDD:YYYYMMDD'
    # Mas uma forma mais simples é usar a query 'after:YYYY-MM-DD'
    
    full_query = f"{query} site:valor.globo.com after:{after_date}"
    
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": full_query,
        "num": 10 # Limita a 10 resultados
    }

    results = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(SEARCH_URL, params=params, timeout=20)
            
            if response.status_code != 200:
                print(f"Erro na API do Google: {response.status_code} - {response.text}")
                return []

            data = response.json()
            items = data.get("items", [])
            
            for item in items:
                results.append(SearchResult(item))
                
            return results
            
    except Exception as e:
        print(f"Exceção ao buscar no Google: {e}")
        return []
