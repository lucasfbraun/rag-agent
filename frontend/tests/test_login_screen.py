"""
Regressão da tela de login (Sessão 27 — card de PWA + identidade visual).

Seam: streamlit.testing.v1.AppTest, que executa frontend/app.py de verdade
e inspeciona a árvore de elementos Streamlit-nativos renderizados. Não cobre
o componente HTML do card de PWA (iframe via components.v1.html) — a
própria AppTest não tem um acessor público pra esse tipo de nó, e ir buscar
por dentro da árvore interna seria testar implementação, não comportamento
(ver skill tdd, seção "Implementation-coupled"). O que este teste garante é
o que já existia antes desta sessão e não pode quebrar: sem token de sessão,
a tela mostra o formulário de login e nada além dele tenta rodar.
"""
import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest


def _app():
    at = AppTest.from_file(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    )
    at.run(timeout=15)
    return at


def test_sem_token_mostra_formulario_de_login_e_nao_quebra():
    at = _app()

    assert not at.exception
    assert len(at.text_input) == 2  # usuário + senha
    assert "Manter conectado" in [item.label for item in at.checkbox]
    assert any("Entrar" in b.label for b in at.button)


def test_sem_token_nao_chega_na_area_principal_do_chat():
    at = _app()

    # st.stop() logo após o formulário de login deve impedir que o resto do
    # script (sidebar com chamada HTTP de health-check, chat_input etc.)
    # rode — sinal indireto: nenhum chat_input foi montado.
    assert len(at.chat_input) == 0


def test_login_marcado_envia_manter_conectado_ao_backend():
    at = _app()
    next(item for item in at.text_input if item.label == "Usuário").input("ana")
    next(item for item in at.text_input if item.label == "Senha").input("segredo")
    next(item for item in at.checkbox if item.label == "Manter conectado").check()

    login_response = Mock(status_code=200)
    login_response.json.return_value = {
        "access_token": "jwt-teste",
        "token_type": "bearer",
        "expires_in_seconds": 3600,
    }
    me_response = Mock(status_code=200)
    me_response.json.return_value = {
        "id": "11111111-1111-1111-1111-111111111111",
        "nome": "Ana",
        "perfil": "vendedor",
        "permissoes": ["view_catalog"],
    }

    with patch("requests.post", return_value=login_response) as post, patch(
        "requests.get", return_value=me_response
    ):
        next(button for button in at.button if button.label == "Entrar").click().run(timeout=15)

    assert post.call_args.kwargs["json"]["manter_conectado"] is True
    assert at.session_state.access_token == "jwt-teste"
