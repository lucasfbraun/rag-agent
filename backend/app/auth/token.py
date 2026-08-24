"""
Token de sessão (Fase 5, tarefa 3) — JWT assinado com SECRET_KEY.

Interface pequena: create_access_token / decode_access_token. Esconde o formato
do token (claims, algoritmo, expiração) — nenhum outro módulo deve chamar `jwt.*`
diretamente.
"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES

ALGORITHM = "HS256"


class TokenInvalidoError(ValueError):
    """Token ausente, expirado, malformado ou com assinatura inválida."""


def create_access_token(user_id: uuid.UUID) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """Retorna o user_id codificado no token. Levanta TokenInvalidoError em qualquer
    problema (expirado, assinatura errada, malformado) — nunca deixa exceção do
    PyJWT vazar para quem chama."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise TokenInvalidoError("Token inválido ou expirado.") from e
