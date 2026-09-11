"""Busca reversa completa para perguntas "X aparece em quais produtos?"."""
import json
from unittest.mock import patch

from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent


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
