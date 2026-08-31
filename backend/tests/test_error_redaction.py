"""
Ticket 10 do plano de correção (AUD-011): exceções internas (texto bruto,
incluindo o que `str(exception)` produz — pode conter host, porta, provedor,
detalhes de config) vazavam direto pro cliente em 3 pontos: `/api/health`
(público, sem login), `/api/match` (erro genérico) e o evento `error` do
streaming. Corrigido: mensagem genérica pro cliente, detalhe completo só no
log do servidor (`logger.error`/`logger.warning`, já existia nos 3 pontos).
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user
from app.main import app
from app.rag.engine import stream_pu_matcher_agent

SEGREDO = "internal-db-host.corp.local:5432 senha=trocatudo123"


@pytest.fixture
def created_user_ids():
    ids = []
    yield ids
    if ids:
        cleanup = SessionLocal()
        try:
            for user_id in ids:
                user = cleanup.get(User, user_id)
                if user is not None:
                    cleanup.delete(user)
            cleanup.commit()
        finally:
            cleanup.close()


@pytest.fixture
def client():
    return TestClient(app)


def _token(created_user_ids, client):
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"redact_{u}", nome="Usuário Redação",
            email=f"redact_{u}@teste.local", password="senha_segura_123", perfil=Role.VENDEDOR,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()
    resp = client.post("/api/auth/login", json={"username": f"redact_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def test_health_nao_vaza_texto_da_excecao_do_qdrant(client):
    # QdrantClient é importado localmente dentro de health_detailed() (import
    # lazy) — o patch precisa mirar a origem real, não um nome em app.main.
    with patch("qdrant_client.QdrantClient", side_effect=Exception(SEGREDO)):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body_text = str(resp.json())
    assert SEGREDO not in body_text
    assert "offline" in resp.json()["qdrant"].lower()


def test_match_erro_generico_nao_vaza_texto_da_excecao(client, created_user_ids):
    token = _token(created_user_ids, client)
    with patch("app.main.run_pu_matcher_agent", side_effect=Exception(SEGREDO)):
        resp = client.post(
            "/api/match", json={"query": "teste"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 500
    assert SEGREDO not in resp.json()["detail"]


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_stream_erro_generico_nao_vaza_texto_da_excecao(mock_completion, mock_retrieve):
    mock_completion.side_effect = Exception(SEGREDO)

    events = list(stream_pu_matcher_agent(query="teste"))

    error_events = [e for e in events if '"type": "error"' in e]
    assert len(error_events) == 1
    assert SEGREDO not in error_events[0]
