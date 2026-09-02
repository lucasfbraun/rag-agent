"""Histórico de conversas pela interface HTTP autenticada.

Seam aprovado: FastAPI TestClient + PostgreSQL real. Asserções observam a
conversa somente pelos endpoints públicos, sem consultar tabelas por fora.
"""
import uuid
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth.user_service import create_user
from app.db import SessionLocal
from app.main import app
from app.models import Role, User


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_factory(client):
    user_ids = []

    def create_authenticated_user():
        suffix = uuid.uuid4().hex[:8]
        username = f"conv_{suffix}"
        session = SessionLocal()
        try:
            user = create_user(
                session,
                username=username,
                nome="Usuario Conversas",
                email=f"{username}@teste.local",
                password="senha_segura_123",
                perfil=Role.VENDEDOR,
            )
            session.commit()
            user_id = user.id
            user_ids.append(user_id)
        finally:
            session.close()

        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": "senha_segura_123"},
        )
        return user_id, {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

    yield create_authenticated_user

    cleanup = SessionLocal()
    try:
        for user_id in user_ids:
            user = cleanup.get(User, user_id)
            if user is not None:
                cleanup.delete(user)
        cleanup.commit()
    finally:
        cleanup.close()


def test_usuario_cria_conversa_e_consegue_recupera_la(client, auth_factory):
    _, headers = auth_factory()

    created = client.post("/api/conversations", headers=headers)

    assert created.status_code == 201
    conversation = created.json()
    assert conversation["title"] == "Nova conversa"
    assert conversation["messages"] == []

    retrieved = client.get(
        f"/api/conversations/{conversation['id']}", headers=headers
    )
    assert retrieved.status_code == 200
    assert retrieved.json() == conversation


def test_listagem_traz_so_conversas_do_usuario_mais_recentes_primeiro(
    client, auth_factory
):
    _, owner_headers = auth_factory()
    _, other_headers = auth_factory()
    first = client.post("/api/conversations", headers=owner_headers).json()
    second = client.post("/api/conversations", headers=owner_headers).json()
    client.post("/api/conversations", headers=other_headers)

    response = client.get("/api/conversations", headers=owner_headers)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [second["id"], first["id"]]


def test_so_proprietario_pode_ver_e_apagar_conversa(client, auth_factory):
    _, owner_headers = auth_factory()
    _, other_headers = auth_factory()
    conversation = client.post("/api/conversations", headers=owner_headers).json()
    url = f"/api/conversations/{conversation['id']}"

    assert client.get(url, headers=other_headers).status_code == 404
    assert client.delete(url, headers=other_headers).status_code == 404

    deleted = client.delete(url, headers=owner_headers)
    assert deleted.status_code == 204
    assert client.get(url, headers=owner_headers).status_code == 404


def test_match_persiste_pergunta_resposta_e_metadados_na_conversa(
    client, auth_factory
):
    _, headers = auth_factory()
    conversation = client.post("/api/conversations", headers=headers).json()
    query = "Preciso de produto para colchao"

    with patch("app.main.run_pu_matcher_agent") as agent:
        agent.return_value = {
            "answer": "Use o produto FLEXX TESTE.",
            "sources": ["Boletim Teste.pdf"],
            "model_used": "gpt-4o-mini",
        }
        response = client.post(
            "/api/match",
            json={"query": query, "conversation_id": conversation["id"]},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation["id"]
    saved = client.get(
        f"/api/conversations/{conversation['id']}", headers=headers
    ).json()
    assert saved["title"] == query
    assert [message["role"] for message in saved["messages"]] == [
        "user",
        "assistant",
    ]
    assert saved["messages"][0]["content"] == query
    assert saved["messages"][1]["content"] == "Use o produto FLEXX TESTE."
    assert saved["messages"][1]["sources"] == ["Boletim Teste.pdf"]
    assert saved["messages"][1]["model_used"] == "gpt-4o-mini"


def test_match_stream_persiste_resposta_completa_apos_done(client, auth_factory):
    _, headers = auth_factory()
    conversation = client.post("/api/conversations", headers=headers).json()
    events = iter(
        [
            json.dumps(
                {
                    "type": "meta",
                    "sources": ["Boletim Stream.pdf"],
                    "model_used": "gpt-4o-mini",
                }
            )
            + "\n",
            json.dumps({"type": "delta", "content": "Resposta "}) + "\n",
            json.dumps({"type": "delta", "content": "completa."}) + "\n",
            json.dumps({"type": "done"}) + "\n",
        ]
    )

    with patch("app.main.stream_pu_matcher_agent", return_value=events):
        response = client.post(
            "/api/match/stream",
            json={
                "query": "Consulta por streaming",
                "conversation_id": conversation["id"],
            },
            headers=headers,
        )

    assert response.status_code == 200
    streamed_events = [json.loads(line) for line in response.text.splitlines()]
    assert streamed_events[0]["conversation_id"] == conversation["id"]

    saved = client.get(
        f"/api/conversations/{conversation['id']}", headers=headers
    ).json()
    assert saved["messages"][1]["content"] == "Resposta completa."
    assert saved["messages"][1]["sources"] == ["Boletim Stream.pdf"]
    assert saved["messages"][1]["model_used"] == "gpt-4o-mini"


def test_match_stream_com_erro_nao_persiste_resposta_parcial(client, auth_factory):
    _, headers = auth_factory()
    conversation = client.post("/api/conversations", headers=headers).json()
    events = iter(
        [
            json.dumps(
                {"type": "meta", "sources": [], "model_used": "gpt-4o-mini"}
            )
            + "\n",
            json.dumps({"type": "delta", "content": "resposta incompleta"})
            + "\n",
            json.dumps({"type": "error", "message": "falha do provedor"})
            + "\n",
            json.dumps({"type": "done"}) + "\n",
        ]
    )

    with patch("app.main.stream_pu_matcher_agent", return_value=events):
        response = client.post(
            "/api/match/stream",
            json={"query": "consulta que falha", "conversation_id": conversation["id"]},
            headers=headers,
        )

    assert response.status_code == 200
    saved = client.get(
        f"/api/conversations/{conversation['id']}", headers=headers
    ).json()
    assert saved["messages"] == []
