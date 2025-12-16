# Nome do arquivo: dou_pdf_reader.py
# Versão: 2.0 (Lógica Exaustiva MPO + Tags)

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

# UGs da Marinha/Defesa (Tags Críticas)
NAVY_UGS = {
    "52131": "Comando da Marinha",
    "52133": "SECIRM",
    "52232": "CCCPM",
    "52233": "AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo Ensino Profissional Marítimo",
    "52000": "Ministério da Defesa" # (Monitorar Movimentação)
}

# Palavras-chave de Interesse Direto (Geral)
KEYWORDS_DIRECT = [
    "ministério da defesa", "forças armadas", "autoridade marítima", "comando da marinha",
    "marinha do brasil", "fundo naval", "amazônia azul", "ccçpm", "emgepron",
    "fundos públicos", "rardp", "programação orçamentária e financeira",
    "dpof", "programa nuclear", "plano plurianual", "lei orçamentária",
    "nuclep", "submarino", "tamandaré", "patrulha"
]

# Palavras-chave Orçamentárias (Geral - Captura ampla)
KEYWORDS_BUDGET = [
    "crédito suplementar", "limite de pagamento", "crédito extraordinário",
    "execução orçamentária", "reforço de dotações", "orçamento fiscal",
    "altera grupos de natureza", "limites de movimentação", "fontes de recursos",
    "movimentação e empenho", "gestão fiscal", "contingenciamento", "bloqueio"
]

# ==============================================================================
# 2. PROMPTS ESPECÍFICOS
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

### FORMATO DE SAÍDA (Apenas o texto abaixo, sem markdown extra)
▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA]
[Breve resumo do que trata a portaria]
⚓ [Sua Análise aqui: "MB: ✅ Suplementações..." OU "MB: Para conhecimento. Sem impacto..."]
"""

PROMPT_GERAL_MB = """
Você é um analista da Marinha. Encontrei termos de interesse (Defesa, Submarino, Fundo Naval, etc) nesta página.
Faça um resumo executivo de 2 linhas para WhatsApp.
Comece com: "▶️ [Órgão] - [Assunto]"
Termine com: "⚓ [Impacto/Resumo]"
"""

# ==============================================================================
# 3. FUNÇÕES DO LEITOR
# ==============================================================================

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    """Tenta construir o link do PDF. Retorna None se falhar."""
    try:
        ano, mes, dia = date_str.split("-")
        base_cdn = "https://ens-cdn.in.gov.br/imprensa/jornal"
        
        # Tentativa 1: Link Padrão
        url_candidate = f"{base_cdn}/{ano}/{mes}/{dia}/{section}/pdf/jornal-{ano}-{mes}-{dia}-{section}.pdf"
        
        # Verifica se o link existe (HEAD request)
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.head(url_candidate)
            if resp.status_code == 200:
                print(f"[PDF Check] Link encontrado: {url_candidate}")
                return url_candidate
            else:
                print(f"[PDF Check] Link não acessível ({resp.status_code}): {url_candidate}")
                
                # Tentativa 2: Às vezes o arquivo chama 'principal.pdf' em pastas antigas
                # Mas para 2024/2025 o padrão acima é o correto.
                return None
    except Exception as e:
        print(f"[PDF Check] Erro ao gerar link: {e}")
        return None

async def download_pdf(url: str, filename: str) -> str:
    path = os.path.join("/tmp", filename) # Render usa /tmp
    # Se estiver local (Windows), usa pasta local
    if os.name == 'nt': 
        path = filename

    async with httpx.AsyncClient(timeout=90, verify=False) as client:
        print(f"[Download] Baixando {url}...")
        resp = await client.get(url)
        with open(path, "wb") as f:
            f.write(resp.content)
    return path

def extract_text_from_page(page) -> str:
    return page.get_text("text")

async def analyze_pdf_content(pdf_path: str, model) -> List[Dict]:
    results = []
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Erro ao abrir PDF: {e}")
        return []
    
    print(f"📄 PDF carregado. Total páginas: {len(doc)}")
    
    tasks = []
    
    # Prepara strings de busca (lowercase para performance)
    mpo_triggers = ["ministério do planejamento", "ministério da fazenda", "secretaria de orçamento", "tesouro nacional"]
    
    # Combina keywords gerais para busca rápida
    general_triggers = KEYWORDS_DIRECT + KEYWORDS_BUDGET

    for i, page in enumerate(doc):
        text_lower = extract_text_from_page(page).lower()
        
        # --- LÓGICA DE TRIAGEM (O Bibliotecário) ---
        
        # 1. É MPO ou Fazenda? (CRÍTICO - SEMPRE ANALISAR)
        is_mpo_mf = any(t in text_lower for t in mpo_triggers)
        
        # 2. Tem menção direta à Marinha/Defesa ou Orçamento? (RELEVANTE)
        # Só verifica se NÃO for MPO (para não duplicar)
        is_general_interest = False
        if not is_mpo_mf:
            is_general_interest = any(k in text_lower for k in general_triggers)

        # --- AÇÃO ---
        
        if is_mpo_mf:
            # Envia para IA com Prompt Especialista (que sabe lidar com Tipo 5)
            # Passamos o texto cru (case sensitive) para a IA ler melhor
            tasks.append(run_gemini_analysis(page.get_text(), model, PROMPT_ESPECIALISTA_MPO, i+1, "MPO"))
            
        elif is_general_interest:
            # Envia para IA com Prompt Geral
            tasks.append(run_gemini_analysis(page.get_text(), model, PROMPT_GERAL_MB, i+1, "GERAL"))

    # Processa em lotes para não estourar a API
    chunk_size = 10 
    for i in range(0, len(tasks), chunk_size):
        chunk = tasks[i:i + chunk_size]
        if chunk:
            print(f"[IA] Processando lote de páginas {i} a {i+len(chunk)}...")
            chunk_results = await asyncio.gather(*chunk)
            for res in chunk_results:
                if res: results.append(res)
                
    doc.close()
    return results

async def run_gemini_analysis(text: str, model, prompt_template: str, page_num: int, context_type: str) -> Optional[Dict]:
    try:
        # Verifica se tem conteúdo mínimo
        if len(text) < 50: return None

        full_prompt = f"{prompt_template}\n\n--- CONTEÚDO DA PÁGINA {page_num} ---\n{text[:15000]}"
        
        response = await model.generate_content_async(full_prompt)
        analysis = response.text.strip()
        
        # Filtro de qualidade da resposta
        if not analysis or "Erro" in analysis: return None
        
        # Se for MPO e a IA disse "Sem impacto", nós MANTEMOS (conforme seu pedido),
        # mas podemos descartar se a IA alucinar e não seguir o padrão.
        
        # Parse básico para identificar Órgão e Título
        organ = "DOU (Seção 1)"
        title = f"Página {page_num}"
        
        lines = analysis.split("\n")
        for line in lines:
            if "▶️" in line: organ = line.replace("▶️", "").strip()
            if "📌" in line: title = line.replace("📌", "").strip()

        return {
            "organ": organ,
            "type": title,
            "summary": analysis, # O texto completo gerado pela IA
            "relevance_reason": f"IA (Pág {page_num})",
            "section": "DO1",
            "clean_text": text,
            "is_mpo_navy_hit": (context_type == "MPO")
        }

    except Exception as e:
        print(f"Erro IA Pág {page_num}: {e}")
        return None
