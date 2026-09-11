"""Regressões do incidente em que uma correção explícita foi ignorada.

Seam: interfaces públicas síncrona e streaming do agente. O provedor LLM e
a recuperação são fronteiras externas simuladas; montagem de histórico,
validação e entrega da resposta continuam reais.
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.conversation_service import history_for_agent
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent
from app.rag.exceptions import RetrievalIndisponivelError


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
@pytest.mark.parametrize("usa_ferramenta", [False, True])
def test_stream_nao_vaza_resposta_insegura_em_nenhum_delta(mock_completion, _mock_retrieve, usa_ferramenta):
    initial = _completion(RESPOSTA_INSEGURA)
    if usa_ferramenta:
        initial.choices[0].message.tool_calls = [SimpleNamespace(
            id="consulta_1", function=SimpleNamespace(
                name="consultar_estatisticas_catalogo", arguments="{}",
            ),
        )]
    mock_completion.side_effect = [initial, _stream(RESPOSTA_INSEGURA)]

    with patch("app.rag.engine.execute_mcp_tool", return_value='{"produtos_catalogados": 1}'):
        events = [json.loads(line) for line in stream_pu_matcher_agent(query=REJEICAO)]
    answer = "".join(event.get("content", "") for event in events if event["type"] == "delta")

    assert "Produto Recomendado: FLEXX ADT" not in answer
    assert "foi descartada" in answer
    assert mock_completion.call_count == (2 if usa_ferramenta else 1)


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
    mock_completion.return_value = _completion("Vou procurar outra família.")

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

    result = run_pu_matcher_agent(query="qual o status comercial do FLEXX TH 16010?")

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


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_listagem_de_elastomeros_remove_adts_e_cats_inseridos_pelo_llm(
    mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "por_aplicacao_ou_tipo": {
            "total": 2,
            "produtos": ["FLEXX TH T160DE1", "FLEXX TH T193AH4"],
            "truncado": False,
        }
    })
    mock_completion.return_value = _completion(
        "Produtos encontrados:\n"
        "1. FLEXX ADT 432\n"
        "2. FLEXX CAT 100\n"
        "3. FLEXX TH T160DE1"
    )

    result = run_pu_matcher_agent(query="liste os produtos elastômeros")

    assert "FLEXX ADT" not in result["answer"]
    assert "FLEXX CAT" not in result["answer"]
    assert "FLEXX TH T160DE1" in result["answer"]
    assert "2 produtos" in result["answer"]
    mock_completion.assert_not_called()
    _mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_listagem_elastomeros_tambem_e_deterministico(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "por_aplicacao_ou_tipo": {
            "total": 1,
            "produtos": ["FLEXX TH T160DE1"],
            "truncado": False,
        }
    })

    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(query="liste os produtos elastômeros")
    ]
    answer = "".join(event.get("content", "") for event in events if event["type"] == "delta")

    assert "FLEXX TH T160DE1" in answer
    assert "FLEXX ADT" not in answer
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.buscar_evidencias_de_aplicacao_explicita", return_value=[])
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_assento_de_onibus_sem_evidencia_nao_recomenda_categoria_proxima(
    mock_completion, mock_retrieve, _mock_busca
):
    mock_completion.return_value = _completion(
        "Produtos relacionados: FLEXX HR 3070 para colchão e FLEXX CL 2001 automotivo."
    )

    result = run_pu_matcher_agent(query="em nenhum boletim tem nada para assento de ônibus?")

    assert "não encontrei" in result["answer"].lower()
    assert "assento" in result["answer"].lower()
    assert "colchão" not in result["answer"].lower()
    assert "automotivo" not in result["answer"].lower()
    assert result["sources"] == []
    assert result["model_used"] == "catalogo-estruturado"
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.buscar_evidencias_de_aplicacao_explicita")
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_assento_de_onibus_so_retorna_produto_com_boletim_que_comprova_aplicacao(
    mock_completion, mock_retrieve, mock_busca
):
    mock_busca.return_value = [{
        "produto": "FLEXX BUS 100",
        "documentos": ["Boletim FLEXX BUS 100.pdf"],
        "termos_encontrados": ["assento de ônibus"],
    }]

    result = run_pu_matcher_agent(query="quero um produto para assento de onibus")

    assert "FLEXX BUS 100" in result["answer"]
    assert "Boletim FLEXX BUS 100.pdf" in result["answer"]
    assert result["sources"] == ["Boletim FLEXX BUS 100.pdf"]
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.buscar_evidencias_de_aplicacao_explicita", return_value=[])
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_stream_assento_de_onibus_aplica_mesma_validacao(
    mock_completion, mock_retrieve, _mock_busca
):
    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(query="produto para assento de ônibus")
    ]
    answer = "".join(event.get("content", "") for event in events if event["type"] == "delta")

    assert "não encontrei" in answer.lower()
    assert "colchão" not in answer.lower()
    assert events[0]["model_used"] == "catalogo-estruturado"
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.buscar_evidencias_de_aplicacao_explicita", return_value=[])
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.litellm.completion")
def test_outra_aplicacao_tambem_usa_expressao_literal_sem_expansao_do_modelo(
    mock_completion, mock_retrieve, mock_busca
):
    result = run_pu_matcher_agent(query="preciso de um produto para rolha de cortiça")

    mock_busca.assert_called_once_with(["rolha de cortiça"])
    assert "rolha de cortiça" in result["answer"].lower()
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch(
    "app.rag.engine.buscar_evidencias_de_aplicacao_explicita",
    side_effect=RetrievalIndisponivelError("qdrant fora do ar"),
)
@patch("app.rag.engine.litellm.completion")
def test_stream_aplicacao_nao_responde_sem_catalogo(mock_completion, _mock_busca):
    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(query="produto para assento de ônibus")
    ]

    assert [event["type"] for event in events] == ["error", "done"]
    assert "indisponível" in events[0]["message"].lower()
    mock_completion.assert_not_called()
