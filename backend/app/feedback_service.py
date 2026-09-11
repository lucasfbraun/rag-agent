"""
Feedback do usuário por resposta do agente (útil/não útil, opcional).

Feedback bruto é sinal para revisão e métricas. Ele não vira instrução do
agente automaticamente: só uma correção criada no módulo de treinamento e
aprovada pode alterar respostas futuras.
"""
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Feedback


def registrar_feedback(
    session: Session,
    *,
    user_id: uuid.UUID,
    query: str,
    answer: str,
    util: bool,
    comentario: Optional[str] = None,
    model_used: Optional[str] = None,
    sources: Optional[List[str]] = None,
) -> Feedback:
    """Grava uma avaliação. `comentario` é opcional mesmo quando `util=False`
    — o clique em útil/não útil já é feedback válido por si só."""
    registro = Feedback(
        user_id=user_id,
        query=query,
        answer=answer,
        util=util,
        comentario=comentario,
        model_used=model_used,
        sources=sources or [],
    )
    session.add(registro)
    session.commit()
    session.refresh(registro)
    return registro


def obter_licoes_de_feedback(limit: int = 5) -> List[Dict[str, Any]]:
    """Feedback negativo recente para telas e relatórios de revisão.

    Mantida como consulta administrativa; o motor RAG não consome esse
    retorno. Apenas itens aprovados de treinamento alteram respostas."""
    session = SessionLocal()
    try:
        registros = (
            session.query(Feedback)
            .filter(Feedback.util.is_(False))
            .order_by(Feedback.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "query": r.query,
                "comentario": r.comentario,
            }
            for r in registros
        ]
    finally:
        session.close()
