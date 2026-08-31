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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from streamlit.testing.v1 import AppTest


def _app():
    at = AppTest.from_file(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    )
    at.run()
    return at


def test_sem_token_mostra_formulario_de_login_e_nao_quebra():
    at = _app()

    assert not at.exception
    assert len(at.text_input) == 2  # usuário + senha
    assert any("Entrar" in b.label for b in at.button)


def test_sem_token_nao_chega_na_area_principal_do_chat():
    at = _app()

    # st.stop() logo após o formulário de login deve impedir que o resto do
    # script (sidebar com chamada HTTP de health-check, chat_input etc.)
    # rode — sinal indireto: nenhum chat_input foi montado.
    assert len(at.chat_input) == 0
