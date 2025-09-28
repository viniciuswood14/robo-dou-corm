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
# ########## VERSÃO 8.0 - TUDO NO API (MPO/MB integrado) ######
# #############################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v8.0 (API única)")

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

# ====== UGs de interesse (MB) ======
MB_UGS = ("52131","52133","52232","52233","52931","52932")

# ====== FRASES PADRÃO ======
ANNOTATION_POSITIVE = (
    "Há menção específica ou impacto direto identificado para a Marinha do Brasil, "
    "o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional Marítimo nas partes da publicação analisadas."
)
ANNOTATION_MPO_BUDGET = "Ato orçamentário do MPO. Recomenda-se análise manual dos anexos para verificar o impacto na MB/MD."

# ====== PALAVRAS-CHAVE HEURÍSTICA ======
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

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    lines = ["Bom dia!", "", "PTC as seguintes publicações de interesse:"]
    try:
        dt = datetime.fromisoformat(when)
        meses = ["JAN","FEV","MAR","ABR","MAI","JUN","JUL","AGO","SET","OUT","NOV","DEZ"]
        dd = f"{dt.day:02d}{meses[dt.month-1]}"
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
        lines.append(f"⚓ {p.relevance_reason or 'Para conhecimento.'}")
        lines.append("")
    return "\n".join(lines)

def br_money(x: float) -> str:
    # sem casas decimais, separador milhar '.', decimal ','
    s = f"{int(round(x)):,.0f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")

# ====== INLABS SESSION & DOWNLOAD ======
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

# ============================================================
# =========== PARSER DEDICADO MPO/MB (DENTRO DO API) =========
# ============================================================

_HEADER_ABRE = re.compile(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", re.I)
_HEADER_ADEQUA = re.compile(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", re.I)
_HEADER_GENERIC = [
    re.compile(r"(Altera[\s\S]*?\.)", re.I),
    re.compile(r"(Autoriza[\s\S]*?\.)", re.I),
    re.compile(r"(Disp(õ|o)e[\s\S]*?\.)", re.I),
    re.compile(r"(Estabelece[\s\S]*?\.)", re.I),
    re.compile(r"(Fixa[\s\S]*?\.)", re.I),
    re.compile(r"(Prorroga[\s\S]*?\.)", re.I),
]

def _extract_header_hint(text: str) -> str:
    if not text:
        return ""
    m = _HEADER_ABRE.search(text)
    if m: return norm(m.group(1))
    m = _HEADER_ADEQUA.search(text)
    if m: return norm(m.group(1))
    for rx in _HEADER_GENERIC:
        m = rx.search(text)
        if m: return norm(m.group(1))
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    sentences = re.split(r"(?<=\.)\s+", pre)
    for s in sentences:
        s_norm = norm(s)
        if len(s_norm) > 80 and any(x in s_norm.lower() for x in ["orçament", "lme", "limites", "crédito"]):
            return s_norm
    return norm(pre)[:220].rstrip(" ,;")

def _find_portaria_id(art_text: str, identifica_text: str, article_name_attr: str) -> str:
    # Tenta: PORTARIA GM/MPO Nº 330, DE 23 DE ...
    m = re.search(r"(PORTARIA\s+GM/?MPO\s*N[ºo]\s*[^,\n]+)", art_text, flags=re.I)
    if m: 
        core = norm(m.group(1))
        # normaliza para "Nº 330/2025" quando possível
        m2 = re.search(r"N[ºo]\s*([0-9\.]+).*?(\d{4})", core)
        if m2:
            return f"Nº {m2.group(1).replace('.', '')}/{m2.group(2)}"
        return core
    # cai para Identifica ou name
    if identifica_text:
        return identifica_text
    if article_name_attr:
        return article_name_attr
    return "PORTARIA GM/MPO"

def _parse_mpo_mb_totais_from_xml(xml_bytes: bytes) -> Tuple[
    Optional[str], Optional[str], Dict[str, int], Dict[str, int], Optional[str]
]:
    """
    Retorna:
      (portaria_id, header_hint, suplementos_por_ug, cancelamentos_por_ug, organ_category)
    usando 'TOTAL - GERAL' por bloco 'UNIDADE: <UG>'.
    """
    soup = BeautifulSoup(xml_bytes, "lxml-xml")
    art = soup.find("article")
    if not art:
        return None, None, {}, {}, None

    organ_cat = norm(art.get("artCategory", ""))  # ex.: MPO/Gabinete da Ministra
    body = art.find("body")
    if not body:
        return None, None, {}, {}, organ_cat

    identifica = norm(body.find("Identifica").get_text(strip=True) if body.find("Identifica") else "")
    texto = norm(body.get_text("\n", strip=True))
    art_all_text = norm(art.get_text("\n", strip=True))
    name_attr = norm(art.get("name", ""))

    # Só processa MPO/Gabinete da Ministra
    if "planejamento" not in organ_cat.lower() or "gabinete da ministra" not in organ_cat.lower():
        return None, None, {}, {}, organ_cat

    header_hint = _extract_header_hint(texto)
    portaria_id = _find_portaria_id(art_all_text, identifica, name_attr)

    # Vamos percorrer blocos por UNIDADE: <UG>
    # Estratégia: split por "ÓRGÃO:" e "UNIDADE:"
    # Para cada bloco de UNIDADE, ver se contém uma UG MB, capturar "TOTAL - GERAL" e classificar
    supplements: Dict[str, int] = {}
    cancels: Dict[str, int] = {}

    # Mantém apenas a área do ÓRGÃO: 52000 quando existir (para reduzir ruído)
    if "ÓRGÃO: 52000" in texto or "ORGÃO: 52000" in texto or "ÓRGÃO : 52000" in texto:
        region = texto
    else:
        region = texto  # fallback: usa tudo; alguns XMLs podem não trazer o prefixo completo

    # Varre por UNIDADE: <UG ...>
    for m in re.finditer(r"UNIDADE:\s*(\d{5})\s*-\s*[^\n]+", region, flags=re.I):
        ug = m.group(1)
        # Pegamos do match até o próximo "UNIDADE:" ou "ÓRGÃO:" ou fim
        start = m.start()
        next_m = re.search(r"\n(?:ÓRGÃO|ORGÃO|UNIDADE)\s*:", region[m.end():], flags=re.I)
        end = m.end() + next_m.start() if next_m else len(region)
        bloco = region[start:end]

        if ug not in MB_UGS:
            continue

        # Busca "TOTAL - GERAL" (último valor do bloco)
        total_vals = [v for v in re.findall(r"TOTAL\s*-\s*GERAL\s*\n?\s*R?\$?\s*([\d\.\,]+)", bloco, flags=re.I)]
        if not total_vals:
            # alternativa: linhas como "<p>TOTAL - GERAL</p> ... <p>250.000</p>"
            alt = re.findall(r"TOTAL\s*-\s*GERAL[^\d]*([\d\.\,]+)", bloco, flags=re.I)
            total_vals = alt

        if not total_vals:
            continue  # sem total geral, ignora

        # última ocorrência do total no bloco
        val_str = total_vals[-1]
        val_num = int(re.sub(r"[^\d]", "", val_str) or "0")

        # Classificação: se o bloco mencionar "( CANCELAMENTO )" antes do total, trata como cancelamento, senão suplementação
        bloco_before_total = bloco[:bloco.rfind(val_str)] if val_str in bloco else bloco
        is_cancel = bool(re.search(r"\(\s*CANCELAMENTO\s*\)", bloco_before_total, flags=re.I))

        if is_cancel:
            cancels[ug] = cancels.get(ug, 0) + val_num
        else:
            supplements[ug] = supplements.get(ug, 0) + val_num

    return portaria_id, header_hint, supplements, cancels, organ_cat

def _build_mpo_mb_whatsapp_block(portaria_id: str, header_hint: str,
                                 supplements: Dict[str, int], cancels: Dict[str, int]) -> Optional[str]:
    if not portaria_id:
        return None
    lines: List[str] = []
    # Sem "🔰 Seção 1" e sem "📁 anexo"
    lines.append("▶️Ministério do Planejamento e Orçamento/Gabinete da Ministra")
    lines.append("")
    lines.append(f"📌PORTARIA GM/MPO {portaria_id}")
    if header_hint:
        lines.append("")
        lines.append(header_hint)

    # Bloco MB
    lines.append("")
    lines.append("⚓ MB:")
    lines.append("")

    tot_sup = sum(supplements.values()) if supplements else 0
    tot_can = sum(cancels.values()) if cancels else 0

    if tots := tot_sup:
        lines.append(f"Suplementação (total: R$ {br_money(tot_sup)})")
        # Ordena por UG
        for ug in sorted(supplements.keys()):
            lines.append(f"UG {ug} - R$ {br_money(supplements[ug])}")
        lines.append("")
    if totc := tot_can:
        lines.append(f"Cancelamento (total: R$ {br_money(tot_can)})")
        for ug in sorted(cancels.keys()):
            lines.append(f"UG {ug} - R$ {br_money(cancels[ug])}")
        lines.append("")

    saldo = tot_sup - tot_can
    lines.append(f"(Suplementação – Cancelamento) = R$ {br_money(saldo)}")

    return "\n".join(lines).strip()

def _parse_mpo_mb_from_zipbytes(zip_bytes: bytes) -> List[str]:
    """
    Procura portarias MPO/Gabinete da Ministra dentro do ZIP,
    sumariza por UG (TOTAL - GERAL) e retorna blocos prontos de WhatsApp.
    """
    results: List[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if not name.lower().endswith(".xml"):
                continue
            xml_bytes = z.read(name)
            pid, hint, sups, cals, organ = _parse_mpo_mb_totais_from_xml(xml_bytes)
            if pid:
                block = _build_mpo_mb_whatsapp_block(pid, hint or "", sups, cals)
                if block and block.strip():
                    results.append(block)
    # Remove duplicidades de blocos idênticos
    dedup = []
    seen = set()
    for b in results:
        if b not in seen:
            seen.add(b)
            dedup.append(b)
    return dedup

# ============================================================
# ================== PIPELINE HEURÍSTICO (RODAPÉ) ============
# ============================================================

def parse_xml_bytes_heuristic(xml_bytes: bytes) -> List[Publicacao]:
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

            # ===== BLOQUEIO FINO =====
            # Se for MPO/Gabinete da Ministra e o bloco tem ANEXO + referência a MD/UGs MB,
            # então esta portaria já sairá consolidada no bloco MPO/MB → suprimir aqui.
            organ_l = (organ or "").lower()
            if ("planejamento" in organ_l and "gabinete da ministra" in organ_l):
                raw_l = display_text.lower()
                mentions_mb_ug = any(ug in raw_l for ug in MB_UGS)
                mentions_md_orgao = ("órgão: 52000" in raw_l) or ("orgão: 52000" in raw_l) or ("órgão : 52000" in raw_l)
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
                is_mpo = ("planejamento" in organ_l)
                if is_broad or is_mpo:
                    is_relevant = True
                    reason = ANNOTATION_MPO_BUDGET
            
            if is_relevant:
                final_summary = summary if summary else (display_text[:500] + '...' if len(display_text) > 500 else display_text)
                pubs.append(Publicacao(
                    organ=organ if organ else "Órgão não identificado",
                    type=act_type if act_type else "Ato não identificado",
                    summary=final_summary,
                    raw=display_text,
                    relevance_reason=reason
                ))

    except Exception as e:
        pubs.append(Publicacao(
            type="Erro de Parsing",
            summary=f"Falha ao processar XML: {str(e)}",
            raw=xml_bytes.decode("utf-8", errors="ignore")[:1000]
        ))
    return pubs

# ============================================================
# ========================= ENDPOINT =========================
# ============================================================

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

            # 1) Parser MPO/MB (TOTAL-GERAL por UG)
            try:
                mpo_blocks.extend(_parse_mpo_mb_from_zipbytes(zb))
            except Exception:
                # não interrompe o fluxo
                pass

            # 2) Heurístico geral (rodapé)
            for blob in extract_xml_from_zip(zb):
                pubs.extend(parse_xml_bytes_heuristic(blob))
        
        # Anti-duplicação do rodapé
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged.append(p)

        # 1º: "Bom dia!"
        texto = monta_whatsapp(merged, data)
        # 2º: MPO/MB, sem seção e sem anexo
        if mpo_blocks:
            texto = texto + "\n\n" + "\n\n".join([b for b in mpo_blocks if b.strip()])

        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()

