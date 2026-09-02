"""
Feedback do usuário por resposta do agente (útil/não útil, opcional) —
pedido do usuário: fechar o loop de melhoria contínua. O agente consulta o
feedback negativo mais recente ANTES de responder (ver
`obter_licoes_de_feedback`, usada em toda consulta por app.rag.engine — não
é um recurso opcional que precisa ser pedido, roda sempre), pra não repetir
um padrão de resposta já sinalizado como ruim.
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
    """Feedback NEGATIVO mais recente (com comentário quando houver) — base
    do bloco "LIÇÕES APRENDIDAS" injetado no prompt do agente em toda
    consulta (ver `_montar_licoes_str` em app.rag.engine).

    Só feedback negativo entra aqui de propósito: o objetivo é o agente
    evitar repetir um erro já sinalizado, não acumular elogios genéricos que
    não mudam comportamento nenhum. Sessão própria (não recebe uma de fora)
    porque quem chama (engine.py) não tem — nem deveria ter — acesso a uma
    sessão de banco; RAG e Postgres são camadas historicamente separadas
    neste projeto (Qdrant de um lado, auth/DB relacional do outro)."""
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
