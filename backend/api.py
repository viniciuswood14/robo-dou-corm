from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# #############################################################
# ########## VERSÃO DE DIAGNÓSTICO DO ROBÔ DOU API ##########
# #############################################################

app = FastAPI(title="Robô DOU API - MODO DIAGNÓSTICO")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp_diagnostico(pubs: List[Publicacao], when: str, xml_count: int, zip_links: List[str]) -> str:
    lines = [f"===== INÍCIO DO RELATÓRIO DE DIAGNÓSTICO (Data: {when}) ====="]
    lines.append(f"\nLinks de ZIP encontrados: {len(zip_links)}")
    for link in zip_links:
        lines.append(f"- {link}")
    
    lines.append(f"\nTotal de arquivos XML extraídos: {xml_count}")

    if not pubs and xml_count > 0:
        lines.append("\nNENHUMA TAG DE ARTIGO FOI ENCONTRADA nos XMLs.")
        lines.append("Isso significa que os nomes de tag <Artigo> ou <Materia> estão incorretos.")
    elif not pubs:
         lines.append("\nNENHUMA PUBLICAÇÃO ENCONTRADA.")
    else:
        lines.append(f"\nTotal de publicações (Artigos/Materias) encontradas: {len(pubs)}")
        lines.append("--------------------------------------------------")
        for i, p in enumerate(pubs):
            lines.append(f"\nPUBLICAÇÃO #{i+1}:")
            lines.append(f"Órgão Extraído: {p.organ}")
            lines.append(f"Tags Encontradas: {p.type}")
            lines.append(f"Conteúdo XML Bruto (amostra):\n{p.summary}")
            lines.append("--------------------------------------------------")

    lines.append("\n===== FIM DO RELATÓRIO DE DIAGNÓSTICO =====")
    return "\n".join(lines)


def parse_xml_bytes_diagnostico(xml_bytes: bytes) -> List[Publicacao]:
    """
    Esta função ignora todos os filtros e tenta extrair a estrutura de qualquer artigo que encontrar.
    """
    pubs: List[Publicacao] = []
    try:
        soup = BeautifulSoup(xml_bytes, 'lxml-xml')
        # Tenta encontrar artigos com vários nomes possíveis, ignorando maiúsculas/minúsculas
        possible_tags = re.compile(r'^(Artigo|Materia)$', re.I)
        articles = soup.find_all(possible_tags)

        for art in articles:
            # Pega o nome da tag que foi encontrada (ex: 'artigo', 'Materia')
            tag_name_found = art.name

            # Tenta extrair as tags internas
            found_tags = [tag_name_found]
            organ_tag = art.find('Orgao')
            if organ_tag: found_tags.append('Orgao')
            
            identifica_tag = art.find('Identifica')
            if identifica_tag: found_tags.append('Identifica')
            
            ementa_tag = art.find('Ementa')
            if ementa_tag: found_tags.append('Ementa')
            
            texto_tag = art.find('Texto')
            if texto_tag: found_tags.append('Texto')

            organ = norm(organ_tag.get_text()) if organ_tag else "NÃO ENCONTRADO"
            
            # Mostra o conteúdo XML bruto do artigo para análise
            raw_content_sample = str(art)[:800] # Pega os primeiros 800 caracteres do XML

            pub = Publicacao(
                organ=organ,
                type=f"Tags: {', '.join(found_tags)}",
                summary=raw_content_sample
            )
            pubs.append(pub)

    except Exception as e:
        pubs.append(Publicacao(organ="ERRO CRÍTICO NO PARSING", type=str(e), summary=xml_bytes.decode('utf-8', errors='ignore')[:500]))
    return pubs

# Funções de login e download permanecem as mesmas
async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    # ... (código inalterado)
    if not INLABS_USER or not INLABS_PASS: raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400: await client.aclose(); raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client
async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado)
    r = await client.get(INLABS_BASE); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser"); cand_texts = [date, date.replace("-", "_"), date.replace("-", "")];
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip(); txt = (a.get_text() or "").strip(); hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts): return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"; rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")
async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado)
    url = await resolve_date_url(client, date); r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text
def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    # ... (código inalterado)
    soup = BeautifulSoup(html, "html.parser"); links: List[str] = []; wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted): links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))
async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    # ... (código inalterado)
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content
def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    # ... (código inalterado)
    xml_blobs: List[bytes] = [];
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"): xml_blobs.append(z.read(name))
    return xml_blobs


@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1", description="Ex.: 'DO1' ou 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None)
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}' na data informada.")
        
        pubs: List[Publicacao] = []
        total_xml_count = 0
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            xml_blobs = extract_xml_from_zip(zb)
            total_xml_count += len(xml_blobs)
            for blob in xml_blobs:
                pubs.extend(parse_xml_bytes_diagnostico(blob))
        
        texto = monta_whatsapp_diagnostico(pubs, data, total_xml_count, zip_links)
        # O Pydantic pode reclamar aqui, mas o importante é ver o texto no frontend
        return ProcessResponse(date=data, count=len(pubs), publications=pubs, whatsapp_text=texto)
    finally:
        await client.aclose()
