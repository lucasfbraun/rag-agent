"""A criaÃ§Ã£o de correÃ§Ãµes deve ser barrada no endpoint, alÃ©m da interface."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.db import get_session
from app.main import app


def _usuario_com(*permissoes):
    usuario = MagicMock()
    usuario.perfil.nome = "Perfil de teste"
    usuario.perfil.nomes_de_permissoes.return_value = set(permissoes)
    return usuario


def _sessao_falsa():
    yield MagicMock()


@pytest.mark.parametrize("permissoes", [(), ("approve_training",)])
def test_post_de_correcao_exige_train_agent_mesmo_se_usuario_pode_aprovar(permissoes):
    app.dependency_overrides[get_current_user] = lambda: _usuario_com(*permissoes)
    app.dependency_overrides[get_session] = _sessao_falsa
    try:
        with patch("app.treinamento_router.criar") as criar:
            resposta = TestClient(app).post("/api/treinamento", json={
                "tipo": "correcao",
                "pergunta": "Qual produto atende esta aplicaÃ§Ã£o?",
                "resposta": "A resposta correta deve citar o boletim tÃ©cnico.",
            })
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 403
    criar.assert_not_called()


@pytest.mark.parametrize("permissoes", [(), ("approve_uploads",)])
def test_upload_pelo_chat_exige_upload_documents_mesmo_se_usuario_pode_aprovar(permissoes):
    app.dependency_overrides[get_current_user] = lambda: _usuario_com(*permissoes)
    app.dependency_overrides[get_session] = _sessao_falsa
    try:
        with patch("app.upload_router.registrar_envio") as registrar_envio:
            resposta = TestClient(app).post(
                "/api/documentos",
                files={"arquivo": ("boletim.pdf", b"conteudo", "application/pdf")},
                data={"observacao": "enviado pelo chat"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 403
    registrar_envio.assert_not_called()

