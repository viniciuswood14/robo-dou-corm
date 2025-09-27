from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

app = FastAPI(title="Robô DOU API (INLABS XML) - v2 Inteligente")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== CONFIG via variáveis de ambiente ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

# ====== NOVAS LISTAS DE PALAVRAS-CHAVE ======
# Filtro 1: Palavras de interesse direto (sempre capturar)
KEYWORDS_DIRECT_INTEREST = [
    "classificação orçamentária", "Defesa", "Força Armanda", "Forças Armandas",
    "militar", "militares", "Comandos da Marinha", "Comando da Marinha",
    "Marinha do Brasil", "Fundo Naval", "Amazônia Azul Tecnologias de Defesa",
    "Caixa de Construções de casas para o pessoal da marinha",
    "Empresa Gerencial de Projetos Navais",
    "Fundo de Desenvolvimento do Ensino Profissional Marítimo"
]

# Filtro 2: Órgãos de interesse orçamentário geral
BUDGET_MONITOR_ORGS = [
    "ministério da fazenda", "ministério do planejamento", "presidência da república",
    "atos do poder executivo"
]

# Filtro 2: Termos orçamentários para monitorar nos órgãos acima
BUDGET_KEYWORDS = [
    "crédito suplementar", "crédito extraordinário", "execução orçamentária",
    "lei orçamentária", "orçamentos fiscal", "reforço de dotações",
    "programação orçamentária e financeira", "altera grupos de natureza de despesa",
    "limites de movimentação", "limites de pagamento", "fontes de recursos"
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
        # Formato DDMMMM (ex: 27SET)
        dd = dt.strftime("%d%b").upper()
    except Exception:
        dd = when
    lines += [f"DOU {dd}:","", "🔰 Seção 1",""]
    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)
    for p in pubs:
        lines.append(f"▶️ {p.organ or 'Órgão'}")
        lines.append("")
        # O campo 'type' agora conterá o tipo e número do ato
        lines.append(f"📌 {p.type or 'Ato/Portaria'}")
        if p.summary: lines.append(p.summary)
        lines.append("")
        lines.append("⚓ Para conhecimento.")
        lines.append("")
    return "\n".join(lines)

def parse_xml_bytes(xml_bytes: bytes, direct_keywords: List[str], budget_orgs: List[str], budget_keywords: List[str]) -> List[Publicacao]:
    """
    Nova função de parsing inteligente:
    1. Lê a estrutura do XML.
    2. Itera sobre cada 'Artigo' (publicação).
    3. Extrai campos específicos: orgao, identifica, ementa.
    4. Aplica a lógica de filtros em camadas.
    """
    pubs: List[Publicacao] = []
    try:
        soup = BeautifulSoup(xml_bytes, 'lxml-xml')
        articles = soup.find_all('Artigo')

        for art in articles:
            organ = norm(art.find('Orgao').get_text() if art.find('Orgao') else "")
            # 'Identifica' geralmente contém o tipo, número e data do ato
            act_type = norm(art.find('Identifica').get_text() if art.find('Identifica') else "")
            # 'Ementa' é o resumo oficial do ato
            summary = norm(art.find('Ementa').get_text() if art.find('Ementa') else "")
            # Texto completo para busca
            full_text = norm(art.get_text())

            # Normaliza para busca case-insensitive
            search_content = (organ + ' ' + act_type + ' ' + summary + ' ' + full_text).lower()
            organ_lower = organ.lower()

            is_relevant = False

            # Filtro 1: Interesse Direto
            if any(kw.lower() in search_content for kw in direct_keywords):
                is_relevant = True

            # Filtro 2: Interesse Orçamentário Amplo
            if not is_relevant:
                is_budget_org = any(org_name.lower() in organ_lower for org_name in budget_orgs)
                if is_budget_org and any(bkw.lower() in search_content for bkw in budget_keywords):
                    is_relevant = True
            
            if is_relevant:
                # Usa a Ementa como resumo, se não houver, usa o início do texto
                final_summary = summary if summary else (full_text[:500] + '...' if len(full_text) > 500 else full_text)
                
                pub = Publicacao(
                    organ=organ if organ else "Órgão não identificado",
                    type=act_type if act_type else "Ato não identificado",
                    summary=final_summary,
                    raw=full_text
                )
                pubs.append(pub)

    except Exception as e:
        # Em caso de falha no parsing, retorna um erro simples para depuração
        pubs.append(Publicacao(type="Erro de Parsing", summary=f"Falha ao processar XML: {str(e)}", raw=xml_bytes.decode("utf-8", errors="ignore")[:1000]))

    return pubs

# As funções de login e download permanecem as mesmas
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
    cand_texts = [date, date.replace("-", "_"), date.replace("-", "")]
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        txt = (a.get_text() or "").strip()
        hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts):
            return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"
    rr = await client.get(fallback_url)
    if rr.status_code == 200:
        return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    url = await resolve_date_url(client, date)
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted):
            links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))

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
    keywords_json: Optional[str] = Form(None) # Mantido para customização via UI
):
    # Se keywords_json for enviado pela UI, ele tem prioridade
    if keywords_json:
        custom_keywords = json.loads(keywords_json)
        direct_keywords = custom_keywords
        budget_orgs = []  # Desativa busca por orçamento se customizar
        budget_keywords = []
    else:
        direct_keywords = KEYWORDS_DIRECT_INTEREST
        budget_orgs = BUDGET_MONITOR_ORGS
        budget_keywords = BUDGET_KEYWORDS

    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]

    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}' na data informada.")
        
        pubs: List[Publicacao] = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            for blob in extract_xml_from_zip(zb):
                pubs.extend(parse_xml_bytes(blob, direct_keywords, budget_orgs, budget_keywords))
        
        # Dedup para evitar publicações idênticas que podem aparecer em XMLs diferentes
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            # Chave de identificação: Órgão + Tipo + Primeiras 100 letras do resumo
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged.append(p)
        
        texto = monta_whatsapp(merged, data)
        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()
