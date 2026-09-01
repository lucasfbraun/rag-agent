"""
Ticket 5 do plano de correção (AUD-006 + achados relacionados): o loop de
tool calling em `run_pu_matcher_agent()` fazia `messages.append(choice.message)`
DENTRO do `for tool_call in choice.message.tool_calls`, então com 2+ tool
calls na mesma resposta a mesma mensagem `assistant` era anexada uma vez por
tool call, intercalada com as respostas — sequência inválida pro protocolo
de tool calling (deveria ser 1 mensagem `assistant` com todas as tool_calls,
seguida de N mensagens `tool`, uma por tool_call).

Corrigido junto (mesmo seam, achados da mesma área que o ticket 5 já linkava):
- `json.loads(tool_call.function.arguments)` sem try/except derrubava a
  request inteira com JSON inválido vindo do LLM; agora degrada com uma
  mensagem de erro na resposta da tool, sem quebrar a conversa.
- `execute_mcp_tool()` devolvia `str(dict)` (repr Python — aspas simples,
  `True`/`None`) em vez de JSON válido no conteúdo da mensagem `tool`.

Seam: `run_pu_matcher_agent()` com `litellm.completion` e
`retrieve_products_context` mockados — não sobe LLM nem Qdrant de verdade,
mas exercita a montagem real de `messages`.

Também cobre `stream_pu_matcher_agent()`: até esta sessão o streaming não
suportava tool calling (só o RAG rodava ali) — gap real, já que o frontend
só usa o endpoint de streaming, então nenhuma ferramenta MCP (nem a
`consultar_estatisticas_catalogo` nova) era alcançável de verdade pela tela
que o usuário usa. Streaming resolve a tool call numa 1ª chamada SEM stream,
só a resposta final é streamada — não dá pra streamar uma resposta que
ainda depende de decidir chamar ferramenta.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent
from app.mcp.pu_mcp_server import execute_mcp_tool


def _tool_call(call_id, name, arguments_json):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = arguments_json
    return tc


def _completion_with_tool_calls(tool_calls):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = tool_calls
    resp.choices[0].message.content = None
    return resp


def _final_completion(answer_text):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.tool_calls = None
    resp.choices[0].message.content = answer_text
    return resp


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_duas_tool_calls_geram_uma_assistant_seguida_de_duas_tool(
    mock_completion, mock_execute, mock_retrieve
):
    tool_calls = [
        _tool_call("call_1", "consultar_catalogo_erp", '{"termo_busca": "FLEXX AG"}'),
        _tool_call("call_2", "consultar_normas_homologadas", '{"norma_requerida": "UL94"}'),
    ]
    first_response = _completion_with_tool_calls(tool_calls)
    assistant_msg_with_tools = first_response.choices[0].message
    mock_completion.side_effect = [
        first_response,
        _final_completion("resposta final"),
    ]
    mock_execute.side_effect = ["resultado erp", "resultado normas"]

    result = run_pu_matcher_agent(query="teste")

    assert result["answer"] == "resposta final"
    final_call_messages = mock_completion.call_args_list[1].kwargs["messages"]

    # As últimas 3 mensagens devem ser: assistant (com as 2 tool_calls), tool, tool.
    last_three = final_call_messages[-3:]
    assert last_three[0] == assistant_msg_with_tools
    assert last_three[1]["role"] == "tool"
    assert last_three[1]["tool_call_id"] == "call_1"
    assert last_three[2]["role"] == "tool"
    assert last_three[2]["tool_call_id"] == "call_2"

    # A mensagem assistant não pode aparecer mais de uma vez (bug antigo:
    # aparecia 1x por tool_call, ou seja 2x aqui).
    assert final_call_messages.count(assistant_msg_with_tools) == 1


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_argumentos_json_invalidos_nao_derruba_a_request(
    mock_completion, mock_execute, mock_retrieve
):
    tool_calls = [_tool_call("call_1", "consultar_catalogo_erp", "{isto nao e json valido")]
    mock_completion.side_effect = [
        _completion_with_tool_calls(tool_calls),
        _final_completion("resposta final apesar do erro"),
    ]

    result = run_pu_matcher_agent(query="teste")

    assert result["answer"] == "resposta final apesar do erro"
    mock_execute.assert_not_called()  # não chega a executar a tool com args quebrados

    final_call_messages = mock_completion.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in final_call_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    # Conteúdo precisa ser JSON válido (mesma disciplina do achado abaixo).
    json.loads(tool_messages[0]["content"])


# --- execute_mcp_tool devolve JSON válido, não repr Python ------------------

def test_execute_mcp_tool_devolve_json_valido_nao_repr_python():
    resultado = execute_mcp_tool("consultar_catalogo_erp", {"termo_busca": "x"}, ver_custos=True)
    parsed = json.loads(resultado)  # levanta se ainda for repr Python (aspas simples etc.)
    assert parsed["custo_industrial_kg"] == "R$ 18,40"


def test_execute_mcp_tool_normas_tambem_devolve_json_valido():
    resultado = execute_mcp_tool("consultar_normas_homologadas", {"norma_requerida": "UL94"})
    parsed = json.loads(resultado)
    assert parsed["norma_pesquisada"] == "UL94"


# --- stream_pu_matcher_agent também resolve tool calling ---------------------

def _stream_delta_chunks(texts):
    for t in texts:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = t
        yield chunk


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_resolve_tool_call_antes_de_streamar_resposta_final(
    mock_completion, mock_execute, mock_retrieve
):
    tool_calls = [_tool_call("call_1", "consultar_estatisticas_catalogo", "{}")]
    mock_completion.side_effect = [
        _completion_with_tool_calls(tool_calls),
        _stream_delta_chunks(["temos ", "850 produtos"]),
    ]
    mock_execute.return_value = json.dumps({"produtos_catalogados": 850, "documentos_indexados": 7797})

    eventos = [json.loads(linha) for linha in stream_pu_matcher_agent(query="quantos produtos temos catalogados")]

    mock_execute.assert_called_once_with("consultar_estatisticas_catalogo", {}, ver_custos=False, ver_laudo_completo=False)
    deltas = [e["content"] for e in eventos if e["type"] == "delta"]
    assert deltas == ["temos ", "850 produtos"]
    assert eventos[-1] == {"type": "done"}

    # 2ª chamada (a que gera o texto streamado) precisa incluir a resposta da tool.
    segunda_chamada_messages = mock_completion.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in segunda_chamada_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_sem_tool_call_vai_direto_pro_streaming(mock_completion, mock_execute, mock_retrieve):
    resp_sem_tool_call = MagicMock()
    resp_sem_tool_call.choices = [MagicMock()]
    resp_sem_tool_call.choices[0].message.tool_calls = None
    mock_completion.side_effect = [resp_sem_tool_call, _stream_delta_chunks(["ok"])]

    eventos = [json.loads(linha) for linha in stream_pu_matcher_agent(query="teste")]

    mock_execute.assert_not_called()
    deltas = [e["content"] for e in eventos if e["type"] == "delta"]
    assert deltas == ["ok"]
