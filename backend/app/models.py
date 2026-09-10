"""
Models de domínio (Fase 5 — RBAC & Governança).

Campos e enums seguem docs/spec_rbac.md — não adicionar campo sem justificativa lá.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Boolean, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

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


class Perfil(Base):
    """Perfil de acesso — o que antes era o enum `Role` fixo em código.

    Virou tabela em 2026-09-10 para o Admin TI criar, editar e excluir perfis
    pela tela, sem migration nem deploy.

    O que NÃO virou dado: a lista de permissões possíveis, que segue sendo o
    enum `Permission` em código. Cada permissão só existe porque algum endpoint
    a verifica — deixar criar permissão pela tela produziria um checkbox que
    não protege nada. O que é dado é a ATRIBUIÇÃO permissão↔perfil.
    """
    __tablename__ = "perfis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Identificador estável, usado na API e nos testes ("vendedor", "admin_ti").
    # O `nome` é rótulo de tela e pode ser reescrito sem quebrar integração.
    slug: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    nome: Mapped[str] = mapped_column(String(100), nullable=False)
    descricao: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Perfis semeados pela migration inicial. Protegido impede EXCLUIR e
    # renomear o slug — as permissões continuam editáveis. Sem isso, apagar o
    # "admin_ti" por engano deixaria o sistema sem administração e sem um
    # caminho óbvio de volta pela tela.
    protegido: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    permissoes: Mapped[list["PerfilPermissao"]] = relationship(
        back_populates="perfil", cascade="all, delete-orphan", lazy="selectin"
    )
    usuarios: Mapped[list["User"]] = relationship(back_populates="perfil")

    def nomes_de_permissoes(self) -> set[str]:
        return {p.permissao for p in self.permissoes}


class PerfilPermissao(Base):
    """Uma permissão concedida a um perfil.

    `permissao` é string, não enum do banco: a lista de permissões muda com o
    código (cada release pode acrescentar uma), e um enum do Postgres exigiria
    migration a cada vez. Valor desconhecido é ignorado na checagem, em vez de
    quebrar — assim um downgrade da aplicação não derruba o login de ninguém.
    """
    __tablename__ = "perfil_permissoes"

    perfil_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("perfis.id", ondelete="CASCADE"), primary_key=True
    )
    permissao: Mapped[str] = mapped_column(String(60), primary_key=True)

    perfil: Mapped["Perfil"] = relationship(back_populates="permissoes")


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
    # Perfil deixou de ser enum do Postgres em 2026-09-10: virou linha na tabela
    # `perfis`, para o Admin TI poder criar/editar/excluir perfis pela tela sem
    # migration nem deploy. O enum `Role` continua existindo só como catálogo
    # dos perfis que o sistema semeia (ver Perfil.slug).
    perfil_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("perfis.id"), nullable=False, index=True
    )
    perfil: Mapped["Perfil"] = relationship(back_populates="usuarios", lazy="joined")

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


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="Nova conversa")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow, index=True
    )
    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ConversationMessage.created_at, ConversationMessage.id",
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


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


class StatusDocumento(str, enum.Enum):
    PENDENTE = "pendente"
    APROVADO = "aprovado"
    REJEITADO = "rejeitado"


class DocumentoEnviado(Base):
    """Documento enviado por um usuário, aguardando decisão de um aprovador.

    Fila de aprovação, e não indexação direta, por decisão do usuário: o acervo
    é a fonte que o agente cita como verdade para toda a equipe. Um PDF errado
    entrando sozinho contamina as respostas de todo mundo, e o estrago só
    aparece quando alguém desconfia de uma recomendação.

    O arquivo fica em disco (`caminho`), não no Postgres: são PDFs de MB, e
    bytea transformaria cada listagem da fila numa leitura de tudo. A linha
    guarda o rastro — quem enviou, quem decidiu, quando, e quantos trechos
    entraram no índice.
    """
    __tablename__ = "documentos_enviados"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    nome_arquivo: Mapped[str] = mapped_column(String(255), nullable=False)
    caminho: Mapped[str] = mapped_column(String(500), nullable=False)
    tamanho_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[StatusDocumento] = mapped_column(
        SAEnum(StatusDocumento, name="status_documento"),
        nullable=False, default=StatusDocumento.PENDENTE, index=True,
    )
    # Contexto que o enviador escreve: "boletim novo do AG 2032, revisão 03".
    # Sem isso o aprovador recebe um PDF sem saber por que ele deveria entrar.
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)
    motivo_decisao: Mapped[str | None] = mapped_column(Text, nullable=True)

    enviado_por_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    decidido_por_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    # Quantos trechos foram para o Qdrant na aprovação — é o que permite dizer
    # se a remoção depois apagou tudo.
    chunks_indexados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    enviado_por: Mapped["User"] = relationship(foreign_keys=[enviado_por_id], lazy="joined")
    decidido_por: Mapped["User | None"] = relationship(foreign_keys=[decidido_por_id], lazy="joined")
