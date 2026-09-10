"""
Rotas da fila de aprovação de documentos (Sessão 38).

Duas permissões distintas, e a separação é o ponto: `UPLOAD_DOCUMENTS` para
enviar, `APPROVE_UPLOADS` para decidir. Quem está em campo tem o boletim que
falta; quem aprova responde pelo que a base passa a afirmar para a equipe.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, has_permission, require_permission
from app.db import get_session
from app.models import DocumentoEnviado, StatusDocumento, User
from app.upload_service import (
    DecisaoInvalidaError,
    DocumentoInvalidoError,
    DocumentoNaoEncontradoError,
    FalhaAoIndexarError,
    TAMANHO_MAXIMO_BYTES,
    aprovar,
    listar,
    recusar,
    registrar_envio,
    remover_do_acervo,
)

router = APIRouter(prefix="/api/documentos", tags=["documentos"])


class DocumentoResponse(BaseModel):
    id: str
    nome_arquivo: str
    tamanho_bytes: int
    status: str
    observacao: str | None
    motivo_decisao: str | None
    enviado_por: str
    decidido_por: str | None
    chunks_indexados: int
    created_at: str
    decidido_em: str | None

    @classmethod
    def de(cls, documento: DocumentoEnviado) -> "DocumentoResponse":
        return cls(
            id=str(documento.id),
            nome_arquivo=documento.nome_arquivo,
            tamanho_bytes=documento.tamanho_bytes,
            status=documento.status.value,
            observacao=documento.observacao,
            motivo_decisao=documento.motivo_decisao,
            enviado_por=documento.enviado_por.nome,
            decidido_por=documento.decidido_por.nome if documento.decidido_por else None,
            chunks_indexados=documento.chunks_indexados,
            created_at=documento.created_at.isoformat(),
            decidido_em=documento.decidido_em.isoformat() if documento.decidido_em else None,
        )


class DecisaoRequest(BaseModel):
    motivo: str = Field(min_length=1, max_length=1000)


@contextmanager
def _commit_traduzindo_erros(session: Session):
    try:
        yield
        session.commit()
    except DocumentoNaoEncontradoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except (DocumentoInvalidoError, DecisaoInvalidaError) as e:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except FalhaAoIndexarError as e:
        session.rollback()
        # 422 e não 500: a causa quase sempre é o conteúdo do arquivo (PDF
        # digitalizado sem texto), e quem aprovou precisa saber disso para
        # recusar em vez de tentar de novo.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"O arquivo foi aceito mas não pôde ser indexado: {e}",
        )


@router.post("", response_model=DocumentoResponse, status_code=status.HTTP_201_CREATED)
async def enviar(
    arquivo: UploadFile = File(...),
    observacao: str = Form(default=""),
    usuario: User = Depends(require_permission(Permission.UPLOAD_DOCUMENTS)),
    session: Session = Depends(get_session),
):
    """Recebe o arquivo e o põe na fila como PENDENTE.

    O corpo é lido inteiro na memória porque o limite é pequeno (25 MB) e o
    conteúdo precisa ser medido antes de gravar — streaming direto para o disco
    escreveria o arquivo antes de saber se ele passa da validação."""
    conteudo = await arquivo.read()
    if len(conteudo) > TAMANHO_MAXIMO_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Arquivo maior que {TAMANHO_MAXIMO_BYTES // 1024 // 1024} MB.",
        )
    with _commit_traduzindo_erros(session):
        documento = registrar_envio(
            session, usuario=usuario, nome_arquivo=arquivo.filename or "documento",
            conteudo=conteudo, observacao=observacao,
        )
    return DocumentoResponse.de(documento)


@router.get("", response_model=list[DocumentoResponse])
def listar_documentos(
    status_filtro: str | None = None,
    usuario: User = Depends(require_permission(Permission.UPLOAD_DOCUMENTS)),
    session: Session = Depends(get_session),
):
    """Quem aprova vê a fila inteira; quem só envia vê os próprios envios.

    Sem esse recorte, um vendedor enxergaria o que os colegas mandaram e ainda
    não foi aprovado — material que a empresa ainda não validou."""
    filtro = None
    if status_filtro:
        try:
            filtro = StatusDocumento(status_filtro)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Status '{status_filtro}' inválido.")

    pode_aprovar = has_permission(usuario, Permission.APPROVE_UPLOADS)
    documentos = listar(
        session, status=filtro, enviado_por=None if pode_aprovar else usuario
    )
    return [DocumentoResponse.de(d) for d in documentos]


@router.post("/{documento_id}/aprovar", response_model=DocumentoResponse)
def aprovar_documento(
    documento_id: uuid.UUID,
    usuario: User = Depends(require_permission(Permission.APPROVE_UPLOADS)),
    session: Session = Depends(get_session),
):
    with _commit_traduzindo_erros(session):
        documento = aprovar(session, documento_id, aprovador=usuario)
    return DocumentoResponse.de(documento)


@router.post("/{documento_id}/recusar", response_model=DocumentoResponse)
def recusar_documento(
    documento_id: uuid.UUID, req: DecisaoRequest,
    usuario: User = Depends(require_permission(Permission.APPROVE_UPLOADS)),
    session: Session = Depends(get_session),
):
    with _commit_traduzindo_erros(session):
        documento = recusar(session, documento_id, aprovador=usuario, motivo=req.motivo)
    return DocumentoResponse.de(documento)


@router.post("/{documento_id}/remover", response_model=DocumentoResponse)
def remover_documento(
    documento_id: uuid.UUID, req: DecisaoRequest,
    usuario: User = Depends(require_permission(Permission.APPROVE_UPLOADS)),
    session: Session = Depends(get_session),
):
    """Tira do índice um documento já aprovado — a volta que faltava."""
    with _commit_traduzindo_erros(session):
        documento = remover_do_acervo(
            session, documento_id, aprovador=usuario, motivo=req.motivo
        )
    return DocumentoResponse.de(documento)
