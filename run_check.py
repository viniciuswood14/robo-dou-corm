# Nome do arquivo: run_check.py
# Versão: 16.0.0 (Com Redundância/Fallback Automático)

import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo 
from typing import Dict, List, Any, Set
from bs4 import BeautifulSoup

# IA / Gemini
import google.generativeai as genai

# Importa as funções do Robô DOU (api.py)
try:
    from api import (
        inlabs_login_and_get_session,
        resolve_date_url,
        fetch_listing_html,
        pick_zip_links_from_listing,
        download_zip,
        extract_xml_from_zip,
        process_grouped_materia,
        get_ai_analysis,
        monta_whatsapp,
        GEMINI_API_KEY,
        GEMINI_MASTER_PROMPT,
        GEMINI_MPO_PROMPT,
        MPO_ORG_STRING,
        Publicacao
    )
except ImportError as e:
    print(f"Erro: Falha ao importar módulos do 'api.py'. Detalhe: {e}")
    raise

# Importa o sender do Telegram
try:
    from telegram import send_telegram_message
except ImportError as e:
    print(f"Erro: Falha ao importar 'telegram.py'. Detalhe: {e}")
    raise

# Importa a função do robô Valor
try:
    from check_valor import check_and_process_valor
except ImportError as e:
    print(f"Erro: Falha ao importar 'check_valor.py'. Detalhe: {e}")
    raise

# Importa a função do robô PAC
try:
    from check_pac import check_and_process_pac
except ImportError as e:
    print(f"Erro: Falha ao importar 'check_pac.py'. Detalhe: {e}")
    raise

# --- [NOVA IMPORTAÇÃO] ---
try:
    from dou_fallback import executar_fallback
except ImportError:
    print("Aviso: 'dou_fallback.py' não encontrado. Redundância desativada.")
    executar_fallback = None
# --- [FIM DA NOVA IMPORTAÇÃO] ---


# --- CONFIGURAÇÃO DO ESTADO (DOU) ---
STATE_FILE_PATH = os.environ.get("STATE_FILE_PATH", "/dados/processed_state.json")

def load_state() -> Dict[str, List[str]]:
    """Carrega o estado (ZIPs processados) do disco."""
    try:
        state_dir = os.path.dirname(STATE_FILE_PATH)
        if not os.path.exists(state_dir):
            os.makedirs(state_dir, exist_ok=True)
        
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {} 

def save_state(state: Dict[str, List[str]]):
    """Salva o estado atual no disco."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico ao salvar estado: {e}")


async def check_and_process_dou(today_str: str):
    """
    Função principal com REDUNDÂNCIA.
    Tenta InLabs (XML). Se falhar, tenta Site Público (Fallback).
    """
    print(f"--- Iniciando verificação do DOU para a data: {today_str} ---")
    
    # 0. Configura IA
    if not GEMINI_API_KEY:
        print("Erro: GEMINI_API_KEY não encontrada.")
        return
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        print(f"Falha IA: {e}")
        return

    state = load_state()
    processed_zips_today = set(state.get(today_str, []))
    
    # Flag para saber se usamos o fallback hoje para não repetir
    fallback_marker = f"FALLBACK_DONE_{today_str}"
    if fallback_marker in processed_zips_today:
        print("Modo Fallback já foi executado com sucesso hoje. Pulando para evitar duplicidade.")
        return

    pubs_finais: List[Publicacao] = []
    usou_fallback = False
    sucesso_inlabs = False
    client = None

    # --- TENTATIVA 1: INLABS ---
    try:
        print(">>> Tentando conexão InLabs (Principal)...")
        client = await inlabs_login_and_get_session()
        listing_url = await resolve_date_url(client, today_str)
        html = await fetch_listing_html(client, today_str)
        all_zip_links = pick_zip_links_from_listing(html, listing_url, ["DO1", "DO2", "DO3"])
        
        if not all_zip_links:
            # Se logou mas não achou ZIP, pode ser que não saiu ainda OU erro no InLabs
            print(f"Nenhum ZIP encontrado no InLabs para {today_str} ainda.")
            # Não vamos pro fallback imediatamente se apenas "não saiu", 
            # mas se der ERRO de conexão, vamos.
            return 

        # Filtra novos
        current_zip_set = set(all_zip_links)
        new_zip_links = list(current_zip_set - processed_zips_today)

        if not new_zip_links:
            print("Nenhuma nova edição (ZIP) encontrada.")
            return

        print(f"Encontrados {len(new_zip_links)} novos arquivos ZIP.")
        
        # Processa XMLs
        all_new_xml_blobs = []
        for zurl in new_zip_links:
            zb = await download_zip(client, zurl)
            all_new_xml_blobs.extend(extract_xml_from_zip(zb))
        
        if not all_new_xml_blobs:
            print("ZIPs vazios.")
            state[today_str] = list(current_zip_set)
            save_state(state)
            return

        # Agrupa e Filtra
        materias = {}
        for blob in all_new_xml_blobs:
            try:
                soup = BeautifulSoup(blob, "lxml-xml")
                article = soup.find("article")
                if not article: continue
                materia_id = article.get("idMateria")
                if not materia_id: continue
                if materia_id not in materias:
                    materias[materia_id] = {"main_article": None, "full_text": ""}
                materias[materia_id]["full_text"] += (blob.decode("utf-8", errors="ignore") + "\n")
                body = article.find("body")
                if body and body.find("Identifica") and body.find("Identifica").get_text(strip=True):
                    materias[materia_id]["main_article"] = article
            except: continue
        
        pubs_filtradas = []
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"], content["full_text"], custom_keywords=[]
                )
                if publication:
                    pubs_filtradas.append(publication)

        # Deduplicar
        seen = set()
        for p in pubs_filtradas:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                pubs_finais.append(p)
        
        sucesso_inlabs = True
        # Atualiza estado com os ZIPs processados
        state[today_str] = list(current_zip_set)

    except Exception as e:
        print(f"⚠️ Erro no InLabs: {e}")
        usou_fallback = True
        
    finally:
        if client: await client.aclose()

    # --- TENTATIVA 2: FALLBACK (Se InLabs falhou) ---
    if usou_fallback and executar_fallback:
        print(">>> Iniciando Modo de Redundância (Fallback)...")
        try:
            # Keywords vazias pois o fallback já tem a lista "Hardcoded" da Marinha
            res_fallback = await executar_fallback(today_str, [])
            
            if res_fallback:
                print(f"Fallback encontrou {len(res_fallback)} itens.")
                for item in res_fallback:
                    # Converte dict para objeto Publicacao
                    p = Publicacao(
                        organ=item['organ'],
                        type=item['type'],
                        summary=item['summary'],
                        raw=item['raw'],
                        relevance_reason=item['relevance_reason'],
                        section=item['section'],
                        clean_text=item['raw']
                    )
                    pubs_finais.append(p)
                
                # Marca no estado que o fallback rodou hoje para não repetir em loop
                current_list = state.get(today_str, [])
                current_list.append(fallback_marker)
                state[today_str] = current_list
            else:
                print("Fallback rodou mas não encontrou nada relevante.")

        except Exception as ef:
            print(f"Erro CRÍTICO: Falha também no Fallback: {ef}")
            return

    # --- ANÁLISE COM IA (Comum aos dois métodos) ---
    if not pubs_finais:
        if sucesso_inlabs: 
            save_state(state)
        print("Nenhuma publicação relevante encontrada (após filtros).")
        return

    print(f"Enviando {len(pubs_finais)} matérias para análise da IA...")
    tasks = []
    for p in pubs_finais:
        prompt_to_use = GEMINI_MASTER_PROMPT
        if p.is_mpo_navy_hit:
            prompt_to_use = GEMINI_MPO_PROMPT
        
        # Se veio do fallback, o texto pode ser menor, mas a IA analisa igual
        texto_analise = p.clean_text if p.clean_text else p.raw
        tasks.append(get_ai_analysis(texto_analise, model, prompt_to_use))

    ai_results = await asyncio.gather(*tasks, return_exceptions=True)

    pubs_para_envio = []
    for p, ai_out in zip(pubs_finais, ai_results):
        if isinstance(ai_out, Exception) or not ai_out:
            # Se IA falhar, manda assim mesmo para garantir
            p.relevance_reason = "⚠️ IA indisponível. Verifique manualmente."
            pubs_para_envio.append(p)
            continue
        
        if "sem impacto direto" in ai_out.lower() and not p.is_mpo_navy_hit:
            continue # Filtra irrelevantes
            
        p.relevance_reason = ai_out
        pubs_para_envio.append(p)

    if not pubs_para_envio:
        if sucesso_inlabs: save_state(state)
        print("IA filtrou tudo. Nada a enviar.")
        return

    # --- ENVIO TELEGRAM ---
    texto_zap = monta_whatsapp(pubs_para_envio, today_str)
    
    header = f"Alerta de Publicações - DOU ({today_str})\n"
    if usou_fallback:
        header += "⚠️ *Aviso: InLabs instável. Busca realizada via portal público.*\n"
    
    final_msg = header + "\n" + texto_zap
    
    await send_telegram_message(final_msg)
    
    # Salva o estado final
    save_state(state)
    print("Ciclo finalizado com sucesso.")


# --- LOOP PRINCIPAL ---
async def main_loop():
    """
    Loop de serviço contínuo.
    """
    TZ_BRASILIA = ZoneInfo("America/Sao_Paulo")
    INTERVALO_SEGUNDOS = 10 * 60 # 10 minutos
    
    valor_check_done = False
    pac_check_done = False
    last_day = None
    
    print("--- Robô Integrado (DOU + Fallback + Valor + PAC) Iniciado ---")

    while True:
        agora = datetime.now(TZ_BRASILIA)
        hoje_str = agora.strftime('%Y-%m-%d')
        ano_str = agora.strftime('%Y')
        ontem_str = (agora - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # Reseta flags diárias
        if last_day != hoje_str:
            valor_check_done = False
            pac_check_done = False
            last_day = hoje_str
            print(f"*** Novo dia: {hoje_str} ***")

        # [cite_start]Horário de expediente (05h às 23h) [cite: 1]
        if 5 <= agora.hour < 23:
            
            # 1. DOU (Roda a cada 10 min)
            try:
                await check_and_process_dou(hoje_str)
            except Exception as e:
                print(f"Erro no loop DOU: {e}")

            is_weekday = agora.weekday() < 5

            # 2. Valor (05:10+, dias úteis)
            if is_weekday and agora.hour == 5 and agora.minute >= 10 and not valor_check_done:
                try:
                    await check_and_process_valor(ontem_str)
                    valor_check_done = True
                except Exception as e:
                    print(f"Erro Valor: {e}")

            # 3. PAC (05:15+, dias úteis)
            if is_weekday and agora.hour == 5 and agora.minute >= 15 and not pac_check_done:
                try:
                    await check_and_process_pac(ano_str)
                    pac_check_done = True
                except Exception as e:
                    print(f"Erro PAC: {e}")

        else:
            print(f"[{agora.strftime('%H:%M')}] Fora de expediente. Dormindo.")

        await asyncio.sleep(INTERVALO_SEGUNDOS)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("Robô parado.")
