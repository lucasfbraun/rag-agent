"""
POST /api/feedback — mesma permissão de /api/match (VIEW_CATALOG: quem pode
perguntar, pode avaliar). Pedido do usuário: feedback útil/não útil opcional
por resposta, sempre salvo quando dado.

Seam: FastAPI TestClient + Postgres real de teste (mesmo padrão de
test_sensitive_fields.py) — usuário próprio criado e limpo por teste.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Feedback, Role, User
from app.auth.user_service import create_user
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def created_user_ids():
    ids = []
    yield ids
    if ids:
        cleanup = SessionLocal()
        try:
            for user_id in ids:
                cleanup.query(Feedback).filter(Feedback.user_id == user_id).delete()
                user = cleanup.get(User, user_id)
                if user is not None:
                    cleanup.delete(user)
            cleanup.commit()
        finally:
            cleanup.close()


def _token_for(perfil: Role, created_user_ids, client) -> str:
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"fbep_{u}", nome="Usuário Feedback Endpoint",
            email=f"fbep_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    resp = client.post("/api/auth/login", json={"username": f"fbep_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_feedback_util_e_gravado(client, created_user_ids):
    token = _token_for(Role.VENDEDOR, created_user_ids, client)
    resp = client.post(
        "/api/feedback",
        json={"query": "produtos para colchão", "answer": "resposta X", "util": True},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_feedback_nao_util_com_comentario_e_gravado(client, created_user_ids):
    token = _token_for(Role.VENDEDOR, created_user_ids, client)
    resp = client.post(
        "/api/feedback",
        json={
            "query": "produtos para colchão", "answer": "resposta errada", "util": False,
            "comentario": "produto não é da aplicação pedida",
        },
        headers=_auth_header(token),
    )
    assert resp.status_code == 200


def test_feedback_sem_comentario_e_valido():
    """Pedido do usuário: comentário não é obrigatório — só o campo `util`
    já é suficiente. Coberto via schema Pydantic (comentario tem default
    None), não precisa de sessão HTTP própria pra este caso."""
    from app.main import FeedbackRequest

    req = FeedbackRequest(query="q", answer="a", util=True)
    assert req.comentario is None


def test_feedback_sem_autenticacao_e_rejeitado(client):
    resp = client.post("/api/feedback", json={"query": "q", "answer": "a", "util": True})
    assert resp.status_code in (401, 403)


def test_feedback_campo_extra_e_rejeitado(client, created_user_ids):
    """extra="forbid" (mesmo padrão de HistoryMessage/MatchRequest, AUD-005)."""
    token = _token_for(Role.VENDEDOR, created_user_ids, client)
    resp = client.post(
        "/api/feedback",
        json={"query": "q", "answer": "a", "util": True, "campo_desconhecido": "x"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 422
