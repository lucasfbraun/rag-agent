"""
Ticket 6, continuação: a permissão de custos (`Permission.VIEW_COSTS`, já
computada em app.main) precisa chegar até `retrieve_products_context()` — não
só até as ferramentas MCP como antes. `run_pu_matcher_agent` já recebia
`ver_custos`; `stream_pu_matcher_agent` não recebia nada (o streaming não
chamava tools, então nunca precisou de permissão — mas agora TAMBÉM faz RAG,
que precisa do filtro). `/api/match/stream` precisou passar a injetar
`current_user` (antes só usava `dependencies=[...]`, sem o objeto de volta)
pra poder calcular a permissão, igual `/api/match` já fazia.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent


def _final_completion(answer_text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.content = answer_text
    return resp


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_run_pu_matcher_agent_repassa_ver_custos_como_incluir_sensivel(mock_completion, mock_retrieve):
    mock_completion.return_value = _final_completion("ok")

    run_pu_matcher_agent(query="teste", ver_custos=True)

    mock_retrieve.assert_called_once()
    assert mock_retrieve.call_args.kwargs["incluir_sensivel"] is True


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_run_pu_matcher_agent_sem_ver_custos_nao_inclui_sensivel(mock_completion, mock_retrieve):
    mock_completion.return_value = _final_completion("ok")

    run_pu_matcher_agent(query="teste")  # ver_custos default False

    assert mock_retrieve.call_args.kwargs["incluir_sensivel"] is False


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_stream_pu_matcher_agent_aceita_e_repassa_ver_custos(mock_completion, mock_retrieve):
    mock_completion.return_value = iter([])  # stream vazio, só interessa a chamada de retrieve

    list(stream_pu_matcher_agent(query="teste", ver_custos=True))

    assert mock_retrieve.call_args.kwargs["incluir_sensivel"] is True


# --- /api/match/stream agora injeta current_user e calcula a permissão -----

import uuid  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.auth.user_service import create_user  # noqa: E402
from app.main import app  # noqa: E402


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
            session, username=f"sensrag_{u}", nome="Usuário Sens RAG",
            email=f"sensrag_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    login_client = TestClient(app)
    resp = login_client.post("/api/auth/login", json={"username": f"sensrag_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def test_match_stream_gestor_chama_stream_agent_com_ver_custos_true(client, created_user_ids):
    token = _token_for(Role.GESTOR, created_user_ids)
    with patch("app.main.stream_pu_matcher_agent") as mock_stream:
        mock_stream.return_value = iter(['{"type": "delta", "content": "mock"}\n'])
        resp = client.post(
            "/api/match/stream", json={"query": "teste"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert mock_stream.call_args.kwargs["ver_custos"] is True


def test_match_stream_vendedor_chama_stream_agent_com_ver_custos_false(client, created_user_ids):
    token = _token_for(Role.VENDEDOR, created_user_ids)
    with patch("app.main.stream_pu_matcher_agent") as mock_stream:
        mock_stream.return_value = iter(['{"type": "delta", "content": "mock"}\n'])
        resp = client.post(
            "/api/match/stream", json={"query": "teste"}, headers={"Authorization": f"Bearer {token}"}
        )
    assert resp.status_code == 200
    assert mock_stream.call_args.kwargs["ver_custos"] is False
