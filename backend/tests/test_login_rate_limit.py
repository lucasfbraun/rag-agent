"""
Ticket 10 do plano de correção (AUD-010): POST /api/auth/login não tinha
limite de tentativas — bcrypt podia ser chamado ilimitadamente contra um
username. Corrigido com um limitador em memória (sem Redis: único processo
backend hoje, ver ressalva no módulo) por username, contando falhas —
sucesso limpa o contador.

Cuidado deliberado: o limite conta pra qualquer username, exista ou não a
conta — se só contasse pra usernames reais, a própria presença/ausência do
bloqueio viraria mais um canal de enumeração (mesma categoria de bug do
ticket 3, canal lateral de tempo).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user
from app.auth.rate_limit import MAX_TENTATIVAS
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


def test_apos_o_limite_de_tentativas_falhas_retorna_429(client):
    username = f"ratelimit_{uuid.uuid4().hex[:8]}"

    for _ in range(MAX_TENTATIVAS):
        resp = client.post("/api/auth/login", json={"username": username, "password": "errada"})
        assert resp.status_code == 401

    resp = client.post("/api/auth/login", json={"username": username, "password": "errada"})
    assert resp.status_code == 429


def test_limite_vale_tambem_para_username_inexistente(client):
    """Sem isso, o bloqueio em si viraria um jeito de descobrir quais
    usernames existem (só levaria ao 429 quem for real) — mesmo risco do
    canal lateral de tempo do ticket 3."""
    username = f"ratelimit_naoexiste_{uuid.uuid4().hex[:8]}"

    for _ in range(MAX_TENTATIVAS):
        client.post("/api/auth/login", json={"username": username, "password": "qualquer"})

    resp = client.post("/api/auth/login", json={"username": username, "password": "qualquer"})
    assert resp.status_code == 429


def test_limite_e_isolado_por_username(client):
    username_a = f"ratelimit_a_{uuid.uuid4().hex[:8]}"
    username_b = f"ratelimit_b_{uuid.uuid4().hex[:8]}"

    for _ in range(MAX_TENTATIVAS):
        client.post("/api/auth/login", json={"username": username_a, "password": "errada"})

    resp_a = client.post("/api/auth/login", json={"username": username_a, "password": "errada"})
    resp_b = client.post("/api/auth/login", json={"username": username_b, "password": "errada"})
    assert resp_a.status_code == 429
    assert resp_b.status_code == 401  # b não foi afetado pelas tentativas de a


def test_login_com_sucesso_limpa_o_contador(client, created_user_ids):
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"ratelimit_ok_{u}", nome="Usuário Rate Limit",
            email=f"ratelimit_ok_{u}@teste.local", password="senha_correta_123", perfil=Role.VENDEDOR,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    username = f"ratelimit_ok_{u}"

    for _ in range(MAX_TENTATIVAS - 1):
        resp = client.post("/api/auth/login", json={"username": username, "password": "errada"})
        assert resp.status_code == 401

    # Login certo antes de bater no limite — deve limpar o contador.
    resp_ok = client.post("/api/auth/login", json={"username": username, "password": "senha_correta_123"})
    assert resp_ok.status_code == 200

    # Mais tentativas erradas depois do sucesso não devem herdar o contador antigo.
    for _ in range(MAX_TENTATIVAS - 1):
        resp = client.post("/api/auth/login", json={"username": username, "password": "errada"})
        assert resp.status_code == 401
