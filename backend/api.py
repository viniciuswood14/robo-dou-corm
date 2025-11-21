# Nome do arquivo: api.py
# Versão: 14.0.6 (COMPLETO - DOU + Valor + PAC Histórico + Correção de Rotas)

from fastapi import FastAPI, Form, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
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
from google_search import perform_google_search, SearchResult

# Importações PAC
import numpy as np
from orcamentobr import despesa_detalhada

# Importa a função de atualização do cache (para o endpoint manual)
# Certifique-se de que check_pac.py está no mesmo diretório
from check_pac import update_pac_historical_cache


# =====================================================================================
# Robô DOU/Valor API
# =====================================================================================

app = FastAPI(
    title="Robô DOU/Valor API - v14.0.6"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================================================
# CONFIG
# =====================================================================================

try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    raise RuntimeError("Erro: Arquivo 'config.json' não encontrado.")
except json.JSONDecodeError:
    raise RuntimeError("Erro: Falha ao decodificar 'config.json'. Verifique a sintaxe.")

# Credenciais / URLs InLabs
INLABS_BASE = os.getenv(
    "INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br")
)
INLABS_LOGIN_URL = os.getenv(
    "INLABS_LOGIN_URL", config.get("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
)
INLABS_USER = os.getenv("INLABS_USER", config.get("INLABS_USER", None))
INLABS_PASS = os.getenv("INLABS_PASS", config.get("INLABS_PASS", None))

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", config.get("GEMINI_API_KEY", None))
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Constantes auxiliares
TEMPLATE_LME = config.get("TEMPLATE_LME", "")
TEMPLATE_FONTE = config.get("TEMPLATE_FONTE", "")
TEMPLATE_CREDITO = config.get("TEMPLATE_CREDITO", "")
ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get("MPO_ORG_STRING", "ministério do planejamento e orçamento")
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower)

# PATHS
HISTORICAL_CACHE_PATH = os.environ.get("PAC_HISTORICAL_CACHE_PATH", "pac_historical_dotacao.json")

# PROMPTS IA
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil, especialista em legislação e defesa.
Sua tarefa é ler a publicação do Diário Oficial da União (DOU) abaixo e escrever uma única frase curta (máximo 2 linhas) para um relatório de WhatsApp, focando exclusivamente no impacto para a Marinha do Brasil (MB).

Critérios de Análise:
1.  Se for ato orçamentário (MPO/Fazenda), foque no impacto: É crédito, LME, fontes? Afeta UGs da Marinha ("52131": "Comando da Marinha",
    "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
    "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
    "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
    "52000": "Ministério da Defesa")?
2.  Se for ato normativo (Decreto, Portaria), qual a ação ou responsabilidade criada para a Marinha/Autoridade Marítima?
3.  Se for ato de pessoal (Seção 2), mencionar o nome da pessoa e qual atividade/ação a ela designada. Exemplo de resposta: JOÃO DA SILVA foi nomeado para o cargo de Assessor Técnico.
4.  Se a menção for trivial ou sem impacto direto (ex: 'Ministério da Defesa' apenas listado numa reunião, ou 'Marinha' no nome de uma empresa privada), responda APENAS: "Sem impacto direto."
5.  Nunca alucinar ou inventar numeros. 

Responda só a frase final, sem rodeio adicional.

TEXTO DA PUBLICAÇÃO:
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha do Brasil. A publicação abaixo é do Ministério do Planejamento e Orçamento (MPO) e JÁ FOI CLASSIFICADA como tendo impacto direto em dotações ligadas à Marinha (ex.: Fundo Naval, Comando da Marinha, etc.).

Sua tarefa é:
1. Dizer claramente qual o efeito orçamentário: crédito suplementar (reforço de dotação), alteração de GND (reclassificação da natureza da despesa), mudança de fonte de recursos, antecipação/ajuste de LME etc.
2. Dizer quem é afetado (Ex.: Comando da Marinha, Fundo Naval, Defesa/52000).
3. Se houver reforço de dotação ou acréscimo, deixe isso claro como positivo. Se houver cancelamento/redução, diga isso também.
4. Se conseguir identificar os valores alterados, tambem mencionar, mas nunca alucione ou invente. 
4. Entregar um texto formal para WhatsApp. Não omitir informações como nome e números.

Você NÃO pode responder "Sem impacto direto", porque esta portaria JÁ foi marcada como relevante para a MB.

TEXTO DA PUBLICAÇÃO:
"""

SEARCH_QUERIES = [
    # Query 1: Termos de Política Fiscal Macro
    '"contas publicas" OR "politica fiscal" OR "Arcabouço fiscal" OR "Teto de gastos" OR "Meta fiscal" OR "Resultado primário" OR "Resultado nominal" OR "Dívida Pública" OR "Gastos Públicos" OR "Arrecadação" OR "Reforma tributária" OR "Incentivos fiscais"',
    
    # Query 2: Termos de Instrumentos Orçamentários
    '"orcamento" OR "LDO" OR "LOA" OR "PPA" OR "Contingenciamento" OR "Crédito adicional" OR "Despesas discricionárias" OR "Despesas obrigatórias" OR "fundo publico" OR "governo federal"',
    
    # Query 3: Economia Geral e Defesa (Termos de captura ampla)
    '"economia" OR "mercado financeiro" OR "PIB" OR "Inflação" OR "defesa" OR "marinha" OR "forças armadas" OR "base industrial de defesa"'
]

GEMINI_VALOR_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil.
Sua tarefa é ler o TÍTULO e o RESUMO (snippet) de uma notícia do Valor Econômico e dizer, em uma única frase curta (máximo 2 linhas), qual o impacto ou relevância para a Marinha, Defesa ou para o Orçamento Federal.

- Se for sobre Orçamento Federal, LDO, LOA, Teto de Gastos, Arcabouço Fiscal, etc., diga o impacto.
- Se for sobre Fundos Públicos, analise se afeta a Marinha (Fundo Naval) ou o orçamento.

TÍTULO: {titulo}
RESUMO: {resumo}

Responda só a frase final, sem rodeio.
"""


# =====================================================================================
# MODELOS Pydantic
# =====================================================================================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False  # chave pro prompt da IA


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
    if not s:
        return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    meses_pt = {
        1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR",
        5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO",
        9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"
    }
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception:
        dd = when

    lines = []
    lines.append("Bom dia, senhores!")
    lines.append("")
    lines.append(f"PTC as seguintes publicações de interesse no DOU de {dd}:")
    lines.append("")

    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU"
        pubs_by_section.setdefault(sec, []).append(p)

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    for section_name in sorted(pubs_by_section.keys()):
        subseq = pubs_by_section[section_name]
        if not subseq:
            continue

        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}")
        lines.append("")

        for p in subseq:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {p.type or 'Ato/Portaria'}")
            if p.summary:
                lines.append(p.summary)

            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚓"
            
            if (
                reason.startswith("Erro na análise de IA:")
                or reason.startswith("Erro GRAVE")
                or reason.startswith("⚠️")
            ):
                prefix = "⚠️ Erro IA:"
                reason = (
                    reason.replace("Erro na análise de IA:", "")
                    .replace("Erro GRAVE na análise de IA:", "")
                    .replace("⚠️ IA ignorou impacto MPO:", "")
                    .strip()
                )

            if "\n" in reason:
                lines.append(f"{prefix}\n{reason}")
            else:
                lines.append(f"{prefix} {reason}")

            lines.append("")

    return "\n".join(lines)


def monta_valor_whatsapp(pubs: List[ValorPublicacao], when: str) -> str:
    """Gera o texto para o endpoint /processar-valor-ia"""
    meses_pt = {
        1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR",
        5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO",
        9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"
    }
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception:
        dd = when

    lines = []
    lines.append("Bom dia, senhores!")
    lines.append("")
    lines.append(f"PTC as seguintes publicações de interesse no Valor Econômico de {dd}:")
    lines.append("")

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    for p in pubs:
        lines.append(f"▶️ {p.titulo}")
        lines.append(f"📌 {p.link}")
        
        reason = p.analise_ia or "Para conhecimento."
        prefix = "⚓"
        
        if reason.startswith("Erro"):
            prefix = "⚠️"

        lines.append(f"{prefix} {reason}")
        lines.append("")

    return "\n".join(lines)


# =====================================================================================
# PARSER MPO (tabelas de suplementação / redução / totais por UO)
# =====================================================================================
MB_UOS = {
    "52131": "Comando da Marinha",
    "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
    "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
    "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
    "52000": "Ministério da Defesa",
}
def _clean_text_local(t: str) -> str:
    if t is None:
        return ""
    return re.sub(r"\s+", " ", t).strip()
def _parse_money(raw: str) -> int:
    raw = _clean_text_local(raw)
    if not raw:
        return 0
    raw = raw.replace(".", "").replace(",", "")
    m = re.findall(r"\d+", raw)
    if not m:
        return 0
    try:
        return int("".join(m))
    except:
        return 0
def parse_mpo_budget_table(full_text_content: str) -> str:
    # ... (Esta função permanece idêntica à original) ...
    def _clean_text_local(t: str) -> str:
        if t is None:
            return ""
        return re.sub(r"\s+", " ", t).strip()
    def _parse_money(raw: str) -> int:
        raw = _clean_text_local(raw)
        if not raw:
            return 0
        raw = raw.replace(".", "").replace(",", "")
        m = re.findall(r"\d+", raw)
        if not m:
            return 0
        try:
            return int("".join(m))
        except:
            return 0
    MB_UOS = {
        "52131": "Comando da Marinha",
        "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
        "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
        "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
        "52931": "Fundo Naval",
        "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
        "52000": "Ministério da Defesa",
    }
    ORGAO_REGEX = re.compile(
        r"ÓRGÃO(?:\s+\w+)?\s*:\s*(\d+)\s*-\s*(.+)",
        flags=re.IGNORECASE
    )
    UO_REGEX = re.compile(
        r"(UNIDADE(?:\s+\w+)?|UNIDADE\s+ORÇAMENTÁRIA|UNIDADE\s+ORCAMENTARIA|UG)\s*:\s*(\d+)\s*-\s*(.+)",
        flags=re.IGNORECASE,
    )
    BLOCO_TIPO_REGEX = re.compile(
        r"\(\s*(ACRÉSCIMO|ACRESCIMO|REDUÇÃO|REDUCAO)\s*\)",
        flags=re.IGNORECASE
    )
    TOTAL_REGEX = re.compile(
        r"TOTAL\s*-\s*(FISCAL|SEGURIDADE|GERAL)",
        flags=re.IGNORECASE
    )
    cdata_blocks = re.findall(r"<!\[CDATA\[(.*?)\]\]>", full_text_content,
                              flags=re.DOTALL | re.IGNORECASE)
    if not cdata_blocks:
        cdata_blocks = [full_text_content]
    blocks = []
    for blob in cdata_blocks:
        soup_blob = BeautifulSoup(blob, "html.parser")
        tables = soup_blob.find_all("table")
        if not tables:
            continue
        current_orgao = None
        current_uo_code = None
        current_uo_name = None
        current_tipo = None
        current_block = None
        def start_new_block():
            return {
                "orgao": current_orgao,
                "uo_code": current_uo_code,
                "uo_name": current_uo_name,
                "tipo": current_tipo,
                "acoes": [],
                "totais": {},
            }
        for table in tables:
            all_rows = []
            for tr in table.find_all("tr"):
                cols = [
                    _clean_text_local(td.get_text(" ", strip=True))
                    for td in tr.find_all(["td","th"])
                ]
                if any(col for col in cols):
                    all_rows.append(cols)
            for row in all_rows:
                joined = " ".join(row)
                m_org = ORGAO_REGEX.search(joined)
                if m_org:
                    numero = m_org.group(1).strip()
                    nome = m_org.group(2).strip()
                    current_orgao = f"{numero} - {nome}"
                    current_uo_code = None
                    current_uo_name = None
                    current_tipo = None
                    current_block = None
                    continue
                m_uo = UO_REGEX.search(joined)
                if m_uo:
                    numero = m_uo.group(2).strip()
                    nome = m_uo.group(3).strip()
                    current_uo_code = numero
                    current_uo_name = nome
                    current_tipo = None
                    current_block = None
                    continue
                m_tipo = BLOCO_TIPO_REGEX.search(joined)
                if m_tipo:
                    tipo_raw = m_tipo.group(1).upper()
                    if current_block:
                        blocks.append(current_block)
                    if "ACR" in tipo_raw:
                        current_tipo = "ACRÉSCIMO"
                    else:
                        current_tipo = "REDUÇÃO"
                    current_block = start_new_block()
                    continue
                if current_block:
                    possible_code = row[0].strip() if len(row) > 0 else ""
                    possible_val  = row[-1].strip() if len(row) > 0 else ""
                    has_code = re.match(r"^\d{3,4}", possible_code) is not None
                    has_money = (
                        re.search(r"\d", possible_val) is not None
                        and _parse_money(possible_val) > 0
                    )
                    if has_code and has_money and len(row) >= 2:
                        desc = row[1].strip()
                        current_block["acoes"].append({
                            "acao": possible_code,
                            "desc": desc,
                            "valor": _parse_money(possible_val),
                        })
                        continue
                    m_total = TOTAL_REGEX.search(joined)
                    if m_total and len(row) >= 2:
                        tipo_total = m_total.group(1).upper()
                        raw_val = row[-1]
                        current_block["totais"][tipo_total] = _parse_money(raw_val)
                        continue
        if current_block:
            blocks.append(current_block)
    mb_blocks = []
    for b in blocks:
        orgao_ok = (
            b["orgao"]
            and (
                "DEFESA" in b["orgao"].upper()
                or "52000" in b["orgao"]
            )
        )
        uo_ok = (b["uo_code"] in MB_UOS)
        if orgao_ok or uo_ok:
            mb_blocks.append(b)
    if not mb_blocks:
        return (
            "Publicação orçamentária do MPO potencialmente relevante, "
            "mas não foi possível extrair valores específicos das UOs da Marinha/Defesa nos anexos."
        )
    grouped = {}
    for b in mb_blocks:
        if b["uo_code"] and b["uo_name"]:
            uo_key = f"{b['uo_code']} - {b['uo_name']}"
        elif b["orgao"]:
            uo_key = b["orgao"]
        else:
            uo_key = "Unidade não identificada"
        grouped.setdefault(uo_key, []).append(b)
    out_lines = []
    out_lines.append(
        "Ato orçamentário do MPO com impacto na Defesa/Marinha. Dados extraídos automaticamente:\n"
    )
    for uo_key, lista in grouped.items():
        nice_key = uo_key
        m_code = re.match(r"^(\d{5})", uo_key)
        if m_code:
            code = m_code.group(1)
            if code in MB_UOS:
                nice_key = f"{code} - {MB_UOS[code]}"
        out_lines.append(f"*{nice_key}*")
        for b in lista:
            tipo_legenda = (
                "Suplementação (ACRÉSCIMO)"
                if b["tipo"] == "ACRÉSCIMO"
                else "Cancelamento (REDUÇÃO)"
            )
            total_fiscal = b["totais"].get("FISCAL", 0)
            total_geral  = b["totais"].get("GERAL", 0)
            total_base = total_fiscal if total_fiscal else total_geral
            if total_base:
                val_fmt = f"R$ {total_base:,}".replace(",", ".")
                out_lines.append(f"  - {tipo_legenda}: {val_fmt}")
            else:
                out_lines.append(f"  - {tipo_legenda} (valores por ação abaixo)")
            for acao in b["acoes"][:6]:
                val_fmt = f"R$ {acao['valor']:,}".replace(",", ".")
                desc_curta = acao["desc"]
                out_lines.append(
                    f"    • {acao['acao']} {desc_curta} — {val_fmt}"
                )
        out_lines.append("")
    return "\n".join(out_lines).strip()

# =====================================================================================
# CLASSIFICAÇÃO INICIAL (SEM IA)
# =====================================================================================
def process_grouped_materia(
    main_article: BeautifulSoup,
    full_text_content: str,
    custom_keywords: List[str],
) -> Optional[Publicacao]:
    # --- [MODIFICAÇÃO v14.0.2 - Filtro MF] ---
    organ = norm(main_article.get("artCategory", ""))
    organ_lower = organ.lower()
    if (
        "comando da aeronáutica" in organ_lower
        or "comando do exército" in organ_lower
    ):
        return None
    section = (main_article.get("pubName", "") or "").upper()
    body = main_article.find("body")
    if not body:
        return None
    act_type = norm(
        body.find("Identifica").get_text(strip=True)
        if body.find("Identifica")
        else ""
    )
    if not act_type:
        return None
    summary = norm(
        body.find("Ementa").get_text(strip=True)
        if body.find("Ementa")
        else ""
    )
    display_text = norm(body.get_text(strip=True))
    if not summary:
        match = re.search(
            r"EMENTA:(.*?)(Vistos|ACORDAM)",
            display_text,
            re.DOTALL | re.I,
        )
        if match:
            summary = norm(match.group(1))
    
    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()
    clean_text_for_ia = ""
    is_mpo_navy_hit_flag = False
    
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ_lower
        is_mf = "ministério da fazenda" in organ_lower

        # --- ETAPA 1: MPO + UOs da Marinha (MAIOR PRIORIDADE) ---
        if is_mpo:
            found_navy_codes = [
                code for code in MPO_NAVY_TAGS
                if code.lower() in search_content_lower
            ]
            if found_navy_codes:
                is_relevant = True
                found_specific = [c for c in found_navy_codes if c != "52000"]
                found_defesa = "52000" in found_navy_codes
                if found_specific:
                    is_mpo_navy_hit_flag = True
                elif found_defesa and found_specific:
                    is_mpo_navy_hit_flag = True
                else:
                    is_mpo_navy_hit_flag = False
                
                summary_lower = summary.lower()
                gatilho_gnd = (
                    "grupo de natureza da despesa" in summary_lower
                    or "grupos de natureza da despesa" in summary_lower
                    or "gnd" in summary_lower
                    or "natureza da despesa" in summary_lower
                    or "altera parcialmente grupos" in summary_lower
                    or "adequa os grupos de natureza" in summary_lower
                    or "adequa os grupos de natureza da despesa" in summary_lower
                )
                gatilho_lme = (
                    "limites de movimentação e empenho" in summary_lower
                    or "limite de movimentação e empenho" in summary_lower
                    or "ajusta os limites de movimentação e empenho" in summary_lower
                    or "antecipa os limites de movimentação e empenho" in summary_lower
                    or "lme" in summary_lower
                )
                gatilho_fonte = (
                    "fonte de recursos" in summary_lower
                    or "fontes de recursos" in summary_lower
                    or "reclassificação de fonte" in summary_lower
                    or "altera a fonte" in summary_lower
                    or "modifica fontes de recursos" in summary_lower
                    or "alteração de fonte" in summary_lower
                    or "identificador de resultado primário" in summary_lower
                )
                gatilho_credito = (
                    "abre crédito suplementar" in summary_lower
                    or "crédito suplementar" in summary_lower
                    or "abre aos orçamentos fiscal" in summary_lower
                    or "suplementa dotações" in summary_lower
                    or "reforço de dotações" in summary_lower
                    or "reforço de dotação" in summary_lower
                    or "suplementação de crédito" in summary_lower
                    or "suplementação de dotações" in summary_lower
                    or "crédito suplementar no valor de" in summary_lower
                )
                if gatilho_gnd or gatilho_lme or gatilho_fonte or gatilho_credito:
                    reason = parse_mpo_budget_table(full_text_content)
                else:
                    reason = (
                        ANNOTATION_POSITIVE_GENERIC
                        or "Publicação potencialmente relevante para a Marinha. Recomenda-se análise detalhada."
                    )

        # --- ETAPA 2: Keywords de Interesse Direto (Ex: "Comando da Marinha") ---
        if not is_relevant:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break
        
        # --- ETAPA 3: Keywords de Orçamento (SÓ SE FOR MPO ou MF) ---
        if not is_relevant and (is_mpo or is_mf):
            if any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                if is_mpo:
                    reason = (
                        ANNOTATION_NEGATIVE
                        or "Ato orçamentário do MPO, mas não foi possível confirmar impacto direto na Marinha."
                    )
                else:
                    # É MF e tem keyword de orçamento (Nova lógica v14.0.2)
                    reason = "Ato orçamentário do Ministério da Fazenda com potencial impacto orçamentário."

    elif "DO2" in section:
        # --- Lógica da Seção 2 (DO2) ---
        soup_copy = BeautifulSoup(full_text_content, "lxml-xml")
        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]):
            tag.decompose()
        clean_search_content_lower = norm(
            soup_copy.get_text(strip=True)
        ).lower()
        
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{term}'."
                break
        
        if not is_relevant:
            for name in NAMES_TO_TRACK:
                name_lower = name.lower()
                for match in re.finditer(name_lower, clean_search_content_lower):
                    start_pos = max(0, match.start() - 150)
                    ctx = clean_search_content_lower[start_pos: match.start()]
                    if any(verb in ctx for verb in PERSONNEL_ACTION_VERBS):
                        is_relevant = True
                        reason = (
                            f"Ato de pessoal (Seção 2): menção a '{name}' em contexto de ação."
                        )
                        break
                if is_relevant:
                    break
    
    # --- Lógica de Keywords Customizadas ---
    found_custom_kw = None
    custom_reason_text = None
    if custom_keywords:
        for kw in custom_keywords:
            if kw and kw.lower() in search_content_lower:
                found_custom_kw = kw
                custom_reason_text = (
                    f"Há menção à palavra-chave personalizada: '{kw}'."
                )
                break
    
    if found_custom_kw:
        is_relevant = True
        if reason and reason != ANNOTATION_NEGATIVE:
            reason = f"{reason}\n⚓ {custom_reason_text}"
        elif (not reason) or reason == ANNOTATION_NEGATIVE:
            reason = custom_reason_text
            
    # --- Montagem final ---
    if is_relevant:
        soup_full_clean = BeautifulSoup(full_text_content, "lxml-xml")
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
        raise HTTPException(
            status_code=500,
            detail="Config ausente: INLABS_USER e INLABS_PASS.",
        )
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try:
        await client.get(INLABS_BASE)
    except Exception:
        pass
    r = await client.post(
        INLABS_LOGIN_URL,
        data={"email": INLABS_USER, "password": INLABS_PASS},
    )
    if r.status_code >= 400:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Falha de login no INLABS: HTTP {r.status_code}",
        )
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    r = await client.get(INLABS_BASE)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cand_texts = [
        date,
        date.replace("-", "_"),
        date.replace("-", ""),
    ]
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        txt = (a.get_text() or "").strip()
        hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts):
            return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"
    rr = await client.get(fallback_url)
    if rr.status_code == 200:
        return fallback_url
    raise HTTPException(
        status_code=404,
        detail=f"Não encontrei a pasta/listagem da data {date} após login.",
    )

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    base_url = await resolve_date_url(client, date)
    r = await client.get(base_url)
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao abrir listagem {base_url}: HTTP {r.status_code}",
        )
    return r.text

def pick_zip_links_from_listing(
    html: str,
    base_url_for_rel: str,
    only_sections: List[str],
) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip"):
            label = (a.get_text() or href).upper()
            if any(sec in label for sec in wanted):
                links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))

async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url)
    if r.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}",
        )
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    xml_blobs: List[bytes] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"):
                xml_blobs.append(z.read(name))
    return xml_blobs

# =====================================================================================
# /processar-inlabs (SEM IA) - Endpoint Rápido
# =====================================================================================
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(
        None,
        description='JSON lista de keywords. Ex: \'["amazul","prosub"]\'',
    ),
):
    secs = (
        [s.strip().upper() for s in sections.split(",") if s.strip()]
        if sections
        else ["DO1"]
    )
    custom_keywords: List[str] = []
    if keywords_json:
        try:
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [
                    str(k).strip().lower()
                    for k in keywords_list
                    if str(k).strip()
                ]
        except json.JSONDecodeError:
            pass
    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(
                status_code=404,
                detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.",
            )
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))
        materias: Dict[str, Dict[str, Any]] = {}
        for blob in all_xml_blobs:
            try:
                soup = BeautifulSoup(blob, "lxml-xml")
                article = soup.find("article")
                if not article:
                    continue
                materia_id = article.get("idMateria")
                if not materia_id:
                    continue
                if materia_id not in materias:
                    materias[materia_id] = {
                        "main_article": None,
                        "full_text": "",
                    }
                materias[materia_id]["full_text"] += (
                    blob.decode("utf-8", errors="ignore") + "\n"
                )
                body = article.find("body")
                if (
                    body
                    and body.find("Identifica")
                    and body.find("Identifica").get_text(strip=True)
                ):
                    materias[materia_id]["main_article"] = article
            except Exception:
                continue
        pubs: List[Publicacao] = []
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"],
                    content["full_text"],
                    custom_keywords,
                )
                if publication:
                    pubs.append(publication)
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (
                (p.organ or "")
                + "||"
                + (p.type or "")
                + "||"
                + (p.summary or "")[:100]
            )
            if key not in seen:
                seen.add(key)
                merged.append(p)
        texto = monta_whatsapp(merged, data)
        return ProcessResponse(
            date=data,
            count=len(merged),
            publications=merged,
            whatsapp_text=texto,
        )
    finally:
        await client.aclose()


# =====================================================================================
# IA helper
# =====================================================================================
async def get_ai_analysis(
    clean_text: str,
    model: genai.GenerativeModel,
    prompt_template: str = GEMINI_MASTER_PROMPT,
) -> Optional[str]:
    try:
        prompt = f"{prompt_template}\n\n{clean_text}"
        response = await model.generate_content_async(prompt)
        try:
            analysis = norm(response.text)
            if analysis:
                return analysis
            else:
                try:
                    finish_reason = response.prompt_feedback.finish_reason.name
                except Exception:
                    finish_reason = "desconhecido"
                print(f"Resposta da IA vazia. Razão: {finish_reason}")
                return None
        except ValueError as e:
            print(f"Bloco de IA (ValueError): {e}")
            return None
        except Exception as e_inner:
            print(f"Erro inesperado ao processar resposta da IA: {e_inner}")
            return "Erro processando resposta IA: " + str(e_inner)[:50]
    except Exception as e:
        print(f"Erro na API do Gemini: {e}")
        msg = str(e).lower()
        if "quota" in msg:
            return "Erro na análise de IA: Cota de uso da API excedida."
        if "api_key" in msg:
            return "Erro na análise de IA: Chave de API inválida."
        return "Erro na análise de IA: " + str(e)[:100]


# =====================================================================================
# FUNÇÃO DE ANÁLISE DO VALOR
# =====================================================================================
async def run_valor_analysis(today_str: str, use_state: bool = True) -> (List[Dict[str, Any]], Set[str]):
    """
    Função principal de análise do Valor.
    Busca, analisa com IA e retorna uma lista de publicações relevantes.
    """
    
    # 0. Configura a IA
    if not GEMINI_API_KEY:
        print("Erro (Valor): GEMINI_API_KEY não encontrada.")
        return [], set()
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") # Modelo atualizado
    except Exception as e:
        print(f"Falha (Valor) ao inicializar o modelo de IA: {e}")
        return [], set()

    # 1. Carrega o estado (links já vistos)
    if use_state:
        pass
    
    # 2. Busca os links de hoje
    all_results: Dict[str, SearchResult] = {}
    
    for query in SEARCH_QUERIES:
        print(f"Buscando query: {query} para a data {today_str}")
        
        results = await perform_google_search(query, search_date=today_str)
        
        for res in results:
            if res.link not in all_results:
                all_results[res.link] = res
        await asyncio.sleep(1) 

    if not all_results:
        print(f"Nenhuma notícia encontrada no Valor para a data {today_str}.")
        return [], set()

    results_to_process = list(all_results.values())

    if not results_to_process:
        print("Nenhuma notícia *nova* encontrada no Valor (ou já processada).")
        return [], set()
    
    print(f"Encontradas {len(results_to_process)} notícias no Valor. Analisando com IA...")

    # 4. Analisa com IA
    pubs_finais = []
    links_encontrados = set()

    for res in results_to_process:
        prompt = GEMINI_VALOR_PROMPT.format(titulo=res.title, resumo=res.snippet)
        
        ai_reason = await get_ai_analysis(
            clean_text=f"TÍTULO: {res.title}\nSNIPPET: {res.snippet}",
            model=model,
            prompt_template=GEMINI_VALOR_PROMPT
        )
        
        links_encontrados.add(res.link)

        if ai_reason:
            pubs_finais.append({
                "titulo": res.title,
                "link": res.link,
                "analise_ia": ai_reason
            })

    if not pubs_finais:
        print("Análise da IA não retornou nenhuma razão.")
        return [], links_encontrados

    # 5. Retorna os resultados brutos
    return pubs_finais, links_encontrados


# =====================================================================================
# /processar-dou-ia (COM IA) - Endpoint Lento (DOU)
# =====================================================================================
@app.post("/processar-dou-ia", response_model=ProcessResponse)
async def processar_dou_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(
        None, description="JSON string de keywords"
    ),
):
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="A variável GEMINI_API_KEY não está definida.",
        )
    try:
        model = genai.GenerativeModel("gemini-2.5-flash") 
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao inicializar o modelo de IA: {e}",
        )
    secs = (
        [s.strip().upper() for s in sections.split(",") if s.strip()]
        if sections
        else ["DO1"]
    )
    custom_keywords: List[str] = []
    if keywords_json:
        try:
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [
                    str(k).strip().lower()
                    for k in keywords_list
                    if str(k).strip()
                ]
        except json.JSONDecodeError:
            pass
    client = await inlabs_login_and_get_session()
    pubs_filtradas: List[Publicacao] = []
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(
                status_code=404,
                detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.",
            )
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))
        materias: Dict[str, Dict[str, Any]] = {}
        for blob in all_xml_blobs:
            try:
                soup = BeautifulSoup(blob, "lxml-xml")
                article = soup.find("article")
                if not article:
                    continue
                materia_id = article.get("idMateria")
                if not materia_id:
                    continue
                if materia_id not in materias:
                    materias[materia_id] = {
                        "main_article": None,
                        "full_text": "",
                    }
                materias[materia_id]["full_text"] += (
                    blob.decode("utf-8", errors="ignore") + "\n"
                )
                body = article.find("body")
                if (
                    body
                    and body.find("Identifica")
                    and body.find("Identifica").get_text(strip=True)
                ):
                    materias[materia_id]["main_article"] = article
            except Exception:
                continue
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"],
                    content["full_text"],
                    custom_keywords,
                )
                if publication:
                    pubs_filtradas.append(publication)
        seen: Set[str] = set()
        merged_pubs: List[Publicacao] = []
        for p in pubs_filtradas:
            key = (
                (p.organ or "")
                + "||"
                + (p.type or "")
                + "||"
                + (p.summary or "")[:100]
            )
            if key not in seen:
                seen.add(key)
                merged_pubs.append(p)
        tasks = []
        for p in merged_pubs:
            prompt_to_use = GEMINI_MASTER_PROMPT
            if p.is_mpo_navy_hit:
                prompt_to_use = GEMINI_MPO_PROMPT
            if p.clean_text:
                tasks.append(get_ai_analysis(p.clean_text, model, prompt_to_use))
            else:
                tasks.append(
                    get_ai_analysis(
                        p.relevance_reason or "Texto não disponível",
                        model,
                        prompt_to_use,
                    )
                )
        ai_results = await asyncio.gather(*tasks, return_exceptions=True)
        pubs_finais: List[Publicacao] = []
        for p, ai_out in zip(merged_pubs, ai_results):
            if isinstance(ai_out, Exception):
                p.relevance_reason = f"Erro GRAVE na análise de IA: {ai_out}"
                pubs_finais.append(p)
                continue
            if ai_out is None:
                pubs_finais.append(p)
                continue
            if isinstance(ai_out, str):
                lower_ai = ai_out.lower()
                if ai_out.startswith("Erro na análise de IA:"):
                    p.relevance_reason = ai_out
                    pubs_finais.append(p)
                    continue
                if "sem impacto direto" in lower_ai:
                    if p.is_mpo_navy_hit:
                        p.relevance_reason = "⚠️ IA ignorou impacto MPO: " + ai_out
                        pubs_finais.append(p)
                    elif MPO_ORG_STRING in (p.organ or "").lower():
                        p.relevance_reason = ai_out
                        pubs_finais.append(p)
                    else:
                        pass
                    continue
                p.relevance_reason = ai_out
                pubs_finais.append(p)
                continue
            pubs_finais.append(p)
        texto = monta_whatsapp(pubs_finais, data)
        return ProcessResponse(
            date=data,
            count=len(pubs_finais),
            publications=pubs_finais,
            whatsapp_text=texto,
        )
    finally:
        await client.aclose()


# =====================================================================================
# /processar-valor-ia (COM IA) - Endpoint Lento (Valor)
# =====================================================================================
@app.post("/processar-valor-ia", response_model=ProcessResponseValor)
async def processar_valor_ia(
    data: str = Form(..., description="YYYY-MM-DD")
):
    """
    Pipeline com IA (Valor Econômico):
    - Chama a função interna 'run_valor_analysis'
    - NÃO usa o state, para permitir re-análise manual
    - Retorna o resultado formatado
    """
    
    pubs_list, _ = await run_valor_analysis(data, use_state=False)

    pubs_model = [ValorPublicacao(**p) for p in pubs_list]
    texto = monta_valor_whatsapp(pubs_model, data)

    return ProcessResponseValor(
        date=data,
        count=len(pubs_model),
        publications=pubs_model,
        whatsapp_text=texto
    )


# =====================================================================================
# ENDPOINT - DASHBOARD PAC v1.1
# =====================================================================================

PROGRAMAS_ACOES_PAC = {
    'PROSUB': {
        '123G': 'IMPLANTACAO DE ESTALEIRO E BASE NAVAL',
        '123H': 'CONSTRUCAO DE SUBMARINO DE PROPULSAO NUCLEAR',
        '123I': 'CONSTRUCAO DE SUBMARINOS CONVENCIONAIS'
    },
    'PNM': {
        '14T7': 'DESENVOLVIMENTO DE TECNOLOGIA NUCLEAR'
    },
    'PRONAPA': {
        '1N47': 'CONSTRUCAO DE NAVIOS-PATRULHA 500T'
    }
}

async def buscar_dados_acao_pac(ano: int, acao_cod: str) -> Optional[Dict[str, Any]]:
    """
    Busca os dados de UMA ação, totalizados.
    """
    print(f"[PAC API] Buscando dados para {ano}, Ação {acao_cod}...")
    try:
        # Roda a função síncrona 'despesa_detalhada' em um thread
        df_detalhado = await asyncio.to_thread(
            despesa_detalhada,
            exercicio=ano,
            acao=acao_cod,
            inclui_descricoes=True,
            ignore_secure_certificate=True
        )
        
        if df_detalhado.empty:
            return None
            
        colunas_numericas = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago']
        colunas_para_somar = [col for col in colunas_numericas if col in df_detalhado.columns]
        
        if not colunas_para_somar:
            return None
            
        totais_acao = df_detalhado[colunas_para_somar].sum()
        
        # Adiciona o código da Ação para referência
        dados_finais = totais_acao.to_dict()
        dados_finais['Acao_cod'] = acao_cod
        return dados_finais

    except Exception as e:
        print(f"Erro ao consultar o SIOP (PAC API) para a ação {acao_cod}: {e}")
        return None # Retorna None em vez de lançar exceção para o gather


# [NOVO ENDPOINT - INÍCIO]
# Este endpoint deve vir ANTES do endpoint /{ano} para evitar o conflito "integer parsing"
@app.get("/api/pac-data/historical-dotacao", summary="Busca dados históricos de dotação (2010-2025) para o gráfico")
async def get_pac_historical_data():
    """
    Endpoint para o gráfico principal do dashboard PAC.
    Lê o arquivo JSON pré-compilado pelo robô (check_pac.py).
    """
    try:
        with open(HISTORICAL_CACHE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Arquivo de cache histórico não encontrado. O robô pode estar gerando o arquivo pela primeira vez. Tente novamente em alguns minutos."
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao ler o arquivo de cache: {e}"
        )
# [NOVO ENDPOINT - FIM]


# Endpoint genérico para ano específico (DEVE vir DEPOIS do historical-dotacao)
@app.get("/api/pac-data/{ano}", summary="Busca dados de execução do PAC por ano")
async def get_pac_data(
    ano: int = Path(..., description="Ano do exercício (ex: 2010)", ge=2010, le=2025)
):
    """
    Endpoint para o frontend do dashboard PAC.
    Busca os dados de todas as ações e retorna a tabela formatada como JSON.
    """
    
    # Cria uma lista de tarefas para buscar dados em paralelo
    tasks = []
    
    for programa, acoes in PROGRAMAS_ACOES_PAC.items():
        for acao_cod in acoes.keys():
            tasks.append(buscar_dados_acao_pac(ano, acao_cod))

    # Executa todas as buscas
    resultados_brutos = await asyncio.gather(*tasks)
    
    # Filtra dados brutos (remove None se houver falhas)
    dados_brutos = [r for r in resultados_brutos if r is not None]
    
    if not dados_brutos:
         raise HTTPException(
            status_code=404,
            detail=f"Nenhum dado encontrado para as ações do PAC em {ano}.",
        )

    # --- Monta a Tabela JSON para o Frontend ---
    tabela_final = []
    total_geral = {
        'LOA': 0.0, 'DOTAÇÃO ATUAL': 0.0, 'EMPENHADO (c)': 0.0,
        'LIQUIDADO': 0.0, 'PAGO': 0.0
    }

    for programa, acoes in PROGRAMAS_ACOES_PAC.items():
        # 1. Linha de Sumário do Programa
        soma_programa = {
            'LOA': 0.0, 'DOTAÇÃO ATUAL': 0.0, 'EMPENHADO (c)': 0.0,
            'LIQUIDADO': 0.0, 'PAGO': 0.0
        }
        
        linhas_acao_programa = []
        
        # 2. Linhas de Ação
        for acao_cod, acao_desc in acoes.items():
            # Encontra o dado bruto correspondente
            row_data = next((d for d in dados_brutos if d.get('Acao_cod') == acao_cod), None)
            
            loa = row_data.get('loa', 0.0) if row_data else 0.0
            dot_atual = row_data.get('loa_mais_credito', 0.0) if row_data else 0.0
            empenhado = row_data.get('empenhado', 0.0) if row_data else 0.0
            liquidado = row_data.get('liquidado', 0.0) if row_data else 0.0
            pago = row_data.get('pago', 0.0) if row_data else 0.0

            # Adiciona na linha da ação
            linhas_acao_programa.append({
                'PROGRAMA': None,
                'AÇÃO': f"{acao_cod} - {acao_desc.upper()}",
                'LOA': loa,
                'DOTAÇÃO ATUAL': dot_atual,
                'EMPENHADO (c)': empenhado,
                'LIQUIDADO': liquidado,
                'PAGO': pago,
                '% EMP/DOT': (empenhado / dot_atual) if dot_atual else 0.0
            })
            
            # Acumula no total do programa
            soma_programa['LOA'] += loa
            soma_programa['DOTAÇÃO ATUAL'] += dot_atual
            soma_programa['EMPENHADO (c)'] += empenhado
            soma_programa['LIQUIDADO'] += liquidado
            soma_programa['PAGO'] += pago

        # Adiciona a linha de total do programa
        tabela_final.append({
            'PROGRAMA': programa,
            'AÇÃO': None,
            **soma_programa,
            '% EMP/DOT': (soma_programa['EMPENHADO (c)'] / soma_programa['DOTAÇÃO ATUAL']) if soma_programa['DOTAÇÃO ATUAL'] else 0.0
        })
        
        # Adiciona as linhas de ação
        tabela_final.extend(linhas_acao_programa)
        
        # Acumula no total geral
        total_geral['LOA'] += soma_programa['LOA']
        total_geral['DOTAÇÃO ATUAL'] += soma_programa['DOTAÇÃO ATUAL']
        total_geral['EMPENHADO (c)'] += soma_programa['EMPENHADO (c)']
        total_geral['LIQUIDADO'] += soma_programa['LIQUIDADO']
        total_geral['PAGO'] += soma_programa['PAGO']
        
    # 3. Linha de Total Geral
    tabela_final.append({
        'PROGRAMA': 'Total Geral',
        'AÇÃO': None,
        **total_geral,
        '% EMP/DOT': (total_geral['EMPENHADO (c)'] / total_geral['DOTAÇÃO ATUAL']) if total_geral['DOTAÇÃO ATUAL'] else 0.0
    })

    return tabela_final


# [NOVO ENDPOINT MANUAL]
@app.post("/api/admin/force-update-pac")
async def force_update_pac():
    """
    Endpoint manual para forçar a geração do JSON histórico AGORA.
    Útil para popular o gráfico imediatamente após o deploy.
    """
    print("Forçando atualização do cache histórico do PAC...")
    await update_pac_historical_cache()
    return {"status": "Cache histórico atualizado com sucesso! Recarregue o dashboard."}


# =====================================================================================
# HEALTHCHECK E TESTE IA
# =====================================================================================

@app.get("/")
async def root():
    return {"status": "ok", "ts": datetime.now().isoformat()}

@app.get("/test-ia")
async def test_ia_endpoint():
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY não configurada.",
        )
    try:
        model = genai.GenerativeModel("gemini-1.5-pro") # Modelo atualizado
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao inicializar modelo IA: {e}",
        )
    test_prompt = "Qual é a capital do Brasil?"
    print(f"[TESTE IA] Pergunta: {test_prompt}")
    try:
        response = await model.generate_content_async(test_prompt)
        try:
            analysis = norm(response.text)
            if analysis:
                print(f"[TESTE IA] OK: {analysis}")
                return {
                    "result": f"Teste OK! IA respondeu: '{analysis}'",
                    "ts": datetime.now().isoformat(),
                }
            else:
                print("[TESTE IA] Falhou: resposta vazia")
                return {
                    "result": "Teste FALHOU. Resposta vazia da IA.",
                    "ts": datetime.now().isoformat(),
                }
        except ValueError as e:
            print(f"[TESTE IA] Falhou (ValueError): {e}")
            return {
                "result": "Teste FALHOU. A IA bloqueou a resposta.",
                "detail": str(e),
                "ts": datetime.now().isoformat(),
            }
        except Exception as e_inner:
            print(f"[TESTE IA] Falhou (parse): {e_inner}")
            return {
                "result": "Teste FALHOU. Erro processando resposta da IA.",
                "detail": str(e_inner)[:200],
                "ts": datetime.now().isoformat(),
            }
    except Exception as e:
        print(f"[TESTE IA] Falhou (API): {e}")
        detail = str(e)[:200]
        lower_msg = str(e).lower()
        if "quota" in lower_msg:
            detail = "Cota de uso da API excedida."
        elif "api_key" in lower_msg:
            detail = "Chave de API inválida."
        raise HTTPException(
            status_code=500,
            detail=f"Teste FALHOU. Erro na chamada da API: {detail}",
        )
