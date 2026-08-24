"""
Endpoints de autenticação (Fase 5, tarefa 3): POST /login emite o token,
GET /me confirma que um token é aceito e devolve o usuário dono dele.

get_current_user (tarefa 3, agora em dependencies.py) é reaproveitada pela
tarefa 4 (permissions.py) e pela tarefa 5 (proteção dos endpoints existentes).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User
from app.auth.token import create_access_token
from app.auth.dependencies import get_current_user
from app.auth.schemas import UsuarioResponse
from app.auth.user_service import (
    authenticate,
    AutenticacaoInvalidaError,
    UsuarioInativoError,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, session: Session = Depends(get_session)):
    """
    Sempre responde 401 com a mesma mensagem genérica, mesmo para conta desativada —
    um status/mensagem diferente para "existe mas está inativa" permitiria enumerar
    quais usernames são contas reais desativadas (achado do code review). A distinção
    entre AutenticacaoInvalidaError/UsuarioInativoError continua existindo no service,
    disponível pra log/auditoria futura — só não é exposta pra quem chama a API.
    """
    try:
        user = authenticate(session, req.username, req.password)
    except (AutenticacaoInvalidaError, UsuarioInativoError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha incorretos.")
    return LoginResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UsuarioResponse)
def me(current_user: User = Depends(get_current_user)):
    return UsuarioResponse.from_user(current_user)
