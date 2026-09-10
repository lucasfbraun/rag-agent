"""
Testes da camada de autorização (Fase 5, tarefa 4).

**Reescrito em 2026-09-10.** Antes eles conferiam a constante
`ROLE_PERMISSIONS` célula por célula contra `docs/spec_rbac.md`. Com perfis
dinâmicos essa constante deixou de existir: a matriz virou dado, semeado pela
migration `b7e1c4d92f30`.

O que os testes protegem continua sendo o mesmo — que a matriz da spec seja
respeitada — só que agora o alvo é o perfil semeado no BANCO, e não uma
constante. Isso é mais forte, não mais fraco: passou a verificar o que o
sistema realmente usa para autorizar, em vez de uma tabela paralela que
poderia divergir dele.

Continua sem depender de persistir usuário: o `User` é montado em memória,
apontando para um `Perfil` real lido do banco.
"""
import pytest
from fastapi import HTTPException

from app.auth.perfil_service import obter_por_slug
from app.auth.permissions import Permission, has_permission, require_permission
from app.db import SessionLocal
from app.models import Perfil, User, UserStatus


@pytest.fixture(scope="module")
def perfis():
    """Os cinco perfis semeados pela migration, lidos uma vez."""
    s = SessionLocal()
    try:
        encontrados = {
            slug: obter_por_slug(s, slug)
            for slug in ("vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti")
        }
        faltando = [slug for slug, p in encontrados.items() if p is None]
        if faltando:
            pytest.fail(f"perfis semeados ausentes no banco: {faltando}")
        # Materializa as permissões antes de fechar a sessão.
        for perfil in encontrados.values():
            perfil.nomes_de_permissoes()
        yield encontrados
    finally:
        s.close()


def _usuario_com(perfil: Perfil) -> User:
    return User(
        username="x", nome="x", email="x@x.local",
        perfil=perfil, status=UserStatus.ATIVO,
    )


def test_os_cinco_perfis_da_spec_existem(perfis):
    """Um perfil ausente faria todo usuário dele cair sem permissão nenhuma."""
    assert set(perfis) == {"vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti"}


def test_admin_ti_tem_todas_as_permissoes(perfis):
    admin = _usuario_com(perfis["admin_ti"])
    for permissao in Permission:
        assert has_permission(admin, permissao), f"Admin TI deveria ter {permissao}"


def test_vendedor_ve_catalogo_mas_nao_laudo_completo_nem_custos(perfis):
    vendedor = _usuario_com(perfis["vendedor"])
    assert has_permission(vendedor, Permission.VIEW_CATALOG)
    assert has_permission(vendedor, Permission.VIEW_HOMOLOGATION_SUMMARY)
    assert not has_permission(vendedor, Permission.VIEW_HOMOLOGATION_FULL)
    assert not has_permission(vendedor, Permission.VIEW_COSTS)
    assert not has_permission(vendedor, Permission.EDIT_TEMPLATE)
    assert not has_permission(vendedor, Permission.MANAGE_USERS)


def test_tecnico_ve_laudo_completo_mas_nao_custos_pendencia_negada_por_padrao(perfis):
    """docs/spec_rbac.md marca custos pro Técnico como 'Pendência' (a proposta
    diz 'Opcional' sem definir a regra) — o padrão adotado é negar até haver
    decisão de negócio."""
    tecnico = _usuario_com(perfis["tecnico"])
    assert has_permission(tecnico, Permission.VIEW_HOMOLOGATION_FULL)
    assert not has_permission(tecnico, Permission.VIEW_COSTS)
    assert not has_permission(tecnico, Permission.EDIT_TEMPLATE)


def test_gestor_e_quimico_pd_editam_template_e_veem_custos_mas_nao_excluem(perfis):
    """A proposta não distingue editar de excluir template — deny-by-default
    na dúvida."""
    for slug in ("gestor", "quimico_pd"):
        usuario = _usuario_com(perfis[slug])
        assert has_permission(usuario, Permission.EDIT_TEMPLATE), slug
        assert has_permission(usuario, Permission.VIEW_COSTS), slug
        assert not has_permission(usuario, Permission.DELETE_TEMPLATE), slug


def test_somente_admin_ti_administra_entre_os_perfis_semeados(perfis):
    for slug, perfil in perfis.items():
        usuario = _usuario_com(perfil)
        esperado = slug == "admin_ti"
        assert has_permission(usuario, Permission.MANAGE_USERS) is esperado, slug
        assert has_permission(usuario, Permission.MANAGE_INGESTION) is esperado, slug


# --- a dependency de FastAPI -----------------------------------------------

def test_require_permission_deixa_passar_quem_tem_a_permissao(perfis):
    dependency = require_permission(Permission.VIEW_CATALOG)
    usuario = _usuario_com(perfis["vendedor"])
    assert dependency(current_user=usuario) is usuario


def test_require_permission_barra_quem_nao_tem_a_permissao_com_403(perfis):
    dependency = require_permission(Permission.MANAGE_USERS)
    with pytest.raises(HTTPException) as erro:
        dependency(current_user=_usuario_com(perfis["vendedor"]))
    assert erro.value.status_code == 403
    # A mensagem cita o NOME do perfil, que agora é livre — não mais um enum.
    assert perfis["vendedor"].nome in erro.value.detail


def test_require_permission_admin_ti_passa_em_qualquer_permissao(perfis):
    admin = _usuario_com(perfis["admin_ti"])
    for permissao in Permission:
        assert require_permission(permissao)(current_user=admin) is admin


def test_usuario_sem_perfil_nao_recebe_permissao_nenhuma():
    """Estado que não deveria existir (a coluna é NOT NULL), mas se acontecer o
    resultado seguro é negar tudo — nunca estourar no meio de uma requisição."""
    orfao = User(username="x", nome="x", email="x@x.local", status=UserStatus.ATIVO)
    for permissao in Permission:
        assert has_permission(orfao, permissao) is False
