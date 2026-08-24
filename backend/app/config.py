"""
Configuração central do backend — fonte única da verdade para conexões e modelos padrão.

Antes desta extração, QDRANT_HOST/QDRANT_PORT/COLLECTION_NAME/EMBEDDING_MODEL estavam
duplicados como constantes de módulo em ingestion.py e engine.py (e o modelo de chat
padrão hardcoded em main.py + duas funções de engine.py), o que já causou bugs reais
de divergência quando os modelos precisaram trocar (ver PROGRESS.md).
"""
import os

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = "pu_products_catalog"

# gemini-embedding-001 (Google/Gemini) = 3072 dims | ollama/nomic-embed-text (local) = 768 dims
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini/gemini-embedding-001")
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "3072"))

# Alias sempre-atual do Gemini — resistente a descontinuações de versão pontuais
DEFAULT_CHAT_MODEL = "gemini/gemini-flash-latest"
