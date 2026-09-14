"""Busca reversa completa para perguntas "X aparece em quais produtos?"."""
import json
from unittest.mock import patch

from app.rag.engine import (
    _extrair_familias_destino,
    run_pu_matcher_agent,
    stream_pu_matcher_agent,
)


def _resultados():
    return [
        {
            "produto": "FLEXX SIST 100",
            "documentos": ["Boletim FLEXX SIST 100.pdf"],
            "mencoes": [{
                "documento": "Boletim FLEXX SIST 100.pdf",
                "trecho": "Parte A: FLEXX AG 2032. Misturar conforme a relação indicada.",
            }],
        },
        {
            "produto": "FLEXX SIST 200",
            "documentos": ["Boletim FLEXX SIST 200.pdf"],
            "mencoes": [{
                "documento": "Boletim FLEXX SIST 200.pdf",
                "trecho": "O componente AG 2032 integra o sistema.",
            }],
        },
    ]


def test_rota_sincrona_lista_todos_sem_llm_nem_top_k():
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=_resultados()
    ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        resposta = run_pu_matcher_agent(
            "o produto AG 2032 é utilizado em algum produto?"
        )

    busca.assert_called_once_with("ag 2032", incluir_sensivel=False)
    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "Encontrei 2 produtos" in resposta["answer"]
    assert "FLEXX SIST 100" in resposta["answer"]
    assert "FLEXX SIST 200" in resposta["answer"]
    assert resposta["sources"] == [
        "Boletim FLEXX SIST 100.pdf",
        "Boletim FLEXX SIST 200.pdf",
    ]


def test_rota_streaming_entrega_a_mesma_lista_completa():
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=_resultados()
    ), patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(
                "quais produtos utilizam o AG 2032?"
            )
        ]

    preparar.assert_not_called()
    completion.assert_not_called()
    assert eventos[0]["model_used"] == "catalogo-estruturado"
    assert "FLEXX SIST 100" in eventos[1]["content"]
    assert "FLEXX SIST 200" in eventos[1]["content"]
    assert eventos[-1] == {"type": "done"}


def test_resultado_vazio_so_e_afirmado_apos_varredura_completa():
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=[]
    ), patch("app.rag.engine.litellm.completion") as completion:
        resposta = run_pu_matcher_agent(
            "em quais outros produtos o AG 2032 é usado?"
        )

    completion.assert_not_called()
    assert "Não encontrei outro produto" in resposta["answer"]
    assert "sem limite de top-k" in resposta["answer"]


def test_produto_usado_em_familia_vira_busca_reversa_filtrada_sem_llm():
    resultados = [{
        "produto": "FLEXX BT 100",
        "documentos": ["Boletim FLEXX BT 100.pdf"],
        "mencoes": [{
            "documento": "Boletim FLEXX BT 100.pdf",
            "trecho": "Formulação recomendada com FLEXX ISO 13100.",
        }],
    }]

    assert _extrair_familias_destino(
        "O ISO 13100 é utilizado em algum FLEXX BT?"
    ) == ["bt"]
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=resultados
    ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        resposta = run_pu_matcher_agent(
            "O ISO 13100 é utilizado em algum FLEXX BT?"
        )

    busca.assert_called_once_with(
        "iso 13100", incluir_sensivel=False, familias_destino=["bt"]
    )
    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "família FLEXX BT" in resposta["answer"]
    assert "FLEXX BT 100" in resposta["answer"]


def test_familia_destino_e_generica_e_nao_confunde_produto_completo():
    consultas = {
        "Quais FLEXX BT usam o ISO 13100?": ["bt"],
        "O CAT 1 aparece em algum FLEXX TH?": ["th"],
        "O AG 2032 faz parte de FLEXX SIST ou FLEXX ESP?": ["sist", "esp"],
    }

    for pergunta, esperado in consultas.items():
        assert _extrair_familias_destino(pergunta) == esperado

    assert _extrair_familias_destino(
        "Quais são as aplicações do FLEXX ISO 13100?"
    ) == []

    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=[]
    ) as busca, patch("app.rag.engine.litellm.completion") as completion:
        run_pu_matcher_agent("Quais FLEXX BT usam o ISO 13100?")

    busca.assert_called_once_with(
        "iso 13100", incluir_sensivel=False, familias_destino=["bt"]
    )
    completion.assert_not_called()


def test_busca_reversa_por_familia_aceita_outros_produtos_e_familias():
    casos = [
        (
            "O CAT 1 é utilizado em algum FLEXX TH?",
            "cat 1",
            ["th"],
        ),
        (
            "O AG 2032 aparece em algum FLEXX SIST ou FLEXX ESP?",
            "ag 2032",
            ["sist", "esp"],
        ),
        (
            "Quais FLEXX ESP usam o RG 2464?",
            "rg 2464",
            ["esp"],
        ),
    ]

    for pergunta, codigo, familias in casos:
        with patch(
            "app.rag.engine.buscar_produtos_que_mencionam", return_value=[]
        ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, patch(
            "app.rag.engine.litellm.completion"
        ) as completion:
            resposta = run_pu_matcher_agent(pergunta)

        busca.assert_called_once_with(
            codigo, incluir_sensivel=False, familias_destino=familias
        )
        preparar.assert_not_called()
        completion.assert_not_called()
        assert resposta["model_used"] == "catalogo-estruturado"


def test_mais_de_dois_codigos_sao_intersectados_na_familia_de_destino():
    resultados = [{
        "produto": "FLEXX SIST 100",
        "documentos": ["Boletim FLEXX SIST 100.pdf"],
        "mencoes": [
            {
                "codigo": "ag 2032",
                "documento": "Boletim FLEXX SIST 100.pdf",
                "trecho": "Formulação com AG 2032.",
            },
            {
                "codigo": "cat 136",
                "documento": "Boletim FLEXX SIST 100.pdf",
                "trecho": "Utilizar CAT 136.",
            },
            {
                "codigo": "rg 2464",
                "documento": "Boletim FLEXX SIST 100.pdf",
                "trecho": "Combinar com RG 2464.",
            },
        ],
    }]

    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=resultados
    ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        resposta = run_pu_matcher_agent(
            "O AG 2032, CAT 136 e RG 2464 são utilizados em algum FLEXX SIST?"
        )

    busca.assert_called_once_with(
        ["ag 2032", "cat 136", "rg 2464"],
        incluir_sensivel=False,
        familias_destino=["sist"],
    )
    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "todos os códigos" in resposta["answer"]
    assert "FLEXX SIST 100" in resposta["answer"]
    assert "AG 2032:" in resposta["answer"]
    assert "CAT 136:" in resposta["answer"]
    assert "RG 2464:" in resposta["answer"]


def test_mais_de_dois_codigos_sao_intersectados_sem_familia_de_destino():
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=[]
    ) as busca, patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        resposta = run_pu_matcher_agent(
            "Em quais produtos AG 2032, CAT 136 e RG 2464 são utilizados?"
        )

    busca.assert_called_once_with(
        ["ag 2032", "cat 136", "rg 2464"], incluir_sensivel=False
    )
    preparar.assert_not_called()
    completion.assert_not_called()
    assert resposta["model_used"] == "catalogo-estruturado"
    assert "todos os códigos" in resposta["answer"]


def test_produto_usado_em_familia_funciona_tambem_no_streaming():
    with patch(
        "app.rag.engine.buscar_produtos_que_mencionam", return_value=[]
    ), patch("app.rag.engine._preparar_contexto") as preparar, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        eventos = [
            json.loads(linha)
            for linha in stream_pu_matcher_agent(
                "O ISO 13100 é utilizado em algum FLEXX BT?"
            )
        ]

    preparar.assert_not_called()
    completion.assert_not_called()
    assert eventos[0]["model_used"] == "catalogo-estruturado"
    assert "família FLEXX BT" in eventos[1]["content"]
    assert eventos[-1] == {"type": "done"}
