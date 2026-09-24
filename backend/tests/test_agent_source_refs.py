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


def _tool_call(call_id, name, arguments_json):
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = name
    tool_call.function.arguments = arguments_json
    return tool_call


def _completion_with_tool_calls(tool_calls):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = tool_calls
    resp.choices[0].message.content = None
    return resp


def _stream_completion_parts(parts):
    for part in parts:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = part
        yield chunk


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


def test_run_pu_matcher_agent_download_generico_traz_todos_do_codigo(
    tmp_path, monkeypatch
):
    boletim = tmp_path / "Boletim FLEXX CL 2060.pdf"
    fispq = tmp_path / "FISPQ GHS FLEXX CL 2060.pdf"
    vizinho = tmp_path / "Boletim FLEXX CL 2081.pdf"
    for arquivo in (boletim, fispq, vizinho):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [
        {"filename": boletim.name, "filepath": str(boletim), "content": ""},
        {"filename": fispq.name, "filepath": str(fispq), "content": ""},
        {"filename": vizinho.name, "filepath": str(vizinho), "content": ""},
    ]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga o arquivo do CL 2060")

    assert [ref["nome_arquivo"] for ref in resposta["source_refs"]] == [
        boletim.name,
        fispq.name,
    ]


def test_run_pu_matcher_agent_download_boletim_filtra_tipo_especifico(
    tmp_path, monkeypatch
):
    boletim = tmp_path / "Boletim FLEXX CL 2060.pdf"
    fispq = tmp_path / "FISPQ GHS FLEXX CL 2060.pdf"
    for arquivo in (boletim, fispq):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [
        {"filename": boletim.name, "filepath": str(boletim), "content": ""},
        {"filename": fispq.name, "filepath": str(fispq), "content": ""},
    ]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga o boletim do CL 2060")

    assert resposta["sources"] == [boletim.name]
    assert [ref["nome_arquivo"] for ref in resposta["source_refs"]] == [boletim.name]


def test_run_pu_matcher_agent_download_fispq_filtra_tipo_especifico(
    tmp_path, monkeypatch
):
    boletim = tmp_path / "Boletim FLEXX CL 2060.pdf"
    fispq = tmp_path / "FISPQ GHS FLEXX CL 2060.pdf"
    for arquivo in (boletim, fispq):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [
        {"filename": boletim.name, "filepath": str(boletim), "content": ""},
        {"filename": fispq.name, "filepath": str(fispq), "content": ""},
    ]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga a fispq do CL 2060")

    assert resposta["sources"] == [fispq.name]
    assert [ref["nome_arquivo"] for ref in resposta["source_refs"]] == [fispq.name]


def test_run_pu_matcher_agent_download_tipo_especifico_inexistente_nao_traz_tudo(
    tmp_path, monkeypatch
):
    boletim = tmp_path / "Boletim FLEXX CL 2060.pdf"
    boletim.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))
    docs = [{"filename": boletim.name, "filepath": str(boletim), "content": ""}]

    with patch("app.rag.engine.retrieve_products_context", return_value=docs):
        resposta = run_pu_matcher_agent(query="me traga a fispq do CL 2060")

    assert resposta["sources"] == []
    assert resposta["source_refs"] == []
    assert "fispq" in resposta["answer"]
    assert boletim.name not in resposta["answer"]


def test_listagem_de_familia_substitui_fontes_rag_por_fontes_dos_produtos(
    tmp_path, monkeypatch
):
    fonte_ag = tmp_path / "Boletim FLEXX AG 2032.pdf"
    fonte_fispq_ag = tmp_path / "FISPQ FLEXX AG 2032.pdf"
    fonte_iso = tmp_path / "FISPQ FLEXX ISO 130500.pdf"
    fonte_sl = tmp_path / "FISPQ FLEXX SL 2517.pdf"
    for arquivo in (fonte_ag, fonte_fispq_ag, fonte_iso, fonte_sl):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))

    docs_rag = [
        {"filename": fonte_iso.name, "filepath": str(fonte_iso), "content": "ruido"},
        {"filename": fonte_sl.name, "filepath": str(fonte_sl), "content": "ruido"},
    ]
    resultado_familia = {
        "termo_buscado": "AG",
        "por_nome_ou_familia": {
            "total": 2,
            "produtos": ["FLEXX AG 2032", "FLEXX AG 2060"],
            "truncado": False,
            "fontes": [fonte_ag.name, fonte_fispq_ag.name],
            "source_refs": [
                {
                    "id": "src_ag",
                    "nome_arquivo": fonte_ag.name,
                    "download_url": "/api/documentos/fontes/src_ag/download",
                },
                {
                    "id": "src_fispq_ag",
                    "nome_arquivo": fonte_fispq_ag.name,
                    "download_url": "/api/documentos/fontes/src_fispq_ag/download",
                },
            ],
        },
        "por_aplicacao_ou_tipo": {
            "total": 0,
            "produtos": [],
            "truncado": False,
            "fontes": [],
        },
    }
    primeira = _completion_with_tool_calls([
        _tool_call(
            "call_ag",
            "consultar_produtos_por_aplicacao",
            '{"termo_busca":"AG","listar_todos":false}',
        )
    ])

    with patch("app.rag.engine.retrieve_products_context", return_value=docs_rag), \
         patch(
             "app.rag.engine.execute_mcp_tool",
             return_value=json.dumps(resultado_familia),
         ), \
         patch(
             "app.rag.engine.montar_source_refs",
             side_effect=AssertionError("nao deve varrer fontes por filename"),
         ), \
         patch(
             "app.rag.engine.litellm.completion",
             side_effect=[primeira, _final_completion("Temos 2 produtos AG.")],
         ):
        resposta = run_pu_matcher_agent(query="me traga todos os produtos flexx ag")

    assert resposta["sources"] == [fonte_ag.name, fonte_fispq_ag.name]
    assert resposta["source_refs"] == resultado_familia["por_nome_ou_familia"]["source_refs"]
    assert fonte_iso.name not in resposta["sources"]
    assert fonte_sl.name not in resposta["sources"]


def test_stream_listagem_de_familia_publica_fontes_dos_produtos(
    tmp_path, monkeypatch
):
    fonte_ag = tmp_path / "Boletim FLEXX AG 2032.pdf"
    fonte_iso = tmp_path / "FISPQ FLEXX ISO 130500.pdf"
    for arquivo in (fonte_ag, fonte_iso):
        arquivo.write_bytes(b"conteudo")
    monkeypatch.setattr(fontes, "RAG_DOWNLOAD_ROOTS", (str(tmp_path),))

    resultado_familia = {
        "termo_buscado": "AG",
        "por_nome_ou_familia": {
            "total": 1,
            "produtos": ["FLEXX AG 2032"],
            "truncado": False,
            "fontes": [fonte_ag.name],
        },
        "por_aplicacao_ou_tipo": {
            "total": 0,
            "produtos": [],
            "truncado": False,
            "fontes": [],
        },
    }
    primeira = _completion_with_tool_calls([
        _tool_call(
            "call_ag",
            "consultar_produtos_por_aplicacao",
            '{"termo_busca":"AG","listar_todos":false}',
        )
    ])

    with patch(
        "app.rag.engine.retrieve_products_context",
        return_value=[
            {"filename": fonte_iso.name, "filepath": str(fonte_iso), "content": "ruido"}
        ],
    ), patch(
        "app.rag.engine.execute_mcp_tool",
        return_value=json.dumps(resultado_familia),
    ), patch(
        "app.rag.engine.litellm.completion",
        side_effect=[primeira, _stream_completion_parts(["Temos 1 produto AG."])],
    ):
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(query="me traga todos os produtos flexx ag")
        ]

    meta = next(evento for evento in eventos if evento["type"] == "meta")
    assert meta["sources"] == [fonte_ag.name]
    assert [ref["nome_arquivo"] for ref in meta["source_refs"]] == [fonte_ag.name]
    assert fonte_iso.name not in meta["sources"]


def test_listagem_por_terminologia_reutiliza_refs_sem_varrer_smb(
    monkeypatch,
):
    arquivo = "Boletim FLEXX TH T160DE1.pdf"
    refs = [{
        "id": "src_th",
        "nome_arquivo": arquivo,
        "download_url": "/api/documentos/fontes/src_th/download",
    }]
    payload = {
        "nivel_atendido": "identidade_declarada",
        "niveis": {
            "classificacao_estrutural": {"total": 0, "produtos": [], "truncado": False},
            "identidade_declarada": {
                "total": 1,
                "produtos": ["FLEXX TH T160DE1"],
                "truncado": False,
                "evidencias": {
                    "FLEXX TH T160DE1": {"documento": arquivo},
                },
                "source_refs": refs,
            },
            "composicao_comprovada": {"total": 0, "produtos": [], "truncado": False},
            "mencao_no_documento": {"total": 0, "produtos": [], "truncado": False},
        },
    }

    with patch("app.rag.engine.expandir_termos_do_dominio", return_value=[]), \
         patch("app.rag.engine.execute_mcp_tool", return_value=json.dumps(payload)), \
         patch(
             "app.rag.engine.montar_source_refs",
             side_effect=AssertionError("nao deve varrer fontes por filename"),
         ):
        resposta = run_pu_matcher_agent(
            query="me traga os produtos que são FLEXX XXXXXX"
        )

    assert resposta["source_refs"] == refs
    assert resposta["sources"] == [arquivo]


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
