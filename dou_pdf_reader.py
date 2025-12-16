# Nome do arquivo: dou_pdf_reader.py
# Versão: 5.0 (Link Direto Autenticado - Força Bruta)

import fitz  # PyMuPDF
import httpx
import os
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
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
    "52131": "Comando da Marinha", "52133": "SECIRM", "52232": "CCCPM",
    "52233": "AMAZUL", "52931": "Fundo Naval", "52932": "FDEPM", "52000": "MD"
}

KEYWORDS_DIRECT = [
    "ministério da defesa", "forças armadas", "autoridade marítima", "comando da marinha",
    "marinha do brasil", "fundo naval", "amazônia azul", "ccçpm", "emgepron",
    "fundos públicos", "rardp", "programação orçamentária e financeira",
    "dpof", "programa nuclear", "plano plurianual", "lei orçamentária",
    "nuclep", "submarino", "tamandaré", "patrulha"
]

KEYWORDS_BUDGET = [
    "crédito suplementar", "limite de pagamento", "crédito extraordinário",
    "execução orçamentária", "reforço de dotações", "orçamento fiscal",
    "altera grupos de natureza", "limites de movimentação", "fontes de recursos",
    "movimentação e empenho", "gestão fiscal", "contingenciamento", "bloqueio"
]

# ==============================================================================
# 2. PROMPTS
# ==============================================================================
PROMPT_ESPECIALISTA_MPO = """
### ROLE
Você é um Especialista em Análise Orçamentária e Defesa (Marinha do Brasil).

### TAREFA
Analise esta página do DOU (Ministério do Planejamento/Fazenda).
Busque especificamente pelas UGs: 52131, 52133, 52232, 52233, 52931, 52932, 52000.

### REGRAS
1. Achou UG da MB com valores? -> Classifique (Tipo 1, 2, 3 ou 4) e extraia valores.
2. É portaria de Orçamento (MPO/MF) mas NÃO cita MB? -> Classifique como TIPO 5 (Sem Impacto).
   Resumo: "Para conhecimento. Sem impacto para a Marinha."

### SAÍDA (Texto puro)
▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA]
[Resumo]
⚓ [Análise]
"""

PROMPT_GERAL_MB = """
Você é um analista da Marinha. Encontrei termos de interesse (Defesa, Submarino, etc).
Resumo executivo de 2 linhas:
▶️ [Órgão] - [Assunto]
⚓ [Impacto/Resumo]
"""

# ==============================================================================
# 3. FUNÇÕES DE DOWNLOAD (LINK DIRETO + AUTENTICAÇÃO)
# ==============================================================================

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    # Retorna a data para uso interno
    return date_str

async def download_pdf(date_str: str, filename: str) -> str:
    path = os.path.join("/tmp", filename)
    if os.name == 'nt': path = filename

    if not INLABS_USER or not INLABS_PASS:
        raise ValueError("Credenciais InLabs ausentes.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=120, verify=False, headers=headers, follow_redirects=True) as client:
        # --- 1. LOGIN ---
        print(f"[PDF] Logando no InLabs ({INLABS_USER})...")
        await client.get(INLABS_BASE_URL) # Cookies
        
        # Payload de login
        resp_login = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "senha": INLABS_PASS})
        
        # --- 2. CONSTRUÇÃO DO LINK DIRETO ---
        # Data formato: YYYY-MM-DD
        # Link formato: index.php?p=YYYY-MM-DD&dl=YYYY_MM_DD_ASSINADO_do1.pdf
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            data_dl = dt.strftime("%Y_%m_%d") # 2025_12_16
            
            # Link da Seção 1
            # Tenta primeiro com "ASSINADO" (Padrão novo)
            filename_server = f"{data_dl}_ASSINADO_do1.pdf"
            direct_url = f"{INLABS_BASE_URL}/index.php?p={date_str}&dl={filename_server}"
            
            print(f"[PDF] Tentando baixar direto: {direct_url}")
            resp_pdf = await client.get(direct_url)

            # Verifica se deu erro (arquivo pequeno = HTML de erro)
            if len(resp_pdf.content) < 15000 or "text/html" in resp_pdf.headers.get("content-type", ""):
                print("[PDF] Link padrão falhou (veio HTML). Tentando formato alternativo (sem ASSINADO)...")
                
                # Tentativa 2: Sem "ASSINADO" (Padrão antigo ou erro de nomeação)
                filename_alt = f"{data_dl}_do1.pdf"
                alt_url = f"{INLABS_BASE_URL}/index.php?p={date_str}&dl={filename_alt}"
                print(f"[PDF] Tentando alternativo: {alt_url}")
                resp_pdf = await client.get(alt_url)

            # Validação Final
            content_type = resp_pdf.headers.get("content-type", "").lower()
            if "text/html" in content_type:
                # Salva o HTML de erro para debug no log (primeiros 200 chars)
                snippet = resp_pdf.text[:200].replace("\n", " ")
                print(f"[PDF] ERRO CRÍTICO: Servidor retornou HTML: {snippet}")
                raise ValueError("InLabs retornou HTML em vez de PDF. Verifique se o jornal DE HOJE já foi publicado.")

            with open(path, "wb") as f:
                f.write(resp_pdf.content)
            
            size_kb = os.path.getsize(path) / 1024
            print(f"[PDF] Download Sucesso: {size_kb:.2f} KB")
            
            if size_kb < 10:
                raise ValueError("Arquivo baixado é muito pequeno (possível erro de login ou arquivo vazio).")

            return path

        except Exception as e:
            print(f"[PDF] Falha no processo de download: {e}")
            if os.path.exists(path): os.remove(path)
            raise e

def extract_text_from_page(page) -> str:
    return page.get_text("text")

async def analyze_pdf_content(pdf_path: str, model) -> List[Dict]:
    results = []
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[PDF] Erro Fitz: {e}")
        return []
    
    print(f"📄 PDF Aberto. Páginas: {len(doc)}")
    
    tasks = []
    mpo_triggers = ["ministério do planejamento", "ministério da fazenda", "secretaria de orçamento", "tesouro nacional"]
    general_triggers = KEYWORDS_DIRECT + KEYWORDS_BUDGET
    max_pages = 60 

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
            if len(tasks) >= max_pages: break

    if not tasks:
        print("[PDF] Nada relevante encontrado na triagem.")
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
        if not analysis: return None

        organ = "DOU (Seção 1)"
        title = f"Página {page_num}"
        for line in analysis.split("\n"):
            if "▶️" in line: organ = line.replace("▶️", "").strip()[:60]
            if "📌" in line: title = line.replace("📌", "").strip()[:100]

        return {
            "organ": organ, "type": title, "summary": analysis,
            "relevance_reason": f"IA (Pág {page_num})", "section": "DO1",
            "clean_text": text, "is_mpo_navy_hit": (context_type == "MPO")
        }
    except Exception as e:
        print(f"Erro IA: {e}")
        return None
