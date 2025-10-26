from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set, Dict
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin
import asyncio 

import httpx
from bs4 import BeautifulSoup

# --- Importações da IA (Corrigidas v13.3) ---
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
# -------------------------------------------

# #####################################################################
# ########## VERSÃO 13.12 - Correção UnboundLocalError ('match') ##########
# #####################################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v13.12 (IA)")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG E KEYWORDS (CARREGADOS DO config.json) ======
# ... (código inalterado) ...
try:
    with open("config.json", "r", encoding="utf-8") as f: config = json.load(f)
except FileNotFoundError: raise RuntimeError("Erro: Arquivo 'config.json' não encontrado.")
except json.JSONDecodeError: raise RuntimeError("Erro: Falha ao decodificar 'config.json'. Verifique a sintaxe.")
INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", config.get("INLABS_LOGIN_URL", f"{INLABS_BASE}/login"))
INLABS_USER = os.getenv("INLABS_USER"); INLABS_PASS = os.getenv("INLABS_PASS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY: genai.configure(api_key=GEMINI_API_KEY)
TEMPLATE_LME = config.get("TEMPLATE_LME", ""); TEMPLATE_FONTE = config.get("TEMPLATE_FONTE", ""); TEMPLATE_CREDITO = config.get("TEMPLATE_CREDITO", "")
ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", ""); ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {}); KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", []); MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", []); TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)
# ==========================================================

# --- Master Prompt da IA ---
# ... (código inalterado) ...
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil, especialista em legislação e defesa.
Sua tarefa é ler a publicação do Diário Oficial da União (DOU) abaixo e escrever uma única frase curta (máximo 2 linhas) para um relatório de WhatsApp, focando exclusivamente no impacto para a Marinha do Brasil (MB).
Critérios de Análise:
1.  Se for ato orçamentário (MPO/Fazenda), foque no impacto: É crédito, LME, fontes? Afeta UGs da Marinha (Comando, Fundo Naval, AMAZUL)?
2.  Se for ato normativo (Decreto, Portaria), qual a ação ou responsabilidade criada para a Marinha/Autoridade Marítima?
3.  Se for ato de pessoal (Seção 2), quem é a pessoa e qual a ação (nomeação, exoneração, viagem)?
4.  Se a menção for trivial ou sem impacto direto (ex: 'Ministério da Defesa' apenas citado numa lista de participantes de reunião, ou 'Marinha' em nome de empresa privada), responda APENAS com a frase: "Sem impacto direto."
Seja direto e objetivo.
TEXTO DA PUBLICAÇÃO:
"""
# ---------------------------------

class Publicacao(BaseModel):
    # ... (código inalterado) ...
    organ: Optional[str] = None; type: Optional[str] = None; summary: Optional[str] = None
    raw: Optional[str] = None; relevance_reason: Optional[str] = None; section: Optional[str] = None
    clean_text: Optional[str] = None 

class ProcessResponse(BaseModel):
    # ... (código inalterado) ...
    date: str; count: int; publications: List[Publicacao]; whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str: # ... (código inalterado) ...
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str: # ... (código inalterado) ...
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try: dt = datetime.fromisoformat(when); dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception: dd = when
    lines = ["Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]; pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs: sec = p.section or "DOU";
    if sec not in pubs_by_section: pubs_by_section[sec] = []; pubs_by_section[sec].append(p)
    if not pubs: lines.append("— Sem ocorrências para os critérios informados —"); return "\n".join(lines)
    for section_name in sorted(pubs_by_section.keys()):
        if not pubs_by_section[section_name]: continue
        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}"); lines.append("")
        for p in pubs_by_section[section_name]:
            lines.append(f"▶️ {p.organ or 'Órgão'}"); lines.append(f"📌 {p.type or 'Ato/Portaria'}");
            if p.summary: lines.append(p.summary)
            reason = p.relevance_reason or "Para conhecimento."; prefix = "⚓"
            if reason.startswith("Erro na análise de IA:") or reason.startswith("Erro GRAVE"): prefix = "⚠️ Erro IA:"; reason = reason.replace("Erro na análise de IA:", "").replace("Erro GRAVE na análise de IA:", "").strip()
            if '\n' in reason: lines.append(f"{prefix}\n{reason}")
            else: lines.append(f"{prefix} {reason}")
            lines.append("")
    return "\n".join(lines)

def parse_gnd_change_table(full_text_content: str) -> str: # ... (código inalterado) ...
    soup = BeautifulSoup(full_text_content, 'lxml-xml'); results = {'acrescimo': [], 'reducao': []}
    current_unidade = None; current_operation = None
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cols = row.find_all('td'); row_text_cells = [norm(c.get_text()) for c in cols]; row_full_text = " ".join(row_text_cells)
            if "UNIDADE:" in row_full_text: current_unidade = row_full_text.replace("UNIDADE:", "").strip(); continue 
            if "PROGRAMA DE TRABALHO" in row_full_text:
                if "ACRÉSCIMO" in row_full_text.upper(): current_operation = "acrescimo"
                elif "REDUÇÃO" in row_full_text.upper() or "CANCELAMENTO" in row_full_text.upper(): current_operation = "reducao"
                else: current_operation = None; continue 
            if len(cols) != 10 or "PROGRAMÁTICA" in row_full_text.upper(): continue
            if current_unidade and current_operation and any(tag in current_unidade for tag in MPO_NAVY_TAGS.keys()):
                try: ao, desc, _, _, gnd, _, _, _, _, valor = row_text_cells;
                if not valor: continue; clean_gnd = gnd.replace('-','').replace('ODC','').replace('INV',''); line = f"- AO {ao} - {desc} | GND: {clean_gnd} | Valor: {valor}"; results[current_operation].append((current_unidade, line))
                except (IndexError, ValueError): continue
    if not results['acrescimo'] and not results['reducao']: return "Ato de Alteração de GND com impacto na Defesa/Marinha. Recomenda-se análise manual dos anexos."
    output_lines = ["Ato de Alteração de GND com impacto na Defesa/Marinha. Dados extraídos dos anexos:"]
    if results['acrescimo']: output_lines.append("\n**-- ACRÉSCIMOS (Suplementação) --**"); last_unidade = None;
    for unidade, line in sorted(results['acrescimo']):
        if unidade != last_unidade: unidade_code = unidade.split(' ')[0]; output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*"); last_unidade = unidade
        output_lines.append(line)
    if results['reducao']: output_lines.append("\n**-- REDUÇÕES (Cancelamento) --**"); last_unidade = None;
    for unidade, line in sorted(results['reducao']):
        if unidade != last_unidade: unidade_code = unidade.split(' ')[0]; output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*"); last_unidade = unidade
        output_lines.append(line)
    return "\n".join(output_lines)

# (Função de filtro v12.7 - Esta é o Estágio 1)
def process_grouped_materia(
    main_article: BeautifulSoup, 
    full_text_content: str, # Este é o XML/HTML bruto
    custom_keywords: List[str]
) -> Optional[Publicacao]:
    
    organ = norm(main_article.get('artCategory', ''))
    section = main_article.get('pubName', '').upper()
    body = main_article.find('body')
    if not body: return None
    act_type = norm(body.find('Identifica').get_text(strip=True) if body.find('Identifica') else "")
    if not act_type: return None
    summary = norm(body.find('Ementa').get_text(strip=True) if body.find('Ementa') else "")
    display_text = norm(body.get_text(strip=True)) # Texto limpo apenas do body principal
    
    # --- ALTERAÇÃO v13.12 ---
    match = None # Inicializa match como None para evitar UnboundLocalError
    # -----------------------
    
    if not summary:
        # Tenta extrair a ementa do texto completo (display_text) se não encontrada na tag Ementa
        match_ementa = re.search(r'EMENTA:(.*?)(Vistos|ACORDAM|\Z)', display_text, re.DOTALL | re.I)
        if match_ementa: 
            summary = norm(match_ementa.group(1))

    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower() # Texto bruto completo para busca de keywords
    clean_text_for_ia = "" # Prepara o texto limpo para a IA

    # ... (Restante da lógica de relevância - sem alterações) ...
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ.lower()
        if is_mpo:
            found_navy_codes = [code for code in MPO_NAVY_TAGS if code in search_content_lower]
            if found_navy_codes:
                is_relevant = True; summary_lower = summary.lower()
                if "altera parcialmente grupos de natureza de despesa" in summary_lower: reason = parse_gnd_change_table(full_text_content) 
                elif "os limites de movimentação e empenho constantes" in summary_lower: reason = TEMPLATE_LME
                elif "modifica fontes de recursos" in summary_lower: reason = TEMPLATE_FONTE
                elif "abre aos orçamentos fiscal" in summary_lower: reason = TEMPLATE_CREDITO
                else: reason = ANNOTATION_POSITIVE_GENERIC
            elif any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1): is_relevant = True; reason = ANNOTATION_NEGATIVE
        else:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower: is_relevant = True; reason = f"Há menção específica à TAG: '{kw}'."; break
    elif "DO2" in section:
        soup_copy = BeautifulSoup(full_text_content, 'lxml-xml')
        for tag in soup_copy.find_all('p', class_=['assina', 'cargo']): tag.decompose()
        clean_search_content_lower = norm(soup_copy.get_text(strip=True)).lower()
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower: is_relevant = True; reason = f"Ato de pessoal (Seção 2): menção a '{term}'."; break
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                name_lower = name.lower()
                for match_name in re.finditer(name_lower, clean_search_content_lower): # Renomeado para match_name
                    start_pos = max(0, match_name.start() - 150); context_window_text = clean_search_content_lower[start_pos:match_name.start()]
                    if any(verb in context_window_text for verb in PERSONNEL_ACTION_VERBS):
                        is_relevant = True; reason = f"Ato de pessoal (Seção 2): menção a '{name}' em contexto de ação."; break
                if is_relevant: break
    found_custom_kw = None; custom_reason_text = None
    if custom_keywords:
        for kw in custom_keywords:
            if kw in search_content_lower: found_custom_kw = kw; custom_reason_text = f"Há menção à palavra-chave personalizada: '{kw}'."; break
    if found_custom_kw:
        is_relevant = True 
        if reason and reason != ANNOTATION_NEGATIVE: reason = f"{reason}\n⚓ {custom_reason_text}"
        elif not reason or reason == ANNOTATION_NEGATIVE: reason = custom_reason_text
    if is_relevant:
        soup_full_clean = BeautifulSoup(full_text_content, 'lxml-xml'); clean_text_for_ia = norm(soup_full_clean.get_text(strip=True))
        return Publicacao(organ=organ, type=act_type, summary=summary, raw=display_text, relevance_reason=reason, section=section, clean_text=clean_text_for_ia)
    return None

# --- Funções de Rede (sem mudança) ---
async def inlabs_login_and_get_session() -> httpx.AsyncClient: # ... (código inalterado) ...
    if not INLABS_USER or not INLABS_PASS: raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True); 
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400: await client.aclose(); raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client
async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str: # ... (código inalterado) ...
    r = await client.get(INLABS_BASE); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser"); cand_texts = [date, date.replace("-", "_"), date.replace("-", "")];
    for a in soup.find_all("a"): href = (a.get("href") or "").strip(); txt = (a.get_text() or "").strip(); hay = (txt + " " + href).lower();
    if any(c.lower() in hay for c in cand_texts): return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"; rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")
async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str: # ... (código inalterado) ...
    url = await resolve_date_url(client, date); r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text
def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]: # ... (código inalterado) ...
    soup = BeautifulSoup(html, "html.parser"); links: List[str] = []; wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True): href = a["href"];
    if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted): links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))
async def download_zip(client: httpx.AsyncClient, url: str) -> bytes: # ... (código inalterado) ...
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content
def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]: # ... (código inalterado) ...
    xml_blobs: List[bytes] = [];
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"): xml_blobs.append(z.read(name))

