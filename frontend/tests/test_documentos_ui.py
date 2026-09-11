"""
Tela de documentos — envio e fila de aprovação (Sessão 38).

O que estes testes protegem:

  1. **Quem vê o quê.** O atalho e a tela aparecem por PERMISSÃO, não pelo nome
     do perfil. Essa distinção deixou de ser detalhe na Sessão 37: com perfis
     criáveis pela tela, amarrar a interface ao slug "admin_ti" esconderia a
     funcionalidade de um perfil novo que a tem de direito.
  2. **Quem só envia não recebe botão de aprovar** — oferecer um botão que
     termina em 403 é a tela mentindo para o usuário.
  3. **A ordem das abas segue o que a pessoa veio fazer:** quem aprova vê a
     fila primeiro; quem só envia vê o formulário primeiro.

Seam: AppTest executa a tela real; só a API HTTP é simulada.
"""
import os
from unittest.mock import Mock, patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

APP_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"
)

ID_PENDENTE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
ID_APROVADO = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

DOCUMENTOS = [
    {"id": ID_PENDENTE, "nome_arquivo": "Boletim FLEXX AG 2032 rev03.pdf",
     "tamanho_bytes": 240000, "status": "pendente",
     "observacao": "substitui a rev 02 do acervo", "motivo_decisao": None,
     "enviado_por": "Ana Silva", "decidido_por": None, "chunks_indexados": 0,
     "created_at": "2026-09-10T10:00:00", "decidido_em": None},
    {"id": ID_APROVADO, "nome_arquivo": "FISPQ CAT 136.pdf",
     "tamanho_bytes": 120000, "status": "aprovado", "observacao": None,
     "motivo_decisao": None, "enviado_por": "Ana Silva",
     "decidido_por": "Lucas Braun", "chunks_indexados": 4,
     "created_at": "2026-09-09T10:00:00", "decidido_em": "2026-09-09T11:00:00"},
]


@pytest.fixture(autouse=True)
def _limpar_cache():
    st.cache_data.clear()
    yield
    st.cache_data.clear()


def _response(status_code, data=None):
    resposta = Mock()
    resposta.status_code = status_code
    resposta.json.return_value = data
    resposta.content = b"{}" if data is not None else b""
    return resposta


def _fake_get(url, **_kwargs):
    if url.endswith("/api/health"):
        return _response(200, {"qdrant": "online", "collection": {"points_count": 10}})
    if url.endswith("/api/models"):
        return _response(200, {"models": ["gpt-4o-mini"], "default": "gpt-4o-mini"})
    if url.endswith("/api/conversations"):
        return _response(200, [])
    if url.endswith("/api/documentos"):
        return _response(200, DOCUMENTOS)
    raise AssertionError(f"GET inesperado: {url}")


def _fake_request(metodo, url, **kwargs):
    if metodo == "GET":
        return _fake_get(url, **kwargs)
    return _response(200, DOCUMENTOS[0])


def _app(permissoes, pagina="documentos"):
    app = AppTest.from_file(APP_PATH)
    app.session_state.access_token = "token-de-teste"
    app.session_state.current_user = {
        "id": "11111111-1111-1111-1111-111111111111", "nome": "Teste",
        "perfil": "qualquer", "permissoes": permissoes,
    }
    app.session_state.pagina = pagina
    return app


SO_ENVIA = ["view_catalog", "upload_documents"]
APROVA = ["view_catalog", "upload_documents", "approve_uploads"]
SEM_NADA = ["view_catalog"]


# --- quem enxerga -----------------------------------------------------------

def test_quem_pode_enviar_ve_o_atalho_na_sidebar():
    app = _app(SO_ENVIA, pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert "Documentos" in [b.label for b in app.button]


def test_quem_nao_pode_enviar_nao_ve_o_atalho():
    app = _app(SEM_NADA, pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert "Documentos" not in [b.label for b in app.button]


def test_chat_aceita_anexos_para_quem_pode_enviar_documentos():
    app = _app(SO_ENVIA, pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert app.chat_input[0].proto.accept_file == 2  # MULTIPLE
    assert set(app.chat_input[0].proto.file_type) == {".pdf", ".docx", ".doc", ".txt"}


def test_chat_nao_aceita_anexos_para_quem_nao_tem_permissao():
    app = _app(SEM_NADA, pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert app.chat_input[0].proto.accept_file == 0  # NONE


def test_versao_minima_do_streamlit_suporta_anexo_no_chat():
    requirements = os.path.join(os.path.dirname(os.path.dirname(APP_PATH)), "requirements.txt")
    with open(requirements, encoding="utf-8") as arquivo:
        assert "streamlit>=1.43.0" in arquivo.read()


def test_o_atalho_aparece_pela_permissao_e_nao_pelo_nome_do_perfil():
    """O perfil aqui se chama "qualquer" — um nome que não existe no catálogo
    original. Se a tela ainda decidisse pelo slug, este botão sumiria, e um
    perfil criado pelo Admin TI ficaria sem acesso ao que ele concedeu."""
    app = _app(APROVA, pagina="chat")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    rotulos = [b.label for b in app.button]
    assert "Documentos" in rotulos
    assert "Usuários e perfis" not in rotulos  # não tem manage_users


def test_quem_forca_a_pagina_sem_permissao_cai_no_chat():
    app = _app(SEM_NADA, pagina="documentos")
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    assert app.session_state.pagina == "chat"


# --- o que cada um pode fazer ----------------------------------------------

def test_quem_so_envia_nao_recebe_botao_de_aprovar():
    """Oferecer um botão que termina em 403 é a tela mentindo."""
    app = _app(SO_ENVIA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    chaves = [b.key for b in app.button]
    assert f"ap_{ID_PENDENTE}" not in chaves


def test_quem_aprova_recebe_o_botao_no_documento_pendente():
    app = _app(APROVA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert f"ap_{ID_PENDENTE}" in [b.key for b in app.button]


def test_documento_ja_aprovado_nao_oferece_aprovar_de_novo():
    """Aprovar duas vezes reindexaria o mesmo arquivo — o backend recusa, e a
    tela não chega a oferecer."""
    app = _app(APROVA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert f"ap_{ID_APROVADO}" not in [b.key for b in app.button]


def test_aprovar_chama_a_rota_certa():
    app = _app(APROVA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request) as request:
        app.run(timeout=15)
        next(b for b in app.button if b.key == f"ap_{ID_PENDENTE}").click().run(timeout=15)

    chamadas = [c.args for c in request.call_args_list]
    assert any(
        metodo == "POST" and url.endswith(f"/{ID_PENDENTE}/aprovar")
        for metodo, url in chamadas
    )


# --- apresentação -----------------------------------------------------------

def test_a_fila_mostra_a_observacao_de_quem_enviou():
    """Sem o contexto, o aprovador recebe um PDF sem saber por que ele deveria
    entrar."""
    app = _app(APROVA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    textos = " ".join(e.value for e in app.markdown)
    assert "substitui a rev 02" in textos


def test_documento_aprovado_mostra_quantos_trechos_entraram():
    """É o número que permite conferir se a remoção depois apagou tudo."""
    app = _app(APROVA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    legendas = " ".join(e.value for e in app.caption)
    assert "4 trechos" in legendas


def test_o_uploader_so_aceita_formatos_com_texto_extraivel():
    """Imagem fica de fora: sem OCR, o arquivo seria aprovado e não
    acrescentaria nada às respostas."""
    app = _app(SO_ENVIA)
    with patch("requests.get", side_effect=_fake_get), \
         patch("requests.request", side_effect=_fake_request):
        app.run(timeout=15)

    assert not app.exception
    # O `type=` do file_uploader não é exposto pelo AppTest; o que dá para
    # afirmar é que o aviso sobre OCR chegou à tela.
    ajudas = " ".join(e.value for e in app.caption) + " ".join(e.value for e in app.markdown)
    assert "OCR" in ajudas or "digitalizados" in ajudas
