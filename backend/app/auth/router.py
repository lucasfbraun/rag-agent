"""
Endpoints de autenticação (Fase 5, tarefa 3): POST /login emite o token,
GET /me confirma que um token é aceito e devolve o usuário dono dele.

get_current_user() é a dependency que a tarefa 5 (proteção dos endpoints
existentes) vai reaproveitar — autenticação sem uma forma de verificar o
token não é autenticação completa, então já fica pronta aqui, mesmo sem
ainda ser aplicada a `/api/match` e companhia.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Role, User, UserStatus
from app.auth.token import create_access_token, decode_access_token, TokenInvalidoError
from app.auth.user_service import (
    authenticate,
    get_user_by_id,
    AutenticacaoInvalidaError,
    UsuarioInativoError,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
_bearer_scheme = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsuarioResponse(BaseModel):
    """Nunca inclui password_hash — resposta explícita, não o model do banco cru."""
    id: str
    username: str
    nome: str
    email: str
    perfil: Role
    status: UserStatus

    @classmethod
    def from_user(cls, user: User) -> "UsuarioResponse":
        return cls(
            id=str(user.id), username=user.username, nome=user.nome,
            email=user.email, perfil=user.perfil, status=user.status,
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Dependency FastAPI: extrai o Bearer token, decodifica, carrega o usuário e
    confere que segue ativo — token de usuário desativado depois do login para de
    funcionar no request seguinte, não só num futuro login."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de autenticação ausente.")
    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenInvalidoError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido ou expirado.")
    user = get_user_by_id(session, user_id)
    if user is None or user.status != UserStatus.ATIVO:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não encontrado ou inativo.")
    return user


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
