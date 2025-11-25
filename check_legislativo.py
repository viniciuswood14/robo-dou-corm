# Nome do arquivo: check_legislativo.py
# Versão: 8.0 (Diagnóstico de Estrutura JSON + Teste PLN 32)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Mock do Telegram se falhar import
try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino", "nuclear", 
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial", "militar", "autoridade marítima", "emgepron",
    "cisb", "ctmsp", "amazul", "nuclep", "orçamento", "crédito", 
    "pln", "suplementar", "especial", "extraordinário", "fiscal",
    "aeronáutica", "exército"
]

# Adicionei PLN no início para prioridade
SENADO_SIGLAS = ["PLN", "PL", "PEC", "PDL"]

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

def load_state() -> Set[str]:
    if not os.path.exists(STATE_FILE_PATH): return set()
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f: return set(json.load(f))
    except: return set()

def save_state(processed_ids: Set[str]):
    try:
        dirname = os.path.dirname(STATE_FILE_PATH)
        if dirname and not os.path.exists(dirname): os.makedirs(dirname, exist_ok=True)
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(list(processed_ids), f)
    except Exception as e: print(f"Erro state: {e}")

# --- CONSULTA CÂMARA ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Consultando...")
    results = []
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json"}
    
    for kw in KEYWORDS:
        if len(kw) < 4 and kw != "pln": continue 
        try:
            resp = await client.get(URL_CAMARA, params={"dataInicio": start_date_iso, "ordem": "DESC", "ordenarPor": "id", "keywords": kw, "itens": 10}, headers=headers, timeout=10)
            if resp.status_code == 200:
                for item in resp.json().get("dados", []):
                    results.append({
                        "uid": f"CAM_{item['id']}", "casa": "Câmara",
                        "tipo": item['siglaTipo'], "numero": str(item['numero']),
                        "ano": str(item['ano']), "ementa": item['ementa'],
                        "link": f"https://www.camara.leg.br/propostas-legislativas/{item['id']}",
                        "keyword": kw
                    })
        except: pass
    return results

# --- CONSULTA SENADO (DEBUG MODE) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando varredura (v8 Debug)...")
    results = []
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36", "Accept": "application/json"}
    ano_atual = datetime.now().year 
    
    # 1. TESTE DE CONEXÃO ESPECÍFICO (PLN 32/2025)
    # Isso verifica se o problema é a lista geral ou a conexão
    try:
        print(f"   -> Testando acesso direto ao PLN 32/2025...")
        url_test = f"{URL_SENADO}?sigla=PLN&numero=32&ano=2025"
        resp_test = await client.get(url_test, headers=headers, timeout=15)
        if resp_test.status_code == 200:
            d_test = resp_test.json()
            # Verifica se retornou algo na busca direta
            mats = d_test.get("PesquisaBasicaMateria", {}).get("Materias", {}).get("Materia", [])
            if mats:
                print(f"   -> [SUCESSO] PLN 32/2025 acessível via busca direta!")
            else:
                print(f"   -> [ALERTA] Busca direta PLN 32 retornou 200 OK mas lista vazia. JSON Raw keys: {d_test.keys()}")
        else:
            print(f"   -> [ERRO] Falha no teste PLN 32: HTTP {resp_test.status_code}")
    except Exception as e:
        print(f"   -> [ERRO CRÍTICO] Exceção no teste direto: {e}")

    # 2. VARREDURA GERAL
    for sigla in SENADO_SIGLAS:
        url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
        try:
            resp = await client.get(url, headers=headers, timeout=30)
            print(f"   -> [{sigla}] HTTP {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                
                # DEBUG DA ESTRUTURA
                pesquisa = data.get("PesquisaBasicaMateria")
                if not pesquisa:
                    print(f"      [AVISO] 'PesquisaBasicaMateria' não encontrada. Chaves raiz: {list(data.keys())}")
                    continue
                
                container = pesquisa.get("Materias")
                if not container:
                    print(f"      [AVISO] 'Materias' não encontrada dentro de PesquisaBasicaMateria.")
                    continue
                
                lista = container.get("Materia", [])
                if isinstance(lista, dict): lista = [lista]
                
                print(f"      -> Recebidos {len(lista)} itens de {sigla}. Filtrando...")

                count_found = 0
                for mat in lista:
                    dados = mat.get("DadosBasicosMateria", {})
                    ementa = dados.get("EmentaMateria", "")
                    texto_completo = (ementa + " " + dados.get("NaturezaMateria", "")).lower()
                    
                    # Verifica Keywords
                    found_kw = None
                    for kw in KEYWORDS:
                        if kw.lower() in texto_completo:
                            found_kw = kw
                            break
                    
                    if found_kw:
                        count_found += 1
                        results.append({
                            "uid": f"SEN_{dados.get('CodigoMateria')}",
                            "casa": "Senado",
                            "tipo": dados.get("SiglaMateria"),
                            "numero": dados.get("NumeroMateria"),
                            "ano": dados.get("AnoMateria"),
                            "ementa": ementa,
                            "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{dados.get('CodigoMateria')}",
                            "keyword": found_kw.upper()
                        })
                print(f"      -> Filtrados: {count_found} relevantes.")
            
            elif resp.status_code == 403:
                print(f"⚠️ ERRO 403 (Bloqueio) para {sigla}")
            
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"Erro API Senado ({sigla}): {e}")

    return results

# --- MAIN LOOP ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    print(f"--- Iniciando Robô Legislativo v8 (Debug) ---")
    processed_ids = load_state()
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    new_for_telegram = []
    all_proposals = []
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        res_senado = await check_senado(client, days_back)
        res_camara = await check_camara(client, start_date_iso)
        
        full = res_senado + res_camara
        seen = set()
        for p in full:
            if p['uid'] in seen: continue
            seen.add(p['uid'])
            all_proposals.append(p)
            if p['uid'] not in processed_ids:
                new_for_telegram.append(p)
                processed_ids.add(p['uid'])

    save_state(processed_ids)

    if only_new:
        if not new_for_telegram: return []
        msg = [f"🏛️ *Monitoramento Legislativo - Novidades*"]
        for p in new_for_telegram:
            ementa = (p['ementa'] or "")[:200]
            msg.append(f"\n{p['casa']} | {p['tipo']} {p['numero']}/{p['ano']}\n🔎 {p['keyword']}\n📝 {ementa}\n🔗 [Link]({p['link']})")
            msg.append("---")
        await send_telegram_message("\n".join(msg))
        return new_for_telegram

    return all_proposals
