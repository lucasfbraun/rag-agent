import json
from unittest.mock import MagicMock, patch

from app import document_source_service as fontes
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent


def _final_completion(answer_text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.content = answer_text
    return resp


def test_run_pu_matcher_agent_devolve_source_refs_dos_docs_recuperados(
    tmp_path, monkeypatch
):
    arquivo = tmp_path / "Boletim FLEXX AG 2032.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{
        "filename": arquivo.name,
        "filepath": str(arquivo),
        "chunk_index": 0,
        "content": "dados do AG 2032",
    }]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs), \
         patch("app.rag.engine.litellm.completion", return_value=_final_completion("ok")):
        resposta = run_pu_matcher_agent(query="traga informacoes sobre AG 2032")

    assert resposta["sources"] == [arquivo.name]
    assert resposta["source_refs"] == [
        {
            "id": resposta["source_refs"][0]["id"],
            "nome_arquivo": arquivo.name,
            "download_url": f"/api/documentos/fontes/{resposta['source_refs'][0]['id']}/download",
        }
    ]


def test_run_pu_matcher_agent_resolve_source_refs_por_nome_quando_path_e_antigo(
    tmp_path, monkeypatch
):
    arquivo = tmp_path / "Boletim FLEXX AG 2032 ESP.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{
        "filename": arquivo.name,
        "filepath": "/mnt/acervo/antigo/" + arquivo.name,
        "chunk_index": 0,
        "content": "dados do AG 2032",
    }]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs), \
         patch("app.rag.engine.litellm.completion", return_value=_final_completion("ok")):
        resposta = run_pu_matcher_agent(query="me fale sobre AG 2032")

    assert resposta["sources"] == [arquivo.name]
    assert resposta["source_refs"][0]["nome_arquivo"] == arquivo.name
    assert resposta["source_refs"][0]["download_url"].endswith("/download")


def test_run_pu_matcher_agent_traz_arquivo_direto_sem_historico(tmp_path, monkeypatch):
    arquivo = tmp_path / "Boletim FLEXX AG 2032 ESP.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{
        "filename": arquivo.name,
        "filepath": str(arquivo),
        "chunk_index": 0,
        "content": "dados do AG 2032",
    }]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs), \
         patch("app.rag.engine.litellm.completion") as completion:
        resposta = run_pu_matcher_agent(query="me traga o arquivo do AG 2032")

    completion.assert_not_called()
    assert resposta["model_used"] == "atalho-download-fontes"
    assert resposta["caminho"] == "download-direto-fontes"
    assert resposta["sources"] == [arquivo.name]
    assert resposta["source_refs"][0]["nome_arquivo"] == arquivo.name
    assert "Use o download abaixo" in resposta["answer"]


def test_run_pu_matcher_agent_download_direto_filtra_codigo_exato(
    tmp_path, monkeypatch
):
    arquivo_cl_2060 = tmp_path / "Boletim FLEXX CL 2060.pdf"
    arquivo_cl_2081 = tmp_path / "Boletim FLEXX CL 2081.pdf"
    arquivo_iso = tmp_path / "ISO 22635 - Dimensao.pdf"
    for arquivo in (arquivo_cl_2060, arquivo_cl_2081, arquivo_iso):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [
        {
            "filename": arquivo_cl_2060.name,
            "filepath": str(arquivo_cl_2060),
            "chunk_index": 0,
            "content": "dados do CL 2060",
        },
        {
            "filename": arquivo_iso.name,
            "filepath": str(arquivo_iso),
            "chunk_index": 0,
            "content": "norma citada no contexto",
        },
        {
            "filename": arquivo_cl_2081.name,
            "filepath": str(arquivo_cl_2081),
            "chunk_index": 0,
            "content": "dados do CL 2081",
        },
    ]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga o arquivo do CL 2060")

    assert resposta["sources"] == [arquivo_cl_2060.name]
    assert [ref["nome_arquivo"] for ref in resposta["source_refs"]] == [
        arquivo_cl_2060.name
    ]


def test_run_pu_matcher_agent_download_direto_nao_cai_em_vizinhos_sem_codigo(
    tmp_path, monkeypatch
):
    arquivo_rge = tmp_path / "Boletim FLEXX RGE 2020.pdf"
    arquivo_iso = tmp_path / "Boletim FLEXX ISO 131500.pdf"
    for arquivo in (arquivo_rge, arquivo_iso):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [
        {
            "filename": arquivo_rge.name,
            "filepath": str(arquivo_rge),
            "chunk_index": 0,
            "content": "vizinho semantico",
        },
        {
            "filename": arquivo_iso.name,
            "filepath": str(arquivo_iso),
            "chunk_index": 0,
            "content": "outro vizinho",
        },
    ]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga o boletim do AG 2060")

    assert resposta["sources"] == []
    assert resposta["source_refs"] == []
    assert "AG 2060" in resposta["answer"]
    assert arquivo_rge.name not in resposta["answer"]


def test_stream_pu_matcher_agent_publica_source_refs_no_meta(tmp_path, monkeypatch):
    arquivo = tmp_path / "Boletim FLEXX AG 2032.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{
        "filename": arquivo.name,
        "filepath": str(arquivo),
        "chunk_index": 0,
        "content": "dados do AG 2032",
    }]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs), \
         patch("app.rag.engine.litellm.completion", return_value=_final_completion("ok")):
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(query="traga informacoes sobre AG 2032")
        ]

    meta = eventos[0]
    assert meta["sources"] == [arquivo.name]
    assert meta["source_refs"][0]["nome_arquivo"] == arquivo.name
    assert meta["source_refs"][0]["download_url"].endswith("/download")


def test_stream_pu_matcher_agent_traz_arquivo_direto_sem_historico(
    tmp_path, monkeypatch
):
    arquivo = tmp_path / "Boletim FLEXX AG 2032 ESP.pdf"
    arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{
        "filename": arquivo.name,
        "filepath": str(arquivo),
        "chunk_index": 0,
        "content": "dados do AG 2032",
    }]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs), \
         patch("app.rag.engine.litellm.completion") as completion:
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(query="me traga o arquivo do AG 2032")
        ]

    completion.assert_not_called()
    assert eventos[0]["type"] == "meta"
    assert eventos[0]["caminho"] == "download-direto-fontes"
    assert eventos[0]["source_refs"][0]["nome_arquivo"] == arquivo.name
    assert eventos[1]["type"] == "delta"
    assert "Use o download abaixo" in eventos[1]["content"]
