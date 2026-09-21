"""
O relatório que fecha o ciclo de validação técnica (Sessão 58).

O QUE ESTES TESTES PROTEGEM

O item 0 de docs/avaliacao_arquitetura_2026-09-18.md diz que nenhum dos ~380
testes do projeto prova que a taxa de acerto sobre o acervo real melhorou.
Estes aqui também não provam — nada num teste unitário prova isso. Eles
protegem a MEDIÇÃO: garantem que, quando os vereditos existirem, o número
derivado deles seja honesto.

Três propriedades importam mais que as outras:

  1. **A taxa por caminho não se mistura.** É o número que diz ONDE o sistema
     erra. Se o caminho determinístico acerta 90% e o conversacional 40%,
     "65% no geral" é um número que não serve a ninguém.
  2. **Amostra pequena é declarada, nunca maquiada.** Sem histórico, o
     relatório diz que não há histórico. O princípio da tarefa é não mascarar
     com dado sintético, e ele tem de valer também para a apresentação.
  3. **Sem julgamento não existe taxa.** `None`, e não `0.0` — zero seria lido
     como "erra tudo", afirmação que o dado não faz.

Seam: `agregar_relatorio` é função pura sobre `LinhaDeVeredito`. É de
propósito: não há PostgreSQL no ambiente de desenvolvimento, e uma regra de
contagem que só se verifica com banco de pé é uma regra que ninguém verifica.
"""
from app.cli import formatar_relatorio_validacao
from app.rag.caminhos import (
    CAMINHO_CONVERSACIONAL,
    CAMINHO_DESCONHECIDO,
    CAMINHO_NATUREZA,
)
from app.validacao_service import (
    DIVERGENTE,
    LinhaDeVeredito,
    agregar_relatorio,
)


def _linha(veredito, caminho=CAMINHO_CONVERSACIONAL, mensagem_id="m1", **kw):
    return LinhaDeVeredito(
        veredito=veredito,
        caminho=caminho,
        pergunta=kw.pop("pergunta", "tem produto para colchão?"),
        mensagem_id=mensagem_id,
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. A taxa por caminho é o ponto do relatório
# ---------------------------------------------------------------------------

def test_taxa_por_caminho_separa_mecanismos_que_a_taxa_geral_misturaria():
    """Cenário do enunciado: determinístico 100%, conversacional 0%.

    A taxa geral (50%) não diz nada acionável; as duas linhas por caminho
    dizem exatamente onde trabalhar."""
    linhas = [
        _linha("correta", CAMINHO_NATUREZA, mensagem_id=f"nat{i}") for i in range(4)
    ] + [
        _linha("incorreta", CAMINHO_CONVERSACIONAL, mensagem_id=f"conv{i}")
        for i in range(4)
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=8)

    assert relatorio["taxa_acerto"] == 0.5
    por_caminho = {c["caminho"]: c for c in relatorio["por_caminho"]}
    assert por_caminho[CAMINHO_NATUREZA]["taxa_acerto"] == 1.0
    assert por_caminho[CAMINHO_CONVERSACIONAL]["taxa_acerto"] == 0.0


def test_pior_caminho_vem_primeiro_porque_o_relatorio_ordena_trabalho():
    linhas = (
        [_linha("correta", CAMINHO_NATUREZA, mensagem_id=f"n{i}") for i in range(3)]
        + [_linha("incorreta", CAMINHO_CONVERSACIONAL, mensagem_id="c1")]
    )

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=4)

    assert relatorio["por_caminho"][0]["caminho"] == CAMINHO_CONVERSACIONAL


def test_resposta_antiga_sem_caminho_registrado_nao_some_do_relatorio():
    """Respostas gravadas antes desta sessão não têm caminho. Elas continuam
    valendo como regressão — só não podem ser creditadas a um mecanismo."""
    relatorio = agregar_relatorio(
        [_linha("incorreta", caminho=None, mensagem_id="antiga")],
        total_respostas_no_periodo=1,
    )

    caminhos = {c["caminho"] for c in relatorio["por_caminho"]}
    assert caminhos == {CAMINHO_DESCONHECIDO}
    assert len(relatorio["regressao"]) == 1


# ---------------------------------------------------------------------------
# 2. Amostra pequena é declarada, não maquiada
# ---------------------------------------------------------------------------

def test_sem_nenhuma_resposta_no_banco_o_relatorio_diz_isso_em_vez_de_dar_numero():
    relatorio = agregar_relatorio([], total_respostas_no_periodo=0)

    assert relatorio["taxa_acerto"] is None
    assert relatorio["total_respostas_validadas"] == 0
    assert any("nenhuma resposta registrada" in a for a in relatorio["avisos"])


def test_respostas_existem_mas_ninguem_validou_e_o_relatorio_nao_inventa_taxa():
    relatorio = agregar_relatorio([], total_respostas_no_periodo=140)

    assert relatorio["taxa_acerto"] is None
    assert relatorio["cobertura"] == 0.0
    assert any("nenhuma foi validada" in a for a in relatorio["avisos"])


def test_amostra_abaixo_do_minimo_e_marcada_como_insuficiente():
    linhas = [_linha("correta", mensagem_id=f"m{i}") for i in range(3)]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=90)

    assert relatorio["taxa_acerto"] == 1.0  # o número existe...
    assert relatorio["amostra_suficiente"] is False  # ...mas não sustenta conclusão
    assert relatorio["por_caminho"][0]["amostra_suficiente"] is False
    assert any("Amostra pequena" in a for a in relatorio["avisos"])
    assert any("amostra insuficiente" in a for a in relatorio["avisos"])


def test_cobertura_mostra_quanto_do_uso_real_foi_efetivamente_olhado():
    linhas = [_linha("correta", mensagem_id=f"m{i}") for i in range(5)]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=20)

    assert relatorio["cobertura"] == 0.25


# ---------------------------------------------------------------------------
# 3. Contagem honesta
# ---------------------------------------------------------------------------

def test_a_mesma_resposta_avaliada_por_dois_tecnicos_conta_uma_vez():
    """Sem isto, uma resposta revisada em dupla pesaria o dobro na taxa."""
    linhas = [
        _linha("correta", mensagem_id="mesma", avaliado_por="Ana"),
        _linha("correta", mensagem_id="mesma", avaliado_por="Bruno"),
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=1)

    assert relatorio["total_vereditos"] == 2
    assert relatorio["total_respostas_validadas"] == 1
    assert relatorio["taxa_acerto"] == 1.0


def test_tecnicos_que_discordam_saem_da_taxa_e_entram_na_regressao():
    """Escolher um dos dois vereditos inventaria um consenso que não houve.
    Divergência entre especialistas normalmente significa pergunta ambígua —
    informação sobre o acervo, não sobre o motor."""
    linhas = [
        _linha("correta", mensagem_id="polemica", avaliado_por="Ana"),
        _linha("incorreta", mensagem_id="polemica", avaliado_por="Bruno"),
        _linha("correta", mensagem_id="pacifica"),
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=2)

    assert relatorio["divergentes"] == 1
    # denominador = 2 respostas - 1 divergente = 1; a taxa não é 1/2 nem 2/3.
    assert relatorio["taxa_acerto"] == 1.0
    assert [r["veredito"] for r in relatorio["regressao"]] == [DIVERGENTE]


def test_incompleta_nao_conta_como_acerto():
    """"Incompleta" é o desfecho mais comum deste motor (cita um produto certo
    e omite cinco). Contá-la como acerto esconderia justamente o buraco de
    recall que o diagnóstico de arquitetura aponta."""
    linhas = [
        _linha("correta", mensagem_id="a"),
        _linha("incompleta", mensagem_id="b"),
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=2)

    assert relatorio["taxa_acerto"] == 0.5
    assert relatorio["incompletas"] == 1
    assert len(relatorio["regressao"]) == 1


def test_regressao_carrega_a_resposta_certa_escrita_pelo_tecnico():
    linhas = [
        _linha(
            "incorreta",
            mensagem_id="x",
            pergunta="qual produto serve para rolha de cortiça?",
            resposta_correta="FLEXX AG 2066",
            justificativa="é o único com aplicação declarada no boletim",
            avaliado_por="Ana",
        )
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=1)

    item = relatorio["regressao"][0]
    assert item["pergunta"] == "qual produto serve para rolha de cortiça?"
    assert item["correcoes"][0]["resposta_correta"] == "FLEXX AG 2066"
    assert item["correcoes"][0]["avaliado_por"] == "Ana"


def test_veredito_orfao_de_conversa_apagada_continua_contando():
    """A conversa é do vendedor e ele pode apagá-la. O conjunto de regressão
    não pode encolher em silêncio por causa disso."""
    linhas = [
        LinhaDeVeredito(
            veredito="incorreta",
            caminho=CAMINHO_CONVERSACIONAL,
            pergunta="pergunta de uma conversa apagada",
            mensagem_id=None,
        ),
        LinhaDeVeredito(
            veredito="correta",
            caminho=CAMINHO_CONVERSACIONAL,
            pergunta="outra conversa apagada",
            mensagem_id=None,
        ),
    ]

    relatorio = agregar_relatorio(linhas, total_respostas_no_periodo=0)

    # Dois órfãos são duas respostas, não uma só agrupada pelo id nulo.
    assert relatorio["total_respostas_validadas"] == 2
    assert relatorio["taxa_acerto"] == 0.5


# ---------------------------------------------------------------------------
# 4. A apresentação não pode desmentir o dado
# ---------------------------------------------------------------------------

def test_texto_do_cli_nao_transforma_ausencia_de_taxa_em_zero_por_cento():
    texto = formatar_relatorio_validacao(
        agregar_relatorio([], total_respostas_no_periodo=0)
    )

    assert "0.0%" not in texto
    assert "TAXA DE ACERTO GERAL           : —" in texto
    assert "nenhuma resposta registrada" in texto


def test_texto_do_cli_mostra_a_taxa_por_caminho_e_a_regressao():
    relatorio = agregar_relatorio(
        [
            _linha("correta", CAMINHO_NATUREZA, mensagem_id="n1"),
            _linha(
                "incorreta",
                CAMINHO_CONVERSACIONAL,
                mensagem_id="c1",
                pergunta="tem cola para borracha?",
                resposta_correta="sim, o adesivo X",
            ),
        ],
        total_respostas_no_periodo=2,
    )

    texto = formatar_relatorio_validacao(relatorio)

    assert "TAXA POR CAMINHO DO MOTOR" in texto
    assert "Natureza química do produto" in texto
    assert "CONJUNTO DE REGRESSÃO" in texto
    assert "tem cola para borracha?" in texto
    assert "sim, o adesivo X" in texto
