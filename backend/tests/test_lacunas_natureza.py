"""Lacunas de cobertura da cascata de evidência por natureza (d4d66fc/d76c492).

Escritos a partir de análise de mutação: cada teste aqui corresponde a uma
mutação que NÃO quebrou nenhum teste existente. Sem eles, o código coberto
abaixo pode ser deletado inteiro e a suíte continua verde.

Seams cobertos:
  1. `execute_mcp_tool` -> `consultar_produtos_por_tipo` (o despacho real, que
     nenhum teste do motor exercita porque todos mockam `execute_mcp_tool`);
  2. a tradução leigo->técnico chegando ao MCP como `sinonimos`;
  3. `truncado=True` -> oferta de listar todos;
  4. a oferta do próximo nível da cascata;
  5. `listar_todos` derivado de "todos" na pergunta;
  6. a cascata no caminho de STREAMING (os testes existentes só cobrem o nível
     mais forte ali).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.mcp.pu_mcp_server import execute_mcp_tool
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent

NIVEIS = (
    "classificacao_estrutural",
    "identidade_declarada",
    "composicao_comprovada",
    "mencao_no_documento",
)


def _payload(atendido, **niveis):
    base = {n: {"total": 0, "produtos": [], "truncado": False} for n in NIVEIS}
    base.update(niveis)
    return json.dumps({"nivel_atendido": atendido, "niveis": base})


def _completion_simples(texto):
    """Resposta de LLM no formato que `litellm.completion` devolve — usada nos
    casos em que a cascata cai para o caminho conversacional."""
    from types import SimpleNamespace

    mensagem = SimpleNamespace(content=texto, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=mensagem)])


def _resposta_stream(query):
    eventos = [json.loads(linha) for linha in stream_pu_matcher_agent(query=query)]
    return "".join(e.get("content", "") for e in eventos if e["type"] == "delta")


# --- 1. o despacho MCP real ------------------------------------------------
# MUTACAO QUE PASSOU: renomear a branch `elif tool_name ==
# "consultar_produtos_por_tipo"` em pu_mcp_server.py nao quebrou NENHUM teste.
# Todo teste do motor mocka `execute_mcp_tool`, entao a ferramenta inteira
# podia estar morta em producao com a suite verde.

def test_execute_mcp_tool_despacha_consultar_produtos_por_tipo():
    with patch("app.mcp.pu_mcp_server.listar_produtos_por_tipo") as mock_listar:
        mock_listar.return_value = {"nivel_atendido": "identidade_declarada"}
        bruto = execute_mcp_tool(
            "consultar_produtos_por_tipo",
            {"termo": "elastomero", "listar_todos": True, "sinonimos": ["borracha"]},
        )

    assert json.loads(bruto) == {"nivel_atendido": "identidade_declarada"}
    assert mock_listar.call_args.args[0] == "elastomero"
    assert mock_listar.call_args.kwargs["listar_todos"] is True
    assert mock_listar.call_args.kwargs["sinonimos"] == ["borracha"]


def test_execute_mcp_tool_por_tipo_sobrevive_a_argumentos_ausentes():
    """Os argumentos vem do LLM: ausentes nao podem estourar o despacho."""
    with patch("app.mcp.pu_mcp_server.listar_produtos_por_tipo") as mock_listar:
        mock_listar.return_value = {"nivel_atendido": None}
        execute_mcp_tool("consultar_produtos_por_tipo", {})

    assert mock_listar.call_args.args[0] == ""
    assert mock_listar.call_args.kwargs["sinonimos"] is None


def test_consultar_produtos_por_tipo_esta_declarada_no_catalogo_de_ferramentas():
    from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS

    nomes = {t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS}
    assert "consultar_produtos_por_tipo" in nomes


# --- 2. a traducao chegando como sinonimo ----------------------------------
# MUTACAO QUE PASSOU: trocar `sinonimos = expandir_termos_do_dominio(termo)`
# por `sinonimos = []` no motor nao quebrou nada. O teste de sinonimo existente
# passa a lista direto para `listar_produtos_por_tipo` -- a fiacao
# motor->traducao->MCP nunca e exercida.

@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=["elastomero"])
def test_traducao_do_termo_leigo_chega_ao_mcp_como_sinonimo(
    mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = _payload(
        "identidade_declarada",
        identidade_declarada={
            "total": 1, "produtos": ["FLEXX EL 1000"], "truncado": False,
        },
    )

    resposta = run_pu_matcher_agent(query="quais produtos são borrachas?")["answer"]

    mock_expandir.assert_called_once()
    assert mock_execute.call_args.args[1]["sinonimos"] == ["elastomero"]
    # E o vendedor precisa SABER que a busca usou outra palavra.
    assert "elastomero" in resposta
    mock_completion.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch(
    "app.rag.engine.expandir_termos_do_dominio",
    side_effect=RuntimeError("LLM fora"),
)
def test_falha_da_traducao_nao_derruba_o_caminho_de_natureza(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    """Fail-open: sem traducao a busca segue so com a palavra do usuario."""
    mock_execute.return_value = _payload(
        "identidade_declarada",
        identidade_declarada={
            "total": 1, "produtos": ["FLEXX EL 1000"], "truncado": False,
        },
    )

    resposta = run_pu_matcher_agent(query="quais produtos são borrachas?")["answer"]

    assert "FLEXX EL 1000" in resposta
    assert mock_execute.call_args.args[1]["sinonimos"] == []
    mock_completion.assert_not_called()


# --- 3. truncado=True ------------------------------------------------------
# MUTACAO QUE PASSOU: desligar o bloco `if bucket.get("truncado")`. Todo
# payload de teste existente usa truncado=False.

@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_previa_truncada_oferece_a_lista_completa(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = _payload(
        "identidade_declarada",
        identidade_declarada={
            "total": 45,
            "produtos": [f"FLEXX EL {i}" for i in range(1, 11)],
            "truncado": True,
        },
    )

    resposta = run_pu_matcher_agent(query="quais produtos são elastômeros?")["answer"]

    assert "45" in resposta
    assert "Quer que eu liste todos os 45?" in resposta
    mock_completion.assert_not_called()


# --- 4. a oferta do proximo nivel ------------------------------------------
# MUTACAO QUE PASSOU: `if extra > total:` -> `if False and extra > total:`.
# O bloco EXECUTA no teste existente (composicao=2, mencao=9) mas ninguem
# afirma nada sobre ele.

@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_nivel_mais_fraco_com_mais_produtos_e_oferecido(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = _payload(
        "composicao_comprovada",
        composicao_comprovada={
            "total": 2, "produtos": ["FLEXX A", "FLEXX B"], "truncado": False,
        },
        mencao_no_documento={"total": 9, "produtos": [], "truncado": False},
    )

    resposta = run_pu_matcher_agent(query="quais produtos são elastômeros?")["answer"]

    assert "9" in resposta
    assert "evidência mais fraca" in resposta
    mock_completion.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_nivel_mais_fraco_sem_ganho_nao_e_oferecido(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    """Oferecer evidencia pior sem produto novo so gera ruido."""
    mock_execute.return_value = _payload(
        "composicao_comprovada",
        composicao_comprovada={
            "total": 2, "produtos": ["FLEXX A", "FLEXX B"], "truncado": False,
        },
        mencao_no_documento={"total": 2, "produtos": [], "truncado": False},
    )

    resposta = run_pu_matcher_agent(query="quais produtos são elastômeros?")["answer"]

    assert "evidência mais fraca" not in resposta


# --- 5. listar_todos vindo da pergunta -------------------------------------
# MUTACAO QUE PASSOU: `listar_todos = False` fixo no motor.

@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("liste todos os produtos que são elastômeros", True),
        ("quais produtos são elastômeros?", False),
    ],
)
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_pedido_explicito_de_lista_completa_chega_ao_mcp(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve, pergunta, esperado
):
    mock_execute.return_value = _payload(None)

    run_pu_matcher_agent(query=pergunta)

    assert mock_execute.call_args.args[1]["listar_todos"] is esperado


# --- 6. a cascata no streaming ---------------------------------------------
# O teste de streaming existente so cobre o nivel mais forte. A queda de nivel
# e a ausencia total -- exatamente o comportamento que os commits corrigem --
# nunca foram vistas pelo caminho que o usuario realmente usa no chat.

@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_stream_cai_para_nivel_mais_fraco_em_vez_de_nao_encontrei(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = _payload(
        "composicao_comprovada",
        composicao_comprovada={
            "total": 2,
            "produtos": ["FLEXX TH T160DE1", "FLEXX TH T193AH4"],
            "truncado": False,
        },
        mencao_no_documento={"total": 9, "produtos": [], "truncado": False},
    )

    resposta = _resposta_stream("quais produtos são elastômeros?")

    assert "FLEXX TH T160DE1" in resposta
    assert "evidência mais forte" in resposta
    assert "não é o mesmo que o produto SER aquilo" in resposta
    mock_completion.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_stream_cascata_vazia_segue_para_o_caminho_conversacional(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    """Mesma válvula de segurança da rota síncrona: cascata vazia não encerra
    em resposta determinística, segue para o caminho com RAG e ferramentas.
    O detector é regex sobre a forma da pergunta e sempre deixa passar algum
    termo que não é uma natureza de produto."""
    mock_execute.return_value = _payload(None)
    mock_completion.return_value = _completion_simples("Resposta do modelo.")

    resposta = _resposta_stream("quais produtos são atóxicos?")

    mock_completion.assert_called()
    assert "Resposta do modelo." in resposta


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
@patch("app.rag.engine.expandir_termos_do_dominio", return_value=[])
def test_catalogo_indisponivel_no_caminho_de_natureza_nao_inventa_resposta(
    _mock_expandir, mock_completion, mock_execute, _mock_retrieve
):
    mock_execute.return_value = json.dumps({"erro": "Catálogo indisponível: timeout"})

    resposta = run_pu_matcher_agent(query="quais produtos são elastômeros?")["answer"]

    assert "indisponível" in resposta
    mock_completion.assert_not_called()
