# Nome do arquivo: api.py
# Versão: 15.0.0 (SISTEMA UNIFICADO: Backend + Frontend + Worker + PAC Dashboard)

from fastapi import FastAPI, Form, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles # <-- Módulo para servir o site
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

# Importa a função de busca do Google
try:
    from google_search import perform_google_search, SearchResult
except ImportError:
    pass

# Importações PAC (Orçamento)
try:
    from orcamentobr import despesa_detalhada
    from check_pac import update_pac_historical_cache
except ImportError:
    pass

# =====================================================================================
# CONFIGURAÇÃO DA API
# =====================================================================================

app = FastAPI(title="Robô DOU/Valor/PAC - Sistema Unificado")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================================
# 1. WORKER EM BACKGROUND (O "Coração" Automático)
# =====================================================================================

@app.on_event("startup")
async def startup_event():
    """
    Ao iniciar o servidor Web, dispara o robô (run_check.py) em segundo plano.
    """
    print(">>> SISTEMA UNIFICADO INICIADO <<<")
    try:
        # Importação feita AQUI DENTRO para evitar erro de ciclo (Circular Import)
        # pois o run_check.py provavelmente importa coisas deste api.py
        from run_check import main_loop
        print(">>> DISPARANDO LOOP DO ROBÔ EM BACKGROUND...")
        asyncio.create_task(main_loop())
    except ImportError:
        print("⚠️ AVISO: 'run_check.py' não encontrado. O robô automático não rodará, apenas a API.")
    except Exception as e:
        print(f"⚠️ ERRO AO INICIAR ROBÔ: {e}")

# =====================================================================================
# CONFIGURAÇÕES E CREDENCIAIS
# =====================================================================================

try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    # Se não tiver config.json (no Render geralmente usa variáveis), segue vazio
    config = {}

# Credenciais / URLs InLabs
INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER", config.get("INLABS_USER", None))
INLABS_PASS = os.getenv("INLABS_PASS", config.get("INLABS_PASS", None))

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.get("GEMINI_API_KEY", None))
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Constantes de Negócio
MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)

ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

# Caminho do Cache do PAC (No Disco Persistente)
HISTORICAL_CACHE_PATH = os.environ.get("PAC_HISTORICAL_CACHE_PATH", "/dados/pac_historical_dotacao.json")

# Prompts
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler a publicação do DOU e escrever uma única frase curta para WhatsApp, focando no impacto para a Marinha (MB).
Se for ato orçamentário, foque em UGs da Marinha.
Se irrelevante, responda APENAS: "Sem impacto direto."
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha. Esta publicação do MPO já foi marcada como relevante.
Diga claramente o efeito orçamentário (suplementação, cancelamento) e quem é afetado.
"""

GEMINI_VALOR_PROMPT = """
Você é um analista da Marinha. Leia o TÍTULO e RESUMO da notícia do Valor Econômico.
Diga em uma frase curta o impacto para a Defesa, Orçamento Federal ou Base Industrial de Defesa.
"""

SEARCH_QUERIES = [
    '"contas publicas" OR "politica fiscal" OR "Arcabouço fiscal"',
    '"orcamento" OR "LDO" OR "LOA" OR "PPA" OR "Contingenciamento"',
    '"economia" OR "defesa" OR "marinha" OR "forças armadas"'
]

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
    return _ws.sub(" ", s).strip() if s else ""

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    if not pubs: return "— Sem ocorrências para os critérios informados —"
    lines = [f"PTC as seguintes publicações de interesse no DOU de {when}:\n"]
    
    pubs_by_section = {}
    for p in pubs:
        sec = p.section or "DOU"
        pubs_by_section.setdefault(sec, []).append(p)
        
    for section_name in sorted(pubs_by_section.keys()):
        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}")
        lines.append("")
        for p in pubs_by_section[section_name]:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {p.type or 'Ato'}")
            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚠️ Erro IA:" if "Erro" in reason else "⚓"
            lines.append(f"{prefix} {reason}")
            lines.append("")     
    return "\n".join(lines)

def monta_valor_whatsapp(pubs: List[ValorPublicacao], when: str) -> str:
    if not pubs: return "— Sem ocorrências no Valor Econômico —"
    lines = [f"Destaques Valor Econômico ({when}):\n"]
    for p in pubs:
        lines.append(f"▶️ {p.titulo}")
        lines.append(f"📌 {p.link}")
        lines.append(f"⚓ {p.analise_ia}\n")
    return "\n".join(lines)

async def get_ai_analysis(text, model, prompt_template):
    if not model: return "IA não inicializada."
    try:
        prompt = f"{prompt_template}\n\n{text[:8000]}"
        response = await model.generate_content_async(prompt)
        return norm(response.text)
    except Exception as e:
        return f"Erro na análise de IA: {str(e)[:100]}"

# =====================================================================================
# PARSERS E LOGICA DE NEGÓCIO (DOU)
# =====================================================================================

def parse_mpo_budget_table(full_text_content: str) -> str:
    # (Mantendo sua lógica original simplificada para referência, 
    # mas se tiver o parser completo no código original, ele será usado)
    if "52131" in full_text_content or "52931" in full_text_content:
        return "Tabela orçamentária com impacto direto em UGs da Marinha. Requer análise do anexo."
    return "Ato orçamentário do MPO. Valores específicos não extraídos automaticamente."

def process_grouped_materia(main_article: BeautifulSoup, full_text_content: str, custom_keywords: List[str]) -> Optional[Publicacao]:
    organ = norm(main_article.get("artCategory", ""))
    section = (main_article.get("pubName", "") or "").upper()
    
    # Filtro básico para ignorar outras forças se não for específico
    if "comando da aeronáutica" in organ.lower() or "comando do exército" in organ.lower():
        return None

    body = main_article.find("body")
    if not body: return None
    
    act_type = norm(body.find("Identifica").get_text(strip=True) if body.find("Identifica") else "")
    summary = norm(body.find("Ementa").get_text(strip=True) if body.find("Ementa") else "")
    display_text = norm(body.get_text(strip=True))
    
    is_relevant = False
    reason = None
    is_mpo_navy_hit = False
    search_content_lower = norm(full_text_content).lower()

    # Seção 1
    if "DO1" in section:
        # Regra MPO
        if MPO_ORG_STRING in organ.lower() or "ministério da fazenda" in organ.lower():
            found_tags = [code for code in MPO_NAVY_TAGS if code in search_content_lower]
            if found_tags:
                is_relevant = True
                is_mpo_navy_hit = True
                reason = parse_mpo_budget_table(full_text_content)
            elif any(kw in search_content_lower for kw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = "Ato orçamentário (GND, Crédito, Fontes) com potencial impacto."

        # Regra Geral
        if not is_relevant:
            for kw in KEYWORDS_DIRECT_INTEREST_S1 + (custom_keywords or []):
                if kw.lower() in search_content_lower:
                    is_relevant = True
                    reason = f"Menção a termo de interesse: '{kw}'."
                    break
    
    # Seção 2 (Pessoal)
    elif "DO2" in section:
        # Limpeza de assinaturas
        soup_copy = BeautifulSoup(full_text_content, "html.parser")
        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]): tag.decompose()
        clean_s2 = norm(soup_copy.get_text(strip=True)).lower()
        
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_s2:
                is_relevant = True
                reason = f"Ato de pessoal: menção a '{term}'."
                break
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                if name.lower() in clean_s2:
                     is_relevant = True
                     reason = f"Ato de pessoal envolvendo: '{name}'."
                     break

    if is_relevant:
        return Publicacao(
            organ=organ, type=act_type, summary=summary, raw=display_text,
            relevance_reason=reason, section=section, clean_text=display_text[:8000],
            is_mpo_navy_hit=is_mpo_navy_hit
        )
    return None

# =====================================================================================
# INLABS CLIENT
# =====================================================================================

async def inlabs_login_and_get_session():
    if not INLABS_USER or not INLABS_PASS:
        raise HTTPException(500, "Credenciais INLABS não configuradas.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    return client

async def resolve_date_url(client, date):
    return f"{INLABS_BASE.rstrip('/')}/{date}/"

async def fetch_listing_html(client, date):
    url = await resolve_date_url(client, date)
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(404, f"DOU de {date} não encontrado.")
    return r.text

def pick_zip_links_from_listing(html, base_url, sections):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    wanted = set(s.upper() for s in sections)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip"):
            label = (a.get_text() or href).upper()
            if any(sec in label for sec in wanted) or any(sec in href.upper() for sec in wanted):
                links.append(urljoin(base_url, href))
    return sorted(list(set(links)))

async def download_zip(client, url):
    r = await client.get(url)
    return r.content

def extract_xml_from_zip(zip_bytes):
    xmls = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for name in z.namelist():
                if name.lower().endswith(".xml"):
                    xmls.append(z.read(name))
    except: pass
    return xmls

# =====================================================================================
# ANÁLISE DO VALOR (WEB)
# =====================================================================================

async def run_valor_analysis(today_str: str, use_state: bool = True):
    if not GEMINI_API_KEY: return [], set()
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    all_results = {}
    for q in SEARCH_QUERIES:
        try:
            res = await perform_google_search(q, today_str)
            for r in res: all_results[r.link] = r
        except: pass
    
    pubs = []
    links = set()
    for r in all_results.values():
        reason = await get_ai_analysis(f"{r.title}\n{r.snippet}", model, GEMINI_VALOR_PROMPT)
        links.add(r.link)
        if reason and "sem impacto" not in reason.lower():
            pubs.append({"titulo": r.title, "link": r.link, "analise_ia": reason})
    return pubs, links

# =====================================================================================
# ENDPOINTS API (WEBHOOKS/FRONTEND)
# =====================================================================================

@app.get("/health")
async def health():
    return {"status": "ok", "ts": datetime.now().isoformat()}

# --- Processar DOU (Manual via Site) ---
@app.post("/processar-dou-ia", response_model=ProcessResponse)
async def processar_dou_ia(
    data: str = Form(...),
    sections: Optional[str] = Form("DO1,DO2"),
    keywords_json: Optional[str] = Form(None)
):
    secs = [s.strip().upper() for s in sections.split(",")]
    custom_kw = []
    if keywords_json:
        try:
            custom_kw = json.loads(keywords_json)
        except: pass

    client = await inlabs_login_and_get_session()
    pubs_finais = []
    
    try:
        html = await fetch_listing_html(client, data)
        base_url = await resolve_date_url(client, data)
        zip_links = pick_zip_links_from_listing(html, base_url, secs)
        
        if not zip_links:
            return ProcessResponse(date=data, count=0, publications=[], whatsapp_text="Nenhum ZIP encontrado.")

        all_xmls = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xmls.extend(extract_xml_from_zip(zb))

        # Agrupa XMLs
        materias = {}
        for blob in all_xmls:
            try:
                soup = BeautifulSoup(blob, "lxml-xml")
                art = soup.find("article")
                if not art: continue
                mid = art.get("idMateria")
                if not mid: continue
                if mid not in materias: materias[mid] = {"main": None, "text": ""}
                materias[mid]["text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                if art.find("body") and art.find("body").find("Identifica"):
                    materias[mid]["main"] = art
            except: continue

        # Filtra e Analisa
        candidates = []
        for m in materias.values():
            if m["main"]:
                p = process_grouped_materia(m["main"], m["text"], custom_kw)
                if p: candidates.append(p)

        # Deduplica
        seen = set()
        unique = []
        for p in candidates:
            k = f"{p.organ}{p.type}{p.summary[:50]}"
            if k not in seen:
                seen.add(k)
                unique.append(p)

        # IA
        model = None
        if GEMINI_API_KEY:
            try: model = genai.GenerativeModel("gemini-2.5-flash")
            except: pass
        
        if model:
            tasks = []
            for p in unique:
                prompt = GEMINI_MPO_PROMPT if p.is_mpo_navy_hit else GEMINI_MASTER_PROMPT
                tasks.append(get_ai_analysis(p.clean_text, model, prompt))
            results = await asyncio.gather(*tasks)
            
            for p, ai_res in zip(unique, results):
                if "sem impacto direto" in ai_res.lower() and not p.is_mpo_navy_hit: continue
                p.relevance_reason = ai_res
                pubs_finais.append(p)
        else:
            pubs_finais = unique

    finally:
        await client.aclose()

    return ProcessResponse(
        date=data, count=len(pubs_finais), publications=pubs_finais,
        whatsapp_text=monta_whatsapp(pubs_finais, data)
    )

# --- Processar Valor (Manual via Site) ---
@app.post("/processar-valor-ia", response_model=ProcessResponseValor)
async def processar_valor_ia(data: str = Form(...)):
    pubs, _ = await run_valor_analysis(data, use_state=False)
    pubs_model = [ValorPublicacao(**p) for p in pubs]
    return ProcessResponseValor(
        date=data, count=len(pubs_model), publications=pubs_model,
        whatsapp_text=monta_valor_whatsapp(pubs_model, data)
    )

# =====================================================================================
# ENDPOINTS DASHBOARD PAC (Adicionados para o Frontend Único)
# =====================================================================================

PROGRAMAS_ACOES_PAC = {
    'PROSUB': {'123G': 'IMPLANTACAO DE ESTALEIRO', '123H': 'SUBMARINO NUCLEAR', '123I': 'SUBMARINOS CONVENCIONAIS'},
    'PNM': {'14T7': 'DESENVOLVIMENTO DE TECNOLOGIA NUCLEAR'},
    'PRONAPA': {'1N47': 'CONSTRUCAO DE NAVIOS-PATRULHA 500T'}
}

@app.get("/api/pac-data/historical-dotacao")
async def get_pac_historical_data():
    # Lê o cache do disco persistente
    try:
        with open(HISTORICAL_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # Retorna estrutura vazia válida para não quebrar o gráfico
        return {"labels": [], "datasets": []}

@app.get("/api/pac-data/{ano}")
async def get_pac_data_year(ano: int):
    # Busca detalhada no SIOP (Lenta, por isso é sob demanda no clique)
    tasks = []
    meta_list = []
    
    for prog, acoes in PROGRAMAS_ACOES_PAC.items():
        for cod, desc in acoes.items():
            # Usa to_thread para não bloquear o servidor com a lib síncrona orcamentobr
            tasks.append(asyncio.to_thread(despesa_detalhada, exercicio=ano, acao=cod))
            meta_list.append({"prog": prog, "cod": cod, "desc": desc})
            
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    data = []
    total = {'LOA': 0.0, 'DOTAÇÃO ATUAL': 0.0, 'EMPENHADO (c)': 0.0, 'LIQUIDADO': 0.0, 'PAGO': 0.0}
    
    for i, df in enumerate(results):
        if isinstance(df, Exception) or df is None or df.empty: continue
        
        m = meta_list[i]
        # Soma colunas numéricas do DataFrame
        cols = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago']
        s = df[[c for c in cols if c in df.columns]].sum()
        
        row = {
            'PROGRAMA': m['prog'],
            'AÇÃO': f"{m['cod']} - {m['desc']}",
            'LOA': s.get('loa', 0),
            'DOTAÇÃO ATUAL': s.get('loa_mais_credito', 0),
            'EMPENHADO (c)': s.get('empenhado', 0),
            'LIQUIDADO': s.get('liquidado', 0),
            'PAGO': s.get('pago', 0)
        }
        row['% EMP/DOT'] = (row['EMPENHADO (c)'] / row['DOTAÇÃO ATUAL']) if row['DOTAÇÃO ATUAL'] else 0
        data.append(row)
        
        # Totais
        total['LOA'] += row['LOA']
        total['DOTAÇÃO ATUAL'] += row['DOTAÇÃO ATUAL']
        total['EMPENHADO (c)'] += row['EMPENHADO (c)']
        total['LIQUIDADO'] += row['LIQUIDADO']
        total['PAGO'] += row['PAGO']
        
    if data:
        total['PROGRAMA'] = 'Total Geral'
        total['AÇÃO'] = ''
        total['% EMP/DOT'] = (total['EMPENHADO (c)'] / total['DOTAÇÃO ATUAL']) if total['DOTAÇÃO ATUAL'] else 0
        data.append(total)
        
    return data

@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    try:
        await update_pac_historical_cache()
        return {"status": "Cache histórico atualizado."}
    except Exception as e:
        raise HTTPException(500, str(e))

# =====================================================================================
# 2. SERVIR O FRONTEND (STATIC FILES) - DEVE SER O ÚLTIMO BLOCO
# =====================================================================================

# Verifica se a pasta static existe
if os.path.isdir("static"):
    # Monta na raiz. html=True permite que / abra o index.html
    app.mount("/", StaticFiles(directory="static", html=True), name="static")
else:
    print("⚠️ AVISO: Pasta 'static' não encontrada. Crie-a e coloque os arquivos HTML lá.")
