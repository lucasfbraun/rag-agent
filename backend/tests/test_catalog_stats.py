"""
Nova capacidade: responder perguntas agregadas sobre o acervo ("quantos
produtos catalogados?") — RAG puro (top-k de chunks) nunca conseguiria
responder isso, precisa de uma varredura estruturada da coleção.

Seam: `obter_estatisticas_catalogo()` com o cliente Qdrant mockado — nunca
toca a coleção real.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag.catalog_stats import obter_estatisticas_catalogo, _produto_do_filepath
from app.rag.engine import RetrievalIndisponivelError


# --- _produto_do_filepath: extração do nome de produto a partir do caminho ---

def test_produto_do_filepath_pega_pasta_imediatamente_acima_do_arquivo():
    fp = r"//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto\FLEXX® AG\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf"
    assert _produto_do_filepath(fp) == "FLEXX AG 2032"


def test_produto_do_filepath_pula_subpasta_obsoletos():
    fp = r"//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto\FLEXX® SL\FLEXX® SL 2524\Obsoletos\Boletim antigo.pdf"
    assert _produto_do_filepath(fp) == "FLEXX® SL 2524"


def test_produto_do_filepath_pula_subpasta_certificados_case_insensitive():
    fp = r"//10.1.1.205/flexivel/.../FLEXX RG 2464\CERTIFICADOS\Certificado FLEXX RG 2464 56079.pdf"
    assert _produto_do_filepath(fp) == "FLEXX RG 2464"


def test_produto_do_filepath_caminho_raso_demais_retorna_none():
    assert _produto_do_filepath("arquivo.pdf") is None


# --- obter_estatisticas_catalogo: agregação sobre a coleção -----------------

def _ponto(filepath):
    p = MagicMock()
    p.payload = {"filepath": filepath}
    return p


def test_conta_produtos_e_documentos_distintos_de_uma_pagina_so():
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto(r"...\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf"),
            _ponto(r"...\FLEXX AG 2032\FISPQ FLEXX AG 2032.pdf"),  # mesmo produto
            _ponto(r"...\FLEXX AG 2062\Boletim FLEXX AG 2062.pdf"),  # produto diferente
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = obter_estatisticas_catalogo()

    assert resultado["produtos_catalogados"] == 2
    assert resultado["documentos_indexados"] == 3


def test_pagina_por_todo_o_scroll_ate_offset_none():
    fake_client = MagicMock()
    fake_client.scroll.side_effect = [
        ([_ponto(r"...\FLEXX AG 2032\Boletim.pdf")], "offset-1"),
        ([_ponto(r"...\FLEXX AG 2062\Boletim.pdf")], None),
    ]
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = obter_estatisticas_catalogo()

    assert resultado["produtos_catalogados"] == 2
    assert fake_client.scroll.call_count == 2


def test_falha_no_qdrant_levanta_erro_tipado_em_vez_de_contagem_zerada():
    fake_client = MagicMock()
    fake_client.scroll.side_effect = Exception("qdrant fora do ar")
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        with pytest.raises(RetrievalIndisponivelError):
            obter_estatisticas_catalogo()
