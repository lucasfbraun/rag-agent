"""
Integração da tradução leigo → técnico com `retrieve_products_context`
(bloco 2 da correção de arquitetura, 2026-09-18).

O que estes testes protegem, em ordem de importância:

1. A tradução acontece SÓ no caminho leigo. Quando a pergunta cita um código de
   produto ou um nome de seção, a recuperação exata já é mais confiável que
   qualquer sinônimo — injetar termos traduzidos ali só diluiria um resultado
   que já está certo. Foi assim que "AG 2032" passou a trazer "AG 2062" na
   versão que só tinha busca vetorial; não repetir esse erro é o ponto.

2. Os termos traduzidos SOMAM, nunca substituem. A pergunta original continua
   sendo o critério em todos os caminhos.

3. A tradução é fail-open de ponta a ponta. Nem a chamada ao LLM nem a segunda
   busca vetorial podem derrubar uma recuperação que funcionaria sem elas.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.config import COLLECTION_NAME
from app.rag.engine import _montar_context_str, retrieve_products_context


def _fake_collections(names):
    collections = []
    for name in names:
        c = MagicMock()
        c.name = name
        collections.append(c)
    result = MagicMock()
    result.collections = collections
    return result


def _hit(filename, chunk_index=0, content="texto do boletim"):
    ponto = MagicMock()
    ponto.payload = {
        "filename": filename,
        "filepath": f"/acervo/{filename}",
        "chunk_index": chunk_index,
        "content": content,
    }
    return ponto


@pytest.fixture
def cliente_qdrant():
    fake = MagicMock()
    fake.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake.scroll.return_value = ([], None)
    fake.search.return_value = []
    return fake


# --- quando a tradução entra (e quando não entra) ---------------------------

def test_pergunta_leiga_dispara_a_traducao(cliente_qdrant):
    """Sem código de produto e sem nome de seção: é o caminho que hoje depende
    só do vetor, e o único que todo usuário não-especialista percorre."""
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=[]) as expandir:
        retrieve_products_context("tem alguma cola pra colar espuma em tecido?")
    expandir.assert_called_once()


def test_pergunta_com_codigo_de_produto_nao_dispara_traducao(cliente_qdrant):
    """Correspondência exata de código é mais confiável que sinônimo neste
    acervo — a tradução não tem o que melhorar aqui e só traria ruído."""
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio") as expandir:
        retrieve_products_context("qual a densidade do FLEXX AG 2032?")
    expandir.assert_not_called()


def test_pedido_de_secao_nao_dispara_traducao(cliente_qdrant):
    """O pedido de seção tem caminho próprio, mais preciso (`_recuperar_por_secao`)."""
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio") as expandir:
        retrieve_products_context("qual a reatividade e o armazenamento do AG 2032?")
    expandir.assert_not_called()


# --- os termos traduzidos chegam à busca ------------------------------------

def test_termos_traduzidos_viram_busca_textual_no_qdrant(cliente_qdrant):
    """O ponto central do bloco: "cola" não existe no texto dos boletins, mas
    "adesivo" existe. Sem isto o caminho por palavra-chave morre em silêncio."""
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio",
               return_value=["adesivo", "laminacao"]):
        retrieve_products_context("tem cola pra grudar espuma?")

    pesquisados = []
    for chamada in cliente_qdrant.scroll.call_args_list:
        filtro = chamada.kwargs.get("scroll_filter")
        if filtro is None:
            continue
        for condicao in filtro.must or []:
            texto = getattr(getattr(condicao, "match", None), "text", None)
            if texto:
                pesquisados.append(texto)

    assert "adesivo" in pesquisados
    assert "laminacao" in pesquisados


def test_palavras_da_pergunta_continuam_sendo_pesquisadas(cliente_qdrant):
    """A tradução SOMA. Se ela substituísse a pergunta, um termo traduzido
    errado apagaria o critério real do usuário."""
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=["adesivo"]):
        retrieve_products_context("tem cola pra cortiça?")

    pesquisados = []
    for chamada in cliente_qdrant.scroll.call_args_list:
        filtro = chamada.kwargs.get("scroll_filter")
        if filtro is None:
            continue
        for condicao in filtro.must or []:
            texto = getattr(getattr(condicao, "match", None), "text", None)
            if texto:
                pesquisados.append(texto)

    assert "cortiça" in pesquisados
    assert "adesivo" in pesquisados


def test_segunda_busca_vetorial_usa_a_consulta_traduzida(cliente_qdrant):
    """O embedding da pergunta leiga é o sinal mais fraco do motor; a pergunta
    traduzida costuma ser uma consulta melhor. Ela soma candidatos.

    A asserção olha o texto da SEGUNDA chamada especificamente. A versão
    anterior só checava que a pergunta aparecia em alguma das chamadas — e
    passava mesmo quando a segunda busca SUBSTITUÍA a pergunta pelos termos,
    porque a primeira chamada já satisfazia a condição sozinha.
    """
    embeddings_pedidos = []

    def _embedding(texto, _modelo):
        embeddings_pedidos.append(texto)
        return [0.1, 0.2]

    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", side_effect=_embedding), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=["adesivo"]):
        retrieve_products_context("tem cola pra espuma?")

    assert cliente_qdrant.search.call_count == 2
    assert len(embeddings_pedidos) == 2
    consulta_original, consulta_traduzida = embeddings_pedidos
    assert consulta_original == "tem cola pra espuma?"
    assert "adesivo" in consulta_traduzida
    assert "tem cola pra espuma?" in consulta_traduzida, (
        "a consulta traduzida tem que SOMAR à pergunta, não substituí-la"
    )


def test_resultado_repetido_entre_as_duas_buscas_nao_duplica(cliente_qdrant):
    """As duas consultas alcançam trechos em comum por construção — o mesmo
    chunk duas vezes só ocuparia vaga de contexto."""
    cliente_qdrant.search.side_effect = [
        [_hit("Boletim FLEXX AG 2066.pdf", 0)],
        [_hit("Boletim FLEXX AG 2066.pdf", 0), _hit("Boletim FLEXX AG 2070.pdf", 1)],
    ]
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=["adesivo"]):
        docs = retrieve_products_context("tem cola pra espuma?")

    chaves = [(d["filename"], d["chunk_index"]) for d in docs]
    assert len(chaves) == len(set(chaves))
    assert ("Boletim FLEXX AG 2070.pdf", 1) in chaves


# --- hipótese não expulsa evidência real ------------------------------------

def test_hit_so_de_termo_traduzido_nao_expulsa_o_resultado_da_pergunta(cliente_qdrant):
    """REGRESSÃO REAL (achada na revisão de 18/09): era o único caminho em que
    a tradução podia deixar a recuperação PIOR do que antes de existir.

    `keyword_hits` entrava inteiro na lista de prioritários. Bastavam seis
    trechos alcançados só por termo traduzido — uma HIPÓTESE de vocabulário
    produzida por um modelo — para zerar as vagas e descartar todos os trechos
    que a pergunta real encontrou pelo vetor.
    """
    ruido = [
        _hit(f"Ruido {i}.pdf", i, "adesivo para laminação industrial")
        for i in range(8)
    ]
    cliente_qdrant.scroll.return_value = (ruido, None)
    cliente_qdrant.search.return_value = [
        _hit("Boletim FLEXX AG 2066.pdf", 0, "produção de rolhas de cortiça aglomerada")
    ]

    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio",
               return_value=["adesivo", "laminação"]):
        docs = retrieve_products_context("tem cola pra rolha de cortiça?")

    nomes = [d["filename"] for d in docs]
    assert "Boletim FLEXX AG 2066.pdf" in nomes, (
        "o documento achado pela pergunta original foi expulso por trechos de hipótese"
    )


def test_hit_de_palavra_do_usuario_continua_prioritario(cliente_qdrant):
    """A correção acima não pode rebaixar o caminho por palavra-chave que já
    existia: um trecho que casa com a palavra DO VENDEDOR continua na frente
    do semântico, com ou sem tradução ativa."""
    cliente_qdrant.scroll.return_value = (
        [_hit("Boletim FLEXX AG 2066.pdf", 0, "rolhas de cortiça aglomerada e adesivo")],
        None,
    )
    cliente_qdrant.search.return_value = [
        _hit(f"Semantico {i}.pdf", i, "outro assunto") for i in range(8)
    ]

    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=["adesivo"]):
        docs = retrieve_products_context("tem cola pra rolha de cortiça?")

    assert docs[0]["filename"] == "Boletim FLEXX AG 2066.pdf"


# --- fail-open --------------------------------------------------------------

def test_falha_da_traducao_nao_derruba_a_recuperacao(cliente_qdrant):
    """Expansão é ganho de recall, nunca dependência do caminho principal.

    O módulo já é fail-open internamente, mas o chamador não pode depender
    dessa promessa: um defeito dentro da tradução tem que degradar a busca para
    o comportamento anterior, não derrubar a consulta do vendedor.
    """
    cliente_qdrant.search.return_value = [_hit("Boletim FLEXX AG 2066.pdf")]
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio",
               side_effect=RuntimeError("defeito dentro da tradução")):
        docs = retrieve_products_context("tem cola pra espuma?")

    assert [d["filename"] for d in docs] == ["Boletim FLEXX AG 2066.pdf"]
    assert cliente_qdrant.search.call_count == 1, "não deve tentar a segunda busca vetorial"


def test_falha_da_segunda_busca_vetorial_preserva_o_resultado_da_primeira(cliente_qdrant):
    """A busca com a consulta traduzida é complementar: se ela falhar, o
    resultado da consulta original tem que chegar inteiro ao agente."""
    cliente_qdrant.search.side_effect = [
        [_hit("Boletim FLEXX AG 2066.pdf")],
        Exception("timeout na segunda busca"),
    ]
    with patch("app.rag.engine._get_qdrant_client", return_value=cliente_qdrant), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine.expandir_termos_do_dominio", return_value=["adesivo"]):
        docs = retrieve_products_context("tem cola pra espuma?")

    assert [d["filename"] for d in docs] == ["Boletim FLEXX AG 2066.pdf"]


# --- o agente é avisado de que tradução não é evidência ---------------------

def test_contexto_avisa_que_termo_traduzido_nao_prova_equivalencia():
    """Sem este aviso a expansão viraria fonte nova de recomendação sem lastro:
    um boletim alcançado por "estofamento" seria apresentado como prova da
    aplicação "sofá" que o vendedor pediu."""
    docs = [{"filename": "Boletim FLEXX AG 2066.pdf", "content": "adesivo para laminação"}]
    with patch("app.rag.engine.termos_expandidos_em_cache", return_value=["adesivo"]):
        contexto = _montar_context_str("tem cola pra espuma?", docs)

    assert "TRADUÇÃO DA PERGUNTA" in contexto
    assert "'adesivo'" in contexto
    assert "NÃO É EVIDÊNCIA DE EQUIVALÊNCIA" in contexto


def test_contexto_sem_traducao_nao_ganha_bloco_nenhum():
    """Pergunta já técnica não paga o custo de contexto de um aviso vazio."""
    docs = [{"filename": "Boletim FLEXX AG 2066.pdf", "content": "adesivo"}]
    with patch("app.rag.engine.termos_expandidos_em_cache", return_value=[]):
        contexto = _montar_context_str("qual a viscosidade do adesivo?", docs)

    assert "TRADUÇÃO DA PERGUNTA" not in contexto
