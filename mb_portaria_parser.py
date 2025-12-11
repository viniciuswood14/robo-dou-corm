# -*- coding: utf-8 -*-
"""
Nome do arquivo: mb_portaria_parser.py
Versão: 3.0 (Suporte a Portarias de Limites/Bloqueio e Linhas Diretas de UG)
Descrição: Parser especializado para Portarias orçamentárias (GM, SOF, SE) do MPO.
"""
from __future__ import annotations

import re
import zipfile
import io
from xml.etree import ElementTree as ET
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple, Union

# UGs da Marinha/Defesa (padrão)
MB_UGS_DEFAULT = {
    "52111", # Comando da Marinha (Algumas variações)
    "52131", # Comando da Marinha
    "52133", # Secretaria da Comissão Interministerial para os Recursos do Mar
    "52232", # CCCPM
    "52233", # AMAZUL
    "52931", # Fundo Naval
    "52932", # Fundo de Desenvolvimento do Ensino Profissional Marítimo
    "52000", # Ministério da Defesa (Administração Direta)
    "52121", # Comando do Exército (Opcional, mas às vezes agrupado)
    "52111"  # Comando da Aeronáutica (Opcional)
}

def _sanitize_html_content(html_str: str) -> str:
    if not html_str: return ""
    # Remove atributos de namespace que quebram o ET.fromstring as vezes
    s = re.sub(r'\sxmlns="[^"]+"', '', html_str, count=1)
    s = s.replace("&nbsp;", " ").replace("&quot;", '"').replace("&apos;", "'")
    return s

def _html_to_text(html: str) -> str:
    if not html: return ""
    try:
        clean_html = _sanitize_html_content(html)
        # Envolve em root para garantir XML válido
        root = ET.fromstring(f"<root>{clean_html}</root>")
        txt = " ".join(x.strip() for x in root.itertext() if x.strip())
        return re.sub(r"\s+", " ", txt)
    except Exception:
        # Fallback regex se o XML falhar
        return re.sub(r"<[^>]+>", " ", html).strip()

def _extract_header_hint(text: str) -> str:
    if not text: return ""
    
    # Tentativas de capturar o objetivo da portaria
    patterns = [
        r"(Abre\s+ao?s?\s+Or(ç|c)amentos?[\s\S]*?vigente\.)",
        r"(Adequa[\s\S]*?alterações\s+posteriores\.)",
        r"(Altera\s+parcialmente\s+grupos[\s\S]*?vigente\.)",
        r"(Ajusta\s+os\s+valores\s+constantes[\s\S]*?vigente\.?)",
        r"(Atualiza\s+os\s+valores[\s\S]*?posteriores\.?)"
    ]
    
    for pat in patterns:
        m = re.search(pat, text, flags=re.I)
        if m: return re.sub(r"\s+", " ", m.group(1)).strip()

    # Fallback: pega o texto antes do ANEXO I
    pre = re.split(r"ANEXO\s+I", text, flags=re.I)[0]
    return pre.strip()[:250].rstrip(" ,;") + "..."

def _port_id_from_text(text: str, name_attr: str) -> str:
    # 1. Tenta formato padrão completo no texto
    m = re.search(r"PORTARIA\s+(?:[A-Z]+/?)*MPO\s+N[ºo]?\s*(\d+).+?(20\d{2})", text, flags=re.I)
    if m: return f"{m.group(1)}/{m.group(2)}"
    
    # 2. Tenta pelo atributo 'name' do arquivo XML
    m2 = re.search(r"Portaria\s+(?:[A-Z]+\.?/?)*MPO\s+n\S*\s+(\d+)[\.\-_/](\d{4})", (name_attr or ""), flags=re.I)
    if m2: return f"{m2.group(1)}/{m2.group(2)}"
    
    # 3. Fallback genérico
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
        # Agrupa arquivos divididos (ex: id-1.xml, id-2.xml) ou únicos (id.xml)
        # Regex ajustada para pegar o ID numérico base
        m = re.search(r"(\d+)(?:-(\d+))?\.xml$", n, flags=re.I)
        if m:
            base = m.group(1) # O ID da matéria (ex: 23408456)
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
    current_kind = "OUTROS" # Default seguro
    mb_ugs = set(mb_ugs)

    # Iteramos sobre TODOS os elementos para pegar contexto (<p>) e dados (<tr>)
    for elem in root.iter():
        # Normaliza o texto do elemento
        elem_text = " ".join(x.strip() for x in elem.itertext() if x.strip()).upper()
        
        # 1. Detecção de Contexto (Cabeçalhos fora da tabela)
        # Prioridade para palavras-chave que indicam o sentido do valor
        if "REDUÇÃO" in elem_text or "CANCELAMENTO" in elem_text or "BLOQUEIO" in elem_text:
            current_kind = "CANCELAMENTO" # Bloqueio tratado como redução para alerta visual
        elif "ACRÉSCIMO" in elem_text or "SUPLEMENTA" in elem_text or "AMPLIAÇÃO" in elem_text:
            current_kind = "SUPLEMENTACAO"
        
        # Se for uma linha de tabela, processa os dados
        if elem.tag == 'tr':
            tr_text = " ".join(x.strip() for x in elem.itertext() if x.strip())
            tr_text_upper = tr_text.upper()

            # Lógica A: UG no cabeçalho (Formato Crédito Suplementar Detalhado)
            m_ug_header = re.search(r"UNIDADE:?\s*(\d{5})", tr_text, re.I)
            if m_ug_header:
                current_ug = m_ug_header.group(1)
                # O kind geralmente já foi definido pelo contexto anterior
                continue

            # Identifica se é linha de cabeçalho interno de anexo para reforçar o contexto
            if "ANEXO" in tr_text_upper and "REDUÇÃO" in tr_text_upper: current_kind = "CANCELAMENTO"
            if "ANEXO" in tr_text_upper and "AMPLIAÇÃO" in tr_text_upper: current_kind = "SUPLEMENTACAO"

            # Tentativa de extração de valor
            ug_to_use = current_ug
            
            # Lógica B: UG na própria linha (Formato Limites/Portaria 495)
            # Procura por "52000" no início da linha
            m_ug_inline = re.search(r"^(\d{5})\b", tr_text.strip())
            if m_ug_inline:
                ug_candidate = m_ug_inline.group(1)
                if ug_candidate in mb_ugs:
                    ug_to_use = ug_candidate
            
            # Se temos uma UG alvo identificada (seja pelo header ou inline)
            if ug_to_use in mb_ugs:
                # Regex para pegar valores monetários (ex: 1.181.099,00 ou 1.181.099)
                # Pega todos os valores da linha
                matches = re.findall(r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)", tr_text)
                
                if matches:
                    # Normalmente o valor relevante é o último (Total) ou o único da linha
                    val_str = matches[-1].replace(".", "").replace(",", ".")
                    try:
                        val = float(val_str)
                        
                        # Filtros de ruído
                        is_year = "2024" in tr_text or "2025" in tr_text
                        # Se o valor for igual ao ano (ex: 2025.0), ignora, a menos que seja muito grande
                        if is_year and val == 2025.0: continue
                        
                        if val > 0:
                            rows.append({
                                "UG": ug_to_use,
                                "kind": current_kind,
                                "valor": val
                            })
                            # Reseta UG inline para não poluir próximas linhas
                            if m_ug_inline: current_ug = None
                            
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
            # Tenta ler o header do primeiro arquivo do grupo
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
                
                cat = art.attrib.get("artCategory", "").upper()
                pid = _port_id_from_text(combined_text, art.attrib.get("name", ""))
                
                # Critérios de Aceite
                has_valid_id = pid != "PORTARIA MPO (ID n/d)"
                is_mpo_doc = "PLANEJAMENTO" in cat or "MPO" in cat or "PLANEJAMENTO" in full_text.upper() or "MPO" in full_text.upper()
                
                if has_valid_id or is_mpo_doc:
                    base_to_pid[base] = pid
                    base_to_hint[base] = _extract_header_hint(full_text)
                    
            except Exception as e:
                print(f"[DEBUG MPO] Erro ao ler header {header_name}: {e}")
                continue

        # Processa TODOS os arquivos XML (partes da mesma matéria)
        for n in xml_names:
            # Encontra a base deste arquivo
            m = re.search(r"(\d+)(?:-(\d+))?\.xml$", n)
            if not m: continue
            
            base = m.group(1)
            if base in base_to_pid:
                pid = base_to_pid[base]
                try:
                    with z.open(n) as f:
                        # Extrai linhas de dados deste arquivo
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

    # Agrupa valores por UG (soma se houver múltiplas linhas para a mesma UG)
    def aggregate_per_ug(row_list):
        ug_sum = defaultdict(float)
        for r in row_list:
            ug_sum[r["UG"]] += r["valor"]
        return ug_sum

    sup_agg = aggregate_per_ug(sup_rows)
    canc_agg = aggregate_per_ug(canc_rows)

    wa = []
    wa.append(f"🔎 *Análise Contábil Automática ({pid})*")
    wa.append(f"_{hint}_")
    wa.append("")

    if sup_agg:
        total_sup = sum(sup_agg.values())
        wa.append(f"🟢 *Ampliação/Suplementação:* {_brl(total_sup)}")
        for ug, val in sup_agg.items():
            nome_ug = ""
            if ug == "52131": nome_ug = "- CM"
            elif ug == "52931": nome_ug = "- FuN"
            elif ug == "52233": nome_ug = "- AMAZUL"
            elif ug == "52000": nome_ug = "- MD"
            wa.append(f"   └ UG {ug} {nome_ug}: {_brl(val)}")
    
    if canc_agg:
        if sup_agg: wa.append("") 
        total_canc = sum(canc_agg.values())
        # Usa termo genérico para cobrir Bloqueio e Redução
        wa.append(f"🔴 *Redução/Bloqueio:* {_brl(total_canc)}")
        for ug, val in canc_agg.items():
            nome_ug = ""
            if ug == "52000": nome_ug = "- MD"
            wa.append(f"   └ UG {ug} {nome_ug}: {_brl(val)}")

    total_sup = sum(sup_agg.values())
    total_canc = sum(canc_agg.values())
    net = total_sup - total_canc
    
    wa.append("")
    if net > 0:
        wa.append(f"💰 *Saldo Líquido Positivo:* {_brl(net)}")
    elif net < 0:
        wa.append(f"🔻 *Saldo Líquido Negativo:* {_brl(net)}")
    else:
        wa.append(f"⚪ *Remanejamento sem alteração de valor global.*")
    
    return "\n".join(wa)
