# Nome do arquivo: dou_pdf_reader.py
# Versão: 6.0 (Crawler Híbrido + Login Fix)

import fitz  # PyMuPDF
import httpx
import os
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from urllib.parse import urljoin

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

# URLs
# Nota: Usamos logar.php que é o endpoint padrão do InLabs legado
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
# 3. FUNÇÕES DE CRAWLER & DOWNLOAD
# ==============================================================================

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    return date_str

async def download_pdf(date_str: str, filename: str) -> str:
    path = os.path.join("/tmp", filename)
    if os.name == 'nt': path = filename

    if not INLABS_USER or not INLABS_PASS:
        raise ValueError("Credenciais InLabs ausentes.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=60, verify=False, headers=headers, follow_redirects=True) as client:
        # --- PASSO 1: LOGIN (Payload Duplo para Garantia) ---
        print(f"[PDF] Logando no InLabs ({INLABS_USER})...")
        await client.get(INLABS_BASE_URL)
        
        # Envia 'password' E 'senha' para garantir compatibilidade com versões diferentes do backend
        login_data = {
            "email": INLABS_USER, 
            "password": INLABS_PASS, 
            "senha": INLABS_PASS
        }
        resp_login = await client.post(INLABS_LOGIN_URL, data=login_data)
        
        # --- PASSO 2: TENTATIVA VIA CRAWLER (Buscar link na página) ---
        day_url = f"{INLABS_BASE_URL}/index.php?p={date_str}"
        print(f"[PDF] Acessando índice: {day_url}")
        resp_page = await client.get(day_url)
        
        soup = BeautifulSoup(resp_page.text, "html.parser")
        target_href = None
        
        # Procura links .pdf
        all_pdfs = [a["href"] for a in soup.find_all("a", href=True) if ".pdf" in a["href"].lower()]
        
        # Filtra Seção 1
        for href in all_pdfs:
            if "do1" in href.lower() or "secao_1" in href.lower():
                target_href = href
                print(f"[PDF] Crawler encontrou link: {target_href}")
                break
        
        # Se o Crawler falhar, tenta o Método Direto (Força Bruta)
        if not target_href:
            print("[PDF] Crawler não achou link na página. Tentando construção direta...")
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            data_dl = dt.strftime("%Y_%m_%d")
            # Tenta padrão 'ASSINADO'
            target_href = f"index.php?p={date_str}&dl={data_dl}_ASSINADO_do1.pdf"
        
        final_url = urljoin(INLABS_BASE_URL, target_href)
        
        # --- PASSO 3: BAIXAR ---
        print(f"[PDF] Baixando: {final_url}")
        resp_file = await client.get(final_url)
        
        # --- PASSO 4: VALIDAÇÃO DO ARQUIVO ---
        content_type = resp_file.headers.get("content-type", "").lower()
        file_size_kb = len(resp_file.content) / 1024
        
        # Se for HTML ou muito pequeno -> Erro
        if "text/html" in content_type or file_size_kb < 15:
            # Tenta ler o título do HTML para saber o erro
            try:
                err_soup = BeautifulSoup(resp_file.content, "html.parser")
                page_title = err_soup.title.string.strip() if err_soup.title else "Sem Título"
                h1_text = err_soup.find("h1").get_text().strip() if err_soup.find("h1") else ""
            except:
                page_title = "Erro desconhecido"
                h1_text = ""

            error_msg = f"InLabs retornou HTML ({page_title} - {h1_text}). Provável Login Inválido ou Arquivo não publicado."
            print(f"[PDF] FALHA: {error_msg}")
            raise ValueError(error_msg)

        with open(path, "wb") as f:
            f.write(resp_file.content)
            
        print(f"[PDF] Download Sucesso: {file_size_kb:.2f} KB")
        return path

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
