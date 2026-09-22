import os

from fastapi.testclient import TestClient

os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import document_source_service as fontes
from app.auth.dependencies import get_current_user
from app.main import app


class _PerfilFake:
    nome = "Vendedor"

    def nomes_de_permissoes(self):
        return {"view_catalog"}


class _UsuarioFake:
    perfil = _PerfilFake()


def _client_autenticado():
    app.dependency_overrides[get_current_user] = lambda: _UsuarioFake()
    return TestClient(app)


def _limpar_overrides():
    app.dependency_overrides.clear()


def test_download_de_fonte_autenticada(tmp_path, monkeypatch):
    client = _client_autenticado()
    try:
        arquivo = tmp_path / "Boletim FLEXX AG 2032.pdf"
        arquivo.write_bytes(b"conteudo do boletim")
        monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
        source_id = fontes.montar_source_refs([
            {"filename": arquivo.name, "filepath": str(arquivo)}
        ])[0]["id"]

        response = client.get(
            f"/api/documentos/fontes/{source_id}/download"
        )

        assert response.status_code == 200
        assert response.content == b"conteudo do boletim"
        assert "Boletim%20FLEXX%20AG%202032.pdf" in response.headers["content-disposition"]
    finally:
        _limpar_overrides()


def test_download_exige_autenticacao(tmp_path, monkeypatch):
    client = TestClient(app)
    arquivo = tmp_path / "boletim.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    source_id = fontes.montar_source_refs([
        {"filename": arquivo.name, "filepath": str(arquivo)}
    ])[0]["id"]

    response = client.get(f"/api/documentos/fontes/{source_id}/download")

    assert response.status_code == 401


def test_download_rejeita_source_id_malformado():
    client = _client_autenticado()
    try:
        response = client.get("/api/documentos/fontes/invalido/download")

        assert response.status_code == 400
    finally:
        _limpar_overrides()


def test_download_retorna_404_quando_arquivo_sumiu(tmp_path, monkeypatch):
    client = _client_autenticado()
    try:
        arquivo = tmp_path / "boletim.pdf"
        arquivo.write_bytes(b"conteudo")
        monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
        source_id = fontes.montar_source_refs([
            {"filename": arquivo.name, "filepath": str(arquivo)}
        ])[0]["id"]
        arquivo.unlink()

        response = client.get(
            f"/api/documentos/fontes/{source_id}/download"
        )

        assert response.status_code == 404
    finally:
        _limpar_overrides()
