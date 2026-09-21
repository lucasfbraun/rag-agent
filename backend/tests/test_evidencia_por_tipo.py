"""
Cascata de evidência genérica para "quais produtos são X" (18/09/2026).

O PROBLEMA QUE ORIGINOU ISTO

"quais produtos são elastômeros?" respondia "não encontrei" num acervo com
centenas de boletins. Não era falha de recuperação — a varredura lê a coleção
inteira. Era a regra de aceitação: exigia o boletim conter literalmente
"<produto> é um elastômero", frase que boletim técnico não escreve. Zero,
sempre, por mais documentos que existissem.

E o problema não era de elastômero: o motor tinha SEIS funções codificadas só
para esse termo e uma só para "rígidos". Qualquer outra terminologia que o
usuário trouxesse — adesivo, selante, catalisador — exigiria mais uma.

Estes testes travam as duas propriedades que importam:
  1. a cascata nunca termina em "não há nada" quando há evidência mais fraca,
     e diz QUAL nível respondeu;
  2. nada nela é específico de elastômero.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag.catalog_stats import (
    NIVEIS_DE_EVIDENCIA,
    _LARGURA_TRECHO_EVIDENCIA,
    _conteudo_comprova_composicao_do_termo,
    _conteudo_declara_produto_como,
    _variantes_do_termo,
    listar_produtos_por_tipo,
)

RAIZ = r"\\servidor\acervo\Documentação de Produto"


def _ponto(filepath, content):
    p = MagicMock()
    p.payload = {"filepath": filepath, "content": content}
    return p


def _cliente(pontos):
    cliente = MagicMock()
    cliente.scroll.return_value = (pontos, None)
    return cliente


def _listar(pontos, termo, **kwargs):
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=_cliente(pontos)):
        return listar_produtos_por_tipo(termo, **kwargs)


# --- a prova de identidade é genérica ---------------------------------------

@pytest.mark.parametrize(
    "termo,frase",
    [
        ("elastomero", "FLEXX EL 1000 é um elastômero de alta resiliência."),
        ("adesivo", "FLEXX EL 1000 é um adesivo bicomponente."),
        ("selante", "FLEXX EL 1000 trata-se de um selante poliuretânico."),
        ("catalisador", "FLEXX EL 1000 consiste em um catalisador amínico."),
        ("isocianato", "FLEXX EL 1000 é um isocianato modificado."),
    ],
)
def test_identidade_declarada_vale_para_qualquer_termo(termo, frase):
    """Antes existiam duas cópias literais desta regra, uma por substantivo.
    Cada natureza nova exigia uma terceira."""
    caminho = rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf"
    assert _conteudo_declara_produto_como(termo, caminho, frase) is True


def test_identidade_nao_aceita_finalidade():
    """"produz elastômero" é para que serve, não o que é. A distinção é o
    motivo de a regra estrita existir — ela só não podia ser o fim da linha."""
    caminho = rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf"
    texto = "FLEXX EL 1000 produz elastômero de alta performance."
    assert _conteudo_declara_produto_como("elastomero", caminho, texto) is False


def test_composicao_aceita_a_forma_que_boletim_realmente_usa():
    """A frase que o acervo escreve de verdade — e que a regra estrita
    descartava, produzindo zero resultados."""
    caminho = rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf"
    for texto in (
        "Sistema bicomponente para obtenção de elastômeros de alta resiliência.",
        "Indicado para produção de elastômero microcelular.",
        "Sistema elastomérico de dois componentes.",
    ):
        assert _conteudo_comprova_composicao_do_termo("elastomero", caminho, texto) is True


def test_variantes_cobrem_plural_e_forma_adjetiva():
    variantes = _variantes_do_termo("elastômero")
    assert "elastomero" in variantes
    assert "elastomeros" in variantes
    assert "elastomerico" in variantes


# --- a cascata ---------------------------------------------------------------

def test_cai_para_composicao_quando_nenhum_boletim_declara_identidade():
    """O CASO RELATADO. Antes: "não encontrei" e fim. Agora: a resposta mais
    fraca existe, está rotulada, e o nível atendido diz o que ela é."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL {n}\Boletim FLEXX EL {n}.pdf",
            "Sistema bicomponente para obtenção de elastômeros de alta resiliência.",
        )
        for n in (1000, 1001, 1002)
    ]
    resultado = _listar(pontos, "elastomero")

    assert resultado["niveis"]["identidade_declarada"]["total"] == 0
    assert resultado["nivel_atendido"] == "composicao_comprovada"
    assert resultado["niveis"]["composicao_comprovada"]["total"] == 3


def test_identidade_vence_composicao_quando_as_duas_existem():
    """A cascata é ordenada: a evidência mais forte é a que responde."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "FLEXX EL 1000 é um elastômero. Indicado para produção de elastômero.",
        )
    ]
    resultado = _listar(pontos, "elastomero")
    assert resultado["nivel_atendido"] == "identidade_declarada"


def test_classificacao_estrutural_vence_o_texto():
    """A árvore de pastas é a única prova independente de como o boletim foi
    redigido — mesma disciplina já usada para a tecnologia de rígidos."""
    pontos = [
        _ponto(
            rf"{RAIZ}\Elastômeros\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "Sistema bicomponente para obtenção de elastômeros.",
        )
    ]
    resultado = _listar(pontos, "elastômeros")
    assert resultado["nivel_atendido"] == "classificacao_estrutural"
    assert resultado["niveis"]["classificacao_estrutural"]["total"] == 1


def test_menciona_e_o_ultimo_recurso_e_nao_afirma_classificacao():
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX AG\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf",
            "Compatível com peças de borracha e elastômero vizinhas na montagem.",
        )
    ]
    resultado = _listar(pontos, "elastomero")
    assert resultado["nivel_atendido"] == "mencao_no_documento"
    assert resultado["niveis"]["identidade_declarada"]["total"] == 0
    assert resultado["niveis"]["composicao_comprovada"]["total"] == 0


def test_nada_em_lugar_nenhum_devolve_nivel_atendido_nulo():
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX AG\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf",
            "Adesivo para colagem de rolhas de cortiça aglomerada.",
        )
    ]
    resultado = _listar(pontos, "elastomero")
    assert resultado["nivel_atendido"] is None
    assert all(resultado["niveis"][n]["total"] == 0 for n in NIVEIS_DE_EVIDENCIA)


# --- as exclusões viraram dado e valem para qualquer termo ------------------

def test_isocianato_declarado_nao_entra_como_outra_natureza():
    """Regressão do caso real FLEXX ISO 131001: o boletim dizia que o
    isocianato participa da combinação que produz poliuretano elastomérico, e
    ele aparecia listado como elastômero."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX ISO\FLEXX ISO 131001\Boletim FLEXX ISO 131001.pdf",
            "FLEXX ISO 131001 é um isocianato. Combinado com poliol, "
            "permite obtenção de elastômero.",
        )
    ]
    resultado = _listar(pontos, "elastomero")
    assert resultado["niveis"]["composicao_comprovada"]["total"] == 0


def test_quem_pergunta_por_isocianato_nao_e_excluido_pela_propria_natureza():
    """A exclusão é comparada com o termo perguntado — senão "quais produtos
    são isocianatos" também devolveria zero, pelo mesmo bug ao contrário."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX ISO\FLEXX ISO 131001\Boletim FLEXX ISO 131001.pdf",
            "FLEXX ISO 131001 é um isocianato modificado.",
        )
    ]
    resultado = _listar(pontos, "isocianato")
    assert resultado["nivel_atendido"] == "identidade_declarada"
    assert resultado["niveis"]["identidade_declarada"]["total"] == 1


def test_aditivo_e_catalisador_nao_sao_o_material_para_nenhum_termo():
    """Era regra escrita à mão dentro do caminho de elastômero; virou dado e
    passou a valer para qualquer natureza perguntada."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX ADT\FLEXX ADT 55\Boletim FLEXX ADT 55.pdf",
            "Indicado para produção de elastômero de alta resiliência.",
        ),
        _ponto(
            rf"{RAIZ}\FLEXX CAT\FLEXX CAT 42\Boletim FLEXX CAT 42.pdf",
            "Indicado para produção de adesivo estrutural.",
        ),
    ]
    assert _listar(pontos, "elastomero")["niveis"]["composicao_comprovada"]["total"] == 0
    assert _listar(pontos, "adesivo")["niveis"]["composicao_comprovada"]["total"] == 0


# --- terminologia do usuário ------------------------------------------------

def test_sinonimo_encontra_o_que_a_palavra_do_usuario_nao_encontraria():
    """"borracha" não aparece em boletim nenhum; "elastômero" aparece. Os
    sinônimos vêm da tradução leigo→técnico, em vez de uma tabela de aliases
    mantida à mão por terminologia."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "FLEXX EL 1000 é um elastômero de alta resiliência.",
        )
    ]
    sem_sinonimo = _listar(pontos, "borracha")
    com_sinonimo = _listar(pontos, "borracha", sinonimos=["elastômero"])

    assert sem_sinonimo["nivel_atendido"] is None
    assert com_sinonimo["nivel_atendido"] == "identidade_declarada"
    assert com_sinonimo["termo_buscado"] == "borracha"


def test_catalogo_indisponivel_levanta_erro_tipado():
    from app.rag.exceptions import RetrievalIndisponivelError

    with patch("app.rag.catalog_stats.get_qdrant_client", side_effect=Exception("fora do ar")):
        with pytest.raises(RetrievalIndisponivelError):
            listar_produtos_por_tipo("elastomero")


# --- regressões achadas na revisão independente de 18/09/2026 ---------------

@pytest.mark.parametrize(
    "plural,singular",
    [
        ("catalisadores", "catalisador"),
        ("vernizes", "verniz"),
        ("poliois", "poliol"),
        ("elastomeros", "elastomero"),
        ("adesivos", "adesivo"),
    ],
)
def test_plural_alcanca_o_singular_que_o_boletim_escreve(plural, singular):
    """A primeira versão só sabia tirar e pôr um "s": "catalisadores" virava
    "catalisadore" e NUNCA alcançava "catalisador". Resultado — "quais produtos
    são catalisadores?" devolvia ZERO num acervo onde o Boletim diz, literal,
    "FLEXX CAT 42 é um catalisador". O bug que esta cascata existe para matar,
    ressuscitado em outra palavra."""
    assert singular in _variantes_do_termo(plural)
    assert plural in _variantes_do_termo(singular)


def test_perguntar_pela_familia_auxiliar_nao_exclui_a_propria_familia():
    """A exclusão de ADT/CAT era incondicional, então "quais produtos são
    catalisadores?" removia justamente os FLEXX CAT — a resposta certa — e a
    pergunta caía para o nível de menção, rotulada como a evidência mais fraca
    do sistema. Mesmo escape que `_NATUREZAS_EXCLUDENTES` já tinha."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX CAT\FLEXX CAT 42\Boletim FLEXX CAT 42.pdf",
            "FLEXX CAT 42 é um catalisador organometálico.",
        )
    ]
    resultado = _listar(pontos, "catalisadores")
    assert resultado["nivel_atendido"] == "identidade_declarada"
    assert resultado["niveis"]["identidade_declarada"]["total"] == 1


def test_familia_auxiliar_continua_excluida_de_outra_natureza():
    """O escape não pode virar porta dos fundos: perguntar por elastômero
    continua sem trazer catalisador."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX CAT\FLEXX CAT 42\Boletim FLEXX CAT 42.pdf",
            "Indicado para produção de elastômero de alta resiliência.",
        )
    ]
    assert _listar(pontos, "elastomero")["niveis"]["composicao_comprovada"]["total"] == 0


@pytest.mark.parametrize(
    "texto",
    [
        "Este produto adesivo deve ser armazenado em local seco.",
        "Produto novo da linha, disponível a partir de março.",
    ],
)
def test_frase_nominal_generica_nao_e_prova_de_composicao(texto):
    """O padrão aceitava "produto|material|peça <termo>", que em boletim é
    boilerplate. "Este PRODUTO ADESIVO deve ser armazenado" virava composição
    comprovada — e, pior, identidade sendo rotulada com o texto do nível de
    composição, que diz ao vendedor o contrário do que a fonte afirma."""
    caminho = rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf"
    assert _conteudo_comprova_composicao_do_termo("adesivo", caminho, texto) is False
    assert _conteudo_comprova_composicao_do_termo("novo", caminho, texto) is False


def test_sistema_e_poliuretano_continuam_provando_composicao():
    """A restrição acima não pode matar a forma que o boletim realmente usa."""
    caminho = rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf"
    for texto in ("Sistema elastomérico bicomponente.", "Poliuretano elastomérico de alta dureza."):
        assert _conteudo_comprova_composicao_do_termo("elastomero", caminho, texto) is True


# --- a evidência citável: Bloco 1 chegando à cascata (18/09/2026) -----------
#
# O Bloco 1 da avaliação de arquitetura exige que toda afirmação técnica venha
# com o documento de origem e o trecho literal, porque o usuário-alvo é um
# vendedor que NÃO conhece os produtos e precisa conferir sozinho.
#
# Este caminho violava isso da pior forma possível: dizia "o Boletim Técnico do
# próprio produto declara que ele é isso" — a afirmação mais forte do motor —
# e devolvia `sources: []`. O filepath e o content já estavam em mãos no exato
# ponto da aceitação; a evidência era calculada e jogada fora.


def _evidencia(resultado, nivel, produto):
    return (resultado["niveis"][nivel].get("evidencias") or {}).get(produto)


def test_identidade_declarada_cita_documento_e_trecho_literal():
    conteudo = (
        "Vantagens: alta resiliencia. FLEXX EL 1000 é um elastômero de alta "
        "resiliência para peças técnicas. Armazenar em local seco."
    )
    pontos = [
        _ponto(rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf", conteudo)
    ]
    resultado = _listar(pontos, "elastomero")

    evidencia = _evidencia(resultado, "identidade_declarada", "FLEXX EL 1000")
    assert evidencia["documento"] == "Boletim FLEXX EL 1000.pdf"
    assert evidencia["tipo_de_prova"] == "textual"
    # LITERAL, não parafraseado: o vendedor confere o trecho contra o PDF.
    assert evidencia["trecho"].strip("…") in conteudo
    # E é o trecho que DECIDIU, não uma frase qualquer do documento.
    assert "é um elastômero" in evidencia["trecho"]


def test_composicao_comprovada_cita_o_trecho_que_a_comprovou():
    conteudo = (
        "Descrição: sistema bicomponente para obtenção de elastômeros de "
        "dureza média. Validade: 6 meses."
    )
    pontos = [
        _ponto(rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf", conteudo)
    ]
    resultado = _listar(pontos, "elastomero")

    evidencia = _evidencia(resultado, "composicao_comprovada", "FLEXX EL 1000")
    assert evidencia["trecho"].strip("…") in conteudo
    assert "obtenção de elastômeros" in evidencia["trecho"]


def test_mencao_cita_o_trecho_e_nao_apenas_o_nome_do_arquivo():
    """O nível mais fraco é o que MAIS precisa do trecho: "apareceu no
    documento" sem mostrar onde não dá ao vendedor como julgar se a menção era
    uso, comparação ou restrição."""
    conteudo = "Compatível com peças de elastômero vizinhas na montagem."
    pontos = [
        _ponto(rf"{RAIZ}\FLEXX AG\FLEXX AG 2032\Boletim FLEXX AG 2032.pdf", conteudo)
    ]
    resultado = _listar(pontos, "elastomero")

    evidencia = _evidencia(resultado, "mencao_no_documento", "FLEXX AG 2032")
    assert evidencia["documento"] == "Boletim FLEXX AG 2032.pdf"
    assert evidencia["trecho"].strip("…") in conteudo
    assert "elastômero" in evidencia["trecho"]


def test_classificacao_estrutural_cita_a_classificacao_e_nao_inventa_trecho():
    """A prova aqui é o CAMINHO NA ÁRVORE do catálogo, não uma frase. Atribuir
    ao boletim uma citação que ele não tem seria pior que não citar nada —
    e este é justamente o nível apresentado como a evidência MAIS FORTE."""
    pontos = [
        _ponto(
            rf"{RAIZ}\Elastômeros\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "Sistema bicomponente para obtenção de elastômeros.",
        )
    ]
    resultado = _listar(pontos, "elastômeros")
    assert resultado["nivel_atendido"] == "classificacao_estrutural"

    evidencia = _evidencia(resultado, "classificacao_estrutural", "FLEXX EL 1000")
    assert evidencia["tipo_de_prova"] == "estrutural"
    assert evidencia["classificacao"] == "Elastômeros"
    assert evidencia["documento"] == "Boletim FLEXX EL 1000.pdf"
    assert "trecho" not in evidencia


def test_uma_evidencia_por_produto_por_nivel_a_primeira_que_aparecer():
    """A varredura passa por ~11.000 pontos e um produto pode ser aceito por
    dezenas deles. Guardar todas multiplicaria memória por nada: a resposta
    cita uma."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "FLEXX EL 1000 é um elastômero de alta resiliência.",
        ),
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000 rev2.pdf",
            "FLEXX EL 1000 é um elastômero revisado.",
        ),
    ]
    resultado = _listar(pontos, "elastomero")

    evidencias = resultado["niveis"]["identidade_declarada"]["evidencias"]
    assert list(evidencias) == ["FLEXX EL 1000"]
    assert evidencias["FLEXX EL 1000"]["documento"] == "Boletim FLEXX EL 1000.pdf"


def test_trecho_tem_teto_e_nao_devolve_o_chunk_inteiro():
    """Chunk no acervo real chega a 700 palavras. Um chunk inteiro por produto
    numa lista de dez é uma parede de texto, não uma citação conferível."""
    enchimento = "Informacao de praxe sobre armazenamento e manuseio. " * 80
    conteudo = (
        enchimento + "FLEXX EL 1000 é um elastômero de alta resiliência. " + enchimento
    )
    pontos = [
        _ponto(rf"{RAIZ}\FLEXX EL\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf", conteudo)
    ]
    resultado = _listar(pontos, "elastomero")

    trecho = _evidencia(resultado, "identidade_declarada", "FLEXX EL 1000")["trecho"]
    assert len(conteudo) > 4000
    # Teto + o comprimento da própria frase casada, com folga.
    assert len(trecho) < _LARGURA_TRECHO_EVIDENCIA + 200
    assert trecho.strip("…") in conteudo


def test_lista_completa_troca_o_trecho_pelo_nome_do_documento():
    """DECISÃO DE ORÇAMENTO. A prévia traz trecho literal; `listar_todos` pode
    devolver centenas de produtos, e centenas de citações não são conferíveis.
    Na lista completa fica o documento, suficiente para abrir o PDF."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL {n}\Boletim FLEXX EL {n}.pdf",
            f"FLEXX EL {n} é um elastômero de alta resiliência.",
        )
        for n in range(1000, 1015)
    ]
    previa = _listar(pontos, "elastomero")
    completa = _listar(pontos, "elastomero", listar_todos=True)

    nivel_previa = previa["niveis"]["identidade_declarada"]
    nivel_completa = completa["niveis"]["identidade_declarada"]

    # A semântica da cascata não muda: os mesmos 15 produtos entram.
    assert nivel_previa["total"] == nivel_completa["total"] == 15
    assert len(nivel_previa["produtos"]) == 10
    assert len(nivel_previa["evidencias"]) == 10
    assert all(e.get("trecho") for e in nivel_previa["evidencias"].values())

    assert len(nivel_completa["produtos"]) == 15
    assert len(nivel_completa["evidencias"]) == 15
    assert all("trecho" not in e for e in nivel_completa["evidencias"].values())
    assert all(e["documento"] for e in nivel_completa["evidencias"].values())


def test_evidencia_acompanha_apenas_os_produtos_realmente_listados():
    """Prévia de 10 não pode carregar evidência dos 15 — o payload vai para o
    LLM via MCP e pagar contexto por produto que não aparece é desperdício."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX EL\FLEXX EL {n}\Boletim FLEXX EL {n}.pdf",
            f"FLEXX EL {n} é um elastômero.",
        )
        for n in range(1000, 1015)
    ]
    nivel = _listar(pontos, "elastomero")["niveis"]["identidade_declarada"]
    assert set(nivel["evidencias"]) == set(nivel["produtos"])


def test_produto_excluido_do_nivel_nao_leva_evidencia_junto():
    """A exclusão de isocianato/auxiliares acontece DEPOIS da varredura. Se a
    evidência não respeitasse a lista final, a resposta citaria fonte de um
    produto que ela mesma não lista."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX ISO\FLEXX ISO 131001\Boletim FLEXX ISO 131001.pdf",
            "FLEXX ISO 131001 é um isocianato. Combinado com poliol, "
            "permite obtenção de elastômero.",
        )
    ]
    nivel = _listar(pontos, "elastomero")["niveis"]["composicao_comprovada"]
    assert nivel["total"] == 0
    assert nivel["evidencias"] == {}


# --- alias de negócio: o nome que as pessoas usam x o código do acervo -------

def test_elastomero_resolve_na_linha_th_do_catalogo():
    """Confirmado pelo usuário em 21/09/2026: TH é a linha de elastômeros.

    A pergunta não encontrava a linha porque no acervo ela se chama TH, e a
    resposta determinística listava as classificações reais — nenhuma delas
    "elastômero". O alias faz a pergunta de NATUREZA resolver no nível
    `classificacao_estrutural`, a evidência mais forte da cascata, em vez de
    cair para declaração de boletim ou menção no texto.
    """
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX® TH\FLEXX TH T160DE1\Boletim FLEXX TH T160DE1.pdf",
            "Sistema bicomponente para peças técnicas de reposição.",
        )
    ]
    resultado = _listar(pontos, "elastomeros")

    assert resultado["nivel_atendido"] == "classificacao_estrutural"
    assert resultado["niveis"]["classificacao_estrutural"]["total"] == 1
    assert "FLEXX® TH" in resultado["classificacoes_encontradas"]


def test_borracha_chega_na_linha_th_pela_traducao():
    """O alias cobre o termo técnico; a tradução leigo→técnico cobre o resto.
    "borracha" → sinônimo "elastômero" → alias → FLEXX TH. Sem isso, o termo do
    vendedor precisaria coincidir com o código corporativo do acervo."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX® TH\FLEXX TH T160DE1\Boletim FLEXX TH T160DE1.pdf",
            "Sistema bicomponente para peças técnicas de reposição.",
        )
    ]
    resultado = _listar(pontos, "borracha", sinonimos=["elastômero"])
    assert resultado["nivel_atendido"] == "classificacao_estrutural"


def test_termo_desconhecido_continua_devolvendo_zero():
    """O alias não pode virar porta aberta: a regra geral continua sendo
    correspondência exata, para termo desconhecido não virar uma lista de
    produtos apenas relacionados."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX® TH\FLEXX TH T160DE1\Boletim FLEXX TH T160DE1.pdf",
            "Sistema bicomponente para peças técnicas de reposição.",
        )
    ]
    resultado = _listar(pontos, "termo que nao existe")
    assert resultado["niveis"]["classificacao_estrutural"]["total"] == 0


def test_alias_soma_a_descoberta_dinamica_em_vez_de_esconde_la():
    """Enquanto o alias dava `return` antecipado, cadastrar "elastomero →
    flexx th" ESCONDIA uma pasta chamada literalmente "Elastômeros": o atalho
    de negócio passava na frente da fonte de verdade estrutural."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX® TH\FLEXX TH T160DE1\Boletim FLEXX TH T160DE1.pdf",
            "Sistema bicomponente para peças técnicas.",
        ),
        _ponto(
            rf"{RAIZ}\Elastômeros\FLEXX EL 1000\Boletim FLEXX EL 1000.pdf",
            "Sistema bicomponente para peças técnicas.",
        ),
    ]
    resultado = _listar(pontos, "elastomeros")

    assert resultado["nivel_atendido"] == "classificacao_estrutural"
    encontradas = set(resultado["classificacoes_encontradas"])
    assert "FLEXX® TH" in encontradas, "o alias de negócio sumiu"
    assert "Elastômeros" in encontradas, "a descoberta dinâmica foi escondida pelo alias"
