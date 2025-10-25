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
# ########## VERSÃO 12.4 - CONFIG EXTERNA E KEYWORDS ##########
# #####################################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v12.4 Config Externa")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG E KEYWORDS (CARREGADOS DO config.json) ======
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    raise RuntimeError("Erro: Arquivo 'config.json' não encontrado.")
except json.JSONDecodeError:
    raise RuntimeError("Erro: Falha ao decodificar 'config.json'. Verifique a sintaxe.")

INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", config.get("INLABS_LOGIN_URL", f"{INLABS_BASE}/login"))
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

# Carrega constantes do JSON
TEMPLATE_LME = config.get("TEMPLATE_LME", "")
TEMPLATE_FONTE = config.get("TEMPLATE_FONTE", "")
TEMPLATE_CREDITO = config.get("TEMPLATE_CREDITO", "")
ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)
# ==========================================================


class Publicacao(BaseModel):
    organ: Optional[str] = None; type: Optional[str] = None; summary: Optional[str] = None
    raw: Optional[str] = None; relevance_reason: Optional[str] = None; section: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str; count: int; publications: List[Publicacao]; whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when); dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception: dd = when
    lines = ["Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]
    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU";
        if sec not in pubs_by_section: pubs_by_section[sec] = []
        pubs_by_section[sec].append(p)
    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —"); return "\n".join(lines)
    for section_name in sorted(pubs_by_section.keys()):
        if not pubs_by_section[section_name]: continue
        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}"); lines.append("")
        for p in pubs_by_section[section_name]:
            lines.append(f"▶️ {p.organ or 'Órgão'}"); lines.append(f"📌 {p.type or 'Ato/Portaria'}")
            if p.summary: lines.append(p.summary)
            if p.relevance_reason and '\n' in p.relevance_reason:
                lines.append(f"⚓\n{p.relevance_reason}")
            elif p.relevance_reason:
                lines.append(f"⚓ {p.relevance_reason}")
            else:
                lines.append("⚓ Para conhecimento.")
            lines.append("")
    return "\n".join(lines)

def parse_gnd_change_table(full_text_content: str) -> str:
    soup = BeautifulSoup(full_text_content, 'lxml-xml')
    results = {'acrescimo': [], 'reducao': []}
    current_unidade = None
    current_operation = None

    # Itera por todas as tabelas em todos os anexos
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cols = row.find_all('td')
            # Extrai o texto limpo de todas as colunas da linha
            row_text_cells = [norm(c.get_text()) for c in cols]
            row_full_text = " ".join(row_text_cells)

            # --- Lógica de Estado (Cabeçalhos) ---
            # Um cabeçalho de 'UNIDADE' ou 'PROGRAMA' reseta o estado
            if "UNIDADE:" in row_full_text:
                current_unidade = row_full_text.replace("UNIDADE:", "").strip()
                continue # Pula para a próxima linha
            
            if "PROGRAMA DE TRABALHO" in row_full_text:
                if "ACRÉSCIMO" in row_full_text.upper():
                    current_operation = "acrescimo"
                elif "REDUÇÃO" in row_full_text.upper() or "CANCELAMENTO" in row_full_text.upper():
                    current_operation = "reducao"
                else:
                    current_operation = None # Operação desconhecida
                continue # Pula para a próxima linha

            # Ignora linhas que não são de dados (cabeçalhos de tabela, etc.)
            if len(cols) != 10 or "PROGRAMÁTICA" in row_full_text.upper():
                continue

            # --- Lógica de Extração (Linhas de Dados) ---
            # Só extrai se tivermos uma unidade e operação válidas E se a unidade for de interesse
            if current_unidade and current_operation and any(tag in current_unidade for tag in MPO_NAVY_TAGS.keys()):
                try:
                    ao, desc, _, _, gnd, _, _, _, _, valor = row_text_cells
                    if not valor: continue # Pula linhas sem valor
                    
                    clean_gnd = gnd.replace('-','').replace('ODC','').replace('INV','')
                    line = f"- AO {ao} - {desc} | GND: {clean_gnd} | Valor: {valor}"
                    results[current_operation].append((current_unidade, line))
                except (IndexError, ValueError):
                    continue
    
    if not results['acrescimo'] and not results['reducao']:
        return "Ato de Alteração de GND com impacto na Defesa/Marinha. Recomenda-se análise manual dos anexos."

    output_lines = ["Ato de Alteração de GND com impacto na Defesa/Marinha. Dados extraídos dos anexos:"]
    
    if results['acrescimo']:
        output_lines.append("\n**-- ACRÉSCIMOS (Suplementação) --**")
        last_unidade = None
        for unidade, line in sorted(results['acrescimo']):
            if unidade != last_unidade:
                # Mostra o nome bonito da unidade
                unidade_code = unidade.split(' ')[0]
                output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*") 
                last_unidade = unidade
            output_lines.append(line)

    if results['reducao']:
        output_lines.append("\n**-- REDUÇÕES (Cancelamento) --**")
        last_unidade = None
        for unidade, line in sorted(results['reducao']):
            if unidade != last_unidade:
                unidade_code = unidade.split(' ')[0]
                output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*")
                last_unidade = unidade
            output_lines.append(line)
            
    return "\n".join(output_lines)

def process_grouped_materia(
    main_article: BeautifulSoup, 
    full_text_content: str,
    custom_keywords: List[str]
) -> Optional[Publicacao]:
    
    organ = norm(main_article.get('artCategory', ''))
    section = main_article.get('pubName', '').upper()
    body = main_article.find('body')
    if not body: return None
    act_type = norm(body.find('Identifica').get_text(strip=True) if body.find('Identifica') else "")
    if not act_type: return None
    summary = norm(body.find('Ementa').get_text(strip=True) if body.find('Ementa') else "")
    display_text = norm(body.get_text(strip=True))
    if not summary:
        match = re.search(r'EMENTA:(.*?)(Vistos|ACORDAM)', display_text, re.DOTALL | re.I)
        if match: summary = norm(match.group(1))

    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()

    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ.lower()
        if is_mpo:
            found_navy_codes = [code for code in MPO_NAVY_TAGS if code in search_content_lower]
            if found_navy_codes:
                is_relevant = True
                summary_lower = summary.lower()
                if "altera parcialmente grupos de natureza de despesa" in summary_lower:
                    reason = parse_gnd_change_table(full_text_content)
                elif "os limites de movimentação e empenho constantes" in summary_lower:
                    reason = TEMPLATE_LME
                elif "modifica fontes de recursos" in summary_lower:
                    reason = TEMPLATE_FONTE
                elif "abre aos orçamentos fiscal" in summary_lower:
                    reason = TEMPLATE_CREDITO
                else:
                    reason = ANNOTATION_POSITIVE_GENERIC
            elif any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = ANNOTATION_NEGATIVE
        else:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break
    
    elif "DO2" in section:
        soup_copy = BeautifulSoup(full_text_content, 'lxml-xml')
        for tag in soup_copy.find_all('p', class_=['assina', 'cargo']):
            tag.decompose()
        clean_search_content_lower = norm(soup_copy.get_text(strip=True)).lower()

        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{term}'."
                break
        
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                name_lower = name.lower()
                for match in re.finditer(name_lower, clean_search_content_lower):
                    start_pos = max(0, match.start() - 150)
                    context_window = clean_search_content_lower[start_pos:match.start()]
                    if any(verb in context_window for verb in PERSONNEL_ACTION_VERBS):
                        is_relevant = True
                        reason = f"Ato de pessoal (Seção 2): menção a '{name}' em contexto de ação."
                        break
                if is_relevant:
                    break
    
    # Lógica de Palavra-Chave Personalizada (roda para todas as seções)
    found_custom_kw = None
    if custom_keywords:
        for kw in custom_keywords:
            if kw in search_content_lower:
                found_custom_kw = kw
                break
    
    if found_custom_kw:
        is_relevant = True # Garante que seja relevante
        custom_reason = f"Há menção à palavra-chave personalizada: '{found_custom_kw}'."
        if reason and reason != ANNOTATION_NEGATIVE:
            # Se já tinha um motivo (ex: MPO), anexa
            reason = f"{reason}\n⚓ {custom_reason}"
        elif not reason or reason == ANNOTATION_NEGATIVE:
            # Se não tinha motivo, ou o motivo era "negativo", substitui
            reason = custom_reason

    if is_relevant:
        return Publicacao(
            organ=organ, type=act_type, summary=summary,
            raw=display_text, relevance_reason=reason, section=section
        )
    return None

async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS: raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400: await client.aclose(); raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    r = await client.get(INLABS_BASE); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser"); cand_texts = [date, date.replace("-", "_"), date.replace("-", "")];
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip(); txt = (a.get_text() or "").strip(); hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts): return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"; rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    url = await resolve_date_url(client, date); r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser"); links: List[str] = []; wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted): links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))

async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = [];
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"): xml_blobs.append(z.read(name))
    return xml_blobs

@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None, description="Um JSON string de uma lista de keywords. Ex: '[\"amazul\", \"prosub\"]'")
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    
    # Processa as keywords customizadas
    custom_keywords = []
    if keywords_json:
        try:
            # Tenta carregar o JSON (que deve ser uma lista de strings)
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [str(k).strip().lower() for k in keywords_list if str(k).strip()]
        except json.JSONDecodeError:
            pass # Ignora keywords mal formatadas
    
    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.")
        
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))

        materias: Dict[str, Dict] = {}
        for blob in all_xml_blobs:
            try:
                soup = BeautifulSoup(blob, 'lxml-xml')
                article = soup.find('article')
                if not article: continue
                materia_id = article.get('idMateria')
                if not materia_id: continue
                if materia_id not in materias:
                    materias[materia_id] = {'main_article': None, 'full_text': ''}
                materias[materia_id]['full_text'] += blob.decode('utf-8', errors='ignore') + "\n"
                body = article.find('body')
                if body and body.find('Identifica') and body.find('Identifica').get_text(strip=True):
                    materias[materia_id]['main_article'] = article
            except Exception:
                continue
        
        pubs: List[Publicacao] = []
        for materia_id, content in materias.items():
            if content['main_article']:
                publication = process_grouped_materia(
                    content['main_article'], 
                    content['full_text'],
                    custom_keywords # Passa as keywords para o processador
                )
                if publication:
                    pubs.append(publication)
        
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged.append(p)
        
        texto = monta_whatsapp(merged, data)
        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()
