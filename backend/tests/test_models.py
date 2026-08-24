"""
Teste de integração do model User (Fase 5 — RBAC & Governança).

Requer Postgres real acessível via app.config.DATABASE_URL (o serviço
`postgres` do docker-compose.yml) — não é um teste unitário isolado,
é o primeiro teste automatizado do projeto e valida a tarefa 1
(model + migration) end-to-end contra o banco de verdade.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import User, Role, UserStatus, UserOrigin


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_user(**overrides):
    unique = uuid.uuid4().hex[:8]
    defaults = dict(
        username=f"user_{unique}",
        nome="Usuário de Teste",
        email=f"user_{unique}@teste.local",
        perfil=Role.VENDEDOR,
    )
    defaults.update(overrides)
    return User(**defaults)


def test_persiste_usuario_com_defaults_corretos(session):
    user = _make_user()
    session.add(user)
    session.flush()

    persisted = session.get(User, user.id)
    assert persisted is not None
    assert persisted.status == UserStatus.ATIVO  # default do model
    assert persisted.origem == UserOrigin.MANUAL  # default do model
    assert persisted.perfil == Role.VENDEDOR
    assert persisted.password_hash is None
    assert persisted.external_id is None
    assert persisted.created_at is not None
    assert persisted.updated_at is not None


def test_aceita_qualquer_um_dos_5_perfis(session):
    for role in Role:
        user = _make_user(perfil=role)
        session.add(user)
        session.flush()
        assert session.get(User, user.id).perfil == role


def test_username_duplicado_e_rejeitado(session):
    user1 = _make_user(username="duplicado_teste")
    session.add(user1)
    session.flush()

    user2 = _make_user(username="duplicado_teste")
    session.add(user2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_email_duplicado_e_rejeitado(session):
    user1 = _make_user(email="dup@teste.local")
    session.add(user1)
    session.flush()

    user2 = _make_user(email="dup@teste.local")
    session.add(user2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_usuario_inativo_e_persistido_com_status_correto(session):
    user = _make_user(status=UserStatus.INATIVO)
    session.add(user)
    session.flush()
    assert session.get(User, user.id).status == UserStatus.INATIVO


def test_usuario_origem_ldap_aceita_external_id_sem_senha(session):
    user = _make_user(
        origem=UserOrigin.LDAP,
        external_id="CN=teste,DC=empresa,DC=local",
        password_hash=None,
    )
    session.add(user)
    session.flush()

    persisted = session.get(User, user.id)
    assert persisted.origem == UserOrigin.LDAP
    assert persisted.external_id == "CN=teste,DC=empresa,DC=local"
    assert persisted.password_hash is None
