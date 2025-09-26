from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

app = FastAPI(title="Robô DOU API (INLABS XML)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # depois restrinja para o domínio do seu frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== CONFIG via variáveis de ambiente ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

DEFAULT_KEYWORDS = [
    "PRONAPA","PCFT","PNM","Comando da Marinha","Fundo Naval",
    "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
    "reforço de dotações","alterações nos limites de movimentação",
    "alterações nos limites de pagamento","créditos suplementares",
    "Transfere recursos entre categorias","alterações de fontes de recursos",
]

class Publicacao(BaseModel):
    date: Optional[str] = None
    section: Optional[str] = None
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    lines = ["Bom dia!","","PTC as seguintes publicações de interesse:"]
    try:
        dt = datetime.fromisoformat(when)
        dd = dt.strftime("%d%b").upper()
    except Exception:
        dd = when
    lines += [f"DOU {dd}:","", "🔰 Seção 1",""]
    if not pubs:
        lines.append("— Sem ocorrências para as palavras-chave informadas —")
        return "\n".join(lines)
    for p in pubs:
        lines.append(f"▶️ {p.organ or 'Órgão'}")
        lines.append("")
        lines.append(f"📌 {p.type or 'Ato/Portaria'}")
        if p.summary: lines.append(p.summary)
        lines.append("")
        lines.append("⚓ Para conhecimento.")
        lines.append("")
    return "\n".join(lines)

def parse_xml_bytes(xml_bytes: bytes, keywords: List[str]) -> List[Publicacao]:
    # parser simples: procura palavras-chave no texto bruto
    text = xml_bytes.decode("utf-8", errors="ignore")
    pubs: List[Publicacao] = []
    if any(k.lower() in text.lower() for k in keywords):
        pubs.append(Publicacao(type="XML", summary=norm(text[:1000]), raw=norm(text)))
    return pubs

async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(status_code=500, detail="Config ausente: defina INLABS_USER e INLABS_PASS.")

    client = httpx.AsyncClient(timeout=60, follow_redirects=True)

    # pre-hit para cookies
    try:
        await client.get(INLABS_BASE)
    except Exception:
        pass

    # login: form usa email/password
    payload = {"email": INLABS_USER, "password": INLABS_PASS}
    r = await client.post(INLABS_LOGIN_URL, data=payload)
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")

    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    """
    Descobre a URL da pasta da data depois do login, lendo links da home autenticada.
    Evita assumir /YYYY-MM-DD (que deu 404 no seu ambiente).
    """
    # 1) Home autenticada
    r = await client.get(INLABS_BASE)
    r.raise_for_status()
    html = r.text

    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a")

    # candidatos: 2025-09-26, 2025_09_26, 20250926
    cand_texts = [date, date.replace("-", "_"), date.replace("-", "")]
    for a in anchors:
        href = (a.get("href") or "").strip()
        txt = (a.get_text() or "").strip()
        hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts):
            return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))

    # 2) Fallback: tentar padrões comuns
    candidates = [
        f"{INLABS_BASE.rstrip('/')}/{date}",
        f"{INLABS_BASE.rstrip('/')}/{date}/",
        f"{INLABS_BASE.rstrip('/')}/{date.replace('-', '_')}",
        f"{INLABS_BASE.rstrip('/')}/{date.replace('-', '_')}/",
    ]
    for u in candidates:
        rr = await client.get(u)
        if rr.status_code == 200 and (".zip" in rr.text.lower() or "<a" in rr.text.lower()):
            return u

    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    url = await resolve_date_url(client, date)
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    """
    Retorna links absolutos de arquivos .zip da listagem,
    priorizando nomes com DO1/DO2/DO3 conforme 'only_sections'.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a")
    links: List[str] = []
    wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}

    for a in anchors:
        href = a.get("href") or ""
        label = (a.get_text() or href).upper()
        if not href.lower().endswith(".zip"):
            continue
        if any(sec in label for sec in wanted) or "DO" in label:
            links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))

    # dedup preservando ordem
    seen = set()
    uniq = []
    for u in links:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq

async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"):
                xml_blobs.append(z.read(name))
    return xml_blobs

@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1", description="Ex.: 'DO1' ou 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None)
):
    keywords = DEFAULT_KEYWORDS if not keywords_json else json.loads(keywords_json)
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]

    client = await inlabs_login_and_get_session()
    try:
        # 1) página da data
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)  # reutiliza a checagem
        # 2) zips
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail="Não encontrei ZIPs da data informada.")
        # 3) baixar + extrair + filtrar
        pubs: List[Publicacao] = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            for blob in extract_xml_from_zip(zb):
                pubs.extend(parse_xml_bytes(blob, keywords))
        # 4) dedup
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (p.type or "") + "||" + (p.summary or "")[:200]
            if key not in seen:
                seen.add(key)
                merged.append(p)
        texto = monta_whatsapp(merged, data)
        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()
