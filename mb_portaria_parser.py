# -*- coding: utf-8 -*-
"""
Parser para Portarias GM/MPO (DOU) com foco nas UGs da MB.
Versão Adaptada para In-Memory Processing (io.BytesIO).
"""
from __future__ import annotations

import re
import zipfile
import io  # Adicionado para suportar bytes na memória
from xml.etree import ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple, Union

# UGs da Marinha (padrão) - Adicionei as UGs que você citou no prompt anterior
MB_UGS_DEFAULT = {
    "52131", "52133", "52232", "52233", "52931", "52932", "52000"
}

# ----------------------------- helpers ----------------------------- #
def _html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        root = ET.fromstring(f"<root>{html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except Exception:
        return ""

def _extract_header_hint(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r"(Adequa[\s\S]*?alterações\s+posteriores\.)", text, flags=re.I)
    if m: return re.sub(r"\s+", " ", m.group(1)).strip()
    
    # Fallback genérico melhorado
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
        # Tenta parsear limpo, se falhar, ignora erros de caractere
        parser = ET.XMLParser(encoding="utf-8")
        art = ET.fromstring(xml_bytes, parser=parser)
    except:
        return []

    texto = art.find(".//body/Texto")
    if texto is None or texto.text is None:
        return []
    
    html = texto.text
    try:
        # Wrap em root para garantir validade se for fragmento
        root = ET.fromstring(f"<root>{html}</root>")
    except Exception:
        return []

    rows = []
    current_ug = None
    current_kind = None
    mb_ugs = set(mb_ugs)

    for tr in root.findall(".//tr"):
        tr_text = " ".join(x.strip() for x in tr.itertext() if x.strip())

        # Regex flexível para pegar a UG
        m_ug = re.search(r"UNIDADE:?\s*(\d{5})", tr_text)
        if m_ug:
            current_ug = m_ug.group(1)
            # Reseta o tipo ao mudar de UG (segurança)
            current_kind = None 
            continue

        # Detecta o tipo de operação (Suplementação vs Cancelamento)
        if "PROGRAMA DE TRABALHO" in tr_text:
            if re.search(r"cancelamento", tr_text, flags=re.I):
                current_kind = "CANCELAMENTO"
            elif re.search(r"suplementa", tr_text, flags=re.I):
                current_kind = "SUPLEMENTACAO"
            # Se não achar nada explícito, mantém o anterior ou None
            continue

        # Captura linha de TOTAL
        if re.search(r"TOTAL\s*-\s*GERAL", tr_text, flags=re.I):
            # Regex para pegar dinheiro no fim da string (ex: 1.000,00)
            m_val = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\s*$", tr_text)
            if not m_val or not current_ug or not current_kind:
                continue
            try:
                val_str = m_val.group(1).replace(".", "").replace(",", ".")
                val = float(val_str)
            except ValueError:
                continue
            
            if current_ug in mb_ugs:
                rows.append({"UG": current_ug, "kind": current_kind, "valor": val})

    return rows

def _brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"

# ----------------------------- API pública ----------------------------- #

def parse_zip_in_memory(zip_file_obj: Union[str, io.BytesIO], mb_ugs: Iterable[str] = None):
    """
    Versão modificada para aceitar BytesIO (memória) ou path (str).
    """
    if mb_ugs is None:
        mb_ugs = MB_UGS_DEFAULT

    # Abre o ZIP (seja arquivo ou bytes)
    try:
        z = zipfile.ZipFile(zip_file_obj, "r")
    except zipfile.BadZipFile:
        return {}, {}

    with z:
        xml_names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        groups = _group_files_by_base(xml_names)

        base_to_pid: Dict[str, str] = {}
        base_to_hint: Dict[str, str] = {}

        # 1. Identifica portaria e hint no arquivo de menor sufixo (cabeçalho)
        for base, items in groups.items():
            header_name = items[0][1]
            try:
                with z.open(header_name) as f:
                    xmlb = f.read()
                
                # Parse leniente
                parser = ET.XMLParser(encoding="utf-8")
                art = ET.fromstring(xmlb, parser=parser)
                
                name_attr = art.attrib.get("name", "")
                texto = art.find(".//body/Texto")
                html = texto.text if texto is not None else ""
                text = _html_to_text(html)

                pid = _port_id_from_text(text, name_attr)
                hint = _extract_header_hint(text)

                base_to_pid[base] = pid
                base_to_hint[base] = hint
            except Exception:
                continue

        # 2. Agrega linhas MB por portaria (varre todos os arquivos do ZIP)
        agg = defaultdict(list)
        for n in xml_names:
            m = re.search(r"\d+_\d+_(\d+)", n)
            if not m:
                continue
            base = m.group(1)
            pid = base_to_pid.get(base, "PORTARIA GM/MPO (Sem ID)")

            try:
                with z.open(n) as f:
                    xmlb = f.read()
                rows = _parse_totals_rows(xmlb, mb_ugs)
                if rows:
                    agg[pid].extend(rows)
            except Exception:
                continue

        # 3. Mapeia Hint final
        pid_to_hint: Dict[str, str] = {}
        for base, pid in base_to_pid.items():
            # Só guarda o hint se a portaria tiver dados relevantes ou se quisermos todas
            if pid in agg: 
                pid_to_hint[pid] = base_to_hint.get(base, "")

        return agg, pid_to_hint

def render_whatsapp_block(pid: str, hint: str, rows: List[Dict]) -> str:
    """Gera o texto formatado para UMA portaria."""
    sup = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
    canc = [r for r in rows if r["kind"] == "CANCELAMENTO"]

    wa = []
    # wa.append("▶️ Ministério do Planejamento e Orçamento") # Removido para economizar espaço
    wa.append(f"📌 *{pid}*")
    wa.append(f"_{hint}_")
    wa.append("")

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
