"""source refs nas mensagens de conversa

Revision ID: 9b1a2c3d4e5f
Revises: f1c93a7b2d45
Create Date: 2026-09-22

Guarda as referencias baixaveis exibidas pelo agente. `sources` continua como
lista simples de nomes para compatibilidade historica; `source_refs` preserva o
id opaco e a URL de download que a interface usa.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "9b1a2c3d4e5f"
down_revision = "f1c93a7b2d45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages", sa.Column("source_refs", JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "source_refs")
