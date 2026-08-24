"""
Testes de integração do service de usuários (Fase 5 — RBAC & Governança).
Requer Postgres real (mesmo padrão de test_models.py).
"""
import uuid

import pytest

from app.db import SessionLocal
from app.models import Role, UserStatus
from app.auth.security import hash_password, verify_password, SenhaFracaError
from app.auth.user_service import (
    create_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    update_user,
    set_password,
    deactivate_user,
    UsuarioJaExisteError,
    UsuarioNaoEncontradoError,
)


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _unique():
    return uuid.uuid4().hex[:8]


# --- security.py -----------------------------------------------------------

def test_hash_password_nunca_retorna_a_senha_em_texto_puro():
    h = hash_password("senha_segura_123")
    assert h != "senha_segura_123"
    assert "senha_segura_123" not in h


def test_verify_password_aceita_senha_correta():
    h = hash_password("senha_segura_123")
    assert verify_password("senha_segura_123", h) is True


def test_verify_password_rejeita_senha_errada():
    h = hash_password("senha_segura_123")
    assert verify_password("senha_errada", h) is False


def test_verify_password_com_hash_vazio_nao_lanca_e_nega():
    assert verify_password("qualquer_coisa", "") is False


def test_hash_password_rejeita_senha_curta():
    with pytest.raises(SenhaFracaError):
        hash_password("123")


# --- user_service.py ---------------------------------------------------------

def test_create_user_persiste_e_nao_guarda_senha_em_texto_puro(session):
    u = _unique()
    user = create_user(
        session, username=f"vend_{u}", nome="Vendedor Teste", email=f"vend_{u}@teste.local",
        password="senha_segura_123", perfil=Role.VENDEDOR,
    )
    assert user.id is not None
    assert user.password_hash != "senha_segura_123"
    assert verify_password("senha_segura_123", user.password_hash) is True


def test_create_user_username_duplicado_levanta_erro_de_dominio(session):
    u = _unique()
    create_user(session, username=f"dup_{u}", nome="A", email=f"a_{u}@teste.local",
                password="senha_segura_123", perfil=Role.VENDEDOR)
    with pytest.raises(UsuarioJaExisteError):
        create_user(session, username=f"dup_{u}", nome="B", email=f"b_{u}@teste.local",
                    password="senha_segura_123", perfil=Role.TECNICO)


def test_get_user_by_username_encontra_usuario_criado(session):
    u = _unique()
    create_user(session, username=f"busca_{u}", nome="Busca Teste", email=f"busca_{u}@teste.local",
                password="senha_segura_123", perfil=Role.GESTOR)
    found = get_user_by_username(session, f"busca_{u}")
    assert found is not None
    assert found.nome == "Busca Teste"


def test_get_user_by_username_inexistente_retorna_none(session):
    assert get_user_by_username(session, f"nao_existe_{_unique()}") is None


def test_list_users_inclui_usuario_recem_criado(session):
    u = _unique()
    create_user(session, username=f"lista_{u}", nome="Lista Teste", email=f"lista_{u}@teste.local",
                password="senha_segura_123", perfil=Role.QUIMICO_PD)
    usernames = [x.username for x in list_users(session)]
    assert f"lista_{u}" in usernames


def test_update_user_altera_nome_e_perfil(session):
    u = _unique()
    user = create_user(session, username=f"upd_{u}", nome="Nome Antigo", email=f"upd_{u}@teste.local",
                       password="senha_segura_123", perfil=Role.VENDEDOR)
    updated = update_user(session, user.id, nome="Nome Novo", perfil=Role.GESTOR)
    assert updated.nome == "Nome Novo"
    assert updated.perfil == Role.GESTOR


def test_update_user_inexistente_levanta_erro(session):
    with pytest.raises(UsuarioNaoEncontradoError):
        update_user(session, uuid.uuid4(), nome="X")


def test_set_password_troca_o_hash(session):
    u = _unique()
    user = create_user(session, username=f"pwd_{u}", nome="A", email=f"pwd_{u}@teste.local",
                       password="senha_original_123", perfil=Role.VENDEDOR)
    old_hash = user.password_hash
    set_password(session, user.id, "senha_nova_456")
    session.refresh(user)
    assert user.password_hash != old_hash
    assert verify_password("senha_nova_456", user.password_hash) is True
    assert verify_password("senha_original_123", user.password_hash) is False


def test_deactivate_user_muda_status_sem_apagar_linha(session):
    u = _unique()
    user = create_user(session, username=f"del_{u}", nome="A", email=f"del_{u}@teste.local",
                       password="senha_segura_123", perfil=Role.VENDEDOR)
    deactivate_user(session, user.id)
    still_there = get_user_by_id(session, user.id)
    assert still_there is not None
    assert still_there.status == UserStatus.INATIVO


def test_deactivate_user_inexistente_levanta_erro(session):
    with pytest.raises(UsuarioNaoEncontradoError):
        deactivate_user(session, uuid.uuid4())
