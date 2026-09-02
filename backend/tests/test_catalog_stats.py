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
    _termo_bate_no_nome_produto,
    _termo_bate_no_conteudo,
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


# --- listar_produtos_por_aplicacao: listagem/categoria, não recomendação única

def _ponto_com_conteudo(filepath, content):
    p = MagicMock()
    p.payload = {"filepath": filepath, "content": content}
    return p


def test_lista_produtos_distintos_que_mencionam_flexao_do_termo():
    """Achado real: "colchão" não é substring de "colchões" — a flexão
    precisa achar as duas formas. Nenhum produto se chama literalmente
    "colchão", então tudo cai no bucket por_aplicacao_ou_tipo."""
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

    bucket = resultado["por_aplicacao_ou_tipo"]
    assert bucket["total"] == 2
    assert set(bucket["produtos"]) == {"FLEXX UC 2258", "FLEXX ADT 428"}
    assert bucket["truncado"] is False
    assert resultado["por_nome_ou_familia"]["total"] == 0


def test_lista_produtos_por_padrao_traz_previa_de_10_e_sinaliza_truncamento():
    fake_client = MagicMock()
    pontos = [
        _ponto_com_conteudo(f"...\\PRODUTO {i}\\Boletim.pdf", "menciona colchão")
        for i in range(15)
    ]
    fake_client.scroll.return_value = (pontos, None)
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("colchão")  # listar_todos=False (padrão)

    bucket = resultado["por_aplicacao_ou_tipo"]
    assert bucket["total"] == 15
    assert len(bucket["produtos"]) == 10
    assert bucket["truncado"] is True


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

    bucket = resultado["por_aplicacao_ou_tipo"]
    assert bucket["total"] == 37
    assert len(bucket["produtos"]) == 37
    assert bucket["truncado"] is False


# --- sem termo_busca: lista o catálogo inteiro, sem filtro nenhum ----------
# Pedido do usuário: "liste todos os produtos" (sem categoria) hoje só
# recebia a contagem agregada — precisa também poder listar sem filtro.

def test_sem_termo_busca_inclui_todos_os_produtos_no_bucket_de_nome():
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto_com_conteudo(r"...\FLEXX UC 2258\Boletim.pdf", "aplicação em colchões"),
            _ponto_com_conteudo(r"...\Adesivo Pisos\Boletim.pdf", "nada a ver com o outro"),
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao()  # sem termo_busca nenhum

    assert resultado["por_nome_ou_familia"]["total"] == 2
    assert set(resultado["por_nome_ou_familia"]["produtos"]) == {"FLEXX UC 2258", "Adesivo Pisos"}
    assert resultado["por_aplicacao_ou_tipo"]["total"] == 0
    assert resultado["termo_buscado"] == ""


def test_termo_busca_vazio_explicito_tambem_lista_tudo():
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [_ponto_com_conteudo(r"...\FLEXX UC 2258\Boletim.pdf", "qualquer coisa")], None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("")

    assert resultado["por_nome_ou_familia"]["total"] == 1


# --- família/código no nome do produto (ex: "CAT", "TH", "AG") -------------
# Pedido do usuário: "quando eu pedir Cat, traga somente produtos que tem
# Cat no nome" — diferente de aplicação/tipo (que olha o CONTEÚDO), família
# olha o NOME do produto (padrão real do acervo: FLEXX <FAMÍLIA> <NÚMERO>).
# Os dois ficam em buckets SEPARADOS (pedido do usuário: o agente precisa
# entender a diferença entre nome de produto e aplicação/segmento, e
# perguntar quando houver dúvida em vez de misturar os dois).

def test_termo_bate_no_nome_produto_palavra_inteira():
    assert _termo_bate_no_nome_produto("cat", "FLEXX CAT 42") is True
    assert _termo_bate_no_nome_produto("CAT", "FLEXX CAT 42") is True  # case-insensitive


def test_termo_bate_no_nome_produto_nao_casa_substring():
    """"cat" não pode bater em "catalisador"/nomes que só CONTÊM as letras —
    tem que ser uma palavra inteira do nome, senão "categoria" no conteúdo
    de qualquer produto viraria falso positivo pra família "CAT"."""
    assert _termo_bate_no_nome_produto("cat", "FLEXX CATALISADOR X") is False


def test_listar_por_familia_no_nome_fica_separado_do_bucket_de_conteudo():
    """Achado real: "CAT" aparece em ~36 produtos "FLEXX CAT NNN" pelo NOME
    — vai pra por_nome_ou_familia, nunca pra por_aplicacao_ou_tipo."""
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto_com_conteudo(r"...\FLEXX CAT 42\Boletim.pdf", "ficha de segurança padrão"),
            _ponto_com_conteudo(r"...\FLEXX TH M60AMA3\Boletim.pdf", "produto totalmente distinto"),
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("CAT")

    assert resultado["por_nome_ou_familia"]["total"] == 1
    assert resultado["por_nome_ou_familia"]["produtos"] == ["FLEXX CAT 42"]
    assert resultado["por_aplicacao_ou_tipo"]["total"] == 0


def test_listar_por_familia_e_por_conteudo_nao_se_misturam():
    """Produto que bate por NOME (família CAT) e produto que bate por
    CONTEÚDO genuíno (aplicação "colchão") ficam em buckets diferentes —
    nenhum aparece no bucket errado."""
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto_com_conteudo(r"...\FLEXX CAT 42\Boletim.pdf", "qualquer coisa"),
            _ponto_com_conteudo(r"...\FLEXX AG 2066\Boletim.pdf", "usado para colchão"),
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("colchão")

    assert resultado["por_aplicacao_ou_tipo"]["produtos"] == ["FLEXX AG 2066"]
    assert resultado["por_nome_ou_familia"]["total"] == 0


# --- _termo_bate_no_conteudo: palavra inteira, não substring bruta ---------
# Achado real ao validar a busca por família "CAT" ao vivo: substring simples
# ("cat" in content) batia em "catálise"/"catalisador" (termos reais de
# química), inflando o resultado de ~36 produtos certos pra ~550 errados.

def test_termo_curto_exige_palavra_inteira_nao_bate_em_fragmento():
    assert _termo_bate_no_conteudo("cat", "alguns ajustes na catálise serão necessários") is False


def test_termo_curto_bate_quando_aparece_como_palavra_isolada():
    assert _termo_bate_no_conteudo("cola", "o produto é uma cola de pu monocomponente") is True


def test_termo_longo_cobre_plural_sem_prefixo_aberto():
    assert _termo_bate_no_conteudo("colchão", "aplicação em colchões de espuma") is True


def test_correia_nao_bate_em_corretamente_nem_corrente():
    """Regressão do incidente real: o stem ``corre`` transformava textos
    genéricos de FISPQ em 341 supostos produtos para correia."""
    assert _termo_bate_no_conteudo(
        "correia", "nenhum perigo quando usado corretamente; lavar com água corrente"
    ) is False


def test_correia_bate_no_singular_e_plural_como_palavra_inteira():
    assert _termo_bate_no_conteudo("correia", "aplicação em correia industrial") is True
    assert _termo_bate_no_conteudo("correia", "produção de correias transportadoras") is True


def test_termo_de_varias_palavras_continua_usando_substring():
    """Frase (não 1 palavra só) — risco de falso positivo por fragmento é
    baixo o bastante pra manter o comportamento simples de antes."""
    assert _termo_bate_no_conteudo(
        "construção civil", "usado no setor de construção civil e reformas"
    ) is True


def test_listar_por_familia_cat_nao_traz_ruido_de_catalise():
    """Reprodução direta do bug relatado pelo usuário: buscar "CAT" não pode
    trazer produto nenhum (em nenhum bucket) só porque o conteúdo menciona
    "catálise". Achado seguinte, também real: uma tabela comparativa no
    boletim de outro produto pode citar "FLEXX CAT 90" pelo nome exato —
    isso É um match de conteúdo genuíno (não é bug), só não pode ser
    confundido com "este produto é da família CAT"; por isso fica no
    bucket por_aplicacao_ou_tipo, separado do por_nome_ou_familia."""
    fake_client = MagicMock()
    fake_client.scroll.return_value = (
        [
            _ponto_com_conteudo(r"...\FLEXX CAT 42\Boletim.pdf", "ficha de segurança padrão"),
            _ponto_com_conteudo(
                r"...\FLEXX ADT 404\Boletim.pdf",
                "promove excelente estabilidade de processo, ajustes na catálise",
            ),
        ],
        None,
    )
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        resultado = listar_produtos_por_aplicacao("CAT")

    assert resultado["por_nome_ou_familia"]["produtos"] == ["FLEXX CAT 42"]
    assert resultado["por_aplicacao_ou_tipo"]["total"] == 0


def test_falha_no_qdrant_ao_listar_levanta_erro_tipado():
    fake_client = MagicMock()
    fake_client.scroll.side_effect = Exception("qdrant fora do ar")
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=fake_client):
        with pytest.raises(RetrievalIndisponivelError):
            listar_produtos_por_aplicacao("colchão")
