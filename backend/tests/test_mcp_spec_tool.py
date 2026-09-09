"""
Ferramenta MCP `consultar_produtos_por_especificacao` — terceira dimensão de
consulta ao acervo, ao lado de nome/família e de aplicação/tipo: o VALOR da
especificação técnica ("quais produtos têm hidroxila de 180?").

Seam: mocka `buscar_produtos_por_especificacao` (testada isoladamente em
test_spec_search.py) — aqui só o dispatch, a coerção de argumentos vindos do
LLM e a serialização MCP.
"""
import json
from unittest.mock import patch

from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool
from app.rag.exceptions import RetrievalIndisponivelError
from app.rag.spec_search import PROPRIEDADES

_RESPOSTA = {"total": 1, "produtos": [{"produto": "FLEXX POL 1180"}]}


def _definicao():
    return next(
        t["function"] for t in MCP_TOOLS_DEFINITIONS
        if t["function"]["name"] == "consultar_produtos_por_especificacao"
    )


def test_ferramenta_esta_registrada_na_lista_do_agente():
    assert _definicao()["name"] == "consultar_produtos_por_especificacao"


def test_enum_de_propriedades_acompanha_o_vocabulario_real():
    """A lista de propriedades vive em spec_search; duplicá-la à mão na
    definição da ferramenta é exatamente o tipo de divergência que já causou
    bug neste projeto (ver docstring de app.config)."""
    enum = _definicao()["parameters"]["properties"]["propriedade"]["enum"]
    assert set(enum) == set(PROPRIEDADES.keys())
    assert "indice_hidroxila" in enum


def test_dispatch_repassa_criterio_completo():
    with patch(
        "app.mcp.pu_mcp_server.buscar_produtos_por_especificacao", return_value=_RESPOSTA
    ) as busca:
        resultado = execute_mcp_tool("consultar_produtos_por_especificacao", {
            "propriedade": "teor_nco", "valor": 12, "operador": "entre", "valor_maximo": 13,
        })

    assert json.loads(resultado) == _RESPOSTA
    assert busca.call_args.kwargs["operador"] == "entre"
    assert busca.call_args.kwargs["valor_maximo"] == 13.0


def test_valor_em_texto_e_convertido_em_vez_de_estourar():
    """Argumento vem de um LLM: "180" como string é o caso comum, e derrubar a
    tool_call por isso quebraria a sequência de tool calling inteira (ver
    test_tool_calling_sequence.py)."""
    with patch(
        "app.mcp.pu_mcp_server.buscar_produtos_por_especificacao", return_value=_RESPOSTA
    ) as busca:
        execute_mcp_tool("consultar_produtos_por_especificacao", {
            "propriedade": "indice_hidroxila", "valor": "180",
        })

    assert busca.call_args.kwargs["valor"] == 180.0


def test_valor_ausente_vira_erro_legivel_para_o_modelo_corrigir():
    with patch("app.mcp.pu_mcp_server.buscar_produtos_por_especificacao") as busca:
        resultado = execute_mcp_tool("consultar_produtos_por_especificacao", {
            "propriedade": "indice_hidroxila",
        })

    assert "erro" in json.loads(resultado)
    busca.assert_not_called()


def test_valor_maximo_invalido_nao_derruba_a_chamada():
    with patch(
        "app.mcp.pu_mcp_server.buscar_produtos_por_especificacao", return_value=_RESPOSTA
    ) as busca:
        execute_mcp_tool("consultar_produtos_por_especificacao", {
            "propriedade": "viscosidade", "valor": 5000, "valor_maximo": "não sei",
        })

    assert busca.call_args.kwargs["valor_maximo"] is None


def test_falha_no_qdrant_vira_payload_de_erro_nao_excecao():
    with patch(
        "app.mcp.pu_mcp_server.buscar_produtos_por_especificacao",
        side_effect=RetrievalIndisponivelError("qdrant fora do ar"),
    ):
        resultado = execute_mcp_tool("consultar_produtos_por_especificacao", {
            "propriedade": "indice_hidroxila", "valor": 180,
        })

    assert "indisponível" in json.loads(resultado)["erro"]
