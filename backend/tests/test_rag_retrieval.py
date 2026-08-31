"""
Ticket 2 do plano de correção (AUD-003): falha real do Qdrant/embedding não
pode ser confundida com "catálogo vazio" — hoje um `except Exception: return []`
genérico faz o agente responder de conhecimento geral como se a coleção só
estivesse vazia, mesmo quando o Qdrant está fora do ar.

Seam: `retrieve_products_context()` (backend/app/rag/engine.py), mockando o
cliente Qdrant e `get_embedding` — não sobe um Qdrant real, mas exercita a
função de verdade (não é side-channel: é a interface pública do módulo).
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag.engine import retrieve_products_context, RetrievalIndisponivelError


def _fake_collections(names):
    fake = MagicMock()
    fake.name = None
    collections = []
    for name in names:
        c = MagicMock()
        c.name = name
        collections.append(c)
    result = MagicMock()
    result.collections = collections
    return result


def test_colecao_ausente_retorna_lista_vazia_sem_levantar_erro():
    """Coleção nunca ingerida é um estado normal (ainda não fizeram a primeira
    ingestão) — não é uma falha, continua retornando []."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections(["outra_colecao"])

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client):
        result = retrieve_products_context("consulta qualquer")

    assert result == []


def test_falha_ao_conectar_no_qdrant_levanta_erro_tipado_em_vez_de_lista_vazia():
    with patch("app.rag.engine._get_qdrant_client", side_effect=ConnectionError("qdrant fora do ar")):
        with pytest.raises(RetrievalIndisponivelError):
            retrieve_products_context("consulta qualquer")


def test_falha_no_get_collections_levanta_erro_tipado():
    fake_client = MagicMock()
    fake_client.get_collections.side_effect = Exception("timeout")

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client):
        with pytest.raises(RetrievalIndisponivelError):
            retrieve_products_context("consulta qualquer")


def test_falha_no_embedding_levanta_erro_tipado():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", side_effect=Exception("modelo indisponível")):
        with pytest.raises(RetrievalIndisponivelError):
            retrieve_products_context("consulta qualquer")


def test_falha_na_busca_vetorial_levanta_erro_tipado():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.search.side_effect = Exception("vetor incompatível")

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        with pytest.raises(RetrievalIndisponivelError):
            retrieve_products_context("consulta qualquer")


def test_busca_bem_sucedida_retorna_payloads():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    hit = MagicMock()
    hit.payload = {"filename": "boletim.pdf", "content": "texto"}
    fake_client.search.return_value = [hit]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context("consulta qualquer")

    assert result == [{"filename": "boletim.pdf", "content": "texto"}]
