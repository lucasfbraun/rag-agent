"""
Integração no agente das duas novas formas de perguntar sobre um produto:
por SEÇÃO do boletim ("quais as vantagens do AG 2032") e por ESPECIFICAÇÃO
NUMÉRICA ("quero um produto com hidroxila de 180").

O que estes testes protegem, e que os módulos isolados (test_doc_sections.py,
test_spec_search.py) não cobrem: a fiação dentro de `retrieve_products_context`
e `_montar_context_str` — quem entra primeiro no contexto, o que é injetado
junto e o que NÃO pode disparar.

Seam: as funções reais do engine com o cliente Qdrant e o embedding mockados.
"""
import json
from unittest.mock import MagicMock, patch

from app.config import COLLECTION_NAME
from app.rag.engine import (
    _extrair_palavras_chave,
    _montar_context_str,
    retrieve_products_context,
    run_pu_matcher_agent,
    stream_pu_matcher_agent,
)


def _fake_collections(names):
    collections = []
    for name in names:
        c = MagicMock()
        c.name = name
        collections.append(c)
    result = MagicMock()
    result.collections = collections
    return result


def _hit(filename, chunk_index, content):
    hit = MagicMock()
    hit.payload = {"filename": filename, "chunk_index": chunk_index, "content": content}
    return hit


CHUNK_VANTAGENS = (
    "CARACTERÍSTICAS E VANTAGENS Reduz o consumo de máquina, boa fluidez e ótima "
    "relação de trabalho."
)
CHUNK_ESPECIFICACAO = "ESPECIFICAÇÕES TÉCNICAS Viscosidade, 25°C cPs 450 a 750"


# --- pedido de seção --------------------------------------------------------

def test_trecho_com_a_secao_pedida_vem_primeiro_no_contexto():
    """Achado que motivou o recurso: perguntar "vantagens do AG 2032" trazia o
    chunk da tabela de especificação do MESMO produto — produto certo, seção
    errada — e o agente respondia com o que tinha na mão."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.return_value = (
        [
            _hit("Boletim FLEXX AG 2032.pdf", 1, CHUNK_ESPECIFICACAO),
            _hit("Boletim FLEXX AG 2032.pdf", 2, CHUNK_VANTAGENS),
        ],
        None,
    )
    fake_client.search.return_value = [_hit("FISPQ qualquer.pdf", 0, "texto padrão de segurança")]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        resultado = retrieve_products_context("quais as vantagens do AG 2032", top_k=6)

    assert resultado[0]["chunk_index"] == 2


def test_pergunta_sem_secao_nao_faz_scroll_extra_de_secao():
    """A busca por seção é um scroll a mais no Qdrant: não pode rodar em
    pergunta que não é dela."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.return_value = ([], None)
    fake_client.search.return_value = []

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]), \
         patch("app.rag.engine._recuperar_por_secao") as por_secao:
        retrieve_products_context("me traga o boletim do AG 2032", top_k=6)

    por_secao.assert_not_called()


def test_falha_no_scroll_de_secao_nao_derruba_a_consulta():
    """Mesma disciplina da busca exata por código e por palavra-chave: um
    recurso de priorização que falha degrada, não quebra."""
    fake_client = MagicMock()
    fake_client.get_collections.return_value = _fake_collections([COLLECTION_NAME])
    fake_client.scroll.side_effect = Exception("índice de texto ausente")
    fake_client.search.return_value = [_hit("Boletim FLEXX AG 2032.pdf", 0, CHUNK_VANTAGENS)]

    with patch("app.rag.engine._get_qdrant_client", return_value=fake_client), \
         patch("app.rag.engine.get_embedding", return_value=[0.1, 0.2]):
        resultado = retrieve_products_context("quais as vantagens do AG 2032", top_k=6)

    assert resultado[0]["filename"] == "Boletim FLEXX AG 2032.pdf"


def test_nome_de_secao_nao_vira_palavra_chave_generica():
    """"vantagens" e "armazenamento" aparecem em quase todo boletim do acervo:
    como palavra-chave solta só enchiam o top-k de ruído. O pedido de seção
    tem caminho próprio, mais preciso."""
    assert _extrair_palavras_chave("quais as vantagens e o armazenamento do produto") == []
    # Um termo de conteúdo de verdade continua passando.
    assert "cortiça" in _extrair_palavras_chave("vantagens da cola para cortiça")


def test_instrucao_de_secao_entra_no_contexto():
    docs = [{"filename": "Boletim FLEXX AG 2032.pdf", "content": CHUNK_VANTAGENS}]
    contexto = _montar_context_str("quais as vantagens do AG 2032", docs)
    assert "SEÇÃO PEDIDA" in contexto
    assert "Características e vantagens" in contexto


# --- ficha estruturada ------------------------------------------------------

def test_tabela_lida_por_regra_acompanha_o_texto_bruto_no_contexto():
    """A extração de PDF embaralha as colunas; a leitura estruturada dá ao
    modelo o par propriedade→valor já resolvido, SEM tirar o texto original do
    contexto (ele precisa poder conferir a evidência)."""
    docs = [{"filename": "Boletim FLEXX AG 2032.pdf", "content": CHUNK_ESPECIFICACAO}]
    contexto = _montar_context_str("me traga o boletim do AG 2032", docs)
    assert "LEITURA ESTRUTURADA" in contexto
    assert "Viscosidade: 450 a 750 cPs" in contexto
    assert CHUNK_ESPECIFICACAO in contexto


# --- busca por especificação injetada no contexto ---------------------------

def _resultado_de_busca(total=1):
    return {
        "propriedade": "indice_hidroxila",
        "propriedade_titulo": "Índice de hidroxila",
        "criterio": "Índice de hidroxila ≈ 180 mgKOH/g (tolerância de ±5%)",
        "total": total,
        "produtos": [{
            "produto": "FLEXX POL 1180",
            "valores": "175 a 185",
            "unidade": "mgKOH/g",
            "documento": "Boletim FLEXX POL 1180.pdf",
            "tipo_documento": "Boletim Técnico",
            "trecho": "Índice de hidroxilas mgKOH/g 175,0 – 185,0",
        }] if total else [],
        "truncado": False,
        "faixa_no_acervo": {"minimo": 20.0, "maximo": 415.0, "unidade": "mgKOH/g"},
        "aviso": "Confirme no documento citado antes de fechar a proposta.",
    }


def test_pergunta_por_especificacao_traz_a_varredura_pronta_no_contexto():
    """Depender só do tool calling deixa a resposta errada de um jeito
    silencioso quando o modelo não chama a ferramenta: ele responde com os
    trechos semânticos, que falam de hidroxila com OUTRO valor."""
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacao", return_value=_resultado_de_busca()
    ) as busca:
        contexto = _montar_context_str("quero um produto com hidroxila de 180", [])

    busca.assert_called_once()
    assert busca.call_args.kwargs["propriedade"] == "indice_hidroxila"
    assert busca.call_args.kwargs["valor"] == 180.0
    assert "BUSCA POR ESPECIFICAÇÃO TÉCNICA" in contexto
    assert "FLEXX POL 1180" in contexto


def test_bloco_diz_que_os_produtos_listados_atendem_ao_criterio():
    """Achado validando ao vivo com gpt-4o-mini: para "hidroxila de 180" o
    agente abriu com "Nenhum produto do acervo atende" e, na linha seguinte,
    listou o FLEXX ADR 204 (158 a 178, dentro da tolerância). Contradição na
    cara do vendedor — o bloco precisa dizer explicitamente que quem está na
    lista atende, e que faixa que não cobre o número exato se apresenta como
    "atende dentro da tolerância", não como ausência de resultado."""
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacao", return_value=_resultado_de_busca()
    ):
        contexto = _montar_context_str("quero um produto com hidroxila de 180", [])

    assert "ATENDEM ao critério" in contexto
    assert "TOLERÂNCIA" in contexto


def test_sem_resultado_o_contexto_carrega_a_faixa_real_do_acervo():
    """"Não encontrei" seco não diz ao vendedor se ele errou o número ou se o
    acervo não tem aquilo."""
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacao", return_value=_resultado_de_busca(total=0)
    ):
        contexto = _montar_context_str("quero um produto com hidroxila de 180", [])

    assert "NENHUM produto" in contexto
    assert "20 a 415" in contexto


def test_pergunta_com_dois_limites_entrega_intersecao_pronta_ao_modelo():
    resultado = {
        "criterios": [
            {"propriedade": "densidade", "criterio": "Densidade abaixo de 32 kg/m³",
             "faixa_no_acervo": {"minimo": 18.0, "maximo": 40.0, "unidade": "kg/m³"}},
            {"propriedade": "tempo_pega", "criterio": "Tempo de pega livre abaixo de 220 s",
             "faixa_no_acervo": {"minimo": 120.0, "maximo": 300.0, "unidade": "s"}},
        ],
        "total": 1,
        "produtos": [{
            "produto": "FLEXX ESP 3",
            "requisitos": [
                {"propriedade": "densidade", "propriedade_titulo": "Densidade",
                 "valores": "29 a 31", "unidade": "kg/m³", "documento": "Boletim 3.pdf"},
                {"propriedade": "tempo_pega", "propriedade_titulo": "Tempo de pega livre",
                 "valores": "3 min a 3 min 30 s", "unidade": "", "documento": "Boletim 3.pdf"},
            ],
        }],
        "truncado": False,
        "aviso": "Todos os requisitos foram encontrados e atendidos.",
    }
    pergunta = (
        "preciso de um produto com densidade livre abaixo de 32 kg/m³ "
        "e tempo de pega livre abaixo de 220 segundos"
    )
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacoes", return_value=resultado,
    ) as busca:
        contexto = _montar_context_str(pergunta, [])

    busca.assert_called_once()
    assert "INTERSEÇÃO dos critérios" in contexto
    assert "atendem a TODOS os requisitos: 1" in contexto
    assert "FLEXX ESP 3" in contexto
    assert "Densidade 29 a 31 kg/m³" in contexto
    assert "Tempo de pega livre 3 min a 3 min 30 s" in contexto


def _resultado_composto_deterministico():
    return {
        "criterios": [
            {"propriedade": "densidade", "criterio": "Densidade abaixo de 32 kg/m³",
             "faixa_no_acervo": {"minimo": 18.0, "maximo": 40.0, "unidade": "kg/m³"}},
            {"propriedade": "tempo_pega", "criterio": "Tempo de pega livre abaixo de 220 s",
             "faixa_no_acervo": {"minimo": 120.0, "maximo": 300.0, "unidade": "s"}},
        ],
        "total": 1,
        "produtos": [{
            "produto": "FLEXX ESP 3",
            "requisitos": [
                {"propriedade": "densidade", "propriedade_titulo": "Densidade",
                 "valores": "29 a 31", "unidade": "kg/m³", "documento": "Boletim 3.pdf"},
                {"propriedade": "tempo_pega", "propriedade_titulo": "Tempo de pega livre",
                 "valores": "3 min a 3 min 30 s", "unidade": "", "documento": "Boletim 3.pdf"},
            ],
        }],
        "truncado": False,
        "aviso": "Todos os requisitos foram encontrados e atendidos.",
    }


def _pergunta_composta():
    return (
        "preciso de um produto com densidade livre abaixo de 32 kg/m³ "
        "e tempo de pega livre abaixo de 220 segundos"
    )


def test_rota_sincrona_nao_delega_resultado_composto_ao_llm():
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacoes",
        return_value=_resultado_composto_deterministico(),
    ), patch("app.rag.engine._preparar_contexto") as preparar, \
         patch("app.rag.engine.litellm.completion") as completion:
        resposta = run_pu_matcher_agent(_pergunta_composta())

    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "FLEXX ESP 3" in resposta["answer"]
    assert resposta["sources"] == ["Boletim 3.pdf"]


def test_rota_streaming_nao_delega_resultado_composto_ao_llm():
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacoes",
        return_value=_resultado_composto_deterministico(),
    ), patch("app.rag.engine._preparar_contexto") as preparar, \
         patch("app.rag.engine.litellm.completion") as completion:
        eventos = [json.loads(linha) for linha in stream_pu_matcher_agent(_pergunta_composta())]

    preparar.assert_not_called()
    completion.assert_not_called()
    assert eventos[0]["model_used"] == "catalogo-estruturado"
    assert "FLEXX ESP 3" in eventos[1]["content"]
    assert eventos[-1] == {"type": "done"}


def _resultado_aplicacao_e_especificacao():
    return {
        "aplicacao": {"termos_buscados": ["solado"]},
        "criterios": [{
            "propriedade": "densidade_imersao",
            "criterio": "Densidade por imersão a partir de 200 kg/m³",
            "faixa_no_acervo": {"minimo": 150.0, "maximo": 290.0, "unidade": "kg/m³"},
        }],
        "total": 1,
        "produtos": [{
            "produto": "FLEXX SL ECO 2539",
            "aplicacao": {
                "documento": "Boletim FLEXX SL ECO 2539.pdf",
                "termos_encontrados": ["solado"],
            },
            "requisitos": [{
                "propriedade": "densidade_imersao",
                "propriedade_titulo": "Densidade por imersão",
                "valores": "270 a 290",
                "unidade": "kg/m³",
                "documento": "Boletim FLEXX SL ECO 2539.pdf",
            }],
        }],
        "truncado": False,
        "aviso": "Aplicação e especificação comprovadas no mesmo Boletim Técnico.",
    }


def _pergunta_aplicacao_e_especificacao():
    return (
        "Preciso de um material para fazer solado de tênis, ele precisa ter "
        "no mínimo 200Kg/m³ de densidade por imersão"
    )


def test_rota_sincrona_cruza_aplicacao_e_especificacao_sem_llm():
    with patch(
        "app.rag.engine.buscar_produtos_por_aplicacao_e_especificacoes",
        return_value=_resultado_aplicacao_e_especificacao(),
    ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, \
         patch("app.rag.engine.litellm.completion") as completion:
        resposta = run_pu_matcher_agent(_pergunta_aplicacao_e_especificacao())

    busca.assert_called_once()
    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "solado de tênis" in resposta["answer"].lower()
    assert "FLEXX SL ECO 2539" in resposta["answer"]
    assert "270 a 290 kg/m³" in resposta["answer"]
    assert resposta["sources"] == ["Boletim FLEXX SL ECO 2539.pdf"]


def test_rota_streaming_cruza_aplicacao_e_especificacao_sem_llm():
    with patch(
        "app.rag.engine.buscar_produtos_por_aplicacao_e_especificacoes",
        return_value=_resultado_aplicacao_e_especificacao(),
    ), patch("app.rag.engine._preparar_contexto") as preparar, \
         patch("app.rag.engine.litellm.completion") as completion:
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(_pergunta_aplicacao_e_especificacao())
        ]

    preparar.assert_not_called()
    completion.assert_not_called()
    assert eventos[0]["model_used"] == "catalogo-estruturado"
    assert "FLEXX SL ECO 2539" in eventos[1]["content"]
    assert eventos[-1] == {"type": "done"}


def test_pedido_do_dado_de_um_produto_nomeado_nao_dispara_varredura():
    """"Qual a hidroxila do AG 2032" é consulta de ficha, não busca no acervo —
    e a varredura completa é cara demais para rodar à toa."""
    with patch("app.rag.engine.buscar_produtos_por_especificacao") as busca:
        _montar_context_str("qual a hidroxila do AG 2032", [])
    busca.assert_not_called()


def test_pergunta_comum_nao_dispara_varredura():
    with patch("app.rag.engine.buscar_produtos_por_especificacao") as busca:
        _montar_context_str("quero um produto para colchão", [])
    busca.assert_not_called()


def test_catalogo_indisponivel_na_varredura_nao_derruba_a_resposta():
    """O RAG normal ainda pode responder; perder a busca por especificação
    degrada a resposta, não deve apagá-la."""
    from app.rag.exceptions import RetrievalIndisponivelError

    docs = [{"filename": "Boletim FLEXX AG 2032.pdf", "content": CHUNK_ESPECIFICACAO}]
    with patch(
        "app.rag.engine.buscar_produtos_por_especificacao",
        side_effect=RetrievalIndisponivelError("fora do ar"),
    ):
        contexto = _montar_context_str("quero um produto com hidroxila de 180", docs)

    assert CHUNK_ESPECIFICACAO in contexto
    assert "BUSCA POR ESPECIFICAÇÃO TÉCNICA" not in contexto
