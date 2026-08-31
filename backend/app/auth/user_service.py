"""
Repository/service de usuários (Fase 5 — RBAC & Governança).

Interface pequena por propósito: create/get/list/update/deactivate/set_password.
Esconde: hashing de senha, tradução de erro de unicidade do banco em exceção de
domínio, e a regra de que "excluir" um usuário é desativar (status), não apagar
a linha — perda de histórico de quem fez o quê não é aceitável para auditoria.

Nenhum outro módulo deve fazer session.add(User(...)) diretamente — sempre passar
por aqui, para que hashing e checagem de duplicidade fiquem garantidos num só lugar.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.models import Role, User, UserOrigin, UserStatus


class UsuarioJaExisteError(ValueError):
    """username ou email já cadastrado."""


class UsuarioNaoEncontradoError(ValueError):
    """Nenhum usuário com o id/username informado."""


class AutenticacaoInvalidaError(ValueError):
    """username ou senha incorretos. Mensagem sempre genérica — não revela qual dos dois errou."""


class UsuarioInativoError(ValueError):
    """Credenciais corretas, mas a conta está desativada."""


class UltimoAdminError(ValueError):
    """A operação deixaria zero Admin TI ativos — nenhuma mudança de perfil
    ou desativação pode zerar esse número (ver AUD-004,
    docs/auditoria_2026-08-25.md)."""


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


def _contar_admins_ativos_exceto(session: Session, user_id) -> int:
    return session.execute(
        select(func.count()).select_from(User).where(
            User.perfil == Role.ADMIN_TI,
            User.status == UserStatus.ATIVO,
            User.id != user_id,
        )
    ).scalar_one()


def _garantir_que_nao_zera_admins_ativos(session: Session, user: User) -> None:
    """Chamar ANTES de aplicar uma mudança que tire `user` da condição
    "Admin TI ativo" (rebaixar perfil ou desativar). Só levanta erro se `user`
    é hoje um admin ativo E não sobra nenhum outro — editar/desativar
    qualquer outro perfil, ou um admin já inativo, não aciona isto."""
    if user.perfil != Role.ADMIN_TI or user.status != UserStatus.ATIVO:
        return
    if _contar_admins_ativos_exceto(session, user.id) == 0:
        raise UltimoAdminError(
            "Esta operação deixaria o sistema sem nenhum Admin TI ativo — "
            "promova outro usuário a Admin TI antes de continuar."
        )


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
    if perfil is not None and perfil != user.perfil:
        _garantir_que_nao_zera_admins_ativos(session, user)
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
    _garantir_que_nao_zera_admins_ativos(session, user)
    user.status = UserStatus.INATIVO
    session.flush()
    return user


def authenticate(session: Session, username: str, password: str) -> User:
    """Confere username+senha. Levanta AutenticacaoInvalidaError (credencial errada
    ou usuário inexistente — mesma mensagem pros dois casos, para não revelar quais
    usernames existem) ou UsuarioInativoError (credenciais certas, conta desativada).

    verify_password() roda sempre, mesmo com username inexistente (contra um
    hash dummy fixo) — sem isso, a checagem de bcrypt (~100ms) só acontecia
    quando o username existia, e o tempo de resposta virava um canal de
    enumeração de usuário mesmo com a mensagem de erro sendo uniforme."""
    user = get_user_by_username(session, username)
    password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    senha_confere = verify_password(password, password_hash)
    if user is None or user.password_hash is None or not senha_confere:
        raise AutenticacaoInvalidaError("Usuário ou senha incorretos.")
    if user.status != UserStatus.ATIVO:
        raise UsuarioInativoError("Esta conta está desativada. Contate o administrador.")
    return user
