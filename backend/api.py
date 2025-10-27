import os
import json
import io
import zipfile
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import httpx
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bs4 import BeautifulSoup

import google.generativeai as genai

# =========================
# CONFIG / CONSTANTES
# =========================

with open(os.path.join(os.path.dirname(__file__), "config.json"), "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

INLABS_API_URL = CONFIG.get("INLABS_API_URL", "")
INLABS_TOKEN = CONFIG.get("INLABS_TOKEN", "")

GEMINI_API_KEY = CONFIG.get("GEMINI_API_KEY", "")
GEMINI_MODEL_NAME = CONFIG.get("GEMINI_MODEL_NAME", "gemini-1.5-flash")

# órgão MPO no DOU, normalizado em minúsculas
MPO_ORG_STRING = "ministério do planejamento e orçamento"

# Mapeamento de códigos -> nomes das unidades da MB (impacto direto)
MPO_NAVY_TAGS = {
    "52131": "Comando da Marinha",
    "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
    "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
    "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo (FDEPM)",
}

# Textos-base (templates) que você já usava:
TEMPLATE_LME = (
    "Portaria sobre antecipação ou alteração de Limite de Movimentação e Empenho (LME). "
    "Normalmente trata do Ministério da Defesa como um todo, sem discriminar unidades "
    "específicas da Marinha."
)

TEMPLATE_FONTE = (
    "Portaria que altera/reclassifica a fonte de recursos em dotações orçamentárias que "
    "alcançam unidades da Marinha. Não necessariamente muda o valor global, mas muda a "
    "origem do recurso."
)

TEMPLATE_CREDITO = (
    "Portaria que suplementa crédito/dotação em favor do Ministério da Defesa e inclui "
    "unidades da Marinha (p.ex. Fundo Naval, Comando da Marinha etc.), reforçando recursos."
)

ANNOTATION_POSITIVE_GENERIC = (
    "Publicação potencialmente relevante para a Marinha. Recomenda-se análise detalhada."
)

# Prompt padrão para publicações que NÃO são MPO/não têm impacto direto detectado:
GEMINI_MASTER_PROMPT = (
    "Você é um analista orçamentário experiente. Analise o texto a seguir e responda em "
    "português, em até 4 frases objetivas:\n\n"
    "1. Diga se há impacto orçamentário para a Marinha do Brasil.\n"
    "2. Se houver, descreva rapidamente qual é o impacto (ex: suplementação, "
    "remanejamento de fonte, remanejamento de GND, antecipação de LME, etc.).\n"
    "3. Se não houver impacto, diga claramente 'Sem impacto direto para a Marinha do Brasil.'\n"
    "Não invente valores numéricos que não estejam no texto."
)

# Prompt reforçado para MPO COM impacto direto detectado:
GEMINI_MPO_PROMPT = (
    "Você é analista orçamentário da Marinha do Brasil. A publicação abaixo é do "
    "Ministério do Planejamento e Orçamento e JÁ FOI MARCADA como tendo impacto direto "
    "em dotações da Marinha (ex: Fundo Naval, Comando da Marinha).\n\n"
    "Tarefa:\n"
    "- Explique claramente qual é o impacto para a Marinha (ex: suplementação de crédito, "
    "alteração de fonte de recursos, mudança de Grupo de Natureza da Despesa - GND, "
    "antecipação de LME etc.).\n"
    "- Fale em no máximo 4 frases diretas.\n"
    "- Você NÃO pode responder apenas 'Sem impacto direto', pois já sabemos que há impacto direto.\n"
    "- Não invente valores ou ações que não estejam no texto.\n"
    "Se o texto falar em reforço/suplementação de crédito, destaque isso como positivo.\n"
    "Se falar em remanejamento/alteração de GND ou fonte de recursos, explique que houve "
    "reclassificação orçamentária atingindo unidades da MB.\n"
)

# =========================
# FASTAPI SETUP
# =========================

app = FastAPI()
origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


# =========================
# MODELOS Pydantic
# =========================

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None
    clean_text: Optional[str] = None
    is_mpo_navy_hit: bool = False
    contexto_regra: Optional[Dict[str, Any]] = None  # NOVO: contexto gerado pela regra


class DOUResponse(BaseModel):
    date: str
    items: List[Publicacao]


# =========================
# FUNÇÕES AUXILIARES
# =========================

def norm(x: str) -> str:
    if not x:
        return ""
    return " ".join(x.split())


def is_mpo_organ(organ_text: Optional[str]) -> bool:
    if not organ_text:
        return False
    return MPO_ORG_STRING in organ_text.lower()


def detect_mpo_navy_codes(text: str) -> List[str]:
    """
    Procura códigos da Marinha (52131 etc.) OU o nome completo.
    Retorna lista de códigos encontrados.
    """
    found_codes = []
    low = text.lower()

    for code, nome in MPO_NAVY_TAGS.items():
        if code in text:
            found_codes.append(code)
            continue
        if nome.lower() in low:
            found_codes.append(code)

    # remove duplicata mantendo ordem
    uniq = []
    seen = set()
    for c in found_codes:
        if c not in seen:
            uniq.append(c)
            seen.add(c)

    return uniq


def parse_gnd_change_table(full_text_content: str) -> str:
    """
    Analisa anexos em tabela (caso 'altera parcialmente grupos de natureza de despesa' etc.)
    e tenta extrair UNIDADE, operação (ACRÉSCIMO/REDUÇÃO) e linhas AO/GND/Valor.
    Só guarda se a unidade for Marinha.
    """
    soup = BeautifulSoup(full_text_content, 'lxml-xml')

    results = {
        "acrescimo": [],
        "reducao": []
    }

    current_unidade = None
    current_operation = None

    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cols = row.find_all('td')
            row_text_cells = [norm(c.get_text()) for c in cols]
            row_full_text = " ".join(row_text_cells)

            # Detecta UNIDADE:
            if "UNIDADE:" in row_full_text:
                current_unidade = row_full_text.replace("UNIDADE:", "").strip()
                continue

            # Detecta se bloco é ACRÉSCIMO ou REDUÇÃO/CANCELAMENTO
            if "PROGRAMA DE TRABALHO" in row_full_text.upper():
                upper_line = row_full_text.upper()
                if "ACRÉSCIMO" in upper_line:
                    current_operation = "acrescimo"
                elif "REDUÇÃO" in upper_line or "CANCELAMENTO" in upper_line:
                    current_operation = "reducao"
                else:
                    current_operation = None
                continue

            # Linhas 'úteis' costumam ter 10 colunas (AO, desc, PTRES, UO, GND etc.)
            if len(cols) != 10:
                continue

            # Ignora cabeçalho tipo "PROGRAMÁTICA"
            if "PROGRAMÁTICA" in row_full_text.upper():
                continue

            # Só registra se:
            # - já temos unidade
            # - sabemos se é acréscimo ou redução
            # - a unidade é da Marinha (um dos códigos MPO_NAVY_TAGS)
            if (
                current_unidade
                and current_operation
                and any(tag in current_unidade for tag in MPO_NAVY_TAGS.keys())
            ):
                try:
                    ao, desc, _, _, gnd, _, _, _, _, valor = row_text_cells
                except ValueError:
                    # Se o split falhar porque a estrutura é diferente, ignora a linha
                    continue

                if not valor:
                    continue

                clean_gnd = (
                    gnd.replace('-', '')
                    .replace('ODC', '')
                    .replace('INV', '')
                    .strip()
                )

                line = f"- AO {ao} - {desc} | GND: {clean_gnd} | Valor: {valor}"
                results[current_operation].append((current_unidade, line))

    # Se não capturou nada, devolve fallback:
    if not results["acrescimo"] and not results["reducao"]:
        return (
            "Ato de Alteração de GND com possível impacto na Defesa/Marinha. "
            "Recomenda-se análise manual dos anexos."
        )

    output_lines = [
        "Ato de Alteração de GND com impacto na Defesa/Marinha. Dados extraídos dos anexos:"
    ]

    # Bloco ACRÉSCIMO
    if results["acrescimo"]:
        output_lines.append("\n**-- ACRÉSCIMOS (Suplementação) --**")
        last_unidade = None
        for unidade, line in sorted(results["acrescimo"]):
            if unidade != last_unidade:
                unidade_code = unidade.split(" ")[0]
                output_lines.append(
                    f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*"
                )
                last_unidade = unidade
            output_lines.append(line)

    # Bloco REDUÇÃO
    if results["reducao"]:
        output_lines.append("\n**-- REDUÇÕES (Cancelamento) --**")
        last_unidade = None
        for unidade, line in sorted(results["reducao"]):
            if unidade != last_unidade:
                unidade_code = unidade.split(" ")[0]
                output_lines.append(
                    f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*"
                )
                last_unidade = unidade
            output_lines.append(line)

    return "\n".join(output_lines)


def infer_reason(summary: str, full_text_content: str) -> str:
    """
    Gera um texto técnico preliminar (antes de IA), com base no tipo da portaria.
    Faz matching FLEXÍVEL para LME / Fonte / Crédito / GND.
    """
    summary_lower = (summary or "").lower()

    # Alteração de GND
    if (
        "grupo de natureza da despesa" in summary_lower
        or "grupos de natureza da despesa" in summary_lower
        or "gnd" in summary_lower
        or "natureza da despesa" in summary_lower
        or "altera parcialmente grupos" in summary_lower
        or "adequa os grupos de natureza" in summary_lower
    ):
        return parse_gnd_change_table(full_text_content)

    # LME
    if (
        "limites de movimentação e empenho" in summary_lower
        or "limite de movimentação e empenho" in summary_lower
        or "lme" in summary_lower
    ):
        return TEMPLATE_LME

    # Alteração de Fonte
    if (
        "fonte de recursos" in summary_lower
        or "fontes de recursos" in summary_lower
        or "reclassificação de fonte" in summary_lower
        or "altera a fonte" in summary_lower
        or "modifica fontes de recursos" in summary_lower
        or "alteração de fonte" in summary_lower
    ):
        return TEMPLATE_FONTE

    # Suplementação de Crédito
    if (
        "abre crédito suplementar" in summary_lower
        or "crédito suplementar" in summary_lower
        or "abre aos orçamentos fiscal" in summary_lower
        or "suplementa dotações" in summary_lower
        or "reforço de dotação" in summary_lower
        or "suplementação de crédito" in summary_lower
        or "suplementação de dotações" in summary_lower
    ):
        return TEMPLATE_CREDITO

    # Genérico
    return ANNOTATION_POSITIVE_GENERIC


async def fetch_inlabs_by_date(date_str: str) -> Dict[str, Any]:
    """
    Faz o request na API da inlabs para a data especificada (AAAA-MM-DD).
    """
    headers = {
        "Authorization": f"Bearer {INLABS_TOKEN}",
        "Accept": "application/json",
    }

    params = {
        "date": date_str,
        "with_content_xml": "true",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(INLABS_API_URL, headers=headers, params=params)
        r.raise_for_status()
        return r.json()


def group_materia_by_id(inlabs_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Inlabs traz uma lista de 'materias' possivelmente repetidas (mesma materia_id).
    Aqui agrupamos por materia_id e juntamos campos úteis.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in inlabs_data.get("materias", []):
        materia_id = item.get("materia_id")
        if not materia_id:
            continue

        if materia_id not in grouped:
            grouped[materia_id] = {
                "materia_id": materia_id,
                "organ": item.get("orgao", ""),
                "section": item.get("secao", ""),
                "type": item.get("tipo", ""),
                "summary": item.get("ementa", ""),
                "full_text_content": "",
                "clean_text": "",
            }

        # concatena conteúdo bruto (XML)
        full_content_xml = item.get("content_xml", "") or ""
        grouped[materia_id]["full_text_content"] += "\n" + full_content_xml

        # concatena texto limpo
        texto_limpo = item.get("content_text", "") or ""
        grouped[materia_id]["clean_text"] += "\n" + texto_limpo

    return grouped


def build_contexto_regra(
    is_mpo_navy_hit_flag: bool,
    found_navy_codes: List[str],
    reason: str
) -> Dict[str, Any]:
    """
    Monta um dicionário com as pistas da REGRA que depois será
    injetado no prompt da IA.
    """
    return {
        "is_mpo_navy_hit": is_mpo_navy_hit_flag,
        "found_navy_codes": found_navy_codes,
        "resumo_preliminar": reason or "",
    }


def process_grouped_materia(grouped: Dict[str, Dict[str, Any]]) -> List[Publicacao]:
    """
    Transforma o dicionário agrupado em lista de Publicacao já com:
    - reason preliminar
    - flag se é MPO com impacto na MB
    - contexto_regra preparado
    """
    pubs: List[Publicacao] = []

    for mid, data in grouped.items():
        organ = data.get("organ", "")
        section = data.get("section", "")
        tipo = data.get("type", "")
        summary = data.get("summary", "")
        full_text_content = data.get("full_text_content", "")
        clean_text_for_ia = norm(data.get("clean_text", ""))

        # detecta se é MPO
        organ_is_mpo = is_mpo_organ(organ)

        # detecta se cita unidades da MB
        found_navy_codes = detect_mpo_navy_codes(full_text_content + " " + summary)
        is_mpo_navy_hit_flag = organ_is_mpo and len(found_navy_codes) > 0

        # gera razão preliminar flexível (GND, LME, Fonte, Suplementação etc.)
        reason = ""
        if organ_is_mpo:
            reason = infer_reason(summary, full_text_content)
        else:
            # fallback genérico para outros órgãos
            reason = ANNOTATION_POSITIVE_GENERIC

        contexto_regra = build_contexto_regra(
            is_mpo_navy_hit_flag=is_mpo_navy_hit_flag,
            found_navy_codes=found_navy_codes,
            reason=reason
        )

        pubs.append(Publicacao(
            organ=organ,
            type=tipo,
            summary=summary,
            raw=full_text_content,
            relevance_reason=reason,
            section=section,
            clean_text=clean_text_for_ia,
            is_mpo_navy_hit=is_mpo_navy_hit_flag,
            contexto_regra=contexto_regra
        ))

    return pubs


def monta_whatsapp(pub: Publicacao) -> str:
    """
    Formata a publicação em texto curto estilo WhatsApp.
    """
    resumo_org = pub.organ or ""
    resumo_tipo = pub.type or ""
    resumo_sum = pub.summary or ""
    resumo_reason = pub.relevance_reason or ""

    msg = []
    msg.append(f"Órgão: {resumo_org}")
    if resumo_tipo:
        msg.append(f"Ato: {resumo_tipo}")
    if resumo_sum:
        msg.append(f"Ementa: {resumo_sum}")
    msg.append(f"Análise: {resumo_reason}")

    return "\n".join(msg)


async def get_ai_analysis(
    clean_text: str,
    model: genai.GenerativeModel,
    prompt_template: str,
    contexto_regra: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Chama o modelo Gemini passando:
    - prompt_template (padrão ou MPO reforçado)
    - contexto_regra (TAGs detectadas, códigos MB, resumo preliminar)
    - texto bruto (clean_text)
    """
    try:
        extra_context = ""
        if contexto_regra:
            extra_context += "INFORMAÇÕES PRÉ-CLASSIFICADAS (regras automáticas):\n"
            if contexto_regra.get("is_mpo_navy_hit"):
                extra_context += (
                    "- Esta portaria foi marcada como IMPACTO DIRETO NA MARINHA.\n"
                )
            codes = contexto_regra.get("found_navy_codes") or []
            if codes:
                extra_context += (
                    "- Unidades/Códigos detectados: " + ", ".join(codes) + ".\n"
                )
            resumo_pre = contexto_regra.get("resumo_preliminar", "").strip()
            if resumo_pre:
                extra_context += (
                    "- Resumo técnico preliminar (automático): "
                    + resumo_pre
                    + "\n"
                )
            extra_context += "\n"

        prompt = (
            f"{prompt_template}\n\n"
            f"{extra_context}"
            f"TEXTO DA PORTARIA:\n{clean_text}"
        )

        response = await model.generate_content_async(prompt)
        if not response:
            return None

        # Em versões novas do SDK, .text pode estar em 'candidates' / 'parts'.
        # Vamos tentar ser resilientes:
        try:
            ai_text = response.text
        except AttributeError:
            ai_text = None

        if not ai_text:
            try:
                cand = response.candidates[0]
                parts = cand.get("content", {}).get("parts", [])
                ai_text = " ".join(p.get("text", "") for p in parts)
            except Exception:
                ai_text = None

        if ai_text:
            return ai_text.strip()
        return None

    except Exception as e:
        # Em produção você logaria:
        # print("Erro Gemini:", e)
        return None


# =========================
# ROTAS
# =========================

@app.post("/processar-inlabs")
async def processar_inlabs(date_str: Optional[str] = Form(None)):
    """
    Faz TODO o fluxo exceto IA:
    1. busca na inlabs
    2. agrupa por materia_id
    3. cria lista de Publicacao com reason preliminar
    """
    if not date_str:
        # default = hoje
        hoje = datetime.now().strftime("%Y-%m-%d")
        date_str = hoje

    inlabs_json = await fetch_inlabs_by_date(date_str)
    grouped = group_materia_by_id(inlabs_json)
    pubs = process_grouped_materia(grouped)

    # devolve bruto
    return DOUResponse(
        date=date_str,
        items=pubs
    )


@app.post("/processar-inlabs-ia")
async def processar_inlabs_ia(date_str: Optional[str] = Form(None)):
    """
    Roda TODO o pipeline:
    - busca INLABS
    - agrupa
    - cria Publicacao (reason preliminar)
    - passa IA
    - faz salvaguarda "Sem impacto..." vs is_mpo_navy_hit
    - devolve só as publicações que importam
    """
    if not date_str:
        hoje = datetime.now().strftime("%Y-%m-%d")
        date_str = hoje

    # 1) Puxa dados da Inlabs
    inlabs_json = await fetch_inlabs_by_date(date_str)

    # 2) Agrupa por materia_id
    grouped = group_materia_by_id(inlabs_json)

    # 3) Converte para lista de Publicacao com reason preliminar
    pubs_iniciais = process_grouped_materia(grouped)

    # 4) Configura o modelo Gemini
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL_NAME)

    # 5) Monta as tasks de IA
    tasks = []
    for p in pubs_iniciais:
        # Se publicação é MPO + impacto na Marinha => prompt reforçado
        prompt_to_use = GEMINI_MPO_PROMPT if p.is_mpo_navy_hit else GEMINI_MASTER_PROMPT
        tasks.append(
            get_ai_analysis(
                p.clean_text or "",
                model,
                prompt_to_use,
                p.contexto_regra
            )
        )

    ai_results = await asyncio.gather(*tasks, return_exceptions=True)

    pubs_finais: List[Publicacao] = []

    for p, ai_res in zip(pubs_iniciais, ai_results):
        if isinstance(ai_res, Exception):
            # Se houve erro chamando IA, mantém reason preliminar
            pubs_finais.append(p)
            continue

        ai_reason_result = ai_res  # pode ser None ou str
        if isinstance(ai_reason_result, str):
            # Se IA falou "sem impacto direto", temos lógica condicional
            if ai_reason_result.strip().lower().startswith("sem impacto"):
                if p.is_mpo_navy_hit:
                    # IA não deveria dizer isso, mas disse => preserva e marca alerta
                    p.relevance_reason = (
                        "⚠️ IA disse 'sem impacto', mas a regra marcou impacto direto na MB: "
                        + ai_reason_result
                    )
                    pubs_finais.append(p)
                elif MPO_ORG_STRING in (p.organ or "").lower():
                    # MPO, mas regra não identificou MB -> ok deixar 'sem impacto'
                    p.relevance_reason = ai_reason_result
                    pubs_finais.append(p)
                else:
                    # não é MPO e a IA disse 'sem impacto' => descarta
                    pass
            else:
                # IA trouxe análise útil => substitui reason
                p.relevance_reason = ai_reason_result
                pubs_finais.append(p)

        else:
            # IA não retornou texto útil, fica com reason preliminar
            pubs_finais.append(p)

    # 6) monta retorno final com WhatsApp-style também
    retorno = {
        "date": date_str,
        "total_publicacoes_processadas": len(pubs_iniciais),
        "publicacoes_relevantes": []
    }

    for pub in pubs_finais:
        retorno["publicacoes_relevantes"].append({
            "organ": pub.organ,
            "type": pub.type,
            "summary": pub.summary,
            "analysis": pub.relevance_reason,
            "whatsapp": monta_whatsapp(pub),
        })

    return retorno


@app.post("/upload-xml-zip")
async def upload_xml_zip(file: UploadFile = File(...)):
    """
    Endpoint utilitário:
    Você sobe um zip com XMLs do DOU, a gente processa local
    sem chamar a inlabs.
    Útil pra debug/simulações offline.
    """

    content = await file.read()
    zf = zipfile.ZipFile(io.BytesIO(content))

    grouped_local: Dict[str, Dict[str, Any]] = {}

    for info in zf.infolist():
        if info.filename.lower().endswith(".xml"):
            xml_bytes = zf.read(info)
            xml_text = xml_bytes.decode("utf-8", errors="ignore")

            # heurística simples para gerar um id sintético
            materia_id = info.filename

            soup = BeautifulSoup(xml_text, "lxml-xml")
            organ = norm(" ".join(x.get_text() for x in soup.find_all("ORGAO")))
            section = norm(" ".join(x.get_text() for x in soup.find_all("SECAO")))
            tipo = norm(" ".join(x.get_text() for x in soup.find_all("TIPO")))
            summary = norm(" ".join(x.get_text() for x in soup.find_all("EMENTA")))
            clean_text_for_ia = norm(soup.get_text(" ", strip=True))

            if materia_id not in grouped_local:
                grouped_local[materia_id] = {
                    "materia_id": materia_id,
                    "organ": organ,
                    "section": section,
                    "type": tipo,
                    "summary": summary,
                    "full_text_content": xml_text,
                    "clean_text": clean_text_for_ia,
                }
            else:
                grouped_local[materia_id]["full_text_content"] += "\n" + xml_text
                grouped_local[materia_id]["clean_text"] += "\n" + clean_text_for_ia

    pubs = process_grouped_materia(grouped_local)

    return {
        "items": [p.dict() for p in pubs],
        "count": len(pubs)
    }


# =========================
# IMPORTANTE: precisamos do asyncio no /processar-inlabs-ia
# =========================
import asyncio  # manter no final para não quebrar import cíclico

