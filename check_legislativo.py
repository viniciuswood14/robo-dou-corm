# Nome do arquivo: check_legislativo.py
# Módulo para monitorar Projetos de Lei via APIs Oficiais (Câmara e Senado)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# Tenta importar o enviador do Telegram (se existir no projeto)
try:
    from telegram import send_telegram_message
except ImportError:
    # Mock para testes locais sem telegram configurado
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

# --- CONFIGURAÇÃO ---
# Arquivo para salvar quais PLs já avisamos (para não repetir)
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

# Palavras-chave Estratégicas da Marinha
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
    "amazônia azul",
    "Pensões Militares"
]

# URLs Oficiais das APIs
URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

def load_state() -> Set[str]:
    """Carrega IDs de propostas já processadas."""
    if not os.path.exists(STATE_FILE_PATH):
        return set()
    try:
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    except:
        return set()

def save_state(processed_ids: Set[str]):
    """Salva o estado no disco."""
    try:
        # Garante que o diretório existe
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(processed_ids), f)
    except Exception as e:
        print(f"Erro ao salvar estado legislativo: {e}")

# --- CONSULTA CÂMARA ---
async def check_camara(client: httpx.AsyncClient, start_date: str) -> List[Dict]:
    print(">>> [API Câmara] Iniciando consulta...")
    results = []
    
    for kw in KEYWORDS:
        params = {
            "dataInicio": start_date,
            "ordem": "DESC",
            "ordenarPor": "id",
            "keywords": kw,
            "itens": 5  # Traz apenas as 5 mais recentes por palavra-chave
        }
        try:
            # A API da Câmara é chata com headers, user-agent ajuda
            headers = {"User-Agent": "RoboLegislativoMB/1.0"}
            resp = await client.get(URL_CAMARA, params=params, headers=headers)
            
            if resp.status_code == 200:
                dados = resp.json().get("dados", [])
                for item in dados:
                    results.append({
                        "uid": f"CAM_{item['id']}", # ID Único
                        "casa": "Câmara",
                        "tipo": item['siglaTipo'],
                        "numero": str(item['numero']),
                        "ano": str(item['ano']),
                        "ementa": item['ementa'],
                        "link": f"https://www.camara.leg.br/propostas-legislativas/{item['id']}",
                        "keyword": kw
                    })
            # Respeita limite de taxa da API
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Erro API Câmara ({kw}): {e}")
            
    return results

# --- CONSULTA SENADO ---
async def check_senado(client: httpx.AsyncClient) -> List[Dict]:
    print(">>> [API Senado] Iniciando consulta...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    for kw in KEYWORDS:
        # O Senado busca na ementa ou indexação
        url = f"{URL_SENADO}?palavraChave={kw}"
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                # A estrutura do JSON do Senado é complexa e aninhada
                pesquisa = data.get("PesquisaBasicaMateria", {})
                if not pesquisa: continue
                
                materias_container = pesquisa.get("Materias", {})
                if not materias_container: continue
                
                lista_materias = materias_container.get("Materia", [])
                if isinstance(lista_materias, dict): 
                    lista_materias = [lista_materias] # Normaliza se for 1 item
                
                for mat in lista_materias:
                    dados = mat.get("DadosBasicosMateria", {})
                    data_apres = dados.get("DataApresentacao", "")[:10] # YYYY-MM-DD
                    
                    # Filtra data manualmente (Senado não filtra na query da pesquisa textual)
                    if data_apres:
                        try:
                            dt_obj = datetime.strptime(data_apres, "%Y-%m-%d")
                            # Pega apenas coisas dos últimos 5 dias
                            if dt_obj >= datetime.now() - timedelta(days=5):
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
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Erro API Senado ({kw}): {e}")

    return results

# --- FUNÇÃO PRINCIPAL (WORKER) ---
async def check_and_process_legislativo():
    """
    Orquestra a verificação.
    """
    print("--- Iniciando Robô Legislativo (APIs Oficiais) ---")
    
    processed_ids = load_state()
    
    # Define janela de tempo (últimos 5 dias para garantir que nada passou no fim de semana)
    start_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    
    novas_propostas = []
    
    async with httpx.AsyncClient(timeout=30) as client:
        # Roda Câmara e Senado em paralelo para ganhar tempo? 
        # Melhor sequencial com delay para não tomar block de IP por flood.
        res_camara = await check_camara(client, start_date)
        res_senado = await check_senado(client)
        
        todas = res_camara + res_senado
        
        # Deduplica e filtra IDs já vistos
        seen_now = set()
        for p in todas:
            if p['uid'] not in processed_ids and p['uid'] not in seen_now:
                novas_propostas.append(p)
                processed_ids.add(p['uid'])
                seen_now.add(p['uid'])

    if not novas_propostas:
        print("--- Nenhuma nova proposição legislativa encontrada. ---")
        return

    # Monta o Relatório
    msg = [f"🏛️ *Monitoramento Legislativo - Novas Proposições*\n"]
    
    for p in novas_propostas:
        icon = "🟢" if p['casa'] == "Câmara" else "🔵"
        ementa_curta = p['ementa'][:250] + "..." if len(p['ementa']) > 250 else p['ementa']
        
        msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
        msg.append(f"🔎 _Tema: {p['keyword']}_")
        msg.append(f"📝 {ementa_curta}")
        msg.append(f"🔗 [Ver Inteiro Teor]({p['link']})")
        msg.append("---------------------------------------")

    # Envia pro Telegram (em blocos se for muito grande)
    final_text = "\n".join(msg)
    
    # Limite do Telegram é 4096 caracteres. Se passar, corta ou manda em partes.
    # Aqui vamos mandar truncado por segurança.
    if len(final_text) > 4000:
        final_text = final_text[:4000] + "\n\n(Relatório truncado por tamanho...)"

# ... (código anterior de envio para o Telegram) ...
    
    await send_telegram_message(final_text)
    print("Relatório Legislativo enviado ao Telegram.")
    
    # Salva o estado atualizado no HD
    save_state(processed_ids)

    # --- [NOVO] RETORNA A LISTA PARA A API ---
    return novas_propostas
