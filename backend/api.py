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

# --- Importações da IA (Corrigidas v13.3) ---
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
# -------------------------------------------

# #####################################################################
# ########## VERSÃO 13.9.1 - (IA com MPO Prompt dinâmico) ##############
# #####################################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v13.9.1 (IA MPO-Aware)")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG E KEYWORDS (CARREGADOS DO config.json) ======
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
except FileNotFoundError:
    raise RuntimeError("Erro: Arquivo 'config.json' não encontrado.")
except json.JSONDecodeError:
    raise RuntimeError("Erro: Falha ao decodificar 'config.json'. Verifique a sintaxe.")

INLABS_BASE = os.getenv("INLABS_BASE", config.get("INLABS_BASE", "https://inlabs.in.gov.br"))
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", config.get("INLABS_LOGIN_URL", f"{INLABS_BASE}/login"))
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")

# --- Configuração da API do Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
# ----------------------------------------

# Carrega constantes do JSON
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
# ==========================================================

# --- Master Prompt da IA ---
GEMINI_MASTER_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil, especialista em legislação e defesa.
Sua tarefa é ler a publicação do Diário Oficial da União (DOU) abaixo e escrever uma única frase curta (máximo 2 linhas) para um relatório de WhatsApp, focando exclusivamente no impacto para a Marinha do Brasil (MB).

Critérios de Análise:
1.  Se for ato orçamentário (MPO/Fazenda), foque no impacto: É crédito, LME, fontes? Afeta UGs da Marinha ( "52131": "Comando da Marinha",
    "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
    "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
    "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
    "52000": "Ministério da Defesa")?
2.  Se for ato normativo (Decreto, Portaria), qual a ação ou responsabilidade criada para a Marinha/Autoridade Marítima?
3.  Se for ato de pessoal (Seção 2), quem é a pessoa e qual a ação (nomeação, exoneração, viagem)?
4.  Se a menção for trivial ou sem impacto direto (ex: 'Ministério da Defesa' apenas citado numa lista de participantes de reunião, ou 'Marinha' em nome de empresa privada), responda APENAS com a frase: "Sem impacto direto."

Seja direto e objetivo.

TEXTO DA PUBLICAÇÃO:
"""
# ---------------------------------

# --- NOVO: Master Prompt para MPO (Alta Responsabilidade) ---
GEMINI_MPO_PROMPT = """
Você é um analista de orçamento e finanças do Comando da Marinha do Brasil, especialista em legislação e defesa.
Sua tarefa é ler a publicação do Diário Oficial da União (DOU) abaixo.

ATENÇÃO: Esta publicação do MPO/Fazenda já foi pré-filtrada e CONFIRMADA como de alto impacto para a Marinha do Brasil (MB), pois contém menções diretas a UGs orçamentárias da Marinha (como 52131, 52931, 52000, etc.) em seus anexos.

Sua tarefa NÃO é julgar a relevância, mas sim EXPLICAR O IMPACTO.

Instruções:
1.  Leia o texto completo, incluindo os anexos.
2.  Identifique QUAIS Unidades Orçamentárias da Marinha (ou Defesa) são afetadas.
3.  Resuma a alteração: É um crédito suplementar (acréscimo)? Um cancelamento (redução)? Uma alteração de GND?
4.  Seja específico. Se possível, cite as Ações Orçamentárias (AO) e os valores.
5.  Escreva uma única frase curta (máximo 2 linhas) para um relatório de WhatsApp.

Exemplo de Resposta: "Ato do MPO altera GND, suplementando R$ 10,5M para a AO 1234 (GND 3) do Comando da Marinha e cancelando R$ 2,0M da AO 5678 (GND 4) do Fundo Naval."

NÃO RESPONDA "Sem impacto direto." Esta publicação TEM impacto.

TEXTO DA PUBLICAÇÃO:
"""
# ---------------------------------


# ### MODIFICAÇÃO 1: Adicionado 'is_mpo_navy_hit'
class Publicacao(BaseModel):
    organ: Optional[str] = None; type: Optional[str] = None; summary: Optional[str] = None
    raw: Optional[str] = None; relevance_reason: Optional[str] = None; section: Optional[str] = None
    clean_text: Optional[str] = None # Campo para guardar texto limpo para a IA
    is_mpo_navy_hit: bool = False # Flag para S1 avisar S2

class ProcessResponse(BaseModel):
    date: str; count: int; publications: List[Publicacao]; whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when); dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception: dd = when
    lines = ["Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]
    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU";
        if sec not in pubs_by_section: pubs_by_section[sec] = []
        pubs_by_section[sec].append(p)
    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —"); return "\n".join(lines)
    for section_name in sorted(pubs_by_section.keys()):
        if not pubs_by_section[section_name]: continue
        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}"); lines.append("")
        for p in pubs_by_section[section_name]:
            lines.append(f"▶️ {p.organ or 'Órgão'}"); lines.append(f"📌 {p.type or 'Ato/Portaria'}")
            if p.summary: lines.append(p.summary)
            # Adiciona tratamento para erro no motivo (mostra erro em vermelho no preview)
            reason = p.relevance_reason or "Para conhecimento."
            prefix = "⚓"
            
            # Ajustado para pegar Erros da IA E o novo Alerta de desobediência
            if reason.startswith("Erro na análise de IA:") or reason.startswith("Erro GRAVE") or reason.startswith("⚠️"):
                 prefix = "⚠️ Erro IA:" # Muda o prefixo para erros
                 reason = reason.replace("Erro na análise de IA:", "").replace("Erro GRAVE na análise de IA:", "").replace("⚠️ IA ignorou impacto MPO:", "").strip()

            if '\n' in reason:
                lines.append(f"{prefix}\n{reason}")
            else:
                 lines.append(f"{prefix} {reason}")
            lines.append("")
    return "\n".join(lines)

def parse_gnd_change_table(full_text_content: str) -> str:
    # ... (código inalterado) ...
    soup = BeautifulSoup(full_text_content, 'lxml-xml')
    results = {'acrescimo': [], 'reducao': []}
    current_unidade = None
    current_operation = None
    for table in soup.find_all('table'):
        for row in table.find_all('tr'):
            cols = row.find_all('td')
            row_text_cells = [norm(c.get_text()) for c in cols]
            row_full_text = " ".join(row_text_cells)
            if "UNIDADE:" in row_full_text:
                current_unidade = row_full_text.replace("UNIDADE:", "").strip()
                continue 
            if "PROGRAMA DE TRABALHO" in row_full_text:
                if "ACRÉSCIMO" in row_full_text.UPPER():
                    current_operation = "acrescimo"
                elif "REDUÇÃO" in row_full_text.UPPER() or "CANCELAMENTO" in row_full_text.UPPER():
                    current_operation = "reducao"
                else: current_operation = None
                continue 
            if len(cols) != 10 or "PROGRAMÁTICA" in row_full_text.UPPER(): continue
            if current_unidade and current_operation and any(tag in current_unidade for tag in MPO_NAVY_TAGS.keys()):
                try:
                    ao, desc, _, _, gnd, _, _, _, _, valor = row_text_cells
                    if not valor: continue
                    clean_gnd = gnd.replace('-','').replace('ODC','').replace('INV','')
                    line = f"- AO {ao} - {desc} | GND: {clean_gnd} | Valor: {valor}"
                    results[current_operation].append((current_unidade, line))
                except (IndexError, ValueError): continue
    if not results['acrescimo'] and not results['reducao']:
        return "Ato de Alteração de GND com impacto na Defesa/Marinha. Recomenda-se análise manual dos anexos."
    output_lines = ["Ato de Alteração de GND com impacto na Defesa/Marinha. Dados extraídos dos anexos:"]
    if results['acrescimo']:
        output_lines.append("\n**-- ACRÉSCIMOS (Suplementação) --**")
        last_unidade = None
        for unidade, line in sorted(results['acrescimo']):
            if unidade != last_unidade:
                unidade_code = unidade.split(' ')[0]
                output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*") 
                last_unidade = unidade
            output_lines.append(line)
    if results['reducao']:
        output_lines.append("\n**-- REDUÇÕES (Cancelamento) --**")
        last_unidade = None
        for unidade, line in sorted(results['reducao']):
            if unidade != last_unidade:
                unidade_code = unidade.split(' ')[0]
                output_lines.append(f"*{MPO_NAVY_TAGS.get(unidade_code, unidade)}*")
                last_unidade = unidade
            output_lines.append(line)
    return "\n".join(output_lines)

# ### MODIFICAÇÃO 2: process_grouped_materia agora seta a flag 'is_mpo_navy_hit_flag'
def process_grouped_materia(
    main_article: BeautifulSoup, 
    full_text_content: str, # Este é o XML/HTML bruto
    custom_keywords: List[str]
) -> Optional[Publicacao]:
    # ... (código inalterado) ...
    organ = norm(main_article.get('artCategory', ''))
    organ_lower = organ.lower() # Helper

    # --- INÍCIO DA MODIFICAÇÃO (Exclusão de Órgãos) ---
    if (
        "comando da aeronáutica" in organ_lower or
        "comando do exército" in organ_lower
    ):
        return None # Exclui publicações da Aeronáutica e Exército
    # --- FIM DA MODIFICAÇÃO ---

    section = main_article.get('pubName', '').upper()
    body = main_article.find('body')
    if not body: return None
    act_type = norm(body.find('Identifica').get_text(strip=True) if body.find('Identifica') else "")
    if not act_type: return None
    summary = norm(body.find('Ementa').get_text(strip=True) if body.find('Ementa') else "")
    display_text = norm(body.get_text(strip=True))
    if not summary:
        match = re.search(r'EMENTA:(.*?)(Vistos|ACORDAM)', display_text, re.DOTALL | re.I)
        if match: summary = norm(match.group(1))

    is_relevant = False
    reason = None
    search_content_lower = norm(full_text_content).lower()
    clean_text_for_ia = "" # Prepara o texto limpo para a IA
    is_mpo_navy_hit_flag = False # <-- FLAG INICIALIZADA

    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ.lower()
        if is_mpo:
            found_navy_codes = [code for code in MPO_NAVY_TAGS if code in search_content_lower]
            if found_navy_codes:
                is_relevant = True
                is_mpo_navy_hit_flag = True # <-- FLAG SETADA
                summary_lower = summary.lower()
                if "altera parcialmente grupos de natureza de despesa" in summary_lower:
                    reason = parse_gnd_change_table(full_text_content) 
                elif "os limites de movimentação e empenho constantes" in summary_lower:
                    reason = TEMPLATE_LME
                elif "modifica fontes de recursos" in summary_lower:
                    reason = TEMPLATE_FONTE
                elif "abre aos orçamentos fiscal" in summary_lower:
                    reason = TEMPLATE_CREDITO
                else:
                    reason = ANNOTATION_POSITIVE_GENERIC
            elif any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                reason = ANNOTATION_NEGATIVE
        else:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True
                    reason = f"Há menção específica à TAG: '{kw}'."
                    break
    
    elif "DO2" in section:
        soup_copy = BeautifulSoup(full_text_content, 'lxml-xml')
        for tag in soup_copy.find_all('p', class_=['assina', 'cargo']):
            tag.decompose()
        clean_search_content_lower = norm(soup_copy.get_text(strip=True)).lower()

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
                    context_window_text = clean_search_content_lower[start_pos:match.start()]
                    if any(verb in context_window_text for verb in PERSONNEL_ACTION_VERBS):
                        is_relevant = True
                        reason = f"Ato de pessoal (Seção 2): menção a '{name}' em contexto de ação."
                        break
                if is_relevant: break
    
    found_custom_kw = None
    custom_reason_text = None
    if custom_keywords:
        for kw in custom_keywords:
            if kw in search_content_lower:
                found_custom_kw = kw
                custom_reason_text = f"Há menção à palavra-chave personalizada: '{kw}'."
                break
    
    if found_custom_kw:
        is_relevant = True 
        if reason and reason != ANNOTATION_NEGATIVE:
            reason = f"{reason}\n⚓ {custom_reason_text}"
        elif not reason or reason == ANNOTATION_NEGATIVE:
            reason = custom_reason_text

    if is_relevant:
        soup_full_clean = BeautifulSoup(full_text_content, 'lxml-xml')
        clean_text_for_ia = norm(soup_full_clean.get_text(strip=True))
        return Publicacao(
            organ=organ, type=act_type, summary=summary,
            raw=display_text, relevance_reason=reason, section=section,
            clean_text=clean_text_for_ia,
            is_mpo_navy_hit=is_mpo_navy_hit_flag # <-- FLAG PASSADA PARA O OBJETO
        )
    return None

# --- Funções de Rede (sem mudança) ---
async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    # ... (código inalterado) ...
    if not INLABS_USER or not INLABS_PASS: raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400: await client.aclose(); raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client

async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado) ...
    r = await client.get(INLABS_BASE); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser"); cand_texts = [date, date.replace("-", "_"), date.replace("-", "")];
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip(); txt = (a.get_text() or "").strip(); hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts): return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"; rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")

async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado) ...
    url = await resolve_date_url(client, date); r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text

def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    # ... (código inalterado) ...
    soup = BeautifulSoup(html, "html.parser"); links: List[str] = []; wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted): links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))

async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    # ... (código inalterado) ...
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content

def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    # ... (código inalterado) ...
    xml_blobs: List[bytes] = [];
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"): xml_blobs.append(z.read(name))
    return xml_blobs
# --- Fim das Funções de Rede ---


# === ENDPOINT RÁPIDO (v12.7) ===
@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None, description="Um JSON string de uma lista de keywords. Ex: '[\"amazul\", \"prosub\"]'")
):
    # ... (código inalterado) ...
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    custom_keywords = []
    if keywords_json:
        try:
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [str(k).strip().lower() for k in keywords_list if str(k).strip()]
        except json.JSONDecodeError: pass 
    
    client = await inlabs_login_and_get_session()
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.")
        
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))

        materias: Dict[str, Dict] = {}
        for blob in all_xml_blobs:
            try:
                soup = BeautifulSoup(blob, 'lxml-xml')
                article = soup.find('article')
                if not article: continue
                materia_id = article.get('idMateria')
                if not materia_id: continue
                if materia_id not in materias:
                    materias[materia_id] = {'main_article': None, 'full_text': ''}
                materias[materia_id]['full_text'] += blob.decode('utf-8', errors='ignore') + "\n"
                body = article.find('body')
                if body and body.find('Identifica') and body.find('Identifica').get_text(strip=True):
                    materias[materia_id]['main_article'] = article
            except Exception: continue
        
        pubs: List[Publicacao] = []
        for materia_id, content in materias.items():
            if content['main_article']:
                publication = process_grouped_materia( # Filtro Estágio 1
                    content['main_article'], content['full_text'], custom_keywords 
                )
                if publication: pubs.append(publication)
        
        seen: Set[str] = set()
        merged: List[Publicacao] = []
        for p in pubs:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged.append(p)
        
        texto = monta_whatsapp(merged, data)
        return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
    finally:
        await client.aclose()


# ### MODIFICAÇÃO 3: get_ai_analysis agora aceita 'prompt_template'
async def get_ai_analysis(clean_text: str, model: genai.GenerativeModel, prompt_template: str = GEMINI_MASTER_PROMPT) -> Optional[str]:
    """Chama a API do Gemini. Retorna análise, erro leve ou None se bloqueado."""
    try:
        # Constrói o prompt final USANDO O TEMPLATE FORNECIDO
        prompt = f"{prompt_template}\n\n{clean_text}" 
        
        # --- ALTERAÇÃO v13.9 ---
        # Faz a chamada assíncrona SEM safety_settings e generation_config
        response = await model.generate_content_async(prompt)
        # --- FIM DA ALTERAÇÃO ---
        
        # Lógica de extração de texto robusta (v13.7)
        try:
            analysis = norm(response.text)
            if analysis:
                return analysis
            else:
                try: finish_reason = response.prompt_feedback.finish_reason.name
                except Exception: finish_reason = "desconhecido"
                print(f"Resposta da IA vazia. Razão: {finish_reason}")
                return None # Fallback para resposta vazia

        except ValueError as e: 
            print(f"Bloco de IA (ValueError): {e}")
            return None # Fallback para bloqueio
        except Exception as e_inner:
             print(f"Erro inesperado ao processar resposta da IA: {e_inner}")
             return f"Erro processando resposta IA: {str(e_inner)[:50]}" # Retorna erro leve

    except Exception as e:
        # Erros na chamada da API (conexão, chave, cota, etc.)
        print(f"Erro na API do Gemini: {e}")
        error_msg = str(e).lower()
        if "quota" in error_msg: return "Erro na análise de IA: Cota de uso da API excedida."
        if "api_key" in error_msg: return "Erro na análise de IA: Chave de API inválida."
        return f"Erro na análise de IA: {str(e)[:100]}" # Retorna erro específico
# ------------------------------------------------


# ### MODIFICAÇÃO 4 E 5: Endpoint de IA usa a flag e seleciona o prompt
@app.post("/processar-inlabs-ia", response_model=ProcessResponse)
async def processar_inlabs_ia(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None, description="JSON string de keywords")
):
    # ... (Inicialização e Estágio 1 - sem alterações) ...
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="A variável de ambiente GEMINI_API_KEY não foi configurada no servidor.")
    try:
        model = genai.GenerativeModel('gemini-2.5-pro') 
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao inicializar o modelo de IA: {e}")

    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    custom_keywords = []
    if keywords_json:
        try:
            keywords_list = json.loads(keywords_json)
            if isinstance(keywords_list, list):
                custom_keywords = [str(k).strip().lower() for k in keywords_list if str(k).strip()]
        except json.JSONDecodeError: pass 
    
    client = await inlabs_login_and_get_session()
    pubs_filtradas: List[Publicacao] = [] 
    
    try:
        listing_url = await resolve_date_url(client, data)
        html = await fetch_listing_html(client, data)
        zip_links = pick_zip_links_from_listing(html, listing_url, secs)
        if not zip_links:
            raise HTTPException(status_code=404, detail=f"Não encontrei ZIPs para a seção '{', '.join(secs)}'.")
        
        all_xml_blobs = []
        for zurl in zip_links:
            zb = await download_zip(client, zurl)
            all_xml_blobs.extend(extract_xml_from_zip(zb))

        materias: Dict[str, Dict] = {}
        for blob in all_xml_blobs:
            try:
                soup = BeautifulSoup(blob, 'lxml-xml')
                article = soup.find('article')
                if not article: continue
                materia_id = article.get('idMateria')
                if not materia_id: continue
                if materia_id not in materias:
                    materias[materia_id] = {'main_article': None, 'full_text': ''}
                materias[materia_id]['full_text'] += blob.decode('utf-8', errors='ignore') + "\n"
                body = article.find('body')
                if body and body.find('Identifica') and body.find('Identifica').get_text(strip=True):
                    materias[materia_id]['main_article'] = article
            except Exception: continue
        
        for materia_id, content in materias.items():
            if content['main_article']:
                publication = process_grouped_materia( # Filtro Estágio 1
                    content['main_article'], content['full_text'], custom_keywords 
                )
                if publication:
                    pubs_filtradas.append(publication)
        
        seen: Set[str] = set()
        merged_pubs: List[Publicacao] = []
        for p in pubs_filtradas:
            key = (p.organ or "") + "||" + (p.type or "") + "||" (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged_pubs.append(p)
        
        # 4. Executa o ESTÁGIO 2 (Análise com IA) - Lógica MODIFICADA
        tasks = []
        for p in merged_pubs:
            
            # --- INÍCIO DA MODIFICAÇÃO 4 ---
            # Decide qual prompt usar
            prompt_to_use = GEMINI_MASTER_PROMPT
            if p.is_mpo_navy_hit:
                prompt_to_use = GEMINI_MPO_PROMPT # <-- USA O PROMPT ESPECIAL
            # --- FIM DA MODIFICAÇÃO 4 ---
                
            if p.clean_text:
                # Passa o prompt_to_use para a função
                tasks.append(get_ai_analysis(p.clean_text, model, prompt_to_use))
            else:
                # Fallback (raro, mas seguro)
                tasks.append(get_ai_analysis(p.relevance_reason or "Texto não disponível", model, prompt_to_use))
        
        ai_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 5. Monta o resultado final - Lógica MODIFICADA
        pubs_finais: List[Publicacao] = []
        for i, p in enumerate(merged_pubs):
            if i < len(ai_results):
                ai_reason_result = ai_results[i]
                
                if isinstance(ai_reason_result, Exception):
                    # Erro grave na chamada da IA
                    p.relevance_reason = f"Erro GRAVE na análise de IA: {ai_reason_result}"
                    pubs_finais.append(p)
                
                elif ai_reason_result is None:
                    # Fallback (bloqueio ou resposta vazia da IA)
                    # Mantém a razão original do Estágio 1 (p.relevance_reason)
                    pubs_finais.append(p) 
                
                elif isinstance(ai_reason_result, str):
                    # A IA retornou uma string
                    if ai_reason_result.startswith("Erro na análise de IA:"):
                        # Erro leve retornado (chave inválida, cota, etc.)
                        p.relevance_reason = ai_reason_result
                        pubs_finais.append(p)
                    
                    # --- INÍCIO DA MODIFICAÇÃO 5 ---
                    elif "sem impacto direto" in ai_reason_result.lower():
                        
                        if p.is_mpo_navy_hit:
                            # A IA desobedeceu o prompt especial!
                            print(f"ALERTA: IA desobedeceu o MPO_PROMPT para {p.type}. Respondeu 'Sem impacto'.")
                            p.relevance_reason = f"⚠️ IA ignorou impacto MPO: {ai_reason_result}"
                            pubs_finais.append(p)
                        
                        elif MPO_ORG_STRING in (p.organ or "").lower():
                            # É MPO, mas não um 'hit' da marinha (ex: S1 achou 'reforço')
                            # A IA pode estar certa.
                            p.relevance_reason = ai_reason_result
                            pubs_finais.append(p)
                        else:
                            # NÃO é MPO e é "sem impacto", DESCARTA.
                            pass 
                    # --- FIM DA MODIFICAÇÃO 5 ---
                        
                    else:
                        # IA funcionou e confirmou relevância
                        p.relevance_reason = ai_reason_result
                        pubs_finais.append(p)
                
                else:
                    # Tipo inesperado (nem str, nem Exception, nem None)
                    # Mantém a publicação com a razão original do Estágio 1
                    pubs_finais.append(p)
            
            else:
                # Caso raro: não houve resultado da IA para esta publicação
                pubs_finais.append(p)
        
        texto = monta_whatsapp(pubs_finais, data)
        return ProcessResponse(date=data, count=len(pubs_finais), publications=pubs_finais, whatsapp_text=texto)
    
    finally:
        await client.aclose()


# --- Endpoint de Teste (v13.9 - Simplificado) ---
@app.get("/test-ia")
async def test_ia_endpoint():
    # ... (código inalterado da v13.8) ...
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY não configurada.")
    try: model = genai.GenerativeModel('gemini-2.5-pro') 
    except Exception as e: raise HTTPException(status_code=500, detail=f"Falha ao inicializar modelo: {e}")
    test_prompt = "Qual a capital do Brasil?"
    print(f"Enviando prompt de teste: '{test_prompt}'")
    try:
        response = await model.generate_content_async(test_prompt) # Chamada simplificada
        try:
            analysis = norm(response.text)
            if analysis:
                print(f"Teste OK! Resposta: {analysis}")
                return {"result": f"Teste OK! Resposta da IA: '{analysis}'"}
            else:
                print("Teste FALHOU. Resposta vazia.")
                return {"result": "Teste FALHOU. Resposta vazia da IA."}
        except ValueError as e: 
            print(f"Teste FALHOU (ValueError): {e}")
            return {"result": f"Teste FALHOU. A IA foi bloqueada (ValueError): {e}"}
        except Exception as e_inner:
             print(f"Teste FALHOU (Erro Processando): {e_inner}")
             return {"result": f"Teste FALHOU. Erro processando resposta IA: {str(e_inner)[:50]}"}
    except Exception as e:
        print(f"Teste FALHOU (Erro API): {e}")
        error_msg = str(e).lower(); detail = str(e)[:100]
        if "quota" in error_msg: detail = "Cota de uso da API excedida."
        elif "api_key" in error_msg: detail = "Chave de API inválida."
        raise HTTPException(status_code=500, detail=f"Teste FALHOU. Erro na chamada da API: {detail}")
# === FIM DO ENDPOINT DE TESTE ===
