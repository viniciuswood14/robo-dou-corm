from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set, Dict
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# #############################################################
# ########## VERSÃO 10.2 - SINTAXE CORRIGIDA ##########
# #############################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v10.2 Corrigido")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")


# ====== ANOTAÇÕES PADRÃO ======
ANNOTATION_NEGATIVE = "Não há menção específica ou impacto direto identificado para a Marinha do Brasil, o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional Marítimo nas partes da publicação analisadas."

# ====== LISTAS DE PALAVRAS-CHAVE POR SEÇÃO ======

# SEÇÃO 1
MPO_NAVY_TAGS = {"52131": "Comando da Marinha", "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar", "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM", "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL", "52931": "Fundo Naval", "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo", "52000": "Ministério da Defesa"}
KEYWORDS_DIRECT_INTEREST_S1 = ["ministério da defesa", "forças armadas", "comandos da marinha", "comando da marinha", "marinha do brasil", "fundo naval", "amazônia azul tecnologias de defesa", "caixa de construções de casas para o pessoal da marinha", "empresa gerencial de projetos navais", "fundo de desenvolvimento do ensino profissional marítimo", "programa nuclear brasileiro"]
BUDGET_KEYWORDS_S1 = ["crédito suplementar", "crédito extraordinário", "execução orçamentária", "lei orçamentária", "orçamentos fiscal", "reforço de dotações", "programação orçamentária e financeira", "altera grupos de natureza de despesa", "limites de movimentação", "limites de pagamento", "fontes de recursos", "movimentação e empenho", "classificação orçamentária", "gestão fiscal"]
BROAD_IMPACT_KEYWORDS_S1 = ["diversos órgãos", "vários órgãos", "diversos ministérios"]
MPO_ORG_STRING = "ministério do planejamento e orçamento"

# SEÇÃO 2
# CORREÇÃO DEFINITIVA: Parênteses ajustados para que 'key' seja argumento de 'sorted'.
KEYWORDS_SECAO_2 = sorted(list(set([
    "Comandante da Marinha", "Almirante", "Almirantes", 
    "Secretária Adjunta VII", 
    "VAlte (IM)", "CAlte (IM)", "CMG (IM)", "CF (IM)", "CC (IM)", "CT (IM)", "1T (IM)", "2ºT (IM)",
    "CLAYTON LUIZ MONTES", "ANTONIO CARLOS FRISSO JÚNIOR", "MICHELLE FEVERSANI PROLO", "MYCHELLE CELESTE RABELO DE SÁ", "GERMANO SANTANA DE FREITAS", "FÁBIO PIFANO PONTES", "SERGIO PINHEIRO FIRPO", "UGO CARNEIRO CURADO", "ALEX FRAGA", "PABLO DA NÓBREGA", "JORGE LUIZ MARONI DIAS", "ADRIANA RIBEIRO MARQUES", "ALEXANDRE AUGUSTO MENDES HATADANI", "FERNANDA COSTA BERNARDES LOUZADA", "MARCOS BARBOSA PINTO", "THALITA FERREIRA DE OLIVEIRA", "CINARA DIAS CUSTÓDIO", "KELLY MIYUKI OSHIRO", "BRUNO MORETTI", "EULER ALBERGARIA DE MELO", "RODRIGO RODRIGUES DA FONSECA", "ROBERTO RAMOS COLLETTI", "PATRICK DE FARIA E SILVA", "DÉBORA RAQUEL CRUZ FERREIRA", "ELISA VIEIRA LEONEL", "RICARDO LEE NAMBA", "FABIO HENRIQUE BITTES TERRA", "BRUNO CIRILO MENDONÇA DE CAMPOS", "CHARLES CARVALHO GUEDES", "VIVIANE APARECIDA DA SILVA VARGA", "MARCELO PEREIRA DE AMORIM", "PAULO JOSÉ DOS REIS SOUZA", "MIGUEL RAGONE DE MATTOS", "MARCELO MARTINS PIMENTEL", "JOSÉ ROBERTO DE MORAES REGO PAIVA FERNANDES JÚNIOR", "JOSÉ FERNANDES PONTES JUNIOR", "WALTER COSTA SANTOS", "GILSON ALVES DE ALMEIDA JÚNIOR", "GUILHERME LOURO BRAGA", "AUGUSTO CÉSAR DE CARVALHO FONSECA", "CINARA WAGNER FREDO", "MAX RODRIGO TOMAZ DE AQUINO ELIAS", "ANDRÉ GUIMARÃES RESENDE MARTINS DO VALLE", "BRUNO CORREIA CARDOSO", "OSWALDO GOMES DOS REIS JUNIOR", "FRANSELMO ARAÚJO COSTA", "JOSÉ LOPES FERNANDES", "MARCELO ARANTES GUEDON", "HERALDO LUIZ RODRIGUES", "JULIANA RIBEIRO LARENAS", "MAURÍCIO DE SOUZA BEZERRA", "IDERVÂNIO DA SILVA COSTA", "VIRGINIE HURST", "UALLACE MOREIRA LIMA", "LUIS FELIPE GIESTEIRA", "ROMILSON VOLOTÃO", "RAQUEL BARBOSA DE ALBUQUERQUE", "ANALIZE LENZI RUAS DE ALMEIDA", "FABÍOLA INEZ GUEDES DE CASTRO SALDANHA", "SUELY DIB DE SOUZA E SILVA", "ANA LUCIA GATTO DE OLIVEIRA", "ANA RACHEL FREITAS", "FABIANI FADEL BORIN", "DARCIO GUEDES JUNIOR", "MÁRIO LUÍS GURGEL DE SOUZA", "ELONI CARLOS MARIANI", "BRUNO CÉSAR GROSSI DE SOUZA", "KLEBER PAULINO DE SOUZA", "FERNANDO QUEIROZ", "FLAVIO GESCA VERISSIMO DE PAULA", "PAULO ALVARENGA", "MARIANA CUNHA ELEUTÉRIO RODRIGUES", "FABIANA MATSUO NOMURA", "VIVIANE VECCHI MENDES MULLER", "ARTUR OLAVO FERREIRA", "ALEXANDRINO MACHADO NETO", "ALEXANDRE RODRIGUES VIVEIROS", "LEONARDO DIAS DE ASSUMPÇÃO", "GUSTAVO PEREIRA PINTO", "ALEXANDRE AUGUSTO LOPES VILLELA DE MORAES", "ALEXANDRE DE MELLO BRAGA", "VICTOR LEAL DOMINGUES", "RICARDO YUKIO IAMAGUCHI", "MARCELLO NOGUEIRA CANUTO", "MARCO ALEXANDRE RODRIGUES DE AGUIAR", "MARCOS SAMPAIO OLSEN", "RENATO RODRIGUES DE AGUIAR FREIRE", "LEONARDO PUNTEL", "CELSO LUIZ NAZARETH", "CLÁUDIO PORTUGAL DE VIVEIROS", "ANDRÉ LUIZ SILVA LIMA DE SANTANA MENDES", "CLAUDIO HENRIQUE MELLO DE ALMEIDA", "EDUARDO MACHADO VAZQUEZ", "EDGAR LUIZ SIQUEIRA BARBOSA", "ALEXANDRE RABELLO DE FARIA", "SÍLVIO LUÍS DOS SANTOS", "ARTHUR FERNANDO BETTEGA CORRÊA", "RENATO GARCIA ARRUDA", "CARLOS CHAGAS VIANNA BRAGA", "GUILHERME DA SILVA COSTA", "PAULO CÉSAR BITTENCOURT FERREIRA", "ANDRÉ LUIZ DE ANDRADE FELIX", "JOSÉ ACHILLES ABREU JORGE TEIXEIRA", "ANDRÉ MORAES FERREIRA", "MARCELO MENEZES CARDOSO", "THADEU MARCOS OROSCO COELHO LOBO", "ANTONIO CARLOS CAMBRA", "ALEXANDER REIS LEITE", "AUGUSTO JOSÉ DA SILVA FONSECA JUNIOR", "ROGERIO PINTO FERREIRA RODRIGUES", "MARCO ANTONIO ISMAEL TROVÃO DE OLIVEIRA", "JOÃO ALBERTO DE ARAUJO LAMPERT", "GUSTAVO CALERO GARRIGA PIRES", "MARCO ANTÔNIO LINHARES SOARES", "CARLOS ANDRÉ CORONHA MACEDO", "CARLOS HENRIQUE DE LIMA ZAMPIERI", "ADRIANO MARCELINO BATISTA", "JOSÉ CLÁUDIO OLIVEIRA MACEDO", "JOSÉ VICENTE DE ALVARENGA FILHO", "MANOEL LUIZ PAVÃO BARROSO", "IUNIS TÁVORA SAID", "MARCELO DA SILVA GOMES", "PEDRO AUGUSTO BITTENCOURT HEINE FILHO", "JORGE JOSÉ DE MORAES RULFF", "MARCELO REIS DA SILVA", "RICARDO JAQUES FERREIRA", "FRANCISCO ANDRÉ BARROS CONDE", "VAGNER BELARMINO DE OLIVEIRA", "ALEXANDRE BESSA DE OLIVEIRA", "ALEXANDRE ITIRO VILLELA ASSANO", "ALEXANDRE TAUMATURGO PAVONI", "ANTONIO BRAZ DE SOUZA", "ALEXANDRE VERAS VASCONCELOS", "SÉRGIO BLANCO OZÓRIO", "HUMBERTO LUIS RIBEIRO BASTOS CARMO", "ALEXANDRE AMENDOEIRA NUNES", "NEYDER CAMILLO DE BARROS", "EMERSON AUGUSTO SERAFIM", "GIOVANI CORRÊA", "RICARDO LHAMAS GUASTINI", "GUSTAVO LEITE CYPRIANO NEVES", "MAURICIO BARATA SOARES COELHO RANGEL", "JOÃO CANDIDO MARQUES DIAS", "JOÃO BATISTA BARBOSA", "CARLOS MARCELO FERNANDES CONSIDERA", "DINO AVILA BUSSO", "HÉLIO MOREIRA BRANCO JUNIOR", "LEANDRO FERRONE DEMÉTRIO DE SOUZA", "FERNANDO DE LUCA MARQUES DE OLIVEIRA", "PAULO MAX VILLAS DA SILVA", "JOSÉ CARLOS DE SOUZA JUNIOR", "MARCELO DO NASCIMENTO MARCELINO", "ANDRÉ GUSTAVO SILVEIRA GUIMARÃES", "ALVARO VALENTIM LEMOS", "WASHINGTON LUIZ DE PAULA SANTOS", "PAULO ROBERTO BLANCO OZORIO", "ROBLEDO DE LEMOS COSTA E SÁ", "MARCELO LANCELLOTTI", "SÉRGIO TADEU LEÃO ROSÁRIO", "ANDRÉ RICARDO ARAUJO SILVA", "LEONARDO PACHECO VIANNA", "CARLOS ALEXANDRE ALVES BORGES DIAS", "ANDERSON MARCOS ALVES DA SILVA", "LEONARDO CAVALCANTI DE SOUZA LIMA", "LEONARDO BRAGA MARTINS", "ANDRÉ LUIZ GONÇALVES RIBEIRO", "ANDRÉ BASTOS SILVA", "ANDRÉ LUIZ SANTOS DA SILVA", "ALCIDES ROBERTO NUNES", "MARCELO BRASIL CARVALHO DA FONSECA", "EDUARDO QUESADO FILGUEIRAS"
])), key=str.lower)

class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str
    count: int
    publications: List[Publicacao]
    whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    # ... (código inalterado) ...
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception:
        dd = when
    lines = ["Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]
    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU"
        if sec not in pubs_by_section:
            pubs_by_section[sec] = []
        pubs_by_section[sec].append(p)
    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)
    for section_name in sorted(pubs_by_section.keys()):
        lines.append(f"🔰 {section_name.replace('DO', 'Seção ')}")
        lines.append("")
        for p in pubs_by_section[section_name]:
            lines.append(f"▶️ {p.organ or 'Órgão'}")
            lines.append(f"📌 {p.type or 'Ato/Portaria'}")
            if p.summary: lines.append(p.summary)
            if p.relevance_reason:
                lines.append(f"⚓ {p.relevance_reason}")
            else:
                lines.append("⚓ Para conhecimento.")
            lines.append("")
    return "\n".join(lines)

def process_grouped_materia(main_article: BeautifulSoup, full_text_content: str) -> Optional[Publicacao]:
    # ... (código inalterado) ...
    organ = norm(main_article.get('artCategory', ''))
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
    search_content_lower = norm(full_text_content).lower()
    is_relevant = False
    reason = None
    if "DO1" in section:
        is_mpo = MPO_ORG_STRING in organ.lower()
        if is_mpo:
            found_tags_in_mpo = [name for code, name in MPO_NAVY_TAGS.items() if code in search_content_lower]
            if found_tags_in_mpo:
                is_relevant = True
                reason = f"Há menção específica ou impacto direto identificado para {', '.join(found_tags_in_mpo)} nas partes da publicação analisadas."
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
        for kw in KEYWORDS_SECAO_2:
            # Para nomes, busca case-insensitive simples
            if len(kw) > 4 and kw.isalpha() and " " in kw: 
                if kw.lower() in search_content_lower:
                    is_relevant = True
                    reason = f"Ato de pessoal (Seção 2): menção a '{kw}'."
                    break
            # Para siglas e termos, busca como palavra inteira
            else: 
                if re.search(r'\b' + re.escape(kw.lower()) + r'\b', search_content_lower):
                    is_relevant = True
                    reason = f"Ato de pessoal (Seção 2): menção a '{kw}'."
                    break
    if is_relevant:
        final_summary = summary if summary else (display_text[:500] + '...' if len(display_text) > 500 else display_text)
        return Publicacao(organ=organ, type=act_type, summary=final_summary, raw=display_text, relevance_reason=reason, section=section)
    return None

async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    # ... (código inalterado)
    if not INLABS_USER or not INLABS_PASS: raise HTTPException(status_code=500, detail="Config ausente: INLABS_USER e INLABS_PASS.")
    client = httpx.AsyncClient(timeout=60, follow_redirects=True)
    try: await client.get(INLABS_BASE)
    except Exception: pass
    r = await client.post(INLABS_LOGIN_URL, data={"email": INLABS_USER, "password": INLABS_PASS})
    if r.status_code >= 400: await client.aclose(); raise HTTPException(status_code=502, detail=f"Falha de login no INLABS: HTTP {r.status_code}")
    return client
async def resolve_date_url(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado)
    r = await client.get(INLABS_BASE); r.raise_for_status(); soup = BeautifulSoup(r.text, "html.parser"); cand_texts = [date, date.replace("-", "_"), date.replace("-", "")];
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip(); txt = (a.get_text() or "").strip(); hay = (txt + " " + href).lower()
        if any(c.lower() in hay for c in cand_texts): return urljoin(INLABS_BASE.rstrip("/") + "/", href.lstrip("/"))
    fallback_url = f"{INLABS_BASE.rstrip('/')}/{date}/"; rr = await client.get(fallback_url)
    if rr.status_code == 200: return fallback_url
    raise HTTPException(status_code=404, detail=f"Não encontrei a pasta/listagem da data {date} após o login.")
async def fetch_listing_html(client: httpx.AsyncClient, date: str) -> str:
    # ... (código inalterado)
    url = await resolve_date_url(client, date); r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao abrir listagem {url}: HTTP {r.status_code}")
    return r.text
def pick_zip_links_from_listing(html: str, base_url_for_rel: str, only_sections: List[str]) -> List[str]:
    # ... (código inalterado)
    soup = BeautifulSoup(html, "html.parser"); links: List[str] = []; wanted = set(s.upper() for s in only_sections) if only_sections else {"DO1"}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".zip") and any(sec in (a.get_text() or href).upper() for sec in wanted): links.append(urljoin(base_url_for_rel.rstrip("/") + "/", href))
    return sorted(list(set(links)))
async def download_zip(client: httpx.AsyncClient, url: str) -> bytes:
    # ... (código inalterado)
    r = await client.get(url)
    if r.status_code >= 400: raise HTTPException(status_code=502, detail=f"Falha ao baixar ZIP {url}: HTTP {r.status_code}")
    return r.content
def extract_xml_from_zip(zip_bytes: bytes) -> List[bytes]:
    # ... (código inalterado)
    xml_blobs: List[bytes] = [];
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.lower().endswith(".xml"): xml_blobs.append(z.read(name))
    return xml_blobs

@app.post("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1", description="Ex.: 'DO1' ou 'DO1,DO2,DO3'"),
    keywords_json: Optional[str] = Form(None)
):
    # ... (código inalterado)
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    if keywords_json:
        raise HTTPException(status_code=400, detail="Customização de keywords desativada em favor da lógica inteligente.")
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
            except Exception:
                continue
        pubs: List[Publicacao] = []
        for materia_id, content in materias.items():
            if content['main_article']:
                publication = process_grouped_materia(content['main_article'], content['full_text'])
                if publication:
                    pubs.append(publication)
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
