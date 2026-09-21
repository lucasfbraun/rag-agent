"""veredito tecnico das respostas e rastro de recuperacao

Revision ID: f1c93a7b2d45
Revises: e7c4a2f913b0
Create Date: 2026-09-18

Fecha o item 0 de docs/avaliacao_arquitetura_2026-09-18.md ("o que continua em
aberto"): não existe medição da taxa de acerto sobre o acervo real.

Traz três coisas:

1. `conversation_messages.caminho` e `.termos_busca` — o rastro de qual
   mecanismo do motor atendeu e o que a busca procurou. `sources` já existia e
   é reaproveitado como "documentos citados"; não há coluna nova para isso.

2. A tabela `vereditos_tecnicos` — o julgamento de quem conhece o catálogo
   sobre uma resposta que o agente já deu. Ver a docstring do model, que
   explica por que não é `Feedback` nem `ItemTreinamento`.

3. A permissão `validate_answers`, concedida aos perfis que já representam
   conhecimento técnico no sistema — `tecnico`, `quimico_pd` e `admin_ti`.
   `vendedor` e `gestor` ficam de fora: quem pergunta não é quem valida, e o
   valor do dado depende disso. Perfis criados pela tela não recebem nada, na
   mesma disciplina da migration `d4a91c37e2b8` — só o Admin TI sabe o que um
   perfil novo deveria poder fazer.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "f1c93a7b2d45"
down_revision = "e7c4a2f913b0"
branch_labels = None
depends_on = None

PERFIS_TECNICOS = ("tecnico", "quimico_pd", "admin_ti")


def upgrade() -> None:
    # --- 1. rastro de recuperação na mensagem ---
    # Nulos: toda mensagem já gravada foi respondida antes de existir registro
    # de caminho. Preencher com um palpite seria pior que o nulo — o relatório
    # sabe separar "não registrado" de um caminho real.
    op.add_column(
        "conversation_messages", sa.Column("caminho", sa.String(40), nullable=True)
    )
    op.add_column(
        "conversation_messages", sa.Column("termos_busca", JSONB(), nullable=True)
    )
    op.create_index(
        "ix_conversation_messages_caminho", "conversation_messages", ["caminho"]
    )

    # --- 2. veredito técnico ---
    veredito = sa.Enum("CORRETA", "INCORRETA", "INCOMPLETA", name="veredito_resposta")
    veredito.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "vereditos_tecnicos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        # SET NULL, e não CASCADE: a conversa é do vendedor e ele pode apagá-la;
        # o conjunto de regressão não pode encolher junto (ver model).
        sa.Column(
            "mensagem_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("pergunta", sa.Text(), nullable=False),
        sa.Column("resposta", sa.Text(), nullable=False),
        sa.Column("caminho", sa.String(40), nullable=True, index=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("fontes", JSONB(), nullable=True),
        sa.Column("termos_busca", JSONB(), nullable=True),
        sa.Column(
            "veredito",
            postgresql.ENUM(
                "CORRETA", "INCORRETA", "INCOMPLETA",
                name="veredito_resposta", create_type=False,
            ),
            nullable=False,
            index=True,
        ),
        sa.Column("resposta_correta", sa.Text(), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column(
            "avaliado_por_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"), index=True,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "mensagem_id", "avaliado_por_id", name="uq_veredito_mensagem_avaliador"
        ),
    )

    # --- 3. permissão ---
    conexao = op.get_bind()
    for slug in PERFIS_TECNICOS:
        conexao.execute(
            sa.text(
                "insert into perfil_permissoes (perfil_id, permissao) "
                "select id, 'validate_answers' from perfis where slug = :slug "
                "on conflict do nothing"
            ),
            {"slug": slug},
        )


def downgrade() -> None:
    conexao = op.get_bind()
    conexao.execute(
        sa.text("delete from perfil_permissoes where permissao = 'validate_answers'")
    )
    op.drop_table("vereditos_tecnicos")
    sa.Enum(name="veredito_resposta").drop(op.get_bind(), checkfirst=True)
    op.drop_index("ix_conversation_messages_caminho", table_name="conversation_messages")
    op.drop_column("conversation_messages", "termos_busca")
    op.drop_column("conversation_messages", "caminho")
