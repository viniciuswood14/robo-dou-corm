# Nome do arquivo: dou_pdf_reader.py
# Versão: 8.0 (Smart Crawler - Prioriza Edição Normal vs Extra)

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
# 2. PROMPTS
# ==============================================================================

PROMPT_ESPECIALISTA_MPO = """
Você é um extrator de dados. NÃO converse.

ANALISE ESTA PÁGINA (MPO/FAZENDA).
REGRAS:
1. Busque UGs: 52131, 52133, 52232, 52233, 52931, 52932, 52000.
2. Extraia: Valor, Ação, Tipo (Suplementação/Cancelamento) e Nº do Pedido/NUP.
3. Se for MPO/MF mas SEM citação da MB -> Responda apenas: TIPO 5.

SAÍDA OBRIGATÓRIA (Use exatamente este layout):
▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA OU ATO]
[Resumo técnico direto de 1 linha]
⚓ MB: [Se houver impacto: Detalhes financeiros] [Se não houver: Para conhecimento. Sem impacto.]
"""

PROMPT_GERAL_MB = """
Você é um filtro de inteligência.
Se houver menção explícita a "Marinha", "Defesa", "Submarino" ou "Nuclear":
Gere o resumo no layout abaixo.
CASO CONTRÁRIO, RESPONDA APENAS: NULL

SAÍDA:
▶️ [Órgão]
📌 [Título]
[Resumo técnico de 1 linha]
⚓ MB: [Análise de Impacto]
"""

# ==============================================================================
# 3. FUNÇÕES (SMART CRAWLER)
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
        # Login
        print(f"[PDF] Logando no InLabs ({INLABS_USER})...")
        await client.get(INLABS_BASE_URL)
        await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS, "senha": INLABS_PASS})
        
        # Acessa Página do Dia
        day_url = f"{INLABS_BASE_URL}/index.php?p={date_str}"
        print(f"[PDF] Acessando índice: {day_url}")
        resp_page = await client.get(day_url)
        
        soup = BeautifulSoup(resp_page.text, "html.parser")
        
        # --- LÓGICA DE SELEÇÃO INTELIGENTE (NOVO) ---
        candidates = []
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            # Filtra tudo que é PDF da Seção 1
            if ".pdf" in href and ("do1" in href or "secao_1" in href):
                candidates.append(a["href"]) # Guarda o link original (case sensitive)

        if not candidates:
            # Fallback direto se não achar nada no HTML
            print("[PDF] Nenhum link encontrado no Crawler. Tentando força bruta...")
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            target_href = f"index.php?p={date_str}&dl={dt.strftime('%Y_%m_%d')}_ASSINADO_do1.pdf"
        else:
            # Seleciona o melhor candidato
            target_href = None
            
            # Prioridade 1: Link que NÃO tem "extra" e NÃO tem "suplemento"
            for c in candidates:
                if "extra" not in c.lower() and "suplemento" not in c.lower():
                    target_href = c
                    print(f"[PDF] Edição Principal detectada: {c}")
                    break
            
            # Prioridade 2: Se não achou principal, pega o primeiro da lista (pode ser Extra)
            if not target_href:
                target_href = candidates[0]
                print(f"[PDF] Apenas edições extras/suplementares encontradas. Usando: {target_href}")

        final_url = urljoin(INLABS_BASE_URL, target_href)
        print(f"[PDF] Baixando: {final_url}")
        
        resp_file = await client.get(final_url)
        
        if "text/html" in resp_file.headers.get("content-type", "") or len(resp_file.content) < 15000:
            raise ValueError("Falha: InLabs retornou HTML ou arquivo inválido (Login caiu ou arquivo não existe).")

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
    
    for i, page in enumerate(doc):
        text_lower = extract_text_from_page(page).lower()
        
        is_mpo_mf = any(t in text_lower for t in mpo_triggers)
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
        
        if not analysis or "NULL" in analysis or len(analysis) < 10: return None

        lines = analysis.split("\n")
        organ = "DOU"
        title = f"Página {page_num}"
        clean_lines = []

        for line in lines:
            line = line.strip()
            if not line: continue
            if line.startswith("▶️"): organ = line.replace("▶️", "").strip()
            elif line.startswith("📌"): title = line.replace("📌", "").strip()
            else:
                if not line.startswith("Compreendido") and not line.startswith("Aqui está"):
                    clean_lines.append(line)

        final_summary = "\n".join(clean_lines).strip()

        return {
            "organ": organ, 
            "type": title, 
            "summary": final_summary,
            "relevance_reason": f"Pág {page_num}", 
            "section": "DO1",
            "clean_text": text, 
            "is_mpo_navy_hit": (context_type == "MPO")
        }
    except Exception as e:
        print(f"Erro IA: {e}")
        return None
