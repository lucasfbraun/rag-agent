"""treinamento do agente: correcao, conhecimento e exemplo

Revision ID: d4a91c37e2b8
Revises: c2f8a05b71d4
Create Date: 2026-09-10

Ver docs/spec_treinamento.md. Reaproveita o enum `status_documento` da fila de
aprovação (Sessão 38) em vez de criar um paralelo: os estados são exatamente os
mesmos — pendente, aprovado, rejeitado — e dois enums idênticos com nomes
diferentes seriam duas coisas para manter em sincronia sem ganho nenhum.

Concede `train_agent` a todos os perfis semeados (quem usa o agente é quem
percebe a resposta errada) e `approve_training` só ao Admin TI. Perfis criados
pela tela não recebem nada — só o Admin TI sabe o que um perfil novo deveria
poder fazer.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision = "d4a91c37e2b8"
down_revision = "c2f8a05b71d4"
branch_labels = None
depends_on = None

TODOS_OS_PERFIS = ("vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti")


def upgrade() -> None:
    tipo = sa.Enum("CORRECAO", "CONHECIMENTO", "EXEMPLO", name="tipo_treinamento")
    tipo.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "itens_treinamento",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tipo",
            postgresql.ENUM(
                "CORRECAO", "CONHECIMENTO", "EXEMPLO",
                name="tipo_treinamento", create_type=False,
            ),
            nullable=False, index=True,
        ),
        sa.Column("pergunta", sa.Text(), nullable=False),
        sa.Column("resposta", sa.Text(), nullable=False),
        sa.Column("resposta_original", sa.Text(), nullable=True),
        # Mesmo enum da fila de documentos — os estados são idênticos.
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDENTE", "APROVADO", "REJEITADO",
                name="status_documento", create_type=False,
            ),
            nullable=False, server_default="PENDENTE", index=True,
        ),
        sa.Column("motivo_decisao", sa.Text(), nullable=True),
        sa.Column("criado_por_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("decidido_por_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"), index=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
    )

    conexao = op.get_bind()
    for slug in TODOS_OS_PERFIS:
        conexao.execute(
            sa.text(
                "insert into perfil_permissoes (perfil_id, permissao) "
                "select id, 'train_agent' from perfis where slug = :slug "
                "on conflict do nothing"
            ),
            {"slug": slug},
        )
    conexao.execute(
        sa.text(
            "insert into perfil_permissoes (perfil_id, permissao) "
            "select id, 'approve_training' from perfis where slug = 'admin_ti' "
            "on conflict do nothing"
        )
    )


def downgrade() -> None:
    conexao = op.get_bind()
    conexao.execute(
        sa.text(
            "delete from perfil_permissoes "
            "where permissao in ('train_agent', 'approve_training')"
        )
    )
    op.drop_table("itens_treinamento")
    # `status_documento` NÃO é dropado: ele pertence à fila de documentos, que
    # continua existindo depois deste downgrade.
    sa.Enum(name="tipo_treinamento").drop(op.get_bind(), checkfirst=True)
