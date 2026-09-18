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
