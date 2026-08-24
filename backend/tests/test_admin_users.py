"""
Testes de administração de usuários (Fase 5, tarefa 7). `backend/app/auth/
admin_router.py` expõe create/list/get/edit/set_password/deactivate via HTTP,
atrás de Permission.MANAGE_USERS (só Admin TI, ver docs/spec_rbac.md) — até
esta tarefa, a única forma de provisionar usuário era um script direto no
banco.

Cobertura: sem token (401), com token mas sem a permissão (403), com a
permissão (passa), usuário inexistente (404), duplicidade de username/email
(409), senha fraca (400), e a guarda de não poder desativar a própria conta.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user, get_user_by_username
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


def _token_for(perfil: Role, created_user_ids, client) -> tuple[str, uuid.UUID]:
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"adm_{u}", nome="Usuário Admin Teste",
            email=f"adm_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        user_id = user.id
        created_user_ids.append(user_id)
    finally:
        session.close()

    resp = client.post("/api/auth/login", json={"username": f"adm_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"], user_id


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _novo_usuario_payload() -> dict:
    u = uuid.uuid4().hex[:8]
    return {
        "username": f"prov_{u}",
        "nome": "Usuário Provisionado",
        "email": f"prov_{u}@teste.local",
        "password": "senha_segura_123",
        "perfil": "vendedor",
    }


# --- sem token: 401 em todas as rotas -------------------------------------------

def test_criar_usuario_sem_token_retorna_401(client):
    resp = client.post("/api/auth/users", json=_novo_usuario_payload())
    assert resp.status_code == 401


def test_listar_usuarios_sem_token_retorna_401(client):
    assert client.get("/api/auth/users").status_code == 401


# --- com token, sem a permissão (Vendedor): 403 ---------------------------------

def test_criar_usuario_vendedor_com_token_retorna_403(client, created_user_ids):
    token, _ = _token_for(Role.VENDEDOR, created_user_ids, client)
    resp = client.post("/api/auth/users", json=_novo_usuario_payload(), headers=_auth_header(token))
    assert resp.status_code == 403


def test_listar_usuarios_vendedor_com_token_retorna_403(client, created_user_ids):
    token, _ = _token_for(Role.VENDEDOR, created_user_ids, client)
    resp = client.get("/api/auth/users", headers=_auth_header(token))
    assert resp.status_code == 403


# --- com token, Admin TI: fluxo feliz completo ----------------------------------

def test_admin_cria_lista_e_obtem_usuario(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()

    resp = client.post("/api/auth/users", json=payload, headers=_auth_header(token))
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == payload["username"]
    assert "password_hash" not in body
    created_user_ids.append(uuid.UUID(body["id"]))

    resp = client.get("/api/auth/users", headers=_auth_header(token))
    assert resp.status_code == 200
    assert any(u["username"] == payload["username"] for u in resp.json())

    resp = client.get(f"/api/auth/users/{body['id']}", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["email"] == payload["email"]


def test_admin_edita_usuario(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()
    criado = client.post("/api/auth/users", json=payload, headers=_auth_header(token)).json()
    created_user_ids.append(uuid.UUID(criado["id"]))

    resp = client.patch(
        f"/api/auth/users/{criado['id']}", json={"nome": "Nome Editado", "perfil": "gestor"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 200
    assert resp.json()["nome"] == "Nome Editado"
    assert resp.json()["perfil"] == "gestor"


def test_admin_redefine_senha_e_login_novo_funciona(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()
    criado = client.post("/api/auth/users", json=payload, headers=_auth_header(token)).json()
    created_user_ids.append(uuid.UUID(criado["id"]))

    resp = client.post(
        f"/api/auth/users/{criado['id']}/password", json={"new_password": "nova_senha_456"},
        headers=_auth_header(token),
    )
    assert resp.status_code == 204

    login_velha = client.post("/api/auth/login", json={"username": payload["username"], "password": payload["password"]})
    assert login_velha.status_code == 401

    login_nova = client.post("/api/auth/login", json={"username": payload["username"], "password": "nova_senha_456"})
    assert login_nova.status_code == 200


def test_admin_desativa_usuario(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()
    criado = client.post("/api/auth/users", json=payload, headers=_auth_header(token)).json()
    created_user_ids.append(uuid.UUID(criado["id"]))

    resp = client.post(f"/api/auth/users/{criado['id']}/deactivate", headers=_auth_header(token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "inativo"

    # Usuário desativado não consegue mais logar (comportamento já coberto na
    # tarefa 3 — aqui só confirma que deactivate via API teve efeito real).
    login_resp = client.post("/api/auth/login", json={"username": payload["username"], "password": payload["password"]})
    assert login_resp.status_code == 401


def test_admin_nao_pode_desativar_a_propria_conta(client, created_user_ids):
    token, admin_id = _token_for(Role.ADMIN_TI, created_user_ids, client)
    resp = client.post(f"/api/auth/users/{admin_id}/deactivate", headers=_auth_header(token))
    assert resp.status_code == 400


# --- casos de erro: 404 / 409 / 400 ---------------------------------------------

def test_obter_usuario_inexistente_retorna_404(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    resp = client.get(f"/api/auth/users/{uuid.uuid4()}", headers=_auth_header(token))
    assert resp.status_code == 404


def test_editar_usuario_inexistente_retorna_404(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    resp = client.patch(f"/api/auth/users/{uuid.uuid4()}", json={"nome": "X"}, headers=_auth_header(token))
    assert resp.status_code == 404


def test_desativar_usuario_inexistente_retorna_404(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    resp = client.post(f"/api/auth/users/{uuid.uuid4()}/deactivate", headers=_auth_header(token))
    assert resp.status_code == 404


def test_criar_usuario_com_username_duplicado_retorna_409(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()
    primeiro = client.post("/api/auth/users", json=payload, headers=_auth_header(token))
    assert primeiro.status_code == 201
    created_user_ids.append(uuid.UUID(primeiro.json()["id"]))

    payload["email"] = f"outro_{uuid.uuid4().hex[:8]}@teste.local"
    segundo = client.post("/api/auth/users", json=payload, headers=_auth_header(token))
    assert segundo.status_code == 409


def test_criar_usuario_com_senha_fraca_retorna_400(client, created_user_ids):
    token, _ = _token_for(Role.ADMIN_TI, created_user_ids, client)
    payload = _novo_usuario_payload()
    payload["password"] = "curta"
    resp = client.post("/api/auth/users", json=payload, headers=_auth_header(token))
    assert resp.status_code == 400
    # Confirma que nada foi persistido (senha fraca não deixa lixo no banco)
    session = SessionLocal()
    try:
        assert get_user_by_username(session, payload["username"]) is None
    finally:
        session.close()
