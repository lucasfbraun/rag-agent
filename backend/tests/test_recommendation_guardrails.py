"""Regressões do incidente em que uma correção explícita foi ignorada.

Seam: interfaces públicas síncrona e streaming do agente. O provedor LLM e
a recuperação são fronteiras externas simuladas; montagem de histórico,
validação e entrega da resposta continuam reais.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.conversation_service import history_for_agent
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent


REJEICAO = "esses adts não são elastômeros"
RESPOSTA_INSEGURA = """🎯 RECOMENDAÇÃO
Produto Recomendado: FLEXX ADT 432
Família Química: Aditivo para elastômeros.
Status: Produto ativo em linha.
"""


def _completion(answer):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.tool_calls = None
    response.choices[0].message.content = answer
    return response


def _stream(answer):
    for part in (answer[:30], answer[30:]):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = part
        yield chunk


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_run_bloqueia_recomendacao_de_familia_rejeitada(mock_completion, _mock_retrieve):
    mock_completion.return_value = _completion(RESPOSTA_INSEGURA)

    result = run_pu_matcher_agent(
        query=REJEICAO,
        history=[
            {"role": "user", "content": "quero elastômero para correia"},
            {"role": "assistant", "content": "Encontrei produtos FLEXX ADT."},
        ],
    )

    assert "Produto Recomendado: FLEXX ADT" not in result["answer"]
    assert "foi descartada" in result["answer"]
    assert "não encontrei evidência suficiente" in result["answer"].lower()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_stream_nao_vaza_resposta_insegura_em_nenhum_delta(mock_completion, _mock_retrieve):
    initial = MagicMock()
    initial.choices = [MagicMock()]
    initial.choices[0].message.tool_calls = None
    mock_completion.side_effect = [initial, _stream(RESPOSTA_INSEGURA)]

    events = [json.loads(line) for line in stream_pu_matcher_agent(query=REJEICAO)]
    answer = "".join(event.get("content", "") for event in events if event["type"] == "delta")

    assert "Produto Recomendado: FLEXX ADT" not in answer
    assert "foi descartada" in answer


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_resposta_sem_recomendacao_rejeitada_permanece_inalterada(mock_completion, _mock_retrieve):
    safe_answer = "Você está correto. ADT é aditivo, não elastômero; vou descartá-lo."
    mock_completion.return_value = _completion(safe_answer)

    result = run_pu_matcher_agent(query=REJEICAO)

    assert result["answer"] == safe_answer


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_followup_recupera_com_demanda_anterior_e_correcao_atual(mock_completion, mock_retrieve):
    mock_completion.return_value = _completion("Vou procurar outra família.")

    run_pu_matcher_agent(
        query=REJEICAO,
        history=[
            {"role": "user", "content": "quero elastômero para pneu industrial, peça mecânica e correia"},
            {"role": "assistant", "content": "Encontrei produtos FLEXX ADT."},
        ],
    )

    query_recuperacao = mock_retrieve.call_args.args[0]
    assert "pneu industrial" in query_recuperacao
    assert "adts" not in query_recuperacao


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_stream_followup_tambem_recupera_com_demanda_anterior(mock_completion, mock_retrieve):
    initial = MagicMock()
    initial.choices = [MagicMock()]
    initial.choices[0].message.tool_calls = None
    mock_completion.side_effect = [initial, _stream("Vou procurar outra família.")]

    list(stream_pu_matcher_agent(
        query=REJEICAO,
        history=[
            {"role": "user", "content": "quero elastômero para pneu industrial, peça mecânica e correia"},
            {"role": "assistant", "content": "Encontrei produtos FLEXX ADT."},
        ],
    ))

    query_recuperacao = mock_retrieve.call_args.args[0]
    assert "pneu industrial" in query_recuperacao
    assert "adts" not in query_recuperacao


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_rejeicao_permanece_ativa_em_turno_posterior(mock_completion, _mock_retrieve):
    mock_completion.return_value = _completion(RESPOSTA_INSEGURA)

    result = run_pu_matcher_agent(
        query="então qual você indica?",
        history=[
            {"role": "user", "content": "quero elastômero para correia"},
            {"role": "assistant", "content": "Encontrei produtos FLEXX ADT."},
            {"role": "user", "content": REJEICAO},
            {"role": "assistant", "content": "Você está correto, vou descartar ADT."},
        ],
    )

    assert "Produto Recomendado: FLEXX ADT" not in result["answer"]
    assert "família ADT foi descartada" in result["answer"]


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_status_comercial_sem_erp_real_e_substituido(mock_completion, _mock_retrieve):
    mock_completion.return_value = _completion(
        "Produto Recomendado: FLEXX TH T160DE1\nStatus: Produto ativo em linha."
    )

    result = run_pu_matcher_agent(query="produto para correia")

    assert "Produto ativo em linha" not in result["answer"]
    assert "Status comercial não verificado" in result["answer"]


def test_historico_persistido_preserva_correcoes_anteriores_para_guardrail():
    messages = [
        SimpleNamespace(role="user" if i % 2 == 0 else "assistant", content=f"mensagem {i}")
        for i in range(12)
    ]
    conversation = SimpleNamespace(messages=messages)

    history = history_for_agent(conversation)

    assert len(history) == 12
    assert history[0]["content"] == "mensagem 0"
