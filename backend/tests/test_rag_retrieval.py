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

from app.rag.engine import (
    retrieve_products_context,
    RetrievalIndisponivelError,
    _detectar_codigos_produto,
    _extrair_palavras_chave,
    _codigos_sem_correspondencia,
    _montar_context_str,
)


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


# --- busca híbrida: código de produto detectado na pergunta ------------------
# Motivo: busca puramente semântica confundia códigos parecidos (ex: "AG 2032"
# trazia "AG 2062" ou produto totalmente diferente) — ver PROGRESS.md.

def test_detectar_codigos_produto_acha_sigla_mais_numero():
    assert _detectar_codigos_produto("traga os dados do boletim AG 2032") == ["ag 2032"]


def test_detectar_codigos_produto_sem_padrao_reconhecivel_retorna_vazio():
    assert _detectar_codigos_produto("quero um produto pra assento de ônibus") == []


def test_detectar_codigos_produto_multiplos_codigos():
    assert _detectar_codigos_produto("compare AG 2032 com CAT 136") == ["ag 2032", "cat 136"]


def test_detectar_codigos_produto_ignora_artigo_colado_em_numero():
    """Bug real: "liste os 77" (77 = contagem de uma listagem anterior, não
    código nenhum) casava "os" + "77" como se fosse o código "OS 77" — o
    agente respondia "produto não encontrado" em vez de listar os 77
    pedidos. "os"/"as"/"um"/"de"/etc. nunca são sigla de família real."""
    assert _detectar_codigos_produto("liste os 77") == []
    assert _detectar_codigos_produto("quero ver as 15 opções") == []
    assert _detectar_codigos_produto("mostra um 20") == []


def test_detectar_codigos_produto_nao_ignora_familia_real_parecida_com_stopword():
    """Sigla real de 2 letras que não está na lista de artigos/preposições
    continua funcionando normalmente."""
    assert _detectar_codigos_produto("boletim RG 2464") == ["rg 2464"]


def _hit(filename, chunk_index, content="texto"):
    payload = {"filename": filename, "chunk_index": chunk_index, "content": content}
    ponto = MagicMock()
    ponto.payload = payload
    return ponto


def test_codigo_de_produto_detectado_prioriza_match_exato_sobre_semantico():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    # busca exata (scroll) acha o produto certo
    fake_client.scroll.return_value = ([_hit("Boletim FLEXX AG 2032.pdf", 0)], None)
    # busca semântica erra feio (embedding local confundindo códigos)
    hit_errado = MagicMock()
    hit_errado.payload = {"filename": "Pró Bloq Cimento Elástico.pdf", "chunk_index": 0, "content": "outro produto"}
    fake_client.search.return_value = [hit_errado]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context("traga os dados do boletim AG 2032", top_k=6)

    assert result[0]["filename"] == "Boletim FLEXX AG 2032.pdf"
    assert any(r["filename"] == "Pró Bloq Cimento Elástico.pdf" for r in result)


def test_pergunta_sem_codigo_nem_palavra_chave_extraivel_nunca_chama_scroll():
    """Pergunta só com palavras curtas/stopwords (sem código de produto e sem
    termo de conteúdo >=4 letras fora da stoplist) não tem nenhum sinal pra
    busca exata/palavra-chave — só a semântica roda."""
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        retrieve_products_context("que tem pra mim")

    fake_client.scroll.assert_not_called()


# --- busca por palavra-chave: pergunta descreve aplicação/uso, não código ----
# Achado real: "cola para rolha de cortiça" não trazia o FLEXX AG 2066 (cujo
# texto de aplicação cita "rolhas de cortiça" quase literalmente) nos top-6
# por busca semântica pura — o embedding local falhou até com quase as
# mesmas palavras do boletim certo.

def test_extrair_palavras_chave_descarta_stopwords_e_termos_curtos():
    assert _extrair_palavras_chave("quero um produto para colar rolha de cortiça") == [
        "colar", "rolha", "cortiça"
    ]


def test_palavra_chave_com_duas_ou_mais_batendo_prioriza_sobre_semantico():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    hit_certo = MagicMock()
    hit_certo.payload = {
        "filename": "Boletim FLEXX AG 2066.pdf", "chunk_index": 0,
        "content": "agente de colagem para producao de rolhas de cortica aglomerada",
    }
    hit_fraco = MagicMock()
    hit_fraco.payload = {
        "filename": "Boletim qualquer.pdf", "chunk_index": 0,
        "content": "menciona rolha uma vez só, nada mais bate",
    }
    fake_client.scroll.return_value = ([hit_certo, hit_fraco], None)
    hit_errado = MagicMock()
    hit_errado.payload = {"filename": "FISPQ CLEAR 165.pdf", "chunk_index": 0, "content": "produto sem relacao"}
    fake_client.search.return_value = [hit_errado]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context("cola para rolha de cortiça", top_k=6)

    assert result[0]["filename"] == "Boletim FLEXX AG 2066.pdf"
    assert not any(r["filename"] == "Boletim qualquer.pdf" for r in result)


def test_termo_discriminante_nao_some_por_limite_global_de_50_candidatos():
    """Regressão real: a consulta com pneus/peças/correias fazia um único
    scroll OR limitado a 50. A ordem não é relevância e nenhum TH correto
    entrou no lote, apesar de o boletim conter todos os termos pedidos."""
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    hit_certo = _hit(
        "BOLETIM TÉCNICO FLEXX TH T160DE1.pdf",
        0,
        "produz elastômero de poliuretano para pneus industriais sólidos, "
        "peças mecânicas e correias transportadoras",
    )
    ruido = [_hit(f"FISPQ {i}.pdf", 0, "produto usado corretamente") for i in range(50)]

    def scroll_por_termo(**kwargs):
        filtro = kwargs["scroll_filter"]
        termos_must = [cond.match.text for cond in (filtro.must or []) if hasattr(cond.match, "text")]
        if "correia" in termos_must:
            return ([hit_certo], None)
        return (ruido, None)

    fake_client.scroll.side_effect = scroll_por_termo
    hit_semantico = MagicMock()
    hit_semantico.payload = {
        "filename": "Boletim FLEXX ADT 431.pdf",
        "chunk_index": 0,
        "content": "aditivo para elastômeros",
    }
    fake_client.search.return_value = [hit_semantico]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context(
            "elastômero para pneu industrial, peça mecânica e correia",
            top_k=6,
        )

    assert result[0]["filename"] == "BOLETIM TÉCNICO FLEXX TH T160DE1.pdf"


def test_falha_na_busca_por_palavra_chave_nao_derruba_a_busca_semantica():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.side_effect = Exception("índice de texto indisponível")
    hit = MagicMock()
    hit.payload = {"filename": "boletim.pdf", "chunk_index": 0, "content": "texto"}
    fake_client.search.return_value = [hit]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context("cola para rolha de cortiça")

    assert result == [{"filename": "boletim.pdf", "chunk_index": 0, "content": "texto"}]


def test_falha_na_busca_exata_nao_derruba_a_busca_semantica():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.side_effect = Exception("índice de texto indisponível")
    hit = MagicMock()
    hit.payload = {"filename": "boletim.pdf", "chunk_index": 0, "content": "texto"}
    fake_client.search.return_value = [hit]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        result = retrieve_products_context("traga os dados do boletim AG 2032")

    assert result == [{"filename": "boletim.pdf", "chunk_index": 0, "content": "texto"}]


def test_match_exato_respeita_filtro_de_conteudo_sensivel():
    from app.config import COLLECTION_NAME

    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.return_value = ([], None)
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        retrieve_products_context("traga os dados do boletim AG 2032", incluir_sensivel=False)

    filtro_usado = fake_client.scroll.call_args.kwargs["scroll_filter"]
    assert len(filtro_usado.must_not) == 1


# --- "não encontrado": código citado mas ausente de todo o contexto retornado --
# Pedido do usuário: se ele perguntar sobre um produto e não achar, o sistema
# deve dizer que não achou na base — não apresentar o produto mais parecido
# (retornado pela busca semântica) como se fosse o certo.

def test_codigos_sem_correspondencia_quando_nenhum_doc_bate_com_o_codigo():
    docs = [{"filename": "Pró Bloq Cimento Elástico.pdf"}, {"filename": "Boletim FLEXX AG 2062.pdf"}]
    assert _codigos_sem_correspondencia("traga os dados do boletim AG 2032", docs) == ["ag 2032"]


def test_codigos_sem_correspondencia_vazio_quando_algum_doc_bate():
    docs = [{"filename": "Boletim FLEXX AG 2032.pdf"}]
    assert _codigos_sem_correspondencia("traga os dados do boletim AG 2032", docs) == []


def test_codigos_sem_correspondencia_vazio_quando_pergunta_nao_cita_codigo():
    docs = [{"filename": "Pró Bloq Cimento Elástico.pdf"}]
    assert _codigos_sem_correspondencia("quero um produto pra assento de ônibus", docs) == []


def test_montar_context_str_sem_docs_avisa_catalogo_vazio():
    context = _montar_context_str("qualquer pergunta", [])
    assert "não foi indexada ou está vazia" in context


def test_montar_context_str_inclui_aviso_quando_codigo_pedido_nao_bate_em_nada():
    docs = [{"filename": "Pró Bloq Cimento Elástico.pdf", "content": "outro produto"}]
    context = _montar_context_str("traga os dados do boletim AG 2032", docs)
    assert "NENHUM documento com esse código exato foi encontrado" in context
    assert "ag 2032" in context


def test_montar_context_str_sem_aviso_quando_codigo_pedido_foi_encontrado():
    docs = [{"filename": "Boletim FLEXX AG 2032.pdf", "content": "dados reais"}]
    context = _montar_context_str("traga os dados do boletim AG 2032", docs)
    assert "NENHUM documento com esse código exato foi encontrado" not in context


def test_montar_context_str_liste_os_77_nao_dispara_aviso_de_nao_encontrado():
    """Bug real (relatado pelo usuário): resposta de acompanhamento "liste os
    77" (pedindo a lista completa mencionada na resposta anterior) disparava
    o aviso de "código não encontrado", atropelando a listagem pedida."""
    docs = [{"filename": "FLEXX CL 2001.pdf", "content": "aplicação automotiva"}]
    context = _montar_context_str("liste os 77", docs)
    assert "NENHUM documento com esse código exato foi encontrado" not in context
