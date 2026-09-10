"""
Rotas de treinamento do agente (Sessão 38, item 4).

`TRAIN_AGENT` para contribuir, `APPROVE_TRAINING` para decidir — separadas
pela mesma razão do upload: correção e conhecimento afirmam fatos que o agente
repete como verdade da empresa.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, has_permission, require_permission
from app.db import get_session
from app.models import ItemTreinamento, StatusDocumento, TipoTreinamento, User
from app.treinamento_service import (
    DecisaoInvalidaError,
    ItemInvalidoError,
    ItemNaoEncontradoError,
    aprovar,
    criar,
    excluir,
    listar,
    recusar,
)

router = APIRouter(prefix="/api/treinamento", tags=["treinamento"])


class ItemResponse(BaseModel):
    id: str
    tipo: str
    pergunta: str
    resposta: str
    resposta_original: str | None
    status: str
    motivo_decisao: str | None
    criado_por: str
    decidido_por: str | None
    created_at: str

    @classmethod
    def de(cls, item: ItemTreinamento) -> "ItemResponse":
        return cls(
            id=str(item.id), tipo=item.tipo.value, pergunta=item.pergunta,
            resposta=item.resposta, resposta_original=item.resposta_original,
            status=item.status.value, motivo_decisao=item.motivo_decisao,
            criado_por=item.criado_por.nome,
            decidido_por=item.decidido_por.nome if item.decidido_por else None,
            created_at=item.created_at.isoformat(),
        )


class CriarItemRequest(BaseModel):
    tipo: TipoTreinamento
    pergunta: str = Field(min_length=1, max_length=4000)
    resposta: str = Field(min_length=1, max_length=8000)
    # Só em correção: o que o agente respondeu errado, guardado para auditoria.
    resposta_original: str | None = Field(default=None, max_length=20000)


class MotivoRequest(BaseModel):
    motivo: str = Field(min_length=1, max_length=1000)


@contextmanager
def _commit_traduzindo_erros(session: Session):
    try:
        yield
        session.commit()
    except ItemNaoEncontradoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except (ItemInvalidoError, DecisaoInvalidaError) as e:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


@router.get("/tipos")
def listar_tipos(_: User = Depends(require_permission(Permission.TRAIN_AGENT))):
    """As três modalidades, com o rótulo e a regra de aprovação de cada uma.

    Vem do backend para a tela não repetir a lista — mesma disciplina de
    `/api/models` e `/api/auth/perfis`, cuja duplicação já causou o bug do 422.
    """
    from app.treinamento_service import TIPOS_QUE_EXIGEM_APROVACAO

    descricoes = {
        TipoTreinamento.CORRECAO: (
            "Corrigir uma resposta",
            "O agente respondeu errado e você escreve a resposta certa.",
        ),
        TipoTreinamento.CONHECIMENTO: (
            "Registrar conhecimento",
            "Um fato ou regra que a equipe sabe e não está em boletim nenhum.",
        ),
        TipoTreinamento.EXEMPLO: (
            "Ensinar pelo exemplo",
            "Um par pergunta/resposta modelo — ensina a FORMA de responder, não o conteúdo.",
        ),
    }
    return [
        {
            "tipo": tipo.value,
            "rotulo": descricoes[tipo][0],
            "descricao": descricoes[tipo][1],
            "exige_aprovacao": tipo in TIPOS_QUE_EXIGEM_APROVACAO,
        }
        for tipo in TipoTreinamento
    ]


@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def criar_item(
    req: CriarItemRequest,
    usuario: User = Depends(require_permission(Permission.TRAIN_AGENT)),
    session: Session = Depends(get_session),
):
    with _commit_traduzindo_erros(session):
        item = criar(
            session, autor=usuario, tipo=req.tipo, pergunta=req.pergunta,
            resposta=req.resposta, resposta_original=req.resposta_original,
        )
    return ItemResponse.de(item)


@router.get("", response_model=list[ItemResponse])
def listar_itens(
    tipo: str | None = None, status_filtro: str | None = None,
    usuario: User = Depends(require_permission(Permission.TRAIN_AGENT)),
    session: Session = Depends(get_session),
):
    """Quem aprova vê tudo; quem só treina vê o que escreveu.

    Sem o recorte, alguém enxergaria correções de colegas ainda não revisadas —
    conteúdo que a empresa ainda não validou."""
    try:
        filtro_tipo = TipoTreinamento(tipo) if tipo else None
        filtro_status = StatusDocumento(status_filtro) if status_filtro else None
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Filtro inválido: {e}")

    pode_aprovar = has_permission(usuario, Permission.APPROVE_TRAINING)
    itens = listar(
        session, tipo=filtro_tipo, status=filtro_status,
        autor=None if pode_aprovar else usuario,
    )
    return [ItemResponse.de(i) for i in itens]


@router.post("/{item_id}/aprovar", response_model=ItemResponse)
def aprovar_item(
    item_id: uuid.UUID,
    usuario: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
    session: Session = Depends(get_session),
):
    with _commit_traduzindo_erros(session):
        item = aprovar(session, item_id, aprovador=usuario)
    return ItemResponse.de(item)


@router.post("/{item_id}/recusar", response_model=ItemResponse)
def recusar_item(
    item_id: uuid.UUID, req: MotivoRequest,
    usuario: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
    session: Session = Depends(get_session),
):
    with _commit_traduzindo_erros(session):
        item = recusar(session, item_id, aprovador=usuario, motivo=req.motivo)
    return ItemResponse.de(item)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_item(
    item_id: uuid.UUID,
    usuario: User = Depends(require_permission(Permission.APPROVE_TRAINING)),
    session: Session = Depends(get_session),
):
    """Apaga do índice e do banco. Diferente de usuário (onde excluir é
    desativar): um item de treinamento não carrega histórico de ninguém."""
    with _commit_traduzindo_erros(session):
        excluir(session, item_id)
