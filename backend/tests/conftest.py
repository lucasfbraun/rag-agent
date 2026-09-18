"""
Configuração compartilhada da suíte.

DESLIGAR A EXPANSÃO DE CONSULTA POR PADRÃO NOS TESTES

`retrieve_products_context` passou a traduzir a pergunta leiga para o
vocabulário do acervo antes de buscar (app/rag/query_expansion.py). A tradução
é uma chamada de rede ao LLM. Sem esta trava, TODO teste que exercita a
recuperação — dezenas deles, a maioria sem nenhuma relação com tradução —
passaria a tentar sair para a internet a cada execução.

Isso não quebraria a suíte (o módulo é fail-open: falha vira lista vazia), e é
justamente esse o problema: quebraria em silêncio, trocando segundos por
minutos de espera e gastando cota de API para produzir exatamente o mesmo
resultado do caminho desligado.

Quem TESTA a tradução liga explicitamente — seja injetando um stub em
`app.rag.engine.expandir_termos_do_dominio`, seja voltando a flag com
`patch.object`. Ligar de propósito é barato; desligar setenta vezes não seria.
"""
import pytest


@pytest.fixture(autouse=True)
def expansao_de_consulta_desligada_por_padrao():
    from unittest.mock import patch

    from app.rag import query_expansion

    with patch.object(query_expansion, "EXPANSAO_CONSULTA_ATIVA", False):
        yield
