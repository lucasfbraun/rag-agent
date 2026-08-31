"""
Ticket 3, achado novo da verificação de 2026-08-26: authenticate() só chama
verify_password() (bcrypt, ~100ms) quando o username existe — `user is None
or ... or not verify_password(...)` faz curto-circuito. A mensagem de erro é
uniforme de propósito ("Usuário ou senha incorretos"), mas o TEMPO de
resposta não era, o que ainda permite enumerar usernames reais por timing.

Não dá pra testar "tempo" de forma determinística e rápida num teste
automatizado (seria um teste lento e flaky). O que dá pra testar é o
CONTRATO que corrige o canal — bcrypt.checkpw (via verify_password) precisa
rodar sempre, mesmo com username inexistente — via mock, não é side-channel:
é exatamente o comportamento que estava faltando.
"""
import uuid
from unittest.mock import patch

import pytest

from app.db import SessionLocal
from app.models import User
from app.auth.user_service import AutenticacaoInvalidaError, authenticate, create_user


@pytest.fixture
def session():
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


def test_username_inexistente_ainda_assim_chama_verify_password(session):
    """Antes do fix, este mock nunca era chamado pra username inexistente —
    é exatamente o curto-circuito que criava a diferença de tempo."""
    with patch("app.auth.user_service.verify_password", return_value=False) as mock_verify:
        with pytest.raises(AutenticacaoInvalidaError):
            authenticate(session, "usuario_que_nao_existe_" + uuid.uuid4().hex[:8], "qualquer-senha")

    mock_verify.assert_called_once()


def test_username_existente_com_senha_errada_continua_negando(session, created_user_ids):
    u = uuid.uuid4().hex[:8]
    user = create_user(
        session, username=f"timing_{u}", nome="Usuário Timing",
        email=f"timing_{u}@teste.local", password="senha_correta_123",
        perfil=__import__("app.models", fromlist=["Role"]).Role.VENDEDOR,
    )
    session.commit()
    created_user_ids.append(user.id)

    with pytest.raises(AutenticacaoInvalidaError):
        authenticate(session, f"timing_{u}", "senha_errada")


def test_username_e_senha_corretos_continua_autenticando(session, created_user_ids):
    u = uuid.uuid4().hex[:8]
    user = create_user(
        session, username=f"timing_{u}", nome="Usuário Timing",
        email=f"timing_{u}@teste.local", password="senha_correta_123",
        perfil=__import__("app.models", fromlist=["Role"]).Role.VENDEDOR,
    )
    session.commit()
    created_user_ids.append(user.id)

    autenticado = authenticate(session, f"timing_{u}", "senha_correta_123")
    assert autenticado.id == user.id
