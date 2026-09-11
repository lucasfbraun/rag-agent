"""Regressões para perguntas claramente fora do escopo do PU Matcher."""

import json
from unittest.mock import patch

import pytest

from app.rag.engine import (
    _responder_fora_do_escopo,
    run_pu_matcher_agent,
    stream_pu_matcher_agent,
)


PERGUNTA_FORA_DO_ESCOPO = "voce pode me passar como fazer um bolo de cenoura?"


def test_pergunta_culinaria_nao_consulta_catalogo_nem_modelo():
    with patch("app.rag.engine._preparar_contexto") as preparar_contexto, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        resultado = run_pu_matcher_agent(PERGUNTA_FORA_DO_ESCOPO)

    preparar_contexto.assert_not_called()
    completion.assert_not_called()
    assert resultado["sources"] == []
    assert resultado["model_used"] == "escopo-deterministico"
    assert "fora do escopo do PU Matcher" in resultado["answer"]
    assert "produtos" in resultado["answer"].lower()


def test_stream_de_pergunta_culinaria_nao_consulta_catalogo_nem_modelo():
    with patch("app.rag.engine._preparar_contexto") as preparar_contexto, patch(
        "app.rag.engine.litellm.completion"
    ) as completion:
        eventos = [json.loads(linha) for linha in stream_pu_matcher_agent(
            PERGUNTA_FORA_DO_ESCOPO
        )]

    preparar_contexto.assert_not_called()
    completion.assert_not_called()
    assert eventos[0] == {
        "type": "meta",
        "sources": [],
        "model_used": "escopo-deterministico",
    }
    assert "fora do escopo do PU Matcher" in eventos[1]["content"]
    assert eventos[-1] == {"type": "done"}


@pytest.mark.parametrize(
    "pergunta",
    [
        "preciso de uma cola para rolha de cortiça",
        "quero um produto para assento de ônibus",
        "como faço para corrigir uma resposta do agente?",
        "como fazer um molde de bolo com poliuretano?",
    ],
)
def test_filtro_nao_bloqueia_consultas_legitimas(pergunta):
    assert _responder_fora_do_escopo(pergunta) is None
