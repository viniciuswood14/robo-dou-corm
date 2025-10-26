import asyncio
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
import httpx
import json
import os
from mb_portaria_parser import process_grouped_materia, merge_publications, montar_mensagem_whatsapp

########################################
# CONFIG / MODELOS
########################################

with open("config.json", "r", encoding="utf-8") as f:
    cfg = json.load(f)

API_BASE_URL = cfg.get("api_base_url")
OPENAI_API_KEY = cfg.get("openai_api_key")
OPENAI_MODEL = cfg.get("openai_model", "gpt-4o-mini")  # default fallback se quiser

app = FastAPI(title="Monitor DOU MB", version="corrigida-2025-10-26")

class Publicacao(BaseModel):
    id: str
    orgao: Optional[str] = None
    edicao: Optional[str] = None
    data: Optional[str] = None
    secao: Optional[str] = None
    titulo: Optional[str] = None
    ementa: Optional[str] = None
    url_pdf: Optional[str] = None
    pagina: Optional[str] = None
    clean_text: Optional[str] = None
    relevance_reason: Optional[str] = None

class ProcessResponse(BaseModel):
    date: str
    count: int
    whatsapp_text: str
    publications: List[Publicacao]

########################################
# FUNÇÕES AUXILIARES
########################################

async def fetch_dou(date_str: str, secoes: List[str]) -> List[dict]:
    """
    Chama a API do DOU consolidada por matéria.
    """
    params = {
        "data": date_str,
        "secoes": ",".join(secoes),
        "grouped": "true"
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{API_BASE_URL}/dou", params=params)
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Erro ao consultar DOU: {r.text}")
        return r.json()

async def get_ai_analysis(text: str, model: str) -> Optional[str]:
    """
    Faz chamada ao modelo de IA pedindo um resumo rápido de impacto p/ MB.
    Se ocorrer erro, retorna string de erro.
    Se bloqueio/silêncio, retorna None.
    """
    prompt = (
        "Você é analista orçamentário e de pessoal da Marinha do Brasil.\n"
        "Leia o texto a seguir (ato do DOU) e responda em UMA frase curta:\n"
        "- Se há impacto direto para a Marinha do Brasil (orçamento, cargos de FN, movimentação de militares da MB, liberação de crédito, limites de empenho, etc).\n"
        "- Caso não haja impacto direto, responda exatamente: 'Sem impacto direto.'\n\n"
        "Texto:\n"
        f"{text}\n\n"
        "Resposta:"
    )

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Você é um assistente de análise de atos normativos com foco na MB."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 120,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=body,
            )
        if resp.status_code != 200:
            return f"Erro na análise de IA: status {resp.status_code} - {resp.text}"

        data = resp.json()
        # garantir caminho seguro
        ai_msg = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
        )
        if not ai_msg:
            return None

        return ai_msg

    except Exception as e:
        # erro grave de runtime
        return f"Erro GRAVE na análise de IA: {e}"

def build_whatsapp(publicacoes: List[Publicacao], data_ref: str) -> str:
    """
    Usa sua função montar_mensagem_whatsapp já existente para formatar.
    """
    return montar_mensagem_whatsapp(publicacoes, data_ref)

########################################
# ENDPOINT BASE (sem IA)
########################################

@app.get("/processar-inlabs", response_model=ProcessResponse)
async def processar_inlabs(
    data: Optional[str] = Query(None, description="Formato YYYY-MM-DD. Se vazio, usa hoje."),
):
    """
    Pipeline original:
    1. Baixa DOU
    2. Filtra via regras determinísticas (orçamento MB, pessoal FN/MB, etc)
    3. Faz merge de duplicatas
    4. Monta texto WhatsApp
    Sem IA.
    """

    # Data alvo
    if not data:
        data_ref = datetime.now().strftime("%Y-%m-%d")
    else:
        data_ref = data

    secoes = ["1", "2"]
    materias_grouped = await fetch_dou(data_ref, secoes)

    pubs: List[Publicacao] = []
    for materia in materias_grouped:
        pub = process_grouped_materia(materia)
        if pub:
            pubs.append(Publicacao(**pub))

    merged = merge_publications(pubs)

    whatsapp_text = build_whatsapp(merged, data_ref)

    return ProcessResponse(
        date=data_ref,
        count=len(merged),
        whatsapp_text=whatsapp_text,
        publications=merged,
    )

########################################
# ENDPOINT COM IA (corrigido)
########################################

@app.get("/processar-inlabs-ia", response_model=ProcessResponse)
async def processar_inlabs_ia(
    data: Optional[str] = Query(None, description="Formato YYYY-MM-DD. Se vazio, usa hoje."),
    model: Optional[str] = Query(None, description="Modelo IA (opcional, senão usa config)"),
):
    """
    Versão corrigida:
    - Mesmo funil inicial do /processar-inlabs
    - IA NÃO descarta mais publicações.
      Ela só adiciona/ajusta relevance_reason.
    - Protegido contra desalinhamento entre merged_pubs e resultados da IA.
    """

    # Data alvo
    if not data:
        data_ref = datetime.now().strftime("%Y-%m-%d")
    else:
        data_ref = data

    ia_model = model or OPENAI_MODEL
    secoes = ["1", "2"]
    materias_grouped = await fetch_dou(data_ref, secoes)

    # 1) regras determinísticas (Estágio 1)
    pubs_filtradas: List[Publicacao] = []
    for materia in materias_grouped:
        pub = process_grouped_materia(materia)
        if pub:
            pubs_filtradas.append(Publicacao(**pub))

    # 2) merge duplicatas
    merged_pubs = merge_publications(pubs_filtradas)

    # 3) preparar tarefas de IA COM índice, para não desalinharmos
    #    vamos criar uma lista de (idx, coro)
    tasks_indexed = []
    for idx, p in enumerate(merged_pubs):
        if p.clean_text and p.clean_text.strip():
            tasks_indexed.append(
                (
                    idx,
                    get_ai_analysis(p.clean_text, ia_model)
                )
            )
        else:
            # se não tem texto limpo, IA_result = None
            # vamos "simular" isso depois
            tasks_indexed.append(
                (
                    idx,
                    None
                )
            )

    # 4) rodar as tarefas reais de IA de forma assíncrona
    #    precisamos separar: onde é coro e onde é None
    coros = [coro for (_, coro) in tasks_indexed if asyncio.iscoroutine(coro)]
    ai_raw_results = []
    if coros:
        ai_raw_results = await asyncio.gather(*coros, return_exceptions=True)

    # agora vamos reconstruir um dict idx -> resultado_IA
    ai_indexed_map = {}
    ai_iter = iter(ai_raw_results)
    for (idx, maybe_coro) in tasks_indexed:
        if asyncio.iscoroutine(maybe_coro):
            ai_indexed_map[idx] = next(ai_iter)
        else:
            ai_indexed_map[idx] = None  # não tinha texto limpo

    # 5) montar pubs_finais
    pubs_finais: List[Publicacao] = []
    for i, p in enumerate(merged_pubs):
        ai_reason_result = ai_indexed_map.get(i, None)

        # Vamos definir uma reason_final começando pela razão original do estágio 1
        reason_final = p.relevance_reason

        if isinstance(ai_reason_result, Exception):
            # erro grave de runtime na IA
            reason_final = f"Erro GRAVE na análise de IA: {ai_reason_result}"

        elif ai_reason_result is None:
            # sem análise IA (falta texto limpo ou bloqueio)
            # mantemos reason_final como estava
            pass

        elif isinstance(ai_reason_result, str) and ai_reason_result.startswith("Erro na análise de IA:"):
            # erro http / timeout etc
            reason_final = ai_reason_result

        elif isinstance(ai_reason_result, str):
            # Temos uma resposta normal da IA
            # Se a IA disser "Sem impacto direto.", NÃO vamos descartar a publicação.
            # Só não vamos substituir a razão original se a IA disse que não tem impacto.
            if "sem impacto direto" not in ai_reason_result.lower().strip():
                # IA achou impacto -> substitui reason_final
                reason_final = ai_reason_result
            else:
                # IA disse "sem impacto direto."
                # mantemos o reason_final original do estágio 1
                pass

        # Atualiza a publicação com a razão final
        p.relevance_reason = reason_final

        # IMPORTANTE: SEMPRE adiciona a publicação
        pubs_finais.append(p)

    # 6) monta texto whatsapp com pubs_finais
    whatsapp_text = build_whatsapp(pubs_finais, data_ref)

    return ProcessResponse(
        date=data_ref,
        count=len(pubs_finais),
        whatsapp_text=whatsapp_text,
        publications=pubs_finais,
    )

########################################
# ENDPOINT HEALTHCHECK SIMPLES
########################################

@app.get("/ping")
async def ping():
    return {"status": "ok", "time": datetime.now().isoformat()}

