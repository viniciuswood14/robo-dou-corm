# -*- coding: utf-8 -*-
"""
Nome do arquivo: mb_portaria_parser.py
Versão: 2.4 (Correção Lógica de Validação + Leitura artCategory)
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

def _sanitize_html_content(html_str: str) -> str:
    if not html_str: return ""
    s = html_str.replace("&nbsp;", " ").replace("&quot;", '"').replace("&apos;", "'")
    return s

def _html_to_text(html: str) -> str:
    if not html: return ""
    try:
        clean_html = _sanitize_html_content(html)
        root = ET.fromstring(f"<root>{clean_html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()

def _extract_header_hint(text: str) -> str:
    if not text: return ""
    m = re.search(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    m = re.search(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    m = re.search(r"(Altera\s+parcialmente\s+grupos[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()

    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    return pre.strip()[:250].rstrip(" ,;") + "..."

def _port_id_from_text(text: str, name_attr: str) -> str:
    # 1. Tenta formato padrão completo
    m = re.search(r"PORTARIA\s+(?:[A-Z]+/?)*MPO\s+N[ºo]?\s*(\d+).+?(20\d{2})", text, flags=re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    
    # 2. Tenta pelo atributo 'name' do arquivo XML
    m2 = re.search(r"Portaria\s+(?:[A-Z]+\.?/?)*MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m2: return f"{m2.group(1)}/{m2.group(2)}"
    
    # 3. Fallback: Se tem MPO e "Nº XXX", aceita.
    if "MPO" in text.upper() or "PLANEJAMENTO" in text.upper():
        m3 = re.search(r"(?:PORTARIA|RESOLUÇÃO).*?N[ºo]?\s*(\d+)", text, flags=re.I)
        if m3:
             ano = "2025" 
             m_ano = re.search(r"(202\d)", text)
             if m_ano: ano = m_ano.group(1)
             return f"{m3.group(1)}/{ano}"

    return "PORTARIA MPO (ID n/d)"

def _group_files_by_base(zip_names: Iterable[str]) -> Dict[str, List[Tuple[int, str]]]:
    groups: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for n in zip_names:
        m = re.search(r"\d+_\d+_(\d+)(?:-(\d+))?\.xml$", n)
        if m:
            base = m.group(1)
            suffix = int(m.group(2) or 0)
            groups[base].append((suffix, n))
            
    for base in groups:
        groups[base].sort()
    return groups

def _parse_totals_rows(xml_bytes: bytes, mb_ugs: Iterable[str]) -> List[Dict]:
    try:
        parser = ET.XMLParser(encoding="utf-8")
        art = ET.fromstring(xml_bytes, parser=parser)
    except: return []

    texto = art.find(".//body/Texto")
    if texto is None or texto.text is None: return []
    
    html = texto.text
    try: 
        clean_html = _sanitize_html_content(html)
        root = ET.fromstring(f"<root>{clean_html}</root>")
    except: return []

    rows = []
    current_ug = None
    current_kind = None 
    mb_ugs = set(mb_ugs)

    for tr in root.findall(".//tr"):
        tr_text = " ".join(x.strip() for x in tr.itertext() if x.strip())
        
        m_ug = re.search(r"UNIDADE:?\s*(\d{5})", tr_text)
        if m_ug:
            current_ug = m_ug.group(1)
            current_kind = None 
            continue

        if "ACRÉSCIMO" in tr_text or "SUPLEMENTA" in tr_text.upper():
            current_kind = "SUPLEMENTACAO"
        elif "REDUÇÃO" in tr_text or "CANCELAMENTO" in tr_text.upper():
            current_kind = "CANCELAMENTO"
        
        if "ANEXO I" in tr_text and "ANEXO II" not in tr_text: current_kind = "SUPLEMENTACAO"
        if "ANEXO II" in tr_text: current_kind = "CANCELAMENTO"

        if current_ug in mb_ugs and current_kind:
            m_val = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*$", tr_text)
            is_header = "FUNCIONAL" in tr_text or "PROGRAMÁTICA" in tr_text
            
            if m_val and not is_header:
                try:
                    val_str = m_val.group(1).replace(".", "").replace(",", ".")
                    val = float(val_str)
                    is_action_row = re.match(r"\d{4}", tr_text)
                    is_total_row = "TOTAL" in tr_text.upper()
                    
                    if (is_action_row or is_total_row) and val > 0:
                        rows.append({
                            "UG": current_ug,
                            "kind": current_kind,
                            "valor": val
                        })
                except: continue
                
    return rows

def _brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

# ----------------------------- API pública ----------------------------- #

def parse_zip_in_memory(zip_file_obj: Union[str, io.BytesIO], mb_ugs: Iterable[str] = None):
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

        for base, items in groups.items():
            header_name = items[0][1] 
            try:
                with z.open(header_name) as f: xmlb = f.read()
                parser = ET.XMLParser(encoding="utf-8")
                art = ET.fromstring(xmlb, parser=parser)
                
                text_node = art.find(".//body/Texto")
                full_text = _html_to_text(text_node.text) if text_node is not None else ""
                ident_node = art.find(".//body/Identifica")
                ident_text = _html_to_text(ident_node.text) if ident_node is not None else ""
                
                combined_text = (ident_text + "\n" + full_text).strip()
                
                # Pega Categoria do Artigo (MPO/SOF)
                cat = art.attrib.get("artCategory", "").upper()

                # Extrai PID
                pid = _port_id_from_text(combined_text, art.attrib.get("name", ""))
                
                # CRITÉRIOS DE ACEITE (CORREÇÃO V2.4):
                # 1. PID válido (regex pegou "470/2025") -> Aceita
                # 2. OU Tem "PLANEJAMENTO" no texto/categoria -> Aceita
                # 3. OU Tem "MPO" no texto/categoria -> Aceita
                has_valid_id = pid != "PORTARIA MPO (ID n/d)"
                is_mpo_doc = "PLANEJAMENTO" in cat or "MPO" in cat or "PLANEJAMENTO" in full_text.upper() or "MPO" in full_text.upper()
                
                if has_valid_id or is_mpo_doc:
                    print(f"[DEBUG MPO] Identificado: {pid} (ValidID={has_valid_id}, IsMPO={is_mpo_doc}) em {header_name}")
                    base_to_pid[base] = pid
                    base_to_hint[base] = _extract_header_hint(full_text)
                    
            except Exception as e:
                print(f"[DEBUG MPO] Erro ao ler header {header_name}: {e}")
                continue

        for n in xml_names:
            m = re.search(r"\d+_\d+_(\d+)", n)
            if not m: continue
            
            base = m.group(1)
            if base in base_to_pid:
                pid = base_to_pid[base]
                try:
                    with z.open(n) as f:
                        rows = _parse_totals_rows(f.read(), mb_ugs)
                        if rows:
                            agg[pid].extend(rows)
                except: continue

        for base, pid in base_to_pid.items():
            if pid in agg:
                pid_to_hint[pid] = base_to_hint.get(base, "Ato Orçamentário")

    return agg, pid_to_hint

def render_whatsapp_block(pid: str, hint: str, rows: List[Dict]) -> str:
    sup_rows = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
    canc_rows = [r for r in rows if r["kind"] == "CANCELAMENTO"]

    def get_max_per_ug(row_list):
        ug_max = defaultdict(float)
        for r in row_list:
            if r["valor"] > ug_max[r["UG"]]:
                ug_max[r["UG"]] = r["valor"]
        return ug_max

    sup_agg = get_max_per_ug(sup_rows)
    canc_agg = get_max_per_ug(canc_rows)

    wa = []
    wa.append(f"🔎 *Análise Contábil Automática ({pid})*")
    wa.append(f"_{hint}_")
    wa.append("")

    if sup_agg:
        total_sup = sum(sup_agg.values())
        wa.append(f"🟢 *Suplementação (Crédito):* {_brl(total_sup)}")
        for ug, val in sup_agg.items():
            nome_ug = ""
            if ug == "52131": nome_ug = "- Comando da Marinha"
            elif ug == "52931": nome_ug = "- Fundo Naval"
            elif ug == "52233": nome_ug = "- AMAZUL"
            elif ug == "52232": nome_ug = "- CCCPM"
            elif ug == "52000": nome_ug = "- MD"
            wa.append(f"   └ UG {ug} {nome_ug}: {_brl(val)}")
    
    if canc_agg:
        if sup_agg: wa.append("") 
        total_canc = sum(canc_agg.values())
        wa.append(f"🔴 *Cancelamento (Redução):* {_brl(total_canc)}")
        for ug, val in canc_agg.items():
            wa.append(f"   └ UG {ug}: {_brl(val)}")

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
