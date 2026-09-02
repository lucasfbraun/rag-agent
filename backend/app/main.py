from fastapi import Depends, FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Literal, Optional
import uuid
from sqlalchemy.orm import Session
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent, RetrievalIndisponivelError
from app.templates import TEMPLATES_DISPONIVEIS
from app.config import (
    QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL,
    DEFAULT_CHAT_MODEL, ALLOWED_CHAT_MODELS,
)
from app.auth.router import router as auth_router
from app.auth.admin_router import router as admin_router
from app.conversation_router import router as conversation_router
from app.auth.permissions import Permission, has_permission, require_permission
from app.models import User
from app.db import get_session
from app.feedback_service import registrar_feedback
from app.conversation_service import (
    ConversationNotFoundError,
    create_conversation,
    get_conversation,
    history_for_agent,
    save_exchange,
)
import logging
import json
import os

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PU Matcher API",
    version="2.1.0",
    description="Agente investigativo RAG para match de produtos de poliuretano"
)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(conversation_router)

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
    conversation_id: Optional[uuid.UUID] = None

    @field_validator("model_name")
    @classmethod
    def _modelo_precisa_estar_na_allowlist(cls, value: str) -> str:
        if value not in ALLOWED_CHAT_MODELS:
            raise ValueError(
                f"Modelo '{value}' não está na lista de modelos aprovados. "
                f"Modelos disponíveis: {sorted(ALLOWED_CHAT_MODELS)}"
            )
        return value

class FeedbackRequest(BaseModel):
    """Avaliação opcional (útil/não útil) de uma resposta do agente já
    exibida na tela — o cliente manda de volta a pergunta e a resposta
    (não um ID de interação, que o backend hoje não gera/rastreia) porque
    isso é tudo que o frontend já tem em mãos no momento do clique."""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=8000)
    answer: str = Field(min_length=1, max_length=20000)
    util: bool
    comentario: Optional[str] = Field(default=None, max_length=2000)
    model_used: Optional[str] = Field(default=None, max_length=100)
    sources: Optional[List[str]] = None


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
def match_product(
    req: MatchRequest,
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    """Executa o agente investigativo para match de produto.

    Campos sensíveis (custos industriais, laudo de homologação completo) são
    liberados às ferramentas MCP conforme a permissão do usuário logado — nunca
    por instrução de prompt (docs/spec_rbac.md, "Campos sensíveis")."""
    try:
        persisted_conversation = None
        history = _history_as_dicts(req)
        if req.conversation_id is not None:
            persisted_conversation = get_conversation(
                session,
                conversation_id=req.conversation_id,
                user_id=current_user.id,
            )
            history = history_for_agent(persisted_conversation)

        res = run_pu_matcher_agent(
            query=req.query,
            template_id=req.template_id,
            model_name=req.model_name,
            history=history,
            ver_custos=has_permission(current_user, Permission.VIEW_COSTS),
            ver_laudo_completo=has_permission(current_user, Permission.VIEW_HOMOLOGATION_FULL),
        )
        conversation = save_exchange(
            session,
            user_id=current_user.id,
            conversation_id=(persisted_conversation.id if persisted_conversation else None),
            query=req.query,
            answer=res["answer"],
            sources=res.get("sources"),
            model_used=res.get("model_used"),
        )
        return {**res, "conversation_id": str(conversation.id)}
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")
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
    req: MatchRequest,
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
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
    try:
        if req.conversation_id is None:
            conversation = create_conversation(session, user_id=current_user.id)
        else:
            conversation = get_conversation(
                session,
                conversation_id=req.conversation_id,
                user_id=current_user.id,
            )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversa não encontrada.")

    generator = stream_pu_matcher_agent(
        query=req.query,
        template_id=req.template_id,
        model_name=req.model_name,
        history=history_for_agent(conversation),
        ver_custos=has_permission(current_user, Permission.VIEW_COSTS),
        ver_laudo_completo=has_permission(current_user, Permission.VIEW_HOMOLOGATION_FULL),
    )

    def persist_and_stream():
        answer_parts = []
        sources = []
        model_used = req.model_name
        failed = False

        for raw_event in generator:
            try:
                event = json.loads(raw_event)
            except (json.JSONDecodeError, TypeError):
                yield raw_event
                continue

            if event.get("type") == "meta":
                sources = event.get("sources") or []
                model_used = event.get("model_used") or req.model_name
                event["conversation_id"] = str(conversation.id)
                yield json.dumps(event) + "\n"
            elif event.get("type") == "delta":
                answer_parts.append(event.get("content", ""))
                yield raw_event
            elif event.get("type") == "error":
                failed = True
                yield raw_event
            elif event.get("type") == "done":
                answer = "".join(answer_parts)
                if not failed and answer:
                    save_exchange(
                        session,
                        user_id=current_user.id,
                        conversation_id=conversation.id,
                        query=req.query,
                        answer=answer,
                        sources=sources,
                        model_used=model_used,
                    )
                yield raw_event
            else:
                yield raw_event

    return StreamingResponse(
        persist_and_stream(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no"}
    )


@app.post("/api/feedback")
def submit_feedback(
    req: FeedbackRequest,
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    """Grava a avaliação (útil/não útil) de uma resposta — mesma permissão
    de /api/match (quem pode perguntar, pode avaliar). Pedido do usuário:
    não é obrigatório dar feedback, mas quando dado (positivo ou negativo)
    precisa ser salvo — o agente consulta o negativo em toda consulta futura
    (app.rag.engine._montar_licoes_str)."""
    try:
        registrar_feedback(
            session,
            user_id=current_user.id,
            query=req.query,
            answer=req.answer,
            util=req.util,
            comentario=req.comentario,
            model_used=req.model_used,
            sources=req.sources,
        )
    except Exception as e:
        logger.error("Erro ao gravar feedback: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Erro interno ao gravar o feedback. Tente novamente em instantes.",
        )
    return {"status": "ok"}


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
