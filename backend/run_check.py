# Nome do arquivo: run_check.py

import asyncio
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo # Para checar o fuso-horário
from typing import Dict, List, Any, Set
from bs4 import BeautifulSoup

# IA / Gemini
import google.generativeai as genai

# Importa as funções que JÁ CRIAMOS em api.py
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
        Publicacao  # Importa o modelo Pydantic
    )
except ImportError as e:
    print(f"Erro: Falha ao importar módulos do 'api.py'. Verifique o arquivo. Detalhe: {e}")
    raise

# Importa o novo sender do Telegram
try:
    from telegram import send_telegram_message
except ImportError as e:
    print(f"Erro: Falha ao importar 'telegram.py'. Verifique o arquivo. Detalhe: {e}")
    raise

# --- CONFIGURAÇÃO DO ESTADO ---
STATE_FILE_PATH = os.environ.get("STATE_FILE_PATH", "/dados/processed_state.json")

def load_state() -> Dict[str, List[str]]:
    """Carrega o estado (ZIPs processados) do disco."""
    try:
        state_dir = os.path.dirname(STATE_FILE_PATH)
        if not os.path.exists(state_dir):
            os.makedirs(state_dir, exist_ok=True)
            print(f"Diretório de estado criado em: {state_dir}")
        
        with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
            
    except FileNotFoundError:
        print(f"Arquivo de estado não encontrado em {STATE_FILE_PATH}. Criando um novo.")
        return {} 
    except (json.JSONDecodeError, IsADirectoryError):
        print(f"Erro ao ler {STATE_FILE_PATH} (corrompido ou é um diretório). Resetando estado.")
        return {}
    except Exception as e:
        print(f"Erro inesperado ao carregar estado: {e}")
        return {}


def save_state(state: Dict[str, List[str]]):
    """Salva o estado atual no disco."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"Erro Crítico: Falha ao salvar estado em {STATE_FILE_PATH}: {e}")

# --- LÓGICA PRINCIPAL DO AGENDADOR ---

# ----------------------------------------------------------------------------------
# ↓↓↓ MUDANÇA IMPORTANTE AQUI ↓↓↓
# A função agora RECEBE a data de Brasília (today_str) como argumento
# ----------------------------------------------------------------------------------
async def check_and_process_dou(today_str: str):
    """
    Função principal de verificação.
    """
    print(f"--- Iniciando verificação do DOU para a data: {today_str} ---")
    
    # 0. Configura a IA
    if not GEMINI_API_KEY:
        print("Erro: GEMINI_API_KEY não encontrada. Abortando.")
        return
    genai.configure(api_key=GEMINI_API_KEY)
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
    except Exception as e:
        print(f"Falha ao inicializar o modelo de IA: {e}")
        return

    # 1. Carrega o estado
    # (Não precisamos mais pegar a data aqui, ela veio como argumento)
    state = load_state()
    processed_zips_today = set(state.get(today_str, []))
    
    client = None
    try:
        # 2. Loga e lista os ZIPs do dia (usando o today_str correto)
        client = await inlabs_login_and_get_session()
        listing_url = await resolve_date_url(client, today_str)
        html = await fetch_listing_html(client, today_str)
        
        all_zip_links = pick_zip_links_from_listing(html, listing_url, ["DO1", "DO2", "DO3"])
        
        if not all_zip_links:
            print(f"Nenhum ZIP encontrado para {today_str} ainda.")
            return

        # 3. Compara e descobre ZIPs novos
        current_zip_set = set(all_zip_links)
        new_zip_links = list(current_zip_set - processed_zips_today)

        if not new_zip_links:
            print("Nenhuma nova edição do DOU encontrada.")
            return

        print(f"Sucesso! Encontrados {len(new_zip_links)} novos arquivos ZIP:")
        for link in new_zip_links:
            print(f" - {link.split('/')[-1]}")

        # 4. Processa apenas os ZIPs novos
        all_new_xml_blobs = []
        for zurl in new_zip_links:
            zb = await download_zip(client, zurl)
            all_new_xml_blobs.extend(extract_xml_from_zip(zb))

        if not all_new_xml_blobs:
            print("Os novos ZIPs estavam vazios ou não continham XMLs.")
            state[today_str] = list(current_zip_set)
            save_state(state)
            return

        # ... (O resto da função continua igual) ...
        
        # 4a. Agrupar XMLs por Matéria
        materias: Dict[str, Dict[str, Any]] = {}
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
            except Exception:
                continue
        
        # 4b. Estágio 1 (Filtro por Regra Fixa)
        pubs_filtradas: List[Publicacao] = []
        for materia_id, content in materias.items():
            if content["main_article"]:
                publication = process_grouped_materia(
                    content["main_article"],
                    content["full_text"],
                    custom_keywords=[] 
                )
                if publication:
                    pubs_filtradas.append(publication)

        # 4c. Deduplicar
        seen: Set[str] = set()
        merged_pubs: List[Publicacao] = []
        for p in pubs_filtradas:
            key = (p.organ or "") + "||" + (p.type or "") + "||" + (p.summary or "")[:100]
            if key not in seen:
                seen.add(key)
                merged_pubs.append(p)

        if not merged_pubs:
            print("Novos ZIPs processados, mas nenhuma matéria relevante encontrada.")
            state[today_str] = list(current_zip_set)
            save_state(state)
            return

        # 4d. Estágio 2 (IA)
        tasks = []
        for p in merged_pubs:
            prompt_to_use = GEMINI_MASTER_PROMPT
            if p.is_mpo_navy_hit:
                prompt_to_use = GEMINI_MPO_PROMPT
            
            tasks.append(get_ai_analysis(p.clean_text or "", model, prompt_to_use))

        ai_results = await asyncio.gather(*tasks, return_exceptions=True)

        # 4e. Montar publicações finais
        pubs_finais: List[Publicacao] = []
        for p, ai_out in zip(merged_pubs, ai_results):
            if isinstance(ai_out, Exception):
                p.relevance_reason = f"Erro GRAVE na análise de IA: {ai_out}"
            elif ai_out is None:
                pass
            elif isinstance(ai_out, str):
                lower_ai = ai_out.lower()
                if ai_out.startswith("Erro na análise de IA:"):
                    p.relevance_reason = ai_out
                elif "sem impacto direto" in lower_ai and p.is_mpo_navy_hit:
                    p.relevance_reason = "⚠️ IA ignorou impacto MPO: " + ai_out
                elif "sem impacto direto" not in lower_ai:
                    p.relevance_reason = ai_out
                else:
                    continue
            
            pubs_finais.append(p)

        if not pubs_finais:
            print("Matérias filtradas pela IA. Nenhuma relevante para notificar.")
            state[today_str] = list(current_zip_set)
            save_state(state)
            return

        # 5. Gera o relatório e envia
        texto_whatsapp = monta_whatsapp(pubs_finais, today_str)
        report_header = f"Alerta de novas publicações no DOU de {today_str} (detectadas às {datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%H:%M')}):\n\n"
        
        await send_telegram_message(report_header + texto_whatsapp)

        # 6. Atualiza o estado com sucesso
        state[today_str] = list(current_zip_set)
        save_state(state)
        print(f"Estado salvo. {len(current_zip_set)} ZIPs processados para {today_str}.")

    except Exception as e:
        print(f"Erro inesperado no fluxo principal: {e}")
        await send_telegram_message(f"Erro no Robô DOU: {e}")
        
    finally:
        if client:
            await client.aclose()
        print(f"--- Verificação do DOU finalizada ---")


# --- LOOP PRINCIPAL (PARA BACKGROUND WORKER) ---

async def main_loop():
    """
    Loop principal que roda como um Background Worker, mas com 
    horário agendado (5h às 23h de Brasília).
    """
    
    TZ_BRASILIA = ZoneInfo("America/Sao_Paulo")
    INTERVALO_SEGUNDOS = 30 * 60 # 30 minutos
    
    print("--- Iniciando Robô DOU em modo Background Worker (com horário agendado) ---")

    while True:
        agora_brasilia = datetime.now(TZ_BRASILIA)
        hora_atual = agora_brasilia.hour 
        
        # Define o período de atividade
        hora_inicio = 5
        hora_fim = 24 # TEMPORÁRIO PARA TESTAR (Voltar para 23)
        
        if hora_inicio <= hora_atual < hora_fim:
            print(f"[{agora_brasilia.strftime('%Y-%m-%d %H:%M:%S')}] Horário comercial (Hora: {hora_atual}h). Iniciando verificação...")
            try:
                # ------------------------------------------------------------------
                # ↓↓↓ MUDANÇA IMPORTANTE AQUI ↓↓↓
                # Pegamos a data DE BRASÍLIA e passamos para a função
                # ------------------------------------------------------------------
                data_hoje_brasilia = agora_brasilia.strftime('%Y-%m-%d')
                await check_and_process_dou(data_hoje_brasilia)
                
            except Exception as e:
                print(f"Erro CRÍTICO no loop principal: {e}")
                try:
                    await send_telegram_message(f"Erro CRÍTICO no Robô DOU: {e}\nVou tentar rodar de novo em 30 min.")
                except:
                    pass
        else:
            print(f"[{agora_brasilia.strftime('%Y-%m-%d %H:%M:%S')}] Fora de expediente (Hora: {hora_atual}h). Pulando esta verificação.")

        
        print(f"--- Próxima checagem em 30 minutos... ---")
        await asyncio.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n--- Robô interrompido manualmente ---")
