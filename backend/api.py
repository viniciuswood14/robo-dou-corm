from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Set, Dict
from datetime import datetime
import os, io, zipfile, json, re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# ##################################################################
# ########## VERSÃO 13.2 - CORREÇÃO FINAL DA FONTE PÚBLICA ##########
# ##################################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v13.2 Fonte Dupla Corrigida")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG E KEYWORDS (inalterados) ======
INLABS_BASE = os.getenv("INLABS_BASE", "https://inlabs.in.gov.br")
INLABS_LOGIN_URL = os.getenv("INLABS_LOGIN_URL", f"{INLABS_BASE}/login")
INLABS_USER = os.getenv("INLABS_USER")
INLABS_PASS = os.getenv("INLABS_PASS")
TEMPLATE_LME = """Ato de Alteração de Limite de Movimentação com impacto na Defesa/Marinha. Recomenda-se análise para detalhar os valores abaixo:\n\nAmpliação de LME - (RPX): \nAté SET: R$ XXX\nAté NOV: R$ XXX\nAté DEZ: R$ XXX"""
TEMPLATE_FONTE = """Ato de Modificação de Fontes com impacto na Defesa/Marinha. Recomenda-se análise para detalhar os valores abaixo:\n\nSuplementação (valor total):\nAO | Descrição | Fonte de Recurso | Valor\n\nCancelamento (valor total):\nAO | Descrição | Fonte de Recurso | Valor"""
TEMPLATE_CREDITO = """Ato de Crédito Suplementar com impacto na Defesa/Marinha. Recomenda-se análise para detalhar os valores abaixo:\n\nSuplementação (valor total):\nAO | Descrição | Valor\n\nCancelamento (valor total):\nAO | Descrição | Valor"""
ANNOTATION_POSITIVE_GENERIC = "Há menção específica ou impacto direto identificado para a Marinha do Brasil, o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional Marítimo nas partes da publicação analisadas."
ANNOTATION_NEGATIVE = "Não há menção específica ou impacto direto identificado para a Marinha do Brasil, o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional Marítimo nas partes da publicação analisadas."
MPO_NAVY_TAGS = {"52131": "Comando da Marinha", "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar", "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM", "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL", "52931": "Fundo Naval", "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo", "52000": "Ministério da Defesa"}
KEYWORDS_DIRECT_INTEREST_S1 = ["ministério da defesa", "forças armadas", "comandos da marinha", "comando da marinha", "marinha do brasil", "fundo naval", "amazônia azul tecnologias de defesa", "caixa de construções de casas para o pessoal da marinha", "empresa gerencial de projetos navais", "fundo de desenvolvimento do ensino profissional marítimo", "programa nuclear brasileiro"]
BUDGET_KEYWORDS_S1 = ["crédito suplementar", "crédito extraordinário", "execução orçamentária", "lei orçamentária", "orçamentos fiscal", "reforço de dotações", "programação orçamentária e financeira", "altera grupos de natureza de despesa", "limites de movimentação", "limites de pagamento", "fontes de recursos", "movimentação e empenho", "classificação orçamentária", "gestão fiscal"]
MPO_ORG_STRING = "ministério do planejamento e orçamento"
PERSONNEL_ACTION_VERBS = ["nomear", "designar", "exonerar", "dispensar", "promover", "promovido", "agregar", "autoriza o afastamento", "autorizar o afastamento", "viagem", "substituto", "conceder aposentadoria", "passar para a inatividade", "transferir para a reserva"]
NAMES_TO_TRACK = sorted(list(set(["CLAYTON LUIZ MONTES", "ANTONIO CARLOS FRISSO JÚNIOR", "MICHELLE FEVERSANI PROLO", "MYCHELLE CELESTE RABELO DE SÁ", "GERMANO SANTANA DE FREITAS", "FÁBIO PIFANO PONTES", "SERGIO PINHEIRO FIRPO", "UGO CARNEIRO CURADO", "ALEX FRAGA", "PABLO DA NÓBREGA", "JORGE LUIZ MARONI DIAS", "ADRIANA RIBEIRO MARQUES", "ALEXANDRE AUGUSTO MENDES HATADANI", "FERNANDA COSTA BERNARDES LOUZADA", "MARCOS BARBOSA PINTO", "THALITA FERREIRA DE OLIVEIRA", "CINARA DIAS CUSTÓDIO", "KELLY MIYUKI OSHIRO", "BRUNO MORETTI", "EULER ALBERGARIA DE MELO", "RODRIGO RODRIGUES DA FONSECA", "ROBERTO RAMOS COLLETTI", "PATRICK DE FARIA E SILVA", "DÉBORA RAQUEL CRUZ FERREIRA", "ELISA VIEIRA LEONEL", "RICARDO LEE NAMBA", "FABIO HENRIQUE BITTES TERRA", "BRUNO CIRILO MENDONÇA DE CAMPOS", "CHARLES CARVALHO GUEDES", "VIVIANE APARECIDA DA SILVA VARGA", "MARCELO PEREIRA DE AMORIM", "PAULO JOSÉ DOS REIS SOUZA", "MIGUEL RAGONE DE MATOS", "MARCELO MARTINS PIMENTEL", "JOSÉ ROBERTO DE MORAES REGO PAIVA FERNANDES JÚNIOR", "JOSÉ FERNANDES PONTES JUNIOR", "WALTER COSTA SANTOS", "GILSON ALVES DE ALMEIDA JÚNIOR", "GUILHERME LOURO BRAGA", "AUGUSTO CÉSAR DE CARVALHO FONSECA", "CINARA WAGNER FREDO", "MAX RODRIGO TOMAZ DE AQUINO ELIAS", "ANDRÉ GUIMARÃES RESENDE MARTINS DO VALLE", "BRUNO CORREIA CARDOSO", "OSWALDO GOMES DOS REIS JUNIOR", "FRANSELMO ARAÚJO COSTA", "JOSÉ LOPES FERNANDES", "MARCELO ARANTES GUEDON", "HERALDO LUIZ RODRIGUES", "JULIANA RIBEIRO LARENAS", "MAURÍCIO DE SOUZA BEZERRA", "IDERVÂNIO DA SILVA COSTA", "VIRGINIE HURST", "UALLACE MOREIRA LIMA", "LUIS FELIPE GIESTEIRA", "ROMILSON VOLOTÃO", "RAQUEL BARBOSA DE ALBUQUERQUE", "ANALIZE LENZI RUAS DE ALMEIDA", "FABÍOLA INEZ GUEDES DE CASTRO SALDANHA", "SUELY DIB DE SOUZA E SILVA", "ANA LUCIA GATTO DE OLIVEIRA", "ANA RACHEL FREITAS", "FABIANI FADEL BORIN", "DARCIO GUEDES JUNIOR", "MÁRIO LUÍS GURGEL DE SOUZA", "ELONI CARLOS MARIANI", "BRUNO CÉSAR GROSSI DE SOUZA", "KLEBER PAULINO DE SOUZA", "FERNANDO QUEIROZ", "FLAVIO GESCA VERISSIMO DE PAULA", "PAULO ALVARENGA", "MARIANA CUNHA ELEUTÉRIO RODRIGUES", "FABIANA MATSUO NOMURA", "VIVIANE VECCHI MENDES MULLER", "ARTUR OLAVO FERREIRA", "ALEXANDRINO MACHADO NETO", "ALEXANDRE RODRIGUES VIVEIROS", "LEONARDO DIAS DE ASSUMPÇÃO", "GUSTAVO PEREIRA PINTO", "ALEXANDRE AUGUSTO LOPES VILLELA DE MORAES", "ALEXANDRE DE MELLO BRAGA", "VICTOR LEAL DOMINGUES", "RICARDO YUKIO IAMAGUCHI", "MARCELLO NOGUEIRA CANUTO", "MARCO ALEXANDRE RODRIGUES DE AGUIAR", "MARCOS SAMPAIO OLSEN", "RENATO RODRIGUES DE AGUIAR FREIRE", "LEONARDO PUNTEL", "CELSO LUIZ NAZARETH", "CLÁUDIO PORTUGAL DE VIVEIROS", "ANDRÉ LUIZ SILVA LIMA DE SANTANA MENDES", "CLAUDIO HENRIQUE MELLO DE ALMEIDA", "EDUARDO MACHADO VAZQUEZ", "EDGAR LUIZ SIQUEIRA BARBOSA", "ALEXANDRE RABELLO DE FARIA", "SÍLVIO LUÍS DOS SANTOS", "ARTHUR FERNANDO BETTEGA CORRÊA", "RENATO GARCIA ARRUDA", "CARLOS CHAGAS VIANNA BRAGA", "GUILHERME DA SILVA COSTA", "PAULO CÉSAR BITTENCOURT FERREIRA", "ANDRÉ LUIZ DE ANDRADE FELIX", "JOSÉ ACHILLES ABREU JORGE TEIXEIRA", "ANDRÉ MORAES FERREIRA", "MARCELO MENEZES CARDOSO", "THADEU MARCOS OROSCO COELHO LOBO", "ANTONIO CARLOS CAMBRA", "ALEXANDER REIS LEITE", "AUGUSTO JOSÉ DA SILVA FONSECA JUNIOR", "ROGERIO PINTO FERREIRA RODRIGUES", "MARCO ANTONIO ISMAEL TROVÃO DE OLIVEIRA", "JOÃO ALBERTO DE ARAUJO LAMPERT", "GUSTAVO CALERO GARRIGA PIRES", "MARCO ANTÔNIO LINHARES SOARES", "CARLOS ANDRÉ CORONHA MACEDO", "CARLOS HENRIQUE DE LIMA ZAMPIERI", "ADRIANO MARCELINO BATISTA", "JOSÉ CLÁUDIO OLIVEIRA MACEDO", "JOSÉ VICENTE DE ALVARENGA FILHO", "MANOEL LUIZ PAVÃO BARROSO", "IUNIS TÁVORA SAID", "MARCELO DA SILVA GOMES", "PEDRO AUGUSTO BITTENCOURT HEINE FILHO", "JORGE JOSÉ DE MORAES RULFF", "MARCELO REIS DA SILVA", "RICARDO JAQUES FERREIRA", "FRANCISCO ANDRÉ BARROS CONDE", "VAGNER BELARMINO DE OLIVEIRA", "ALEXANDRE BESSA DE OLIVEIRA", "ALEXANDRE ITIRO VILLELA ASSANO", "ALEXANDRE TAUMATURGO PAVONI", "ANTONIO BRAZ DE SOUZA", "ALEXANDRE VERAS VASCONCELOS", "SÉRGIO BLANCO OZÓRIO", "HUMBERTO LUIS RIBEIRO BASTOS CARMO", "ALEXANDRE AMENDOEIRA NUNES", "NEYDER CAMILLO DE BARROS", "EMERSON AUGUSTO SERAFIM", "GIOVANI CORRÊA", "RICARDO LHAMAS GUASTINI", "GUSTAVO LEITE CYPRIANO NEVES", "MAURICIO BARATA SOARES COELHO RANGEL", "JOÃO CANDIDO MARQUES DIAS", "JOÃO BATISTA BARBOSA", "CARLOS MARCELO FERNANDES CONSIDERA", "DINO AVILA BUSSO", "HÉLIO MOREIRA BRANCO JUNIOR", "LEANDRO FERRONE DEMÉTRIO DE SOUZA", "FERNANDO DE LUCA MARQUES DE OLIVEIRA", "PAULO MAX VILLAS DA SILVA", "JOSÉ CARLOS DE SOUZA JUNIOR", "MARCELO DO NASCIMENTO MARCELINO", "ANDRÉ GUSTAVO SILVEIRA GUIMARÃES", "ALVARO VALENTIM LEMOS", "WASHINGTON LUIZ DE PAULA SANTOS", "PAULO ROBERTO BLANCO OZORIO", "ROBLEDO DE LEMOS COSTA E SÁ", "MARCELO LANCELLOTTI", "SÉRGIO TADEU LEÃO ROSÁRIO", "ANDRÉ RICARDO ARAUJO SILVA", "LEONARDO PACHECO VIANNA", "CARLOS ALEXANDRE ALVES BORGES DIAS", "ANDERSON MARCOS ALVES DA SILVA", "LEONARDO CAVALCANTI DE SOUZA LIMA", "LEONARDO BRAGA MARTINS", "ANDRÉ LUIZ GONÇALVES RIBEIRO", "ANDRÉ BASTOS SILVA", "ANDRÉ LUIZ SANTOS DA SILVA", "ALCIDES ROBERTO NUNES", "MARCELO BRASIL CARVALHO DA FONSECA", "EDUARDO QUESADO FILGUEIRAS"])), key=str.lower)
TERMS_AND_ACRONYMS_S2 = ["SOF", "SG-MD", "SEORI-MD", "DEORG-MD", "DEOFM", "VAlte (IM)", "CAlte (IM)", "CMG (IM)", "CF (IM)", "CC (IM)", "CT (IM)", "1T (IM)", "2T (IM)"]

class Publicacao(BaseModel):
    organ: Optional[str] = None; type: Optional[str] = None; summary: Optional[str] = None
    raw: Optional[str] = None; relevance_reason: Optional[str] = None; section: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str; count: int; publications: List[Publicacao]; whatsapp_text: str

_ws = re.compile(r"\s+")
def norm(s: Optional[str]) -> str:
    if not s: return ""
    return _ws.sub(" ", s).strip()

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    # ... (código inalterado) ...

# ===============================================================
# FUNÇÕES PARA FONTE: INLABS (XML) - (Omitidas para brevidade)
# ===============================================================

def parse_gnd_change_table(full_text_content: str) -> str:
    # ...
    return "Função de parse GND"

def process_grouped_materia(main_article: BeautifulSoup, full_text_content: str) -> Optional[Publicacao]:
    # ...
    return None

async def inlabs_login_and_get_session() -> httpx.AsyncClient:
    # ...
    return None
# ... (demais funções do INLABS omitidas)

# ===============================================================
# FUNÇÕES PARA FONTE: SITE PÚBLICO (HTML) - CORRIGIDAS
# ===============================================================

async def fetch_public_dou_html(client: httpx.AsyncClient, date: str) -> str:
    try:
        formatted_date = datetime.fromisoformat(date).strftime('%d-%m-%Y')
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD.")
    url = f"https://www.in.gov.br/leiturajornal?data={formatted_date}"
    try:
        r = await client.get(url, follow_redirects=True, timeout=90)
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Falha ao acessar o site público do DOU ({url}): HTTP {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro de rede ao buscar no site público: {str(e)}")

def parse_public_html_materia(materia_soup: BeautifulSoup, section_str: str) -> Optional[Publicacao]:
    organ = norm(materia_soup.select_one(".publicado-por").get_text(strip=True) if materia_soup.select_one(".publicado-por") else "")
    identifica = norm(materia_soup.select_one(".identifica").get_text(strip=True) if materia_soup.select_one(".identifica") else "")
    summary = norm(materia_soup.select_one(".ementa").get_text(strip=True) if materia_soup.select_one(".ementa") else "")
    display_text = norm(materia_soup.get_text(strip=True))

    if not identifica: return None
    if not summary: summary = display_text[:700] + '...'

    is_relevant = False
    reason = None
    search_content_lower = display_text.lower()
    
    section_num = re.search(r'(\d+)', section_str)
    if not section_num: return None
    
    if section_num.group(1) == '1':
        is_mpo = MPO_ORG_STRING in organ.lower()
        if is_mpo:
            if any(bkw in search_content_lower for bkw in BUDGET_KEYWORDS_S1):
                is_relevant = True
                summary_lower = summary.lower()
                if "altera parcialmente grupos de natureza de despesa" in summary_lower:
                    reason = "Ato de Alteração de GND. Recomenda-se análise manual dos anexos."
                elif "os limites de movimentação e empenho constantes" in summary_lower:
                    reason = "Ato de Alteração de Limite de Movimentação. Recomenda-se análise manual."
                elif "modifica fontes de recursos" in summary_lower:
                    reason = "Ato de Modificação de Fontes. Recomenda-se análise manual."
                elif "abre aos orçamentos fiscal" in summary_lower:
                    reason = "Ato de Crédito Suplementar. Recomenda-se análise manual."
                else:
                    reason = ANNOTATION_NEGATIVE
        else:
            for kw in KEYWORDS_DIRECT_INTEREST_S1:
                if kw in search_content_lower:
                    is_relevant = True; reason = f"Há menção específica à TAG: '{kw}'."; break
    
    elif section_num.group(1) == '2':
        clean_search_content_lower = re.sub(r'assinatura\s*eletrônica', '', search_content_lower, flags=re.I)
        combined_keywords_s2 = TERMS_AND_ACRONYMS_S2 + NAMES_TO_TRACK + PERSONNEL_ACTION_VERBS
        for kw in combined_keywords_s2:
            if kw.lower() in clean_search_content_lower:
                is_relevant = True
                reason = f"Ato de pessoal (Seção 2): menção a '{kw}'."
                break

    if is_relevant:
        return Publicacao(
            organ=organ, type=identifica, summary=summary,
            raw=display_text, relevance_reason=reason, section=section_str
        )
    return None

# ===============================================================
# ROTA PRINCIPAL UNIFICADA
# ===============================================================

@app.post("/processar", response_model=ProcessResponse)
async def processar(
    data: str = Form(..., description="YYYY-MM-DD"),
    sections: Optional[str] = Form("DO1,DO2", description="Ex.: 'DO1,DO2,DO3'"),
    source: str = Form("INLABS", description="Fonte: 'INLABS' ou 'PUBLICO'")
):
    secs = [s.strip().upper() for s in sections.split(",") if s.strip()] if sections else ["DO1"]
    pubs: List[Publicacao] = []

    if source.upper() == "INLABS":
        # Lógica INLABS completa...
        client = await inlabs_login_and_get_session()
        try:
            # ... (código omitido para brevidade)
        finally:
            await client.aclose()

    elif source.upper() == "PUBLICO":
        async with httpx.AsyncClient() as client:
            try:
                html_content = await fetch_public_dou_html(client, data)
                soup = BeautifulSoup(html_content, 'html.parser')
                
                for section_str in secs:
                    section_num_search = re.search(r'(\d+)', section_str)
                    if not section_num_search: continue
                    section_id = f"secao-{section_num_search.group(1)}"
                    
                    section_container = soup.select_one(f"#{section_id}")
                    if not section_container: continue
                    
                    materias_html = section_container.select(".publicacao")
                    for materia_soup in materias_html:
                        publication = parse_public_html_materia(materia_soup, section_str)
                        if publication:
                            pubs.append(publication)
            except HTTPException as e:
                 raise e
    else:
        raise HTTPException(status_code=400, detail="Fonte de dados inválida. Use 'INLABS' ou 'PUBLICO'.")

    # ... (Lógica de deduplicação e retorno inalterada) ...
    seen: Set[str] = set()
    merged: List[Publicacao] = []
    for p in pubs:
        key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
        if key not in seen:
            seen.add(key)
            merged.append(p)
    texto = monta_whatsapp(merged, data)
    return ProcessResponse(date=data, count=len(merged), publications=merged, whatsapp_text=texto)
