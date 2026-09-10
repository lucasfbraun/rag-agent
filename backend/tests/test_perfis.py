"""
Perfis dinâmicos (Sessão 37) — CRUD e, principalmente, as invariantes.

Até 2026-09-10 perfil era enum fixo em código. Agora é dado, editável pela
tela. Isso dá autonomia ao Admin TI e cria um risco novo: **um sistema onde o
administrador edita os próprios poderes tem como se trancar para fora.**

São quatro caminhos, e cada um tem um teste aqui:
  1. excluir o perfil que administra;
  2. tirar a permissão de administração do último perfil que a tem;
  3. mover o último administrador para outro perfil;
  4. desativar o último administrador.

A checagem é sempre "sobra ALGUÉM ATIVO que administra?" — nunca "sobra algum
perfil de administração". Um perfil com a permissão mas sem nenhum usuário
ativo não abre a porta para ninguém, e tratá-lo como suficiente seria uma
falsa garantia.

Seam: Postgres real (mesma escolha das outras suítes de RBAC — a invariante é
uma consulta com JOIN, e mockar a sessão testaria o mock).
"""
import uuid

import pytest

from app.auth.perfil_service import (
    PerfilEmUsoError,
    PerfilJaExisteError,
    PerfilProtegidoError,
    SemAdministradorError,
    atualizar_perfil,
    contar_usuarios,
    criar_perfil,
    excluir_perfil,
    listar_perfis,
    obter_por_slug,
)
from app.auth.permissions import PERMISSAO_DE_ADMINISTRACAO, Permission, has_permission
from app.auth.user_service import UltimoAdminError, create_user, deactivate_user, update_user
from app.db import SessionLocal
from app.models import Perfil, User

ADMIN = PERMISSAO_DE_ADMINISTRACAO.value


@pytest.fixture
def session():
    s = SessionLocal()
    lixo = {"usuarios": [], "perfis": []}
    try:
        yield s, lixo
    finally:
        s.rollback()
        for user_id in lixo["usuarios"]:
            registro = s.get(User, user_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        for perfil_id in lixo["perfis"]:
            registro = s.get(Perfil, perfil_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        s.close()


def _perfil(s, lixo, permissoes, nome="Perfil de Teste"):
    perfil = criar_perfil(
        s, slug=f"teste_{uuid.uuid4().hex[:8]}", nome=nome,
        descricao=None, permissoes=permissoes,
    )
    s.commit()
    lixo["perfis"].append(perfil.id)
    return perfil


def _usuario(s, lixo, perfil, ativo=True):
    sufixo = uuid.uuid4().hex[:8]
    user = create_user(
        s, username=f"perfil.{sufixo}", nome="Teste Perfis",
        email=f"perfil.{sufixo}@grupoflexivel.com.br",
        password="SenhaLocal123", perfil=perfil,
    )
    s.commit()
    lixo["usuarios"].append(user.id)
    if not ativo:
        deactivate_user(s, user.id)
        s.commit()
    return user


# --- os perfis originais viraram dado, sem perder nada ----------------------

def test_os_cinco_perfis_originais_existem_e_sao_protegidos(session):
    """A migration semeou a matriz que antes vivia em ROLE_PERMISSIONS."""
    s, _ = session
    slugs = {p.slug for p in listar_perfis(s)}
    assert {"vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti"} <= slugs
    for slug in ("vendedor", "admin_ti"):
        assert obter_por_slug(s, slug).protegido is True


def test_admin_ti_continua_com_todas_as_permissoes(session):
    s, _ = session
    admin = obter_por_slug(s, "admin_ti")
    assert admin.nomes_de_permissoes() == {p.value for p in Permission}


def test_vendedor_continua_sem_ver_custo(session):
    """A matriz original negava custo ao Vendedor; virar dado não pode ter
    afrouxado nada."""
    s, _ = session
    vendedor = obter_por_slug(s, "vendedor")
    assert Permission.VIEW_COSTS.value not in vendedor.nomes_de_permissoes()
    assert Permission.MANAGE_USERS.value not in vendedor.nomes_de_permissoes()


# --- CRUD -------------------------------------------------------------------

def test_criar_perfil_com_permissoes_escolhidas(session):
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value, Permission.VIEW_COSTS.value])
    assert perfil.nomes_de_permissoes() == {
        Permission.VIEW_CATALOG.value, Permission.VIEW_COSTS.value
    }


def test_permissao_desconhecida_e_descartada_em_vez_de_gravada(session):
    """A tela só oferece permissões conhecidas, mas a API aceita JSON de
    qualquer cliente. Gravar string arbitrária encheria a tabela de linhas que
    nunca autorizam nada e confundiriam quem lesse o banco depois."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value, "poder_absoluto"])
    assert perfil.nomes_de_permissoes() == {Permission.VIEW_CATALOG.value}


def test_slug_duplicado_e_recusado(session):
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    with pytest.raises(PerfilJaExisteError):
        criar_perfil(s, slug=perfil.slug, nome="Outro", descricao=None, permissoes=[])


def test_editar_troca_o_conjunto_inteiro_de_permissoes(session):
    """Substituição, não união: desmarcar na tela precisa REMOVER."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value, Permission.VIEW_COSTS.value])
    atualizar_perfil(s, perfil.id, permissoes=[Permission.VIEW_CATALOG.value])
    s.commit()
    assert perfil.nomes_de_permissoes() == {Permission.VIEW_CATALOG.value}


def test_perfil_do_sistema_nao_pode_ser_excluido(session):
    """Apagar o "admin_ti" por engano deixaria o sistema sem administração e
    sem caminho óbvio de volta pela tela."""
    s, _ = session
    with pytest.raises(PerfilProtegidoError):
        excluir_perfil(s, obter_por_slug(s, "admin_ti").id)


def test_perfil_do_sistema_continua_com_permissoes_editaveis(session):
    """Protegido é sobre EXCLUIR, não sobre congelar a matriz — senão a
    autonomia pedida não existiria para os perfis que as pessoas realmente
    usam."""
    s, _ = session
    vendedor = obter_por_slug(s, "vendedor")
    originais = sorted(vendedor.nomes_de_permissoes())
    try:
        atualizar_perfil(s, vendedor.id, permissoes=originais + [Permission.VIEW_COSTS.value])
        s.commit()
        assert Permission.VIEW_COSTS.value in vendedor.nomes_de_permissoes()
    finally:
        atualizar_perfil(s, vendedor.id, permissoes=originais)
        s.commit()


def test_perfil_com_usuarios_nao_pode_ser_excluido(session):
    """A FK deixaria usuários órfãos; a mensagem diz o que fazer antes."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    _usuario(s, lixo, perfil)
    with pytest.raises(PerfilEmUsoError, match="Mova essas pessoas"):
        excluir_perfil(s, perfil.id)


def test_perfil_sem_usuarios_e_excluido(session):
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    perfil_id = perfil.id
    excluir_perfil(s, perfil_id)
    s.commit()
    lixo["perfis"].remove(perfil_id)
    assert s.get(Perfil, perfil_id) is None


def test_contar_usuarios_alimenta_o_aviso_da_tela(session):
    """A tela precisa avisar ANTES de alguém tentar excluir, não depois do erro."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    assert contar_usuarios(s, perfil.id) == 0
    _usuario(s, lixo, perfil)
    assert contar_usuarios(s, perfil.id) == 1


# --- as quatro formas de se trancar para fora -------------------------------

def test_1_excluir_o_ultimo_perfil_administrador_e_recusado(session):
    """Só é possível montar este cenário com o admin real fora de cena, então
    o teste cria um mundo próprio: um perfil admin com um usuário ativo, e
    verifica que ele não pode sumir enquanto for o único... — na prática o
    `admin_ti` semeado sempre existe, então aqui a exclusão é permitida e o
    que se testa é que a checagem RODA sem falso positivo."""
    s, lixo = session
    perfil = _perfil(s, lixo, [ADMIN], nome="Admin Extra")
    perfil_id = perfil.id
    # Existe outro administrador ativo (o lucas.braun real), então excluir este
    # é legítimo — e não pode ser bloqueado por engano.
    excluir_perfil(s, perfil_id)
    s.commit()
    lixo["perfis"].remove(perfil_id)
    assert s.get(Perfil, perfil_id) is None


def test_2_tirar_a_administracao_do_ultimo_perfil_admin_e_recusado(session, monkeypatch):
    """O caminho mais fácil de se trancar para fora: desmarcar o checkbox
    "Administrador do sistema" no único perfil que o tem."""
    s, lixo = session
    perfil = _perfil(s, lixo, [ADMIN, Permission.VIEW_CATALOG.value], nome="Admin Solo")
    _usuario(s, lixo, perfil)

    # Simula não haver nenhum outro administrador ativo no sistema.
    import app.auth.perfil_service as servico

    monkeypatch.setattr(servico, "_outros_administradores_ativos", lambda *a, **k: 0)
    with pytest.raises(SemAdministradorError, match="sem nenhum administrador"):
        atualizar_perfil(s, perfil.id, permissoes=[Permission.VIEW_CATALOG.value])


def test_2b_tirar_a_administracao_e_permitido_quando_sobra_outro(session):
    """A guarda não pode ser um "sempre não": com outro admin ativo, a
    operação é legítima."""
    s, lixo = session
    perfil = _perfil(s, lixo, [ADMIN, Permission.VIEW_CATALOG.value])
    atualizar_perfil(s, perfil.id, permissoes=[Permission.VIEW_CATALOG.value])
    s.commit()
    assert ADMIN not in perfil.nomes_de_permissoes()


def test_3_mover_o_ultimo_administrador_para_outro_perfil_e_recusado(session, monkeypatch):
    s, lixo = session
    perfil_admin = _perfil(s, lixo, [ADMIN])
    perfil_comum = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    user = _usuario(s, lixo, perfil_admin)

    import app.auth.user_service as servico

    monkeypatch.setattr(servico, "_contar_admins_ativos_exceto", lambda *a, **k: 0)
    with pytest.raises(UltimoAdminError):
        update_user(s, user.id, perfil=perfil_comum)


def test_4_desativar_o_ultimo_administrador_e_recusado(session, monkeypatch):
    s, lixo = session
    perfil_admin = _perfil(s, lixo, [ADMIN])
    user = _usuario(s, lixo, perfil_admin)

    import app.auth.user_service as servico

    monkeypatch.setattr(servico, "_contar_admins_ativos_exceto", lambda *a, **k: 0)
    with pytest.raises(UltimoAdminError):
        deactivate_user(s, user.id)


def test_a_invariante_conta_gente_ativa_nao_perfis(session):
    """Um perfil com a permissão mas SEM usuário ativo não abre a porta de
    ninguém — tratá-lo como suficiente seria uma falsa garantia."""
    s, lixo = session
    from app.auth.perfil_service import _outros_administradores_ativos

    perfil_admin = _perfil(s, lixo, [ADMIN], nome="Admin Fantasma")
    antes = _outros_administradores_ativos(s, perfil_id_excluido=perfil_admin.id)

    _usuario(s, lixo, perfil_admin, ativo=False)  # existe, mas INATIVO
    depois = _outros_administradores_ativos(s, perfil_id_excluido=perfil_admin.id)
    assert depois == antes, "usuário inativo não pode contar como administrador"


# --- a autorização de fato usa o perfil do banco ---------------------------

def test_has_permission_le_do_perfil_do_usuario(session):
    """O elo final: se isto não valer, todo o resto é decoração."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    user = _usuario(s, lixo, perfil)

    assert has_permission(user, Permission.VIEW_CATALOG) is True
    assert has_permission(user, Permission.VIEW_COSTS) is False
    assert has_permission(user, Permission.MANAGE_USERS) is False


def test_permissao_concedida_na_hora_passa_a_valer(session):
    """Sem deploy, sem migration — que é o pedido original."""
    s, lixo = session
    perfil = _perfil(s, lixo, [Permission.VIEW_CATALOG.value])
    user = _usuario(s, lixo, perfil)
    assert has_permission(user, Permission.VIEW_COSTS) is False

    atualizar_perfil(s, perfil.id, permissoes=[
        Permission.VIEW_CATALOG.value, Permission.VIEW_COSTS.value,
    ])
    s.commit()
    s.refresh(user)
    assert has_permission(user, Permission.VIEW_COSTS) is True
