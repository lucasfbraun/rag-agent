"""
Rotas da validação técnica (Sessão 58).

`VALIDATE_ANSWERS` em tudo. Quem valida NÃO é quem pergunta: o vendedor leigo
continua com `VIEW_CATALOG` e não enxerga nenhuma destas rotas. A permissão é
semeada nos perfis `tecnico`, `quimico_pd` e `admin_ti` pela migration
`f1c93a7b2d45`, e de lá em diante é editável na tela de perfis como qualquer
outra — nada aqui checa perfil diretamente (ver app/auth/permissions.py).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.db import get_session
from app.models import User, Veredito, VereditoTecnico
from app.rag.caminhos import ROTULOS_DE_CAMINHO
from app.validacao_service import (
    MensagemNaoAvaliavelError,
    gerar_relatorio,
    listar_fila,
    registrar_veredito,
)

router = APIRouter(prefix="/api/validacao", tags=["validacao"])


class RespostaNaFila(BaseModel):
    mensagem_id: str
    pergunta: str
    resposta: str
    caminho: str
    caminho_rotulo: str
    model_used: str | None
    fontes: list[str]
    termos_busca: list[str]
    created_at: datetime


class RegistrarVeredictoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    veredito: Veredito
    # Opcionais: exigir a resposta certa de quem só tem tempo de marcar
    # "errado" faria o técnico não marcar nada, e meia informação registrada
    # vale mais que a informação inteira que ninguém escreveu.
    resposta_correta: str | None = Field(default=None, max_length=20000)
    justificativa: str | None = Field(default=None, max_length=4000)


class VeredictoResponse(BaseModel):
    id: str
    mensagem_id: str | None
    pergunta: str
    veredito: str
    caminho: str | None
    model_used: str | None
    resposta_correta: str | None
    justificativa: str | None
    avaliado_por: str
    created_at: datetime

    @classmethod
    def de(cls, registro: VereditoTecnico) -> "VeredictoResponse":
        return cls(
            id=str(registro.id),
            mensagem_id=str(registro.mensagem_id) if registro.mensagem_id else None,
            pergunta=registro.pergunta,
            veredito=registro.veredito.value,
            caminho=registro.caminho,
            model_used=registro.model_used,
            resposta_correta=registro.resposta_correta,
            justificativa=registro.justificativa,
            avaliado_por=registro.avaliado_por.nome,
            created_at=registro.created_at,
        )


@router.get("/caminhos")
def listar_caminhos(
    _: User = Depends(require_permission(Permission.VALIDATE_ANSWERS)),
):
    """Os caminhos do motor, com rótulo legível.

    Vem do backend pelo mesmo motivo de `/api/models` e `/api/treinamento/tipos`:
    lista duplicada na tela já custou caro neste projeto (o 422 silencioso do
    seletor de modelos)."""
    return [
        {"caminho": caminho, "rotulo": rotulo}
        for caminho, rotulo in ROTULOS_DE_CAMINHO.items()
    ]


@router.get("/vereditos", response_model=list[str])
def listar_vereditos_possiveis(
    _: User = Depends(require_permission(Permission.VALIDATE_ANSWERS)),
):
    return [v.value for v in Veredito]


@router.get("/fila", response_model=list[RespostaNaFila])
def fila(
    apenas_sem_veredito: bool = True,
    caminho: str | None = None,
    apenas_com_feedback_negativo: bool = False,
    limite: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_permission(Permission.VALIDATE_ANSWERS)),
    session: Session = Depends(get_session),
):
    """Perguntas REAIS que já foram feitas, aguardando julgamento.

    Não existe endpoint para criar uma pergunta de avaliação, e isso é
    deliberado: o valor deste ciclo está em medir o que as pessoas de fato
    perguntam. Uma pergunta inventada mede o quanto quem a inventou conhece o
    motor, não o quanto o motor serve a quem o usa."""
    return listar_fila(
        session,
        apenas_sem_veredito=apenas_sem_veredito,
        caminho=caminho,
        apenas_com_feedback_negativo=apenas_com_feedback_negativo,
        limite=limite,
    )


@router.post(
    "/mensagens/{mensagem_id}",
    response_model=VeredictoResponse,
    status_code=status.HTTP_201_CREATED,
)
def julgar(
    mensagem_id: uuid.UUID,
    req: RegistrarVeredictoRequest,
    usuario: User = Depends(require_permission(Permission.VALIDATE_ANSWERS)),
    session: Session = Depends(get_session),
):
    try:
        registro = registrar_veredito(
            session,
            mensagem_id=mensagem_id,
            avaliador=usuario,
            veredito=req.veredito,
            resposta_correta=req.resposta_correta,
            justificativa=req.justificativa,
        )
    except MensagemNaoAvaliavelError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    return VeredictoResponse.de(registro)


@router.get("/relatorio")
def relatorio(
    desde: datetime | None = None,
    _: User = Depends(require_permission(Permission.VALIDATE_ANSWERS)),
    session: Session = Depends(get_session),
):
    """Taxa de acerto geral, por caminho do motor, e a lista de regressão.

    A taxa POR CAMINHO é o número que muda decisão: se o caminho
    determinístico acerta 90% e o conversacional 40%, a fila de trabalho
    inteira é outra. A taxa geral sozinha esconde exatamente isso."""
    if desde is not None and desde.tzinfo is None:
        desde = desde.replace(tzinfo=timezone.utc)
    return gerar_relatorio(session, desde=desde)
