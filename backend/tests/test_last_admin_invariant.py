"""
Ticket 3 do plano de correção (AUD-004 + achado novo da verificação de
2026-08-26): nada impedia zerar os Admin TI ativos — nem rebaixando o
próprio perfil, nem rebaixando/desativando o único outro Admin TI. A guarda
de admin_router.py só cobria autodesativação, e só por ID, não por invariante
de negócio. A correção fica em user_service.py (update_user/deactivate_user)
porque precisa valer pra qualquer chamador, não só pro autoatendimento.

Seams testados: user_service.py direto (regra de negócio) e a camada HTTP
via admin_router (tradução pra 409).

Nota de bastidor (lição real desta sessão, não é só formalidade): a primeira
versão deste arquivo criava só 1 admin de teste e assumia que ele seria "o
único admin do sistema" — falso neste banco de dev, que tem um Admin TI real
e permanente (`lucas.braun`, criado fora dos testes). Como sempre existia
"outro admin" (o lucas.braun), a invariante nunca bloqueava de verdade, o
`pytest.raises` falhava sem levantar exceção, o `session.rollback()` do corpo
do teste nunca era alcançado, e a fixture de limpeza (conexão separada)
ficava esperando um lock que só seria liberado pelo rollback da fixture
`session` — dependência circular entre as duas fixtures, travando o
`pytest` inteiro sem nenhuma saída no terminal. Cenários de "bloqueado" agora
mockam a contagem de admins (`_contar_admins_ativos_exceto`) em vez de
depender do estado global da tabela — mais determinístico e não tem como
recriar esse travamento.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User, UserStatus
from app.auth.user_service import (
    UltimoAdminError,
    create_user,
    deactivate_user,
    update_user,
)
from app.main import app


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def created_user_ids():
    ids = []
    yield ids
    if ids:
        cleanup = SessionLocal()
        try:
            for user_id in ids:
                user = cleanup.get(User, user_id)
                if user is not None:
                    cleanup.delete(user)
            cleanup.commit()
        finally:
            cleanup.close()


def _make_admin(session, created_user_ids, **overrides):
    unique = uuid.uuid4().hex[:8]
    defaults = dict(
        username=f"admin_{unique}", nome="Admin Teste",
        email=f"admin_{unique}@teste.local", password="senha_segura_123",
        perfil=Role.ADMIN_TI,
    )
    defaults.update(overrides)
    user = create_user(session, **defaults)
    session.commit()
    created_user_ids.append(user.id)
    return user


# --- regra de negócio (user_service.py) -----------------------------------

def test_rebaixar_o_unico_admin_ativo_e_bloqueado(session, created_user_ids):
    admin = _make_admin(session, created_user_ids)

    # Mocka a contagem em vez de depender de o banco não ter outro admin (ver
    # nota no topo do arquivo) — testa a lógica de update_user isoladamente.
    with patch("app.auth.user_service._contar_admins_ativos_exceto", return_value=0):
        with pytest.raises(UltimoAdminError):
            update_user(session, admin.id, perfil=Role.VENDEDOR)

    session.rollback()
    ainda_admin = session.get(User, admin.id)
    assert ainda_admin.perfil == Role.ADMIN_TI


def test_desativar_o_unico_admin_ativo_e_bloqueado(session, created_user_ids):
    admin = _make_admin(session, created_user_ids)

    with patch("app.auth.user_service._contar_admins_ativos_exceto", return_value=0):
        with pytest.raises(UltimoAdminError):
            deactivate_user(session, admin.id)

    session.rollback()
    ainda_ativo = session.get(User, admin.id)
    assert ainda_ativo.status == UserStatus.ATIVO


def test_rebaixar_um_admin_quando_existe_outro_admin_ativo_e_permitido(session, created_user_ids):
    """Sem mock aqui de propósito: com admin_a + admin_b criados por este
    teste, sempre existe pelo menos 1 "outro" admin de verdade — não depende
    de quantos outros Admin TI já existem no banco (ex: lucas.braun)."""
    admin_a = _make_admin(session, created_user_ids)
    _make_admin(session, created_user_ids)  # admin_b — mantém pelo menos 1 ativo

    update_user(session, admin_a.id, perfil=Role.VENDEDOR)
    session.commit()

    rebaixado = session.get(User, admin_a.id)
    assert rebaixado.perfil == Role.VENDEDOR


def test_rebaixar_admin_ja_inativo_nao_e_bloqueado_pela_invariante(session, created_user_ids):
    """Admin já desativado não conta como "admin ativo perdido" — a invariante
    é sobre não ZERAR admins ATIVOS, não sobre proteger cada linha da tabela.
    Mock necessário aqui: sem ele, lucas.braun continuaria "outro admin ativo"
    e o teste não exerceria o bloqueio de verdade (ver nota no topo)."""
    admin_a = _make_admin(session, created_user_ids)
    admin_b = _make_admin(session, created_user_ids)
    deactivate_user(session, admin_b.id)
    session.commit()

    with patch("app.auth.user_service._contar_admins_ativos_exceto", return_value=0):
        with pytest.raises(UltimoAdminError):
            update_user(session, admin_a.id, perfil=Role.VENDEDOR)


def test_editar_nome_de_admin_sem_mexer_no_perfil_nao_aciona_a_invariante(session, created_user_ids):
    admin = _make_admin(session, created_user_ids)

    update_user(session, admin.id, nome="Novo Nome")
    session.commit()

    atualizado = session.get(User, admin.id)
    assert atualizado.nome == "Novo Nome"
    assert atualizado.perfil == Role.ADMIN_TI


# --- camada HTTP (admin_router.py) -----------------------------------------
# Mocka _contar_admins_ativos_exceto pelo mesmo motivo dos testes acima —
# não dá pra simular "zero outros admins" de verdade neste banco compartilhado
# sem mexer no admin real (lucas.braun). O que importa testar aqui é só a
# tradução do erro de domínio pra HTTP 409, não a contagem em si (já coberta
# pelos testes de service acima com dados reais).

@pytest.fixture
def client():
    return TestClient(app)


def _token_for(perfil: Role, created_user_ids) -> str:
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"http_{u}", nome="Usuário HTTP",
            email=f"http_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    login_client = TestClient(app)
    resp = login_client.post("/api/auth/login", json={"username": f"http_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def test_patch_rebaixando_o_ultimo_admin_via_api_retorna_409(client, created_user_ids):
    token = _token_for(Role.ADMIN_TI, created_user_ids)
    session = SessionLocal()
    try:
        me = session.execute(
            __import__("sqlalchemy").select(User).where(User.username.like("http_%"))
        ).scalars().first()
        user_id = str(me.id)
    finally:
        session.close()

    with patch("app.auth.user_service._contar_admins_ativos_exceto", return_value=0):
        resp = client.patch(
            f"/api/auth/users/{user_id}",
            json={"perfil": "vendedor"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


def test_desativar_outro_admin_quando_o_chamador_continua_ativo_e_permitido(client, created_user_ids):
    """Guarda-corpo pro caminho contrário: com 2 Admin TI ativos (o alvo e o
    chamador), desativar o alvo sempre deixa pelo menos o chamador — a
    invariante não deve bloquear essa operação legítima. Sem mock: o cenário
    é real (2 admins de teste), não depende de contagem global."""
    session = SessionLocal()
    try:
        alvo = _make_admin(session, created_user_ids)
        target_id = str(alvo.id)
    finally:
        session.close()

    token = _token_for(Role.ADMIN_TI, created_user_ids)

    resp = client.post(
        f"/api/auth/users/{target_id}/deactivate",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
