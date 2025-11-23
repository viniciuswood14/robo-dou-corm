# Nome do arquivo: check_legislativo.py
# Módulo para monitorar Projetos de Lei via APIs Oficiais (Câmara e Senado)
# Versão: 2.1 (Com filtro de data dinâmico)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Tenta importar o enviador do Telegram
try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

# Palavras-chave Estratégicas
KEYWORDS = [
    "Marinha do Brasil", "Forças Armadas", "Defesa Nacional", "Submarino", 
    "Nuclear", "Amazônia Azul", "PROSUB", "Classe Tamandaré", "Fundo Naval", 
    "Base Industrial de Defesa", "Carreira Militar", "ministério da defesa",
    "autoridade marítima", "comando da marinha", "fundo naval",
    "amazônia azul tecnologias de defesa", "empresa gerencial de projetos navais",
    "programa nuclear da marinha", "Defesa Marítima", "fragata", "Pensões Militares"
]

# URLs Oficiais
URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

def load_state() -> Set[str]:
    if not os.path.exists(STATE_FILE_PATH): return set()
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except: return set()

def save_state(processed_ids: Set[str]):
    try:
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(processed_ids), f)
    except Exception as e:
        print(f"Erro ao salvar estado legislativo: {e}")

# --- CÂMARA (Aceita filtro de data na API) ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Iniciando consulta desde {start_date_iso}...")
    results = []
    
    for kw in KEYWORDS:
        params = {
            "dataInicio": start_date_iso,
            "ordem": "DESC",
            "ordenarPor": "id",
            "keywords": kw,
            "itens": 10 
        }
        try:
            headers = {"User-Agent": "RoboLegislativoMB/1.0"}
            resp = await client.get(URL_CAMARA, params=params, headers=headers)
            
            if resp.status_code == 200:
                dados = resp.json().get("dados", [])
                for item in dados:
                    results.append({
                        "uid": f"CAM_{item['id']}",
                        "casa": "Câmara",
                        "tipo": item['siglaTipo'],
                        "numero": str(item['numero']),
                        "ano": str(item['ano']),
                        "ementa": item['ementa'],
                        "link": f"https://www.camara.leg.br/propostas-legislativas/{item['id']}",
                        "keyword": kw
                    })
            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Erro API Câmara ({kw}): {e}")
            
    return results

# --- SENADO (Filtro de data manual no Python) ---
async def check_senado(client: httpx.AsyncClient, days_back: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando consulta ({days_back} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    limit_date = datetime.now() - timedelta(days=days_back)

    for kw in KEYWORDS:
        url = f"{URL_SENADO}?palavraChave={kw}"
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                pesquisa = data.get("PesquisaBasicaMateria", {})
                if not pesquisa: continue
                
                materias_container = pesquisa.get("Materias", {})
                if not materias_container: continue
                
                lista = materias_container.get("Materia", [])
                if isinstance(lista, dict): lista = [lista] 
                
                for mat in lista:
                    dados = mat.get("DadosBasicosMateria", {})
                    data_apres = dados.get("DataApresentacao", "")[:10] # YYYY-MM-DD
                    
                    if data_apres:
                        try:
                            dt_obj = datetime.strptime(data_apres, "%Y-%m-%d")
                            # AQUI APLICA O FILTRO DE DIAS
                            if dt_obj >= limit_date:
                                results.append({
                                    "uid": f"SEN_{dados.get('CodigoMateria')}",
                                    "casa": "Senado",
                                    "tipo": dados.get("SiglaMateria"),
                                    "numero": dados.get("NumeroMateria"),
                                    "ano": dados.get("AnoMateria"),
                                    "ementa": dados.get("EmentaMateria"),
                                    "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{dados.get('CodigoMateria')}",
                                    "keyword": kw
                                })
                        except: pass
            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Erro API Senado ({kw}): {e}")

    return results

# --- FUNÇÃO PRINCIPAL ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    """
    :param only_new: True = Modo Robô (apenas inéditas, envia Telegram). 
                     False = Modo Site (tudo na janela de tempo, retorna lista).
    :param days_back: Quantos dias olhar para trás.
    """
    print(f"--- Robô Legislativo: {days_back} dias (Modo: {'Robô' if only_new else 'Site'}) ---")
    
    processed_ids = load_state()
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    encontradas = []
    telegram_list = []
    
    async with httpx.AsyncClient(timeout=40) as client:
        res_camara = await check_camara(client, start_date_iso)
        res_senado = await check_senado(client, days_back)
        
        todas = res_camara + res_senado
        seen_now = set()
        
        for p in todas:
            if p['uid'] in seen_now: continue
            seen_now.add(p['uid'])
            
            # Adiciona à lista de retorno (Site vê tudo)
            encontradas.append(p)
            
            # Verifica se é inédita para o Telegram
            if p['uid'] not in processed_ids:
                telegram_list.append(p)
                processed_ids.add(p['uid'])

    # Salva o estado para não repetir alertas no futuro
    # (Mesmo que visualizado no site, marcamos como visto para o robô não apitar depois)
    if telegram_list or (not only_new and encontradas):
        save_state(processed_ids)

    # MODO SITE: Retorna tudo
    if not only_new:
        return encontradas

    # MODO ROBÔ: Envia Telegram apenas das novas
    if not telegram_list:
        print("--- Nenhuma novidade legislativa para o Telegram. ---")
        return []

    msg = [f"🏛️ *Monitoramento Legislativo - Novas Proposições*\n"]
    for p in telegram_list:
        icon = "🟢" if p['casa'] == "Câmara" else "🔵"
        ementa = p['ementa'][:200] + "..." if len(p['ementa']) > 200 else p['ementa']
        msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
        msg.append(f"🔎 _{p['keyword']}_")
        msg.append(f"📝 {ementa}")
        msg.append(f"🔗 [Link]({p['link']})")
        msg.append("---")

    final_text = "\n".join(msg)
    if len(final_text) > 4000: final_text = final_text[:4000] + "\n(Cortado...)"
    
    await send_telegram_message(final_text)
    return telegram_list
```json
