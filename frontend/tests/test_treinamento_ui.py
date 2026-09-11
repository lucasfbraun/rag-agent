"""Tela de treinamento e correção originada no feedback negativo."""
import os
from unittest.mock import Mock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
ITEM_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
ITEM = {
    "id": ITEM_ID, "tipo": "correcao", "pergunta": "Qual cola usar para cortiça?",
    "resposta": "Usar FLEXX AG 2066 conforme o boletim.",
    "resposta_original": "Use outro produto.", "produto": "FLEXX AG 2066",
    "aplicacao": "rolha de cortiça", "fonte": "Boletim AG 2066 rev. 03",
    "status": "pendente", "motivo_decisao": None, "criado_por": "Ana",
    "decidido_por": None, "created_at": "2026-09-10T10:00:00",
}


@pytest.fixture(autouse=True)
def _clear_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _response(status, data=None):
    result = Mock()
    result.status_code = status
    result.json.return_value = data
    result.content = b"{}" if data is not None else b""
    return result


def _fake_get(url, **_kwargs):
    if url.endswith("/api/health"):
        return _response(200, {"qdrant": "online", "collection": {"points_count": 10}})
    if url.endswith("/api/models"):
        return _response(200, {"models": ["gpt-4o-mini"], "default": "gpt-4o-mini"})
    if url.endswith("/api/conversations"):
        return _response(200, [])
    raise AssertionError(f"GET inesperado: {url}")


def _fake_request(method, url, **_kwargs):
    if method == "GET" and url.endswith("/api/treinamento"):
        return _response(200, [ITEM])
    return _response(200, ITEM)


def _app(permissoes, pagina="treinamento"):
    app = AppTest.from_file(APP_PATH)
    app.session_state.access_token = "token"
    app.session_state.current_user = {
        "id": "11111111-1111-1111-1111-111111111111", "nome": "Teste",
        "perfil": "qualquer", "permissoes": permissoes,
    }
    app.session_state.pagina = pagina
    return app


def _run(app, request=_fake_request, post=None):
    post = post or (lambda *_args, **_kwargs: _response(200, {"status": "ok"}))
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=request), \
         patch("requests.post", side_effect=post):
        app.run(timeout=15)
    return app


def test_permissao_de_treinar_exibe_atalho_e_pagina():
    app = _run(_app(["view_catalog", "train_agent"], pagina="chat"))
    assert "Treinar agente" in [button.label for button in app.button]

    pagina = _run(_app(["view_catalog", "train_agent"]))
    assert not pagina.exception
    assert "Treinar o agente" in " ".join(item.value for item in pagina.markdown)


def test_aprovador_ve_acao_e_demais_usuarios_nao():
    treinador = _run(_app(["view_catalog", "train_agent"]))
    aprovador = _run(_app(["view_catalog", "train_agent", "approve_training"]))
    assert f"treino_ap_{ITEM_ID}" not in [button.key for button in treinador.button]
    assert f"treino_ap_{ITEM_ID}" in [button.key for button in aprovador.button]


def test_aprovador_sem_permissao_de_criar_consegue_abrir_a_fila():
    pagina = _run(_app(["view_catalog", "approve_training"]))
    assert not pagina.exception
    assert f"treino_ap_{ITEM_ID}" in [button.key for button in pagina.button]
    assert "Registrar ensinamento" not in [button.label for button in pagina.button]

    chat = _run(_app(["view_catalog", "approve_training"], pagina="chat"))
    assert "Treinar agente" in [button.label for button in chat.button]


def test_feedback_negativo_abre_formulario_e_envia_correcao_com_escopo():
    app = _app(["view_catalog", "train_agent"], pagina="chat")
    app.session_state.messages = [
        {"role": "user", "content": "Qual cola usar para cortiça?"},
        {"role": "assistant", "content": "Use outro produto.", "sources": [], "model_used": "gpt-4o-mini"},
    ]

    enviados = []
    def request(method, url, **kwargs):
        enviados.append((method, url, kwargs))
        return _response(201, ITEM)

    def post(url, **_kwargs):
        assert url.endswith("/api/feedback")
        return _response(200, {"status": "ok"})

    _run(app, request=request, post=post)
    app.feedback[0].set_value(0)
    _run(app, request=request, post=post)
    assert any(area.label == "Qual seria a resposta correta?" for area in app.text_area)

    next(area for area in app.text_area if area.label == "Qual seria a resposta correta?").set_value(
        "Use FLEXX AG 2066 conforme o boletim."
    )
    next(field for field in app.text_input if field.label.startswith("Produto ou código")).set_value(
        "FLEXX AG 2066"
    )
    next(field for field in app.text_input if field.label.startswith("Aplicação ou condição")).set_value(
        "rolha de cortiça"
    )
    next(field for field in app.text_input if field.label.startswith("Fonte da correção")).set_value(
        "Boletim AG 2066 rev. 03"
    )
    next(button for button in app.button if button.label == "Enviar correção para aprovação").click()
    _run(app, request=request, post=post)

    payload = next(kwargs["json"] for method, url, kwargs in enviados if method == "POST")
    assert payload["tipo"] == "correcao"
    assert payload["produto"] == "FLEXX AG 2066"
    assert payload["aplicacao"] == "rolha de cortiça"
    assert payload["fonte"] == "Boletim AG 2066 rev. 03"
    assert payload["resposta_original"] == "Use outro produto."


@pytest.mark.parametrize(
    "permissoes",
    [
        ["view_catalog"],
        ["view_catalog", "approve_training"],
    ],
)
def test_feedback_negativo_sem_permissao_de_treinar_nao_exibe_correcao(permissoes):
    """Aprovar itens não concede implicitamente o direito de criar correções."""
    app = _app(permissoes, pagina="chat")
    app.session_state.messages = [
        {"role": "user", "content": "Qual cola usar para cortiça?"},
        {"role": "assistant", "content": "Use outro produto.", "sources": []},
    ]

    _run(app)
    app.feedback[0].set_value(0)
    _run(app)

    assert not any(
        area.label == "Qual seria a resposta correta?" for area in app.text_area
    )
    assert "Enviar correção para aprovação" not in [button.label for button in app.button]
