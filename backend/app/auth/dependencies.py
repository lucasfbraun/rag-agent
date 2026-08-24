"""
Dependency FastAPI que identifica o usuário logado a partir do Bearer token.

Extraído de router.py (tarefa 3) para permissions.py (tarefa 4) poder usá-la sem
depender do módulo de rotas HTTP — get_current_user é "mais core" que qualquer
endpoint específico.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User, UserStatus
from app.auth.token import decode_access_token, TokenInvalidoError
from app.auth.user_service import get_user_by_id

_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    session: Session = Depends(get_session),
) -> User:
    """Extrai o Bearer token, decodifica, carrega o usuário e confere que segue
    ativo — token de usuário desativado depois do login para de funcionar no
    request seguinte, não só num futuro login."""
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
