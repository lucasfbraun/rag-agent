"""perfis dinamicos: enum user_role vira tabela perfis + perfil_permissoes

Revision ID: b7e1c4d92f30
Revises: 6c8f4a2d91e0
Create Date: 2026-09-10

Converte `users.perfil` (enum do Postgres) em `users.perfil_id` (FK para
`perfis`), para o Admin TI criar/editar/excluir perfis pela tela sem migration
nem deploy.

A ordem importa e não é acidental: as tabelas são criadas e SEMEADAS antes de a
coluna nova existir, a coluna é preenchida a partir do enum antigo e só então
passa a NOT NULL. Criar a FK NOT NULL de cara falharia com o banco já populado,
e uma migration que falha no meio deixa o schema num estado que ninguém sabe
descrever.

O enum `user_role` é DELIBERADAMENTE mantido no banco depois do upgrade. Ele é o
que o `downgrade` usa para reconstruir a coluna antiga — e um enum órfão custa
nada, enquanto um downgrade que não funciona custa uma restauração de backup.
(É o mesmo problema catalogado como AUD-009, aqui evitado de propósito.)
"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "b7e1c4d92f30"
down_revision = "6c8f4a2d91e0"
branch_labels = None
depends_on = None


# Espelha ROLE_PERMISSIONS de app/auth/permissions.py NO MOMENTO desta migration.
# Cópia literal de propósito: migration é história, não pode mudar de resultado
# porque alguém editou a matriz em código no ano que vem.
PERFIS_INICIAIS = [
    ("vendedor", "Vendedor", "Vendas técnicas em campo.", [
        "view_catalog", "view_homologation_summary", "select_template",
    ]),
    ("tecnico", "Técnico de Aplicação", "Engenharia de aplicação.", [
        "view_catalog", "view_homologation_summary", "view_homologation_full",
        "select_template",
    ]),
    ("gestor", "Gestor Comercial", "Gestão da equipe comercial.", [
        "view_catalog", "view_homologation_summary", "view_homologation_full",
        "select_template", "edit_template", "view_costs",
    ]),
    ("quimico_pd", "Químico / P&D", "Pesquisa e desenvolvimento.", [
        "view_catalog", "view_homologation_summary", "view_homologation_full",
        "select_template", "edit_template", "view_costs",
    ]),
    ("admin_ti", "Admin TI", "Administração do sistema.", [
        "view_catalog", "view_homologation_summary", "view_homologation_full",
        "select_template", "edit_template", "delete_template", "view_costs",
        "manage_users", "manage_ingestion",
    ]),
]


def upgrade() -> None:
    op.create_table(
        "perfis",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("nome", sa.String(100), nullable=False),
        sa.Column("descricao", sa.String(300), nullable=True),
        sa.Column("protegido", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "perfil_permissoes",
        sa.Column("perfil_id", UUID(as_uuid=True),
                  sa.ForeignKey("perfis.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permissao", sa.String(60), primary_key=True),
    )

    conexao = op.get_bind()
    ids_por_slug = {}
    for slug, nome, descricao, permissoes in PERFIS_INICIAIS:
        perfil_id = uuid.uuid4()
        ids_por_slug[slug] = perfil_id
        conexao.execute(
            sa.text(
                "insert into perfis (id, slug, nome, descricao, protegido, created_at, updated_at) "
                "values (:id, :slug, :nome, :descricao, true, now(), now())"
            ),
            {"id": perfil_id, "slug": slug, "nome": nome, "descricao": descricao},
        )
        for permissao in permissoes:
            conexao.execute(
                sa.text(
                    "insert into perfil_permissoes (perfil_id, permissao) values (:pid, :perm)"
                ),
                {"pid": perfil_id, "perm": permissao},
            )

    # Nullable primeiro: a tabela já tem linhas, e NOT NULL sem default falharia.
    op.add_column("users", sa.Column("perfil_id", UUID(as_uuid=True), nullable=True))

    # O enum é gravado em MAIÚSCULA pelo SQLAlchemy (nome do membro, não o
    # valor) — `ADMIN_TI`, não `admin_ti`. Comparar com o slug direto não
    # casaria nenhuma linha, e todo mundo terminaria sem perfil.
    for slug, perfil_id in ids_por_slug.items():
        conexao.execute(
            sa.text("update users set perfil_id = :pid where perfil::text = :enum_nome"),
            {"pid": perfil_id, "enum_nome": slug.upper()},
        )

    orfaos = conexao.execute(
        sa.text("select count(*) from users where perfil_id is null")
    ).scalar_one()
    if orfaos:
        # Interrompe em vez de inventar um perfil: um usuário com perfil que
        # não existe no catálogo é um dado que precisa de decisão humana, e
        # atribuir o mais restritivo em silêncio esconderia o problema.
        raise RuntimeError(
            f"{orfaos} usuário(s) com perfil fora do catálogo conhecido — "
            "migration interrompida antes de perder o dado."
        )

    op.alter_column("users", "perfil_id", nullable=False)
    op.create_index("ix_users_perfil_id", "users", ["perfil_id"])
    op.create_foreign_key("fk_users_perfil_id", "users", "perfis", ["perfil_id"], ["id"])
    op.drop_column("users", "perfil")


def downgrade() -> None:
    conexao = op.get_bind()
    # O tipo `user_role` continua existindo (nunca foi dropado no upgrade),
    # então recriar a coluna é direto.
    op.add_column(
        "users",
        sa.Column("perfil", sa.Enum(name="user_role", create_type=False), nullable=True),
    )
    conexao.execute(
        sa.text(
            "update users set perfil = upper(p.slug)::user_role "
            "from perfis p where p.id = users.perfil_id"
        )
    )
    # Perfil criado pela tela não existe no enum antigo: sem um valor válido, o
    # downgrade não tem para onde mandar essas pessoas. O perfil mais restritivo
    # é a única escolha segura, e fica registrado aqui que ela acontece.
    conexao.execute(sa.text("update users set perfil = 'VENDEDOR'::user_role where perfil is null"))
    op.alter_column("users", "perfil", nullable=False)

    op.drop_constraint("fk_users_perfil_id", "users", type_="foreignkey")
    op.drop_index("ix_users_perfil_id", table_name="users")
    op.drop_column("users", "perfil_id")
    op.drop_table("perfil_permissoes")
    op.drop_table("perfis")
