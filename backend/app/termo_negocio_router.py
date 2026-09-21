"""
Rotas de terminologia de negócio — o apelido da empresa para uma linha do
catálogo (ver `app.termo_negocio_service` para o porquê de não ser treinamento).

Mesma separação de poderes do treinamento e do upload: `TRAIN_AGENT` para
cadastrar, `APPROVE_TRAINING` para decidir. Um apelido afirma um fato sobre o
catálogo que o agente vai repetir como verdade da empresa — e, diferente de uma
correção, ele muda o resultado de consultas ESTRUTURAIS, que são apresentadas
como a evidência mais forte que existe. Se algo merece revisão, é isto.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.permissions import Permission, has_permission, require_permission
from app.db import get_session
from app.models import StatusDocumento, TermoDeNegocio, User
from app.termo_negocio_service import (
    ClassificacaoInexistenteError,
    invalidar_cache,
    TermoInvalidoError,
    TermoNaoEncontradoError,
    aprovar,
    classificacoes_do_acervo,
    criar,
    excluir,
    listar,
    recusar,
)

router = APIRouter(prefix="/api/terminologia", tags=["terminologia"])


def _require_acesso(usuario: User = Depends(get_current_user)) -> User:
    """A fila precisa estar acessível a quem só tem poder de decisão."""
    if not (
        has_permission(usuario, Permission.TRAIN_AGENT)
        or has_permission(usuario, Permission.APPROVE_TRAINING)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sem permissão para acessar a terminologia do catálogo.",
        )
    return usuario


class TermoRequest(BaseModel):
    classificacao: str = Field(min_length=2, max_length=200)
    termo: str = Field(min_length=2, max_length=200)
    observacao: str | None = Field(default=None, max_length=2000)


class RecusaRequest(BaseModel):
    motivo: str = Field(min_length=3, max_length=2000)


class TermoResponse(BaseModel):
    id: uuid.UUID
    classificacao: str
    termo: str
    observacao: str | None
    status: StatusDocumento
    motivo_decisao: str | None
    criado_por: str | None
    decidido_por: str | None

    @classmethod
    def de(cls, item: TermoDeNegocio) -> "TermoResponse":
        return cls(
            id=item.id,
            classificacao=item.classificacao,
            termo=item.termo,
            observacao=item.observacao,
            status=item.status,
            motivo_decisao=item.motivo_decisao,
            criado_por=getattr(item.criado_por, "nome_completo", None),
            decidido_por=getattr(item.decidido_por, "nome_completo", None),
        )


@contextmanager
def _commit_traduzindo_erros(session: Session):
    try:
        yield
        session.commit()
        # DEPOIS do commit, nunca antes. Invalidar no `flush()` abria uma
        # janela em que uma pergunta concorrente lia o banco pela sua própria
        # conexão, não via a linha ainda não commitada, e regravava o cache
        # vazio com carimbo novo — válido por 60 s, com a tela mostrando
        # "Em uso" e o agente respondendo que não encontrou.
        invalidar_cache()
    except ClassificacaoInexistenteError as e:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except (TermoInvalidoError, TermoNaoEncontradoError) as e:
        session.rollback()
        codigo = (
            status.HTTP_404_NOT_FOUND
            if isinstance(e, TermoNaoEncontradoError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=codigo, detail=str(e))
    except IntegrityError:
        # A constraint de unicidade (classificação, termo) — cadastrar o mesmo
        # apelido duas vezes é engano comum quando duas pessoas trabalham a
        # mesma lista, e merece mensagem clara em vez de 500.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esse termo já está cadastrado para essa linha do catálogo.",
        )


@router.get("/classificacoes", response_model=list[str])
def listar_classificacoes(_: User = Depends(_require_acesso)):
    """Rótulos reais da árvore do acervo, para a tela OFERECER em vez de pedir
    que a pessoa digite — digitar "FLEXX TH" onde o acervo diz "FLEXX® TH"
    criaria um apelido que nunca resolve, sem erro visível."""
    return classificacoes_do_acervo()


@router.post("", response_model=TermoResponse, status_code=status.HTTP_201_CREATED)
def criar_termo(
    payload: TermoRequest,
    session: Session = Depends(get_session),
    usuario: User = Depends(require_permission(Permission.TRAIN_AGENT)),
):
    with _commit_traduzindo_erros(session):
        item = criar(
            session,
            classificacao=payload.classificacao,
            termo=payload.termo,
            observacao=payload.observacao,
            autor=usuario,
        )
    session.refresh(item)
    return TermoResponse.de(item)


@router.get("", response_model=list[TermoResponse])
def listar_termos(
    apenas_pendentes: bool = False,
    session: Session = Depends(get_session),
    _: User = Depends(_require_acesso),
):
    itens = listar(
        session, status=StatusDocumento.PENDENTE if apenas_pendentes else None
    )
    return [TermoResponse.de(i) for i in itens]


@router.post("/{item_id}/aprovar", response_model=TermoResponse)
def aprovar_termo(
    item_id: uuid.UUID,
    session: Session = Depends(get_session),
    usuario: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
):
    with _commit_traduzindo_erros(session):
        item = aprovar(session, item_id, aprovador=usuario)
    session.refresh(item)
    return TermoResponse.de(item)


@router.post("/{item_id}/recusar", response_model=TermoResponse)
def recusar_termo(
    item_id: uuid.UUID,
    payload: RecusaRequest,
    session: Session = Depends(get_session),
    usuario: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
):
    with _commit_traduzindo_erros(session):
        item = recusar(session, item_id, aprovador=usuario, motivo=payload.motivo)
    session.refresh(item)
    return TermoResponse.de(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_termo(
    item_id: uuid.UUID,
    session: Session = Depends(get_session),
    _: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
):
    with _commit_traduzindo_erros(session):
        excluir(session, item_id)
