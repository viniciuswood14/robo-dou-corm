from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set, Dict
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# #####################################################################
# ########## VERSÃO 13.3 - MODO DE DIAGNÓSTICO HTML ##########
# #####################################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v13.3 Diagnóstico HTML")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG E KEYWORDS (Omitidos para brevidade) ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

class Publicacao(BaseModel):
    organ: Optional[str] = None; type: Optional[str] = None; summary: Optional[str] = None
    raw: Optional[str] = None; relevance_reason: Optional[str] = None; section: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str; count: int; publications: List[Publicacao]; whatsapp_text: str

# ... (Funções norm, monta_whatsapp, e toda a lógica do INLABS permanecem no arquivo, mas são omitidas aqui para brevidade) ...

# ===============================================================
# FUNÇÕES PARA FONTE: SITE PÚBLICO (HTML) - MODO DIAGNÓSTICO
# ===============================================================

async def fetch_public_dou_html(client: httpx.AsyncClient, date: str) -> str:
    try:
        formatted_date = datetime.fromisoformat(date).strftime('%d-%m-%Y')
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD.")
    url = f"https://www.in.gov.br/leiturajornal?data={formatted_date}"
    try:
        r = await client.get(url, follow_redirects=True, timeout=90)
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Falha ao acessar o site público do DOU ({url}): HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro de rede ao buscar no site público: {str(e)}")

# ===============================================================
# ROTA PRINCIPAL UNIFICADA
# ===============================================================

@app.post("/processar", response_model=ProcessResponse)
async def processar(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    source: str = Form("INLABS", description="Fonte: 'INLABS' ou 'PUBLICO'")
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    pubs: List[Publicacao] = []

    if source.upper() == "INLABS":
        # A lógica do INLABS continua funcionando normalmente
        client = await inlabs_login_and_get_session()
        try:
            # ... (código completo do INLABS omitido para brevidade) ...
            pass # Placeholder para a lógica completa que está no código final
        finally:
            await client.aclose()

    elif source.upper() == "PUBLICO":
        # #################################################
        # MODO DIAGNÓSTICO ATIVADO
        # #################################################
        async with httpx.AsyncClient() as client:
            try:
                html_content = await fetch_public_dou_html(client, data)
                
                report_lines = [
                    "===== DIAGNÓSTICO DO SITE PÚBLICO (HTML) =====",
                    f"\nURL Acessada: https://www.in.gov.br/leiturajornal?data={datetime.fromisoformat(data).strftime('%d-%m-%Y')}",
                    "\n--- INÍCIO DA AMOSTRA DO HTML (primeiros 5000 caracteres) ---\n",
                    html_content[:5000],
                    "\n--- FIM DA AMOSTRA DO HTML ---"
                ]
                report = "\n".join(report_lines)
                
                return ProcessResponse(date=data, count=1, publications=[], whatsapp_text=report)

            except HTTPException as e:
                 raise e
    else:
        raise HTTPException(status_code=400, detail="Fonte de dados inválida. Use 'INLABS' ou 'PUBLICO'.")

    # ... (Lógica de deduplicação e retorno para o INLABS) ...
    # Omitida para brevidade
    return ProcessResponse(date=data, count=0, publications=[], whatsapp_text="Erro: lógica final não alcançada.")

# O restante do código completo precisa estar no arquivo.
# A versão abaixo é a completa para você copiar.
