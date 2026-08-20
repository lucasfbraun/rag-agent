"""
Script para rodar o frontend Streamlit localmente, sem Docker.

Pré-requisitos:
    pip install -r requirements.txt
    # Backend deve estar rodando em localhost:8000 (via backend/run_local.py ou Docker)

Uso:
    python frontend/run_local.py

    # Apontando para backend em outro host:
    BACKEND_URL=http://192.168.1.10:8000 python frontend/run_local.py
"""
import os
import sys
import subprocess

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Define LOCAL_DEV para o app.py saber usar localhost em vez de 'backend' (nome Docker)
env = os.environ.copy()
env["LOCAL_DEV"] = "true"

backend_url = os.getenv("BACKEND_URL", "http://localhost:8000")
frontend_script = os.path.join(project_root, "frontend", "app.py")

port = int(os.getenv("FRONTEND_PORT", "8501"))

print(f"\n🚀 Subindo PU Matcher Frontend (dev local)")
print(f"   → UI:      http://localhost:{port}")
print(f"   → Backend: {backend_url}")
print()

subprocess.run([
    sys.executable, "-m", "streamlit", "run",
    frontend_script,
    f"--server.port={port}",
    "--server.address=0.0.0.0",
    "--server.headless=true"
], env=env)
