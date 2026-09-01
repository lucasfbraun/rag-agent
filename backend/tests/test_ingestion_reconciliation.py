"""
Ticket 7 do plano de correção (AUD-007 + achados relacionados): a ingestão só
fazia upsert — reduzir, mover ou remover um arquivo deixava chunks obsoletos
no Qdrant pra sempre. Corrigido com reconciliação: antes de processar,
`_pontos_existentes_por_arquivo()` varre a coleção e mapeia
`{filepath: {ponto_id, ...}}`; depois de processar cada arquivo, a diferença
entre os IDs antigos e os novos é apagada — e arquivos que sumiram do
`supported` (removidos do disco, ou extensão não suportada) têm TODOS os
pontos apagados no final.

Achados relacionados corrigidos junto (mesmo seam):
- Falha de embedding no meio de um arquivo não grava mais os chunks parciais
  já processados — o arquivo inteiro é descartado, coerente com o relatório
  final que já o contava como "não indexado".
- Corrida na criação da coleção (duas ingestões simultâneas): `create_collection`
  falhando porque outra chamada venceu a corrida não é mais erro fatal.

Seam: `ingest_catalog_directory()` com o cliente Qdrant inteiramente mockado
(MagicMock) — nunca toca a coleção real de 11.273 pontos. Arquivos de teste
são `.txt` reais num diretório temporário (mais simples que mockar
extract_text_from_file/chunk_text, que já são funções puras e determinísticas).
"""
import os
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.rag.ingestion import ingest_catalog_directory, init_qdrant_collection


def _chunk_id(file_path: str, chunk_idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{file_path}::{chunk_idx}"))


def _write_txt(dir_path: str, name: str, num_words: int) -> str:
    """Gera um .txt com palavras suficientes pra produzir N chunks previsíveis
    (chunk_text usa chunk_size=700 por padrão — cada bloco de ~580 palavras
    novas garante 1 chunk por "unidade" de conteúdo)."""
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(" ".join(f"palavra{i}" for i in range(num_words)))
    return path


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.get_collections.return_value = MagicMock(collections=[])
    client.scroll.return_value = ([], None)  # coleção vazia por padrão
    return client


@pytest.fixture
def fake_embedding():
    with patch("app.rag.ingestion.get_embedding", return_value=[0.1, 0.2, 0.3]):
        yield


def _colecao_existente():
    """MagicMock(name=...) NÃO define o atributo .name (é reservado pelo
    mock pra debug/repr) — precisa atribuir depois de criar."""
    col = MagicMock()
    col.name = "pu_products_catalog"
    return MagicMock(collections=[col])


def _scroll_page(payloads_by_id: dict) -> tuple:
    pontos = []
    for point_id, filepath in payloads_by_id.items():
        p = MagicMock()
        p.id = point_id
        p.payload = {"filepath": filepath}
        pontos.append(p)
    return (pontos, None)


def test_reingestao_remove_chunks_obsoletos_do_mesmo_arquivo(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        # Arquivo hoje só gera 1 chunk (poucas palavras) — simula ter encolhido.
        file_path = _write_txt(tmp, "produto.txt", 50)

        ids_antigos = {_chunk_id(file_path, i) for i in range(5)}  # tinha 5 chunks antes
        fake_client.scroll.return_value = _scroll_page({pid: file_path for pid in ids_antigos})
        fake_client.get_collections.return_value = _colecao_existente()

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

        assert fake_client.delete.called
        ids_apagados = set()
        for call in fake_client.delete.call_args_list:
            ids_apagados |= set(call.kwargs["points_selector"].points)
        # O chunk 0 (novo) deve sobreviver; os antigos 1-4 (que não existem
        # mais na versão atual do arquivo) precisam ser apagados.
        assert ids_apagados == ids_antigos - {_chunk_id(file_path, 0)}


def test_arquivo_removido_do_acervo_tem_todos_os_chunks_apagados(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        _write_txt(tmp, "atual.txt", 50)
        arquivo_fantasma = os.path.join(tmp, "nao_existe_mais.txt")
        ids_fantasma = {_chunk_id(arquivo_fantasma, i) for i in range(3)}

        fake_client.scroll.return_value = _scroll_page({pid: arquivo_fantasma for pid in ids_fantasma})
        fake_client.get_collections.return_value = _colecao_existente()

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

        ids_apagados = set()
        for call in fake_client.delete.call_args_list:
            ids_apagados |= set(call.kwargs["points_selector"].points)
        assert ids_fantasma <= ids_apagados


def test_ingestao_de_pasta_parcial_nunca_apaga_arquivo_de_fora_do_escopo(fake_client, fake_embedding):
    """Incidente real (Sessão 30): rodar ingest_catalog_directory numa pasta
    PARCIAL (ex: `--test`, que indexa só 1 família de produto) apagou o
    acervo inteiro — tudo que não estava na pasta parcial foi tratado como
    "arquivo removido". Isso é exatamente o padrão real de uso do projeto
    (ingest_network.py tem --test, indexando só FLEXX AG, e --full, o
    acervo inteiro) — um arquivo de OUTRA pasta nunca pode ser candidato a
    "removido" só porque esta execução não escaneou aquela pasta."""
    with tempfile.TemporaryDirectory() as raiz:
        pasta_escaneada = os.path.join(raiz, "familia_a")
        pasta_fora_do_escopo = os.path.join(raiz, "familia_b")
        os.makedirs(pasta_escaneada)
        os.makedirs(pasta_fora_do_escopo)

        _write_txt(pasta_escaneada, "produto_a.txt", 50)

        arquivo_de_fora = os.path.join(pasta_fora_do_escopo, "produto_b.txt")
        ids_de_fora = {_chunk_id(arquivo_de_fora, i) for i in range(4)}

        fake_client.scroll.return_value = _scroll_page({pid: arquivo_de_fora for pid in ids_de_fora})
        fake_client.get_collections.return_value = _colecao_existente()

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(pasta_escaneada)  # só a pasta A, não a raiz inteira

        ids_apagados = set()
        for call in fake_client.delete.call_args_list:
            ids_apagados |= set(call.kwargs["points_selector"].points)
        assert ids_apagados.isdisjoint(ids_de_fora), (
            "arquivo fora do diretório escaneado foi apagado — mesmo bug que "
            "zerou a coleção real na Sessão 30"
        )


def test_arquivo_inalterado_nao_apaga_nada(fake_client, fake_embedding):
    with tempfile.TemporaryDirectory() as tmp:
        file_path = _write_txt(tmp, "estavel.txt", 50)
        id_atual = _chunk_id(file_path, 0)

        fake_client.scroll.return_value = _scroll_page({id_atual: file_path})
        fake_client.get_collections.return_value = _colecao_existente()

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client):
            ingest_catalog_directory(tmp)

        fake_client.delete.assert_not_called()


def test_falha_no_meio_de_um_arquivo_nao_grava_chunks_parciais(fake_client):
    with tempfile.TemporaryDirectory() as tmp:
        # ~1500 palavras => múltiplos chunks (chunk_size=700, overlap=120,
        # step=580) — o 2º chunk falha no embedding.
        file_path = _write_txt(tmp, "grande.txt", 1500)
        fake_client.get_collections.return_value = _colecao_existente()

        chamadas = {"n": 0}

        def embedding_com_falha(chunk, model):
            chamadas["n"] += 1
            if chamadas["n"] == 2:
                raise Exception("timeout simulado no meio do arquivo")
            return [0.1, 0.2, 0.3]

        with patch("app.rag.ingestion.get_qdrant_client", return_value=fake_client), \
             patch("app.rag.ingestion.get_embedding", side_effect=embedding_com_falha):
            ingest_catalog_directory(tmp)

        # Nenhum ponto do arquivo com falha pode ter sido gravado — nem o
        # chunk 0, que tinha sido processado com sucesso antes da falha no 1.
        todos_os_ids_gravados = set()
        for call in fake_client.upsert.call_args_list:
            for ponto in call.kwargs["points"]:
                todos_os_ids_gravados.add(ponto.id)
        assert _chunk_id(file_path, 0) not in todos_os_ids_gravados


def test_criacao_concorrente_da_colecao_nao_e_erro_fatal(fake_client):
    """Duas ingestões simultâneas podem colidir tentando criar a coleção nova
    — se a coleção já existe quando checamos de novo, é a outra chamada
    vencendo a corrida, não uma falha real (AUD-007, achado novo)."""
    chamadas = {"n": 0}

    def get_collections_simulando_corrida():
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            return MagicMock(collections=[])  # ainda não existe
        return _colecao_existente()  # outra ingestão criou nesse meio tempo

    fake_client.get_collections.side_effect = get_collections_simulando_corrida
    fake_client.create_collection.side_effect = Exception("já existe (corrida)")

    init_qdrant_collection(fake_client)  # não deve levantar


def test_init_garante_indices_de_texto_em_filename_e_content_colecao_ja_existente(fake_client):
    """Base da busca híbrida (app.rag.engine): código de produto exato usa
    `filename`, palavra-chave de aplicação/uso usa `content` — sem esses
    índices, o filtro MatchText faz correspondência de substring bruta em vez
    de por palavra ("2032" bate em "203200", achado real, ver PROGRESS.md)."""
    fake_client.get_collections.return_value = _colecao_existente()

    init_qdrant_collection(fake_client)

    assert fake_client.create_payload_index.call_count == 2
    campos = {c.kwargs["field_name"] for c in fake_client.create_payload_index.call_args_list}
    assert campos == {"filename", "content"}
    for chamada in fake_client.create_payload_index.call_args_list:
        assert chamada.kwargs["collection_name"] == "pu_products_catalog"


def test_init_nao_levanta_se_indice_de_texto_falhar_ao_criar(fake_client):
    fake_client.get_collections.return_value = _colecao_existente()
    fake_client.create_payload_index.side_effect = Exception("índice indisponível")

    init_qdrant_collection(fake_client)  # não deve levantar
