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
# ########## VERSÃO 10.0 - PROCESSAMENTO MULTI-SEÇÃO ##########
# #############################################################

app = FastAPI(title="Robô DOU API (INLABS XML) - v10.0 Multi-Seção")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ====== CONFIG ======
# ... (config INLABS inalterada) ...

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
# Lista consolidada com os termos que você pediu
KEYWORDS_SECAO_2 = sorted(list(set([
    "Substituição", "Substituto", "Viagem", "Afastamento do País", "Nomeação", "Nomear", "Exoneração", "Exonerar", "Designar", "Dispensar",
    "Comandante da Marinha", "Almirante", "Almirantes", "SOF", "SG-MD", "SEORI-MD", "DEORG-MD", "DEOFM",
    "Secretária Adjunta VII", "SEPAC", "CC-PR",
    "VAlte (IM)", "CAlte (IM)", "CMG (IM)", "CF (IM)", "CC (IM)", "CT (IM)", "1T (IM)", "2ºT (IM)",
    "CLAYTON LUIZ MONTES", "ANTONIO CARLOS FRISSO JÚNIOR", "MICHELLE FEVERSANI PROLO", "MYCHELLE CELESTE RABELO DE SÁ", "GERMANO SANTANA DE FREITAS", "FÁBIO PIFANO PONTES", "SERGIO PINHEIRO FIRPO", "UGO CARNEIRO CURADO", "ALEX FRAGA", "PABLO DA NÓBREGA", "JORGE LUIZ MARONI DIAS", "ADRIANA RIBEIRO MARQUES", "ALEXANDRE AUGUSTO MENDES HATADANI", "FERNANDA COSTA BERNARDES LOUZADA", "MARCOS BARBosa PINTO", "THALITA FERREIRA DE OLIVEIRA", "CINARA DIAS CUSTÓDIO", "KELLY MIYUKI OSHIRO", "BRUNO MORETTI", "EULER ALBERGARIA DE MELO", "RODRIGO RODRIGUES DA FONSECA", "ROBERTO RAMOS COLLETTI", "PATRICK DE FARIA E SILVA", "DÉBORA RAQUEL CRUZ FERREIRA", "ELISA VIEIRA LEONEL", "RICARDO LEE NAMBA", "FABIO HENRIQUE BITTES TERRA", "BRUNO CIRILO MENDONÇA DE CAMPOS", "CHARLES CARVALHO GUEDES", "VIVIANE APARECIDA DA SILVA VARGA", "MARCELO PEREIRA DE AMORIM", "PAULO JOSÉ DOS REIS SOUZA", "MIGUEL RAGONE DE MATTOS", "MARCELO MARTINS PIMENTEL", "JOSÉ ROBERTO DE MORAES REGO PAIVA FERNANDES JÚNIOR", "JOSÉ FERNANDES PONTES JUNIOR", "WALTER COSTA SANTOS", "GILSON ALVES DE ALMEIDA JÚNIOR", "GUILHERME LOURO BRAGA", "AUGUSTO CÉSAR DE CARVALHO FONSECA", "CINARA WAGNER FREDO", "MAX RODRIGO TOMAZ DE AQUINO ELIAS", "ANDRÉ GUIMARÃES RESENDE MARTINS DO VALLE", "ANDRÉ CORREIA CARDOSO", "OSWALDO GOMES DOS REIS JUNIOR", "FRANSELMO ARAÚJO COSTA", "JOSÉ LOPES FERNANDES", "MARCELO ARANTES GUEDON", "HERALDO LUIZ RODRIGUES", "JULIANA RIBEIRO LARENAS", "MAURÍCIO DE SOUZA BEZERRA", "IDERVÂNIO DA SILVA COSTA", "VIRGINIE HURST", "UALLACE MOREIRA LIMA", "LUIS FELIPE GIESTEIRA", "ROMILSON VOLOTÃO", "RAQUEL BARBOSA DE ALBUQUERQUE", "ANALIZE LENZI RUAS DE ALMEIDA", "FABÍOLA INEZ GUEDES DE CASTRO SALDANHA", "SUELY DIB DE SOUZA E SILVA", "ANA LUCIA GATTO DE OLIVEIRA", "ANA RACHEL FREITAS", "FABIANI FADEL BORIN", "DARCIO GUEDES JUNIOR", "MÁRIO LUÍS GURGEL DE SOUZA", "ELONI CARLOS MARIANI", "BRUNO CÉSAR GROSSI DE SOUZA", "KLEBER PAULINO DE SOUZA", "FERNANDO QUEIROZ", "FLAVIO GESCA VERISSIMO DE PAULA", "PAULO ALVARENGA", "MARIANA CUNHA ELEUTÉRIO RODRIGUES", "FABIANA MATSUO NOMURA", "VIVIANE VECCHI MENDES MULLER", "ARTUR OLAVO FERREIRA", "ALEXANDRINO MACHADO NETO", "ALEXANDRE RODRIGUES VIVEIROS", "LEONARDO DIAS DE ASSUMPÇÃO", "GUSTAVO PEREIRA PINTO", "ALEXANDRE AUGUSTO LOPES VILLELA DE MORAES", "ALEXANDRE DE MELLO BRAGA", "VICTOR LEAL DOMINGUES", "RICARDO YUKIO IAMAGUCHI", "MARCELLO NOGUEIRA CANUTO", "MARCO ALEXANDRE RODRIGUES DE AGUIAR", "MARCOS SAMPAIO OLSEN", "RENATO RODRIGUES DE AGUIAR FREIRE", "LEONARDO PUNTEL", "CELSO LUIZ NAZARETH", "CLÁUDIO PORTUGAL DE VIVEIROS", "ANDRÉ LUIZ SILVA LIMA DE SANTANA MENDES", "CLAUDIO HENRIQUE MELLO DE ALMEIDA", "EDUARDO MACHADO VAZQUEZ", "EDGAR LUIZ SIQUEIRA BARBOSA", "ALEXANDRE RABELLO DE FARIA", "SÍLVIO LUÍS DOS SANTOS", "ARTHUR FERNANDO BETTEGA CORRÊA", "RENATO GARCIA ARRUDA", "CARLOS CHAGAS VIANNA BRAGA", "GUILHERME DA SILVA COSTA", "PAULO CÉSAR BITTENCOURT FERREIRA", "ANDRÉ LUIZ DE ANDRADE FELIX", "JOSÉ ACHILLES ABREU JORGE TEIXEIRA", "ANDRÉ MORAES FERREIRA", "MARCELO MENEZES CARDOSO", "THADEU MARCOS OROSCO COELHO LOBO", "ANTONIO CARLOS CAMBRA", "ALEXANDER REIS LEITE", "AUGUSTO JOSÉ DA SILVA FONSECA JUNIOR", "ROGERIO PINTO FERREIRA RODRIGUES", "MARCO ANTONIO ISMAEL TROVÃO DE OLIVEIRA", "JOÃO ALBERTO DE ARAUJO LAMPERT", "GUSTAVO CALERO GARRIGA PIRES", "MARCO ANTÔNIO LINHARES SOARES", "CARLOS ANDRÉ CORONHA MACEDO", "CARLOS HENRIQUE DE LIMA ZAMPIERI", "ADRIANO MARCELINO BATISTA", "JOSÉ CLÁUDIO OLIVEIRA MACEDO", "JOSÉ VICENTE DE ALVARENGA FILHO", "MANOEL LUIZ PAVÃO BARROSO", "IUNIS TÁVORA SAID", "MARCELO DA SILVA GOMES", "PEDRO AUGUSTO BITTENCOURT HEINE FILHO", "JORGE JOSÉ DE MORAES RULFF", "MARCELO REIS DA SILVA", "RICARDO JAQUES FERREIRA", "FRANCISCO ANDRÉ BARROS CONDE", "VAGNER BELARMINO DE OLIVEIRA", "ALEXANDRE BESSA DE OLIVEIRA", "ALEXANDRE ITIRO VILLELA ASSANO", "ALEXANDRE TAUMATURGO PAVONI", "ANTONIO BRAZ DE SOUZA", "ALEXANDRE VERAS VASCONCELOS", "SÉRGIO BLANCO OZÓRIO", "HUMBERTO LUIS RIBEIRO BASTOS CARMO", "ALEXANDRE AMENDOEIRA NUNES", "NEYDER CAMILLO DE BARROS", "EMERSON AUGUSTO SERAFIM", "GIOVANI CORRÊA", "RICARDO LHAMAS GUASTINI", "GUSTAVO LEITE CYPRIANO NEVES", "MAURICIO BARATA SOARES COELHO RANGEL", "JOÃO CANDIDO MARQUES DIAS", "JOÃO BATISTA BARBOSA", "CARLOS MARCELO FERNANDES CONSIDERA", "DINO AVILA BUSSO", "HÉLIO MOREIRA BRANCO JUNIOR", "LEANDRO FERRONE DEMÉTRIO DE SOUZA", "FERNANDO DE LUCA MARQUES DE OLIVEIRA", "PAULO MAX VILLAS DA SILVA", "JOSÉ CARLOS DE SOUZA JUNIOR", "MARCELO DO NASCIMENTO MARCELINO", "ANDRÉ GUSTAVO SILVEIRA GUIMARÃES", "ALVARO VALENTIM LEMOS", "WASHINGTON LUIZ DE PAULA SANTOS", "PAULO ROBERTO BLANCO OZORIO", "ROBLEDO DE LEMOS COSTA E SÁ", "MARCELO LANCELLOTTI", "SÉRGIO TADEU LEÃO ROSÁRIO", "ANDRÉ RICARDO ARAUJO SILVA", "LEONARDO PACHECO VIANNA", "CARLOS ALEXANDRE ALVES BORGES DIAS", "ANDERSON MARCOS ALVES DA SILVA", "LEONARDO CAVALCANTI DE SOUZA LIMA", "LEONARDO BRAGA MARTINS", "ANDRÉ LUIZ GONÇALVES RIBEIRO", "ANDRÉ BASTOS SILVA", "ANDRÉ LUIZ SANTOS DA SILVA", "ALCIDES ROBERTO NUNES", "MARCELO BRASIL CARVALHO DA FONSECA", "EDUARDO QUESADO FILGUEIRAS"
]), key=str.lower)))


class Publicacao(BaseModel):
    organ: Optional[str] = None
    type: Optional[str] = None
    summary: Optional[str] = None
    raw: Optional[str] = None
    relevance_reason: Optional[str] = None
    section: Optional[str] = None # Novo campo para agrupar

class ProcessResponse(BaseModel):
    # ... (inalterado) ...

# ... (função norm inalterada) ...

def monta_whatsapp(pubs: List[Publicacao], when: str) -> str:
    # Lógica de data e saudação
    meses_pt = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN", 7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}
    try:
        dt = datetime.fromisoformat(when)
        dd = f"{dt.day:02d}{meses_pt.get(dt.month, '')}"
    except Exception:
        dd = when
    
    lines = ["Bom dia, senhores!", "", f"PTC as seguintes publicações de interesse no DOU de {dd}:", ""]

    # Agrupa publicações por seção
    pubs_by_section: Dict[str, List[Publicacao]] = {}
    for p in pubs:
        sec = p.section or "DOU"
        if sec not in pubs_by_section:
            pubs_by_section[sec] = []
        pubs_by_section[sec].append(p)

    if not pubs:
        lines.append("— Sem ocorrências para os critérios informados —")
        return "\n".join(lines)

    # Itera sobre as seções e imprime o conteúdo
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
    
    # Aplica o filtro correto com base na seção
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
            # Para nomes, a busca deve ser mais cuidadosa
            if len(kw) > 4 and kw.isalpha(): # Heurística para nomes
                if kw.lower() in search_content_lower:
                    is_relevant = True
                    reason = f"Ato de pessoal (Seção 2): menção a '{kw}'."
                    break
            else: # Para siglas e termos
                if re.search(r'\b' + re.escape(kw.lower()) + r'\b', search_content_lower):
                    is_relevant = True
                    reason = f"Ato de pessoal (Seção 2): menção a '{kw}'."
                    break

    if is_relevant:
        final_summary = summary if summary else (display_text[:500] + '...' if len(display_text) > 500 else display_text)
        return Publicacao(
            organ=organ,
            type=act_type,
            summary=final_summary,
            raw=display_text,
            relevance_reason=reason,
            section=section
        )
    return None

# ... (demais funções do backend, como login e download, inalteradas) ...

@app.post("/processar-inlabs", response_model=ProcessResponse)
# ... (lógica principal inalterada, já agrupa por matéria) ...
