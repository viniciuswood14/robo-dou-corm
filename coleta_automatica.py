import asyncio
import json
import os
from orcamentobr import despesa_detalhada

# Mapeamento do PAC
PROGRAMAS_ACOES_PAC = {
    'PROSUB': {'123G': 'ESTALEIRO', '123H': 'SUB-NUCLEAR', '123I': 'SUB-CONVENCIONAL'},
    'PNM': {'14T7': 'TEC-NUCLEAR'}, 
    'PRONAPA': {'1N47': 'NAVIOS-PATRULHA'}
}

async def main():
    print(">>> Iniciando Coleta Automática SIOP (Via GitHub Actions)...")
    cache = {}
    
    # Vamos pegar 2025 e 2026
    anos = [2025, 2026]
    
    for ano in anos:
        print(f"--- Processando Ano {ano} ---")
        for prog, acoes in PROGRAMAS_ACOES_PAC.items():
            for acao_cod in acoes.keys():
                try:
                    # AQUI ESTÁ A BIBLIOTECA QUE O SENHOR QUER
                    # O GitHub Actions geralmente tem IPs que passam pelo bloqueio
                    df = await asyncio.to_thread(
                        despesa_detalhada, 
                        exercicio=ano, 
                        acao=acao_cod, 
                        inclui_descricoes=True,
                        ignore_secure_certificate=True
                    )
                    
                    if df.empty: continue

                    # Lógica de Soma (igual ao seu api.py)
                    cols = ['loa', 'loa_mais_credito', 'empenhado', 'liquidado', 'pago', 'dotacao_disponivel']
                    valid_cols = [c for c in cols if c in df.columns]
                    totais = df[valid_cols].sum().to_dict()
                    totais['Acao_cod'] = acao_cod
                    
                    if 'dotacao_disponivel' not in totais:
                         totais['dotacao_disponivel'] = totais.get('loa_mais_credito', 0) - totais.get('empenhado', 0)

                    cache[acao_cod] = totais
                    print(f"✅ {acao_cod}: Sucesso")
                    
                except Exception as e:
                    print(f"❌ {acao_cod}: {e}")

        # Salva um arquivo JSON por ano
        arquivo = f"pac_cache_{ano}.json"
        with open(arquivo, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)

if __name__ == "__main__":
    asyncio.run(main())
