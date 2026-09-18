"""
Bloco 2 da correção de arquitetura (2026-09-18): tradução da pergunta leiga
para o vocabulário real dos Boletins Técnicos antes da recuperação.

O sintoma que originou isto: a busca por palavra-chave casa texto LITERAL no
campo `content`. Quem pergunta "tem cola pra colchão?" não bate em nada, porque
o boletim escreve "adesivo" e "espuma flexível". O caminho morre em silêncio —
sem exceção, sem log — e sobra só a busca vetorial, a mais fraca do motor.

Estes testes cobrem o módulo de tradução isoladamente. A integração com o
retriever está em `test_query_expansion_no_retriever.py`.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag import query_expansion
from app.rag.query_expansion import (
    _extrair_lista_json,
    _limpar_termos,
    expandir_termos_do_dominio,
    limpar_cache_de_expansao,
    termos_expandidos_em_cache,
)


@pytest.fixture(autouse=True)
def _expansao_ligada_e_cache_limpo():
    """Este é o módulo que TESTA a tradução, então ele religa a flag que o
    conftest desliga para o resto da suíte. O cache é de processo: sem limpá-lo
    entre os casos, um teste contamina o seguinte."""
    limpar_cache_de_expansao()
    with patch.object(query_expansion, "EXPANSAO_CONSULTA_ATIVA", True):
        yield
    limpar_cache_de_expansao()


def _resposta_do_modelo(conteudo: str):
    resposta = MagicMock()
    resposta.choices = [MagicMock()]
    resposta.choices[0].message.content = conteudo
    return resposta


# --- leitura da resposta do modelo -----------------------------------------

def test_le_array_json_puro():
    assert _extrair_lista_json('["adesivo", "espuma flexivel"]') == ["adesivo", "espuma flexivel"]


def test_le_array_dentro_de_cerca_markdown():
    """Modelos devolvem ```json com frequência; não usamos response_format
    porque nem todo provedor da allowlist aceita o parâmetro."""
    bruto = 'Claro!\n```json\n["adesivo", "laminacao"]\n```\n'
    assert _extrair_lista_json(bruto) == ["adesivo", "laminacao"]


@pytest.mark.parametrize("bruto", ["", None, "não sei responder", "{}", "[não é json]"])
def test_resposta_ilegivel_vira_lista_vazia(bruto):
    """Fail-open: resposta ruim degrada para "sem tradução", nunca quebra."""
    assert _extrair_lista_json(bruto) == []


def test_ignora_itens_que_nao_sao_texto():
    assert _extrair_lista_json('["adesivo", 42, null, {"a": 1}]') == ["adesivo"]


# --- limpeza dos termos -----------------------------------------------------

def test_descarta_termos_genericos_demais():
    """"poliuretano" aparece em todo documento do acervo: como candidato de
    busca ele enche o lote do scroll e empurra o trecho certo para fora."""
    limpos = _limpar_termos(["adesivo", "poliuretano", "sistema", "produto"], "tem cola?")
    assert limpos == ["adesivo"]


def test_descarta_palavra_que_ja_estava_na_pergunta():
    """Repetir a palavra do usuário não amplia busca nenhuma — ela já foi
    pesquisada por `_extrair_palavras_chave`."""
    limpos = _limpar_termos(["colchao", "espuma flexível"], "preciso de algo para colchão")
    assert "colchao" not in limpos
    assert limpos == ["espuma flexível"]


def test_descarta_termo_longo_demais():
    """O modelo às vezes devolve uma frase inteira em vez de um termo."""
    frase = "adesivo poliuretano bicomponente para laminacao de espuma"
    assert _limpar_termos([frase], "cola") == []


def test_preserva_acento_porque_o_acervo_tem_acento():
    """REGRESSÃO REAL (achada na revisão de 18/09): a primeira versão devolvia
    "elastomero", "laminacao", "flexivel" — e nenhum deles casava com nada.

    O texto indexado é o texto cru do boletim, com acento (`payload["content"]`
    em ingestion.py). As duas pontas que consomem estes termos comparam sem
    remover acento: `engine._pontuacao` faz `palavra in content.lower()`, e o
    índice MatchText do Qdrant é criado com `lowercase=True` mas SEM ascii
    folding. Tirar o acento aqui zerava o recall do bloco inteiro.
    """
    limpos = _limpar_termos(["Elastômero", "INJEÇÃO"], "borracha")
    assert limpos == ["elastômero", "injeção"]

    conteudo_do_boletim = "Resina de elastômero para injeção em molde fechado".lower()
    for termo in limpos:
        assert termo in conteudo_do_boletim, f"{termo!r} não casaria com o acervo"


def test_termo_de_varias_palavras_atravessa_inteiro():
    """A primeira versão achatava "espuma flexível" em "espuma" + "flexível".
    "espuma" ocorre em quase todo boletim: promovê-la a candidato próprio
    enchia o lote de `scroll` com o ruído que a stoplist existe para evitar."""
    assert _limpar_termos(["espuma flexível"], "colchão") == ["espuma flexível"]


def test_termo_composto_sobrevive_quando_so_parte_dele_e_generica():
    """"sistema de vazamento" é exemplo literal do prompt. Quebrado, virava o
    fragmento órfão "vazamento" — "sistema" cai na stoplist e "de" é curto
    demais para o índice."""
    assert _limpar_termos(["sistema de vazamento"], "enchimento") == ["sistema de vazamento"]


def test_termo_composto_so_de_palavras_genericas_e_descartado():
    assert _limpar_termos(["sistema de poliuretano"], "cola") == []


def test_descarta_token_curto_demais_para_o_indice():
    """`content` é indexado com min_token_len=3: termo de 1-2 letras não é
    pesquisável e só gastaria uma ida-e-volta no Qdrant."""
    assert _limpar_termos(["pu", "ab", "elastomero"], "borracha") == ["elastomero"]


def test_nao_repete_termo():
    assert _limpar_termos(["adesivo", "adesivo", "Adesivo"], "cola") == ["adesivo"]


# --- chamada ao modelo ------------------------------------------------------

def test_expande_pergunta_leiga_em_termos_do_acervo():
    with patch.object(
        query_expansion.litellm, "completion",
        return_value=_resposta_do_modelo('["adesivo", "espuma flexível", "laminação"]'),
    ):
        termos = expandir_termos_do_dominio("tem cola pra colchão?")
    assert termos == ["adesivo", "espuma flexível", "laminação"]


def test_chamada_ao_modelo_tem_timeout():
    """Sem timeout, o padrão do litellm é 6000 s. Esta chamada é síncrona e
    fica NA FRENTE de toda a recuperação: um provedor lento — não com erro,
    lento — penduraria a pergunta do vendedor por minutos antes de o fail-open
    agir. O fail-open cobre falha, não lentidão."""
    with patch.object(
        query_expansion.litellm, "completion",
        return_value=_resposta_do_modelo("[]"),
    ) as completion:
        expandir_termos_do_dominio("tem cola pra colchão?")

    timeout = completion.call_args.kwargs.get("timeout")
    assert timeout is not None, "chamada de tradução sem timeout"
    assert 0 < timeout <= 30


def test_defeito_na_limpeza_tambem_e_fail_open():
    """A docstring promete "nunca levanta exceção". Antes, o try/except cobria
    só a chamada de rede, e um defeito em `_limpar_termos` escapava do
    módulo — promessa maior que o código."""
    with patch.object(
        query_expansion.litellm, "completion",
        return_value=_resposta_do_modelo('["adesivo"]'),
    ), patch.object(
        query_expansion, "_limpar_termos", side_effect=RuntimeError("defeito na limpeza")
    ):
        assert expandir_termos_do_dominio("tem cola pra colchão?") == []


def test_falha_do_modelo_nao_levanta_e_devolve_vazio():
    """Disciplina central do módulo: expansão é ganho de recall, nunca uma
    dependência. Com o LLM fora do ar o motor volta ao comportamento antigo."""
    with patch.object(
        query_expansion.litellm, "completion", side_effect=RuntimeError("502 upstream")
    ):
        assert expandir_termos_do_dominio("tem cola pra colchão?") == []


def test_desligado_por_configuracao_nao_chama_o_modelo():
    """A chave existe para comparar o motor com e sem tradução sobre as mesmas
    perguntas, sem reverter código."""
    with patch.object(query_expansion, "EXPANSAO_CONSULTA_ATIVA", False), \
         patch.object(query_expansion.litellm, "completion") as completion:
        assert expandir_termos_do_dominio("tem cola pra colchão?") == []
    completion.assert_not_called()


def test_respeita_o_teto_de_termos():
    """Cada termo vira um scroll próprio no Qdrant — o teto é o custo."""
    muitos = '["adesivo","elastômero","laminação","injeção","solado","painel","verniz","selante"]'
    with patch.object(query_expansion, "EXPANSAO_MAX_TERMOS", 3), \
         patch.object(query_expansion.litellm, "completion",
                      return_value=_resposta_do_modelo(muitos)):
        assert len(expandir_termos_do_dominio("tem cola?")) == 3


def test_teto_conta_termos_e_nao_palavras():
    """Enquanto os termos compostos eram quebrados, seis termos de duas
    palavras viravam doze entradas e o corte deixava passar só TRÊS conceitos
    — um terço do que a configuração promete."""
    compostos = (
        '["espuma flexível","espuma rígida","sistema de vazamento",'
        '"adesivo estrutural","solado injetado","painel isolante"]'
    )
    with patch.object(query_expansion, "EXPANSAO_MAX_TERMOS", 6), \
         patch.object(query_expansion.litellm, "completion",
                      return_value=_resposta_do_modelo(compostos)):
        termos = expandir_termos_do_dominio("preciso de algo macio pra estofado")

    assert len(termos) == 6
    assert "espuma flexível" in termos
    assert "painel isolante" in termos


def test_pergunta_repetida_nao_chama_o_modelo_de_novo():
    with patch.object(
        query_expansion.litellm, "completion",
        return_value=_resposta_do_modelo('["adesivo"]'),
    ) as completion:
        expandir_termos_do_dominio("tem cola pra colchão?")
        expandir_termos_do_dominio("  TEM COLA PRA COLCHÃO?  ")
    assert completion.call_count == 1


def test_cache_tem_teto():
    """uvicorn é processo longo: sem teto o cache cresceria sem fim."""
    with patch.object(query_expansion, "_LIMITE_DO_CACHE", 3), \
         patch.object(query_expansion.litellm, "completion",
                      return_value=_resposta_do_modelo('["adesivo"]')):
        for i in range(5):
            expandir_termos_do_dominio(f"pergunta {i}")
    assert len(query_expansion._cache) <= 3


def test_leitura_do_cache_nunca_chama_o_modelo():
    """`_montar_context_str` usa esta função para avisar o agente sobre a
    tradução; ela não pode custar uma segunda chamada nem disparar uma."""
    with patch.object(query_expansion.litellm, "completion") as completion:
        assert termos_expandidos_em_cache("pergunta nunca vista") == []
    completion.assert_not_called()


def test_leitura_do_cache_devolve_o_que_a_recuperacao_usou():
    with patch.object(
        query_expansion.litellm, "completion",
        return_value=_resposta_do_modelo('["adesivo", "laminacao"]'),
    ):
        expandir_termos_do_dominio("tem cola pra colchão?")
    assert termos_expandidos_em_cache("tem cola pra colchão?") == ["adesivo", "laminacao"]


def test_pergunta_vazia_nao_chama_o_modelo():
    with patch.object(query_expansion.litellm, "completion") as completion:
        assert expandir_termos_do_dominio("   ") == []
    completion.assert_not_called()
