"""
Nova capacidade: entender que a pergunta é sobre uma SEÇÃO do Boletim Técnico
("quais as vantagens do AG 2032", "como armazenar", "em que embalagem vem"),
não sobre um número nem sobre um nome.

O problema real: um boletim vira vários chunks no índice, e a busca vetorial
escolhe entre eles por similaridade geral — perguntar "vantagens do X" trazia
o chunk da tabela de especificação ou da FISPQ (produto certo, seção errada) e
o agente respondia com o que tinha na mão.

Os cabeçalhos usados aqui são os REAIS do acervo (boletins FLEXX, linha
Polimper/Pró e FISPQs), incluindo a convivência de "SEGURANÇA E ARMAZENAMENTO"
(boletim) com "MANUSEIO E ARMAZENAMENTO" (FISPQ, seção 7).

Seam: funções puras de app.rag.doc_sections — sem I/O.
"""
import pytest

from app.rag.doc_sections import (
    PALAVRAS_DE_SECAO,
    contem_secao,
    detectar_secoes,
    extrair_secao,
    montar_instrucao_de_secao,
    termos_de_indice,
)

BOLETIM = (
    "ESPECIFICAÇÕES TÉCNICAS Estado físico - Líquido viscoso Viscosidade, 25°C cPs 450 a 750 "
    "CARACTERÍSTICAS E VANTAGENS Reduz o consumo de máquina, boa fluidez, ótima relação de "
    "trabalho, pode variar o tempo de desmolde. "
    "APLICAÇÃO FLEXX é recomendado para produção de espuma flexível de poliuretano moldada. "
    "SEGURANÇA E ARMAZENAMENTO O produto é químico e só deve ser manipulado por pessoas "
    "capacitadas. Manter a embalagem fechada. "
    "Embalagens Baldes plásticos de 20 Kg e tambores de 200 Kg."
)


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("quais as vantagens do FLEXX AG 2032", "caracteristicas_vantagens"),
        ("como devo armazenar o CAT 136?", "seguranca_armazenamento"),
        ("qual a validade do produto", "seguranca_armazenamento"),
        ("em quais embalagens o AG 2066 é fornecido", "embalagem"),
        ("qual a reatividade do TH M60", "reatividade"),
        ("me traga a ficha técnica do AG 2032", "especificacoes"),
        ("quais EPIs preciso usar", "seguranca_armazenamento"),
        ("vem em tambor?", "embalagem"),
    ],
)
def test_detecta_a_secao_pedida_no_vocabulario_do_vendedor(pergunta, esperado):
    assert esperado in detectar_secoes(pergunta)


def test_pergunta_que_nao_e_sobre_secao_nao_ativa_o_caminho_de_secao():
    """Sem isso toda pergunta viraria um pedido de seção e a recuperação
    normal seria distorcida sem motivo."""
    assert detectar_secoes("quero um produto para colchão") == []
    assert detectar_secoes("liste os produtos da família CAT") == []


def test_secoes_saem_na_ordem_em_que_aparecem_na_pergunta():
    assert detectar_secoes("quais as vantagens e como é a embalagem") == [
        "caracteristicas_vantagens", "embalagem",
    ]


def test_termos_de_indice_sao_tokens_unicos():
    """O índice de texto do Qdrant é tokenizado por palavra (ver
    app.rag.ingestion._garantir_indices_texto): frase inteira não casa."""
    for termo in termos_de_indice(["seguranca_armazenamento", "embalagem"]):
        assert " " not in termo


@pytest.mark.parametrize(
    "secao,trecho_esperado",
    [
        ("caracteristicas_vantagens", "Reduz o consumo de máquina"),
        ("seguranca_armazenamento", "só deve ser manipulado"),
        ("embalagem", "Baldes plásticos de 20 Kg"),
        ("aplicacao", "espuma flexível de poliuretano"),
    ],
)
def test_extrai_a_secao_ate_o_proximo_cabecalho(secao, trecho_esperado):
    extraido = extrair_secao(BOLETIM, secao)
    assert trecho_esperado in extraido


def test_secao_extraida_nao_invade_a_seguinte():
    vantagens = extrair_secao(BOLETIM, "caracteristicas_vantagens")
    assert "recomendado para produção" not in vantagens


def test_secao_nao_e_truncada_por_mencao_de_outra_secao_em_frase_corrida():
    """"Manter a embalagem fechada" está DENTRO da seção de armazenamento —
    cortar ali entregaria meia recomendação de segurança ao vendedor."""
    armazenamento = extrair_secao(BOLETIM, "seguranca_armazenamento")
    assert "Manter a embalagem fechada" in armazenamento
    assert "Baldes plásticos" not in armazenamento


def test_secao_ausente_devolve_none_em_vez_de_texto_qualquer():
    """Devolver "algum texto" aqui viraria resposta errada com cara de certa:
    o agente apresentaria outra seção como se fosse a pedida."""
    assert extrair_secao(BOLETIM, "reatividade") is None
    assert contem_secao(BOLETIM, "reatividade") is False


def test_mencionar_a_palavra_nao_e_o_mesmo_que_conter_a_secao():
    """O filtro de texto do Qdrant casa a palavra em qualquer lugar do trecho;
    `contem_secao` é quem confirma que o trecho ABRE a seção. Sem essa
    checagem, um boletim que só cita "vantagens" no meio de um parágrafo seria
    promovido ao topo do contexto como se trouxesse a seção."""
    solto = "O produto tem vantagens frente ao concorrente, conforme tabela."
    assert contem_secao(solto, "caracteristicas_vantagens") is False


def test_instrucao_de_secao_nomeia_a_secao_e_proibe_troca():
    instrucao = montar_instrucao_de_secao(["embalagem"])
    assert "Embalagens" in instrucao
    assert "não consta" in instrucao


def test_sem_secao_nao_ha_instrucao():
    assert montar_instrucao_de_secao([]) == ""


def test_palavras_de_secao_cobrem_os_termos_genericos_do_acervo():
    """Elas são excluídas da extração genérica de palavras-chave do RAG: como
    palavra-chave solta, "vantagens"/"armazenamento" batem em quase todo
    boletim do acervo e só enchem o top-k de ruído."""
    assert {"vantagens", "armazenamento", "reatividade", "embalagem"} <= PALAVRAS_DE_SECAO
