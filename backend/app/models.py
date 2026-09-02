"""
Models de domínio (Fase 5 — RBAC & Governança).

Campos e enums seguem docs/spec_rbac.md — não adicionar campo sem justificativa lá.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Boolean, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    """5 perfis definidos em docs/proposta_do_projeto_similaridade.md, seção 5."""
    VENDEDOR = "vendedor"
    TECNICO = "tecnico"
    GESTOR = "gestor"
    QUIMICO_PD = "quimico_pd"
    ADMIN_TI = "admin_ti"


class UserStatus(str, enum.Enum):
    ATIVO = "ativo"
    INATIVO = "inativo"


class UserOrigin(str, enum.Enum):
    """Qual Adapter de autenticação criou/gerencia este usuário (ver docs/spec_rbac.md)."""
    MANUAL = "manual"
    LDAP = "ldap"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)

    # Nulo para usuários de origem LDAP — a senha do AD nunca é persistida aqui.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, name="user_status"), nullable=False, default=UserStatus.ATIVO
    )
    perfil: Mapped[Role] = mapped_column(SAEnum(Role, name="user_role"), nullable=False)
    origem: Mapped[UserOrigin] = mapped_column(
        SAEnum(UserOrigin, name="user_origin"), nullable=False, default=UserOrigin.MANUAL
    )

    # Identificador no AD/LDAP quando origem=ldap; vazio para usuários manuais.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Feedback(Base):
    """Avaliação opcional (útil/não útil) que o vendedor dá numa resposta do
    agente — pedido do usuário: fechar o loop de melhoria contínua. O agente
    consulta o feedback negativo mais recente ANTES de responder (ver
    app.feedback_service.obter_licoes_de_feedback, usado em toda consulta por
    app.rag.engine), pra não repetir um padrão já sinalizado como ruim."""
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    util: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Comentário é opcional mesmo quando util=False — o pedido do usuário foi
    # "não é obrigatório responder" (o clique em útil/não útil já basta).
    comentario: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
