from pathlib import Path
import os

import pytest

os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from app import document_source_service as fontes


def test_monta_source_refs_deduplica_e_nao_expoe_filepath(tmp_path, monkeypatch):
    arquivo = tmp_path / "Boletim FLEXX AG 2032.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))

    refs = fontes.montar_source_refs([
        {"filename": "Boletim FLEXX AG 2032.pdf", "filepath": str(arquivo)},
        {"filename": "duplicado.pdf", "filepath": str(arquivo)},
    ])

    assert refs == [
        {
            "id": refs[0]["id"],
            "nome_arquivo": "Boletim FLEXX AG 2032.pdf",
            "download_url": f"/api/documentos/fontes/{refs[0]['id']}/download",
        }
    ]
    assert str(arquivo) not in str(refs)


def test_ignora_arquivo_fora_das_raizes_permitidas(tmp_path, monkeypatch):
    permitido = tmp_path / "permitido"
    proibido = tmp_path / "proibido"
    permitido.mkdir()
    proibido.mkdir()
    arquivo = proibido / "segredo.pdf"
    arquivo.write_bytes(b"segredo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(permitido),))

    assert fontes.montar_source_refs([
        {"filename": "segredo.pdf", "filepath": str(arquivo)}
    ]) == []


def test_resolve_source_id_para_arquivo_existente(tmp_path, monkeypatch):
    arquivo = tmp_path / "boletim.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    source_id = fontes.montar_source_refs([
        {"filename": "boletim.pdf", "filepath": str(arquivo)}
    ])[0]["id"]

    assert fontes.resolver_source_id(source_id) == Path(arquivo)


@pytest.mark.parametrize("source_id", ["", "abc", "src_xyz", "src_" + "0" * 31])
def test_rejeita_source_id_malformado(source_id):
    with pytest.raises(fontes.SourceIdInvalidoError):
        fontes.resolver_source_id(source_id)


def test_source_id_valido_mas_sem_arquivo_retorna_nao_encontrado(tmp_path, monkeypatch):
    arquivo = tmp_path / "boletim.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    source_id = fontes.montar_source_refs([
        {"filename": "boletim.pdf", "filepath": str(arquivo)}
    ])[0]["id"]
    arquivo.unlink()

    with pytest.raises(fontes.SourceNaoEncontradaError):
        fontes.resolver_source_id(source_id)
