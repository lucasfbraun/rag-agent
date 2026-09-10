"""Protege a latência sem trocar modelo, fontes ou validação da resposta.

As contagens de chamadas externas dão um sinal determinístico do trabalho
repetido; nenhuma API paga ou banco real é necessário.
"""
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import COLLECTION_NAME
from app.rag.engine import run_pu_matcher_agent, stream_pu_matcher_agent


def _answer(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=text, tool_calls=None,
    ))])


@pytest.fixture
def isolated_context():
    with (
        patch("app.rag.engine.retrieve_products_context", return_value=[]),
        patch("app.rag.engine._montar_system_instruction", return_value="instruções"),
    ):
        yield


def test_stream_nao_gera_duas_vezes_uma_resposta_pronta(isolated_context):
    def provider(**kwargs):
        if kwargs.get("stream"):
            return iter([SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="segunda geração desnecessária"),
            )])])
        return _answer("Dado confirmado pelo boletim.")

    with patch("app.rag.engine.litellm.completion", side_effect=provider) as completion:
        events = [json.loads(line) for line in stream_pu_matcher_agent("consulta neutra")]

    assert completion.call_count == 1, "Cada geração extra acrescenta outra espera pelo LLM"
    assert [e["content"] for e in events if e["type"] == "delta"] == [
        "Dado confirmado pelo boletim."
    ]
    assert events[-1] == {"type": "done"}


@pytest.mark.parametrize("stream", [False, True])
def test_catalogo_e_treinamento_reutilizam_vetor_apenas_na_mesma_consulta(stream):
    catalog = MagicMock()
    catalog.get_collections.return_value.collections = [SimpleNamespace(name=COLLECTION_NAME)]
    catalog.scroll.return_value = ([], None)
    catalog.search.return_value = [SimpleNamespace(payload={
        "filename": "boletim.pdf", "chunk_index": 0,
        "content": "Documento técnico de referência do produto consultado.",
    })]
    training = MagicMock()
    training.get_collections.return_value.collections = [SimpleNamespace(name="pu_treinamento")]
    training.search.return_value = []
    vector = [0.1, 0.2]

    with (
        patch("app.rag.engine._get_qdrant_client", return_value=catalog),
        patch("app.rag.treinamento.get_qdrant_client", return_value=training),
        patch("app.rag.engine._montar_system_instruction", return_value="instruções"),
        patch("app.rag.engine.litellm.completion", return_value=_answer("Confirmado.")),
        patch("app.rag.embeddings.litellm.embedding", return_value=SimpleNamespace(
            data=[{"index": 0, "embedding": vector}],
        )) as embedding,
    ):
        for expected_calls in (1, 2):
            if stream:
                list(stream_pu_matcher_agent("consulta neutra"))
            else:
                run_pu_matcher_agent("consulta neutra")
            assert embedding.call_count == expected_calls

    assert catalog.search.call_args.kwargs["query_vector"] == vector
    assert training.search.call_args.kwargs["query_vector"] == vector
    assert training.search.call_count == 6  # conhecimento é consultado novamente


@pytest.mark.parametrize("stream", [False, True])
def test_ferramentas_independentes_nao_esperam_uma_pela_outra(isolated_context, stream):
    calls = [SimpleNamespace(
        id=f"call_{i}", function=SimpleNamespace(
            name="consultar_produtos_por_aplicacao",
            arguments=json.dumps({"termo_busca": term}),
        ),
    ) for i, term in enumerate(("isolamento térmico", "refrigeração"))]
    first = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, tool_calls=calls,
    ))])
    both_started = Barrier(2, timeout=2)

    def execute(name, args, **permissions):
        assert permissions == {"ver_custos": True, "ver_laudo_completo": False}
        both_started.wait()  # falha se a segunda busca só iniciar após a primeira
        return json.dumps({"termo": args["termo_busca"]})

    final = (iter([SimpleNamespace(choices=[SimpleNamespace(
        delta=SimpleNamespace(content="Resultado conferido."),
    )])]) if stream else _answer("Resultado conferido."))
    with (
        patch("app.rag.engine.execute_mcp_tool", side_effect=execute),
        patch("app.rag.engine.litellm.completion", side_effect=[first, final]) as completion,
    ):
        if stream:
            events = [json.loads(line) for line in stream_pu_matcher_agent(
                "consulta neutra", ver_custos=True,
            )]
            assert not any(e["type"] == "error" for e in events)
        else:
            assert run_pu_matcher_agent("consulta neutra", ver_custos=True)["answer"] == "Resultado conferido."

    messages = completion.call_args_list[1].kwargs["messages"]
    assert messages[-3] is first.choices[0].message
    assert [message["tool_call_id"] for message in messages[-2:]] == ["call_0", "call_1"]
    assert [json.loads(message["content"])["termo"] for message in messages[-2:]] == [
        "isolamento térmico", "refrigeração",
    ]
