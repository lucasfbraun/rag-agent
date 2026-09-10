"""Tela de administração de usuários e perfis.

O backend expunha `/api/auth/users` desde a Fase 5 (tarefa 7), mas sem tela:
provisionar alguém exigia CLI ou `curl`, e o sistema rodou até aqui com um
único usuário cadastrado. Esta é a interface daquelas rotas.

O que estes testes protegem: quem enxerga a administração, que o cadastro
manda o perfil escolhido, que "excluir = desativar" tem volta, e que a tela
mostra o motivo real quando o backend recusa — a alternativa é um "erro 409"
seco na cara de quem só quer cadastrar um vendedor.

Seam aprovado (mesmo de test_conversation_history_ui.py): AppTest executa a
tela real; somente a API HTTP, fronteira do frontend, é simulada.
"""
import os
from unittest.mock import Mock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def _limpar_cache_do_streamlit():
    """`_ldap_disponivel` e `_buscar_modelos_disponiveis` usam `st.cache_data`,
    que é GLOBAL do processo — sem limpar, a resposta de um teste vaza para o
    seguinte e o teste de "AD não configurado" enxerga o True do teste
    anterior."""
    st.cache_data.clear()
    yield
    st.cache_data.clear()

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)

ID_ADMIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ID_VENDEDOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ID_INATIVO = "cccccccc-cccc-cccc-cccc-cccccccccccc"

USUARIOS = [
    {"id": ID_ADMIN, "username": "lucas.braun", "nome": "Lucas Braun",
     "email": "lucas@grupoflexivel.com.br", "perfil": "admin_ti", "status": "ativo", "origem": "manual", "external_id": None},
    {"id": ID_VENDEDOR, "username": "ana.silva", "nome": "Ana Silva",
     "email": "ana@grupoflexivel.com.br", "perfil": "vendedor", "status": "ativo", "origem": "ldap",
     "external_id": "68a37cfc-caa1-46e7-a70d-a9022c27fb65"},
    {"id": ID_INATIVO, "username": "jose.antigo", "nome": "Jose Antigo",
     "email": "jose@grupoflexivel.com.br", "perfil": "tecnico", "status": "inativo", "origem": "manual", "external_id": None},
]


def _response(status_code, data=None):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = data
    response.content = b"{}" if data is not None else b""
    return response


def _fake_get(url, **_kwargs):
    if url.endswith("/api/health"):
        return _response(200, {"qdrant": "online", "collection": {"points_count": 10}})
    if url.endswith("/ldap/status"):
        return _response(200, {"configurado": True})
    if url.endswith("/api/models"):
        return _response(200, {"models": ["gpt-4o-mini", "gpt-4o"], "default": "gpt-4o-mini"})
    if url.endswith("/api/conversations"):
        return _response(200, [])
    if url.endswith("/api/auth/users"):
        return _response(200, USUARIOS)
    raise AssertionError(f"GET inesperado: {url}")


def _fake_request(metodo, url, **kwargs):
    """`_api_usuarios` usa requests.request; o GET da listagem passa por aqui."""
    if metodo == "GET":
        return _fake_get(url, **kwargs)
    return _response(200, USUARIOS[1])


def _app(perfil="admin_ti", user_id=ID_ADMIN, pagina="usuarios"):
    app = AppTest.from_file(APP_PATH)
    app.session_state.access_token = "token-de-teste"
    app.session_state.current_user = {
        "id": user_id, "nome": "Lucas Braun", "perfil": perfil,
    }
    app.session_state.pagina = pagina
    return app


# --- quem enxerga a administração ------------------------------------------

def test_admin_ti_ve_o_atalho_de_usuarios_na_sidebar():
    app = _app(pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert "Usuários e perfis" in [b.label for b in app.button]


def test_vendedor_nao_ve_o_atalho():
    """Conveniência de interface, não segurança — a decisão mora em
    Permission.MANAGE_USERS no backend. Mas oferecer um botão que só pode
    terminar em 403 é uma tela mentindo para o usuário."""
    app = _app(perfil="vendedor", user_id=ID_VENDEDOR, pagina="chat")
    with patch("requests.get", side_effect=_fake_get):
        app.run(timeout=15)

    assert not app.exception
    assert "Usuários e perfis" not in [b.label for b in app.button]


def test_vendedor_que_forca_a_pagina_cai_de_volta_no_chat():
    """Sem esta guarda, um `pagina` deixado em sessão (troca de usuário no
    mesmo navegador) renderizaria a tela de administração — que ia falhar com
    403 a cada chamada, mas depois de já ter sido desenhada."""
    app = _app(perfil="vendedor", user_id=ID_VENDEDOR, pagina="usuarios")
    with patch("requests.get", side_effect=_fake_get):
        app.run(timeout=15)

    assert not app.exception
    assert app.session_state.pagina == "chat"


# --- listagem ---------------------------------------------------------------

def test_lista_mostra_ativos_e_inativos_com_o_estado_visivel():
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    textos = " ".join(e.value for e in app.markdown) + " ".join(e.value for e in app.caption)
    assert "Ana Silva" in textos
    assert "Jose Antigo" in textos
    assert "desativado" in textos


def test_o_proprio_admin_nao_recebe_botao_de_desativar():
    """O backend recusa a autodesativação; oferecer o botão só produziria um
    erro garantido."""
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    chaves = [b.key for b in app.button]
    assert f"off_{ID_ADMIN}" not in chaves
    assert f"off_{ID_VENDEDOR}" in chaves


def test_usuario_desativado_ganha_botao_de_reativar():
    """Contrapartida de "excluir = desativar": sem volta, um clique errado
    trancaria a conta para sempre."""
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    chaves = [b.key for b in app.button]
    assert f"on_{ID_INATIVO}" in chaves
    assert f"off_{ID_INATIVO}" not in chaves


# --- ações ------------------------------------------------------------------

def test_desativar_chama_a_rota_de_desativacao():
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request) as request:
        app.run(timeout=15)
        next(b for b in app.button if b.key == f"off_{ID_VENDEDOR}").click().run(timeout=15)

    chamadas = [c.args for c in request.call_args_list]
    assert any(
        metodo == "POST" and url.endswith(f"/{ID_VENDEDOR}/deactivate")
        for metodo, url in chamadas
    )


def test_reativar_chama_a_rota_de_reativacao():
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request) as request:
        app.run(timeout=15)
        next(b for b in app.button if b.key == f"on_{ID_INATIVO}").click().run(timeout=15)

    chamadas = [c.args for c in request.call_args_list]
    assert any(
        metodo == "POST" and url.endswith(f"/{ID_INATIVO}/activate")
        for metodo, url in chamadas
    )


def test_cadastro_envia_perfil_escolhido_e_nao_a_senha_no_lugar_errado():
    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request) as request:
        app.run(timeout=15)
        campos = {t.label: t for t in app.text_input}
        campos["Usuário (login)"].set_value("ana.silva")
        campos["Nome completo"].set_value("Ana Silva")
        campos["E-mail"].set_value("ana@grupoflexivel.com.br")
        campos["Senha inicial"].set_value("SenhaForte123")
        next(b for b in app.button if "Cadastrar usuário" in b.label).click().run(timeout=15)

    envio = next(
        c for c in request.call_args_list
        if c.args[0] == "POST" and c.args[1].endswith("/api/auth/users")
    )
    corpo = envio.kwargs["json"]
    assert corpo["username"] == "ana.silva"
    assert corpo["password"] == "SenhaForte123"
    assert corpo["perfil"] in {"vendedor", "tecnico", "gestor", "quimico_pd", "admin_ti"}


def test_erro_do_backend_aparece_com_o_motivo_real():
    """"Erro 409" não diz nada a quem está cadastrando. O motivo vem em
    `detail` — "email já usado", "senha fraca", "último Admin TI ativo"."""
    def _request_com_conflito(metodo, url, **kwargs):
        if metodo == "POST" and url.endswith(f"/{ID_VENDEDOR}/deactivate"):
            return _response(409, {"detail": "não é possível desativar o último Admin TI ativo."})
        return _fake_request(metodo, url, **kwargs)

    app = _app()
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_request_com_conflito):
        app.run(timeout=15)
        next(b for b in app.button if b.key == f"off_{ID_VENDEDOR}").click().run(timeout=15)

    assert any("último Admin TI ativo" in e.value for e in app.error)


# --- vínculo com o Active Directory ----------------------------------------

def test_usuario_vinculado_ao_ad_aparece_marcado_na_lista():
    """Sem o selo, um administrador não tem como saber por qual credencial
    cada pessoa entra — e tentaria redefinir uma senha que não existe."""
    app = _app()
    with patch("requests.get", side_effect=_fake_get),          patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    legendas = " ".join(e.value for e in app.caption)
    assert "AD" in legendas


def test_redefinir_senha_nao_aparece_para_usuario_do_ad():
    """A senha local de um usuário LDAP é NULL; definir uma não teria efeito
    nenhum no login. Oferecer o campo só confundiria."""
    app = _app()
    with patch("requests.get", side_effect=_fake_get),          patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    chaves = [t.key for t in app.text_input]
    assert f"s_{ID_VENDEDOR}" not in chaves, "campo de senha apareceu para usuário do AD"
    assert f"s_{ID_ADMIN}" in chaves, "campo de senha sumiu para usuário local"


def test_usuario_do_ad_ganha_opcao_de_desvincular_com_senha():
    """Desvincular sem definir senha deixaria a pessoa sem forma de entrar."""
    app = _app()
    with patch("requests.get", side_effect=_fake_get),          patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert f"du_{ID_VENDEDOR}" in [t.key for t in app.text_input]


def test_sem_ad_configurado_a_secao_nao_aparece():
    """Instalação sem Active Directory não pode oferecer o recurso — falharia
    só no clique."""
    def _get_sem_ldap(url, **kw):
        if url.endswith("/ldap/status"):
            return _response(200, {"configurado": False})
        return _fake_get(url, **kw)

    app = _app()
    with patch("requests.get", side_effect=_get_sem_ldap),          patch("requests.request", side_effect=lambda m, u, **k: _get_sem_ldap(u, **k) if m == "GET" else _response(200, USUARIOS[1])):
        app.run(timeout=15)

    assert not any(t.key == f"ad_busca_{ID_ADMIN}" for t in app.text_input)
