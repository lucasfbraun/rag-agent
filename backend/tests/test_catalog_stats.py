"""
Nova capacidade: responder perguntas agregadas sobre o acervo ("quantos
produtos catalogados?") — RAG puro (top-k de chunks) nunca conseguiria
responder isso, precisa de uma varredura estruturada da coleção.

Seam: `obter_estatisticas_catalogo()` com o cliente Qdrant mockado — nunca
toca a coleção real.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag.catalog_stats import (
    obter_estatisticas_catalogo,
    listar_produtos_por_aplicacao,
    _produto_do_filepath,
    _termo_para_busca,
)
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


def test_produto_do_filepath_pula_pasta_fispq_solta():
    """Achado real: pasta genérica "FISPQ" (documentos avulsos, não ligados a
    1 produto) aparecia como se fosse um produto distinto em listagens."""
    fp = r"...\FLEXX AG 2032\FISPQ\FISPQ - perguntas frequentes.pdf"
    assert _produto_do_filepath(fp) == "FLEXX AG 2032"


def test_produto_do_filepath_pula_pasta_amostra_mesmo_em_nome_composto():
    """"AMOSTRA FLEXX PI" e "FLEXX CL AMOSTRA" não são produtos novos — a
    palavra "amostra" em qualquer posição do nome da pasta marca conteúdo de
    referência/exemplo, não um produto vendável."""
    assert _produto_do_filepath(r"...\FLEXX PI\AMOSTRA FLEXX PI\arquivo.pdf") == "FLEXX PI"
    assert _produto_do_filepath(r"...\FLEXX CL\FLEXX CL AMOSTRA\arquivo.pdf") == "FLEXX CL"


def test_produto_do_filepath_nao_exclui_produto_que_so_parece_com_a_palavra():
    """Correspondência é por PALAVRA INTEIRA — uma pasta cujo nome contenha as
    letras "teste" só como parte de outra palavra não pode ser excluída."""
    assert _produto_do_filepath(r"...\FLEXX TESTEIRA 2000\arquivo.pdf") == "FLEXX TESTEIRA 2000"


def test_produto_do_filepath_arquivo_solto_sem_pasta_de_produto_retorna_none():
    """Achado real: um arquivo administrativo solto direto dentro de "FISPQ",
    numa árvore cujo nome também é administrativo ("...RESTAURADO 0906"),
    não tem nenhuma pasta-produto de verdade acima dele — subir até acertar
    os segmentos estruturais fixos da rede ("Documentação de Produto",
    "Qualidade", "GRUPOS") reportaria a raiz inteira como se fosse 1 produto,
    o que é pior que simplesmente não contar esse arquivo."""
    fp = (
        r"//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto"
        r"\DOCUMENTAÇÃO DE PRODUTO RESTAURADO 0906\FISPQ\FISPQ - perguntas frequentes.pdf"
    )
    assert _produto_do_filepath(fp) is None


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


# --- _termo_para_busca: stem simplificado pra cobrir plural/singular pt-br --

def test_termo_para_busca_corta_2_chars_de_termo_longo():
    assert _termo_para_busca("colchão") == "colch"


def test_termo_para_busca_nao_corta_termo_curto():
    assert _termo_para_busca("cola") == "cola"


# --- listar_produtos_por_aplicacao: listagem/categoria, não recomendação única

def _ponto_com_conteudo(filepath, content):
    p = MagicMock()
    p.payload = {"filepath": filepath, "content": content}
    return p


def test_lista_produtos_distintos_que_mencionam_o_termo_stemado():
    """Achado real: "colchão" não é substring de "colchões" — o stem "colch"
    precisa achar as duas formas."""
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto_com_conteudo(r"...\FLEXX UC 2258\Boletim.pdf", "aplicação em colchões de espuma"),
            _ponto_com_conteudo(r"...\FLEXX UC 2258\FISPQ.pdf", "ficha de segurança, sem relação"),  # mesmo produto
            _ponto_com_conteudo(r"...\FLEXX ADT 428\Boletim.pdf", "usado na produção de colchão ortopédico"),
            _ponto_com_conteudo(r"...\Adesivo Pisos\Boletim.pdf", "para pisos vinílicos, sem relação com o pedido"),
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("colchão")

    assert resultado["total_produtos_encontrados"] == 2
    assert set(resultado["produtos"]) == {"FLEXX UC 2258", "FLEXX ADT 428"}
    assert resultado["truncado"] is False


def test_lista_produtos_por_padrao_traz_previa_de_10_e_sinaliza_truncamento():
    fake_client = MagicMock()
    pontos = [
        _ponto_com_conteudo(f"...\\PRODUTO {i}\\Boletim.pdf", "menciona colchão")
        for i in range(15)
    ]
    fake_client.scroll.return_value = (pontos, None)
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("colchão")  # listar_todos=False (padrão)

    assert resultado["total_produtos_encontrados"] == 15
    assert len(resultado["produtos"]) == 10
    assert resultado["truncado"] is True


def test_lista_produtos_com_listar_todos_devolve_tudo_sem_limite():
    """Pedido do usuário: se ele escolher "todos", listar todos os produtos
    encontrados, não importa quantos sejam."""
    fake_client = MagicMock()
    pontos = [
        _ponto_com_conteudo(f"...\\PRODUTO {i}\\Boletim.pdf", "menciona colchão")
        for i in range(37)
    ]
    fake_client.scroll.return_value = (pontos, None)
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("colchão", listar_todos=True)

    assert resultado["total_produtos_encontrados"] == 37
    assert len(resultado["produtos"]) == 37
    assert resultado["truncado"] is False


def test_falha_no_qdrant_ao_listar_levanta_erro_tipado():
    fake_client = MagicMock()
    fake_client.scroll.side_effect = Exception("qdrant fora do ar")
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        with pytest.raises(RetrievalIndisponivelError):
            listar_produtos_por_aplicacao("colchão")
