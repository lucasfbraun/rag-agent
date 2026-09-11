"""escopo e fonte dos itens de treinamento

Revision ID: e7c4a2f913b0
Revises: d4a91c37e2b8
Create Date: 2026-09-10
"""
import sqlalchemy as sa
from alembic import op

revision = "e7c4a2f913b0"
down_revision = "d4a91c37e2b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("itens_treinamento", sa.Column("produto", sa.String(200), nullable=True))
    op.add_column("itens_treinamento", sa.Column("aplicacao", sa.String(500), nullable=True))
    op.add_column("itens_treinamento", sa.Column("fonte", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("itens_treinamento", "fonte")
    op.drop_column("itens_treinamento", "aplicacao")
    op.drop_column("itens_treinamento", "produto")
