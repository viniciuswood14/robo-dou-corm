# Nome do arquivo: check_legislativo.py
# Módulo para monitorar Projetos de Lei via APIs Oficiais (Câmara e Senado)
# Versão: 5.0 (Produção Otimizada)

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

# Palavras-chave Estratégicas da Marinha (ATUALIZADO)
KEYWORDS = [
    "Marinha do Brasil", 
    "Forças Armadas", 
    "Defesa Nacional", 
    "Submarino", 
    "Nuclear", 
    "Amazônia Azul", 
    "PROSUB", 
    "Classe Tamandaré", 
    "Fundo Naval", 
    "Base Industrial de Defesa",
    "Carreira Militar",
    "ministério da defesa",
    "autoridade marítima",
    "comando da marinha",
    "fundo naval",
    "amazônia azul tecnologias de defesa",
    "caixa de construções de casas para o pessoal da marinha",
    "empresa gerencial de projetos navais",
    "fundo de desenvolvimento do ensino profissional marítimo",
    "programa nuclear da marinha",
    "Defesa Marítima",
    "fragata",
    "Pensões Militares",
    "Soberania Nacional",
    "Almirantado",
    "Corpo de Fuzileiros Navais",
    # --- NOVOS TERMOS ORÇAMENTÁRIOS E PLN ---
    "PLN",
    "Orçamento Fiscal",
    "Crédito Especial",
    "Crédito Suplementar",
    "Crédito Extraordinário"
]

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

# --- CONSULTA CÂMARA ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Iniciando consulta...")
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
            # Headers para evitar bloqueio
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

# --- CONSULTA SENADO (OTIMIZADA) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando consulta ({days_back_int} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    limit_date = datetime.now() - timedelta(days=days_back_int)
    ano_atual = datetime.now().year 

    for kw in KEYWORDS:
        # Filtra pelo ANO ATUAL na URL para evitar baixar histórico inútil
        # Se quiser testar histórico, remova o "&ano=..."
        url = f"{URL_SENADO}?palavraChave={kw}&ano={ano_atual}"
        
        try:
            resp = await client.get(url, headers=headers, timeout=15)
            
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
                    data_apres = dados.get("DataApresentacao")
                    
                    if data_apres:
                        try:
                            dt_obj = datetime.strptime(str(data_apres)[:10], "%Y-%m-%d")
                            
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
    print(f"--- Iniciando Robô Legislativo (Modo: {'Apenas Novos' if only_new else 'Tudo'}, Dias: {days_back}) ---")
    
    processed_ids = load_state()
    
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    propostas_encontradas = []
    novas_para_telegram = []
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
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

    if only_new:
        if not novas_para_telegram:
            print("--- Nenhuma nova proposição legislativa encontrada (Background). ---")
            return []
        
        msg = [f"🏛️ *Monitoramento Legislativo - Novas Proposições*\n"]
        
        for p in novas_para_telegram:
            icon = "🟢" if p['casa'] == "Câmara" else "🔵"
            ementa_curta = (p['ementa'] or "")[:250]
            
            msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
            msg.append(f"🔎 _Tema: {p['keyword']}_")
            msg.append(f"📝 {ementa_curta}")
            msg.append(f"🔗 [Ver Inteiro Teor]({p['link']})")
            msg.append("---------------------------------------")

        final_text = "\n".join(msg)
        if len(final_text) > 4000: final_text = final_text[:4000] + "\n\n(Truncado...)"

        await send_telegram_message(final_text)
        save_state(processed_ids)
        return novas_para_telegram

    save_state(processed_ids)
    return propostas_encontradas
