"""
Schemas de resposta compartilhados entre os routers de auth (Fase 5).

UsuarioResponse mora aqui, não em router.py, porque tanto o self-service
(GET /api/auth/me) quanto a administração (admin_router.py, tarefa 7)
precisam da mesma forma de resposta — nunca o model do banco cru, nunca
password_hash.
"""
from pydantic import BaseModel

from app.models import Role, User, UserOrigin, UserStatus


class UsuarioResponse(BaseModel):
    """Nunca inclui password_hash — resposta explícita, não o model do banco cru."""
    id: str
    username: str
    nome: str
    email: str
    perfil: Role
    status: UserStatus
    # A tela precisa saber se o login é local ou do AD para mostrar o vínculo e
    # esconder "redefinir senha" de quem autentica no diretório — redefinir
    # senha de usuário LDAP não teria efeito nenhum.
    origem: UserOrigin
    external_id: str | None

    @classmethod
    def from_user(cls, user: User) -> "UsuarioResponse":
        return cls(
            id=str(user.id), username=user.username, nome=user.nome,
            email=user.email, perfil=user.perfil, status=user.status,
            origem=user.origem, external_id=user.external_id,
        )
