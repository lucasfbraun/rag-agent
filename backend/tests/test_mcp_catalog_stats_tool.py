"""
Nova ferramenta MCP `consultar_estatisticas_catalogo` — permite o agente
responder perguntas agregadas ("quantos produtos catalogados?") chamando
`app.rag.catalog_stats.obter_estatisticas_catalogo()` real, em vez de tentar
adivinhar a partir do contexto RAG (que só traz um punhado de trechos).

Seam: mocka `obter_estatisticas_catalogo` (já testada isoladamente em
test_catalog_stats.py) — aqui só testamos o dispatch/serialização MCP.
"""
import json
from unittest.mock import patch

from app.mcp.pu_mcp_server import execute_mcp_tool, MCP_TOOLS_DEFINITIONS
from app.rag.exceptions import RetrievalIndisponivelError


def test_ferramenta_esta_registrada_na_lista_do_agente():
    nomes = [t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS]
    assert "consultar_estatisticas_catalogo" in nomes


def test_execute_mcp_tool_devolve_contagens_como_json_valido():
    with patch(
        "app.mcp.pu_mcp_server.obter_estatisticas_catalogo",
        return_value={"produtos_catalogados": 850, "documentos_indexados": 7797},
    ):
        resultado = execute_mcp_tool("consultar_estatisticas_catalogo", {})

    dados = json.loads(resultado)
    assert dados == {"produtos_catalogados": 850, "documentos_indexados": 7797}


def test_falha_no_qdrant_vira_erro_no_payload_nao_excecao_estourada():
    """Uma tool_call que estoura exceção quebraria a sequência de tool calling
    inteira (ver test_tool_calling_sequence.py) — melhor devolver um payload
    de erro que o LLM pode explicar ao usuário."""
    with patch(
        "app.mcp.pu_mcp_server.obter_estatisticas_catalogo",
        side_effect=RetrievalIndisponivelError("qdrant fora do ar"),
    ):
        resultado = execute_mcp_tool("consultar_estatisticas_catalogo", {})

    dados = json.loads(resultado)
    assert "erro" in dados


# --- consultar_produtos_por_aplicacao: listagem/categoria -------------------

def test_ferramenta_de_listagem_esta_registrada_na_lista_do_agente():
    nomes = [t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS]
    assert "consultar_produtos_por_aplicacao" in nomes


def test_execute_mcp_tool_lista_repassa_termo_busca_e_devolve_json_valido():
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        return_value={"termo_buscado": "colchão", "total_produtos_encontrados": 2, "produtos": ["A", "B"], "truncado": False},
    ) as mock_listar:
        resultado = execute_mcp_tool("consultar_produtos_por_aplicacao", {"termo_busca": "colchão"})

    mock_listar.assert_called_once_with("colchão")
    dados = json.loads(resultado)
    assert dados["produtos"] == ["A", "B"]


def test_falha_no_qdrant_ao_listar_vira_erro_no_payload():
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        side_effect=RetrievalIndisponivelError("qdrant fora do ar"),
    ):
        resultado = execute_mcp_tool("consultar_produtos_por_aplicacao", {"termo_busca": "colchão"})

    dados = json.loads(resultado)
    assert "erro" in dados
