"""
Administração de usuários (Fase 5, tarefa 7). Único jeito de provisionar
usuário até aqui era um script/CLI batendo direto no banco (ver PROGRESS.md,
Sessão 16-19, criação do admin real lucas.braun) — esta tarefa expõe as
mesmas operações que backend/app/auth/user_service.py já tinha desde a
tarefa 2 (create/list/get/update/set_password/deactivate) via HTTP, atrás de
Permission.MANAGE_USERS (só Admin TI, ver docs/spec_rbac.md).

Toda rota exige a mesma permissão, declarada via `dependencies=[...]` por rota
(mesmo padrão já usado em main.py) — exceto a de desativação, que recebe
require_permission() como parâmetro de verdade porque é a única que precisa
do usuário logado (guarda de autodesativação abaixo). Não dá pra combinar as
duas formas na mesma rota: cada `Depends(require_permission(...))` é uma
closure nova, então declará-lo tanto no router quanto na rota executaria
get_current_user() duas vezes por request.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import UsuarioResponse
from app.auth.security import SenhaFracaError
from app.auth.user_service import (
    UsuarioJaExisteError,
    UsuarioNaoEncontradoError,
    create_user,
    deactivate_user,
    get_user_by_id,
    list_users,
    set_password,
    update_user,
)
from app.db import get_session
from app.models import Role, User

router = APIRouter(prefix="/api/auth/users", tags=["admin-usuarios"])


@contextmanager
def _commit_traduzindo_erros(session: Session):
    """Confirma a transação e traduz os erros de domínio de user_service.py pro
    HTTP correspondente — único lugar que sabe esse mapeamento, em vez de
    repetir o mesmo try/commit/except/rollback em cada rota."""
    try:
        yield
        session.commit()
    except UsuarioNaoEncontradoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except UsuarioJaExisteError as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except SenhaFracaError as e:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


class CriarUsuarioRequest(BaseModel):
    username: str
    nome: str
    email: str
    password: str
    perfil: Role


class EditarUsuarioRequest(BaseModel):
    """Nenhum campo obrigatório — só altera o que vier preenchido. Não inclui
    senha (ver RedefinirSenhaRequest) nem origem/external_id, mesmo limite já
    documentado em user_service.update_user()."""
    nome: str | None = None
    email: str | None = None
    perfil: Role | None = None


class RedefinirSenhaRequest(BaseModel):
    new_password: str


@router.post(
    "", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def criar_usuario(req: CriarUsuarioRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        user = create_user(
            session, username=req.username, nome=req.nome, email=req.email,
            password=req.password, perfil=req.perfil,
        )
    return UsuarioResponse.from_user(user)


@router.get("", response_model=list[UsuarioResponse], dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def listar_usuarios(session: Session = Depends(get_session)):
    return [UsuarioResponse.from_user(u) for u in list_users(session)]


@router.get("/{user_id}", response_model=UsuarioResponse, dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def obter_usuario(user_id: uuid.UUID, session: Session = Depends(get_session)):
    user = get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado.")
    return UsuarioResponse.from_user(user)


@router.patch("/{user_id}", response_model=UsuarioResponse, dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def editar_usuario(user_id: uuid.UUID, req: EditarUsuarioRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        user = update_user(session, user_id, nome=req.nome, email=req.email, perfil=req.perfil)
    return UsuarioResponse.from_user(user)


@router.post(
    "/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def redefinir_senha(user_id: uuid.UUID, req: RedefinirSenhaRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        set_password(session, user_id, req.new_password)


@router.post("/{user_id}/deactivate", response_model=UsuarioResponse)
def desativar_usuario(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    """'Excluir' = desativar (user_service.deactivate_user), nunca apagar a linha.

    Guarda extra (decisão de engenharia desta tarefa, não um requisito de negócio
    documentado): ninguém pode desativar a própria conta por aqui. Hoje só existe
    um Admin TI real (lucas.braun) — sem essa guarda, um clique errado travaria a
    administração inteira do sistema sem caminho de recuperação a não ser acesso
    direto ao banco. Essa rota recebe require_permission() como parâmetro, não
    como dependencies=[...] como as outras, porque precisa do User de volta para
    essa comparação (ver docstring do módulo)."""
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Não é possível desativar a própria conta.",
        )
    with _commit_traduzindo_erros(session):
        user = deactivate_user(session, user_id)
    return UsuarioResponse.from_user(user)
