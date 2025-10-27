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
from google.generativeai.types import GenerationConfig

# =====================================================================================
# Robô DOU API - versão 13.9.2 (baseada na sua 13.9.1)
# Mudanças:
# - Corrige .UPPER() -> .upper() e parsing de GND
# - Gatilhos MPO mais abrangentes (Fonte / GND / LME / Crédito Suplementar)
# - Remove duplicação de process_grouped_materia
# - Mantém seu fluxo de login InLabs, zip, IA, WhatsApp
# =====================================================================================

app = FastAPI(
    title="Robô DOU API (INLABS XML) - v13.9.2 (fix GND / MPO tagging / IA prompt)"
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

# Credenciais InLabs (Render injeta via env, mas se não tiver usa config.json)
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

# Constantes vindas do config.json
TEMPLATE_LME = config.get("TEMPLATE_LME", "")
TEMPLATE_FONTE = config.get("TEMPLATE_FONTE", "")
TEMPLATE_CREDITO = config.get("TEMPLATE_CREDITO", "")
ANNOTATION_POSITIVE_GENERIC = config.get("ANNOTATION_POSITIVE_GENERIC", "")
ANNOTATION_NEGATIVE = config.get("ANNOTATION_NEGATIVE", "")

MPO_NAVY_TAGS = config.get("MPO_NAVY_TAGS", {})
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

# -------------------------------------------------------------------------------------
# PROMPTS IA
# -------------------------------------------------------------------------------------

# prompt geral
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

# prompt especial pra MPO com impacto direto
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
# MODELOS
# =====================================================================================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False  # chave para o prompt da IA


class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

# =====================================================================================
# FUNÇÕES AUXILIARES
# =====================================================================================

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return _ws.sub(" ", s).strip()


def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    """
    Monta o texto final estilo WhatsApp, agrupando por seção.
    """
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

    # agrupar por seção (DO1, DO2 etc)
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

            # tratamento de avisos de erro IA
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
# PARSER DE GND / TABELAS MPO
# =====================================================================================

def parse_gnd_change_table(full_text_content: str) -> str:
    """
    Lê as tabelas anexas das portarias orçamentárias do MPO
    e tenta extrair acréscimos/reduções especificamente
    quando a unidade é ligada à MB (códigos em MPO_NAVY_TAGS).
    """

    soup = BeautifulSoup(full_text_content, "lxml-xml")

    results = {"acrescimo": [], "reducao": []}
    current_unidade = None
    current_operation = None

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cols = row.find_all("td")
            row_text_cells = [norm(c.get_text()) for c in cols]
            row_full_text = " ".join(row_text_cells)

            # Detecta UNIDADE:
            if "UNIDADE:" in row_full_text:
                current_unidade = row_full_text.replace("UNIDADE:", "").strip()
                continue

            # Detecta bloco atual: ACRÉSCIMO / REDUÇÃO / CANCELAMENTO
            if "PROGRAMA DE TRABALHO" in row_full_text:
                upper_line = row_full_text.upper()
                if "ACRÉSCIMO" in upper_line:
                    current_operation = "acrescimo"
                elif "REDUÇÃO" in upper_line or "CANCELAMENTO" in upper_line:
                    current_operation = "reducao"
                else:
                    current_operation = None
                continue

            # Linhas efetivas de dotação costumam ter ~10 colunas e não conter "PROGRAMÁTICA"
            if len(cols) != 10 or "PROGRAMÁTICA" in row_full_text.upper():
                continue

            # Só registra se:
            #  - já temos a unidade
            #  - já sabemos se é acréscimo ou redução
            #  - a unidade é da Marinha (checa códigos tipo 52131, 52931...)
            if (
                current_unidade
                and current_operation
                and any(tag in current_unidade for tag in MPO_NAVY_TAGS.keys())
            ):
                try:
                    ao, desc, _, _, gnd, _, _, _, _, valor = row_text_cells
                except ValueError:
                    # Estrutura inesperada na linha
                    continue

                if not valor:
                    continue

                clean_gnd = (
                    gnd.replace("-", "")
                    .replace("ODC", "")
                    .replace("INV", "")
                    .strip()
                )

                line = f"- AO {ao} - {desc} | GND: {clean_gnd} | Valor: {valor}"
                results[current_operation].append((current_unidade, line))

    # Se não coletou nada utilizável:
    if not results["acrescimo"] and not results["reducao"]:
        return (
            "Ato orçamentário do MPO potencialmente envolvendo Defesa/Marinha. "
            "Recomenda-se análise manual dos anexos para confirmar acréscimos, reduções, ações e valores."
        )

    out_lines = [
        "Ato orçamentário do MPO com impacto na Defesa/Marinha. Dados relevantes extraídos:"
    ]

    # Acréscimos (suplementação)
    if results["acrescimo"]:
        out_lines.append("\n**-- ACRÉSCIMOS (Suplementação) --**")
        last_unidade = None
        for unidade, line in sorted(results["acrescimo"]):
            if unidade != last_unidade:
                unidade_code = unidade.split(" ")[0]
                out_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*")
                last_unidade = unidade
            out_lines.append(line)

    # Reduções / cancelamentos
    if results["reducao"]:
        out_lines.append("\n**-- REDUÇÕES (Cancelamento) --**")
        last_unidade = None
        for unidade, line in sorted(results["reducao"]):
            if unidade != last_unidade:
                unidade_code = unidade.split(" ")[0]
                out_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*")
                last_unidade = unidade
            out_lines.append(line)

    return "\n".join(out_lines)

# =====================================================================================
# CLASSIFICAÇÃO INICIAL (SEM IA) - process_grouped_materia
# =====================================================================================

def process_grouped_materia(
    main_article: BeautifulSoup,
    full_text_content: str,
    custom_keywords: List[str],
) -> Optional[Publicacao]:
    """
    1. Avalia se a matéria é de interesse.
    2. Gera uma análise inicial (reason).
    3. Seta a flag is_mpo_navy_hit se for MPO e atingir códigos da MB.
    Essa saída é usada tanto no /processar-inlabs (sem IA) quanto
    como insumo para /processar-inlabs-ia (com IA).
    """

    organ = norm(main_article.get("artCategory", ""))
    organ_lower = organ.lower()

    # Ignorar FAB/Exército pra não sujar relatório
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
        # fallback: tenta achar "EMENTA:" manualmente
        match = re.search(
            r"EMENTA:(.*?)(Vistos|ACORDAM)", display_text, re.DOTALL | re.I
        )
        if match:
            summary = norm(match.group(1))

    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()
    clean_text_for_ia = ""
    is_mpo_navy_hit_flag = False

    # -------------------------------------------------
    # SEÇÃO 1 (DO1): atos normativos / portarias / MPO
    # -------------------------------------------------
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ_lower

        if is_mpo:
            # procurar códigos específicos da Marinha/Defesa nos anexos/texto
            found_navy_codes = [
                code
                for code in MPO_NAVY_TAGS
                if code.lower() in search_content_lower
            ]

            if found_navy_codes:
                is_relevant = True

                # separar código 52000 (Defesa genérica) de códigos claramente Marinha
                found_specific = [c for c in found_navy_codes if c != "52000"]
                found_defesa = "52000" in found_navy_codes

                # heurística de impacto direto
                if found_specific:
                    is_mpo_navy_hit_flag = True
                elif found_defesa and found_specific:
                    is_mpo_navy_hit_flag = True
                else:
                    is_mpo_navy_hit_flag = False

                # classificar o tipo de ato MPO com gatilhos mais soltos
                summary_lower = summary.lower()

                # 1) Alteração de GND / natureza de despesa / adequação de GND
                if (
                    "grupo de natureza da despesa" in summary_lower
                    or "grupos de natureza da despesa" in summary_lower
                    or "gnd" in summary_lower
                    or "natureza da despesa" in summary_lower
                    or "altera parcialmente grupos" in summary_lower
                    or "adequa os grupos de natureza" in summary_lower
                    or "adequa os grupos de natureza da despesa" in summary_lower
                ):
                    reason = parse_gnd_change_table(full_text_content)

                # 2) Limite de Movimentação e Empenho (LME)
                elif (
                    "limites de movimentação e empenho" in summary_lower
                    or "limite de movimentação e empenho" in summary_lower
                    or "ajusta os limites de movimentação e empenho" in summary_lower
                    or "antecipa os limites de movimentação e empenho" in summary_lower
                    or "lme" in summary_lower
                ):
                    reason = TEMPLATE_LME or (
                        "Portaria do MPO ajusta/antecipa os Limites de Movimentação "
                        "e Empenho, afetando dotações discricionárias, possivelmente "
                        "incluindo Defesa/Marinha."
                    )

                # 3) Alteração/Modificação de Fonte de Recurso
                elif (
                    "fonte de recursos" in summary_lower
                    or "fontes de recursos" in summary_lower
                    or "reclassificação de fonte" in summary_lower
                    or "altera a fonte" in summary_lower
                    or "modifica fontes de recursos" in summary_lower
                    or "alteração de fonte" in summary_lower
                    or "identificador de resultado primário" in summary_lower
                ):
                    reason = TEMPLATE_FONTE or (
                        "Portaria do MPO modifica fonte de recursos / resultado primário "
                        "em dotações que alcançam Defesa/Marinha."
                    )

                # 4) Crédito suplementar / reforço de dotação
                elif (
                    "abre crédito suplementar" in summary_lower
                    or "crédito suplementar" in summary_lower
                    or "abre aos orçamentos fiscal" in summary_lower
                    or "suplementa dotações" in summary_lower
                    or "reforço de dotações" in summary_lower
                    or "reforço de dotação" in summary_lower
                    or "suplementação de crédito" in summary_lower
                    or "suplementação de dotações" in summary_lower
                    or "crédito suplementar no valor de" in summary_lower
                ):
                    reason = TEMPLATE_CREDITO or (
                        "Portaria do MPO abre crédito suplementar / reforça dotação "
                        "que pode atingir UOs da Defesa/Marinha."
                    )

                else:
                    # MPO + códigos MB detectados, mas não bateu nenhum padrão acima
                    reason = (
                        ANNOTATION_POSITIVE_GENERIC
                        or "Publicação potencialmente relevante para a Marinha. "
                           "Recomenda-se análise detalhada."
                    )

            # MPO mas sem códigos MB nos anexos
            elif any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = ANNOTATION_NEGATIVE or (
                    "Ato orçamentário do MPO, mas não foi possível confirmar impacto "
                    "direto na Marinha com base no texto disponível."
                )

        # NÃO é MPO → ato normativo ou decisório da própria Marinha / Defesa
        else:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break

    # -------------------------------------------------
    # SEÇÃO 2 (DO2): pessoal / movimentação de militares
    # -------------------------------------------------
    elif "DO2" in section:
        soup_copy = BeautifulSoup(full_text_content, "lxml-xml")

        # removendo assinaturas, para não confundir a análise de cargo/autoridade
        for tag in soup_copy.find_all("p", class_=["assina", "cargo"]):
            tag.decompose()

        clean_search_content_lower = norm(
            soup_copy.get_text(strip=True)
        ).lower()

        # 1) termos institucionais ("Comando da Marinha", "Autoridade Marítima", etc.)
        for term in TERMS_AND_ACRONYMS_S2:
            if term.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{term}'."
                break

        # 2) nome rastreado + verbo de ação (nomeação, exoneração etc.)
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

    # -------------------------------------------------
    # KEYWORDS CUSTOM do usuário (parâmetro keywords_json)
    # -------------------------------------------------
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

    # -------------------------------------------------
    # Se for relevante, monta Publicacao
    # -------------------------------------------------
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
# CLIENTE INLABS / DOWNLOAD ZIP
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
    Após login, encontra o índice da data desejada e retorna a URL-base dessa data.
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

    # fallback: tenta montar diretório `${BASE}/${date}/`
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"
    rr = await client.get(fallback_url)
    if rr.status_code == 200:
        return fallback_url

    raise HTTPException(
        status_code=404,
        detail=f"Não encontrei a pasta/listagem da data {date} após login.",
    )


async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    """Pega o HTML da listagem de arquivos da data."""
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
    Acha os .zip relevantes (DO1, DO2, etc.) na listagem HTML daquela data.
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
    Lê um ZIP em memória e devolve todos os XMLs internos.
    """
    xml_blobs: List[bytes] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"):
                xml_blobs.append(z.read(name))

    return xml_blobs

# =====================================================================================
# ENDPOINT /processar-inlabs (SEM IA)
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
    Estágio 1:
    - autentica na InLabs
    - baixa ZIPs do dia
    - junta os XML por materia_id
    - roda process_grouped_materia
    - gera whatsapp_text SEM IA
    """

    secs = (
        [s.strip().upper() for s in sections.split(",") if s.strip()]
        if sections
        else ["DO1"]
    )

    # keywords custom do usuário
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

        # baixa todos os ZIPs listados e extrai XML
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))

        # agrupa XMLs por materia_id
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

        # Deduplicar publicações parecidas (mesmo órgão/ato/ementa)
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
    Chama Gemini e retorna UMA frase.
    Pode retornar:
    - string normal
    - "Erro na análise de IA: ..." (erro leve)
    - None (bloqueio/vazio)
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
            print(f"Bloco de IA (ValueError): {e}")
            return None
        except Exception as e_inner:
            print(f"Erro inesperado ao processar resposta da IA: {e_inner}")
            return (
                "Erro processando resposta IA: " + str(e_inner)[:50]
            )

    except Exception as e:
        print(f"Erro na API do Gemini: {e}")
        error_msg = str(e).lower()
        if "quota" in error_msg:
            return "Erro na análise de IA: Cota de uso da API excedida."
        if "api_key" in error_msg:
            return "Erro na análise de IA: Chave de API inválida."
        return "Erro na análise de IA: " + str(e)[:100]

# =====================================================================================
# ENDPOINT /processar-inlabs-ia (COM IA)
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
    Estágio 1 + IA:
    - Mesma coleta de /processar-inlabs
    - Depois roda a IA pra gerar uma frase curta por item
    - Aplica regra de proteção (se é MPO com impacto direto, a IA não pode dizer 'sem impacto direto')
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

    # keywords custom
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

        # baixa os ZIPs e extrai XML
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))

        # agrupar por materia_id
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

        # estágio 1 (regra)
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"],
                    content["full_text"],
                    custom_keywords,
                )
                if publication:
                    pubs_filtradas.append(publication)

        # merge duplicados
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

        # estágio 2 (IA)
        tasks = []
        for p in merged_pubs:
            prompt_to_use = GEMINI_MASTER_PROMPT
            if p.is_mpo_navy_hit:
                prompt_to_use = GEMINI_MPO_PROMPT

            if p.clean_text:
                tasks.append(get_ai_analysis(p.clean_text, model, prompt_to_use))
            else:
                # fallback raro
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
            # vários casos possíveis de retorno
            if isinstance(ai_out, Exception):
                p.relevance_reason = f"Erro GRAVE na análise de IA: {ai_out}"
                pubs_finais.append(p)
                continue

            if ai_out is None:
                # IA ficou muda → mantém razão S1
                pubs_finais.append(p)
                continue

            if isinstance(ai_out, str):
                # IA retornou texto
                lower_ai = ai_out.lower()

                # caso erro leve: "Erro na análise de IA: ... "
                if ai_out.startswith("Erro na análise de IA:"):
                    p.relevance_reason = ai_out
                    pubs_finais.append(p)
                    continue

                # caso IA diga "sem impacto direto"
                if "sem impacto direto" in lower_ai:
                    if p.is_mpo_navy_hit:
                        # IA contradisse o marcador de impacto direto
                        p.relevance_reason = "⚠️ IA ignorou impacto MPO: " + ai_out
                        pubs_finais.append(p)
                    elif MPO_ORG_STRING in (p.organ or "").lower():
                        # MPO mas sem tag MB explícita → pode aceitar "sem impacto direto"
                        p.relevance_reason = ai_out
                        pubs_finais.append(p)
                    else:
                        # não é MPO e IA falou "sem impacto": descarta do relatório final
                        # (isso reduz ruído)
                        pass
                    continue

                # caso normal (frase útil)
                p.relevance_reason = ai_out
                pubs_finais.append(p)
                continue

            # fallback inesperado: mantém razão original
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
# HELLO / HEALTHCHECK
# =====================================================================================

@app.get("/")
async def root():
    return {"status": "ok", "ts": datetime.now().isoformat()}


# =====================================================================================
# TESTE RÁPIDO DA IA
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
                return {
                    "result": "Teste FALHOU. Resposta vazia da IA."
                }

        except ValueError as e:
            print(f"[TESTE IA] Falhou (ValueError): {e}")
            return {
                "result": f"Teste FALHOU. A IA bloqueou a resposta: {e}"
            }
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
