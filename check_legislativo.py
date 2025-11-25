# Nome do arquivo: check_legislativo.py
# Versão: 9.0 (Forensic Mode - Depuração do PLN 32)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

# Lista de palavras-chave (mantida robusta)
KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino", "nuclear", 
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial", "militar", "autoridade marítima", "emgepron",
    "cisb", "ctmsp", "amazul", "nuclep", "orçamento", "crédito", 
    "pln", "suplementar", "especial", "extraordinário", "fiscal"
]

SENADO_SIGLAS = ["PLN", "PL", "PEC"] # Reduzi para focar no problema

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

def load_state() -> Set[str]:
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f: return set(json.load(f))
    except: return set()

def save_state(processed_ids: Set[str]):
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(list(processed_ids), f)
    except: pass

# --- CÂMARA (Standard) ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    return [] # Desativado temporariamente para limpar o log e focar no Senado

# --- SENADO (FORENSIC MODE) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando varredura FORENSE...")
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json"}
    ano_atual = datetime.now().year
    limit_date = datetime.now() - timedelta(days=days_back_int + 5) # Aumentei a margem para garantir

    for sigla in SENADO_SIGLAS:
        url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
        try:
            resp = await client.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"   -> Erro {resp.status_code} em {sigla}")
                continue

            data = resp.json()
            raw_list = data.get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
            if isinstance(raw_list, dict): raw_list = [raw_list]
            
            print(f"   -> {sigla}: Analisando {len(raw_list)} itens...")

            for mat in raw_list:
                dados = mat.get("DadosBasicosMateria", {})
                numero = str(dados.get("NumeroMateria", "?"))
                
                # --- INSPEÇÃO DO PLN 32 ---
                # Se encontrar o PLN 32, imprime TUDO sobre ele, independente de filtro
                if sigla == "PLN" and numero == "32":
                    print("\n   ================ ALVO ENCONTRADO: PLN 32 ================")
                    print(f"   1. Data Bruta: '{dados.get('DataApresentacao')}'")
                    print(f"   2. Ementa: '{dados.get('EmentaMateria')}'")
                    print(f"   3. Natureza: '{dados.get('NaturezaMateria')}'")
                    print(f"   4. Explicação: '{dados.get('ExplicacaoEmentaMateria')}'")
                    
                    # Teste de Data
                    try:
                        dt_test = datetime.strptime(str(dados.get("DataApresentacao"))[:10], "%Y-%m-%d")
                        print(f"   5. Data Parseada: {dt_test} (Limite: {limit_date}) -> {'APROVADA' if dt_test >= limit_date else 'REPROVADA POR DATA'}")
                    except Exception as e:
                        print(f"   5. Erro Parse Data: {e}")

                    # Teste de Keyword
                    full_text_debug = (str(dados.get('EmentaMateria')) + " " + str(dados.get('NaturezaMateria'))).lower()
                    matches = [k for k in KEYWORDS if k in full_text_debug]
                    print(f"   6. Keywords Encontradas: {matches}")
                    print("   =========================================================\n")
                # --------------------------

                # Lógica Normal de Filtro
                data_str = dados.get("DataApresentacao")
                if not data_str: continue
                
                try:
                    dt_obj = datetime.strptime(str(data_str)[:10], "%Y-%m-%d")
                    if dt_obj < limit_date: continue
                except: continue

                ementa = dados.get("EmentaMateria", "")
                natureza = dados.get("NaturezaMateria", "")
                # Concatenamos tudo para garantir o match
                full_text = (str(ementa) + " " + str(natureza)).lower()
                
                found_kw = None
                for kw in KEYWORDS:
                    if kw in full_text:
                        found_kw = kw
                        break
                
                if found_kw:
                    # print(f"      -> Match: {sigla} {numero}")
                    results.append({
                        "uid": f"SEN_{dados.get('CodigoMateria')}",
                        "casa": "Senado",
                        "tipo": dados.get("SiglaMateria"),
                        "numero": numero,
                        "ano": dados.get("AnoMateria"),
                        "ementa": ementa,
                        "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{dados.get('CodigoMateria')}",
                        "keyword": found_kw.upper()
                    })

        except Exception as e:
            print(f"Erro {sigla}: {e}")

    return results

# --- MAIN ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    processed_ids = load_state()
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # Só senado agora
        res_senado = await check_senado(client, days_back)
        
        all_proposals = res_senado
        new_for_telegram = []
        
        for p in all_proposals:
            if p['uid'] not in processed_ids:
                new_for_telegram.append(p)
                processed_ids.add(p['uid'])

    save_state(processed_ids)

    if only_new:
        if new_for_telegram:
            msg = [f"🏛️ *Monitoramento Senado (Teste)*"]
            for p in new_for_telegram:
                msg.append(f"\n{p['tipo']} {p['numero']}/{p['ano']}\n🔎 {p['keyword']}\n🔗 [Link]({p['link']})")
            await send_telegram_message("\n".join(msg))
        return new_for_telegram

    return all_proposals
