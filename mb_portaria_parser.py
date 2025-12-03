# -*- coding: utf-8 -*-
"""
Nome do arquivo: mb_portaria_parser.py
Versão: 2.1 (Correção SOF/MPO)
Descrição: Parser especializado para Portarias orçamentárias (GM, SOF, SE) do MPO.
"""
from __future__ import annotations

import re
import zipfile
import io
from xml.etree import ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple, Union

# UGs da Marinha (padrão)
MB_UGS_DEFAULT = {
    "52131", # Comando da Marinha
    "52133", # Secretaria da Comissão Interministerial para os Recursos do Mar
    "52232", # CCCPM
    "52233", # AMAZUL
    "52931", # Fundo Naval
    "52932", # Fundo de Desenvolvimento do Ensino Profissional Marítimo
    "52000"  # Ministério da Defesa (Administração Direta - Ocasional)
}

def _html_to_text(html: str) -> str:
    if not html: return ""
    try:
        # Envolve em tag root para garantir validade se for fragmento
        root = ET.fromstring(f"<root>{html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except: return ""

def _extract_header_hint(text: str) -> str:
    if not text: return ""
    # Tenta capturar o objetivo da portaria (Abre aos Orçamentos... / Altera grupos...)
    m = re.search(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    m = re.search(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    # Captura genérica de ementa (Altera... / Abre...)
    m = re.search(r"(Altera\s+parcialmente\s+grupos[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()

    # Fallback: pega o início do texto até o Anexo
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    return pre.strip()[:250].rstrip(" ,;") + "..."

def _port_id_from_text(text: str, name_attr: str) -> str:
    """
    Extrai o número da Portaria.
    CORREÇÃO v2.1: Aceita GM/MPO, SOF/MPO, SE/MPO ou apenas PORTARIA MPO.
    """
    # Regex mais permissiva: PORTARIA + (Siglas Opcionais) + MPO
    # Ex: PORTARIA SOF/MPO Nº 470
    m = re.search(r"PORTARIA\s+(?:[A-Z]+/?)*MPO\s+N[ºo]?\s*(\d+).+?DE\s+(20\d{2})", text, flags=re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    
    # Tenta pelo atributo 'name' do XML (nome do arquivo original muitas vezes tem a info)
    m3 = re.search(r"Portaria\s+(?:[A-Z]+\.?/?)*MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m3: return f"{m3.group(1)}/{m3.group(2)}"
    
    # Última tentativa: Procura "Nº XXX" próximo de "MPO"
    if "MPO" in text.upper() or "PLANEJAMENTO" in text.upper():
        m_fallback = re.search(r"(?:PORTARIA|RESOLUÇÃO).*?N[ºo]?\s*(\d+).+?DE\s+(20\d{2})", text, flags=re.I)
        if m_fallback:
             return f"{m_fallback.group(1)}/{m_fallback.group(2)}"

    return "PORTARIA MPO (ID n/d)"

def _group_files_by_base(zip_names: Iterable[str]) -> Dict[str, List[Tuple[int, str]]]:
    """Agrupa arquivos XML que pertencem à mesma matéria (ex: materia_1.xml, materia_2.xml)"""
    groups: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for n in zip_names:
        # Padrão InLabs: ID_ID_sequencia.xml
        m = re.search(r"\d+_\d+_(\d+)(?:-(\d+))?\.xml$", n)
        if m:
            base = m.group(1) # ID da Matéria
            suffix = int(m.group(2) or 0) # Sequencial (se houver quebra)
            groups[base].append((suffix, n))
            
    # Ordena pelo sufixo para ler na ordem correta
    for base in groups:
        groups[base].sort()
    return groups

def _parse_totals_rows(xml_bytes: bytes, mb_ugs: Iterable[str]) -> List[Dict]:
    """
    Lê o XML de uma parte da matéria e extrai linhas de tabela orçamentária
    que correspondam às UGs de interesse.
    """
    try:
        parser = ET.XMLParser(encoding="utf-8")
        art = ET.fromstring(xml_bytes, parser=parser)
    except: return []

    # O conteúdo real geralmente está dentro de <body><Texto> (HTML escapado ou CDATA)
    texto = art.find(".//body/Texto")
    if texto is None or texto.text is None: return []
    
    html = texto.text
    try: 
        # Parseia o HTML interno da matéria
        root = ET.fromstring(f"<root>{html}</root>")
    except: return []

    rows = []
    current_ug = None
    current_kind = None # SUPLEMENTACAO ou CANCELAMENTO
    mb_ugs = set(mb_ugs)

    # Itera sobre todas as linhas de tabela (tr) encontradas no HTML
    for tr in root.findall(".//tr"):
        # Extrai texto limpo da linha
        tr_text = " ".join(x.strip() for x in tr.itertext() if x.strip())
        
        # 1. Detecta Mudança de Unidade (UG)
        # Ex: "UNIDADE: 52131 - Comando da Marinha"
        m_ug = re.search(r"UNIDADE:?\s*(\d{5})", tr_text)
        if m_ug:
            current_ug = m_ug.group(1)
            # Reseta o tipo ao mudar de UG, pois o cabeçalho Anexo I/II vem depois
            current_kind = None 
            continue

        # 2. Detecta Tipo de Operação (Suplementação vs Cancelamento)
        # Geralmente indicado por "Excesso de Arrecadação", "Anulação", "Superávit" ou nos Anexos
        # Anexo I costuma ser Suplementação, Anexo II Redução/Cancelamento.
        if "ACRÉSCIMO" in tr_text or "SUPLEMENTA" in tr_text.upper():
            current_kind = "SUPLEMENTACAO"
        elif "REDUÇÃO" in tr_text or "CANCELAMENTO" in tr_text.upper():
            current_kind = "CANCELAMENTO"
        
        # Fallback: Se não achou na linha, verifica se é Anexo I ou II no texto
        if "ANEXO I" in tr_text and "ANEXO II" not in tr_text:
             current_kind = "SUPLEMENTACAO"
        if "ANEXO II" in tr_text:
             current_kind = "CANCELAMENTO"

        # 3. Detecta Valores Monetários associados à UG
        # Procura linhas de totais ou linhas de ação que tenham valor no final
        if current_ug in mb_ugs and current_kind:
            # Tenta capturar valor no final da linha: "1.000.000" ou "1.000.000,00"
            # Regex procura número no fim da string, permitindo pontos e vírgula
            m_val = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*$", tr_text)
            
            # Filtra linhas irrelevantes (cabeçalhos de colunas, etc)
            is_header = "FUNCIONAL" in tr_text or "PROGRAMÁTICA" in tr_text
            
            if m_val and not is_header:
                try:
                    val_str = m_val.group(1).replace(".", "").replace(",", ".")
                    val = float(val_str)
                    
                    # Evita duplicidade: as vezes o TOTAL vem logo abaixo. 
                    # Aqui pegamos tudo, depois podemos somar ou filtrar.
                    # Para simplificar, pegamos linhas que parecem ser Ações (começam com código) ou Totais
                    
                    # Se a linha começa com um código de programa/ação (ex: 2000, 21A0) ou é Total
                    is_action_row = re.match(r"\d{4}", tr_text)
                    is_total_row = "TOTAL" in tr_text.upper()
                    
                    if (is_action_row or is_total_row) and val > 0:
                        rows.append({
                            "UG": current_ug,
                            "kind": current_kind,
                            "valor": val,
                            "desc": tr_text[:50] + "..." # Snippet para debug
                        })
                except: continue
                
    return rows

def _brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

# ----------------------------- API pública ----------------------------- #

def parse_zip_in_memory(zip_file_obj: Union[str, io.BytesIO], mb_ugs: Iterable[str] = None):
    """
    Função principal chamada pelo robô.
    Abre o ZIP em memória, agrupa os XMLs por matéria e extrai dados das tabelas.
    """
    if mb_ugs is None: mb_ugs = MB_UGS_DEFAULT
    try: z = zipfile.ZipFile(zip_file_obj, "r")
    except zipfile.BadZipFile: return {}, {}

    agg = defaultdict(list)
    pid_to_hint = {}

    with z:
        xml_names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        groups = _group_files_by_base(xml_names)
        base_to_pid = {}
        base_to_hint = {}

        # Passo 1: Identificar as Matérias (Portarias)
        for base, items in groups.items():
            header_name = items[0][1] # Pega o primeiro arquivo do grupo
            try:
                with z.open(header_name) as f: xmlb = f.read()
                parser = ET.XMLParser(encoding="utf-8")
                art = ET.fromstring(xmlb, parser=parser)
                
                text_node = art.find(".//body/Texto")
                full_text = _html_to_text(text_node.text) if text_node is not None else ""
                
                # Tenta identificar se é uma portaria do MPO (SOF, GM, SE...)
                pid = _port_id_from_text(full_text, art.attrib.get("name", ""))
                
                # Se identificou MPO, guarda info
                if "MPO" in pid or "PLANEJAMENTO" in full_text.upper():
                    base_to_pid[base] = pid
                    base_to_hint[base] = _extract_header_hint(full_text)
                    
            except: continue

        # Passo 2: Processar linhas apenas das matérias identificadas
        for n in xml_names:
            m = re.search(r"\d+_\d+_(\d+)", n)
            if not m: continue
            
            base = m.group(1)
            # Só processa se for uma das portarias identificadas no passo 1
            if base in base_to_pid:
                pid = base_to_pid[base]
                try:
                    with z.open(n) as f:
                        # Extrai linhas financeiras
                        rows = _parse_totals_rows(f.read(), mb_ugs)
                        if rows:
                            agg[pid].extend(rows)
                except: continue

        # Mapeia Hints para retorno
        for base, pid in base_to_pid.items():
            if pid in agg: # Só retorna se tiver dados relevantes extraídos
                pid_to_hint[pid] = base_to_hint.get(base, "Ato Orçamentário")

    return agg, pid_to_hint

def render_whatsapp_block(pid: str, hint: str, rows: List[Dict]) -> str:
    """
    Gera o texto formatado para o WhatsApp com base nos dados extraídos.
    Lógica inteligente para somar totais e evitar repetição de linhas de detalhe.
    """
    # Filtra apenas linhas de TOTAL para o resumo inicial, se existirem
    # Se não, soma tudo.
    
    sup_rows = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
    canc_rows = [r for r in rows if r["kind"] == "CANCELAMENTO"]

    # Agrupa valores por UG para limpar duplicatas de parciais vs totais
    # Estratégia: Usar o maior valor encontrado por UG/Tipo, assumindo que é o "Total Fiscal" ou "Total Geral"
    def get_max_per_ug(row_list):
        ug_max = defaultdict(float)
        for r in row_list:
            if r["valor"] > ug_max[r["UG"]]:
                ug_max[r["UG"]] = r["valor"]
        return ug_max

    sup_agg = get_max_per_ug(sup_rows)
    canc_agg = get_max_per_ug(canc_rows)

    wa = []
    
    # Cabeçalho da análise contábil
    wa.append(f"🔎 *Análise Contábil Automática ({pid})*")
    wa.append(f"_{hint}_")
    wa.append("")

    if sup_agg:
        total_sup = sum(sup_agg.values())
        wa.append(f"🟢 *Suplementação (Crédito):* {_brl(total_sup)}")
        for ug, val in sup_agg.items():
            # Tenta dar nome à UG se for conhecida
            nome_ug = ""
            if ug == "52131": nome_ug = "- Comando da Marinha"
            elif ug == "52931": nome_ug = "- Fundo Naval"
            elif ug == "52233": nome_ug = "- AMAZUL"
            elif ug == "52232": nome_ug = "- CCCPM"
            
            wa.append(f"   └ UG {ug} {nome_ug}: {_brl(val)}")
    
    if canc_agg:
        if sup_agg: wa.append("") # Espaçamento
        total_canc = sum(canc_agg.values())
        wa.append(f"🔴 *Cancelamento (Redução):* {_brl(total_canc)}")
        for ug, val in canc_agg.items():
            wa.append(f"   └ UG {ug}: {_brl(val)}")

    # Saldo Líquido
    total_sup = sum(sup_agg.values())
    total_canc = sum(canc_agg.values())
    net = total_sup - total_canc
    
    wa.append("")
    if net > 0:
        wa.append(f"💰 *Saldo Líquido Positivo:* {_brl(net)}")
    elif net < 0:
        wa.append(f"🔻 *Saldo Líquido Negativo:* {_brl(net)}")
    else:
        wa.append(f"⚪ *Remanejamento sem alteração de valor global (QDD).*")
    
    return "\n".join(wa)
