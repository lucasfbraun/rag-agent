"""
Testes de proteção dos endpoints existentes (Fase 5, tarefa 5). Confirma que
require_permission() está de fato aplicado a /api/match, /api/match/stream,
/api/templates e /api/ingest — sem token, 401; com token mas sem a permissão
certa, 403; com token e permissão, passa.

A lógica de negócio (agente RAG, ingestão) é mockada de propósito: esta tarefa
testa a PORTA de autorização, não o motor por trás dela (já coberto em outras
sessões/manualmente) — evita depender do LLM/Ollama, que este projeto já viu
ser lento/instável nesta máquina.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user
from app.main import app


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
            session, username=f"prot_{u}", nome="Usuário Proteção",
            email=f"prot_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    login_client = TestClient(app)
    resp = login_client.post("/api/auth/login", json={"username": f"prot_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- sem token: 401 em todos os endpoints de negócio ---------------------------

def test_match_sem_token_retorna_401(client):
    resp = client.post("/api/match", json={"query": "teste"})
    assert resp.status_code == 401


def test_match_stream_sem_token_retorna_401(client):
    resp = client.post("/api/match/stream", json={"query": "teste"})
    assert resp.status_code == 401


def test_templates_sem_token_retorna_401(client):
    resp = client.get("/api/templates")
    assert resp.status_code == 401


def test_ingest_sem_token_retorna_401(client):
    resp = client.post("/api/ingest", json={"dir_path": "/app/data/raw_documents"})
    assert resp.status_code == 401


# --- endpoints públicos continuam públicos (não regrediu) ----------------------

def test_raiz_continua_publica(client):
    assert client.get("/").status_code == 200


def test_health_continua_publico(client):
    assert client.get("/api/health").status_code == 200


# --- com token, perfil com a permissão: passa -----------------------------------

def test_templates_vendedor_com_token_retorna_200(client, created_user_ids):
    token = _token_for(Role.VENDEDOR, created_user_ids)
    resp = client.get("/api/templates", headers=_auth_header(token))
    assert resp.status_code == 200


def test_match_vendedor_com_token_chama_o_agente(client, created_user_ids):
    """VIEW_CATALOG é concedida aos 5 perfis — Vendedor deve passar da autorização
    e chegar até o agente (mockado aqui, não testando a lógica do RAG em si)."""
    token = _token_for(Role.VENDEDOR, created_user_ids)
    with patch("app.main.run_pu_matcher_agent") as mock_agent:
        mock_agent.return_value = {"answer": "resposta mockada", "sources": [], "model_used": "mock"}
        resp = client.post("/api/match", json={"query": "teste"}, headers=_auth_header(token))
    assert resp.status_code == 200


def test_match_stream_vendedor_com_token_chama_o_agente(client, created_user_ids):
    """Mesma checagem que test_match_vendedor..., mas pra rota de streaming — só
    testar o 401 não bastava (achado do code review): faltava confirmar que quem
    TEM a permissão também consegue de fato passar pela autorização nessa rota."""
    token = _token_for(Role.VENDEDOR, created_user_ids)
    with patch("app.main.stream_pu_matcher_agent") as mock_stream:
        mock_stream.return_value = iter(['{"type": "delta", "content": "mock"}\n'])
        resp = client.post("/api/match/stream", json={"query": "teste"}, headers=_auth_header(token))
    assert resp.status_code == 200
    mock_stream.assert_called_once()


# --- com token, perfil SEM a permissão: 403 -------------------------------------

def test_ingest_vendedor_com_token_retorna_403(client, created_user_ids):
    """MANAGE_INGESTION só existe para Admin TI — Vendedor tem token válido mas
    não tem a permissão, deve ser barrado antes de disparar qualquer ingestão."""
    token = _token_for(Role.VENDEDOR, created_user_ids)
    with patch("app.rag.ingestion.ingest_catalog_directory") as mock_ingest:
        resp = client.post("/api/ingest", json={"dir_path": "/app/data/raw_documents"}, headers=_auth_header(token))
    assert resp.status_code == 403
    mock_ingest.assert_not_called()


def test_ingest_admin_com_token_retorna_200(client, created_user_ids):
    token = _token_for(Role.ADMIN_TI, created_user_ids)
    with patch("app.rag.ingestion.ingest_catalog_directory") as mock_ingest:
        resp = client.post("/api/ingest", json={"dir_path": "/app/data/raw_documents"}, headers=_auth_header(token))
    assert resp.status_code == 200
    mock_ingest.assert_called_once()
