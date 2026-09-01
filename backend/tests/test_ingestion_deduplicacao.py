"""
Achado real (auditoria da coleção de produção, fora deste ambiente): ~4.900
chunks — quase metade do acervo de 10.499 pontos — são redundantes, por dois
padrões distintos, nenhum filtrado até esta sessão:

1. "DOCUMENTAÇÃO DE PRODUTO RESTAURADO 0906" é uma árvore de pastas
   restaurada de um backup antigo que duplica, byte a byte, ~3.500 arquivos
   já existentes na árvore "atual" do acervo (3.503 grupos, ~4.913 chunks
   redundantes).
2. ~1.200 pares de PDF/DOCX do mesmo boletim/FISPQ, mesma pasta, mesmo nome
   — indexados em dobro (a extração de texto difere ~5% entre formatos, não
   é hash idêntico, mas é claramente o mesmo documento).

Seam: funções puras de detecção (_agrupar_por_nome_base,
_escolher_arquivo_preferido, _hash_conteudo, _ordenar_por_prioridade) testadas
isoladamente; `ingest_catalog_directory()` com o cliente Qdrant mockado e
arquivos `.txt` reais num diretório temporário testa a integração ponta a
ponta do dedup por conteúdo (mesmo padrão de test_ingestion_reconciliation.py).
"""
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.rag.ingestion import (
    ingest_catalog_directory,
    _agrupar_por_nome_base,
    _escolher_arquivo_preferido,
    _filtrar_duplicatas_de_formato,
    _ordenar_por_prioridade,
    _normalizar_texto,
    _hash_conteudo,
)


# --- funções puras de detecção -----------------------------------------------

def test_agrupar_por_nome_base_agrupa_mesma_pasta_mesmo_nome_extensao_diferente():
    arquivos = [
        r"C:\acervo\FLEXX AG 2032\FISPQ FLEXX AG 2032.pdf",
        r"C:\acervo\FLEXX AG 2032\FISPQ FLEXX AG 2032.docx",
        r"C:\acervo\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf",
    ]
    grupos = _agrupar_por_nome_base(arquivos)
    assert len(grupos) == 2
    (chave_fispq,) = [k for k in grupos if "fispq" in k[1]]
    assert len(grupos[chave_fispq]) == 2


def test_agrupar_por_nome_base_nao_agrupa_pastas_diferentes():
    arquivos = [
        r"C:\acervo\FLEXX AG 2032\Boletim.pdf",
        r"C:\acervo\FLEXX AG 2062\Boletim.pdf",
    ]
    grupos = _agrupar_por_nome_base(arquivos)
    assert len(grupos) == 2


def test_escolher_arquivo_preferido_prioriza_pdf_sobre_docx():
    escolhido = _escolher_arquivo_preferido(["doc.docx", "doc.pdf", "doc.doc"])
    assert escolhido == "doc.pdf"


def test_filtrar_duplicatas_de_formato_mantem_so_o_pdf():
    arquivos = [
        r"C:\acervo\X\FISPQ.pdf",
        r"C:\acervo\X\FISPQ.docx",
        r"C:\acervo\X\Boletim.pdf",  # nome diferente, não é duplicata
    ]
    mantidos, descartados = _filtrar_duplicatas_de_formato(arquivos)
    assert set(mantidos) == {r"C:\acervo\X\FISPQ.pdf", r"C:\acervo\X\Boletim.pdf"}
    assert descartados == [r"C:\acervo\X\FISPQ.docx"]


def test_normalizar_texto_ignora_diferenca_de_espacamento():
    assert _normalizar_texto("Texto   com\n\nquebras") == _normalizar_texto("texto com quebras")


def test_hash_conteudo_igual_para_texto_equivalente_com_espacamento_diferente():
    assert _hash_conteudo("Boletim FLEXX AG 2032\n\ndados") == _hash_conteudo("boletim flexx ag 2032 dados")


def test_hash_conteudo_diferente_para_texto_diferente():
    assert _hash_conteudo("produto A") != _hash_conteudo("produto B")


def test_ordenar_por_prioridade_coloca_pasta_restaurada_por_ultimo():
    arquivos = [
        r"C:\acervo\RESTAURADO 0906\FLEXX AG 2032\Boletim.pdf",
        r"C:\acervo\FLEXX AG 2032\Boletim.pdf",
    ]
    ordenado = _ordenar_por_prioridade(arquivos)
    assert ordenado[0] == r"C:\acervo\FLEXX AG 2032\Boletim.pdf"


# --- integração via ingest_catalog_directory --------------------------------

def _write_txt(dir_path: str, name: str, texto: str) -> str:
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(texto)
    return path


def _chunk_id(file_path: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{chunk_idx}"))


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])
    client.scroll.return_value = ([], None)
    return client


@pytest.fixture
def fake_embedding():
    with patch("app.rag.ingestion.get_embedding", return_value=[0.1, 0.2, 0.3]):
        yield


def _payloads_upsertados(fake_client) -> list:
    payloads = []
    for chamada in fake_client.upsert.call_args_list:
        for ponto in chamada.kwargs["points"]:
            payloads.append(ponto.payload)
    return payloads


def test_dois_arquivos_com_mesmo_conteudo_so_um_e_indexado(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        _write_txt(tmp, "copia_a.txt", "texto do boletim identico " * 50)
        _write_txt(tmp, "copia_b.txt", "texto do boletim identico " * 50)
        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

    payloads = _payloads_upsertados(fake_client)
    filepaths_indexados = {p["filepath"] for p in payloads}
    assert len(filepaths_indexados) == 1


def test_conteudo_diferente_ambos_sao_indexados(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        _write_txt(tmp, "a.txt", "produto A especificação técnica " * 50)
        _write_txt(tmp, "b.txt", "produto B especificação técnica completamente distinta " * 50)
        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

    payloads = _payloads_upsertados(fake_client)
    filepaths_indexados = {p["filepath"] for p in payloads}
    assert len(filepaths_indexados) == 2


def test_duplicata_ja_indexada_de_execucao_anterior_e_removida_como_orfa(fake_client, fake_embedding):
    """Arquivo que já tinha pontos indexados (de antes deste filtro existir)
    e agora é detectado como duplicata de conteúdo: seus pontos antigos
    precisam ser limpos, igual um arquivo removido do acervo (reaproveita a
    reconciliação já existente — não precisou de um passo de apagar dedicado)."""
    with tempfile.TemporaryDirectory() as tmp:
        caminho_perdedor = _write_txt(tmp, "b_duplicata.txt", "conteudo repetido " * 50)
        caminho_vencedor = _write_txt(tmp, "a_original.txt", "conteudo repetido " * 50)

        id_antigo = _chunk_id(caminho_perdedor, 0)
        fake_client.scroll.return_value = (
            [MagicMock(id=id_antigo, payload={"filepath": caminho_perdedor})],
            None,
        )

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

    fake_client.delete.assert_called_once()
    ids_apagados = fake_client.delete.call_args.kwargs["points_selector"].points
    assert id_antigo in ids_apagados


def test_conteudo_identico_pasta_restaurada_perde_para_pasta_normal(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        pasta_normal = os.path.join(tmp, "FLEXX AG 2032")
        pasta_restaurada = os.path.join(tmp, "RESTAURADO 0906", "FLEXX AG 2032")
        os.makedirs(pasta_normal)
        os.makedirs(pasta_restaurada)
        caminho_normal = _write_txt(pasta_normal, "boletim.txt", "mesmo conteudo " * 50)
        _write_txt(pasta_restaurada, "boletim.txt", "mesmo conteudo " * 50)

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

    payloads = _payloads_upsertados(fake_client)
    filepaths_indexados = {p["filepath"] for p in payloads}
    assert filepaths_indexados == {caminho_normal}
