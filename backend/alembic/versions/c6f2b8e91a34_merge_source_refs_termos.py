"""merge source refs e termos de negocio

Revision ID: c6f2b8e91a34
Revises: 9b1a2c3d4e5f, a3d6f81c47e9
Create Date: 2026-09-23

Une duas migrations que partiram de `f1c93a7b2d45` em paralelo. Sem esta merge
revision, `alembic upgrade head` ve dois heads e o backend entra em crash-loop
no startup.
"""
from typing import Sequence, Union

revision: str = "c6f2b8e91a34"
down_revision: Union[str, Sequence[str], None] = ("9b1a2c3d4e5f", "a3d6f81c47e9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
