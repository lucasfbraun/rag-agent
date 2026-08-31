"""
Ticket 6 do plano de correção (AUD-002): os 11.273 trechos do RAG não têm
classificação de sensibilidade — Vendedor via o mesmo conteúdo (inclusive
custo/fórmula, se aparecerem em texto livre) que Gestor. Corrigido com uma
classificação por palavra-chave na ingestão (`payload["sensivel"]`) e um
filtro na busca (`retrieve_products_context(..., incluir_sensivel=...)`).

Decisão de design registrada (não é óbvia, documentar por quê): não existe
`Permission.VIEW_FORMULA` — docs/spec_rbac.md, "Pendências" item 2, deixa
regra de fórmulas em aberto ("inferido: mesmo tratamento de custos, não
confirmado"). Reaproveitado `Permission.VIEW_COSTS` pra gatilhar os dois
(custo E fórmula) até uma decisão de negócio formal — mesmo padrão de "leitura
razoável, não extração literal da spec" que a tarefa 6 (camada MCP) já usou.

Heurística de palavra-chave é DELIBERADAMENTE estreita (poucas frases
específicas, não palavras genéricas como "custo" sozinha) — o risco de
falso positivo é real: specs técnicas legítimas (densidade, NCO%, viscosidade)
que Vendedor PRECISA ver não podem ser classificadas como sensíveis por
engano. Sub-inclusiva de propósito, registrada como debito de precisão.

IMPORTANTE — o que este ticket NÃO resolve: os 11.273 pontos JÁ indexados no
Qdrant não têm o campo `sensivel` (não existia até esta sessão) e não ganham
essa classificação retroativamente sem reingestão — decisão de quando/como
reingerir fica pro usuário, não tomada aqui. Este ticket protege ingestão
NOVA e o mecanismo de filtro; o acervo real ainda precisa de uma reingestão
pra ficar de fato protegido — ver docs/plano_correcao_auditoria_2026-08-25.md.
"""
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.rag.engine import retrieve_products_context
from app.rag.ingestion import _e_conteudo_sensivel, ingest_catalog_directory


# --- heurística de classificação --------------------------------------------

def test_texto_com_custo_industrial_e_classificado_como_sensivel():
    assert _e_conteudo_sensivel("O custo industrial deste produto é de R$ 18,40 por kg.")


def test_texto_com_formula_confidencial_e_classificado_como_sensivel():
    assert _e_conteudo_sensivel("A fórmula confidencial usa 3 matérias-primas em proporção específica.")


def test_texto_de_especificacao_tecnica_normal_nao_e_sensivel():
    """Guarda-corpo contra falso positivo: densidade/NCO%/viscosidade são
    specs técnicas normais que Vendedor precisa ver — não podem ser
    bloqueadas por engano por uma heurística ampla demais."""
    texto = (
        "Densidade aparente: 1,04 ± 0,01 g/cm³. Índice NCO%: 30. "
        "Viscosidade a 25°C: 450 cP. Dureza Shore A: 85."
    )
    assert not _e_conteudo_sensivel(texto)


def test_texto_sobre_seguranca_fispq_nao_e_sensivel():
    texto = "Em caso de contato com a pele, lavar com água e sabão. Usar EPI adequado."
    assert not _e_conteudo_sensivel(texto)


# --- classificação entra no payload da ingestão -----------------------------

def _chunk_id(file_path: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{chunk_idx}"))


@pytest.fixture
def fake_client():
    client = MagicMock()
    col = MagicMock()
    col.name = "pu_products_catalog"
    client.get_collections.return_value = MagicMock(collections=[col])
    client.scroll.return_value = ([], None)
    return client


def test_chunk_sensivel_recebe_payload_sensivel_true(fake_client):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "custos.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("O custo industrial deste produto é de R$ 18,40 por kg.")

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client), \
             patch("app.rag.ingestion.get_embedding", return_value=[0.1, 0.2]):
            ingest_catalog_directory(tmp)

        pontos_gravados = []
        for call in fake_client.upsert.call_args_list:
            pontos_gravados.extend(call.kwargs["points"])
        assert len(pontos_gravados) == 1
        assert pontos_gravados[0].payload["sensivel"] is True


def test_chunk_normal_recebe_payload_sensivel_false(fake_client):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "specs.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("Densidade aparente: 1,04 g/cm³. Índice NCO%: 30.")

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client), \
             patch("app.rag.ingestion.get_embedding", return_value=[0.1, 0.2]):
            ingest_catalog_directory(tmp)

        pontos_gravados = []
        for call in fake_client.upsert.call_args_list:
            pontos_gravados.extend(call.kwargs["points"])
        assert len(pontos_gravados) == 1
        assert pontos_gravados[0].payload["sensivel"] is False


# --- filtro no retrieval ------------------------------------------------------

def _fake_collections():
    col = MagicMock()
    col.name = "pu_products_catalog"
    return MagicMock(collections=[col])


def test_sem_incluir_sensivel_busca_aplica_filtro_de_exclusao():
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections()
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        retrieve_products_context("consulta", incluir_sensivel=False)

    _, kwargs = fake_client.search.call_args
    assert kwargs.get("query_filter") is not None


def test_com_incluir_sensivel_busca_nao_aplica_filtro():
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections()
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        retrieve_products_context("consulta", incluir_sensivel=True)

    _, kwargs = fake_client.search.call_args
    assert kwargs.get("query_filter") is None


def test_incluir_sensivel_default_e_false_fail_closed():
    """Quem esquecer de passar o parâmetro não deve vazar conteúdo sensível
    por acidente — mesma disciplina fail-closed já usada no MCP (tarefa 6)."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections()
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        retrieve_products_context("consulta")  # sem passar incluir_sensivel

    _, kwargs = fake_client.search.call_args
    assert kwargs.get("query_filter") is not None
