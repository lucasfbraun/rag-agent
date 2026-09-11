"""Sessão lembrada usa validade própria sem criar token eterno."""

import time
import uuid
from unittest.mock import MagicMock, patch

import jwt

from app.auth.router import LoginRequest, login
from app.auth.token import ALGORITHM, create_access_token
from app.config import REMEMBER_ME_EXPIRE_DAYS, SECRET_KEY


def test_token_aceita_validade_customizada_em_minutos():
    antes = time.time()
    token = create_access_token(uuid.uuid4(), expire_minutes=90)
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

    assert antes + 89 * 60 <= payload["exp"] <= time.time() + 91 * 60


def test_login_marcado_emite_token_longo_e_informa_validade_ao_frontend():
    usuario = MagicMock(id=uuid.uuid4())
    with patch("app.auth.router.limite_excedido", return_value=False), patch(
        "app.auth.router.authenticate", return_value=usuario
    ), patch("app.auth.router.limpar_tentativas"), patch(
        "app.auth.router.create_access_token", return_value="token-longo"
    ) as criar_token:
        resposta = login(
            LoginRequest(username="ana", password="segredo", manter_conectado=True),
            MagicMock(),
        )

    minutos = REMEMBER_ME_EXPIRE_DAYS * 24 * 60
    criar_token.assert_called_once_with(usuario.id, expire_minutes=minutos)
    assert resposta.access_token == "token-longo"
    assert resposta.expires_in_seconds == minutos * 60


def test_login_desmarcado_preserva_validade_normal():
    usuario = MagicMock(id=uuid.uuid4())
    with patch("app.auth.router.limite_excedido", return_value=False), patch(
        "app.auth.router.authenticate", return_value=usuario
    ), patch("app.auth.router.limpar_tentativas"), patch(
        "app.auth.router.create_access_token", return_value="token-curto"
    ) as criar_token:
        resposta = login(LoginRequest(username="ana", password="segredo"), MagicMock())

    criar_token.assert_called_once_with(usuario.id, expire_minutes=None)
    assert resposta.expires_in_seconds > 0
