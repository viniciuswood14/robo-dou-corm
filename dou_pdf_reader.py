# Nome do arquivo: dou_pdf_reader.py
# Versão: 3.1 (Correção de Rota de Login InLabs)

import fitz  # PyMuPDF
import httpx
import os
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Optional
import google.generativeai as genai

# ==============================================================================
# CONFIGURAÇÃO DE CREDENCIAIS INLABS
# ==============================================================================
INLABS_USER = os.environ.get("INLABS_USER")
INLABS_PASS = os.environ.get("INLABS_PASS")

if not INLABS_USER or not INLABS_PASS:
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
            INLABS_USER = cfg.get("INLABS_USER")
            INLABS_PASS = cfg.get("INLABS_PASS")
    except:
        pass

# URL CORRIGIDA PARA O ENDPOINT REAL DE LOGIN DO INLABS
INLABS_LOGIN_URL = "https://inlabs.in.gov.br/logar.php" 
INLABS_BASE_URL = "https://inlabs.in.gov.br"

# ==============================================================================
# 1. LISTAS DE INTERESSE ESTRATÉGICO
# ==============================================================================

NAVY_UGS = {
    "52131": "Comando da Marinha",
    "52133": "SECIRM",
    "52232": "CCCPM",
    "52233": "AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo Ensino Profissional Marítimo",
    "52000": "Ministério da Defesa"
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
Verifique se há menção às seguintes UGs (Tags):
- 52131 (Comando da Marinha)
- 52133 (SECIRM)
- 52232 (CCCPM)
- 52233 (AMAZUL)
- 52931 (Fundo Naval)
- 52932 (Fundo Ensino)
- 52000 (MD - Apenas p/ Movimentação/Limites)

### REGRAS DE DECISÃO
1. Se encontrar qualquer uma das UGs acima com valores (Suplementação, Crédito, Fontes):
   -> Classifique como TIPO 1, 2, 3 ou 4.
   -> Extraia os valores exatos.

2. Se NÃO encontrar as UGs acima, mas for uma Portaria de Crédito/Orçamento do MPO/MF:
   -> Classifique como TIPO 5 (Sem Impacto).
   -> Resumo obrigatório: "Para conhecimento. Sem impacto para a Marinha."

### FORMATO DE SAÍDA (Apenas o texto abaixo)
▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA]
[Breve resumo do que trata a portaria]
⚓ [Sua Análise aqui]
"""

PROMPT_GERAL_MB = """
Você é um analista da Marinha. Encontrei termos de interesse (Defesa, Submarino, Fundo Naval, etc) nesta página.
Faça um resumo executivo de 2 linhas para WhatsApp.
Comece com: "▶️ [Órgão] - [Assunto]"
Termine com: "⚓ [Impacto/Resumo]"
"""

# ==============================================================================
# 3. FUNÇÕES DE DOWNLOAD (VIA INLABS)
# ==============================================================================

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    """
    Constrói o link direto do InLabs baseada na data.
    Padrão: https://inlabs.in.gov.br/index.php?p=2025-12-16&dl=2025_12_16_ASSINADO_do1.pdf
    """
    try:
        # Formato de entrada date_str: YYYY-MM-DD
        dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
        
        p_param = date_str # Ex: 2025-12-16
        
        # dl_param Ex: 2025_12_16_ASSINADO_do1.pdf
        dl_date = dt_obj.strftime("%Y_%m_%d")
        dl_param = f"{dl_date}_ASSINADO_{section}.pdf"
        
        # Monta URL
        url = f"{INLABS_BASE_URL}/index.php?p={p_param}&dl={dl_param}"
        
        print(f"[PDF] Link InLabs construído: {url}")
        return url
        
    except Exception as e:
        print(f"[PDF] Erro ao construir link: {e}")
        return None

async def download_pdf(url: str, filename: str) -> str:
    """
    Realiza login no InLabs e baixa o PDF autenticado.
    """
    path = os.path.join("/tmp", filename)
    if os.name == 'nt': path = filename

    if not INLABS_USER or not INLABS_PASS:
        print("[PDF] Erro: Credenciais do InLabs não encontradas (INLABS_USER/PASS).")
        raise ValueError("Credenciais InLabs ausentes")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    async with httpx.AsyncClient(timeout=120, verify=True, headers=headers, follow_redirects=True) as client:
        # 1. Login
        print(f"[PDF] Autenticando no InLabs como {INLABS_USER}...")
        try:
            # Acessa home para pegar cookies
            await client.get(INLABS_BASE_URL)
            
            # Post Login - CORRIGIDO PARA 'senha' E URL 'logar.php'
            resp_login = await client.post(
                INLABS_LOGIN_URL, 
                data={"email": INLABS_USER, "senha": INLABS_PASS}
            )
            
            # logar.php geralmente redireciona (302) ou retorna 200.
            if resp_login.status_code >= 400:
                print(f"[PDF] Falha no login: {resp_login.status_code}")
                # Às vezes retorna 404 se a rota estiver errada, mas logar.php deve existir.
                raise ValueError("Falha Login InLabs")
                
        except Exception as e:
            print(f"[PDF] Erro na conexão de login: {e}")
            raise e

        # 2. Download PDF
        print(f"[PDF] Baixando arquivo: {url}")
        try:
            resp_pdf = await client.get(url)
            
            # Verifica redirects (caso o login tenha falhado silenciosamente)
            if "login" in str(resp_pdf.url):
                print("[PDF] O sistema redirecionou para o login. As credenciais podem estar erradas.")
                raise ValueError("Redirecionado para Login")

            if resp_pdf.status_code != 200:
                print(f"[PDF] Erro no download: HTTP {resp_pdf.status_code}")
                raise ValueError(f"HTTP {resp_pdf.status_code}")

            # Verifica se baixou um HTML (erro de login) ou PDF real
            content_type = resp_pdf.headers.get("content-type", "")
            if "text/html" in content_type and len(resp_pdf.content) < 50000:
                print("[PDF] Alerta: O arquivo baixado parece ser uma página HTML, não um PDF. Verifique login.")
            
            with open(path, "wb") as f:
                f.write(resp_pdf.content)
                
            size_kb = os.path.getsize(path) / 1024
            print(f"[PDF] Download concluído: {size_kb:.2f} KB")
            
            if size_kb < 10:
                print("[PDF] Arquivo muito pequeno. Provavelmente corrompido ou link errado.")
                
            return path
            
        except Exception as e:
            print(f"[PDF] Falha no download do arquivo: {e}")
            if os.path.exists(path): os.remove(path)
            raise e

def extract_text_from_page(page) -> str:
    return page.get_text("text")

async def analyze_pdf_content(pdf_path: str, model) -> List[Dict]:
    results = []
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"[PDF] Erro ao abrir arquivo local ({pdf_path}): {e}")
        return []
    
    print(f"📄 PDF carregado. Total páginas: {len(doc)}")
    
    tasks = []
    
    # Prepara strings de busca
    mpo_triggers = ["ministério do planejamento", "ministério da fazenda", "secretaria de orçamento", "tesouro nacional"]
    general_triggers = KEYWORDS_DIRECT + KEYWORDS_BUDGET

    max_pages = 60 # Segurança
    count = 0

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
            count += 1
            if count >= max_pages: break

    if not tasks:
        print("[PDF] Nenhuma página relevante encontrada.")
        doc.close()
        return []

    print(f"[IA] Analisando {len(tasks)} páginas...")
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
