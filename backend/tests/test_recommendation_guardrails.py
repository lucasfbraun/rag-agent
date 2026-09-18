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
def test_pedido_de_natureza_e_deterministico_e_nao_chama_o_llm(
    mock_completion, mock_execute, mock_retrieve
):
    """Listagem por natureza nunca passa pelo modelo: um top-k de trechos nunca
    representa uma categoria fielmente, e a cascata de evidência é exata."""
    mock_execute.return_value = json.dumps({
        "nivel_atendido": "identidade_declarada",
        "niveis": {
            "classificacao_estrutural": {"total": 0, "produtos": [], "truncado": False},
            "identidade_declarada": {
                "total": 1, "produtos": ["FLEXX TH T160DE1"], "truncado": False,
            },
            "composicao_comprovada": {"total": 0, "produtos": [], "truncado": False},
            "mencao_no_documento": {"total": 0, "produtos": [], "truncado": False},
        },
    })

    result = run_pu_matcher_agent(query="quais produtos são elastômeros?")

    assert "FLEXX TH T160DE1" in result["answer"]
    assert mock_execute.call_args.args[0] == "consultar_produtos_por_tipo"
    assert mock_execute.call_args.args[1]["termo"] == "elastomeros"
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_do_pedido_de_natureza_tambem_e_deterministico(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "nivel_atendido": "identidade_declarada",
        "niveis": {
            "classificacao_estrutural": {"total": 0, "produtos": [], "truncado": False},
            "identidade_declarada": {
                "total": 1, "produtos": ["FLEXX TH T160DE1"], "truncado": False,
            },
            "composicao_comprovada": {"total": 0, "produtos": [], "truncado": False},
            "mencao_no_documento": {"total": 0, "produtos": [], "truncado": False},
        },
    })

    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(query="quais produtos são elastômeros?")
    ]
    answer = "".join(e.get("content", "") for e in events if e["type"] == "delta")

    assert "FLEXX TH T160DE1" in answer
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_nivel_fraco_e_mostrado_em_vez_de_encerrar_em_nao_encontrei(
    mock_completion, mock_execute, mock_retrieve
):
    """O CASO RELATADO: nenhum boletim declara identidade, mas há evidência de
    composição. Antes a resposta era "não encontrei" e fim."""
    mock_execute.return_value = json.dumps({
        "nivel_atendido": "composicao_comprovada",
        "niveis": {
            "classificacao_estrutural": {"total": 0, "produtos": [], "truncado": False},
            "identidade_declarada": {"total": 0, "produtos": [], "truncado": False},
            "composicao_comprovada": {
                "total": 2,
                "produtos": ["FLEXX TH T160DE1", "FLEXX TH T193AH4"],
                "truncado": False,
            },
            "mencao_no_documento": {"total": 9, "produtos": [], "truncado": False},
        },
    })

    result = run_pu_matcher_agent(query="quais produtos são elastômeros?")
    answer = result["answer"]

    assert "FLEXX TH T160DE1" in answer
    assert "FLEXX TH T193AH4" in answer
    # Diz que procurou evidência mais forte e não achou — senão o vendedor não
    # tem como saber que está recebendo a segunda melhor evidência.
    assert "evidência mais forte" in answer
    assert "não é o mesmo que o produto SER aquilo" in answer
    mock_completion.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_cascata_vazia_segue_para_o_caminho_conversacional(
    mock_completion, mock_execute, mock_retrieve
):
    """VÁLVULA DE SEGURANÇA. Nada nos quatro níveis quase sempre significa que
    o termo capturado não era uma natureza — o detector é regex sobre a FORMA
    da pergunta e sempre deixa passar algum caso. Encerrar ali com resposta
    determinística seria o mesmo beco sem saída que este trabalho existe para
    matar, só que com outra palavra."""
    mock_execute.return_value = json.dumps({
        "nivel_atendido": None,
        "niveis": {
            n: {"total": 0, "produtos": [], "truncado": False}
            for n in (
                "classificacao_estrutural", "identidade_declarada",
                "composicao_comprovada", "mencao_no_documento",
            )
        },
    })
    mock_completion.return_value = _completion("Resposta do modelo com contexto.")

    result = run_pu_matcher_agent(query="quais produtos são atóxicos?")

    # A pergunta chegou ao caminho normal, com RAG e ferramentas.
    mock_completion.assert_called()
    mock_retrieve.assert_called()
    assert result["model_used"] != "catalogo-estruturado"


@pytest.mark.parametrize(
    "pergunta,termo",
    [
        ("quais produtos são elastômeros?", "elastomeros"),
        ("produtos que são adesivos", "adesivos"),
        ("produtos do tipo selante", "selante"),
        ("liste os produtos que são catalisadores", "catalisadores"),
    ],
)
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_o_caminho_de_natureza_vale_para_qualquer_terminologia(
    mock_completion, mock_execute, mock_retrieve, pergunta, termo
):
    """O motor tinha seis funções codificadas só para "elastômero". Qualquer
    outra terminologia exigiria mais uma."""
    mock_execute.return_value = json.dumps({
        "nivel_atendido": "identidade_declarada",
        "niveis": {
            "classificacao_estrutural": {"total": 0, "produtos": [], "truncado": False},
            "identidade_declarada": {
                "total": 1, "produtos": ["FLEXX XX 1"], "truncado": False,
            },
            "composicao_comprovada": {"total": 0, "produtos": [], "truncado": False},
            "mencao_no_documento": {"total": 0, "produtos": [], "truncado": False},
        },
    })

    run_pu_matcher_agent(query=pergunta)

    assert mock_execute.call_args.args[0] == "consultar_produtos_por_tipo"
    assert mock_execute.call_args.args[1]["termo"] == termo
    mock_completion.assert_not_called()


@patch("app.rag.engine._responder_aplicacao_com_evidencia", return_value=None)
@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_pergunta_de_finalidade_nao_vai_para_o_caminho_de_natureza(
    mock_completion, mock_execute, mock_retrieve, _mock_aplicacao
):
    """"produtos PARA produzir elastômero" é finalidade, não natureza. Confundir
    as duas é o erro que a distinção existe para impedir — e agora a finalidade
    segue para o caminho genérico de aplicação, não para um ramo do termo."""
    mock_completion.return_value = _completion("Resposta do modelo.")

    run_pu_matcher_agent(query="liste produtos para produzir elastômero")

    chamadas_de_natureza = [
        c for c in mock_execute.call_args_list
        if c.args and c.args[0] == "consultar_produtos_por_tipo"
    ]
    assert chamadas_de_natureza == []



@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_produtos_da_tecnologia_de_rigidos_usam_classificacao_estrita(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "classificacoes": ["FLEXX® RG"],
        "total": 1,
        "produtos": ["FLEXX RGE 2859"],
        "truncado": False,
    })

    result = run_pu_matcher_agent(
        query="me retorne produtos que são da tecnologia de rígidos"
    )

    mock_execute.assert_called_once_with(
        "consultar_produtos_por_classificacao_catalogo",
        {
            "termo_classificacao": "rigidos",
            "listar_todos": False,
        },
    )
    assert "FLEXX CAT" not in result["answer"]
    assert "INATIVO" not in result["answer"]
    assert "FLEXX RGE 2859" in result["answer"]
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_tecnologia_de_rigidos_tambem_descarta_inativos_e_auxiliares(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "classificacoes": ["FLEXX® RG"],
        "total": 1,
        "produtos": ["FLEXX RGE 2859"],
        "truncado": False,
    })

    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(
            query="me retorne produtos que são da tecnologia de rígidos"
        )
    ]
    answer = "".join(
        event.get("content", "") for event in events if event["type"] == "delta"
    )

    assert "FLEXX RGE 2859" in answer
    assert events[0]["model_used"] == "catalogo-estruturado"
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_listagem_de_qualquer_linha_usa_classificacao_estrutural(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "termo_buscado": "FLEXX BT",
        "classificacoes": ["FLEXX® BT"],
        "total": 2,
        "produtos": ["FLEXX BT 2559", "FLEXX BT 2560"],
        "truncado": False,
    })

    result = run_pu_matcher_agent(query="me retorne os produtos da linha FLEXX BT")

    mock_execute.assert_called_once_with(
        "consultar_produtos_por_classificacao_catalogo",
        {"termo_classificacao": "flexx bt", "listar_todos": False},
    )
    assert "FLEXX BT 2559" in result["answer"]
    assert "classificados" in result["answer"]
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_classificacao_desconhecida_nao_cai_em_busca_textual(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "termo_buscado": "linha inventada",
        "classificacoes": [],
        "total": 0,
        "produtos": [],
        "truncado": False,
        "classificacoes_disponiveis": ["FLEXX® BT", "FLEXX® RG"],
    })

    result = run_pu_matcher_agent(
        query="quais produtos são da tecnologia de linha inventada?"
    )

    assert "não encontrei a tecnologia ou linha" in result["answer"].lower()
    assert "apenas mencionam" in result["answer"].lower()
    mock_completion.assert_not_called()
    mock_retrieve.assert_not_called()


@patch("app.rag.engine.retrieve_products_context", return_value=[])
@patch("app.rag.engine.execute_mcp_tool")
@patch("app.rag.engine.litellm.completion")
def test_stream_de_qualquer_linha_tambem_usa_classificacao_estrutural(
    mock_completion, mock_execute, mock_retrieve
):
    mock_execute.return_value = json.dumps({
        "termo_buscado": "th",
        "classificacoes": ["FLEXX® TH"],
        "total": 1,
        "produtos": ["FLEXX TH M185AH2"],
        "truncado": False,
    })

    events = [
        json.loads(line)
        for line in stream_pu_matcher_agent(
            query="mostre os produtos da linha de produtos TH"
        )
    ]
    answer = "".join(
        event.get("content", "") for event in events if event["type"] == "delta"
    )

    assert "FLEXX TH M185AH2" in answer
    assert events[0]["model_used"] == "catalogo-estruturado"
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
