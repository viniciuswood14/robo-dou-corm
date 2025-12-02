# Nome do arquivo: api.py
# Versão: 16.0.0 (Com Integração mb_portaria_parser)

from fastapi import FastAPI, Form, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles 
from pydantic import BaseModel
from typing import List, Optional, Set, Dict, Any
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin
import asyncio

import httpx
from bs4 import BeautifulSoup

# IA / Gemini
import google.generativeai as genai

# Importa a nova função de busca do 'google_search.py'
try:
    from google_search import perform_google_search, SearchResult
except ImportError:
    pass

# Importações PAC
import numpy as np
try:
    from orcamentobr import despesa_detalhada
    from check_pac import update_pac_historical_cache
except ImportError:
    pass

# --- [LEGISLATIVO] ---
try:
    from check_legislativo import check_and_process_legislativo
except ImportError:
    pass

# --- [FALLBACK] ---
try:
    from dou_fallback import executar_fallback
except ImportError:
    executar_fallback = None

# --- [NOVO PARSER MPO] ---
try:
    import mb_portaria_parser
except ImportError:
    print("Aviso: 'mb_portaria_parser.py' não encontrado.")
    mb_portaria_parser = None

# =====================================================================================
# Robô DOU/Valor API
# =====================================================================================

app = FastAPI(
    title="Robô DOU/Valor API - v16.0.0 (Integrado)"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================================
# 1. WORKER EM BACKGROUND (O "CORAÇÃO" DO ROBÔ)
# =====================================================================================
@app.on_event("startup")
async def startup_event():
    print(">>> SISTEMA UNIFICADO INICIADO: API + SITE + ROBÔ <<<")
    try:
        from run_check import main_loop
        asyncio.create_task(main_loop())
        print(">>> Loop de verificação (run_check) iniciado com sucesso.")
    except ImportError:
        print("⚠️ AVISO: 'run_check.py' não encontrado. O robô automático não rodará.")
    except Exception as e:
        print(f"⚠️ ERRO AO INICIAR ROBÔ: {e}")


# =====================================================================================
# CONFIG
# =====================================================================================

try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}
except json.JSONDecodeError:
    config = {}

# Credenciais / URLs InLabs
INLABS_BASE = os.getenv(
    "INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br")
)
INLABS_LOGIN_URL = os.getenv(
    "INLABS_LOGIN_URL", config.get("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
)
INLABS_USER = os.getenv("INLABS_USER", config.get("INLABS_USER", None))
INLABS_PASS = os.getenv("INLABS_PASS", config.get("INLABS_PASS", None))

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.get("GEMINI_API_KEY", None))
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Constantes auxiliares
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)

ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

# PROMPTS IA
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil, especialista em legislação e defesa.
Sua tarefa é ler a publicação do Diário Oficial da União (DOU) abaixo e escrever uma única frase curta (máximo 2 linhas) para um relatório de WhatsApp, focando exclusivamente no impacto para a Marinha do Brasil (MB).

Critérios de Análise:
1.  Se for ato orçamentário (MPO/Fazenda), foque no impacto: É crédito, LME, fontes? Afeta UGs da Marinha?
2.  Se for ato normativo (Decreto, Portaria), qual a ação ou responsabilidade criada para a Marinha/Autoridade Marítima?
3.  Se for ato de pessoal (Seção 2), mencionar o nome da pessoa e qual atividade/ação a ela designada.
4.  Se a menção for trivial ou sem impacto direto, responda APENAS: "Sem impacto direto."
5.  Nunca alucinar ou inventar numeros. 

Responda só a frase final, sem rodeio adicional.

TEXTO DA PUBLICAÇÃO:
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha do Brasil. A publicação abaixo é do Ministério do Planejamento e Orçamento (MPO) e JÁ FOI CLASSIFICADA como tendo impacto direto em dotações ligadas à Marinha.

Sua tarefa é:
1. Dizer claramente qual o efeito orçamentário: crédito suplementar, alteração de GND, mudança de fonte, LME etc.
2. Dizer quem é afetado (Ex.: Comando da Marinha, Fundo Naval, Defesa).
3. Se houver valores, cite-os se estiverem explícitos. Não invente.
4. Entregar um texto formal para WhatsApp.

Você NÃO pode responder "Sem impacto direto", porque esta portaria JÁ foi marcada como relevante.

TEXTO DA PUBLICAÇÃO:
"""

GEMINI_VALOR_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler o TÍTULO e o RESUMO (snippet) de uma notícia do Valor Econômico e dizer, em uma única frase curta (máximo 2 linhas), qual o impacto ou relevância para a Marinha, Defesa ou para o Orçamento Federal.

TÍTULO: {titulo}
RESUMO: {resumo}

Responda só a frase final, sem rodeio.
"""


# =====================================================================================
# MODELOS Pydantic
# =====================================================================================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False  # chave pro prompt da IA
    is_parsed_mpo: bool = False    # NOVA FLAG: Indica que veio do Parser Especializado


class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

class ValorPublicacao(BaseModel):
    titulo: str
    link: str
    analise_ia: str

class ProcessResponseValor(BaseModel):
    date: str
    count: int
    publications: List[ValorPublicacao]
    whatsapp_text: str


# =====================================================================================
# HELPERS GERAIS
# =====================================================================================

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except: dd = when

    lines = [f"Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    # Agrupa TUDO por seção (inclusive MPO)
    pubs_by_section = {}
    for p in pubs:
        sec = p.section or "DOU"
        # Cria chaves para ordenar: Seção 1 -> 2 -> 3
        if "DO1" in sec or "Seção 1" in sec: sec_key = "1_DO1"
        elif "DO2" in sec or "Seção 2" in sec: sec_key = "2_DO2"
        elif "DO3" in sec or "Seção 3" in sec: sec_key = "3_DO3"
        else: sec_key = "4_OUTROS"
        
        pubs_by_section.setdefault(sec_key, []).append(p)

    for sec_key in sorted(pubs_by_section.keys()):
        label = "🔰 Seção 1" if "DO1" in sec_key else "🔰 Seção 2" if "DO2" in sec_key else "🔰 Seção 3" if "DO3" in sec_key else "🔰 Outros"
        lines.append(label)
        lines.append("")

        for p in pubs_by_section[sec_key]:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {p.type or 'Ato'}")
            
            if p.summary:
                lines.append(f"_{p.summary}_") 
            
            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚓"
            if "erro" in reason.lower() and "ia" in reason.lower(): prefix = "⚠️"
            
            # Se for parser MPO, adiciona quebra de linha antes da tabela
            if p.is_parsed_mpo: lines.append(f"{prefix}\n{reason}")
            else: lines.append(f"{prefix} {reason}")

            lines.append("")

    return "\n".join(lines)


def monta_valor_whatsapp(pubs: List[ValorPublicacao], when: str) -> str:
    """Gera o texto para o endpoint /processar-valor-ia"""
    meses_pt = {
        1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR",
        5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO",
        9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"
    }
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception:
        dd = when

    lines = []
    lines.append("Bom dia, senhores!")
    lines.append("")
    lines.append(f"PTC as seguintes publicações de interesse no Valor Econômico de {dd}:")
    lines.append("")

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    for p in pubs:
        lines.append(f"▶️ {p.titulo}")
        lines.append(f"📌 {p.link}")
        
        reason = p.analise_ia or "Para conhecimento."
        prefix = "⚓"
        
        if reason.startswith("Erro"):
            prefix = "⚠️"

        lines.append(f"{prefix} {reason}")
        lines.append("")

    return "\n".join(lines)


# =====================================================================================
# INTEGRAÇÃO DO PARSER MPO (NOVA FUNÇÃO)
# =====================================================================================
def run_mpo_parser_on_zip(zip_bytes: bytes) -> List[Publicacao]:
    if not mb_portaria_parser: return []
    results = []
    try:
        zip_io = io.BytesIO(zip_bytes)
        agg, pid_to_hint = mb_portaria_parser.parse_zip_in_memory(zip_io)
        
        for pid, rows in agg.items():
            hint = pid_to_hint.get(pid, "Ato orçamentário MPO")
            # Gera APENAS os dados contábeis
            analysis_text = mb_portaria_parser.render_whatsapp_block(pid, hint, rows)
            
            type_str = pid
            if "PORTARIA" not in pid.upper():
                type_str = f"PORTARIA GM/MPO Nº {pid}"

            pub = Publicacao(
                organ="Ministério do Planejamento e Orçamento",
                type=type_str,
                summary=hint,
                raw=analysis_text,
                relevance_reason=analysis_text,
                section="DO1", # Força Seção 1
                is_mpo_navy_hit=True,
                is_parsed_mpo=True
            )
            results.append(pub)
    except Exception as e:
        print(f"Erro no Parser MPO: {e}")
    return results


# =====================================================================================
# CLASSIFICAÇÃO INICIAL (LEGADO PARA OUTROS ORGÃOS)
# =====================================================================================
def process_grouped_materia(
    main_article: BeautifulSoup,
    full_text_content: str,
    custom_keywords: List[str],
) -> Optional[Publicacao]:
    organ = norm(main_article.get("artCategory", ""))
    organ_lower = organ.lower()
    
    # Filtro de ruído
    if (
        "comando da aeronáutica" in organ_lower
        or "comando do exército" in organ_lower
    ):
        return None
        
    section = (main_article.get("pubName", "") or "").upper()
    body = main_article.find("body")
    if not body:
        return None
        
    act_type = norm(body.find("Identifica").get_text(strip=True) if body.find("Identifica") else "")
    if not act_type: return None
    
    summary = norm(body.find("Ementa").get_text(strip=True) if body.find("Ementa") else "")
    display_text = norm(body.get_text(strip=True))
    
    if not summary:
        match = re.search(r"EMENTA:(.*?)(Vistos|ACORDAM)", display_text, re.DOTALL | re.I)
        if match: summary = norm(match.group(1))
    
    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()
    clean_text_for_ia = ""
    is_mpo_navy_hit_flag = False
    
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ_lower
        is_mf = "ministério da fazenda" in organ_lower

        # --- ETAPA 1: MPO (Lógica Residual) ---
        # Se for MPO, deixamos o Parser Especializado pegar se tiver UG. 
        # Aqui pegamos o que SOBROU (ex: textos sem tabela XML estruturada)
        if is_mpo:
            found_navy_codes = [code for code in MPO_NAVY_TAGS if code.lower() in search_content_lower]
            if found_navy_codes:
                is_relevant = True
                is_mpo_navy_hit_flag = True
                reason = "Ato do MPO com menção a códigos da Marinha (Verificar se há tabelas)."

        # --- ETAPA 2: Keywords de Interesse Direto ---
        if not is_relevant:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break
        
        # --- ETAPA 3: Keywords de Orçamento (SÓ SE FOR MPO ou MF) ---
        if not is_relevant and (is_mpo or is_mf):
            if any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = ANNOTATION_NEGATIVE or "Ato orçamentário, mas sem impacto direto explícito."

    elif "DO2" in section:
        # --- Lógica da Seção 2 (DO2) ---
        try:
            soup_copy = BeautifulSoup(full_text_content, "lxml-xml")
        except:
             soup_copy = BeautifulSoup(full_text_content, "html.parser")

        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]):
            tag.decompose()
        clean_search_content_lower = norm(soup_copy.get_text(strip=True)).lower()
        
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{term}'."
                break
        
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                if name.lower() in clean_search_content_lower:
                    is_relevant = True
                    reason = f"Ato de pessoal (Seção 2): menção a '{name}'."
                    break
    
    # --- Lógica de Keywords Customizadas ---
    if custom_keywords:
        for kw in custom_keywords:
            if kw and kw.lower() in search_content_lower:
                is_relevant = True
                reason = f"Há menção à palavra-chave personalizada: '{kw}'."
                break
            
    # --- Montagem final ---
    if is_relevant:
        try:
            soup_full_clean = BeautifulSoup(full_text_content, "lxml-xml")
        except:
            soup_full_clean = BeautifulSoup(full_text_content, "html.parser")
            
        clean_text_for_ia = norm(soup_full_clean.get_text(strip=True))
        return Publicacao(
            organ=organ,
            type=act_type,
            summary=summary,
            raw=display_text,
            relevance_reason=reason,
            section=section,
            clean_text=clean_text_for_ia,
            is_mpo_navy_hit=is_mpo_navy_hit_flag,
        )
    
    return None


# =====================================================================================
# INLABS / DOWNLOAD ZIP
# =====================================================================================
async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(
            status_code=500,
            detail="Config ausente: INLABS_USER e INLABS_PASS.",
        )
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        await client.get(INLABS_BASE)
    except Exception:
        pass
    r = await client.post(
        INLABS_LOGIN_URL,
        data={"email": INLABS_USER, "password": INLABS_PASS},
    )
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Falha de login no INLABS: HTTP {r.status_code}",
        )
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    r = await client.get(INLABS_BASE)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cand_texts = [
        date,
        date.replace("-", "_"),
        date.replace("-", ""),
    ]
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
    raise HTTPException(
        status_code=404,
        detail=f"Não encontrei a pasta/listagem da data {date} após login.",
    )

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    base_url = await resolve_date_url(client, date)
    r = await client.get(base_url)
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao abrir listagem {base_url}: HTTP {r.status_code}",
        )
    return r.text

def pick_zip_links_from_listing(
    html: str,
    base_url_for_rel: str,
    only_sections: List[str],
) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    
    wanted = set(s.strip().upper() for s in only_sections) if only_sections else {"DO1"}
    print(f"DEBUG: Procurando seções {wanted} em {base_url_for_rel}")

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        text = (a.get_text() or "").strip().upper()
        
        if href.lower().endswith(".zip"):
            full_url = urljoin(base_url_for_rel, href)
            filename = href.split("/")[-1].upper()
            
            is_match = False
            for sec in wanted:
                if sec in filename or sec in text:
                    is_match = True
                    break
            
            if is_match:
                print(f"DEBUG: ZIP Encontrado: {full_url}")
                links.append(full_url)
    
    return sorted(list(set(links)))


async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}",
        )
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.lower().endswith(".xml"):
                    xml_blobs.append(z.read(name))
    except zipfile.BadZipFile:
        pass
    return xml_blobs

# =====================================================================================
# /processar-inlabs (COM REDUNDÂNCIA E PARSER MPO)
# =====================================================================================
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(data: str = Form(...), sections: str = Form("DO1,DO2"), keywords_json: str = Form(None)):
    secs = [s.strip().upper() for s in sections.split(",")]
    custom_kw = json.loads(keywords_json) if keywords_json else []
    pubs_final = []
    usou_fallback = False

    try:
        client = await inlabs_login_and_get_session()
        html = await fetch_listing_html(client, data)
        zips = pick_zip_links_from_listing(html, "", secs)
        if not zips: raise HTTPException(404, "ZIPs não encontrados")

        for url in zips:
            zb = await download_zip(client, url)
            
            # 1. PARSER MPO (Prioridade)
            if mb_portaria_parser:
                pubs_final.extend(run_mpo_parser_on_zip(zb))
            
            # 2. PARSER GENÉRICO (XML)
            for blob in extract_xml_from_zip(zb):
                try:
                    soup = BeautifulSoup(blob, "lxml-xml")
                    art = soup.find("article")
                    if not art: continue
                    
                    # Usa a função de extração antiga para pegar o resto
                    p = process_grouped_materia(art, blob.decode("utf-8", "ignore"), custom_kw)
                    if p:
                        # Evita duplicata se o Parser MPO já pegou esta mesma portaria
                        is_dup = any(p.type == existing.type for existing in pubs_final if existing.is_parsed_mpo)
                        if not is_dup:
                            pubs_final.append(p)
                except: continue
        await client.aclose()

    except Exception as e:
        print(f"Erro InLabs: {e}"); usou_fallback = True

    if usou_fallback and executar_fallback:
        try:
            fb = await executar_fallback(data, custom_kw)
            for i in fb:
                pubs_final.append(Publicacao(
                    organ=i['organ'], type=i['type'], summary=i['summary'],
                    raw=i['raw'], relevance_reason=i['relevance_reason']+" (Fallback)",
                    section=i['section'], clean_text=i['raw'], is_parsed_mpo=False
                ))
        except: pass

    # Deduplicação Final
    seen = set()
    unique = []
    for p in pubs_final:
        k = f"{p.organ}{p.type}{str(p.summary)[:30]}"
        if k not in seen: seen.add(k); unique.append(p)

    return ProcessResponse(
        date=data, count=len(unique), publications=unique,
        whatsapp_text=monta_whatsapp(unique, data)
    )

# =====================================================================================
# [NOVO] ENDPOINT DE TESTE DO FALLBACK
# =====================================================================================
@app.post("/teste-fallback", response_model=ProcessResponse)
async def teste_fallback(
    data: str = Form(..., description="YYYY-MM-DD"),
    keywords_json: Optional[str] = Form(None)
):
    if not executar_fallback:
        raise HTTPException(status_code=500, detail="Módulo 'dou_fallback.py' não encontrado ou com erro.")

    print(f">>> [TESTE] Iniciando Fallback Manual para {data}...")
    
    custom_keywords = []
    if keywords_json:
        try:
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [str(k).strip().lower() for k in keywords_list if str(k).strip()]
        except: pass

    try:
        fb_results = await executar_fallback(data, custom_keywords)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no Fallback: {str(e)}")

    pubs = []
    for item in fb_results:
        pubs.append(Publicacao(
            organ=item['organ'],
            type=item['type'],
            summary=item['summary'],
            raw=item['raw'],
            relevance_reason=item['relevance_reason'],
            section=item['section'],
            clean_text=item['raw']
        ))

    texto = monta_whatsapp(pubs, data)
    texto = "🧪 *RELATÓRIO DE TESTE (FALLBACK/DOU PÚBLICO)*\n\n" + texto

    return ProcessResponse(
        date=data,
        count=len(pubs),
        publications=pubs,
        whatsapp_text=texto
    )

# =====================================================================================
# IA helper
# =====================================================================================
async def get_ai_analysis(
    clean_text: str,
    model: genai.GenerativeModel,
    prompt_template: str = GEMINI_MASTER_PROMPT,
) -> Optional[str]:
    try:
        prompt = f"{prompt_template}\n\n{clean_text}"
        response = await model.generate_content_async(prompt)
        try:
            analysis = norm(response.text)
            if analysis:
                return analysis
            else:
                return None
        except ValueError as e:
            print(f"Bloco de IA (ValueError): {e}")
            return None
    except Exception as e:
        print(f"Erro na API do Gemini: {e}")
        return "Erro na análise de IA: " + str(e)[:100]

# =====================================================================================
# FUNÇÕES AUXILIARES PARA O VALOR (CRAWLER)
# =====================================================================================

async def crawl_valor_headlines(cover_url: str, date_str: str) -> List[Dict[str, str]]:
    print(f"[Valor Crawler] Acessando capa: {cover_url}")
    found_articles = []
    date_clean = date_str.replace("-", "") 
    
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            }
            r = await client.get(cover_url, headers=headers)
            if r.status_code != 200:
                print(f"[Valor Crawler] Falha ao acessar capa: {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            
            for a in soup.find_all("a", href=True):
                href = a["href"]
                title = norm(a.get_text())
                
                if date_clean in href and len(href) > len(f"/impresso/{date_clean}/"):
                    full_link = href if href.startswith("http") else f"https://valor.globo.com{href}"
                    
                    if title and len(title) > 10 and not any(f['link'] == full_link for f in found_articles):
                         found_articles.append({"title": title, "link": full_link})
                         
            print(f"[Valor Crawler] Encontradas {len(found_articles)} notícias dentro da capa.")
            return found_articles

        except Exception as e:
            print(f"[Valor Crawler] Erro: {e}")
            return []

# =====================================================================================
# FUNÇÃO DE ANÁLISE DO VALOR (ATUALIZADA COM CRAWLER)
# =====================================================================================
async def run_valor_analysis(today_str: str, use_state: bool = True) -> (List[Dict[str, Any]], Set[str]):
    
    if not GEMINI_API_KEY:
        print("Erro (Valor): GEMINI_API_KEY não encontrada.")
        return [], set()
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        print(f"Falha (Valor) ao inicializar o modelo de IA: {e}")
        return [], set()

    date_suffix = today_str.replace("-", "")

    # 1. Busca no Google
    google_results = []
    for q in SEARCH_QUERIES:
        print(f"Buscando query: {q} para a data {today_str}")
        try:
            res = await perform_google_search(q, search_date=today_str)
            google_results.extend(res)
        except: pass
        await asyncio.sleep(1) 
    
    final_articles_to_analyze = []
    processed_links = set()
    
    # 2. Processa resultados do Google (Crawler se achar capa)
    for res in google_results:
        if res.link.rstrip("/").endswith(date_suffix):
            print(f"⚠️ Capa detectada ({res.link}). Iniciando Crawler...")
            crawled_news = await crawl_valor_headlines(res.link, today_str)
            for news in crawled_news:
                if news['link'] not in processed_links:
                    final_articles_to_analyze.append(news)
                    processed_links.add(news['link'])
        else:
            if res.link not in processed_links:
                final_articles_to_analyze.append({"title": res.title, "link": res.link})
                processed_links.add(res.link)

    if not final_articles_to_analyze:
        print("Nenhuma notícia específica encontrada.")
        return [], set()
    
    print(f"Analisando {len(final_articles_to_analyze)} matérias com IA...")

    # 4. Analisa com IA
    pubs_finais = []
    links_encontrados = set()

    for item in final_articles_to_analyze:
        text_check = item['title'].lower()
        keywords_fast = ["orçamento", "fiscal", "defesa", "marinha", "gasto", "corte", "lula", "haddad", "múcio", "economia"]
        
        if any(k in text_check for k in keywords_fast):
            prompt = GEMINI_VALOR_PROMPT.format(titulo=item['title'], resumo="")
            ai_reason = await get_ai_analysis(
                clean_text=f"TÍTULO: {item['title']}",
                model=model,
                prompt_template=GEMINI_VALOR_PROMPT
            )
            links_encontrados.add(item['link'])

            if ai_reason and "sem impacto" not in ai_reason.lower():
                pubs_finais.append({
                    "titulo": item['title'],
                    "link": item['link'],
                    "analise_ia": ai_reason
                })

    return pubs_finais, links_encontrados

# =====================================================================================
# /processar-dou-ia (COM IA) - Endpoint Lento (DOU)
# =====================================================================================
@app.post("/processar-dou-ia", response_model=ProcessResponse)
async def processar_dou_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None),
):
    # Por segurança, chamamos o processar_inlabs que já tem a lógica toda (incluindo o MPO Parser)
    # e aqui só adicionamos IA no que não for MPO Parser
    
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY não definida.")
        
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") 
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha IA: {e}")

    # Chama o fluxo padrão (que já traz o MPO parseado)
    res_padrao = await processar_inlabs(data, sections, keywords_json)
    
    pubs_analisadas = []
    tasks = []
    
    for p in res_padrao.publications:
        # Se veio do Parser Especializado, NÃO gasta token de IA, já está perfeito
        if p.is_parsed_mpo:
            pubs_analisadas.append(p)
            continue
            
        # Se for publicação genérica, manda pra IA
        prompt = GEMINI_MPO_PROMPT if p.is_mpo_navy_hit else GEMINI_MASTER_PROMPT
        
        # Cria tarefa assíncrona
        tasks.append(analyze_single_pub(p, model, prompt))

    # Roda IA em paralelo
    results = await asyncio.gather(*tasks)
    
    # Reconstrói a lista final (MPO Parser + IA Results)
    # Nota: pubs_analisadas já tem os MPOs. Agora adicionamos os da IA.
    for p_res in results:
        if p_res:
            pubs_analisadas.append(p_res)
            
    # Remonta o texto do WhatsApp
    texto_final = monta_whatsapp(pubs_analisadas, data)
    
    return ProcessResponse(
        date=data,
        count=len(pubs_analisadas),
        publications=pubs_analisadas,
        whatsapp_text=texto_final
    )

async def analyze_single_pub(pub: Publicacao, model, prompt_template):
    # Wrapper para processar uma publicação com IA e retornar o objeto atualizado
    try:
        analysis = await get_ai_analysis(pub.clean_text or pub.raw, model, prompt_template)
        if analysis:
            if "sem impacto" in analysis.lower() and not pub.is_mpo_navy_hit:
                return None # Filtra irrelevantes
            pub.relevance_reason = analysis
        return pub
    except:
        return pub # Retorna original em caso de erro

# =====================================================================================
# /processar-valor-ia (COM IA) - Endpoint Lento (Valor)
# =====================================================================================
@app.post("/processar-valor-ia", response_model=ProcessResponseValor)
async def processar_valor_ia(
    data: str = Form(..., description="YYYY-MM-DD")
):
    pubs_list, _ = await run_valor_analysis(data, use_state=False)
    pubs_model = [ValorPublicacao(**p) for p in pubs_list]
    texto = monta_valor_whatsapp(pubs_model, data)

    return ProcessResponseValor(
        date=data,
        count=len(pubs_model),
        publications=pubs_model,
        whatsapp_text=texto
    )


# =====================================================================================
# ENDPOINT - DASHBOARD PAC v1.1
# =====================================================================================

PROGRAMAS_ACOES_PAC = {
    'PROSUB': {
        '123G': 'IMPLANTACAO DE ESTALEIRO E BASE NAVAL',
        '123H': 'CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR',
        '123I': 'CONSTRUCAO DE SUBMARINOS CONVENCIONAIS'
    },
    'PNM': {
        '14T7': 'DESENVOLVIMENTO DE TECNOLOGIA NUCLEAR'
    },
    'PRONAPA': {
        '1N47': 'CONSTRUCAO DE NAVIOS-PATRULHA 500T'
    }
}

async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    # ... (mesma implementação do original)
    try:
        df_detalhado = await asyncio.to_thread(
            despesa_detalhada,
            exercicio=ano,
            acao=acao_cod,
            inclui_descricoes=True,
            ignore_secure_certificate=True
        )
        if df_detalhado.empty: return None
        
        cols_possiveis = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago', 'dotacao_disponivel', 'saldo_disponivel', 'saldo_dotacao']
        colunas_para_somar = [col for col in cols_possiveis if col in df_detalhado.columns]
        
        if not colunas_para_somar: return None
            
        totais_acao = df_detalhado[colunas_para_somar].sum()
        dados_finais = totais_acao.to_dict()
        dados_finais['Acao_cod'] = acao_cod
        
        if 'dotacao_disponivel' not in dados_finais:
             saldo = dados_finais.get('saldo_disponivel') or dados_finais.get('saldo_dotacao') or 0.0
             dados_finais['dotacao_disponivel'] = saldo

        return dados_finais
    except:
        return None

@app.get("/api/pac-data/{ano}", summary="Busca dados de execução do PAC por ano")
async def get_pac_data(ano: int = Path(..., ge=2010, le=2025)):
    tasks = [] 
    for programa, acoes in PROGRAMAS_ACOES_PAC.items():
        for acao_cod in acoes.keys():
            tasks.append(buscar_dados_acao_pac(ano, acao_cod))

    resultados_brutos = await asyncio.gather(*tasks)
    dados_brutos = [r for r in resultados_brutos if r is not None]
    
    if not dados_brutos: return []

    tabela_final = []
    total_geral = {'LOA': 0.0, 'DOTAÇÃO ATUAL': 0.0, 'DISPONÍVEL': 0.0, 'EMPENHADO (c)': 0.0, 'LIQUIDADO': 0.0, 'PAGO': 0.0}

    for programa, acoes in PROGRAMAS_ACOES_PAC.items():
        soma_programa = {'LOA': 0.0, 'DOTAÇÃO ATUAL': 0.0, 'DISPONÍVEL': 0.0, 'EMPENHADO (c)': 0.0, 'LIQUIDADO': 0.0, 'PAGO': 0.0}
        linhas_acao_programa = []
        
        for acao_cod, acao_desc in acoes.items():
            row_data = next((d for d in dados_brutos if d.get('Acao_cod') == acao_cod), None)
            def get_val(key): return float(row_data.get(key, 0.0)) if row_data else 0.0

            vals = {
                'LOA': get_val('loa'), 'DOTAÇÃO ATUAL': get_val('loa_mais_credito'),
                'DISPONÍVEL': get_val('dotacao_disponivel'), 'EMPENHADO (c)': get_val('empenhado'),
                'LIQUIDADO': get_val('liquidado'), 'PAGO': get_val('pago')
            }

            linhas_acao_programa.append({
                'PROGRAMA': None, 'AÇÃO': f"{acao_cod} - {acao_desc.upper()}", **vals
            })
            
            for k, v in vals.items(): soma_programa[k] += v

        tabela_final.append({'PROGRAMA': programa, 'AÇÃO': None, **soma_programa})
        tabela_final.extend(linhas_acao_programa)
        for k, v in soma_programa.items(): total_geral[k] += v
        
    tabela_final.append({'PROGRAMA': 'Total Geral', 'AÇÃO': None, **total_geral})
    return tabela_final

@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    print("Forçando atualização do cache histórico do PAC...")
    await update_pac_historical_cache()
    return {"status": "Cache histórico atualizado com sucesso!"}

# =====================================================================================
# LEGISLATIVO
# =====================================================================================
@app.post("/processar-legislativo")
async def endpoint_legislativo(days: int = Form(5)):
    try:
        func_check = globals().get('check_and_process_legislativo')
        if not func_check:
            try:
                import check_legislativo
                func_check = check_legislativo.check_and_process_legislativo
            except ImportError:
                return {"count": 0, "message": "Erro: módulo legislativo não encontrado.", "data": []}

        propostas = await func_check(only_new=False, days_back=days)
        if not propostas:
             return {"count": 0, "message": f"Nenhuma proposição encontrada nos últimos {days} dias.", "data": []}
            
        return {"count": len(propostas), "message": f"Foram encontradas {len(propostas)} proposições.", "data": propostas}

    except Exception as e:
        print(f"Erro no endpoint legislativo: {e}")
        raise HTTPException(status_code=500, detail=f"Erro no módulo legislativo: {str(e)}")

# =====================================================================================
# HEALTHCHECK E TESTE IA
# =====================================================================================
@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now().isoformat()}

@app.get("/test-ia")
async def test_ia_endpoint():
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY não configurada.")
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        response = await model.generate_content_async("Teste IA")
        return {"result": f"Teste OK! Resp: {response.text}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Teste FALHOU: {e}")

# =====================================================================================
# SERVIR ARQUIVOS ESTÁTICOS (FRONTEND)
# =====================================================================================
if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else:
    print("⚠️ AVISO: Pasta 'static' não encontrada. O frontend não será servido.")
