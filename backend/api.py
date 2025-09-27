from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set
from datetime import datetime
import os, io, zipfile, json, re, unicodedata
from urllib.parse import urljoin
from collections import defaultdict

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

DO1_NAME = "DO1"
MONEY_RE = re.compile(r"R\$\s*[\d\.\,]+")

INCLUDE_TERMS = [
    "classificação orçamentária",
    "defesa",
    "força armada", "forças armadas",
    "militar", "militares",
    "comando da marinha", "comandos da marinha",
    "marinha do brasil",
    "fundo naval",
    "amazul", "amazônia azul tecnologias de defesa",
    "emgepron", "empresa gerencial de projetos navais",
    "caixa de construções de casas para o pessoal da marinha",
    "fundo de desenvolvimento do ensino profissional marítimo",
    # orçamentárias
    "crédito suplementar", "crédito extraordinário",
    "grupo de natureza de despesa", "gnd",
    "modifica fontes", "fonte de recursos",
    "emendas individuais", "emendas de bancada", "emendas de comissão",
    "programação orçamentária e financeira",
    "cronograma de execução mensal de desembolso",
    "relatório resumido da execução orçamentária",
    "ploa", "projeto de lei orçamentária",
    "decreto", "medida provisória", "despacho do presidente", "mensagem n",
]

# ====== MODELS ======
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

# ====== HELPERS ======
_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()

def contains_any(text: str, terms: List[str]) -> bool:
    t = _normalize(text)
    return any(_normalize(term) in t for term in terms)

def is_budgetary(text: str) -> bool:
    keys = [
        "crédito suplementar", "crédito extraordinário",
        "grupo de natureza de despesa", "gnd",
        "modifica fontes", "fonte de recursos",
        "programação orçamentária", "cronograma de execução",
        "relatório resumido da execução orçamentária",
        "ploa", "emendas",
    ]
    t = _normalize(text)
    return any(_normalize(k) in t for k in keys)

def contains_money(text: str) -> bool:
    return bool(MONEY_RE.search(text or ""))

def pretty_org(art_category: str) -> str:
    parts = [p.strip() for p in (art_category or "").split("/") if p.strip()]
    if not parts:
        return "Órgão não identificado"
    return " / ".join(parts[:2]) if len(parts) >= 2 else parts[0]

def extract_title_and_lines(body_html: str) -> tuple[str, List[str]]:
    if not body_html:
        return ("Ato", [])
    soup = BeautifulSoup(body_html, "html.parser")
    ident = ""
    ident_tag = soup.find(class_="identifica")
    if ident_tag:
        ident = ident_tag.get_text(" ", strip=True)
    lines = []
    for p in soup.find_all("p"):
        txt = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if txt:
            lines.append(txt)
    lines = [ln for i, ln in enumerate(lines) if (ln and ln != ident and ln not in lines[:i])]
    return (ident or "Ato", lines[:6])

def impact_note(fulltext: str) -> str:
    t = _normalize(fulltext)
    if "crédito suplementar" in t:
        return "Abre crédito suplementar — verificar UGs/ações da MB."
    if "crédito extraordinário" in t:
        return "Crédito extraordinário — avaliar reflexos para MD/MB."
    if "grupo de natureza de despesa" in t or "gnd" in t:
        return "Troca de GND — conferir ações/AOs da MB."
    if "modifica fontes" in t or "fonte de recursos" in t:
        return "Alteração de fonte — conferir impactos na MB."
    if "relatório resumido da execução orçamentária" in t:
        return "RREO — contexto fiscal; monitorar."
    if "ploa" in t:
        return "PLOA — prazos/procedimentos; monitorar MPO/SOF."
    if "despacho do presidente" in t or "mensagem n" in t:
        return "Ato presidencial — acompanhar tramitação/efeitos."
    if "decreto" in t or "medida provisoria" in t or "medida provisória" in t:
        return "Pode alterar regras/cronogramas; verificar reflexos."
    return "MB para conhecimento."

def score_snippet(organ: str, title: str, body_summary: str) -> int:
    text = " ".join([organ or "", title or "", body_summary or ""])
    score = 0
    n = _normalize(text)
    if "ministerio da defesa" in n or "comando da marinha" in n or "marinha do brasil" in n:
        score += 40
    if "amazul" in n or "amazonia azul tecnologias de defesa" in n:
        score += 35
    if "emgepron" in n or "empresa gerencial de projetos navais" in n:
        score += 35
    if "ministerio do planejamento e orcamento" in n or "secretaria de orcamento federal" in n:
        score += 45
    if "ministerio da fazenda" in n or "secretaria do tesouro nacional" in n:
        score += 35
    if "presidencia da republica" in n:
        score += 25
    if is_budgetary(text):
        score += 25
    if contains_money(text):
        score += 15
    if contains_any(text, INCLUDE_TERMS):
        score += 20
    return score

# ====== PARSER ======
def parse_xml_bytes(xml_bytes: bytes, keywords: Optional[List[str]] = None) -> List[Publicacao]:
    pubs: List[Publicacao] = []
    soup_xml = BeautifulSoup(xml_bytes, "lxml-xml")
    if not soup_xml or not soup_xml.find():
        soup_xml = BeautifulSoup(xml_bytes, "html.parser")

    for art in soup_xml.find_all("article"):
        section = (art.get("pubName") or "").upper()
        if section != DO1_NAME:
            continue

        art_category = art.get("artCategory") or ""
        organ = pretty_org(art_category)

        body_node = art.find("body")
        body_html = ""
        if body_node:
            texto_tag = body_node.find("Texto")
            if texto_tag:
                body_html = texto_tag.decode_contents()
            else:
                body_html = body_node.decode_contents()
        else:
            body_html = art.decode_contents()

        title, lines = extract_title_and_lines(body_html)
        core = []
        for ln in lines:
            if ln and ln != title:
                core.append(ln)
            if len(core) >= 2:
                break
        summary_txt = " ".join(core) if core else ""
        pdf_page = art.get("pdfPage") or ""
        fulltext = " ".join([art_category, title, summary_txt, body_html])

        kw_list = keywords if keywords else INCLUDE_TERMS
        passes = contains_any(fulltext, kw_list) or is_budgetary(fulltext)
        if not passes:
            passes = any(x in _normalize(fulltext) for x in [
                "ministerio da defesa", "comando da marinha", "marinha do brasil", "amazul", "emgepron"
            ])
        if not passes:
            continue

        _type = title
        _summary = summary_txt
        if pdf_page:
            _summary = (_summary + f" 🔗 Página: {pdf_page}").strip()

        pubs.append(Publicacao(
            date=art.get("pubDate"),
            section=section,
            organ=organ,
            type=_type,
            summary=_summary,
            raw=body_html[:5000]
        ))
    return pubs

# ====== WHATSAPP FORMATTER ======
def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    if not pubs:
        return f"Bom dia!\n\nPTC as seguintes publicações de interesse:\nDOU (Seção 1)\n\n— Sem itens relevantes em {when}."

    scored = []
    for p in pubs:
        organ = p.organ or "Órgão não identificado"
        title = p.type or "Ato"
        summary = p.summary or ""
        s = score_snippet(organ, title, summary)
        scored.append((s, p))
    scored.sort(key=lambda x: x[0], reverse=True)

    groups = defaultdict(list)
    for s, p in scored:
        organ = p.organ or "Órgão não identificado"
        title = p.type or "Ato"
        summary = p.summary or ""
        note = impact_note(" ".join([organ, title, summary]))
        icon = "✔️" if s >= 60 else "▶️"
        line = f"{icon} {title}\n{summary}\n⚓ {note}"
        groups[organ].append(line)

    parts = [
        "Bom dia!",
        "",
        "PTC as seguintes publicações de interesse:",
        "DOU (Seção 1)",
        ""
    ]
    for organ, lines in groups.items():
        parts.append(f"🔰 {organ}")
        parts.extend(lines)
        parts.append("")
    return "\n".join(parts).strip()

# ====== INLABS CLIENT ======
async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(status_code=500, detail="Config ausente: defina INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        await client.get(INLABS_BASE)
    except Exception:
        pass
    payload = {"email": INLABS_USER, "password": INLABS_PASS}
    r = await client.post(INLABS_LOGIN_URL, data=payload)
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    r = await client.get(INLABS_BASE)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    anchors = soup.find_all("a")
    cand_texts = [date, date.replace("-", "_"), date.replace("-", "")]
    for a in anchors:
        href = (a.get("href") or "").strip()
        txt = (a.get_text() or "").strip()
        hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts):
            return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
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

# ====== ENDPOINT ======
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1", description="Ex.: 'DO1' ou 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None)
):
    keywords = INCLUDE_TERMS if not keywords_json else json.loads(keywords_json)
    secs = ["DO1"]  # força DO1

    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail="Não encontrei ZIPs da data informada.")
        pubs: List[Publicacao] = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            for blob in extract_xml_from_zip(zb):
                pubs.extend(parse_xml_bytes(blob, keywords))
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

