# Nome do arquivo: dou_pdf_reader.py
# Versão: 1.0 (PDF Híbrido + Prompt Especialista)

import fitz  # PyMuPDF
import httpx
import os
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
import google.generativeai as genai

# --- SEU PROMPT ESPECIALISTA (Idêntico ao fornecido) ---
PROMPT_ESPECIALISTA_MPO = """
### ROLE
Você é um Especialista em Análise Orçamentária e Defesa (Marinha do Brasil).

### DIRETRIZES DE BUSCA DE ENTIDADES (UOs)
Busque especificamente pelas UGs:
- "52131" (Comando da Marinha), "52133" (SECIRM), "52232" (CCCPM), "52233" (AMAZUL)
- "52931" (Fundo Naval), "52932" (Fundo Ensino), "52000" (MD - Apenas p/ Movimentação)

### REGRA DE EXAUSTIVIDADE
Liste TODAS as Portarias do MPO e MF encontradas nesta página.
- Se citar UOs da MB -> Tipos 1, 2, 3 ou 4.
- Se NÃO citar UOs da MB -> Tipo 5 (Sem Impacto).

### REGRAS DE CLASSIFICAÇÃO (Resumo)
TIPO 1: Crédito Suplementar (Com Impacto MB)
TIPO 2: Movimentação e Empenho (Com Impacto MD)
TIPO 3: Alteração de GND (Com Impacto MB)
TIPO 4: Modificação de Fontes (Com Impacto MB)
TIPO 5: Sem Impacto (Genérico MPO/MF)

### FORMATO DE SAÍDA (Rigoroso)
Para cada ato encontrado, gere a saída exata abaixo (sem markdown json, apenas o texto):

▶️ [Órgão Emissor]
📌 [NOME DA PORTARIA]
[Resumo breve]
⚓ [Análise conforme Tipo]
"""

# Prompt mais simples para capturas gerais (Licitações, Avisos, etc.)
PROMPT_GERAL_MB = """
Você é um analista da Marinha. Encontrei menções à Marinha/Defesa neste texto.
Faça um resumo de 1 frase para relatório WhatsApp.
Comece com: "▶️ [Órgão] - [Tipo do Ato]"
Termine com: "⚓ [Impacto/Resumo]"
"""

# --- CONFIGURAÇÃO ---
IN_GOV_URL = "https://www.in.gov.br/leitura-jornal"
DOWNLOAD_DIR = "/tmp"  # No Render, usar /tmp

async def get_pdf_link_for_date(date_str: str, section: str = "do1") -> Optional[str]:
    """
    Busca o link do PDF completo da seção no in.gov.br
    date_str: YYYY-MM-DD
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    date_formatted = dt.strftime("%d-%m-%Y")
    
    params = {"data": date_formatted, "secao": section}
    
    async with httpx.AsyncClient() as client:
        # Primeiro acessa a página de leitura para pegar o JSON de configuração interna
        # Nota: A URL real do PDF segue um padrão, vamos tentar montar direto primeiro, 
        # se falhar, precisaríamos de um scraper mais complexo (Selenium/Soup), 
        # mas geralmente o padrão é:
        # https://ens-cdn.in.gov.br/imprensa/jornal/{YYYY}/{MM}/{DD}/{SECAO}/pdf/jornal-{YYYY}-{MM}-{DD}-{SECAO}.pdf
        # Vamos tentar construir o link direto primeiro (é mais rápido).
        
        base_cdn = "https://ens-cdn.in.gov.br/imprensa/jornal"
        ano, mes, dia = date_str.split("-")
        
        # O nome do arquivo pode variar (ex: jornal-2025-01-01-do1.pdf ou principal.pdf)
        # Vamos tentar o padrão mais comum do CDN
        url_candidate = f"{base_cdn}/{ano}/{mes}/{dia}/{section}/pdf/jornal-{ano}-{mes}-{dia}-{section}.pdf"
        
        try:
            head = await client.head(url_candidate)
            if head.status_code == 200:
                return url_candidate
        except:
            pass
            
        return None

async def download_pdf(url: str, filename: str) -> str:
    path = os.path.join(DOWNLOAD_DIR, filename)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(url)
        with open(path, "wb") as f:
            f.write(resp.content)
    return path

def extract_text_from_page(page) -> str:
    """Extrai texto preservando um pouco do layout físico"""
    return page.get_text("text")

async def analyze_pdf_content(pdf_path: str, model) -> List[Dict]:
    """
    Lógica Híbrida:
    1. Abre PDF.
    2. Varre páginas.
    3. Filtra páginas de interesse (MPO/MF ou Keywords MB).
    4. Envia para Gemini.
    """
    results = []
    doc = fitz.open(pdf_path)
    
    print(f"📄 PDF carregado. Total páginas: {len(doc)}")
    
    # Keywords para gatilho RÁPIDO (sem gastar token de IA)
    kw_mpo = ["ministério do planejamento", "ministério da fazenda", "secretaria do orçamento", "tesouro nacional"]
    kw_mb = ["comando da marinha", "fundo naval", "prosub", "nuclear", "tamandaré", "emgepron", "amazul", "secirm"]
    
    tasks = []
    
    for i, page in enumerate(doc):
        text = extract_text_from_page(page).lower()
        
        # Lógica 1: É MPO ou Fazenda? (Prioridade Alta - Prompt Especialista)
        is_mpo = any(k in text for k in kw_mpo) and ("portaria" in text or "decreto" in text)
        
        # Lógica 2: É menção à Marinha (Geral)?
        is_mb = any(k in text for k in kw_mb)
        
        if is_mpo:
            # Envia página para Gemini com Prompt Especialista
            raw_text = page.get_text() # Pega texto original (case sensitive)
            tasks.append(run_gemini_analysis(raw_text, model, PROMPT_ESPECIALISTA_MPO, i+1, "MPO"))
            
        elif is_mb:
            # Envia página para Gemini com Prompt Resumo
            raw_text = page.get_text()
            tasks.append(run_gemini_analysis(raw_text, model, PROMPT_GERAL_MB, i+1, "GERAL"))
            
    # Executa em paralelo (cuidado com Rate Limit do Gemini, talvez precise de semáforo)
    # Vamos processar em lotes de 5 para não estourar
    chunk_size = 5
    for i in range(0, len(tasks), chunk_size):
        chunk = tasks[i:i + chunk_size]
        chunk_results = await asyncio.gather(*chunk)
        for res in chunk_results:
            if res:
                results.append(res)
                
    doc.close()
    return results

async def run_gemini_analysis(text: str, model, prompt_template: str, page_num: int, context_type: str) -> Optional[Dict]:
    try:
        full_prompt = f"{prompt_template}\n\n--- TEXTO DA PÁGINA {page_num} DO DOU ---\n{text[:10000]}"
        
        # Gera resposta
        response = await model.generate_content_async(full_prompt)
        analysis = response.text.strip()
        
        # Se for MPO, filtramos "Sem impacto" se quisermos limpar o output
        if context_type == "MPO" and "Sem impacto para a Marinha" in analysis and "TIPO 5" in analysis:
             # Opcional: Se quiser ignorar os "Sem impacto", retorne None aqui.
             # Mas seu prompt pede para listar, então vamos manter.
             pass

        # Cria objeto estruturado para o Frontend
        # O frontend espera: organ, type, summary, relevance_reason
        
        # Tenta extrair o Órgão e Título da resposta da IA para ficar bonitinho no Card
        organ = "DOU (IA)"
        title = f"Página {page_num}"
        
        # Parse simples da saída padronizada
        if "▶️" in analysis:
            lines = analysis.split("\n")
            for line in lines:
                if "▶️" in line: organ = line.replace("▶️", "").strip()
                if "📌" in line: title = line.replace("📌", "").strip()
                break
        
        return {
            "organ": organ,
            "type": title,
            "summary": analysis, # O texto formatado vai aqui
            "relevance_reason": f"Análise IA (Pág {page_num}) - {context_type}",
            "section": "DO1",
            "clean_text": text,
            "is_mpo_navy_hit": (context_type == "MPO")
        }

    except Exception as e:
        print(f"Erro Gemini Pág {page_num}: {e}")
        return None
