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
    limpos = _limpar_termos(["colchao", "espuma flexivel"], "preciso de algo para colchão")
    assert "colchao" not in limpos
    assert "espuma" in limpos and "flexivel" in limpos


def test_descarta_termo_longo_demais():
    """O modelo às vezes devolve uma frase inteira em vez de um termo."""
    frase = "adesivo poliuretano bicomponente para laminacao de espuma"
    assert _limpar_termos([frase], "cola") == []


def test_normaliza_acento_e_caixa():
    """O índice de texto do Qdrant é minúsculo; a comparação em
    `_pontuacao` é feita sobre `content.lower()`."""
    assert _limpar_termos(["Elastômero", "INJEÇÃO"], "borracha") == ["elastomero", "injecao"]


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
        return_value=_resposta_do_modelo('["adesivo", "espuma flexivel", "laminacao"]'),
    ):
        termos = expandir_termos_do_dominio("tem cola pra colchão?")
    assert termos == ["adesivo", "espuma", "flexivel", "laminacao"]


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
    muitos = '["adesivo","elastomero","laminacao","injecao","solado","painel","verniz","selante"]'
    with patch.object(query_expansion, "EXPANSAO_MAX_TERMOS", 3), \
         patch.object(query_expansion.litellm, "completion",
                      return_value=_resposta_do_modelo(muitos)):
        assert len(expandir_termos_do_dominio("tem cola?")) == 3


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
