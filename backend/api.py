from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set, Tuple, Dict
from datetime import datetime
import os, io, zipfile, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# #############################################################
# ########## VERSÃO 8.0 - TUDO NO API (MPO/MB inline) #########
# #############################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v8.0 API-only")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====== CONFIG ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

# ====== UG ALVO (MB/MD) ======
MB_UGS_DEFAULT: Tuple[str, ...] = (
    "52131",  # Comando da Marinha
    "52133",  # Secretaria da Comissão Interministerial para os Recursos do Mar
    "52232",  # CCCPM
    "52233",  # AMAZUL
    "52931",  # Fundo Naval
    "52932",  # Fundo de Desenvolvimento do Ensino Profissional Marítimo
)

# ====== FRASES PADRÃO / KEYWORDS (heurístico) ======
ANNOTATION_POSITIVE = (
    "Há menção específica ou impacto direto identificado para a Marinha do Brasil, "
    "o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional Marítimo nas partes da publicação analisadas."
)
ANNOTATION_MPO_BUDGET = "Ato orçamentário do MPO. Recomenda-se análise manual dos anexos para verificar o impacto na MB/MD."

KEYWORDS_DIRECT_INTEREST = [
    "ministério da defesa", "força armanda", "forças armandas", "militar", "militares",
    "comandos da marinha", "comando da marinha", "marinha do brasil", "fundo naval",
    "amazônia azul tecnologias de defesa", "caixa de construções de casas para o pessoal da marinha",
    "empresa gerencial de projetos navais", "fundo de desenvolvimento do ensino profissional marítimo",
    "programa nuclear brasileiro"
]
BUDGET_KEYWORDS = [
    "crédito suplementar", "crédito extraordinário", "execução orçamentária",
    "lei orçamentária", "orçamentos fiscal", "reforço de dotações",
    "programação orçamentária e financeira", "altera grupos de natureza de despesa",
    "limites de movimentação", "limites de pagamento", "fontes de recursos",
    "movimentação e empenho", "classificação orçamentária", "gestão fiscal"
]
BROAD_IMPACT_KEYWORDS = [
    "diversos órgãos", "diversos orgaos", "vários órgãos", "varios orgaos",
    "diversos ministérios", "diversos ministerios"
]

# ====== MODELOS ======
class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

# ====== UTILS ======
_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return _ws.sub(" ", s).strip()

def month_pt(dt: datetime) -> str:
    meses = ["JAN","FEV","MAR","ABR","MAI","JUN","JUL","AGO","SET","OUT","NOV","DEZ"]
    return f"{dt.day:02d}{meses[dt.month-1]}"

def money_br(n: float) -> str:
    # R$ 1.234.567 -> usamos separador de milhar com ponto e sem casas
    s = f"{int(round(n)):,}".replace(",", ".")
    return f"R$ {s}"

# ====== LOGIN/CRAWL INLABS ======
async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        await client.get(INLABS_BASE)
    except Exception:
        pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
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

# ====== MPO/MB INLINE PARSER (TOTAL-GERAL por UG) ======
TOTAL_GERAL_RE = re.compile(r"TOTAL\s*-\s*GERAL\s*</p>\s*</td>\s*<td[^>]*>\s*<p>\s*([\d\.]+)\s*</p>", re.I)
UNIDADE_RE = re.compile(r"UNIDADE:\s*(\d{5})\s*-\s*(.*?)</p>", re.I)
ORGAO_MD_FLAG = re.compile(r"ÓRGÃO:\s*52000", re.I)

def _extract_header_hint(text: str) -> str:
    if not text:
        return ""
    # 1) Frase clássica de "Abre aos Orçamentos ... vigente."
    m = re.search(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", text, flags=re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # 2) "Adequa ... alterações posteriores."
    m = re.search(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", text, flags=re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # 3) Outras aberturas comuns
    for pat in [
        r"(Altera[\s\S]*?\.)",
        r"(Autoriza[\s\S]*?\.)",
        r"(Disp(õ|o)e[\s\S]*?\.)",
        r"(Estabelece[\s\S]*?\.)",
        r"(Fixa[\s\S]*?\.)",
        r"(Prorroga[\s\S]*?\.)",
    ]:
        m = re.search(pat, text, flags=re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    # 4) Fallback: primeira sentença longa antes de "ANEXO I"
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    sentences = re.split(r"(?<=\.)\s+", pre)
    for s in sentences:
        s_norm = re.sub(r"\s+", " ", s).strip()
        if len(s_norm) > 80 and any(x in s_norm.lower() for x in ["orçament", "lme", "limites", "crédito"]):
            return s_norm
    return pre.strip()[:220].rstrip(" ,;")

def _parse_mpo_mb_from_xml(xml_bytes: bytes, mb_ugs: Tuple[str, ...]) -> Optional[Dict]:
    """
    Procura, dentro do XML da Portaria GM/MPO, os blocos que tenham:
      - ÓRGÃO: 52000 (MD) e/ou UGs MB (UNIDADE: <UG>)
      - Linhas 'TOTAL - GERAL' para somar valores
    Retorna dict com: portaria_id, header_hint, lista de (UG, +valor / -valor), etc.
    """
    try:
        text = xml_bytes.decode("utf-8", errors="ignore")
    except Exception:
        return None

    # precisa mencionar MD (ÓRGÃO 52000) OU alguma das UGs MB
    if not (ORGAO_MD_FLAG.search(text) or any(ug in text for ug in mb_ugs)):
        return None

    # extrai header (id) a partir do <article ... name="Portaria GM.MPO nA 330.2025 e an"...>
    # e também um hint legível
    portaria_id = "Nº /ANO"
    m_id = re.search(r'name="Portaria\s+GM\.?/?MPO\s+n[ºoA]\s*([^\"]+)"', text, flags=re.I)
    if m_id:
        # normalizar "330.2025 e an" -> "Nº 330/2025"
        chunk = m_id.group(1)
        # pega os dois primeiros tokens número.ano
        m_na = re.search(r"(\d{1,4})[^\d]+(20\d{2})", chunk)
        if m_na:
            portaria_id = f"Nº {m_na.group(1)}/{m_na.group(2)}"

    # header hint
    # o corpo fica entre <body> ... </body>
    body_m = re.search(r"<body>([\s\S]*?)</body>", text, flags=re.I)
    body_text = BeautifulSoup(body_m.group(1), "lxml").get_text(" ", strip=True) if body_m else text
    header_hint = _extract_header_hint(body_text)

    # percorre blocos por UNIDADE e captura TOTAL - GERAL
    # para cada UNIDADE, somamos o sinal: em "Crédito Suplementar" (+), em "Cancelamento" (-)
    # Heurística: no XML, a palavra "Crédito Suplementar" aparece antes da tabela do bloco.
    entries: List[Tuple[str, float]] = []

    # Split aproximado por "UNIDADE:" (mantendo conteúdo para trás para achar se é Suplementar/Cancelamento)
    for unit_match in re.finditer(r"(UNIDADE:\s*\d{5}[\s\S]*?)(?=UNIDADE:|\Z)", text, flags=re.I):
        block = unit_match.group(1)
        # UNIDADE + nome
        ug_m = UNIDADE_RE.search(block)
        if not ug_m:
            continue
        ug = ug_m.group(1)

        if ug not in mb_ugs:
            continue  # só nos interessam as UGs MB

        # Determinar se este subbloco é Suplementar (+) ou Cancelamento (-)
        # Observação: na amostra, "ANEXO II ... Crédito Suplementar" + "PROGRAMA DE TRABALHO ( CANCELAMENTO )"
        # → Em MPO, "PROGRAMA DE TRABALHO ( CANCELAMENTO )" indica que os valores listados são de cancelamento.
        sinal = 0
        if re.search(r"PROGRAMA\s+DE\s+TRABALHO\s*\(\s*CANCELAMENTO\s*\)", block, flags=re.I):
            sinal = -1
        else:
            # Se não diz "CANCELAMENTO", assume-se que é aumento (Suplementar)
            if re.search(r"Cr[eê]dito\s+Suplementar", block, flags=re.I):
                sinal = +1
            else:
                # fallback: se não achou nada, pula
                continue

        # TOTAL - GERAL desse bloco/UG
        tg = TOTAL_GERAL_RE.search(block)
        if not tg:
            continue
        valor_str = tg.group(1)  # "985.066" etc
        try:
            valor = float(valor_str.replace(".", ""))
        except Exception:
            continue

        entries.append((ug, sinal * valor))

    if not entries:
        return None

    # Agregar por UG
    agg: Dict[str, float] = {}
    for ug, val in entries:
        agg[ug] = agg.get(ug, 0.0) + val

    # Quebrar entre suplementação/cancelamento para exibição
    suplementacoes: List[Tuple[str, float]] = []
    cancelamentos: List[Tuple[str, float]] = []
    for ug, total in agg.items():
        if total > 0:
            suplementacoes.append((ug, total))
        elif total < 0:
            cancelamentos.append((ug, abs(total)))

    saldo = sum(v for _, v in suplementacoes) - sum(v for _, v in cancelamentos)

    return {
        "portaria_id": portaria_id,
        "header_hint": header_hint,
        "suplementacoes": sorted(suplementacoes, key=lambda x: x[0]),
        "cancelamentos": sorted(cancelamentos, key=lambda x: x[0]),
        "saldo": saldo
    }

def _render_mpo_mb_block(data: Dict) -> str:
    """Renderiza o bloco WhatsApp da portaria MPO/MB **sem 'Seção 1' e sem 'anexo'**."""
    lines: List[str] = []
    lines.append("▶️Ministério do Planejamento e Orçamento/Gabinete da Ministra")
    lines.append("")
    lines.append(f"📌PORTARIA GM/MPO {data['portaria_id']}")
    lines.append("")
    if data.get("header_hint"):
        lines.append(data["header_hint"])
        lines.append("")
    lines.append("⚓ MB:")
    lines.append("")
    sups = data.get("suplementacoes", [])
    cals = data.get("cancelamentos", [])

    if sups:
        tot_sup = sum(v for _, v in sups)
        lines.append(f"Suplementação (total: {money_br(tot_sup)})")
        for ug, val in sups:
            lines.append(f"UG {ug} - {money_br(val)}")
        lines.append("")
    if cals:
        tot_cal = sum(v for _, v in cals)
        lines.append(f"Cancelamento (total: {money_br(tot_cal)})")
        for ug, val in cals:
            lines.append(f"UG {ug} - {money_br(val)}")
        lines.append("")

    saldo = data.get("saldo", 0.0)
    lines.append(f"(Suplementação – Cancelamento) = {money_br(saldo)}")
    return "\n".join(lines).strip()

# ====== HEURÍSTICO DE OUTRAS PUBLICAÇÕES (não-MPO consolidado) ======
def parse_xml_bytes_heuristico(xml_bytes: bytes) -> List[Publicacao]:
    pubs: List[Publicacao] = []
    try:
        soup = BeautifulSoup(xml_bytes, 'lxml-xml')
        articles = soup.find_all('article')

        for art in articles:
            organ = norm(art.get('artCategory', ''))
            body = art.find('body')
            if not body:
                continue

            act_type = norm(body.find('Identifica').get_text(strip=True) if body.find('Identifica') else "")
            if not act_type:
                continue

            summary = norm(body.find('Ementa').get_text(strip=True) if body.find('Ementa') else "")
            search_content = norm(art.get_text(strip=True)).lower()
            display_text = norm(body.get_text(strip=True))

            # BLOQUEIO FINO: não duplicar portarias MPO com ANEXO + MD/UGs MB
            organ_l = (organ or "").lower()
            if ("ministério do planejamento e orçamento" in organ_l and "gabinete da ministra" in organ_l):
                raw_l = display_text.lower()
                mb_ugs = MB_UGS_DEFAULT
                mentions_mb_ug = any(ug in raw_l for ug in mb_ugs)
                mentions_md_orgao = ("órgão: 52000" in raw_l) or ("orgão: 52000" in raw_l) or ("orgão : 52000" in raw_l)
                has_anexo = "anexo" in raw_l
                if has_anexo and (mentions_mb_ug or mentions_md_orgao):
                    continue

            if not summary:
                match = re.search(r'EMENTA:(.*?)(Vistos|ACORDAM)', display_text, re.DOTALL | re.I)
                if match:
                    summary = norm(match.group(1))

            is_relevant = False
            reason = None

            if any(kw in search_content for kw in KEYWORDS_DIRECT_INTEREST):
                is_relevant = True
                reason = ANNOTATION_POSITIVE
            elif any(bkw in search_content for bkw in BUDGET_KEYWORDS):
                is_broad = any(bikw in search_content for bikw in BROAD_IMPACT_KEYWORDS)
                is_mpo = "ministério do planejamento e orçamento" in organ_l
                if is_broad or is_mpo:
                    is_relevant = True
                    reason = ANNOTATION_MPO_BUDGET
            
            if is_relevant:
                final_summary = summary if summary else (display_text[:500] + '...' if len(display_text) > 500 else display_text)
                pubs.append(Publicacao(
                    organ=organ if organ else "Órgão não identificado",
                    type=act_type if act_type else "Ato/Portaria",
                    summary=final_summary,
                    raw=display_text,
                    relevance_reason=reason
                ))

    except Exception as e:
        pubs.append(Publicacao(type="Erro de Parsing", summary=f"Falha ao processar XML: {str(e)}", raw=xml_bytes.decode("utf-8", errors="ignore")[:1000]))
    return pubs

# ====== WHATSAPP HEADER ======
def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    lines = ["Bom dia!", "", "PTC as seguintes publicações de interesse:"]
    try:
        dt = datetime.fromisoformat(when)
        dd = month_pt(dt)
    except Exception:
        dd = when
    lines += [f"DOU {dd}:", "", "🔰 Seção 1", ""]
    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)
    for p in pubs:
        lines.append(f"▶️ {p.organ or 'Órgão'}")
        lines.append(f"📌 {p.type or 'Ato/Portaria'}")
        if p.summary:
            lines.append(p.summary)
        if p.relevance_reason:
            lines.append(f"⚓ {p.relevance_reason}")
        else:
            lines.append("⚓ Para conhecimento.")
        lines.append("")
    return "\n".join(lines).strip()

# ====== ENDPOINT PRINCIPAL ======
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1", description="Ex.: 'DO1' ou 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None)
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    if keywords_json:
        raise HTTPException(status_code=400, detail="Customização de keywords desativada. Deixe em branco.")
    
    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.")

        pubs: List[Publicacao] = []
        mpo_blocks: List[str] = []

        for zurl in zip_links:
            zb = await download_zip(client, zurl)

            # 1) Consolidado MPO/MB inline (a partir dos XMLs dentro do ZIP)
            for blob in extract_xml_from_zip(zb):
                mpo_data = _parse_mpo_mb_from_xml(blob, MB_UGS_DEFAULT)
                if mpo_data:
                    mpo_blocks.append(_render_mpo_mb_block(mpo_data))

            # 2) Pipeline heurístico (demais publicações de interesse)
            for blob in extract_xml_from_zip(zb):
                pubs.extend(parse_xml_bytes_heuristico(blob))
        
        # Anti-duplicação simples
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:120]
            if key not in seen:
                seen.add(key)
                merged.append(p)

        # 1º: "Bom dia!"
        texto = monta_whatsapp(merged, data)
        # 2º: Blocos MPO/MB (sem 'Seção 1' e sem 'anexo'), colocados DEPOIS
        if mpo_blocks:
            texto = texto + "\n\n" + "\n\n".join([b for b in mpo_blocks if b.strip()])

        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()
