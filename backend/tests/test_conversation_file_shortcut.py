from types import SimpleNamespace

from app.conversation_service import responder_pedido_de_arquivo


REF_AG = {
    "id": "src_0123456789abcdef0123456789abcdef",
    "nome_arquivo": "Boletim FLEXX AG 2032.pdf",
    "download_url": "/api/documentos/fontes/src_0123456789abcdef0123456789abcdef/download",
}
REF_CAT = {
    "id": "src_fedcba9876543210fedcba9876543210",
    "nome_arquivo": "FISPQ FLEXX CAT 136.pdf",
    "download_url": "/api/documentos/fontes/src_fedcba9876543210fedcba9876543210/download",
}


def _conversation(*messages):
    return SimpleNamespace(messages=list(messages))


def _assistant(refs):
    return SimpleNamespace(role="assistant", source_refs=refs)


def _user():
    return SimpleNamespace(role="user", source_refs=[])


def test_pergunta_sem_pedido_de_arquivo_nao_dispara_atalho():
    conversa = _conversation(_assistant([REF_AG]))

    assert responder_pedido_de_arquivo(conversa, "qual a densidade?") is None


def test_pedido_de_arquivo_retorna_ultima_fonte_do_assistente():
    conversa = _conversation(_assistant([REF_CAT]), _user(), _assistant([REF_AG]))

    resposta = responder_pedido_de_arquivo(conversa, "agora me traga o arquivo")

    assert resposta["model_used"] == "atalho-download-fontes"
    assert resposta["sources"] == ["Boletim FLEXX AG 2032.pdf"]
    assert resposta["source_refs"] == [REF_AG]
    assert "resposta anterior" in resposta["answer"]


def test_pedido_de_arquivo_filtra_pelo_nome_quando_usuario_cita_codigo():
    conversa = _conversation(_assistant([REF_AG, REF_CAT]))

    resposta = responder_pedido_de_arquivo(conversa, "me traga o pdf CAT 136")

    assert resposta["source_refs"] == [REF_CAT]


def test_pedido_de_arquivo_sem_fonte_anterior_responde_sem_chamar_llm():
    conversa = _conversation(_assistant([]))

    resposta = responder_pedido_de_arquivo(conversa, "baixar arquivo")

    assert resposta["source_refs"] == []
    assert resposta["model_used"] == "atalho-download-fontes"
    assert "Ainda nao tenho" in resposta["answer"]

