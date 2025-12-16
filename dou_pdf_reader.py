# Nome do arquivo: dou_pdf_reader.py
# Versão: 2.1 (Correção de DNS/Headers Gov.br)

import fitz  # PyMuPDF
import httpx
import os
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
import google.generativeai as genai

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
# 3. FUNÇÕES DO LEITOR (COM FIX DE HEADERS)
# ==============================================================================

# Headers para simular um navegador real e evitar bloqueio do gov.br
HEADERS_GOV = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive"
}

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    """Tenta construir o link do PDF e verifica se existe."""
    try:
        ano, mes, dia = date_str.split("-")
        # URL Oficial do CDN do IN.GOV.BR
        base_cdn = "https://ens-cdn.in.gov.br/imprensa/jornal"
        
        url_candidate = f"{base_cdn}/{ano}/{mes}/{dia}/{section}/pdf/jornal-{ano}-{mes}-{dia}-{section}.pdf"
        
        print(f"[PDF Check] Testando URL: {url_candidate}")
        
        async with httpx.AsyncClient(timeout=15, verify=False, headers=HEADERS_GOV, follow_redirects=True) as client:
            resp = await client.head(url_candidate)
            
            if resp.status_code == 200:
                print(f"[PDF Check] Link VÁLIDO: {url_candidate}")
                return url_candidate
            else:
                # Tenta GET se HEAD for bloqueado
                resp_get = await client.get(url_candidate, headers=HEADERS_GOV) 
                if resp_get.status_code == 200:
                    print(f"[PDF Check] Link VÁLIDO (via GET): {url_candidate}")
                    return url_candidate
                    
                print(f"[PDF Check] Link inacessível ({resp.status_code}). O arquivo pode não existir ainda ou bloqueio de IP.")
                return None
                
    except Exception as e:
        print(f"[PDF Check] Erro de conexão/DNS: {e}")
        return None

async def download_pdf(url: str, filename: str) -> str:
    path = os.path.join("/tmp", filename) # Render usa /tmp
    if os.name == 'nt': path = filename

    try:
        async with httpx.AsyncClient(timeout=120, verify=False, headers=HEADERS_GOV, follow_redirects=True) as client:
            print(f"[Download] Baixando PDF...")
            resp = await client.get(url)
            resp.raise_for_status() # Garante que baixou ok
            
            with open(path, "wb") as f:
                f.write(resp.content)
        
        # Verifica se baixou algo válido (> 1KB)
        if os.path.getsize(path) < 1000:
            print("[Download] Alerta: Arquivo baixado muito pequeno (possível erro).")
            
        return path
    except Exception as e:
        print(f"[Download] Falha fatal: {e}")
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
    
    total_paginas = len(doc)
    print(f"📄 PDF carregado com sucesso. Total páginas: {total_paginas}")
    
    tasks = []
    
    # Prepara strings de busca
    mpo_triggers = ["ministério do planejamento", "ministério da fazenda", "secretaria de orçamento", "tesouro nacional"]
    general_triggers = KEYWORDS_DIRECT + KEYWORDS_BUDGET

    # Limite de segurança para não processar jornais gigantescos inteiros na IA se não filtrar
    max_pages_to_analyze = 50 
    analyzed_count = 0

    for i, page in enumerate(doc):
        # Extração de texto rápida
        text_lower = extract_text_from_page(page).lower()
        
        # 1. É MPO ou Fazenda? (CRÍTICO)
        is_mpo_mf = any(t in text_lower for t in mpo_triggers)
        
        # 2. Tem menção direta?
        is_general_interest = False
        if not is_mpo_mf:
            is_general_interest = any(k in text_lower for k in general_triggers)

        if is_mpo_mf or is_general_interest:
            # Seleciona o prompt correto
            prompt = PROMPT_ESPECIALISTA_MPO if is_mpo_mf else PROMPT_GERAL_MB
            type_ctx = "MPO" if is_mpo_mf else "GERAL"
            
            tasks.append(run_gemini_analysis(page.get_text(), model, prompt, i+1, type_ctx))
            analyzed_count += 1
            
            if analyzed_count >= max_pages_to_analyze:
                print(f"[PDF] Limite de segurança atingido ({max_pages_to_analyze} páginas enviadas para IA).")
                break

    if not tasks:
        print("[PDF] Nenhuma página relevante encontrada pelos filtros iniciais.")
        doc.close()
        return []

    # Processamento em lotes (Rate Limit Gemini)
    chunk_size = 5 # Reduzido para estabilidade
    print(f"[IA] Iniciando análise de {len(tasks)} páginas selecionadas...")
    
    for i in range(0, len(tasks), chunk_size):
        chunk = tasks[i:i + chunk_size]
        chunk_results = await asyncio.gather(*chunk)
        for res in chunk_results:
            if res: results.append(res)
                
    doc.close()
    return results

async def run_gemini_analysis(text: str, model, prompt_template: str, page_num: int, context_type: str) -> Optional[Dict]:
    try:
        if len(text) < 100: return None # Pula páginas vazias

        full_prompt = f"{prompt_template}\n\n--- CONTEÚDO DA PÁGINA {page_num} ---\n{text[:15000]}"
        
        # Tenta gerar
        response = await model.generate_content_async(full_prompt)
        analysis = response.text.strip()
        
        # Validação básica
        if not analysis: return None

        # Parse para o Frontend
        organ = "DOU (Seção 1)"
        title = f"Página {page_num}"
        
        lines = analysis.split("\n")
        for line in lines:
            if "▶️" in line: organ = line.replace("▶️", "").strip()[:50]
            if "📌" in line: title = line.replace("📌", "").strip()[:100]

        return {
            "organ": organ,
            "type": title,
            "summary": analysis,
            "relevance_reason": f"Análise IA (Pág {page_num})",
            "section": "DO1",
            "clean_text": text,
            "is_mpo_navy_hit": (context_type == "MPO")
        }

    except Exception as e:
        print(f"Erro IA Pág {page_num}: {e}")
        return None
