# Script de ingestao local - aponta para a pasta de rede e usa Qdrant do Docker
# Uso: python ingest_network.py [--test] [--full]
#   --test : indexa apenas FLEXXI AG para validar o pipeline
#   --full : indexa todo o acervo

import sys
import os

# Garante saida UTF-8 no Windows
sys.stdout.reconfigure(encoding="utf-8")

# Carrega .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Garante que o modulo app seja encontrado
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

ACERVO_BASE = r"\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentacao de Produto"
ACERVO_TESTE = r"\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentação de Produto\FLEXXI® AG"

from app.rag.ingestion import ingest_catalog_directory

mode = "--test"
if len(sys.argv) > 1:
    mode = sys.argv[1]

if mode == "--test":
    print("\n[TESTE] Indexando apenas: FLEXXI AG")
    print(f"   Pasta: {ACERVO_TESTE}\n")
    ingest_catalog_directory(ACERVO_TESTE)

elif mode == "--full":
    print("\n[COMPLETO] Indexando todo o acervo")
    print(f"   Pasta: {ACERVO_BASE}")
    print("   ATENCAO: Isso pode levar 3-6 horas para ~12k arquivos.\n")
    confirm = input("Confirmar? (s/N): ")
    if confirm.lower() == "s":
        ingest_catalog_directory(ACERVO_BASE)
    else:
        print("Cancelado.")

else:
    print("Uso: python ingest_network.py [--test|--full]")
    print("  --test  Indexa apenas FLEXXI AG (validacao rapida)")
    print("  --full  Indexa todo o acervo (~12k arquivos, 3-6h)")
