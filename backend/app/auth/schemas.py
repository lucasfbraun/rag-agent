"""
Schemas de resposta compartilhados entre os routers de auth (Fase 5).

UsuarioResponse mora aqui, não em router.py, porque tanto o self-service
(GET /api/auth/me) quanto a administração (admin_router.py, tarefa 7)
precisam da mesma forma de resposta — nunca o model do banco cru, nunca
password_hash.
"""
from pydantic import BaseModel

from app.models import User, UserOrigin, UserStatus


class UsuarioResponse(BaseModel):
    """Nunca inclui password_hash — resposta explícita, não o model do banco cru."""
    id: str
    username: str
    nome: str
    email: str
    # `perfil` é o SLUG do perfil, não mais um enum: perfis viraram dado em
    # 2026-09-10 e podem ser criados pela tela. O slug é o identificador
    # estável; `perfil_nome` é o rótulo para exibir.
    perfil: str
    perfil_nome: str
    # Permissões efetivas do usuário — a tela usa para decidir o que mostrar,
    # em vez de deduzir do nome do perfil (que agora é livre).
    permissoes: list[str]
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
            email=user.email,
            perfil=user.perfil.slug, perfil_nome=user.perfil.nome,
            permissoes=sorted(user.perfil.nomes_de_permissoes()),
            status=user.status,
            origem=user.origem, external_id=user.external_id,
        )


class PermissaoResponse(BaseModel):
    """Uma permissão que o sistema conhece, com o rótulo que a tela mostra —
    "manage_ingestion" não diz nada a quem está montando um perfil."""
    chave: str
    rotulo: str


class PerfilResponse(BaseModel):
    id: str
    slug: str
    nome: str
    descricao: str | None
    protegido: bool
    permissoes: list[str]
    # Quantos usuários usam este perfil: a tela precisa avisar ANTES de alguém
    # tentar excluir, e não depois do erro.
    usuarios: int
    administra: bool

    @classmethod
    def from_perfil(cls, perfil, usuarios: int = 0) -> "PerfilResponse":
        from app.auth.permissions import PERMISSAO_DE_ADMINISTRACAO

        permissoes = sorted(perfil.nomes_de_permissoes())
        return cls(
            id=str(perfil.id), slug=perfil.slug, nome=perfil.nome,
            descricao=perfil.descricao, protegido=perfil.protegido,
            permissoes=permissoes, usuarios=usuarios,
            administra=PERMISSAO_DE_ADMINISTRACAO.value in permissoes,
        )
