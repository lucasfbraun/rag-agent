"""
Script para rodar o backend FastAPI localmente, sem Docker.

Pré-requisitos:
    pip install -r requirements.txt
    # Qdrant rodando localmente OU usar QDRANT_HOST=memory (modo in-memory para dev)

Uso:
    # Com Qdrant local (porta padrão 6333):
    python backend/run_local.py

    # Com Qdrant in-memory (não persiste, apenas para testes rápidos):
    QDRANT_HOST=memory python backend/run_local.py

    # Especificando host/porta customizados:
    QDRANT_HOST=192.168.1.10 QDRANT_PORT=6333 python backend/run_local.py
"""
import os
import sys

# Garante que o PYTHONPATH inclua a raiz do projeto
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Carrega .env se existir
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, ".env"))
    print("✅ Variáveis do .env carregadas.")
except ImportError:
    print("⚠️ python-dotenv não instalado. Variáveis de ambiente não foram carregadas do .env.")

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    qdrant_host = os.getenv("QDRANT_HOST", "localhost")

    print(f"\n🚀 Subindo PU Matcher Backend (dev local)")
    print(f"   → API:    http://{host}:{port}")
    print(f"   → Health: http://localhost:{port}/api/health")
    print(f"   → Docs:   http://localhost:{port}/docs")
    print(f"   → Qdrant: {qdrant_host}:{os.getenv('QDRANT_PORT', '6333')}")
    if qdrant_host == "memory":
        print("   ⚠️  Modo in-memory: dados não persistem entre reinicializações.")
    print()

    uvicorn.run(
        "app.main:app",
        app_dir=os.path.join(project_root, "backend"),
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )
