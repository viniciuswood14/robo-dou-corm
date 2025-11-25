# Nome do arquivo: check_legislativo.py
# Versão: 6.0 (Correção API Senado - Busca por Sigla + Filtro Local)

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

# Palavras-chave Estratégicas (Filtro Textual)
KEYWORDS = [
    "marinha", "forças armadas", "defesa", "submarino", "nuclear", 
    "amazônia azul", "prosub", "tamandaré", "fundo naval", 
    "base industrial", "militar", "autoridade marítima", "emgepron",
    "cisb", "ctmsp", "amazul", "nuclep", "orçamento", "crédito", 
    "pln", "suplementar", "especial", "extraordinário", "fiscal"
]

# Tipos de matérias para monitorar no Senado (Siglas Oficiais)
# PLN = Projeto de Lei do Congresso (Orçamento)
# PL = Projeto de Lei
# PEC = Proposta de Emenda à Constituição
# PDL = Projeto de Decreto Legislativo
# PRS = Projeto de Resolução do Senado
# REQ = Requerimento (pode ser muito volumoso, usar com cautela)
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

# --- CONSULTA CÂMARA (Mantida igual, pois funciona bem por keyword) ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Iniciando consulta...")
    results = []
    
    # Na Câmara, a busca por keywords funciona bem para texto completo
    for kw in KEYWORDS:
        # Otimização: pular keywords muito genéricas na busca da API da Câmara para evitar timeout,
        # ou manter se a API aguentar. Vamos manter a lista segura.
        if len(kw) < 4 and kw != "pln": continue 

        params = {
            "dataInicio": start_date_iso,
            "ordem": "DESC",
            "ordenarPor": "id",
            "keywords": kw,
            "itens": 20 
        }
        try:
            headers = {"User-Agent": "RoboLegislativoMB/1.0"}
            resp = await client.get(URL_CAMARA, params=params, headers=headers, timeout=15)
            
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
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Erro API Câmara ({kw}): {e}")
            
    return results

# --- CONSULTA SENADO (NOVA LÓGICA: Busca por Sigla -> Filtro Local) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando varredura por SIGLAS ({days_back_int} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    limit_date = datetime.now() - timedelta(days=days_back_int)
    ano_atual = datetime.now().year 

    # Em vez de buscar por PALAVRA, buscamos por TIPO (PLN, PL, PEC)
    # Isso garante que nada escapa, pois filtramos o texto localmente.
    for sigla in SENADO_SIGLAS:
        url = f"{URL_SENADO}?sigla={sigla}&ano={ano_atual}"
        
        try:
            resp = await client.get(url, headers=headers, timeout=20)
            
            if resp.status_code == 200:
                data = resp.json()
                
                pesquisa = data.get("PesquisaBasicaMateria", {})
                if not pesquisa: continue
                
                materias_container = pesquisa.get("Materias", {})
                if not materias_container: continue
                
                lista_materias = materias_container.get("Materia", [])
                if isinstance(lista_materias, dict): 
                    lista_materias = [lista_materias] 
                
                for mat in lista_materias:
                    dados = mat.get("DadosBasicosMateria", {})
                    
                    # 1. Filtro de Data (DataApresentacao)
                    data_apres_str = dados.get("DataApresentacao")
                    if not data_apres_str: continue
                    
                    try:
                        dt_obj = datetime.strptime(str(data_apres_str)[:10], "%Y-%m-%d")
                        if dt_obj < limit_date:
                            continue # Muito antigo, pula
                    except: continue

                    # 2. Filtro de Conteúdo (EMENTA) - AQUI ESTÁ O SEGREDO
                    ementa = dados.get("EmentaMateria", "")
                    natureza = dados.get("NaturezaMateria", "")
                    texto_completo = (ementa + " " + natureza).lower()
                    
                    # Verifica se ALGUMA keyword está no texto da matéria
                    found_kw = None
                    for kw in KEYWORDS:
                        if kw.lower() in texto_completo:
                            found_kw = kw
                            break
                    
                    if found_kw:
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
            
            await asyncio.sleep(0.5) # Pausa para não sobrecarregar a API
            
        except Exception as e:
            print(f"Erro API Senado ({sigla}): {e}")

    return results

# --- FUNÇÃO PRINCIPAL ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    print(f"--- Iniciando Robô Legislativo (Modo: {'Apenas Novos' if only_new else 'Tudo'}, Dias: {days_back}) ---")
    
    processed_ids = load_state()
    
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    propostas_encontradas = []
    novas_para_telegram = []
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # Roda as duas buscas
        res_camara = await check_camara(client, start_date_iso)
        res_senado = await check_senado(client, days_back)
        
        todas = res_camara + res_senado
        seen_now = set()
        
        for p in todas:
            if p['uid'] in seen_now: continue
            seen_now.add(p['uid'])
            
            propostas_encontradas.append(p)
            
            if p['uid'] not in processed_ids:
                novas_para_telegram.append(p)
                processed_ids.add(p['uid'])

    # Salva estado
    save_state(processed_ids)

    if only_new:
        if not novas_para_telegram:
            print("--- Nenhuma nova proposição legislativa encontrada (Background). ---")
            return []
        
        msg = [f"🏛️ *Monitoramento Legislativo - Novas Proposições*\n"]
        
        for p in novas_para_telegram:
            icon = "🟢" if p['casa'] == "Câmara" else "🔵"
            ementa_curta = (p['ementa'] or "")[:250]
            
            msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
            msg.append(f"🔎 _Filtro: {p['keyword']}_")
            msg.append(f"📝 {ementa_curta}")
            msg.append(f"🔗 [Ver Inteiro Teor]({p['link']})")
            msg.append("---------------------------------------")

        final_text = "\n".join(msg)
        if len(final_text) > 4000: final_text = final_text[:4000] + "\n\n(Truncado...)"

        await send_telegram_message(final_text)
        return novas_para_telegram

    # Para o Frontend (retorna tudo encontrado na janela de tempo)
    return propostas_encontradas
