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

    ferramenta = next(
        t for t in MCP_TOOLS_DEFINITIONS
        if t["function"]["name"] == "consultar_produtos_por_aplicacao"
    )
    assert set(ferramenta["function"]["parameters"]["properties"]) == {
        "termo_busca", "listar_todos",
    }


def test_ferramenta_de_aplicacao_nao_oferece_mais_caminho_de_natureza():
    """`exigir_natureza` foi aposentado em 21/09/2026.

    Era um caminho concorrente de `consultar_produtos_por_tipo` para a MESMA
    pergunta ("produtos que são X"), com contrato incompatível (bucket
    booleano contra quatro níveis rotulados) e implementado só para dois
    termos codificados à mão — "elastômero" e "rígidos". Para qualquer outra
    palavra o parâmetro não fazia nada, em silêncio: o modelo pedia prova de
    natureza e recebia busca textual comum sem saber disso.
    """
    ferramenta = next(
        t for t in MCP_TOOLS_DEFINITIONS
        if t["function"]["name"] == "consultar_produtos_por_aplicacao"
    )
    assert "exigir_natureza" not in json.dumps(ferramenta, ensure_ascii=False)
    # E a descrição precisa mandar a pergunta de natureza para o lugar certo,
    # senão o modelo continua usando esta ferramenta para respondê-la.
    assert "consultar_produtos_por_tipo" in ferramenta["function"]["description"]


def test_execute_mcp_tool_ignora_exigir_natureza_vindo_do_modelo():
    """Argumento de LLM não é confiável: um modelo com prompt em cache ainda
    pode mandar o parâmetro aposentado, e isso não pode estourar o despacho."""
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        return_value=_resultado_mock("elastômero"),
    ) as mock_listar:
        execute_mcp_tool(
            "consultar_produtos_por_aplicacao",
            {"termo_busca": "elastômero", "exigir_natureza": True},
        )

    mock_listar.assert_called_once_with("elastômero", listar_todos=False)


def test_ferramenta_de_classificacao_catalogo_e_generica():
    nomes = [t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS]
    assert "consultar_produtos_por_classificacao_catalogo" in nomes

    with patch(
        "app.mcp.pu_mcp_server.consultar_produtos_por_classificacao_catalogo",
        return_value={
            "termo_buscado": "BT",
            "classificacoes": ["FLEXX® BT"],
            "total": 2,
            "produtos": ["FLEXX BT 2559", "FLEXX BT 2560"],
            "truncado": False,
        },
    ) as mock_listar:
        resultado = execute_mcp_tool(
            "consultar_produtos_por_classificacao_catalogo",
            {"termo_classificacao": "BT", "listar_todos": True},
        )

    mock_listar.assert_called_once_with("BT", listar_todos=True)
    assert json.loads(resultado)["classificacoes"] == ["FLEXX® BT"]


def test_ferramentas_simuladas_nao_sao_expostas_ao_modelo():
    """ERP/LIMS ainda são mocks fixos. O LLM não pode apresentá-los como
    evidência real de status comercial, código ou homologação."""
    nomes = [t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS]
    assert "consultar_catalogo_erp" not in nomes
    assert "consultar_normas_homologadas" not in nomes


def _resultado_mock(termo, por_nome=None, por_conteudo=None):
    por_nome = por_nome or {"total": 0, "produtos": [], "truncado": False}
    por_conteudo = por_conteudo or {"total": 0, "produtos": [], "truncado": False}
    return {"termo_buscado": termo, "por_nome_ou_familia": por_nome, "por_aplicacao_ou_tipo": por_conteudo}


def test_execute_mcp_tool_lista_repassa_termo_busca_e_devolve_json_valido():
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        return_value=_resultado_mock("colchão", por_conteudo={"total": 2, "produtos": ["A", "B"], "truncado": False}),
    ) as mock_listar:
        resultado = execute_mcp_tool("consultar_produtos_por_aplicacao", {"termo_busca": "colchão"})

    mock_listar.assert_called_once_with("colchão", listar_todos=False)
    dados = json.loads(resultado)
    assert dados["por_aplicacao_ou_tipo"]["produtos"] == ["A", "B"]


def test_execute_mcp_tool_lista_repassa_listar_todos_true():
    """Pedido do usuário: quando ele escolhe "todos", a segunda chamada da
    ferramenta precisa vir com listar_todos=True, não a prévia de novo."""
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        return_value=_resultado_mock("colchão", por_conteudo={"total": 37, "produtos": list(range(37)), "truncado": False}),
    ) as mock_listar:
        execute_mcp_tool("consultar_produtos_por_aplicacao", {"termo_busca": "colchão", "listar_todos": True})

    mock_listar.assert_called_once_with("colchão", listar_todos=True)


def test_execute_mcp_tool_lista_sem_termo_busca_lista_catalogo_inteiro():
    """Pedido do usuário: "liste todos os produtos" (sem categoria) — a
    ferramenta precisa funcionar sem termo_busca nenhum no argumento."""
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        return_value=_resultado_mock("", por_nome={"total": 1324, "produtos": [], "truncado": True}),
    ) as mock_listar:
        execute_mcp_tool("consultar_produtos_por_aplicacao", {})

    mock_listar.assert_called_once_with("", listar_todos=False)


def test_falha_no_qdrant_ao_listar_vira_erro_no_payload():
    with patch(
        "app.mcp.pu_mcp_server.listar_produtos_por_aplicacao",
        side_effect=RetrievalIndisponivelError("qdrant fora do ar"),
    ):
        resultado = execute_mcp_tool("consultar_produtos_por_aplicacao", {"termo_busca": "colchão"})

    dados = json.loads(resultado)
    assert "erro" in dados
