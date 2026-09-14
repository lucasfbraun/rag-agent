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
