"""fila de aprovacao de documentos enviados por usuarios

Revision ID: c2f8a05b71d4
Revises: b7e1c4d92f30
Create Date: 2026-09-10

Cria `documentos_enviados` e concede as permissões novas aos perfis semeados:
`upload_documents` a todos os perfis (quem está em campo é quem tem o boletim
que falta), e `approve_uploads` só ao Admin TI — enviar e aprovar são coisas
diferentes, e o acervo é a fonte que o agente cita como verdade para a equipe
inteira.

Perfis criados PELA TELA não recebem nada aqui: só o Admin TI sabe o que cada
perfil novo deveria poder fazer, e adivinhar concederia acesso que ninguém
pediu.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID

revision = "c2f8a05b71d4"
down_revision = "b7e1c4d92f30"
branch_labels = None
depends_on = None

TODOS_OS_PERFIS = ("vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti")


def upgrade() -> None:
    # O tipo é criado explicitamente e a COLUNA o referencia com
    # `create_type=False`. Sem isso o `create_table` tenta criá-lo de novo e a
    # migration morre com "type already exists" — o SQLAlchemy não sabe que a
    # linha acima já o criou.
    sa.Enum("PENDENTE", "APROVADO", "REJEITADO", name="status_documento").create(
        op.get_bind(), checkfirst=True
    )
    status = postgresql.ENUM(
        "PENDENTE", "APROVADO", "REJEITADO", name="status_documento", create_type=False
    )

    op.create_table(
        "documentos_enviados",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nome_arquivo", sa.String(255), nullable=False),
        sa.Column("caminho", sa.String(500), nullable=False),
        sa.Column("tamanho_bytes", sa.Integer(), nullable=False),
        sa.Column("status", status, nullable=False, server_default="PENDENTE", index=True),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("motivo_decisao", sa.Text(), nullable=True),
        sa.Column("enviado_por_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("decidido_por_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("chunks_indexados", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()"), index=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
    )

    conexao = op.get_bind()
    for slug in TODOS_OS_PERFIS:
        conexao.execute(
            sa.text(
                "insert into perfil_permissoes (perfil_id, permissao) "
                "select id, 'upload_documents' from perfis where slug = :slug "
                "on conflict do nothing"
            ),
            {"slug": slug},
        )
    conexao.execute(
        sa.text(
            "insert into perfil_permissoes (perfil_id, permissao) "
            "select id, 'approve_uploads' from perfis where slug = 'admin_ti' "
            "on conflict do nothing"
        )
    )


def downgrade() -> None:
    conexao = op.get_bind()
    conexao.execute(
        sa.text(
            "delete from perfil_permissoes "
            "where permissao in ('upload_documents', 'approve_uploads')"
        )
    )
    op.drop_table("documentos_enviados")
    sa.Enum(name="status_documento").drop(op.get_bind(), checkfirst=True)
