from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Literal, Optional
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent, RetrievalIndisponivelError
from app.templates import TEMPLATES_DISPONIVEIS
from app.config import (
    QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL,
    DEFAULT_CHAT_MODEL, ALLOWED_CHAT_MODELS,
)
from app.auth.router import router as auth_router
from app.auth.admin_router import router as admin_router
from app.auth.permissions import Permission, has_permission, require_permission
from app.models import User
import logging
import os

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PU Matcher API",
    version="2.1.0",
    description="Agente investigativo RAG para match de produtos de poliuretano"
)
app.include_router(auth_router)
app.include_router(admin_router)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class HistoryMessage(BaseModel):
    """Mensagem de histórico aceita em MatchRequest.history (AUD-005, ticket
    4). `extra="forbid"` rejeita qualquer campo fora de role/content — sem
    isso um cliente podia anexar chaves arbitrárias (ex: `name`) que alguns
    provedores de LLM interpretam de formas não previstas aqui."""
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class MatchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    template_id: str = "proposta_tecnica_completa"
    model_name: str = DEFAULT_CHAT_MODEL
    history: Optional[List[HistoryMessage]] = []

    @field_validator("model_name")
    @classmethod
    def _modelo_precisa_estar_na_allowlist(cls, value: str) -> str:
        if value not in ALLOWED_CHAT_MODELS:
            raise ValueError(
                f"Modelo '{value}' não está na lista de modelos aprovados. "
                f"Modelos disponíveis: {sorted(ALLOWED_CHAT_MODELS)}"
            )
        return value

class IngestRequest(BaseModel):
    dir_path: str = "/app/data/raw_documents"
    embedding_model: str = EMBEDDING_MODEL

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# "/" e "/api/health" ficam deliberadamente públicos (sem require_permission) —
# são liveness/monitoramento (Docker healthcheck usa "/"), não expõem dado de
# negócio, e ferramentas de infra não têm login. Todo o resto abaixo exige
# autenticação + a permissão certa, via docs/spec_rbac.md.

@app.get("/")
def health_simple():
    return {"status": "online", "service": "PU Matcher - Product Match & Consultative Sales", "version": "2.1.0"}


@app.get("/api/health")
def health_detailed():
    """
    Health check detalhado: verifica conectividade com o Qdrant e status da coleção.
    Útil para monitoramento, liveness probe do Docker e debug de ambiente.
    """
    qdrant_status = "unknown"
    collection_info = {}

    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=5)

        collections = client.get_collections().collections
        qdrant_status = "online"

        col_names = [c.name for c in collections]
        if COLLECTION_NAME in col_names:
            col_info = client.get_collection(COLLECTION_NAME)
            collection_info = {
                "name": COLLECTION_NAME,
                "points_count": col_info.points_count,
                "status": str(col_info.status)
            }
        else:
            collection_info = {
                "name": COLLECTION_NAME,
                "points_count": 0,
                "status": "não inicializada — execute a ingestão"
            }

    except Exception as e:
        # /api/health é público (AUD-011, ticket 10) — texto bruto da exceção
        # (host, porta, detalhe de config) fica só no log, nunca na resposta.
        qdrant_status = "offline"
        logger.warning("Qdrant inacessível no health check: %s", e)

    return {
        "api": "online",
        "qdrant": qdrant_status,
        "collection": collection_info
    }


def _history_as_dicts(req: MatchRequest) -> list[dict]:
    """engine.py monta `messages` do litellm esperando dicts {"role", "content"}
    — MatchRequest.history chega como List[HistoryMessage] (validado, ticket 4),
    então precisa virar dict de novo antes de entrar na conversa do LLM."""
    return [m.model_dump() for m in (req.history or [])]


@app.get("/api/templates", dependencies=[Depends(require_permission(Permission.SELECT_TEMPLATE))])
def list_templates():
    """Lista todos os templates cadastrados no sistema."""
    return TEMPLATES_DISPONIVEIS


@app.post("/api/match")
def match_product(req: MatchRequest, current_user: User = Depends(require_permission(Permission.VIEW_CATALOG))):
    """Executa o agente investigativo para match de produto.

    Campos sensíveis (custos industriais, laudo de homologação completo) são
    liberados às ferramentas MCP conforme a permissão do usuário logado — nunca
    por instrução de prompt (docs/spec_rbac.md, "Campos sensíveis")."""
    try:
        res = run_pu_matcher_agent(
            query=req.query,
            template_id=req.template_id,
            model_name=req.model_name,
            history=_history_as_dicts(req),
            ver_custos=has_permission(current_user, Permission.VIEW_COSTS),
            ver_laudo_completo=has_permission(current_user, Permission.VIEW_HOMOLOGATION_FULL),
        )
        return res
    except RetrievalIndisponivelError as e:
        logger.error("Catálogo indisponível em /api/match: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Catálogo de produtos indisponível no momento. Tente novamente em instantes.",
        )
    except Exception as e:
        # Texto bruto da exceção fica só no log (AUD-011, ticket 10) — pode
        # conter detalhe interno (provedor, host, config) que não é pro cliente.
        logger.error("Erro no agente PU Matcher: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao processar a solicitação. Tente novamente em instantes.",
        )


@app.post("/api/match/stream")
def match_product_stream(
    req: MatchRequest, current_user: User = Depends(require_permission(Permission.VIEW_CATALOG))
):
    """
    Versão streaming do agente (Server-Sent Events).
    Cada linha do response body é um objeto JSON com campos:
      - {"type": "meta", "sources": [...], "model_used": "..."}
      - {"type": "delta", "content": "..."}
      - {"type": "done"}
      - {"type": "error", "message": "..."}

    Repassa `ver_custos`/`ver_laudo_completo` pro RAG e pras ferramentas MCP
    (esta versão agora também resolve tool calling antes de streamar a
    resposta final, ver docstring de stream_pu_matcher_agent) — mesmo
    contrato de `/api/match`. Precisou trocar `dependencies=[...]` por
    injeção real de `current_user`, igual `/api/match` já fazia.
    """
    generator = stream_pu_matcher_agent(
        query=req.query,
        template_id=req.template_id,
        model_name=req.model_name,
        history=_history_as_dicts(req),
        ver_custos=has_permission(current_user, Permission.VIEW_COSTS),
        ver_laudo_completo=has_permission(current_user, Permission.VIEW_HOMOLOGATION_FULL),
    )
    return StreamingResponse(
        generator,
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"}
    )


@app.post("/api/ingest", dependencies=[Depends(require_permission(Permission.MANAGE_INGESTION))])
def trigger_ingest(req: IngestRequest, background_tasks: BackgroundTasks):
    """
    Dispara a ingestão do diretório de documentos em background.
    Útil para reindexar via API sem precisar de acesso ao shell do container.
    """
    import os
    if not os.path.isdir(req.dir_path):
        raise HTTPException(
            status_code=400,
            detail=f"Diretório não encontrado: {req.dir_path}"
        )

    def run_ingest():
        from app.rag.ingestion import ingest_catalog_directory
        try:
            ingest_catalog_directory(req.dir_path, req.embedding_model)
        except Exception as e:
            logger.error("Erro na ingestão via API: %s", e, exc_info=True)

    background_tasks.add_task(run_ingest)
    return {
        "status": "ingestão iniciada em background",
        "dir_path": req.dir_path,
        "embedding_model": req.embedding_model,
        "message": "Acompanhe os logs do container para progresso. Use GET /api/health para verificar o status da coleção após a ingestão."
    }
