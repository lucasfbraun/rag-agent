"""
Pedido do usuário: poder avaliar uma resposta do agente como útil/não útil
(opcional) e o agente "sempre consultar essas memórias" — o feedback
negativo mais recente entra automaticamente em toda consulta futura (ver
app.rag.engine._montar_licoes_str).

Seam: `registrar_feedback`/`obter_licoes_de_feedback` contra o Postgres real
de teste (mesmo padrão de test_sensitive_fields.py/test_user_service.py —
não mockado, é auth/DB relacional, não RAG) — cada teste cria seu próprio
usuário e limpa o que criou ao final (Feedback é apagado ANTES do User, pela
foreign key).
"""
import uuid

import pytest

from app.db import SessionLocal
from app.models import Feedback, Role, User
from app.auth.user_service import create_user
from app.feedback_service import registrar_feedback, obter_licoes_de_feedback


@pytest.fixture
def usuario_de_teste():
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"fb_{u}", nome="Usuário Feedback",
            email=f"fb_{u}@teste.local", password="senha_segura_123", perfil=Role.VENDEDOR,
        )
        session.commit()
        user_id = user.id
    finally:
        session.close()

    yield user_id

    cleanup = SessionLocal()
    try:
        cleanup.query(Feedback).filter(Feedback.user_id == user_id).delete()
        u_obj = cleanup.get(User, user_id)
        if u_obj is not None:
            cleanup.delete(u_obj)
        cleanup.commit()
    finally:
        cleanup.close()


def test_registrar_feedback_grava_e_devolve_o_registro(usuario_de_teste):
    session = SessionLocal()
    try:
        registro = registrar_feedback(
            session,
            user_id=usuario_de_teste,
            query="produtos para colchão",
            answer="Temos os produtos X, Y, Z.",
            util=True,
            model_used="gpt-4o-mini",
            sources=["Boletim X.pdf"],
        )
        assert registro.id is not None
        assert registro.util is True
        assert registro.sources == ["Boletim X.pdf"]
    finally:
        session.close()


def test_registrar_feedback_negativo_sem_comentario_nao_levanta(usuario_de_teste):
    """Pedido do usuário: "não é obrigatório responder" — comentário deve
    poder ficar de fora mesmo num feedback negativo."""
    session = SessionLocal()
    try:
        registro = registrar_feedback(
            session, user_id=usuario_de_teste, query="q", answer="a", util=False,
        )
        assert registro.comentario is None
    finally:
        session.close()


def test_obter_licoes_so_traz_feedback_negativo(usuario_de_teste):
    session = SessionLocal()
    try:
        registrar_feedback(session, user_id=usuario_de_teste, query="pergunta útil", answer="a", util=True)
        registrar_feedback(
            session, user_id=usuario_de_teste, query="pergunta ruim", answer="a", util=False,
            comentario="respondeu produto errado",
        )
    finally:
        session.close()

    licoes = obter_licoes_de_feedback(limit=10)
    queries = [l["query"] for l in licoes]
    assert "pergunta ruim" in queries
    assert "pergunta útil" not in queries


def test_obter_licoes_inclui_comentario_quando_houver(usuario_de_teste):
    session = SessionLocal()
    try:
        registrar_feedback(
            session, user_id=usuario_de_teste, query="pergunta com motivo", answer="a", util=False,
            comentario="motivo especifico do erro",
        )
    finally:
        session.close()

    licoes = obter_licoes_de_feedback(limit=10)
    licao = next(l for l in licoes if l["query"] == "pergunta com motivo")
    assert licao["comentario"] == "motivo especifico do erro"


def test_obter_licoes_respeita_limite(usuario_de_teste):
    session = SessionLocal()
    try:
        for i in range(3):
            registrar_feedback(
                session, user_id=usuario_de_teste, query=f"pergunta ruim {i}", answer="a", util=False,
            )
    finally:
        session.close()

    licoes = obter_licoes_de_feedback(limit=2)
    assert len(licoes) <= 2
