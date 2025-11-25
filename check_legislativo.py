# Nome do arquivo: check_legislativo.py
# Versão: 7.0 (Debug Mode + Browser Headers)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Importação do Telegram (Mock se falhar)
try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

# KEYWORDS (Minúsculo para match insensível a case)
# Adicionamos termos específicos de orçamento para garantir o PLN
KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino", "nuclear", 
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial", "militar", "autoridade marítima", "emgepron",
    "cisb", "ctmsp", "amazul", "nuclep", "orçamento", "crédito", 
    "pln", "suplementar", "especial", "extraordinário", "fiscal",
    "aeronáutica", "exército"
]

# Siglas para varrer no Senado
SENADO_SIGLAS = ["PLN", "PL", "PEC", "PDL", "PLP"]

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

def load_state() -> Set[str]:
    if not os.path.exists(STATE_FILE_PATH):
        return set()
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except:
        return set()

def save_state(processed_ids: Set[str]):
    try:
        dirname = os.path.dirname(STATE_FILE_PATH)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(processed_ids), f)
    except Exception as e:
        print(f"Erro ao salvar estado legislativo: {e}")

# --- CONSULTA CÂMARA (Mantida, pois funciona) ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Consultando...")
    results = []
    # Headers de navegador para evitar bloqueio
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    for kw in KEYWORDS:
        if len(kw) < 4 and kw != "pln": continue 
        
        params = {"dataInicio": start_date_iso, "ordem": "DESC", "ordenarPor": "id", "keywords": kw, "itens": 15}
        try:
            resp = await client.get(URL_CAMARA, params=params, headers=headers, timeout=10)
            if resp.status_code == 200:
                dados = resp.json().get("dados", [])
                for item in dados:
                    results.append({
                        "uid": f"CAM_{item['id']}", "casa": "Câmara",
                        "tipo": item['siglaTipo'], "numero": str(item['numero']),
                        "ano": str(item['ano']), "ementa": item['ementa'],
                        "link": f"https://www.camara.leg.br/propostas-legislativas/{item['id']}",
                        "keyword": kw
                    })
        except Exception as e:
            print(f"Erro API Câmara ({kw}): {e}")
            
    return results

# --- CONSULTA SENADO (MODO DIAGNÓSTICO) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando varredura PROFUNDA (2025)...")
    results = []
    
    # Headers simulam navegador real (CRÍTICO para o Senado)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    # Data limite calculada no Python (não enviada para API para evitar erro de filtro lá)
    limit_date = datetime.now() - timedelta(days=days_back_int + 1) # +1 margem
    ano_atual = datetime.now().year 

    for sigla in SENADO_SIGLAS:
        # Busca TUDO do ano para esta sigla
        url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
        
        try:
            # Timeout maior pois a lista pode ser grande
            resp = await client.get(url, headers=headers, timeout=30)
            
            # [DEBUG] Imprime status para sabermos se conectou
            # print(f"[DEBUG SENADO] {sigla}: HTTP {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                
                # Tratamento de erros na estrutura JSON do Senado
                pesquisa = data.get("PesquisaBasicaMateria")
                if not pesquisa: continue # JSON vazio ou erro
                
                container = pesquisa.get("Materias")
                if not container: continue # Sem matérias no ano (raro)
                
                lista = container.get("Materia", [])
                # Se só tiver 1 item, a API retorna Dict, não List. Normalizamos:
                if isinstance(lista, dict): lista = [lista]
                
                # print(f"[DEBUG SENADO] {sigla}: Encontradas {len(lista)} matérias no total. Filtrando...")

                for mat in lista:
                    dados = mat.get("DadosBasicosMateria", {})
                    
                    # 1. Filtro de Data (Local)
                    data_str = dados.get("DataApresentacao") # Ex: 2025-11-24
                    if not data_str: continue
                    
                    try:
                        dt_obj = datetime.strptime(str(data_str)[:10], "%Y-%m-%d")
                        # Se for mais antigo que o limite, ignora
                        if dt_obj < limit_date:
                            continue 
                    except: continue

                    # 2. Filtro de Texto (Ementa)
                    ementa = dados.get("EmentaMateria", "")
                    explicacao = dados.get("ExplicacaoEmentaMateria", "")
                    natureza = dados.get("NaturezaMateria", "") # Ex: "Crédito"
                    
                    full_text = (ementa + " " + explicacao + " " + natureza).lower()
                    
                    # Match das palavras-chave
                    found_kw = None
                    for kw in KEYWORDS:
                        if kw.lower() in full_text:
                            found_kw = kw
                            break
                    
                    if found_kw:
                        print(f"   -> [ACHOU!] {sigla} {dados.get('NumeroMateria')} - {found_kw}")
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
            
            elif resp.status_code == 403:
                print(f"⚠️ ERRO 403: Senado bloqueou o robô. Tentando header alternativo na próxima.")
            else:
                print(f"⚠️ ERRO SENADO {sigla}: {resp.status_code} - {resp.text[:100]}")

            await asyncio.sleep(0.5)
            
        except Exception as e:
            print(f"Erro Crítico API Senado ({sigla}): {e}")

    return results

# --- FUNÇÃO PRINCIPAL ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    print(f"--- Iniciando Robô Legislativo v7 (Headers Browser) ---")
    processed_ids = load_state()
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    all_proposals = []
    new_for_telegram = []
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # Senado primeiro para testar o fix
        res_senado = await check_senado(client, days_back)
        res_camara = await check_camara(client, start_date_iso)
        
        full_list = res_senado + res_camara
        seen = set()
        
        for p in full_list:
            if p['uid'] in seen: continue
            seen.add(p['uid'])
            all_proposals.append(p)
            
            if p['uid'] not in processed_ids:
                new_for_telegram.append(p)
                processed_ids.add(p['uid'])

    save_state(processed_ids)

    if only_new:
        if not new_for_telegram:
            return []
        
        # Monta mensagem
        msg = [f"🏛️ *Monitoramento Legislativo - Novidades*"]
        for p in new_for_telegram:
            icon = "🟢" if p['casa'] == "Câmara" else "🔵"
            # Ementa limpa
            ementa_clean = (p['ementa'] or "Sem descrição").replace("\n", " ").strip()[:280]
            if len(ementa_clean) == 280: ementa_clean += "..."

            msg.append(f"\n{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
            msg.append(f"🔎 _Match: {p['keyword']}_")
            msg.append(f"📝 {ementa_clean}")
            msg.append(f"🔗 [Link Oficial]({p['link']})")
            msg.append("⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯")

        final_text = "\n".join(msg)
        if len(final_text) > 4000: final_text = final_text[:4000] + "\n(cortado)"
        
        await send_telegram_message(final_text)
        return new_for_telegram

    return all_proposals
