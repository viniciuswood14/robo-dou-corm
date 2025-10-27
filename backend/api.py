from fastapi import FastAPI, Form, HTTPException
import re
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup

# ============================================================
# CONFIG
# ============================================================

# URL base do DOU que você já usa
DOU_API_URL = "https://www.in.gov.br/leiturajornal"

# TAGS de interesse primário (detecção geral)
TAG_KEYWORDS = [
    "marinha do brasil",
    "comando da marinha",
    "estado-maior da armada",
    "ministério da defesa",
    "gabinete do ministro da defesa",
    "fundo naval",
    "fundo de desenvolvimento do ensino profissional marítimo",
    "diretoria de portos e costas",
    "diretoria de hidrografia e navegação",
    "estado-maior conjunto das forças armadas",
    "embaixada",
]

# UOs da MB/MD que caracterizam impacto orçamentário direto
MB_UOS = {
    "52131": "Comando da Marinha",
    "52133": "Secretaria da Comissão Interministerial para os Recursos do Mar",
    "52232": "Caixa de Construções de Casas para o Pessoal da Marinha - CCCPM",
    "52233": "Amazônia Azul Tecnologias de Defesa S.A. - AMAZUL",
    "52931": "Fundo Naval",
    "52932": "Fundo de Desenvolvimento do Ensino Profissional Marítimo",
}

MB_ORGAO_COD = "52000"  # Ministério da Defesa


# ============================================================
# UTILS
# ============================================================

def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def money_from_text(txt: str) -> Optional[str]:
    # txt tipo "466.171.819" -> "R$ 466.171.819"
    t = normalize_spaces(txt)
    if re.match(r"^[0-9][0-9\.\,]*$", t):
        return f"R$ {t}"
    return None


def extract_plain_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # junta parágrafos, células de tabela etc numa string contínua
    return normalize_spaces(soup.get_text(separator="\n"))


def looks_like_mpo_portaria(titulo: str, org_path: str) -> bool:
    """
    Decide se o ato é MPO/SOF/MPO/etc e, portanto, precisa de análise orçamentária.
    """
    t_up = (titulo or "").upper()
    org_up = (org_path or "").upper()

    if "MINISTÉRIO DO PLANEJAMENTO E ORÇAMENTO" in org_up:
        if "PORTARIA" in t_up:
            # inclui GM/MPO, SOF/MPO etc
            return True

    return False


def looks_like_defesa(item_text: str) -> bool:
    """
    Heurística simples pra marcar publicação como de interesse (DOU sem IA).
    """
    txt = item_text.lower()
    for kw in TAG_KEYWORDS:
        if kw in txt:
            return True
    return False


# ============================================================
# PARSER ORÇAMENTÁRIO MPO
# ============================================================

def parse_portaria_mpo_tables(html_concat: str) -> List[Dict[str, Any]]:
    """
    Analisa o HTML bruto (juntando todos os anexos da portaria, mesma idMateria)
    e extrai blocos estruturados:
    [
        {
           "orgao_code": "53000",
           "orgao_nome": "Ministério da Integração e do Desenvolvimento Regional",
           "uo_code": "53201",
           "uo_nome": "CODEVASF",
           "tipo_bloco": "ACRÉSCIMO" ou "REDUÇÃO",
           "itens": [
               {
                 "acao": "2317 00SX 7070",
                 "descricao": "Apoio a Projetos...",
                 "valor": "60.953.531"
               },
               ...
           ],
           "total_geral": "466.171.819"
        },
        ...
    ]
    """
    soup = BeautifulSoup(html_concat, "html.parser")

    # Estratégia:
    # - Percorrer todas as <table>.
    # - Dentro de cada tabela, procurar por linhas (<tr>) que contenham:
    #   "ÓRGÃO:" / "UNIDADE:" / "PROGRAMA DE TRABALHO ( ACRÉSCIMO | REDUÇÃO )"
    # - A partir disso, empacotar blocos.

    blocos = []

    # Vamos manter o contexto corrente enquanto caminhamos linha a linha
    current_orgao_code = None
    current_orgao_nome = None
    current_uo_code = None
    current_uo_nome = None
    current_tipo_bloco = None  # "ACRÉSCIMO" ou "REDUÇÃO"
    current_itens = []
    current_total_geral = None

    def flush_block():
        nonlocal current_orgao_code, current_orgao_nome
        nonlocal current_uo_code, current_uo_nome
        nonlocal current_tipo_bloco, current_itens, current_total_geral

        if current_orgao_code and current_uo_code and current_tipo_bloco:
            blocos.append(
                {
                    "orgao_code": current_orgao_code,
                    "orgao_nome": current_orgao_nome,
                    "uo_code": current_uo_code,
                    "uo_nome": current_uo_nome,
                    "tipo_bloco": current_tipo_bloco,
                    "itens": current_itens[:],
                    "total_geral": current_total_geral,
                }
            )

        # reset parcial (mantém órgão e UO se vier outro bloco dentro da mesma UO)
        current_tipo_bloco = None
        current_itens = []
        current_total_geral = None
        return current_tipo_bloco, current_itens, current_total_geral

    # vamos processar todas as <tr> em ordem
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cols = [normalize_spaces(td.get_text(" ")) for td in tr.find_all("td")]
            rowtxt = " ".join(cols).strip()

            # Detecta ÓRGÃO
            m_org = re.search(r"ÓRGÃO:\s*(\d{2,})\s*-\s*(.+)", rowtxt, flags=re.I)
            if m_org:
                # sempre que mudar de órgão/uo, descarrega bloco anterior em blocos
                current_tipo_bloco, current_itens, current_total_geral = flush_block()

                current_orgao_code = m_org.group(1).strip()
                current_orgao_nome = m_org.group(2).strip()
                current_uo_code = None
                current_uo_nome = None
                continue

            # Detecta UNIDADE
            m_uo = re.search(r"UNIDADE:\s*(\d{2,})\s*-\s*(.+)", rowtxt, flags=re.I)
            if m_uo:
                # trocar de UO também fecha bloco em aberto da UO anterior
                current_tipo_bloco, current_itens, current_total_geral = flush_block()

                current_uo_code = m_uo.group(1).strip()
                current_uo_nome = m_uo.group(2).strip()
                continue

            # Detecta tipo de bloco (ACRÉSCIMO / REDUÇÃO)
            m_tipo = re.search(
                r"PROGRAMA DE TRABALHO\s*\(\s*(ACRÉSCIMO|REDUÇÃO)\s*\)",
                rowtxt,
                flags=re.I,
            )
            if m_tipo:
                # iniciar novo bloco de itens para essa UO
                current_tipo_bloco, current_itens, current_total_geral = flush_block()
                current_tipo_bloco = m_tipo.group(1).upper()
                current_itens = []
                current_total_geral = None
                continue

            # Captura TOTAL - GERAL
            if "TOTAL - GERAL" in rowtxt.upper():
                # última coluna deve ter o valor
                if len(cols) >= 2:
                    possible_value = cols[-1]
                    if re.match(r"^[0-9][0-9\.\,]*$", possible_value):
                        current_total_geral = possible_value
                continue

            # Captura itens programáticos (ação / descrição / valor)
            # Padrão típico:
            #   col0 = "2317 00SX 7070"
            #   col1 = "Apoio a Projetos..."
            #   colN = "60.953.531"
            # Regras:
            #  - primeira coluna começa com dígito
            #  - última coluna é número
            if (
                len(cols) >= 2
                and re.match(r"^\d", cols[0])
                and re.match(r"^[0-9][0-9\.\,]*$", cols[-1])
            ):
                acao = cols[0]
                descricao = cols[1]
                valor = cols[-1]
                current_itens.append(
                    {
                        "acao": acao,
                        "descricao": descricao,
                        "valor": valor,
                    }
                )
                continue

        # fim da tabela -> flush do último bloco em andamento
        current_tipo_bloco, current_itens, current_total_geral = flush_block()

    # flush final só por garantia
    current_tipo_bloco, current_itens, current_total_geral = flush_block()

    return blocos


def filtrar_blocos_mb(blocos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Retorna só blocos que interessam à MB/Defesa:
    - Órgão = 52000 (Ministério da Defesa)
    - OU UO em MB_UOS
    """
    relevantes = []
    for b in blocos:
        is_defesa = (
            b.get("orgao_code") == MB_ORGAO_COD
            or (b.get("orgao_nome") or "").upper().strip().startswith("MINISTÉRIO DA DEFESA")
        )
        is_uo_mb = b.get("uo_code") in MB_UOS

        if is_defesa or is_uo_mb:
            relevantes.append(b)
    return relevantes


def formatar_bloco_mb(bloco: Dict[str, Any]) -> str:
    """
    Transforma um bloco relevante em texto pronto pro WhatsApp.
    Exemplo:

    UO 52131 - Comando da Marinha
    • Suplementação (acréscimo): R$ 12.345.678
      - 2317 00SX 7070 — Aquisição de Máquinas ... — R$ 10.000.000
    """
    uo_code = bloco.get("uo_code", "")
    uo_nome = bloco.get("uo_nome", "")
    tipo = bloco.get("tipo_bloco", "")  # "ACRÉSCIMO" ou "REDUÇÃO"
    total = bloco.get("total_geral", "")

    if tipo == "ACRÉSCIMO":
        cab = f"• Suplementação (acréscimo): {money_from_text(total) or total}"
    elif tipo == "REDUÇÃO":
        cab = f"• Cancelamento (redução): {money_from_text(total) or total}"
    else:
        cab = f"• Alteração orçamentária: {money_from_text(total) or total}"

    linhas_itens = []
    for it in bloco.get("itens", []):
        acao = it["acao"]
        desc = it["descricao"]
        val = money_from_text(it["valor"]) or it["valor"]
        linhas_itens.append(f"  - {acao} — {desc} — {val}")

    uo_header = f"UO {uo_code} - {uo_nome}"
    return "\n".join([uo_header, cab] + linhas_itens)


def analisar_portaria_mpo_consolidada(html_concat: str) -> str:
    """
    Pega TODO o HTML (todas as partes da mesma portaria já concatenadas),
    extrai blocos orçamentários, filtra os da MB/Defesa e gera ⚓.
    """

    blocos = parse_portaria_mpo_tables(html_concat)

    blocos_mb = filtrar_blocos_mb(blocos)

    if not blocos_mb:
        # não achamos Defesa/Marinha/Fundo Naval nos anexos consolidados
        return (
            "Não há menção específica ou impacto direto identificado para a Marinha do Brasil, "
            "o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional "
            "Marítimo nas partes consolidadas da publicação."
        )

    partes = ["Ato orçamentário do MPO com impacto direto na MB:"]
    for b in blocos_mb:
        partes.append(formatar_bloco_mb(b))

    return "\n\n".join(partes)


# ============================================================
# CONSTRUÇÃO DO RELATÓRIO DIÁRIO
# ============================================================

def montar_item_relatorio_sem_ia(item: Dict[str, Any], analise_mpo: Optional[str]) -> str:
    """
    Monta cada bullet do "DOU sem IA".
    analise_mpo só vem preenchido para portarias MPO/SOF/MPO já consolidadas.
    Para os demais atos, a ⚓ é só TAG ou "sem impacto".
    """
    org_path = item.get("orgao_path", "").strip()
    titulo = item.get("titulo", "").strip()
    ementa = item.get("ementa", "").strip()

    header = (
        f"▶️ {org_path}\n"
        f"📌 {titulo}\n"
        f"{ementa}"
    )

    if analise_mpo is not None:
        # ou seja: esse item é MPO e já teve análise consolidada daquela portaria
        corpo = f"⚓ {analise_mpo}"
        return header + "\n" + corpo

    # senão, usar a heurística de TAG
    texto_full = (
        (org_path + " " + titulo + " " + ementa).lower()
    )
    if looks_like_defesa(texto_full):
        corpo = "⚓ Há menção específica à TAG: 'ministério da defesa'."
    else:
        corpo = (
            "⚓ Não há menção específica ou impacto direto identificado para a Marinha do Brasil, "
            "o Comando da Marinha, o Fundo Naval ou o Fundo do Desenvolvimento do Ensino Profissional "
            "Marítimo nas partes da publicação analisadas."
        )

    return header + "\n" + corpo


def montar_item_relatorio_com_ia(item: Dict[str, Any], analise_mpo: Optional[str]) -> str:
    """
    Monta cada bullet do "DOU com IA".
    Aqui você poderia, se quiser, chamar um modelo de LLM para resumir melhor.
    Agora vamos manter a mesma lógica para simplificar.
    """
    return montar_item_relatorio_sem_ia(item, analise_mpo)


def montar_relatorio_final(itens_ordenados: List[Dict[str, Any]], analises_portarias_mpo: Dict[str, str]) -> Dict[str, str]:
    """
    Gera os dois blocos finais:
    - DOU com IA
    - DOU sem IA

    `analises_portarias_mpo` = { idMateria: texto_analise_mpo }
    """
    secoes_sem_ia = []
    secoes_com_ia = []

    secoes_sem_ia.append("Bom dia, senhores!\n\nPTC as seguintes publicações de interesse no DOU:\n\n🔰 Seção 1\n")
    secoes_com_ia.append("Bom dia, senhores!\n\nPTC as seguintes publicações de interesse no DOU:\n\n🔰 Seção 1\n")

    for item in itens_ordenados:
        materia_id = item.get("idMateria")
        analise_mpo = analises_portarias_mpo.get(materia_id) if materia_id in analises_portarias_mpo else None

        bloco_sem_ia = montar_item_relatorio_sem_ia(item, analise_mpo)
        bloco_com_ia = montar_item_relatorio_com_ia(item, analise_mpo)

        secoes_sem_ia.append(bloco_sem_ia + "\n")
        secoes_com_ia.append(bloco_com_ia + "\n")

    rel_sem_ia = "\n".join(secoes_sem_ia).strip()
    rel_com_ia = "\n".join(secoes_com_ia).strip()

    return {
        "dou_sem_ia": rel_sem_ia,
        "dou_com_ia": rel_com_ia,
    }


# ============================================================
# FASTAPI SETUP
# ============================================================

app = FastAPI()

# CORS liberado para frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# FUNÇÕES DE BUSCA NO DOU
# ============================================================

async def fetch_dou(date_str: str) -> Any:
    """
    Busca a edição do DOU (Seção 1) para a data fornecida (dd/mm/yyyy)
    usando a mesma fonte que você já usava.
    """
    params = {
        "pagina": "do1",
        "data": date_str,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(DOU_API_URL, params=params)
        r.raise_for_status()
        return r.json()


def extrair_itens_dou(dou_json: Any) -> List[Dict[str, Any]]:
    """
    Transforma a resposta crua do DOU no formato interno que usamos:
    [
      {
        "idMateria": "23241535",
        "orgao_path": "Ministério .../ ...",
        "titulo": "PORTARIA GM/MPO Nº ...",
        "ementa": "Abre crédito ...",
        "conteudo_html": "<p>...</p><table>...</table>...",
      },
      ...
    ]
    """
    itens = []

    cadernos = dou_json.get("cadernos", [])
    for cad in cadernos:
        materias = cad.get("materias", [])
        for m in materias:
            idmateria = m.get("idMateria")
            orgao_path = m.get("assina", "") or m.get("orgao", "") or cad.get("orgao", "")
            # às vezes vem em campos diferentes; ajuste conforme seu JSON real.
            titulo = m.get("titulo", "") or m.get("identificacao", "")
            ementa = m.get("ementa", "") or m.get("subTitulo", "")

            # conteúdo em HTML
            corpo_html = ""
            if "html" in m:
                corpo_html = m["html"]
            elif "texto" in m:
                corpo_html = m["texto"]
            elif "materia" in m and isinstance(m["materia"], dict):
                corpo_html = m["materia"].get("texto", "")

            itens.append(
                {
                    "idMateria": str(idmateria) if idmateria else None,
                    "orgao_path": normalize_spaces(orgao_path),
                    "titulo": normalize_spaces(titulo),
                    "ementa": normalize_spaces(ementa),
                    "conteudo_html": corpo_html or "",
                }
            )

    return itens


def agrupar_portarias_mpo(itens: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Junta todas as partes da MESMA portaria MPO (mesmo idMateria).
    Retorna: { idMateria: [ {...}, {...} ] }
    """
    grupos: Dict[str, List[Dict[str, Any]]] = {}
    for it in itens:
        mid = it.get("idMateria")
        if not mid:
            continue
        if looks_like_mpo_portaria(it.get("titulo", ""), it.get("orgao_path", "")):
            grupos.setdefault(mid, [])
            grupos[mid].append(it)
    return grupos


def gerar_analises_portarias_mpo(grupos_mpo: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    """
    Para cada idMateria (portaria MPO), concatena HTML de todas as partes e
    roda a análise orçamentária consolidada.
    Retorna: { idMateria: "texto pronto pra ⚓" }
    """
    resultado: Dict[str, str] = {}

    for materia_id, partes in grupos_mpo.items():
        html_concat = "\n".join(p["conteudo_html"] for p in partes if p.get("conteudo_html"))
        analise_txt = analisar_portaria_mpo_consolidada(html_concat)
        resultado[materia_id] = analise_txt

    return resultado


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
async def healthcheck():
    return {"status": "ok"}


@app.post("/processar-rapido")
async def processar_rapido(request: Request):
    """
    Gera o 'DOU sem IA' e 'DOU com IA' para a data informada,
    consolidando portarias MPO por idMateria e aplicando parser interno.
    Body esperado: { "data": "24/10/2025" } no formato dd/mm/yyyy
    """

    body = await request.json()
    date_str = body.get("data")
    if not date_str:
        raise HTTPException(status_code=400, detail="Campo 'data' é obrigatório (dd/mm/yyyy).")

    try:
        datetime.strptime(date_str, "%d/%m/%Y")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido. Use dd/mm/yyyy.")

    dou_json = await fetch_dou(date_str)
    itens = extrair_itens_dou(dou_json)

    # agrupar portarias MPO por idMateria
    grupos_mpo = agrupar_portarias_mpo(itens)

    # gerar análises consolidadas dessas portarias
    analises_portarias_mpo = gerar_analises_portarias_mpo(grupos_mpo)

    # ordenar itens para saída final (opcional: por ordem de aparição mesmo)
    itens_ordenados = itens

    relatorios = montar_relatorio_final(itens_ordenados, analises_portarias_mpo)

    return {
        "dou_sem_ia": relatorios["dou_sem_ia"],
        "dou_com_ia": relatorios["dou_com_ia"],
        "debug": {
            "analises_portarias_mpo": analises_portarias_mpo,
        },
    }


@app.post("/processar-ia")
async def processar_ia(request: Request):
    """
    Mesmo resultado do /processar-rapido por enquanto.
    Mantemos separado para, se você quiser no futuro,
    colocar geração de resumo com modelo de IA só nesse endpoint.
    """
    return await processar_rapido(request)
