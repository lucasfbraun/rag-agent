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

# Nome da coleção no Qdrant. Configurável desde 2026-09-09 por um motivo
# concreto: restaurar um snapshot cria a coleção com o nome que ela tinha na
# ORIGEM, e o Qdrant não renomeia coleção. Com o nome fixo no código, um
# snapshot restaurado sob outro nome deixava a aplicação reportando "base
# vetorial vazia" — tudo saudável, o dado ali do lado, e nenhuma indicação de
# que era só divergência de nome. Também permite mais de um projeto no mesmo
# Qdrant, que é o caso quando a instância é compartilhada.
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pu_products_catalog")

# text-embedding-3-small (OpenAI) = 1536 dims | gemini-embedding-001 = 3072 dims.
# VECTOR_SIZE PRECISA bater com a dimensão do modelo escolhido, e o modelo precisa
# ser o mesmo na ingestão e na consulta — trocar de embedding obriga a recriar a
# coleção do zero (ver .env.example).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "1536"))

# Alias sempre-atual do Gemini — resistente a descontinuações de versão pontuais
DEFAULT_CHAT_MODEL = "gemini/gemini-flash-latest"

# Modelos de chat oferecidos ao usuário (AUD-005, ticket 4 do plano de
# correção): sem uma allowlist, o cliente podia mandar qualquer string de
# modelo/provedor pro litellm.completion(), sem controle de custo/quota.
#
# A duplicação que existia aqui — esta lista mantida à mão em paralelo ao
# selectbox de frontend/app.py — foi eliminada em 2026-09-09: o frontend
# agora busca em GET /api/models. Ela chegou a cobrar o preço previsto na
# docstring do módulo: remover os modelos Ollama daqui sem reconstruir a
# imagem do frontend deixou a tela oferecendo um modelo que o servidor
# recusava, e toda pergunta virava 422 sem pista do motivo.
#
# A ORDEM importa: é a ordem do seletor na tela, e o primeiro é o que o
# usuário pega por padrão. `gpt-4o-mini` vem primeiro por ser o que está
# validado em uso contínuo neste projeto; os modelos Gemini vêm depois porque
# a chave atual está num tier de quota restrito e o endpoint já devolveu 503
# por sobrecarga em horário comercial.
MODELOS_DE_CHAT_EM_ORDEM = (
    "gpt-4o-mini",
    "gpt-4o",
    "gemini/gemini-flash-latest",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-pro-latest",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "groq/llama-3.3-70b-versatile",
)

# Allowlist de modelos aceitos em MatchRequest.model_name — DERIVADA da tupla
# acima, nunca escrita à mão em paralelo. O frontend também não repete a
# lista: busca em GET /api/models (ver app.main.list_models).
ALLOWED_CHAT_MODELS = frozenset(MODELOS_DE_CHAT_EM_ORDEM)

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
