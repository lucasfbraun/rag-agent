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

# Allowlist de modelos aceitos em MatchRequest.model_name (AUD-005, ticket 4
# do plano de correção) — sem isso, o cliente podia mandar qualquer string de
# modelo/provedor pro litellm.completion(), sem controle de custo/quota.
# Espelha as opções do selectbox em frontend/app.py; duplicação conhecida,
# mesma categoria de risco que já causou bugs de divergência neste projeto
# (ver docstring do módulo) — não unificado agora porque o frontend roda num
# processo Python separado, sem import de app.config.
ALLOWED_CHAT_MODELS = frozenset({
    "ollama/qwen2.5:3b",
    "ollama/qwen2.5:7b",
    "gemini/gemini-flash-latest",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-pro-latest",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "groq/llama-3.3-70b-versatile",
})

# Banco relacional (Fase 5 — RBAC & Governança). Usuários/perfis, separado do Qdrant (vetorial).
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
POSTGRES_DB = os.getenv("POSTGRES_DB", "pu_matcher")
POSTGRES_USER = os.getenv("POSTGRES_USER", "pu_matcher")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
if not POSTGRES_PASSWORD:
    raise RuntimeError(
        "POSTGRES_PASSWORD não definida — configure no .env antes de iniciar a aplicação "
        "(sem isso a conexão com o banco de usuários seria feita com senha em branco)."
    )
DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Autenticação (Fase 5, tarefa 3). Assina o token de sessão — mesmo SECRET_KEY que já
# existia no .env sem uso; reaproveitado em vez de criar um segundo segredo.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY não definida — configure no .env antes de iniciar a aplicação "
        "(sem isso os tokens de sessão seriam assinados com uma chave previsível)."
    )
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 480))  # 8h, um turno
