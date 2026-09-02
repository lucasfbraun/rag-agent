"""Histórico de conversas na interface Streamlit.

Seam aprovado: AppTest executa a tela real; somente a API HTTP, fronteira do
frontend, é simulada.
"""
import os
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest


APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)
CONVERSATION_ID = "11111111-1111-1111-1111-111111111111"


def _response(status_code, data=None):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = data
    return response


def _fake_get(url, **_kwargs):
    if url.endswith("/api/health"):
        return _response(
            200,
            {"qdrant": "online", "collection": {"points_count": 10}},
        )
    if url.endswith("/api/conversations"):
        return _response(
            200,
            [
                {
                    "id": CONVERSATION_ID,
                    "title": "Aplicacao para colchoes",
                    "created_at": "2026-09-02T12:00:00Z",
                    "updated_at": "2026-09-02T12:05:00Z",
                }
            ],
        )
    if url.endswith(f"/api/conversations/{CONVERSATION_ID}"):
        return _response(
            200,
            {
                "id": CONVERSATION_ID,
                "title": "Aplicacao para colchoes",
                "created_at": "2026-09-02T12:00:00Z",
                "updated_at": "2026-09-02T12:05:00Z",
                "messages": [
                    {
                        "id": "msg-1",
                        "role": "user",
                        "content": "Qual produto atende colchoes?",
                        "sources": [],
                        "model_used": None,
                        "created_at": "2026-09-02T12:00:00Z",
                    },
                    {
                        "id": "msg-2",
                        "role": "assistant",
                        "content": "Resposta salva",
                        "sources": ["Boletim X.pdf"],
                        "model_used": "gpt-4o-mini",
                        "created_at": "2026-09-02T12:01:00Z",
                    },
                ],
            },
        )
    raise AssertionError(f"GET inesperado: {url}")


def _authenticated_app():
    app = AppTest.from_file(APP_PATH)
    app.session_state.access_token = "token-de-teste"
    app.session_state.current_user = {
        "nome": "Vendedor Teste",
        "perfil": "vendedor",
    }
    return app


def test_sidebar_mostra_nova_conversa_e_conversas_salvas():
    app = _authenticated_app()

    with patch("requests.get", side_effect=_fake_get):
        app.run(timeout=10)

    assert not app.exception
    button_labels = [button.label for button in app.button]
    assert "Nova conversa" in button_labels
    assert "Aplicacao para colchoes" in button_labels


def test_selecionar_conversa_renderiza_mensagens_salvas():
    app = _authenticated_app()

    with patch("requests.get", side_effect=_fake_get):
        app.run(timeout=10)
        next(
            button
            for button in app.button
            if button.label == "Aplicacao para colchoes"
        ).click().run(timeout=10)

    assert not app.exception
    assert app.session_state.active_conversation_id == CONVERSATION_ID
    markdown_values = [element.value for element in app.markdown]
    assert "Qual produto atende colchoes?" in markdown_values
    assert "Resposta salva" in markdown_values


def test_excluir_conversa_ativa_limpa_a_tela():
    app = _authenticated_app()
    app.session_state.active_conversation_id = CONVERSATION_ID
    app.session_state.messages = [{"role": "user", "content": "mensagem antiga"}]

    with (
        patch("requests.get", side_effect=_fake_get),
        patch("requests.delete", return_value=_response(204)) as delete,
    ):
        app.run(timeout=10)
        next(
            button
            for button in app.button
            if button.key == f"delete_conversation_{CONVERSATION_ID}"
        ).click().run(timeout=10)

    delete.assert_called_once()
    assert app.session_state.active_conversation_id is None
    assert app.session_state.messages == []
