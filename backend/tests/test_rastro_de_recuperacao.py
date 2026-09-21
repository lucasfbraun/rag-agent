"""
Rastro de recuperação: qual caminho do motor atendeu cada resposta (Sessão 58).

POR QUE ISTO PRECISA DE TESTE

`model_used` agrupa CINCO detectores diferentes sob "catalogo-estruturado".
Enquanto for assim, o relatório de validação consegue dizer QUANTO o sistema
erra mas não ONDE — e "onde" é a única coisa que muda a fila de trabalho.

O modo de falha que estes testes travam é silencioso: um caminho que esquece
de se identificar não quebra nada, não loga nada, e simplesmente some do
relatório (ou pior, aparece somado a outro). O defeito só apareceria semanas
depois, na forma de uma taxa por caminho que não bate com a realidade — e aí
não há como saber desde quando.

Seam: os detectores são mockados um a um. Não se está testando se a cascata
escolhe bem — isso é assunto de outras suítes —, e sim que o caminho ESCOLHIDO
é o caminho REGISTRADO.
"""
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.rag import engine
from app.rag.caminhos import (
    CAMINHO_APLICACAO,
    CAMINHO_BUSCA_REVERSA,
    CAMINHO_CLASSIFICACAO,
    CAMINHO_CONVERSACIONAL,
    CAMINHO_DESCONHECIDO,
    CAMINHO_FORA_DE_ESCOPO,
    CAMINHO_NATUREZA,
    CAMINHO_REQUISITOS_COMPOSTOS,
    CAMINHOS_CONHECIDOS,
    rotulo_de_caminho,
)

DETECTORES = (
    "_responder_fora_do_escopo",
    "_responder_busca_reversa_produto",
    "_responder_requisitos_compostos",
    "_extrair_pedido_classificacao_catalogo",
    "_extrair_pedido_natureza_do_produto",
    "_responder_aplicacao_com_evidencia",
)


def _silenciar_detectores(exceto=()):
    """Todos os detectores devolvem None, menos os nomeados em `exceto`.

    Sem isto, cada teste dependeria de acertar uma formulação de pergunta que
    dispara exatamente um detector — e passaria a quebrar toda vez que alguém
    ajustasse uma expressão regular por um motivo sem relação nenhuma."""
    return [
        patch.object(engine, nome, return_value=None)
        for nome in DETECTORES
        if nome not in exceto
    ]


def _executar(query="pergunta qualquer", **substitutos):
    patches = _silenciar_detectores(exceto=tuple(substitutos))
    for nome, valor in substitutos.items():
        patches.append(patch.object(engine, nome, return_value=valor))
    for p in patches:
        p.start()
    try:
        return engine.run_pu_matcher_agent(query)
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Cada caminho se identifica
# ---------------------------------------------------------------------------

def test_fora_do_escopo_se_identifica():
    res = _executar(_responder_fora_do_escopo="isso está fora do escopo")
    assert res["caminho"] == CAMINHO_FORA_DE_ESCOPO


def test_busca_reversa_se_identifica_e_registra_o_codigo_procurado():
    resposta = {"answer": "...", "sources": [], "model_used": "catalogo-estruturado"}
    with patch.object(engine, "_detectar_codigos_produto", return_value=["AG 2032"]):
        res = _executar(
            query="o AG 2032 é usado em quê?",
            _responder_busca_reversa_produto=resposta,
        )
    assert res["caminho"] == CAMINHO_BUSCA_REVERSA
    assert res["termos_busca"] == ["AG 2032"]


def test_requisitos_compostos_se_identifica():
    resposta = {"answer": "...", "sources": [], "model_used": "catalogo-estruturado"}
    res = _executar(_responder_requisitos_compostos=resposta)
    assert res["caminho"] == CAMINHO_REQUISITOS_COMPOSTOS


def test_natureza_se_identifica_e_registra_o_termo_extraido():
    with patch.object(
        engine,
        "_responder_natureza_do_produto",
        # Contrato novo: o caminho de natureza devolve o dict completo, com as
        # FONTES reais das evidências da cascata — não mais `sources: []`.
        return_value={
            "answer": "lista de elastômeros",
            "sources": ["Boletim FLEXX EL 1000.pdf"],
            "model_used": "catalogo-estruturado",
        },
    ):
        res = _executar(_extrair_pedido_natureza_do_produto="elastomero")
    assert res["caminho"] == CAMINHO_NATUREZA
    assert res["termos_busca"] == ["elastomero"]


def test_classificacao_de_catalogo_se_identifica_e_registra_o_termo():
    with patch.object(
        engine, "_responder_listagem_classificacao_catalogo", return_value="lista"
    ):
        res = _executar(_extrair_pedido_classificacao_catalogo="rigidos")
    assert res["caminho"] == CAMINHO_CLASSIFICACAO
    assert res["termos_busca"] == ["rigidos"]


def test_aplicacao_com_evidencia_se_identifica():
    resposta = {"answer": "...", "sources": ["b.pdf"], "model_used": "catalogo-estruturado"}
    res = _executar(_responder_aplicacao_com_evidencia=resposta)
    assert res["caminho"] == CAMINHO_APLICACAO


def test_caminho_conversacional_se_identifica_e_registra_os_termos_pesquisados():
    """O caminho aberto é o que o vendedor leigo pega sempre, e o que mais
    erra. Registrar o que a busca procurou é o que permite responder depois
    "a resposta errada veio de ter procurado a palavra errada?"."""
    resposta_llm = MagicMock()
    resposta_llm.choices[0].message.tool_calls = None
    resposta_llm.choices[0].message.content = "resposta do modelo"

    with patch.object(
        engine, "_preparar_contexto", return_value=([{"filename": "b.pdf"}], "ctx")
    ), patch.object(engine.litellm, "completion", return_value=resposta_llm), patch.object(
        engine, "_aplicar_guardrails_resposta", side_effect=lambda q, a, h: a
    ), patch.object(
        engine, "termos_expandidos_em_cache", return_value=["elastômero"]
    ):
        res = _executar(query="tem borracha para vedação de janela?")

    assert res["caminho"] == CAMINHO_CONVERSACIONAL
    # palavras-chave da pergunta + o termo traduzido, sem duplicar
    assert "borracha" in res["termos_busca"]
    assert "elastômero" in res["termos_busca"]


# ---------------------------------------------------------------------------
# O streaming grava o mesmo rastro que o síncrono
# ---------------------------------------------------------------------------

def test_stream_publica_o_caminho_no_evento_meta():
    """O `meta` é o único lugar onde o caminho existe no streaming, e ele
    chega ANTES do primeiro delta. Se ele não carregar o campo, metade do
    histórico fica sem caminho e a taxa por caminho mede só metade do uso."""
    with patch.object(
        engine, "_responder_fora_do_escopo", return_value="fora do escopo"
    ):
        eventos = [json.loads(l) for l in engine.stream_pu_matcher_agent("bolo?")]

    assert eventos[0]["type"] == "meta"
    assert eventos[0]["caminho"] == CAMINHO_FORA_DE_ESCOPO


def test_stream_do_caminho_conversacional_tambem_publica_termos():
    resposta_llm = MagicMock()
    resposta_llm.choices[0].message.tool_calls = None
    resposta_llm.choices[0].message.content = "resposta"

    patches = _silenciar_detectores()
    patches += [
        patch.object(engine, "_preparar_contexto", return_value=([], "ctx")),
        patch.object(engine.litellm, "completion", return_value=resposta_llm),
        patch.object(engine, "_aplicar_guardrails_resposta", side_effect=lambda q, a, h: a),
    ]
    for p in patches:
        p.start()
    try:
        eventos = [
            json.loads(l)
            for l in engine.stream_pu_matcher_agent("produto para colchao de espuma")
        ]
    finally:
        for p in patches:
            p.stop()

    meta = eventos[0]
    assert meta["caminho"] == CAMINHO_CONVERSACIONAL
    assert "colchao" in meta["termos_busca"]


# ---------------------------------------------------------------------------
# O rastro chega até a linha do banco
# ---------------------------------------------------------------------------

def test_save_exchange_grava_caminho_e_termos_na_resposta_do_agente():
    """Seam sem banco: `save_exchange` monta os objetos e a sessão é um mock.
    O que se verifica é que o rastro não é descartado no meio do caminho entre
    o motor e a linha — foi exatamente assim que `model_used` já se perdeu no
    streaming antes."""
    from app.conversation_service import save_exchange

    session = MagicMock()
    save_exchange(
        session,
        user_id=uuid.uuid4(),
        conversation_id=None,
        query="tem cola para borracha?",
        answer="temos o adesivo X",
        sources=["boletim.pdf"],
        model_used="gpt-4o-mini",
        caminho=CAMINHO_CONVERSACIONAL,
        termos_busca=["adesivo", "elastômero"],
    )

    (mensagens,), _ = session.add_all.call_args
    resposta = [m for m in mensagens if m.role == "assistant"][0]
    pergunta = [m for m in mensagens if m.role == "user"][0]

    assert resposta.caminho == CAMINHO_CONVERSACIONAL
    assert resposta.termos_busca == ["adesivo", "elastômero"]
    # A pergunta do usuário não tem caminho de motor — marcar as duas
    # duplicaria cada resposta na contagem por caminho.
    assert pergunta.caminho is None


def test_save_exchange_sem_rastro_continua_funcionando():
    """Compatibilidade: quem chama sem os campos novos (e toda linha gravada
    antes desta sessão) fica com nulo, que o relatório sabe tratar."""
    from app.conversation_service import save_exchange

    session = MagicMock()
    save_exchange(
        session,
        user_id=uuid.uuid4(),
        conversation_id=None,
        query="p",
        answer="r",
    )

    (mensagens,), _ = session.add_all.call_args
    resposta = [m for m in mensagens if m.role == "assistant"][0]
    assert resposta.caminho is None
    assert resposta.termos_busca is None


# ---------------------------------------------------------------------------
# Catálogo de caminhos
# ---------------------------------------------------------------------------

def test_todo_caminho_usado_pelo_motor_tem_rotulo_legivel():
    """Um caminho sem rótulo vira uma linha de relatório que ninguém entende."""
    usados = {
        CAMINHO_FORA_DE_ESCOPO, CAMINHO_BUSCA_REVERSA, CAMINHO_REQUISITOS_COMPOSTOS,
        CAMINHO_NATUREZA, CAMINHO_CLASSIFICACAO, CAMINHO_APLICACAO,
        CAMINHO_CONVERSACIONAL, CAMINHO_DESCONHECIDO,
    }
    assert usados == set(CAMINHOS_CONHECIDOS)
    for caminho in usados:
        assert rotulo_de_caminho(caminho) != caminho


def test_caminho_desconhecido_nao_derruba_o_relatorio():
    """Uma versão mais nova da aplicação pode gravar um caminho que esta não
    conhece. Isso não pode virar exceção num relatório."""
    assert rotulo_de_caminho("caminho-do-futuro") == "caminho-do-futuro"
    assert rotulo_de_caminho(None) == rotulo_de_caminho(CAMINHO_DESCONHECIDO)


def test_caminho_cabe_na_coluna_do_banco():
    """String(40) na migration. Um caminho maior seria truncado pelo Postgres
    e as duas metades contariam como caminhos diferentes."""
    for caminho in CAMINHOS_CONHECIDOS:
        assert len(caminho) <= 40
