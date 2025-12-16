# Nome do arquivo: dou_pdf_reader.py
# Versão: 7.0 (Prompts Rígidos - Estilo Manual)

import fitz  # PyMuPDF
import httpx
import os
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import google.generativeai as genai

# ==============================================================================
# CONFIGURAÇÃO DE CREDENCIAIS
# ==============================================================================
INLABS_USER = os.environ.get("INLABS_USER")
INLABS_PASS = os.environ.get("INLABS_PASS")

if not INLABS_USER or not INLABS_PASS:
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            INLABS_USER = cfg.get("INLABS_USER")
            INLABS_PASS = cfg.get("INLABS_PASS")
    except: pass

INLABS_LOGIN_URL = "https://inlabs.in.gov.br/logar.php" 
INLABS_BASE_URL = "https://inlabs.in.gov.br"

# ==============================================================================
# 1. LISTAS DE INTERESSE
# ==============================================================================
NAVY_UGS = {
    "52131", "52133", "52232", "52233", "52931", "52932", "52000"
}

KEYWORDS_DIRECT = [
    "ministério da defesa", "comando da marinha", "marinha do brasil", 
    "fundo naval", "amazônia azul", "ccçpm", "emgepron", "nuclep", 
    "submarino", "tamandaré", "patrulha", "programa nuclear", "prosub"
]

KEYWORDS_BUDGET = [
    "crédito suplementar", "limite de pagamento", "crédito extraordinário",
    "programação orçamentária", "remanejamento", "alteração de fonte"
]

# ==============================================================================
# 2. PROMPTS RÍGIDOS (LOBOTOMIA NA IA)
# ==============================================================================

# Prompt para MPO/MF (Foco em Orçamento)
PROMPT_ESPECIALISTA_MPO = """
Você é um extrator de dados orçamentários. NÃO converse. NÃO explique.

ANALISE ESTA PÁGINA EM BUSCA DE PORTARIAS DO MPO OU FAZENDA.

REGRAS DE EXTRAÇÃO:
1. Busque menções às UGs: 52131, 52133, 52232, 52233, 52931, 52932, 52000.
2. Se encontrar, extraia: 
   - Número do "Pedido" ou "NUP" (se houver).
   - Ação (ex: 2004, 20RP).
   - Tipo de movimento (Suplementação, Cancelamento, Alteração de Fonte).
3. Se for Portaria do MPO/MF mas NÃO citar a Marinha/Defesa: Classifique como TIPO 5.

SAÍDA OBRIGATÓRIA (Se nada relevante, responda apenas NULL):

▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA]
[Resumo Seco de 1 linha sobre o que a portaria faz]
⚓ MB: [Se houver impacto: "Atendimento do Pedido nº XXX. Ação XXX - Tipo. Valor: R$ XXX"] [Se não houver impacto: "Para conhecimento. Sem impacto para a Marinha."]
"""

# Prompt Geral (Foco em Assuntos Estratégicos)
PROMPT_GERAL_MB = """
Você é um filtro de inteligência. NÃO converse.
Analise o texto. Se houver menção explícita a "Marinha do Brasil", "Comando da Marinha", "Submarino", "Nuclear" ou "Defesa":
Gere o resumo.
CASO CONTRÁRIO, RESPONDA APENAS: NULL

SAÍDA:
▶️ [Órgão]
📌 [Título do Ato]
[Resumo de 1 linha]
⚓ [Impacto para a MB]
"""

# ==============================================================================
# 3. FUNÇÕES (CRAWLER + FILTRO NULL)
# ==============================================================================

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    return date_str

async def download_pdf(date_str: str, filename: str) -> str:
    path = os.path.join("/tmp", filename)
    if os.name == 'nt': path = filename

    if not INLABS_USER or not INLABS_PASS:
        raise ValueError("Credenciais InLabs ausentes.")

    headers = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" }

    async with httpx.AsyncClient(timeout=60, verify=False, headers=headers, follow_redirects=True) as client:
        print(f"[PDF] Logando no InLabs ({INLABS_USER})...")
        await client.get(INLABS_BASE_URL)
        await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS, "senha": INLABS_PASS})
        
        day_url = f"{INLABS_BASE_URL}/index.php?p={date_str}"
        print(f"[PDF] Acessando índice: {day_url}")
        resp_page = await client.get(day_url)
        
        soup = BeautifulSoup(resp_page.text, "html.parser")
        target_href = None
        for a in soup.find_all("a", href=True):
            if ".pdf" in a["href"].lower() and ("do1" in a["href"].lower() or "secao_1" in a["href"].lower()):
                target_href = a["href"]
                break
        
        if not target_href:
            # Fallback forçado
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            target_href = f"index.php?p={date_str}&dl={dt.strftime('%Y_%m_%d')}_ASSINADO_do1.pdf"
        
        final_url = urljoin(INLABS_BASE_URL, target_href)
        print(f"[PDF] Baixando: {final_url}")
        resp_file = await client.get(final_url)
        
        if "text/html" in resp_file.headers.get("content-type", "") or len(resp_file.content) < 15000:
            raise ValueError("Falha: InLabs retornou HTML ou arquivo inválido.")

        with open(path, "wb") as f: f.write(resp_file.content)
        return path

def extract_text_from_page(page) -> str:
    return page.get_text("text")

async def analyze_pdf_content(pdf_path: str, model) -> List[Dict]:
    results = []
    try: doc = fitz.open(pdf_path)
    except: return []
    
    print(f"📄 PDF Aberto. Páginas: {len(doc)}")
    
    tasks = []
    mpo_triggers = ["ministério do planejamento", "ministério da fazenda", "secretaria de orçamento", "tesouro nacional"]
    general_triggers = KEYWORDS_DIRECT + KEYWORDS_BUDGET
    
    # Reduzi para analisar apenas páginas que realmente importam para economizar tempo/token
    # Mas mantendo o pente fino nas de orçamento
    
    for i, page in enumerate(doc):
        text_lower = extract_text_from_page(page).lower()
        
        is_mpo_mf = any(t in text_lower for t in mpo_triggers)
        # Só ativa o geral se tiver keyword MUITO forte
        is_general_interest = False
        if not is_mpo_mf:
            is_general_interest = any(k in text_lower for k in general_triggers)

        if is_mpo_mf or is_general_interest:
            prompt = PROMPT_ESPECIALISTA_MPO if is_mpo_mf else PROMPT_GERAL_MB
            ctx = "MPO" if is_mpo_mf else "GERAL"
            tasks.append(run_gemini_analysis(page.get_text(), model, prompt, i+1, ctx))

    if not tasks:
        doc.close()
        return []

    print(f"[IA] Analisando {len(tasks)} páginas selecionadas...")
    chunk_size = 5
    for i in range(0, len(tasks), chunk_size):
        chunk = tasks[i:i + chunk_size]
        res = await asyncio.gather(*chunk)
        for r in res:
            if r: results.append(r)
                
    doc.close()
    return results

async def run_gemini_analysis(text: str, model, prompt_template: str, page_num: int, context_type: str) -> Optional[Dict]:
    try:
        if len(text) < 100: return None
        full_prompt = f"{prompt_template}\n\n--- PÁGINA {page_num} ---\n{text[:15000]}"
        
        response = await model.generate_content_async(full_prompt)
        analysis = response.text.strip()
        
        # --- FILTRO RIGOROSO ---
        # Se a IA respondeu NULL ou algo vazio, ignoramos
        if not analysis or "NULL" in analysis or len(analysis) < 10:
            return None
            
        # Removemos conversas caso a IA ainda teime em falar
        clean_analysis = analysis.replace("Compreendido.", "").replace("Aqui está o resumo:", "").strip()

        organ = "DOU"
        title = f"Página {page_num}"
        
        for line in clean_analysis.split("\n"):
            if "▶️" in line: organ = line.replace("▶️", "").strip()[:60]
            if "📌" in line: title = line.replace("📌", "").strip()[:100]

        return {
            "organ": organ, 
            "type": title, 
            "summary": clean_analysis, # Manda o texto formatado pelo prompt
            "relevance_reason": f"Pág {page_num}", 
            "section": "DO1",
            "clean_text": text, 
            "is_mpo_navy_hit": (context_type == "MPO")
        }
    except Exception as e:
        print(f"Erro IA: {e}")
        return None
