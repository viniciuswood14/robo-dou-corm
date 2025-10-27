from fastapi import FastAPI, Form, HTTPException
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

# =====================================================================================
# Robô DOU API - versão 13.9.4
#
# Diferenças principais:
# - parse_mpo_budget_table() atualizado com regex tolerante
#   (ÓRGÃO SUPERIOR, UNIDADE ORÇAMENTÁRIA, UG, etc),
#   detecta blocos (ACRÉSCIMO)/(REDUÇÃO) mesmo sem o prefixo "PROGRAMA DE TRABALHO",
#   captura totais "TOTAL - FISCAL" / "TOTAL - GERAL",
#   agrupa por UO (52131, 52931, etc.) e monta texto WhatsApp com valores 💸.
#
# - process_grouped_materia() chama parse_mpo_budget_table() sempre que for ato MPO
#   de LME / Fonte / Crédito / GND e houver códigos de Marinha.
#
# - Rotas /processar-inlabs e /processar-inlabs-ia mantidas.
# =====================================================================================

app = FastAPI(
    title="Robô DOU API (INLABS XML) - v13.9.4 (MPO parser valores Marinha)"
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

# Constantes auxiliares (templates e listas de palavras-chave)
TEMPLATE_LME = config.get("TEMPLATE_LME", "")
TEMPLATE_FONTE = config.get("TEMPLATE_FONTE", "")
TEMPLATE_CREDITO = config.get("TEMPLATE_CREDITO", "")
ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})  # códigos UO -> nome
KEYWORDS_DIRECT_INTEREST_S1 = config.get("KEYWORDS_DIRECT_INTEREST_S1", [])
BUDGET_KEYWORDS_S1 = config.get("BUDGET_KEYWORDS_S1", [])
MPO_ORG_STRING = config.get(
    "MPO_ORG_STRING", "ministério do planejamento e orçamento"
)
PERSONNEL_ACTION_VERBS = config.get("PERSONNEL_ACTION_VERBS", [])
TERMS_AND_ACRONYMS_S2 = config.get("TERMS_AND_ACRONYMS_S2", [])
NAMES_TO_TRACK = sorted(
    list(set(config.get("NAMES_TO_TRACK", []))), key=str.lower
)

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
3.  Se for ato de pessoal (Seção 2), quem é a pessoa e qual a ação (nomeação, exoneração, viagem)?
4.  Se a menção for trivial ou sem impacto direto (ex: 'Ministério da Defesa' apenas listado numa reunião, ou 'Marinha' no nome de uma empresa privada), responda APENAS: "Sem impacto direto."

Responda só a frase final, sem rodeio adicional.

TEXTO DA PUBLICAÇÃO:
"""

GEMINI_MPO_PROMPT = """
Você é analista orçamentário da Marinha do Brasil. A publicação abaixo é do Ministério do Planejamento e Orçamento (MPO) e JÁ FOI CLASSIFICADA como tendo impacto direto em dotações ligadas à Marinha (ex.: Fundo Naval, Comando da Marinha, etc.).

Sua tarefa é:
1. Dizer claramente qual o efeito orçamentário: crédito suplementar (reforço de dotação), alteração de GND (reclassificação da natureza da despesa), mudança de fonte de recursos, antecipação/ajuste de LME etc.
2. Dizer quem é afetado (Ex.: Comando da Marinha, Fundo Naval, Defesa/52000).
3. Se houver reforço de dotação ou acréscimo, deixe isso claro como positivo. Se houver cancelamento/redução, diga isso também.
4. Entregar apenas UMA frase curta (máximo 2 linhas) para WhatsApp.

Você NÃO pode responder "Sem impacto direto", porque esta portaria JÁ foi marcada como relevante para a MB.

TEXTO DA PUBLICAÇÃO:
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

    # agrupar publicações por seção (DO1, DO2...)
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

            # Se a IA disser que errou, a gente marca com ⚠️
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
    "52000": "Ministério da Defesa",  # Defesa inteira
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
    """
    Extrai dados orçamentários das tabelas MPO (inclusive dentro de CDATA),
    filtra apenas blocos ligados ao Ministério da Defesa / UOs da Marinha
    e gera texto pronto pro WhatsApp.

    Melhorias:
    - Detecta quando é portaria de alteração de fonte/IRP (ex: Portaria SOF/MPO nº 402),
      e muda o tom da mensagem ("Alteração de fonte de recurso...") em vez de
      "Suplementação/Redução".
    - Consolida ações parecidas (evita duplicar 21GN / 21GN 0001).
    """

    # -------------------------------------------------
    # Helpers locais
    # -------------------------------------------------
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

    def _canonicalize_action_code(raw_code: str) -> str:
        """
        Normaliza código de ação para agrupar variantes tipo:
        "6112 21GN" vs "6112 21GN 0001"
        -> mantém só os dois primeiros blocos numéricos/letras.
        """
        parts = raw_code.split()
        if len(parts) >= 2:
            # Ex: ["6112","21GN","0001"] -> "6112 21GN"
            return " ".join(parts[:2])
        return raw_code.strip()

    MB_UOS = {
        "52131": "Comando da Marinha",
        "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
        "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
        "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
        "52931": "Fundo Naval",
        "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
        "52000": "Ministério da Defesa",  # nível ministério
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

    # -------------------------------------------------
    # 1. Detectar se essa matéria é de alteração de fonte / IRP
    #    (isso muda o texto que vamos montar no final)
    # -------------------------------------------------
    lower_all = full_text_content.lower()
    is_alteracao_fonte = any(
        kw in lower_all
        for kw in [
            "modifica fontes de recursos",
            "modifica fonte de recursos",
            "fonte de recurso",
            "fontes de recursos",
            "identificador de resultado primário",
            "identificador de resultado primario",
            "alteração de fonte",
            "alteracao de fonte",
            "reclassificação de fonte",
            "reclassificacao de fonte",
        ]
    )

    # -------------------------------------------------
    # 2. Extrair TODOS os blocos CDATA (é onde estão as tabelas MPO)
    # -------------------------------------------------
    cdata_blocks = re.findall(
        r"<!\[CDATA\[(.*?)\]\]>",
        full_text_content,
        flags=re.DOTALL | re.IGNORECASE
    )
    if not cdata_blocks:
        # fallback: tenta usar tudo mesmo assim
        cdata_blocks = [full_text_content]

    # -------------------------------------------------
    # Estrutura intermediária
    # Cada bloco orçamentário detectado ficará assim:
    # {
    #   "orgao": "52000 - Ministério da Defesa",
    #   "uo_code": "52931",
    #   "uo_name": "Fundo Naval",
    #   "tipo": "ACRÉSCIMO" / "REDUÇÃO" (ou None se não detectamos),
    #   "acoes": [ { "acao": "6112 21GN", "desc": "...", "valor": 1906510 }, ... ],
    #   "totais": { "FISCAL": 5271675, "GERAL": ... }
    # }
    # -------------------------------------------------
    all_detected_blocks = []

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
            # transforma tabela em linhas limpas
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

                # Detecta ÓRGÃO (ex: "ÓRGÃO: 52000 - Ministério da Defesa")
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

                # Detecta UNIDADE (ex: "UNIDADE: 52931 - Fundo Naval")
                m_uo = UO_REGEX.search(joined)
                if m_uo:
                    numero = m_uo.group(2).strip()
                    nome = m_uo.group(3).strip()
                    current_uo_code = numero
                    current_uo_name = nome
                    current_tipo = None
                    current_block = None
                    continue

                # Detecta início de sub-bloco "(ACRÉSCIMO)" ou "(REDUÇÃO)"
                m_tipo = BLOCO_TIPO_REGEX.search(joined)
                if m_tipo:
                    tipo_raw = m_tipo.group(1).upper()
                    # Salva bloco anterior
                    if current_block:
                        all_detected_blocks.append(current_block)

                    if "ACR" in tipo_raw:
                        current_tipo = "ACRÉSCIMO"
                    else:
                        current_tipo = "REDUÇÃO"
                    current_block = start_new_block()
                    continue

                # Se estamos dentro de um bloco, tentar extrair conteúdo
                if current_block:
                    # Possível linha de ação orçamentária com valor:
                    # primeira coluna = código (ex: "6112 21GN" ou "6112 21GN 0001")
                    # última coluna = valor numérico
                    possible_code = row[0].strip() if len(row) > 0 else ""
                    possible_val  = row[-1].strip() if len(row) > 0 else ""
                    has_code = re.match(r"^\d{3,4}", possible_code) is not None
                    has_money = (
                        re.search(r"\d", possible_val) is not None
                        and _parse_money(possible_val) > 0
                    )

                    if has_code and has_money and len(row) >= 2:
                        desc = row[1].strip()
                        norm_code = _canonicalize_action_code(possible_code)
                        current_block["acoes"].append({
                            "acao": norm_code,
                            "desc": desc,
                            "valor": _parse_money(possible_val),
                        })
                        continue

                    # Totais "TOTAL - FISCAL   5.271.675"
                    m_total = TOTAL_REGEX.search(joined)
                    if m_total and len(row) >= 2:
                        tipo_total = m_total.group(1).upper()
                        raw_val = row[-1]
                        current_block["totais"][tipo_total] = _parse_money(raw_val)
                        continue

        # fim das tabelas -> guarda último bloco aberto
        if current_block:
            all_detected_blocks.append(current_block)

    # -------------------------------------------------
    # 3. Filtrar apenas blocos Defesa/Marinha
    # -------------------------------------------------
    mb_blocks = []
    for b in all_detected_blocks:
        orgao_ok = (
            b["orgao"]
            and ("DEFESA" in b["orgao"].upper() or "52000" in b["orgao"])
        )
        uo_ok = (b["uo_code"] in MB_UOS)

        if orgao_ok or uo_ok:
            mb_blocks.append(b)

    if not mb_blocks:
        return (
            "Publicação orçamentária do MPO potencialmente relevante, "
            "mas não foi possível extrair valores específicos das UOs da Marinha/Defesa nos anexos."
        )

    # -------------------------------------------------
    # 4. Consolidar ações repetidas
    #    (mesma ação-base aparece várias linhas '0001','0002' etc.)
    # -------------------------------------------------
    for b in mb_blocks:
        dedup: Dict[str, Dict[str, Any]] = {}
        for a in b["acoes"]:
            key = (a["acao"], a["desc"])
            if key not in dedup:
                dedup[key] = {
                    "acao": a["acao"],
                    "desc": a["desc"],
                    "valor": 0,
                }
            dedup[key]["valor"] += a["valor"]

        # agora tira itens idênticos demais tipo:
        #   "6112 21GN" vs "6112 21GN 0001"
        # a essa altura _canonicalize_action_code já transformou ambos pra "6112 21GN",
        # então eles já caíram juntos no mesmo key.
        b["acoes"] = list(dedup.values())

    # -------------------------------------------------
    # 5. Agrupar blocos por UO (UO code + nome amigável)
    # -------------------------------------------------
    grouped = {}  # uo_key -> [blocos]
    for b in mb_blocks:
        if b["uo_code"] and b["uo_name"]:
            uo_key = f"{b['uo_code']} - {b['uo_name']}"
        elif b["orgao"]:
            uo_key = b["orgao"]
        else:
            uo_key = "Unidade não identificada"
        grouped.setdefault(uo_key, []).append(b)

    # -------------------------------------------------
    # 6. Montar mensagem final
    # -------------------------------------------------
    out_lines = []

    if is_alteracao_fonte:
        out_lines.append(
            "Alteração de fonte de recurso/IRP com impacto na Defesa/Marinha. "
            "Recursos foram realocados entre fontes internas; valores a seguir:"
        )
    else:
        out_lines.append(
            "Ato orçamentário do MPO com impacto na Defesa/Marinha. Dados extraídos automaticamente:"
        )

    out_lines.append("")  # linha em branco

    for uo_key, blocos in grouped.items():
        # Se a UO existir na nossa tabela de nomes oficiais, usa o nome bonito
        nice_key = uo_key
        m_code = re.match(r"^(\d{5})", uo_key)
        if m_code:
            code = m_code.group(1)
            if code in MB_UOS:
                nice_key = f"{code} - {MB_UOS[code]}"

        out_lines.append(f"*{nice_key}*")

        # Junta totais por tipo (ACRÉSCIMO vs REDUÇÃO) dentro da mesma UO
        # e também prepara lista de ações
        for b in blocos:
            # decide o rótulo
            if is_alteracao_fonte:
                # não usar "Cancelamento (REDUÇÃO)" que soa negativo;
                # descreve genericamente ajuste interno:
                rotulo = "Ajuste interno de fonte"
            else:
                rotulo = (
                    "Suplementação (ACRÉSCIMO)"
                    if b["tipo"] == "ACRÉSCIMO"
                    else "Cancelamento (REDUÇÃO)"
                )

            total_fiscal = b["totais"].get("FISCAL", 0)
            total_geral = b["totais"].get("GERAL", 0)
            total_base = total_fiscal if total_fiscal else total_geral

            if total_base:
                val_fmt = f"R$ {total_base:,}".replace(",", ".")
                if is_alteracao_fonte:
                    out_lines.append(
                        f"  - {rotulo}: {val_fmt} (troca de fonte, não aumento líquido de gasto)"
                    )
                else:
                    out_lines.append(f"  - {rotulo}: {val_fmt}")
            else:
                out_lines.append(f"  - {rotulo}: valores por ação abaixo")

            # listar até ~5 ações mais relevantes
            for a in b["acoes"][:5]:
                val_fmt = f"R$ {a['valor']:,}".replace(",", ".")
                desc_curta = a["desc"]
                # deixar a descrição mais curta tirando repetições "do Ministério da Defesa" duplicadas?
                # vamos deixar por enquanto
                out_lines.append(f"    • {desc_curta} — {val_fmt}")

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
    """
    1. Decide se a matéria é relevante.
    2. Gera reason inicial (sem IA ou pré-IA).
    3. Marca is_mpo_navy_hit pra proteger relevância na IA.
    """

    organ = norm(main_article.get("artCategory", ""))
    organ_lower = organ.lower()

    # Ignorar conteúdos que são claramente só FAB ou Exército
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

    # fallback pra ementa
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

    # ---------------------
    # SEÇÃO 1 (DO1)
    # ---------------------
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ_lower

        if is_mpo:
            # Checa se esta portaria toca UOs da MB
            found_navy_codes = [
                code for code in MPO_NAVY_TAGS
                if code.lower() in search_content_lower
            ]

            if found_navy_codes:
                is_relevant = True

                # heurística de impacto direto
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

                # NOVO: sempre tenta extrair valores reais pra MPO relevante
                if gatilho_gnd or gatilho_lme or gatilho_fonte or gatilho_credito:
                    reason = parse_mpo_budget_table(full_text_content)
                else:
                    reason = (
                        ANNOTATION_POSITIVE_GENERIC
                        or "Publicação potencialmente relevante para a Marinha. Recomenda-se análise detalhada."
                    )

            # É MPO mas não achou códigos MB/Defesa
            elif any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = (
                    ANNOTATION_NEGATIVE
                    or "Ato orçamentário do MPO, mas não foi possível confirmar impacto direto na Marinha."
                )

        else:
            # Não é MPO, mas pode ter Marinha direta (EMA, DPC, DHN etc.)
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break

    # ---------------------
    # SEÇÃO 2 (DO2) - pessoal
    # ---------------------
    elif "DO2" in section:
        soup_copy = BeautifulSoup(full_text_content, "lxml-xml")
        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]):
            tag.decompose()

        clean_search_content_lower = norm(
            soup_copy.get_text(strip=True)
        ).lower()

        # 1) termos institucionais rastreados
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{term}'."
                break

        # 2) nome rastreado + verbo de ação
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

    # ---------------------
    # Palavras-chave personalizadas
    # ---------------------
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

    # ---------------------
    # Monta Publicacao se relevante
    # ---------------------
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

    # warm-up
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
    """
    Depois do login, acha a pasta da data desejada e retorna URL base.
    """
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

    # fallback: tenta {BASE}/{date}/
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
    """
    Seleciona os .zip relevantes (DO1, DO2, etc.) naquela data.
    """
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
    """
    Lê um ZIP em memória e retorna todos os XMLs (cada XML é um pedaço de uma matéria).
    """
    xml_blobs: List[bytes] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"):
                xml_blobs.append(z.read(name))

    return xml_blobs

# =====================================================================================
# /processar-inlabs (SEM IA)
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
    """
    Pipeline 'rápida', sem IA.
    - Login no InLabs
    - Baixa ZIPs do dia e extrai XML
    - Agrupa fragmentos por idMateria
    - process_grouped_materia decide relevância e reason
    - Gera WhatsApp final
    """

    secs = (
        [s.strip().upper() for s in sections.split(",") if s.strip()]
        if sections
        else ["DO1"]
    )

    # Keywords personalizadas
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

        # Agrupar por idMateria
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

        # Deduplicar publicações parecidas
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
    """
    Chama Gemini pra gerar UMA frase curta de impacto.
    Retornos possíveis:
      - string normal
      - "Erro na análise de IA: ..." (erro recuperável)
      - None (modelo não quis responder)
    """

    try:
        prompt = f"{prompt_template}\n\n{clean_text}"

        response = await model.generate_content_async(prompt)

        try:
            analysis = norm(response.text)
            if analysis:
                return analysis
            else:
                # resposta vazia
                try:
                    finish_reason = response.prompt_feedback.finish_reason.name
                except Exception:
                    finish_reason = "desconhecido"
                print(f"Resposta da IA vazia. Razão: {finish_reason}")
                return None

        except ValueError as e:
            # tipicamente bloqueio de segurança
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
# /processar-inlabs-ia (COM IA)
# =====================================================================================

@app.post("/processar-inlabs-ia", response_model=ProcessResponse)
async def processar_inlabs_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(
        None, description="JSON string de keywords"
    ),
):
    """
    Pipeline com IA:
    - faz todo o fluxo de /processar-inlabs
    - depois chama Gemini pra uma frase curta por item
    - protege MPO com impacto direto na Marinha (is_mpo_navy_hit)
    """

    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="A variável GEMINI_API_KEY não está definida.",
        )

    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
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

        # Estágio 1 (regra fixa)
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"],
                    content["full_text"],
                    custom_keywords,
                )
                if publication:
                    pubs_filtradas.append(publication)

        # Dedup
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

        # Estágio 2 (IA)
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
                # IA ficou muda, mantém reason original (que já pode ter vindo do parser MPO)
                pubs_finais.append(p)
                continue

            if isinstance(ai_out, str):
                lower_ai = ai_out.lower()

                if ai_out.startswith("Erro na análise de IA:"):
                    p.relevance_reason = ai_out
                    pubs_finais.append(p)
                    continue

                # IA disse "sem impacto direto"
                if "sem impacto direto" in lower_ai:
                    if p.is_mpo_navy_hit:
                        # a IA quis minimizar mas a gente já sabe que impacta MB
                        p.relevance_reason = "⚠️ IA ignorou impacto MPO: " + ai_out
                        pubs_finais.append(p)
                    elif MPO_ORG_STRING in (p.organ or "").lower():
                        # é MPO mas sem hit direto -> ok aceitar o 'sem impacto direto'
                        p.relevance_reason = ai_out
                        pubs_finais.append(p)
                    else:
                        # se não é MPO e IA falou que é irrelevante -> filtra fora
                        pass
                    continue

                # caso feliz: IA deu uma frase útil
                p.relevance_reason = ai_out
                pubs_finais.append(p)
                continue

            # fallback
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
# HEALTHCHECK
# =====================================================================================

@app.get("/")
async def root():
    return {"status": "ok", "ts": datetime.now().isoformat()}

# =====================================================================================
# TESTE IA
# =====================================================================================

@app.get("/test-ia")
async def test_ia_endpoint():
    if not GEMINI_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY não configurada.",
        )

    try:
        model = genai.GenerativeModel("gemini-2.5-pro")
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao inicializar modelo IA: {e}",
        )

    test_prompt = "Qual a capital do Brasil?"
    print(f"[TESTE IA] Pergunta: {test_prompt}")

    try:
        response = await model.generate_content_async(test_prompt)
        try:
            analysis = norm(response.text)
            if analysis:
                print(f"[TESTE IA] OK: {analysis}")
                return {"result": f"Teste OK! IA respondeu: '{analysis}'"}
            else:
                print("[TESTE IA] Falhou: resposta vazia")
                return {"result": "Teste FALHOU. Resposta vazia da IA."}
        except ValueError as e:
            print(f"[TESTE IA] Falhou (ValueError): {e}")
            return {"result": f"Teste FALHOU. A IA bloqueou a resposta: {e}"}
        except Exception as e_inner:
            print(f"[TESTE IA] Falhou (parse): {e_inner}")
            return {
                "result": "Teste FALHOU. Erro processando resposta IA: "
                + str(e_inner)[:50]
            }

    except Exception as e:
        print(f"[TESTE IA] Falhou (API): {e}")
        msg = str(e).lower()
        detail = str(e)[:100]
        if "quota" in msg:
            detail = "Cota de uso da API excedida."
        elif "api_key" in msg:
            detail = "Chave de API inválida."
        raise HTTPException(
            status_code=500,
            detail=f"Teste FALHOU. Erro na chamada da API: {detail}",
        )
