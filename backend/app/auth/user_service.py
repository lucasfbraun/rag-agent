"""
Repository/service de usuários (Fase 5 — RBAC & Governança).

Interface pequena por propósito: create/get/list/update/deactivate/set_password.
Esconde: hashing de senha, tradução de erro de unicidade do banco em exceção de
domínio, e a regra de que "excluir" um usuário é desativar (status), não apagar
a linha — perda de histórico de quem fez o quê não é aceitável para auditoria.

Nenhum outro módulo deve fazer session.add(User(...)) diretamente — sempre passar
por aqui, para que hashing e checagem de duplicidade fiquem garantidos num só lugar.
"""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.models import Role, User, UserOrigin, UserStatus


class UsuarioJaExisteError(ValueError):
    """username ou email já cadastrado."""


class UsuarioNaoEncontradoError(ValueError):
    """Nenhum usuário com o id/username informado."""


def _get_user_or_raise(session: Session, user_id) -> User:
    user = get_user_by_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError(f"Usuário {user_id} não encontrado.")
    return user


def _flush_or_raise_duplicate(session: Session, mensagem: str) -> None:
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise UsuarioJaExisteError(mensagem) from e


def create_user(session: Session, *, username: str, nome: str, email: str, password: str, perfil: Role) -> User:
    """Cria um usuário de origem manual. Levanta SenhaFracaError (ver security.py)
    se a senha não atender ao mínimo, ou UsuarioJaExisteError se username/email já existem."""
    user = User(
        username=username,
        nome=nome,
        email=email,
        password_hash=hash_password(password),
        perfil=perfil,
        origem=UserOrigin.MANUAL,
    )
    session.add(user)
    _flush_or_raise_duplicate(session, f"username '{username}' ou email '{email}' já cadastrado.")
    return user


def get_user_by_id(session: Session, user_id) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def list_users(session: Session) -> list[User]:
    return list(session.execute(select(User).order_by(User.nome)).scalars())


def update_user(session: Session, user_id, *, nome: str | None = None, email: str | None = None,
                 perfil: Role | None = None) -> User:
    """Atualiza campos mutáveis de negócio. NÃO mexe em senha/origem/external_id —
    troca de senha é set_password(); origem/external_id não são editáveis por aqui
    (mudar a origem de um usuário depois de criado é uma decisão que ainda não tem
    requisito definido — ver docs/spec_rbac.md)."""
    user = _get_user_or_raise(session, user_id)
    if nome is not None:
        user.nome = nome
    if email is not None:
        user.email = email
    if perfil is not None:
        user.perfil = perfil
    _flush_or_raise_duplicate(session, f"email '{email}' já usado por outro usuário.")
    return user


def set_password(session: Session, user_id, new_password: str) -> User:
    user = _get_user_or_raise(session, user_id)
    user.password_hash = hash_password(new_password)
    session.flush()
    return user


def deactivate_user(session: Session, user_id) -> User:
    """'Excluir' um usuário = desativar (status=INATIVO), nunca apagar a linha."""
    user = _get_user_or_raise(session, user_id)
    user.status = UserStatus.INATIVO
    session.flush()
    return user
