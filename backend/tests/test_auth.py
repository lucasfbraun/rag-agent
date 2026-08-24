"""
Testes de autenticação (Fase 5, tarefa 3): token JWT, authenticate() e os
endpoints /api/auth/login e /api/auth/me. Integração contra Postgres real,
mesmo padrão de test_models.py/test_user_service.py.
"""
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import SECRET_KEY
from app.db import SessionLocal
from app.models import Role, User
from app.auth.token import create_access_token, decode_access_token, TokenInvalidoError
from app.auth.user_service import (
    create_user,
    deactivate_user,
    authenticate,
    AutenticacaoInvalidaError,
    UsuarioInativoError,
)
from app.main import app


@pytest.fixture
def session(created_user_ids):
    """Os testes deste arquivo precisam commitar (TestClient usa uma sessão HTTP
    separada) — diferente de test_models.py/test_user_service.py, que só usam
    flush()+rollback(). Por isso a limpeza aqui é por DELETE explícito no teardown
    (created_user_ids), não por rollback."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


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


def _make_user(session, created_user_ids, **overrides):
    u = uuid.uuid4().hex[:8]
    defaults = dict(
        username=f"auth_{u}", nome="Usuário Auth", email=f"auth_{u}@teste.local",
        password="senha_segura_123", perfil=Role.VENDEDOR,
    )
    defaults.update(overrides)
    user = create_user(session, **defaults)
    session.commit()  # TestClient faz requisição HTTP real; precisa estar commitado pra outra sessão enxergar
    created_user_ids.append(user.id)
    return user, defaults["password"]


# --- token.py ----------------------------------------------------------------

def test_create_e_decode_token_fazem_round_trip():
    user_id = uuid.uuid4()
    token = create_access_token(user_id)
    assert decode_access_token(token) == user_id


def test_decode_token_expirado_levanta_erro():
    payload = {"sub": str(uuid.uuid4()), "exp": time.time() - 60}  # expirou há 1 min
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    with pytest.raises(TokenInvalidoError):
        decode_access_token(token)


def test_decode_token_com_assinatura_errada_levanta_erro():
    payload = {"sub": str(uuid.uuid4()), "exp": time.time() + 3600}
    token = jwt.encode(payload, "chave_errada_diferente_da_configurada", algorithm="HS256")
    with pytest.raises(TokenInvalidoError):
        decode_access_token(token)


def test_decode_token_malformado_levanta_erro():
    with pytest.raises(TokenInvalidoError):
        decode_access_token("isto.nao.e.um.jwt.valido")


# --- authenticate() (user_service.py) -----------------------------------------

def test_authenticate_com_credenciais_corretas(session, created_user_ids):
    user, password = _make_user(session, created_user_ids)
    result = authenticate(session, user.username, password)
    assert result.id == user.id


def test_authenticate_com_senha_errada(session, created_user_ids):
    user, _ = _make_user(session, created_user_ids)
    with pytest.raises(AutenticacaoInvalidaError):
        authenticate(session, user.username, "senha_totalmente_errada")


def test_authenticate_com_username_inexistente(session):
    with pytest.raises(AutenticacaoInvalidaError):
        authenticate(session, f"nao_existe_{uuid.uuid4().hex[:8]}", "qualquer_senha_123")


def test_authenticate_com_usuario_inativo(session, created_user_ids):
    user, password = _make_user(session, created_user_ids)
    deactivate_user(session, user.id)
    session.commit()
    with pytest.raises(UsuarioInativoError):
        authenticate(session, user.username, password)


# --- endpoints HTTP ------------------------------------------------------------

def test_login_com_credenciais_corretas_retorna_token(session, created_user_ids, client):
    user, password = _make_user(session, created_user_ids)
    resp = client.post("/api/auth/login", json={"username": user.username, "password": password})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


def test_login_com_senha_errada_retorna_401(session, created_user_ids, client):
    user, _ = _make_user(session, created_user_ids)
    resp = client.post("/api/auth/login", json={"username": user.username, "password": "errada_123"})
    assert resp.status_code == 401


def test_login_usuario_inativo_retorna_401_generico(session, created_user_ids, client):
    """401 genérico (não 403 distinto) de propósito — evita que a resposta da API
    revele que o username existe e está só desativado (enumeração de usuário)."""
    user, password = _make_user(session, created_user_ids)
    deactivate_user(session, user.id)
    session.commit()
    resp = client.post("/api/auth/login", json={"username": user.username, "password": password})
    assert resp.status_code == 401


def test_me_sem_token_retorna_401(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_com_token_invalido_retorna_401(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer token.invalido.aqui"})
    assert resp.status_code == 401


def test_me_com_token_valido_retorna_usuario_sem_senha(session, created_user_ids, client):
    user, password = _make_user(session, created_user_ids)
    login_resp = client.post("/api/auth/login", json={"username": user.username, "password": password})
    token = login_resp.json()["access_token"]

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == user.username
    assert body["perfil"] == "vendedor"
    assert "password_hash" not in body
    assert "password" not in body


def test_me_com_token_expirado_retorna_401(client):
    """Gap da tarefa 8: só havia teste unitário de decode_access_token() para
    token expirado — nada confirmava que get_current_user() de fato captura
    TokenInvalidoError e devolve 401 na cadeia HTTP real (dependency injection
    do FastAPI), não só na chamada direta da função."""
    payload = {"sub": str(uuid.uuid4()), "exp": time.time() - 60}
    token_expirado = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token_expirado}"})
    assert resp.status_code == 401


def test_me_com_token_de_usuario_desativado_apos_login_retorna_401(session, created_user_ids, client):
    """Token continua tecnicamente válido, mas a conta foi desativada depois —
    get_current_user precisa checar o status atual no banco, não só o token."""
    user, password = _make_user(session, created_user_ids)
    login_resp = client.post("/api/auth/login", json={"username": user.username, "password": password})
    token = login_resp.json()["access_token"]

    deactivate_user(session, user.id)
    session.commit()

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
