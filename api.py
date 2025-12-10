# Nome do arquivo: api.py
# Versão: 16.1.0 (Versão Estável - Fallback Robusto e Formatação Unificada)

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
    print("Aviso: 'mb_portaria_parser.py' não encontrado. Usando apenas análise genérica.")
    mb_portaria_parser = None

# Adicionar logo após os outros imports do PAC no api.py
from check_pac import HISTORICAL_CACHE_PATH

# ... (outras importações)
try:
    from orcamentobr import despesa_detalhada
    # Adicione HISTORICAL_CACHE_PATH aqui:
    from check_pac import update_pac_historical_cache, HISTORICAL_CACHE_PATH 
except ImportError:
    pass

# =====================================================================================
# API SETUP
# =====================================================================================

app = FastAPI(title="Robô DOU/Valor API - v16.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    print(">>> SISTEMA UNIFICADO INICIADO: API + SITE + ROBÔ <<<")

    # --- [NOVO BLOCO] Verificação Inicial do Cache PAC ---
    try:
        if not os.path.exists(HISTORICAL_CACHE_PATH):
            print("⚠️ Cache do PAC não encontrado. Iniciando geração inicial em background...")
            asyncio.create_task(update_pac_historical_cache())
        else:
            print("✅ Cache do PAC já existe.")
    except Exception as e:
        print(f"Erro ao verificar cache PAC: {e}")
    # -----------------------------------------------------

    try:
        from run_check import main_loop
        asyncio.create_task(main_loop())
        print(">>> Loop de verificação (run_check) iniciado com sucesso.")
    except ImportError:
        print("⚠️ AVISO: 'run_check.py' não encontrado. O robô automático não rodará.")

# =====================================================================================
# CONFIGURAÇÕES
# =====================================================================================

try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    config = {}

INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER", config.get("INLABS_USER", None))
INLABS_PASS = os.getenv("INLABS_PASS", config.get("INLABS_PASS", None))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.get("GEMINI_API_KEY", None))

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Constantes de Filtro
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)

ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

# PROMPTS
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler a publicação do DOU e escrever UMA frase curta (max 2 linhas) para relatório WhatsApp.
Critérios:
1. Orçamentário: Impacto (Crédito, LME, Fontes)? Afeta Marinha?
2. Pessoal: Quem/Cargo.
3. Normativo: Qual regra.
4. Trivial: "Sem impacto direto."
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha. Esta portaria JÁ FOI marcada como relevante.
Diga: 1. Tipo alteração. 2. Quem é afetado. 3. Valores (se explícitos).
Responda formalmente.
"""

GEMINI_VALOR_PROMPT = """
Você é um analista financeiro da Marinha. Leia o título e resumo.
Diga em 1 frase o impacto para Defesa, Marinha ou Orçamento Federal.
TÍTULO: {titulo}
RESUMO: {resumo}
"""

# =====================================================================================
# MODELOS PYDANTIC
# =====================================================================================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False
    is_parsed_mpo: bool = False  # Flag indicando origem do Parser Especializado

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
# HELPERS GERAIS & MONTA WHATSAPP
# =====================================================================================

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    """
    Gera o relatório do WhatsApp.
    Integra o MPO Parser dentro da Seção 1 com formato padrão.
    """
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except: dd = when

    lines = [f"Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    # Agrupa TUDO por seção
    pubs_by_section: Dict[str, List[Publicacao]] = {}
    
    for p in pubs:
        # Se veio do Parser MPO e não tem seção definida, joga na DO1
        sec = p.section or "DOU"
        
        # Cria chaves de ordenação (Seção 1 -> 2 -> 3)
        if "DO1" in sec or "Seção 1" in sec: 
            sec_key = "1_DO1"
        elif "DO2" in sec or "Seção 2" in sec: 
            sec_key = "2_DO2"
        elif "DO3" in sec or "Seção 3" in sec: 
            sec_key = "3_DO3"
        else: 
            sec_key = "4_OUTROS"
        
        pubs_by_section.setdefault(sec_key, []).append(p)

    for sec_key in sorted(pubs_by_section.keys()):
        # Define o título visual da seção
        if "DO1" in sec_key: label = "🔰 Seção 1"
        elif "DO2" in sec_key: label = "🔰 Seção 2"
        elif "DO3" in sec_key: label = "🔰 Seção 3"
        else: label = "🔰 Outros"
        
        lines.append(label)
        lines.append("")

        for p in pubs_by_section[sec_key]:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {p.type or 'Ato'}")
            
            # Resumo (Ementa) - Em itálico
            if p.summary:
                lines.append(f"_{p.summary}_") 
            
            # Análise (IA ou Parser Contábil)
            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚓"
            
            if "erro" in reason.lower() and "ia" in reason.lower():
                prefix = "⚠️"
            
            # Se for parser MPO, o texto já vem formatado com quebras de linha
            if p.is_parsed_mpo:
                lines.append(f"{prefix}\n{reason}")
            else:
                lines.append(f"{prefix} {reason}")

            lines.append("")

    return "\n".join(lines)

def monta_valor_whatsapp(pubs: List[ValorPublicacao], when: str) -> str:
    lines = [f"Notícias Valor Econômico ({when}):", ""]
    for p in pubs:
        lines.append(f"▶️ {p.titulo}")
        lines.append(f"📌 {p.link}")
        lines.append(f"⚓ {p.analise_ia}")
        lines.append("")
    return "\n".join(lines)

# =====================================================================================
# INTEGRAÇÃO DO PARSER MPO
# =====================================================================================
def run_mpo_parser_on_zip(zip_bytes: bytes) -> List[Publicacao]:
    """
    Roda o parser especializado no ZIP em memória.
    """
    if not mb_portaria_parser:
        return []
    
    results = []
    try:
        # Usa BytesIO para simular arquivo
        zip_io = io.BytesIO(zip_bytes)
        
        # Chama a função do parser
        agg, pid_to_hint = mb_portaria_parser.parse_zip_in_memory(zip_io)
        
        for pid, rows in agg.items():
            hint = pid_to_hint.get(pid, "Ato orçamentário MPO")
            
            # O parser 'render_whatsapp_block' deve retornar apenas os valores/saldo
            analysis_text = mb_portaria_parser.render_whatsapp_block(pid, hint, rows)
            
            # Formatação do Título
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
    if "comando da aeronáutica" in organ_lower or "comando do exército" in organ_lower:
        return None
        
    section = (main_article.get("pubName", "") or "").upper()
    body = main_article.find("body")
    if not body: return None
        
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
        
        # MPO Genérico (se não foi pego pelo parser especializado)
        if is_mpo:
            found_navy_codes = [code for code in MPO_NAVY_TAGS if code.lower() in search_content_lower]
            if found_navy_codes:
                is_relevant = True
                is_mpo_navy_hit_flag = True
                reason = "Ato do MPO com menção a códigos da Marinha (Texto genérico)."

        # Keywords de Interesse Direto
        if not is_relevant:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break
        
        # Keywords de Orçamento (atos do MPO/SOF que merecem ALWAYS ON)
        if not is_relevant and (is_mpo or "ministério da fazenda" in organ.lower()):
            if any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
            is_relevant = True
            # Marca como "hit" para NÃO ser filtrado pela IA mesmo que ela diga "sem impacto"
            is_mpo_navy_hit_flag = True  
            reason = "MB: para conhecimento. Ato orçamentário do MPO (SOF) relevante para execução/limites."

    elif "DO2" in section:
        try: soup_copy = BeautifulSoup(full_text_content, "lxml-xml")
        except: soup_copy = BeautifulSoup(full_text_content, "html.parser")

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
    
    if custom_keywords:
        for kw in custom_keywords:
            if kw and kw.lower() in search_content_lower:
                is_relevant = True
                reason = f"Há menção à palavra-chave personalizada: '{kw}'."
                break
            
    if is_relevant:
        try: soup_full_clean = BeautifulSoup(full_text_content, "lxml-xml")
        except: soup_full_clean = BeautifulSoup(full_text_content, "html.parser")
            
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
        raise HTTPException(500, "Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(502, f"Falha de login no INLABS: HTTP {r.status_code}")
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
    if rr.status_code == 200: return fallback_url
    raise HTTPException(404, f"Não encontrei a pasta/listagem da data {date}.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    base_url = await resolve_date_url(client, date)
    r = await client.get(base_url)
    if r.status_code >= 400: raise HTTPException(502, f"Falha ao abrir listagem {base_url}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
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
    if r.status_code >= 400: raise HTTPException(502, f"Falha ao baixar ZIP {url}")
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.lower().endswith(".xml"):
                    xml_blobs.append(z.read(name))
    except zipfile.BadZipFile: pass
    return xml_blobs

# =====================================================================================
# /processar-inlabs (PRINCIPAL)
# =====================================================================================
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2"),
    keywords_json: Optional[str] = Form(None),
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    custom_keywords: List[str] = []
    if keywords_json:
        try:
            kl = json.loads(keywords_json)
            if isinstance(kl, list): custom_keywords = [str(k).strip().lower() for k in kl if str(k).strip()]
        except: pass

    pubs_final: List[Publicacao] = []
    usou_fallback = False
    erro_principal = ""

    print(f">>> Tentando InLabs (Principal) para {data}...")
    try:
        client = await inlabs_login_and_get_session()
        try:
            listing_url = await resolve_date_url(client, data)
            html = await fetch_listing_html(client, data)
            zip_links = pick_zip_links_from_listing(html, listing_url, secs)
            
            if not zip_links: raise HTTPException(404, detail="ZIPs não encontrados.")

            for zurl in zip_links:
                print(f"Baixando {zurl}...")
                zb = await download_zip(client, zurl)
                
                # --- PASSO A: RODA O PARSER ESPECIALIZADO DE PORTARIAS ---
                if mb_portaria_parser:
                    print("Rodando Parser MPO especializado...")
                    mpo_pubs = run_mpo_parser_on_zip(zb)
                    pubs_final.extend(mpo_pubs)
                
                # --- PASSO B: RODA O PARSER GENÉRICO (XML) ---
                all_xml_blobs = extract_xml_from_zip(zb)
                materias: Dict[str, Dict[str, Any]] = {}
                for blob in all_xml_blobs:
                    try:
                        soup = BeautifulSoup(blob, "lxml-xml")
                        article = soup.find("article")
                        if not article: continue
                        materia_id = article.get("idMateria")
                        if not materia_id: continue
                        if materia_id not in materias:
                            materias[materia_id] = {"main_article": None, "full_text": ""}
                        materias[materia_id]["full_text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                        body = article.find("body")
                        if body and body.find("Identifica"):
                            materias[materia_id]["main_article"] = article
                    except: continue
                
                for materia_id, content in materias.items():
                    if content["main_article"]:
                        publication = process_grouped_materia(content["main_article"], content["full_text"], custom_keywords)
                        if publication:
                            # Evita duplicata se o Parser Especializado já pegou
                            is_dup = any(p.type == publication.type for p in pubs_final if p.is_parsed_mpo)
                            if not is_dup:
                                pubs_final.append(publication)
        finally:
            await client.aclose()

    except Exception as e:
        print(f"⚠️ Falha no InLabs: {e}")
        erro_principal = str(e)
        usou_fallback = True

    # --- TENTATIVA 2: FALLBACK ---
    if usou_fallback and executar_fallback:
        print(f">>> Acionando Fallback (in.gov.br) para {data}...")
        try:
            fb_results = await executar_fallback(data, custom_keywords)
            for item in fb_results:
                pubs_final.append(Publicacao(
                    organ=item['organ'], type=item['type'], summary=item['summary'],
                    raw=item['raw'], relevance_reason=item['relevance_reason'] + " (Fallback)",
                    section=item['section'], clean_text=item['raw'], is_parsed_mpo=False
                ))
        except Exception as e_fb:
            print(f"Erro no Fallback: {e_fb}")
            if not pubs_final: raise HTTPException(500, detail=f"Erro InLabs ({erro_principal}) E Erro Fallback ({e_fb})")

    # --- Deduplicação e Retorno ---
    seen: Set[str] = set()
    merged: List[Publicacao] = []
    for p in pubs_final:
        key = f"{p.organ}-{p.type}-{str(p.summary)[:50]}"
        if key not in seen:
            seen.add(key)
            merged.append(p)

    texto = monta_whatsapp(merged, data)
    if usou_fallback: texto = "⚠️ *Aviso: Sistema InLabs indisponível. Dados via Portal Público.*\n\n" + texto

    return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)

# =====================================================================================
# /processar-dou-ia (COM IA + MPO INTEGRADO)
# =====================================================================================
@app.post("/processar-dou-ia", response_model=ProcessResponse)
async def processar_dou_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2"),
    keywords_json: Optional[str] = Form(None),
):
    if not GEMINI_API_KEY: raise HTTPException(500, detail="GEMINI_API_KEY não definida.")
    try: model = genai.GenerativeModel("gemini-2.5-flash") 
    except Exception as e: raise HTTPException(500, detail=f"Falha IA: {e}")

    # Chama o fluxo padrão (que já traz o MPO parseado)
    res_padrao = await processar_inlabs(data, sections, keywords_json)
    
    pubs_analisadas = []
    tasks = []
    
    for p in res_padrao.publications:
        # Se veio do Parser Especializado, NÃO gasta token de IA, já está pronto
        if p.is_parsed_mpo:
            pubs_analisadas.append(p)
            continue
            
        # Se for publicação genérica, manda pra IA
        prompt = GEMINI_MPO_PROMPT if p.is_mpo_navy_hit else GEMINI_MASTER_PROMPT
        tasks.append(analyze_single_pub(p, model, prompt))

    results = await asyncio.gather(*tasks)
    for p_res in results:
        if p_res: pubs_analisadas.append(p_res)
            
    texto_final = monta_whatsapp(pubs_analisadas, data)
    return ProcessResponse(date=data, count=len(pubs_analisadas), publications=pubs_analisadas, whatsapp_text=texto_final)

async def analyze_single_pub(pub: Publicacao, model, prompt_template):
    try:
        analysis = await get_ai_analysis(pub.clean_text or pub.raw, model, prompt_template)
        if analysis:
            if "sem impacto" in analysis.lower() and not pub.is_mpo_navy_hit: return None
            pub.relevance_reason = analysis
        return pub
    except: return pub

async def get_ai_analysis(clean_text: str, model: genai.GenerativeModel, prompt_template: str) -> Optional[str]:
    try:
        prompt = f"{prompt_template}\n\n{clean_text[:8000]}"
        response = await model.generate_content_async(prompt)
        return norm(response.text)
    except Exception as e:
        print(f"Erro IA: {e}")
        return None

# =====================================================================================
# OUTROS ENDPOINTS (Valor, Fallback Teste, PAC, Legislativo)
# =====================================================================================

@app.post("/processar-valor-ia", response_model=ProcessResponseValor)
async def processar_valor_ia(data: str = Form(...)):
    # Importa a função do check_valor.py ou api.py (definida acima)
    from api import run_valor_analysis, monta_valor_whatsapp
    pubs_list, _ = await run_valor_analysis(data, use_state=False)
    pubs_model = [ValorPublicacao(**p) for p in pubs_list]
    return ProcessResponseValor(date=data, count=len(pubs_model), publications=pubs_model, whatsapp_text=monta_valor_whatsapp(pubs_model, data))

@app.post("/teste-fallback", response_model=ProcessResponse)
async def teste_fallback(data: str = Form(...), keywords_json: Optional[str] = Form(None)):
    if not executar_fallback: raise HTTPException(500, detail="Módulo 'dou_fallback.py' não encontrado.")
    custom_keywords = []
    if keywords_json:
        try:
            kl = json.loads(keywords_json)
            if isinstance(kl, list): custom_keywords = [str(k).strip().lower() for k in kl if str(k).strip()]
        except: pass
    try:
        fb_results = await executar_fallback(data, custom_keywords)
    except Exception as e: raise HTTPException(500, detail=str(e))
    
    pubs = [Publicacao(organ=i['organ'], type=i['type'], summary=i['summary'], raw=i['raw'], relevance_reason=i['relevance_reason'], section=i['section'], clean_text=i['raw']) for i in fb_results]
    return ProcessResponse(date=data, count=len(pubs), publications=pubs, whatsapp_text=monta_whatsapp(pubs, data))

# --- Crawler Valor ---
async def crawl_valor_headlines(cover_url: str, date_str: str) -> List[Dict[str, str]]:
    print(f"[Valor Crawler] Acessando capa: {cover_url}")
    found_articles = []
    date_clean = date_str.replace("-", "") 
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
            r = await client.get(cover_url, headers=headers)
            if r.status_code != 200: return []
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                title = norm(a.get_text())
                if date_clean in href and len(href) > len(f"/impresso/{date_clean}/"):
                    full_link = href if href.startswith("http") else f"https://valor.globo.com{href}"
                    if title and len(title) > 10 and not any(f['link'] == full_link for f in found_articles):
                         found_articles.append({"title": title, "link": full_link})
            return found_articles
        except Exception: return []

SEARCH_QUERIES = ['"contas publicas" OR "politica fiscal"', '"orcamento" OR "LDO" OR "LOA"', '"economia" OR "defesa" OR "marinha"']

async def run_valor_analysis(today_str: str, use_state: bool = True) -> (List[Dict[str, Any]], Set[str]):
    if not GEMINI_API_KEY: return [], set()
    genai.configure(api_key=GEMINI_API_KEY)
    try: model = genai.GenerativeModel("gemini-2.5-flash")
    except: return [], set()
    date_suffix = today_str.replace("-", "")
    google_results = []
    for q in SEARCH_QUERIES:
        try:
            res = await perform_google_search(q, search_date=today_str)
            google_results.extend(res)
        except: pass
        await asyncio.sleep(1)
    
    final_articles, processed_links = [], set()
    for res in google_results:
        if res.link.rstrip("/").endswith(date_suffix):
            crawled = await crawl_valor_headlines(res.link, today_str)
            for news in crawled:
                if news['link'] not in processed_links:
                    final_articles.append(news); processed_links.add(news['link'])
        else:
            if res.link not in processed_links:
                final_articles.append({"title": res.title, "link": res.link}); processed_links.add(res.link)
    
    pubs_finais, links_encontrados = [], set()
    for item in final_articles:
        text_check = item['title'].lower()
        if any(k in text_check for k in ["orçamento", "fiscal", "defesa", "marinha", "gasto", "corte", "economia"]):
            ai_reason = await get_ai_analysis(f"TÍTULO: {item['title']}", model, GEMINI_VALOR_PROMPT)
            links_encontrados.add(item['link'])
            if ai_reason and "sem impacto" not in ai_reason.lower():
                pubs_finais.append({"titulo": item['title'], "link": item['link'], "analise_ia": ai_reason})
    return pubs_finais, links_encontrados

# --- PAC ---
PROGRAMAS_ACOES_PAC = {
    'PROSUB': {'123G': 'ESTALEIRO E BASE NAVAL', '123H': 'SUBMARINO NUCLEAR', '123I': 'SUBMARINOS CONVENCIONAIS'},
    'PNM': {'14T7': 'TECNOLOGIA NUCLEAR'}, 'PRONAPA': {'1N47': 'NAVIOS-PATRULHA'}
}

async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    try:
        df_detalhado = await asyncio.to_thread(despesa_detalhada, exercicio=ano, acao=acao_cod, inclui_descricoes=True, ignore_secure_certificate=True)
        if df_detalhado.empty: return None
        cols_possiveis = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago', 'dotacao_disponivel', 'saldo_disponivel', 'saldo_dotacao']
        colunas = [c for c in cols_possiveis if c in df_detalhado.columns]
        if not colunas: return None
        totais = df_detalhado[colunas].sum().to_dict()
        totais['Acao_cod'] = acao_cod
        if 'dotacao_disponivel' not in totais:
             totais['dotacao_disponivel'] = totais.get('saldo_disponivel') or totais.get('saldo_dotacao') or 0.0
        return totais
    except: return None
 
@app.get("/api/pac-data/historical-dotacao")
async def get_pac_historical():
    """Retorna o JSON de cache histórico para o gráfico."""
    try:
        if os.path.exists(HISTORICAL_CACHE_PATH):
            with open(HISTORICAL_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            # Se não existir, tenta gerar agora ou retorna vazio
            return {"labels": [], "datasets": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pac-data/{ano}")
async def get_pac_data(ano: int = Path(..., ge=2010, le=2025)):
    tasks = []
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        for acao in acoes.keys(): tasks.append(buscar_dados_acao_pac(ano, acao))
    results = await asyncio.gather(*tasks)
    dados = [r for r in results if r]
    if not dados: return []

    tabela = []
    total = {'LOA':0,'DOTAÇÃO ATUAL':0,'DISPONÍVEL':0,'EMPENHADO (c)':0,'LIQUIDADO':0,'PAGO':0}
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        soma = total.copy()
        soma = {k:0 for k in total}
        linhas = []
        for cod, desc in acoes.items():
            row = next((d for d in dados if d.get('Acao_cod') == cod), None)
            def gv(k): return float(row.get(k, 0.0)) if row else 0.0
            vals = {'LOA':gv('loa'),'DOTAÇÃO ATUAL':gv('loa_mais_credito'),'DISPONÍVEL':gv('dotacao_disponivel'),'EMPENHADO (c)':gv('empenhado'),'LIQUIDADO':gv('liquidado'),'PAGO':gv('pago')}
            linhas.append({'PROGRAMA': None, 'AÇÃO': f"{cod} - {desc}", **vals})
            for k,v in vals.items(): soma[k]+=v
        tabela.append({'PROGRAMA': prog, 'AÇÃO': None, **soma})
        tabela.extend(linhas)
        for k,v in soma.items(): total[k]+=v
    tabela.append({'PROGRAMA': 'Total Geral', 'AÇÃO': None, **total})
    return tabela
    
@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    await update_pac_historical_cache()
    return {"status": "OK"}

# --- LEGISLATIVO ---
@app.post("/processar-legislativo")
async def endpoint_legislativo(days: int = Form(5)):
    try:
        func = globals().get('check_and_process_legislativo')
        if not func:
            import check_legislativo
            func = check_legislativo.check_and_process_legislativo
        res = await func(only_new=False, days_back=days)
        if not res: return {"count": 0, "message": "Nenhuma proposição encontrada.", "data": []}
        return {"count": len(res), "message": f"Encontradas {len(res)} proposições.", "data": res}
    except Exception as e:
        print(f"Erro Legis: {e}")
        raise HTTPException(500, str(e))

@app.get("/health")
async def health(): return {"status": "ok", "ts": datetime.now().isoformat()}

@app.get("/test-ia")
async def test_ia():
    if not GEMINI_API_KEY: raise HTTPException(500, "Sem Key")
    try:
        m = genai.GenerativeModel("gemini-1.5-pro")
        r = await m.generate_content_async("Teste")
        return {"ok": True, "resp": r.text}
    except Exception as e: return {"ok": False, "err": str(e)}

# --- STATIC ---
if os.path.isdir("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else: print("⚠️ Pasta 'static' não encontrada.")
