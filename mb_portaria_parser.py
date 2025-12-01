# -*- coding: utf-8 -*-
"""
Parser para Portarias GM/MPO (DOU) com foco nas UGs da MB.
Versão Adaptada para In-Memory Processing (io.BytesIO) e Formatação Padrão.
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
    "52131", "52133", "52232", "52233", "52931", "52932", "52000"
}

# ... (MANTENHA AS FUNÇÕES DE HELPERS IGUAIS ATÉ '_brl') ...
# ... (_html_to_text, _extract_header_hint, _port_id_from_text, _group_files_by_base, _parse_totals_rows, _brl) ...

def _html_to_text(html: str) -> str:
    if not html: return ""
    try:
        root = ET.fromstring(f"<root>{html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except: return ""

def _extract_header_hint(text: str) -> str:
    if not text: return ""
    m = re.search(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    return pre.strip()[:250].rstrip(" ,;") + "..."

def _port_id_from_text(text: str, name_attr: str) -> str:
    m = re.search(r"PORTARIA\s+GM/?MPO\s+N[ºo]?\s*(\d+).+?DE\s+(20\d{2})", text, flags=re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    m3 = re.search(r"Portaria\s+GM\.?/?MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m3: return f"{m3.group(1)}/{m3.group(2)}"
    return "PORTARIA GM/MPO"

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
    try: root = ET.fromstring(f"<root>{html}</root>")
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

        if "PROGRAMA DE TRABALHO" in tr_text:
            if re.search(r"cancelamento", tr_text, flags=re.I): current_kind = "CANCELAMENTO"
            elif re.search(r"suplementa", tr_text, flags=re.I): current_kind = "SUPLEMENTACAO"
            continue

        if re.search(r"TOTAL\s*-\s*GERAL", tr_text, flags=re.I):
            m_val = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*$", tr_text)
            if not m_val or not current_ug or not current_kind: continue
            try:
                val = float(m_val.group(1).replace(".", "").replace(",", "."))
                if current_ug in mb_ugs:
                    rows.append({"UG": current_ug, "kind": current_kind, "valor": val})
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

    with z:
        xml_names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        groups = _group_files_by_base(xml_names)
        base_to_pid, base_to_hint = {}, {}

        for base, items in groups.items():
            header_name = items[0][1]
            try:
                with z.open(header_name) as f: xmlb = f.read()
                parser = ET.XMLParser(encoding="utf-8")
                art = ET.fromstring(xmlb, parser=parser)
                text = _html_to_text(art.find(".//body/Texto").text)
                base_to_pid[base] = _port_id_from_text(text, art.attrib.get("name", ""))
                base_to_hint[base] = _extract_header_hint(text)
            except: continue

        agg = defaultdict(list)
        for n in xml_names:
            m = re.search(r"\d+_\d+_(\d+)", n)
            if not m: continue
            pid = base_to_pid.get(m.group(1), "PORTARIA GM/MPO (Sem ID)")
            try:
                with z.open(n) as f:
                    rows = _parse_totals_rows(f.read(), mb_ugs)
                    if rows: agg[pid].extend(rows)
            except: continue

        pid_to_hint = {pid: base_to_hint.get(base, "") for base, pid in base_to_pid.items() if pid in agg}
        return agg, pid_to_hint

def render_whatsapp_block(pid: str, hint: str, rows: List[Dict]) -> str:
    """
    Gera APENAS o corpo da análise (valores e UGs).
    O cabeçalho (Órgão, Título, Ementa) será gerado pelo loop padrão do robô.
    """
    sup = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
    canc = [r for r in rows if r["kind"] == "CANCELAMENTO"]

    wa = []
    # Nota: Não adicionamos mais o Título/Hint aqui.
    
    wa.append("Análise contábil automática das UGs da Marinha:")

    if sup:
        total = sum(r["valor"] for r in sup)
        wa.append(f"🟢 *Suplementação:* {_brl(total)}")
        acc = defaultdict(float)
        for r in sup: acc[r["UG"]] += r["valor"]
        for ug in sorted(acc.keys()):
            wa.append(f"   └ UG {ug}: {_brl(acc[ug])}")

    if canc:
        total = sum(r["valor"] for r in canc)
        wa.append(f"🔴 *Cancelamento:* {_brl(total)}")
        acc = defaultdict(float)
        for r in canc: acc[r["UG"]] += r["valor"]
        for ug in sorted(acc.keys()):
            wa.append(f"   └ UG {ug}: {_brl(acc[ug])}")

    net = sum(r["valor"] for r in sup) - sum(r["valor"] for r in canc)
    
    saldo_str = _brl(net)
    icon = "⚪"
    if net > 0: icon = "✅"
    elif net < 0: icon = "🔻"
    
    wa.append(f"\n{icon} *Resultado Líquido: {saldo_str}*")
    
    return "\n".join(wa)
