# Nome do arquivo: check_legislativo.py
# Versão: 4.0 (DEBUG MODE - SENADO)

import os
import json
import asyncio
import httpx
from datetime import datetime, timedelta
from typing import List, Set, Dict

# --- CONFIGURAÇÃO ---
STATE_FILE_PATH = os.environ.get("LEG_STATE_FILE_PATH", "/dados/legislativo_state.json")

# Palavras-chave (Mantive as suas)
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
    "Brasil" # MANTENHA "BRASIL" PARA O TESTE DE CARGA!
]

URL_CAMARA = "https://dadosabertos.camara.leg.br/api/v2/proposicoes"
URL_SENADO = "https://legis.senado.leg.br/dadosabertos/materia/pesquisa/lista"

# --- MOCK TELEGRAM (Para evitar erros de importação se o arquivo faltar) ---
try:
    from telegram import send_telegram_message
except ImportError:
    async def send_telegram_message(msg):
        print(f"[TELEGRAM MOCK] {msg}")

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
        print(f"Erro ao salvar estado: {e}")

# --- CÂMARA (Código Original - Funcional) ---
async def check_camara(client: httpx.AsyncClient, start_date_iso: str) -> List[Dict]:
    print(f">>> [API Câmara] Iniciando consulta...")
    results = []
    for kw in KEYWORDS:
        params = {"dataInicio": start_date_iso, "ordem": "DESC", "ordenarPor": "id", "keywords": kw, "itens": 5}
        try:
            resp = await client.get(URL_CAMARA, params=params, headers={"User-Agent": "RoboMB/1.0"}, timeout=10)
            if resp.status_code == 200:
                for item in resp.json().get("dados", []):
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
        except Exception: pass
    return results

# --- SENADO (VERSÃO DEBUG EXTREMO) ---
async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f"\n>>> [DEBUG SENADO] Iniciando varredura profunda ({days_back_int} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    # Data limite para filtrar
    limit_date = datetime.now() - timedelta(days=days_back_int)
    ano_atual = datetime.now().year

    for kw in KEYWORDS:
        # Vamos remover o filtro de ano da URL para ver se ele traz QUALQUER COISA
        # Se trouxer coisas velhas, a API funciona. Se não trouxer nada, a API mudou.
        # url = f"{URL_SENADO}?palavraChave={kw}&ano={ano_atual}" <-- TENTEI ESSE ANTES
        
        # Voltando ao URL básico para testar conexão:
        url = f"{URL_SENADO}?palavraChave={kw}"
        
        print(f"--- [DEBUG] Consultando: {kw} ---")
        # print(f"    URL: {url}") 

        try:
            resp = await client.get(url, headers=headers, timeout=20)
            print(f"    Status Code: {resp.status_code}")
            
            if resp.status_code != 200:
                print(f"    ERRO HTTP: {resp.text[:200]}")
                continue

            data = resp.json()
            
            # Debug da Estrutura do JSON
            pesquisa = data.get("PesquisaBasicaMateria", {})
            if not pesquisa:
                print("    AVISO: JSON veio sem 'PesquisaBasicaMateria'.")
                continue
                
            materias_container = pesquisa.get("Materias", {})
            if not materias_container:
                print("    AVISO: 'Materias' vazio ou inexistente. (Nenhum resultado para esta palavra)")
                continue
            
            lista_materias = materias_container.get("Materia", [])
            # O Senado retorna um Dict se for só 1 item, e Lista se forem vários.
            if isinstance(lista_materias, dict):
                lista_materias = [lista_materias]
            
            print(f"    ENCONTRADOS: {len(lista_materias)} registros brutos.")

            count_valid = 0
            for mat in lista_materias:
                dados = mat.get("DadosBasicosMateria", {})
                cod = dados.get("CodigoMateria")
                data_apres_raw = dados.get("DataApresentacao")
                
                # print(f"      > Analisando PL {cod} de {data_apres_raw}...")

                if data_apres_raw:
                    try:
                        dt_obj = datetime.strptime(str(data_apres_raw)[:10], "%Y-%m-%d")
                        
                        if dt_obj >= limit_date:
                            # print(f"        ✅ VÁLIDO! Data {dt_obj} é recente.")
                            results.append({
                                "uid": f"SEN_{cod}",
                                "casa": "Senado",
                                "tipo": dados.get("SiglaMateria"),
                                "numero": dados.get("NumeroMateria"),
                                "ano": dados.get("AnoMateria"),
                                "ementa": dados.get("EmentaMateria"),
                                "link": f"https://www25.senado.leg.br/web/atividade/materias/-/materia/{cod}",
                                "keyword": kw
                            })
                            count_valid += 1
                        else:
                            pass
                            # print(f"        ❌ Antigo. (Limite: {limit_date})")
                    except Exception as e:
                        print(f"        ⚠️ Erro ao processar data: {e}")
            
            if count_valid > 0:
                print(f"    *** SUCESSO: {count_valid} matérias recentes adicionadas para '{kw}'. ***")

            await asyncio.sleep(0.5) # Pausa para não bloquear IP
            
        except Exception as e:
            print(f"    ERRO CRÍTICO na requisição: {e}")

    return results

# --- EXECUTOR ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    print(f"--- Iniciando Robô Legislativo v4.0 (DEBUG) ---")
    processed_ids = load_state()
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    propostas_encontradas = []
    novas_para_telegram = []
    
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        # Primeiro checa Senado para vermos os logs logo
        res_senado = await check_senado(client, days_back)
        res_camara = await check_camara(client, start_date_iso)
        
        todas = res_senado + res_camara
        
        seen_now = set()
        for p in todas:
            if p['uid'] in seen_now: continue
            seen_now.add(p['uid'])
            propostas_encontradas.append(p)
            
            if p['uid'] not in processed_ids:
                novas_para_telegram.append(p)
                processed_ids.add(p['uid'])

    if only_new and novas_para_telegram:
        msg = [f"🏛️ *Legislativo - Novas Proposições*\n"]
        for p in novas_para_telegram:
            icon = "🟢" if p['casa'] == "Câmara" else "🔵"
            msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
            msg.append(f"🔎 {p['keyword']}")
            msg.append(f"🔗 {p['link']}")
            msg.append("---")
        
        await send_telegram_message("\n".join(msg)[:4000])
        save_state(processed_ids)
        return novas_para_telegram

    save_state(processed_ids)
    return propostas_encontradas
