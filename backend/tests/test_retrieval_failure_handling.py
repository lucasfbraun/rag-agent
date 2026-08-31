"""
Ticket 2 (AUD-003), continuação: agora que retrieve_products_context() levanta
RetrievalIndisponivelError em vez de engolir a falha, os dois chamadores
precisam decidir o que fazer com ela — não deixar propagar como 500 genérico
(síncrono) nem quebrar o stream sem aviso (streaming).
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user
from app.main import app
from app.rag.engine import RetrievalIndisponivelError, stream_pu_matcher_agent


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


def _token_for(perfil: Role, created_user_ids) -> str:
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"retr_{u}", nome="Usuário Retrieval",
            email=f"retr_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    login_client = TestClient(app)
    resp = login_client.post("/api/auth/login", json={"username": f"retr_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def test_match_retorna_503_quando_catalogo_esta_indisponivel(client, created_user_ids):
    token = _token_for(Role.VENDEDOR, created_user_ids)
    with patch("app.main.run_pu_matcher_agent", side_effect=RetrievalIndisponivelError("qdrant fora do ar")):
        resp = client.post(
            "/api/match", json={"query": "teste"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 503
    # Mensagem genérica pro cliente — não vaza "qdrant fora do ar" (mesma
    # disciplina que o ticket 10/AUD-011 vai aplicar aos outros erros).
    assert "qdrant" not in resp.json()["detail"].lower()


def test_stream_emite_evento_de_erro_e_para_sem_chamar_o_llm_quando_catalogo_indisponivel():
    with patch("app.rag.engine.retrieve_products_context", side_effect=RetrievalIndisponivelError("timeout")), \
         patch("app.rag.engine.litellm.completion") as mock_completion:
        events = list(stream_pu_matcher_agent(query="teste"))

    mock_completion.assert_not_called()
    assert len(events) == 2
    assert '"type": "error"' in events[0]
    assert '"type": "done"' in events[1]
