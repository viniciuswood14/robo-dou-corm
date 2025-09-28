# -*- coding: utf-8 -*-
"""
Parser para Portarias GM/MPO (DOU) com foco nas UGs da MB.

O que faz:
- Agrupa fragmentos XML pelo "id base" do IN (ex.: 23138645) e mapeia para a portaria (nº/ano).
- Extrai o "hint" (frase do cabeçalho: "Abre...", "Adequa...", etc.).
- Considera **apenas** linhas "TOTAL - GERAL" das UGs de interesse.
- Consolida Suplementação / Cancelamento por UG e calcula o saldo líquido por Portaria.
- Renderiza texto no padrão WhatsApp e também retorna um payload JSON estruturado.

Só usa a stdlib (zipfile, re, xml.etree). Nenhuma lib extra.
"""
from __future__ import annotations

import re
import zipfile
from xml.etree import ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

# UGs da Marinha (padrão)
MB_UGS_DEFAULT = {"52131", "52133", "52232", "52233", "52931", "52932"}


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

    # 1) Padrões mais comuns e "fechados" (MPO)
    m = re.search(
        r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)",
        text, flags=re.I
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    m = re.search(
        r"(Adequa[\s\S]*?alterações\s+posteriores\.)",
        text, flags=re.I
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    # 2) Outras aberturas comuns (genéricas), mas mantendo frase longa
    for pat in [
        r"(Altera[\s\S]*?\.)",
        r"(Autoriza[\s\S]*?\.)",
        r"(Disp(õ|o)e[\s\S]*?\.)",
        r"(Estabelece[\s\S]*?\.)",
        r"(Fixa[\s\S]*?\.)",
        r"(Prorroga[\s\S]*?\.)",
    ]:
        m = re.search(pat, text, flags=re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()

    # 3) Fallback: pegar a primeira sentença longa antes de "ANEXO I"
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    sentences = re.split(r"(?<=\.)\s+", pre)
    for s in sentences:
        s_norm = re.sub(r"\s+", " ", s).strip()
        if len(s_norm) > 80 and any(x in s_norm.lower() for x in ["orçament", "lme", "limites", "crédito"]):
            return s_norm
    return pre.strip()[:220].rstrip(" ,;")

def _port_id_from_text(text: str, name_attr: str) -> str:
    # Ex.: "PORTARIA GM/MPO Nº 330, DE ... 2025"
    m = re.search(r"PORTARIA\s+GM/?MPO\s+N[ºo]?\s*(\d+).+?DE\s+(20\d{2})", text, flags=re.I)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    # Ex.: "Portaria GM/MPO nA 330.2025"
    m2 = re.search(r"Portaria\s+GM\.?/?MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", text, flags=re.I)
    if m2:
        return f"{m2.group(1)}/{m2.group(2)}"

    m3 = re.search(r"Portaria\s+GM\.?/?MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m3:
        return f"{m3.group(1)}/{m3.group(2)}"

    return "PORTARIA GM/MPO"


def _group_files_by_base(zip_names: Iterable[str]) -> Dict[str, List[Tuple[int, str]]]:
    """
    Agrupa por 'base id' do IN. Nome típico: 515_YYYYMMDD_23138645-1.xml
    """
    groups: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for n in zip_names:
        m = re.search(r"\d+_\d+_(\d+)(?:-(\d+))?\.xml$", n)
        if m:
            base = m.group(1)
            suffix = int(m.group(2) or 0)
            groups[base].append((suffix, n))
    for base in groups:
        groups[base].sort()  # menor sufixo primeiro (cabeçalho)
    return groups


def _parse_totals_rows(xml_bytes: bytes, mb_ugs: Iterable[str]) -> List[Dict]:
    """
    Varre a tabela, captura apenas linhas 'TOTAL - GERAL' da UG corrente e marca tipo:
    SUPLEMENTACAO ou CANCELAMENTO (definido pela linha 'PROGRAMA DE TRABALHO ( ... )').
    """
    art = ET.fromstring(xml_bytes)
    texto = art.find(".//body/Texto")
    if texto is None or texto.text is None:
        return []
    html = texto.text

    try:
        root = ET.fromstring(f"<root>{html}</root>")
    except Exception:
        return []

    rows = []
    current_ug = None
    current_kind = None
    mb_ugs = set(mb_ugs)

    for tr in root.findall(".//tr"):
        tr_text = " ".join(x.strip() for x in tr.itertext() if x.strip())

        m_ug = re.search(r"UNIDADE:\s*(\d{5})", tr_text)
        if m_ug:
            current_ug = m_ug.group(1)
            current_kind = None
            continue

        if "PROGRAMA DE TRABALHO" in tr_text and "(" in tr_text and ")" in tr_text:
            if re.search(r"\(\s*cancelamento\s*\)", tr_text, flags=re.I):
                current_kind = "CANCELAMENTO"
            elif re.search(r"\(\s*suplementa", tr_text, flags=re.I):
                current_kind = "SUPLEMENTACAO"
            else:
                current_kind = None
            continue

        if re.search(r"TOTAL\s*-\s*GERAL", tr_text, flags=re.I):
            m_val = re.search(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d+)$", tr_text)
            if not m_val or not current_ug or not current_kind:
                continue
            try:
                val = float(m_val.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                continue
            if current_ug in mb_ugs:
                rows.append({"UG": current_ug, "kind": current_kind, "valor": val})

    return rows


def _brl(n: float) -> str:
    s = f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s[:-3] if s.endswith(',00') else s}"


# ----------------------------- API pública ----------------------------- #
def parse_zip(zip_path: str, mb_ugs: Iterable[str] = None):
    """
    Lê o ZIP do DOU e retorna:
      - agg: dict { '330/2025': [ {UG, kind, valor}, ... ], ... }
      - pid_to_hint: dict { '330/2025': 'Abre aos Orçamentos ...', ... }
    """
    if mb_ugs is None:
        mb_ugs = MB_UGS_DEFAULT

    with zipfile.ZipFile(zip_path, "r") as z:
        xml_names = [n for n in z.namelist() if n.lower().endswith(".xml")]
        groups = _group_files_by_base(xml_names)

        base_to_pid: Dict[str, str] = {}
        base_to_hint: Dict[str, str] = {}

        # identifica portaria e hint no arquivo de menor sufixo (cabeçalho)
        for base, items in groups.items():
            header_name = items[0][1]
            with z.open(header_name) as f:
                xmlb = f.read()

            art = ET.fromstring(xmlb)
            name_attr = art.attrib.get("name", "")
            texto = art.find(".//body/Texto")
            html = texto.text if texto is not None else ""
            text = _html_to_text(html)

            pid = _port_id_from_text(text, name_attr)
            hint = _extract_header_hint(text)

            base_to_pid[base] = pid
            base_to_hint[base] = hint

        # agrega linhas MB por portaria
        agg = defaultdict(list)
        for n in xml_names:
            m = re.search(r"\d+_\d+_(\d+)", n)
            if not m:
                continue
            base = m.group(1)
            pid = base_to_pid.get(base, "PORTARIA GM/MPO")

            with z.open(n) as f:
                xmlb = f.read()
            rows = _parse_totals_rows(xmlb, mb_ugs)
            if rows:
                agg[pid].extend(rows)

        # converte base_to_hint -> pid_to_hint
        pid_to_hint: Dict[str, str] = {}
        for base, pid in base_to_pid.items():
            pid_to_hint[pid] = base_to_hint.get(base, "")

        return agg, pid_to_hint


def render_whatsapp(agg: Dict[str, List[Dict]], pid_to_hint: Dict[str, str]) -> str:
    blocks = []
    for pid in sorted(agg.keys()):
        rows = agg[pid]
        sup = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
        canc = [r for r in rows if r["kind"] == "CANCELAMENTO"]

        wa = []
        wa.append("🔰 Seção 1:\n")
        wa.append("▶️Ministério do Planejamento e Orçamento/Gabinete da Ministra\n")
        wa.append(f"📌PORTARIA GM/MPO Nº {pid}\n")
        wa.append(f"{pid_to_hint.get(pid, 'Ato orçamentário do MPO.')}\n")
        wa.append("⚓ MB:\n")

        if sup:
            total = sum(r["valor"] for r in sup)
            wa.append(f"Suplementação (total: {_brl(total)})")
            acc = defaultdict(float)
            for r in sup:
                acc[r["UG"]] += r["valor"]
            for ug in sorted(acc.keys()):
                wa.append(f"UG {ug} - {_brl(acc[ug])}")

        if canc:
            if sup:
                wa.append("")
            total = sum(r["valor"] for r in canc)
            wa.append(f"Cancelamento (total: {_brl(total)})")
            acc = defaultdict(float)
            for r in canc:
                acc[r["UG"]] += r["valor"]
            for ug in sorted(acc.keys()):
                wa.append(f"UG {ug} - {_brl(acc[ug])}")

        net = sum(r["valor"] for r in sup) - sum(r["valor"] for r in canc)
        wa.append(f"\n(Suplementação – Cancelamento) = {_brl(net)}\n")
        wa.append("📁Portaria em anexo.\n")

        blocks.append("\n".join(wa))

    return "\n\n".join(blocks)


def parse_zip_and_render(zip_path: str, mb_ugs: Iterable[str] = None):
    agg, pid_to_hint = parse_zip(zip_path, mb_ugs=mb_ugs or MB_UGS_DEFAULT)
    txt = render_whatsapp(agg, pid_to_hint)

    payload = {}
    for pid, rows in agg.items():
        sup = [r for r in rows if r["kind"] == "SUPLEMENTACAO"]
        canc = [r for r in rows if r["kind"] == "CANCELAMENTO"]
        payload[pid] = {
            "hint": pid_to_hint.get(pid, ""),
            "suplementacao_total": sum(r["valor"] for r in sup),
            "cancelamento_total": sum(r["valor"] for r in canc),
            "resultado_liquido": sum(r["valor"] for r in sup) - sum(r["valor"] for r in canc),
            "linhas": rows,
        }

    return txt, payload
