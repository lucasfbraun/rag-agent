"""
Administração de perfis de acesso (Sessão 37).

Até 2026-09-10 os perfis eram um enum fixo: criar um novo exigia migration e
deploy. Estas rotas expõem o CRUD, atrás da mesma permissão que administra
usuários — quem pode editar perfis pode, na prática, se dar qualquer poder, e
separar isso em duas permissões daria uma sensação falsa de contenção.

As regras que impedem o administrador de se trancar para fora vivem em
`perfil_service`, não aqui: este módulo só traduz erro de domínio em HTTP.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.perfil_service import (
    PerfilEmUsoError,
    PerfilJaExisteError,
    PerfilNaoEncontradoError,
    PerfilProtegidoError,
    SemAdministradorError,
    atualizar_perfil,
    contar_usuarios,
    criar_perfil,
    excluir_perfil,
    listar_perfis,
)
from app.auth.permissions import ROTULOS_DE_PERMISSAO, Permission, require_permission
from app.auth.schemas import PerfilResponse, PermissaoResponse
from app.db import get_session

router = APIRouter(prefix="/api/auth/perfis", tags=["admin-perfis"])


@contextmanager
def _commit_traduzindo_erros(session: Session):
    """Mesmo padrão de admin_router: único lugar que conhece o mapeamento
    erro de domínio → status HTTP."""
    try:
        yield
        session.commit()
    except PerfilNaoEncontradoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except PerfilJaExisteError as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (PerfilProtegidoError, PerfilEmUsoError, SemAdministradorError) as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


class CriarPerfilRequest(BaseModel):
    # `slug` é o identificador estável (usado por integração e testes) e por
    # isso não é editável depois — ver perfil_service.atualizar_perfil.
    slug: str = Field(min_length=2, max_length=50, pattern=r"^[a-z][a-z0-9_]*$")
    nome: str = Field(min_length=2, max_length=100)
    descricao: str | None = Field(default=None, max_length=300)
    permissoes: list[str] = Field(default_factory=list)


class EditarPerfilRequest(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=100)
    descricao: str | None = Field(default=None, max_length=300)
    permissoes: list[str] | None = None


@router.get(
    "/permissoes", response_model=list[PermissaoResponse],
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def listar_permissoes():
    """Catálogo de permissões que o sistema conhece, com rótulo legível.

    Vem do backend, não escrito na tela: a lista muda a cada release que
    acrescenta uma permissão, e duplicá-la no frontend já causou um bug real
    neste projeto (a divergência de modelos que virava 422).

    ATENÇÃO À ORDEM: precisa vir antes de "/{perfil_id}", ou o FastAPI leria
    "permissoes" como um UUID.
    """
    return [
        PermissaoResponse(chave=p.value, rotulo=ROTULOS_DE_PERMISSAO.get(p, p.value))
        for p in Permission
    ]


@router.get(
    "", response_model=list[PerfilResponse],
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def listar(session: Session = Depends(get_session)):
    return [
        PerfilResponse.from_perfil(p, usuarios=contar_usuarios(session, p.id))
        for p in listar_perfis(session)
    ]


@router.post(
    "", response_model=PerfilResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def criar(req: CriarPerfilRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        perfil = criar_perfil(
            session, slug=req.slug, nome=req.nome,
            descricao=req.descricao, permissoes=req.permissoes,
        )
    return PerfilResponse.from_perfil(perfil, usuarios=0)


@router.patch(
    "/{perfil_id}", response_model=PerfilResponse,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def editar(perfil_id: uuid.UUID, req: EditarPerfilRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        perfil = atualizar_perfil(
            session, perfil_id, nome=req.nome,
            descricao=req.descricao, permissoes=req.permissoes,
        )
    return PerfilResponse.from_perfil(perfil, usuarios=contar_usuarios(session, perfil_id))


@router.delete(
    "/{perfil_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def excluir(perfil_id: uuid.UUID, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        excluir_perfil(session, perfil_id)
