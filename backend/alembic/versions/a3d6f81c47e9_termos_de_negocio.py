"""termos de negocio: apelido da empresa para uma linha do catalogo

Tabela PRÓPRIA, e não uma quarta modalidade de `itens_treinamento`, porque o
consumo é diferente em natureza: correção/conhecimento/exemplo são indexados no
Qdrant e recuperados por SIMILARIDADE para dentro do prompt, enquanto um
apelido de linha precisa ser lido DETERMINISTICAMENTE pelo resolvedor de
classificações. Cadastrado como treinamento, o agente "saberia" em prosa que TH
é a linha de elastômeros e a consulta estrutural continuaria devolvendo zero.

Revision ID: a3d6f81c47e9
Revises: f1c93a7b2d45
Create Date: 2026-09-21
"""
import sqlalchemy as sa
from alembic import op
# `sa.dialects.postgresql` NAO resolve por atributo: o submodulo precisa ser
# importado. Sem isto a migration estoura AttributeError — e como
# `app/startup.py` roda `alembic upgrade head` antes do uvicorn, o container
# entraria em crash-loop no deploy.
from sqlalchemy.dialects import postgresql

revision = "a3d6f81c47e9"
down_revision = "f1c93a7b2d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # O enum `status_documento` já existe (criado por c2f8a05b71d4); referenciar
    # com create_type=False evita o "type already exists" que derruba a
    # migração no meio.
    #
    # PRECISA ser `postgresql.ENUM`, e NÃO `sa.Enum`: `sa.Enum` aceita
    # `create_type` no `**kw` e o DESCARTA em silêncio — o `op.create_table`
    # então emite `CREATE TYPE status_documento` com checkfirst=False (ver
    # `alembic.ddl.impl.DefaultImpl.create_table`) num banco onde o tipo já
    # existe, e o deploy entra em crash-loop. Mesmo desenho das migrations
    # c2f8a05b71d4 e d4a91c37e2b8, que compartilham este enum.
    #
    # E os rótulos são MAIÚSCULOS: o SQLAlchemy grava o NOME do membro do
    # enum Python, não o valor, e foi assim que o tipo nasceu no banco.
    # `server_default="pendente"` seria `invalid input value for enum`.
    status_documento = postgresql.ENUM(
        "PENDENTE", "APROVADO", "REJEITADO", name="status_documento", create_type=False
    )

    op.create_table(
        "termos_de_negocio",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("classificacao", sa.String(200), nullable=False),
        sa.Column("classificacao_rotulo", sa.String(200), nullable=False),
        sa.Column("termo", sa.String(200), nullable=False),
        sa.Column("termo_normalizado", sa.String(200), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("status", status_documento, nullable=False, server_default="PENDENTE"),
        sa.Column("motivo_decisao", sa.Text(), nullable=True),
        sa.Column(
            "criado_por_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "decidido_por_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "classificacao_rotulo",
            "termo_normalizado",
            name="uq_termo_de_negocio_classificacao_termo",
        ),
    )
    op.create_index(
        "ix_termos_de_negocio_classificacao_rotulo",
        "termos_de_negocio",
        ["classificacao_rotulo"],
    )
    # O índice que importa para o caminho quente: o resolvedor busca por termo.
    op.create_index(
        "ix_termos_de_negocio_termo_normalizado",
        "termos_de_negocio",
        ["termo_normalizado"],
    )
    op.create_index("ix_termos_de_negocio_status", "termos_de_negocio", ["status"])
    op.create_index(
        "ix_termos_de_negocio_criado_por_id", "termos_de_negocio", ["criado_por_id"]
    )
    op.create_index("ix_termos_de_negocio_created_at", "termos_de_negocio", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_termos_de_negocio_created_at", table_name="termos_de_negocio")
    op.drop_index("ix_termos_de_negocio_criado_por_id", table_name="termos_de_negocio")
    op.drop_index("ix_termos_de_negocio_status", table_name="termos_de_negocio")
    op.drop_index("ix_termos_de_negocio_termo_normalizado", table_name="termos_de_negocio")
    op.drop_index(
        "ix_termos_de_negocio_classificacao_rotulo", table_name="termos_de_negocio"
    )
    op.drop_table("termos_de_negocio")
    # O enum `status_documento` NÃO é removido: ele é compartilhado com
    # documentos_enviados e itens_treinamento, e derrubá-lo aqui quebraria as
    # duas tabelas.
