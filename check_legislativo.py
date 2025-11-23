# Nome do arquivo: check_legislativo.py
# Módulo para monitorar Projetos de Lei via APIs Oficiais (Câmara e Senado)
# Versão: 2.1 (Correção de Sintaxe e Filtros)

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
    "Brasil",
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
        dirname = os.path.dirname(STATE_FILE_PATH)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)
            
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(list(processed_ids), f)
    except Exception as e:
        print(f"Erro ao salvar estado legislativo: {e}")

# --- CONSULTA CÂMARA ---
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
            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Erro API Câmara ({kw}): {e}")
            
    return results

# Substitua a função 'check_senado' no arquivo check_legislativo.py por esta versão:

async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
   # Substitua a função 'check_senado' no arquivo check_legislativo.py por esta versão:

async def check_senado(client: httpx.AsyncClient, days_back_int: int) -> List[Dict]:
    print(f">>> [API Senado] Iniciando consulta ({days_back_int} dias)...")
    results = []
    headers = {"Accept": "application/json", "User-Agent": "RoboLegislativoMB/1.0"}
    
    # Define a data limite (ex: 30 dias atrás)
    limit_date = datetime.now() - timedelta(days=days_back_int)
    
    # Pega o ano atual para forçar a API a trazer coisas novas
    ano_atual = datetime.now().year 

    for kw in KEYWORDS:
        # [CORREÇÃO CRÍTICA]
        # Adicionamos 'ano' nos parâmetros. Sem isso, o Senado manda coisas de 2010 
        # e o seu código filtrava tudo, resultando em lista vazia.
        params = {
            "palavraChave": kw,
            "ano": ano_atual
        }
        
        try:
            # Timeout maior (15s) pois o Senado às vezes oscila
            resp = await client.get(URL_SENADO, params=params, headers=headers, timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                
                # Navegação segura no JSON complexo do Senado
                pesquisa = data.get("PesquisaBasicaMateria", {})
                if not pesquisa: continue
                
                materias_container = pesquisa.get("Materias", {})
                if not materias_container: continue
                
                lista_materias = materias_container.get("Materia", [])
                if isinstance(lista_materias, dict): 
                    lista_materias = [lista_materias] # Normaliza se for item único
                
                # DEBUG: Ver quantas matérias o Senado retornou (mesmo que antigas)
                # Isso vai aparecer no log do Render
                if len(lista_materias) > 0:
                    print(f"   [DEBUG Senado] '{kw}': retornou {len(lista_materias)} itens brutos.")

                for mat in lista_materias:
                    dados = mat.get("DadosBasicosMateria", {})
                    data_apres = dados.get("DataApresentacao") # YYYY-MM-DD
                    
                    if data_apres:
                        try:
                            # Converte string para data
                            dt_obj = datetime.strptime(str(data_apres)[:10], "%Y-%m-%d")
                            
                            # Filtra: Se for mais recente que a data limite, adiciona na lista
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
                        except ValueError:
                            continue
            
            # Pausa leve para não bloquear o IP
            await asyncio.sleep(0.2)
            
        except Exception as e:
            print(f"Erro API Senado ({kw}): {e}")

    return results
# --- FUNÇÃO PRINCIPAL (WORKER + API) ---
async def check_and_process_legislativo(only_new: bool = True, days_back: int = 5) -> List[Dict]:
    """
    Orquestra a verificação.
    :param only_new: Se True (Telegram/Robô), filtra o que já foi visto e só retorna novidades. 
                     Se False (Site), retorna tudo o que encontrar na janela de tempo.
    :param days_back: Quantos dias olhar para trás.
    """
    print(f"--- Iniciando Robô Legislativo (Modo: {'Apenas Novos' if only_new else 'Tudo'}, Dias: {days_back}) ---")
    
    processed_ids = load_state()
    
    # Define janela de tempo
    start_date_iso = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    propostas_encontradas = [] # Lista final de retorno
    novas_para_telegram = []   # Lista apenas para notificação
    
    async with httpx.AsyncClient(timeout=40) as client:
        res_camara = await check_camara(client, start_date_iso)
        res_senado = await check_senado(client, days_back)
        
        todas = res_camara + res_senado
        
        # Deduplica (mesma proposta pode aparecer em várias keywords)
        seen_now = set()
        
        for p in todas:
            if p['uid'] in seen_now:
                continue
            seen_now.add(p['uid'])
            
            # Adiciona à lista geral (para o site ver tudo)
            propostas_encontradas.append(p)
            
            # Verifica se é inédita para o Telegram
            if p['uid'] not in processed_ids:
                novas_para_telegram.append(p)
                processed_ids.add(p['uid']) # Marca como vista

    # Se for rodada automática do Robô, salva o estado e notifica
    if only_new:
        if not novas_para_telegram:
            print("--- Nenhuma nova proposição legislativa encontrada (Background). ---")
            return []
        
        # Monta o Relatório para Telegram
        msg = [f"🏛️ *Monitoramento Legislativo - Novas Proposições*\n"]
        
        for p in novas_para_telegram:
            icon = "🟢" if p['casa'] == "Câmara" else "🔵"
            ementa_curta = p['ementa'][:250] + "..." if p['ementa'] and len(p['ementa']) > 250 else p['ementa']
            
            msg.append(f"{icon} *{p['casa']}* | {p['tipo']} {p['numero']}/{p['ano']}")
            msg.append(f"🔎 _Tema: {p['keyword']}_")
            msg.append(f"📝 {ementa_curta}")
            msg.append(f"🔗 [Ver Inteiro Teor]({p['link']})")
            msg.append("---------------------------------------")

        final_text = "\n".join(msg)
        
        if len(final_text) > 4000:
            final_text = final_text[:4000] + "\n\n(Relatório truncado...)"

        await send_telegram_message(final_text)
        print(f"Relatório Legislativo ({len(novas_para_telegram)} itens) enviado ao Telegram.")
        
        # Salva o estado atualizado no HD
        save_state(processed_ids)
        
        return novas_para_telegram

    # Se for chamada do Site (only_new=False), apenas retorna a lista completa da janela de tempo
    # Nota: Também salvamos o estado aqui para evitar que o robô notifique depois algo que o usuário já viu no site.
    save_state(processed_ids)
    return propostas_encontradas
