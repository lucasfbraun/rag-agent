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

# QDRANT_HOST no .env e "qdrant" (nome do serviço, só resolvível de DENTRO
# da rede do Docker Compose, pelo container backend). Este script roda no
# host (precisa alcançar a pasta de rede \\10.1.1.205\...), então precisa
# do Qdrant pela porta publicada em localhost — sem isto, getaddrinfo falha
# (achado ao investigar por que --full parou de funcionar do host, Sessão 30).
os.environ["QDRANT_HOST"] = "localhost"

# Mesmo problema com o Ollama: .env tem OLLAMA_API_BASE apontando pra
# "host.docker.internal" (nome especial que só o Docker resolve, pra um
# container alcançar o host) — rodando no host, o Ollama está em localhost.
os.environ["OLLAMA_API_BASE"] = "http://localhost:11434"

# Garante que o modulo app seja encontrado
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

# Barra normal, não invertida: `\\10.1.1.205\...` falha em `os.listdir`/`glob`
# quando este script roda a partir de um shell estilo Git Bash/MSYS (a
# tradução de path do MSYS mexe na string antes de chegar no Python) — achado
# ao tentar recuperar o acervo na Sessão 30. Barra normal funciona igual nos
# dois casos (Windows aceita as duas), sem esse problema.
ACERVO_BASE = "//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto"
ACERVO_TESTE = "//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto/FLEXX® AG"

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
