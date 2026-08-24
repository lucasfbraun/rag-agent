"""
Testes da camada de autorização (Fase 5, tarefa 4). Não precisa de Postgres —
User é só um objeto Python aqui, não é persistido; testa a lógica pura de
has_permission()/require_permission() contra a matriz de docs/spec_rbac.md.
"""
import pytest
from fastapi import HTTPException

from app.models import Role, User, UserStatus
from app.auth.permissions import Permission, ROLE_PERMISSIONS, has_permission, require_permission


def _fake_user(perfil: Role) -> User:
    return User(username="x", nome="x", email="x@x.local", perfil=perfil, status=UserStatus.ATIVO)


def test_todos_os_5_perfis_tem_entrada_na_matriz():
    """Nenhum perfil pode ficar de fora do dict — cairia no .get(default=set())
    e silenciosamente perderia toda permissão sem ninguém perceber."""
    assert set(ROLE_PERMISSIONS.keys()) == set(Role)


def test_admin_ti_tem_todas_as_permissoes():
    admin = _fake_user(Role.ADMIN_TI)
    for permission in Permission:
        assert has_permission(admin, permission), f"Admin TI deveria ter {permission}"


def test_vendedor_ve_catalogo_mas_nao_laudo_completo_nem_custos():
    vendedor = _fake_user(Role.VENDEDOR)
    assert has_permission(vendedor, Permission.VIEW_CATALOG)
    assert has_permission(vendedor, Permission.VIEW_HOMOLOGATION_SUMMARY)
    assert not has_permission(vendedor, Permission.VIEW_HOMOLOGATION_FULL)
    assert not has_permission(vendedor, Permission.VIEW_COSTS)
    assert not has_permission(vendedor, Permission.EDIT_TEMPLATE)
    assert not has_permission(vendedor, Permission.MANAGE_USERS)


def test_tecnico_ve_laudo_completo_mas_nao_custos_pendencia_negada_por_padrao():
    """docs/spec_rbac.md marca custos pro Técnico como 'Pendência' (proposta diz
    'Opcional' sem definir a regra) — o padrão adotado é negar até ter decisão."""
    tecnico = _fake_user(Role.TECNICO)
    assert has_permission(tecnico, Permission.VIEW_HOMOLOGATION_FULL)
    assert not has_permission(tecnico, Permission.VIEW_COSTS)
    assert not has_permission(tecnico, Permission.EDIT_TEMPLATE)


def test_gestor_e_quimico_pd_editam_template_e_veem_custos_mas_nao_excluem():
    for perfil in (Role.GESTOR, Role.QUIMICO_PD):
        user = _fake_user(perfil)
        assert has_permission(user, Permission.EDIT_TEMPLATE)
        assert has_permission(user, Permission.VIEW_COSTS)
        assert not has_permission(user, Permission.DELETE_TEMPLATE)  # pendência -> negado
        assert not has_permission(user, Permission.MANAGE_USERS)


def test_somente_admin_ti_gerencia_usuarios():
    for perfil in Role:
        user = _fake_user(perfil)
        esperado = (perfil == Role.ADMIN_TI)
        assert has_permission(user, Permission.MANAGE_USERS) == esperado


# --- require_permission() (a dependency) --------------------------------------

def test_require_permission_deixa_passar_quem_tem_a_permissao():
    dependency = require_permission(Permission.VIEW_CATALOG)
    vendedor = _fake_user(Role.VENDEDOR)
    assert dependency(current_user=vendedor) is vendedor


def test_require_permission_barra_quem_nao_tem_a_permissao_com_403():
    dependency = require_permission(Permission.MANAGE_USERS)
    vendedor = _fake_user(Role.VENDEDOR)
    with pytest.raises(HTTPException) as exc_info:
        dependency(current_user=vendedor)
    assert exc_info.value.status_code == 403


def test_require_permission_admin_ti_passa_em_qualquer_permissao():
    admin = _fake_user(Role.ADMIN_TI)
    for permission in Permission:
        dependency = require_permission(permission)
        assert dependency(current_user=admin) is admin
