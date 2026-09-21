"""
Quem valida não é quem pergunta (Sessão 58).

O valor do ciclo de validação depende inteiramente disto: a resposta certa vem
da Qualidade / Engenharia de Aplicação, não do vendedor leigo que fez a
pergunta. Se `VIEW_CATALOG` bastasse para julgar, o conjunto de avaliação
mediria a sensação de quem não conhece o catálogo — exatamente o problema que
o item 0 de docs/avaliacao_arquitetura_2026-09-18.md descreve.

O modo de falha real aqui é o esquecimento: rota nova acrescentada ao router
sem `require_permission`. Não quebra teste nenhum, não aparece na tela, e
expõe a fila inteira a qualquer usuário autenticado. Por isso o teste varre as
ROTAS, em vez de conferir uma por uma à mão.

Seam: inspeção das dependências declaradas pelo FastAPI. Sem banco, sem token.
"""
import uuid
from unittest.mock import MagicMock

import pytest

from app.auth.permissions import Permission
from app.validacao_service import MensagemNaoAvaliavelError, registrar_veredito
from app.models import Veredito


def _rotas_de_validacao():
    """As rotas declaradas pelo router, lidas do próprio módulo.

    Não se varre `app.router.routes`: o FastAPI desta versão guarda cada
    `include_router` como um nó opaco e não achata as sub-rotas, então a
    varredura voltaria vazia e todo teste abaixo passaria sem verificar nada.
    Que o router esteja de fato montado é o que
    `test_router_de_validacao_esta_montado_em_main` confere, por outro
    caminho."""
    from app.validacao_router import router

    return list(router.routes)


def _permissoes_exigidas(rota) -> set[str]:
    """As permissões que as dependencies da rota checam.

    `require_permission` devolve um closure; a permissão fica na sua célula de
    fechamento. Ler dali é feio, mas é o único jeito de descobrir o que a rota
    exige sem subir um servidor e um banco — e o teste que ninguém consegue
    rodar é o teste que não protege nada."""
    encontradas = set()
    for dependencia in rota.dependant.dependencies:
        chamavel = dependencia.call
        for celula in getattr(chamavel, "__closure__", None) or ():
            valor = celula.cell_contents
            if isinstance(valor, Permission):
                encontradas.add(valor.value)
    return encontradas


def test_existe_rota_de_validacao_registrada():
    """Se o router ficasse vazio, os testes abaixo passariam sem verificar
    nada — parametrização sobre lista vazia não falha, só some."""
    assert _rotas_de_validacao(), "o router de validação não declara rota nenhuma"


def test_router_de_validacao_esta_montado_em_main():
    """Um router que existe mas ninguém inclui é um recurso que não existe."""
    from app.main import app

    caminhos = [p for p in app.openapi()["paths"] if p.startswith("/api/validacao")]
    assert "/api/validacao/fila" in caminhos
    assert "/api/validacao/relatorio" in caminhos


@pytest.mark.parametrize("rota", _rotas_de_validacao(), ids=lambda r: r.path)
def test_toda_rota_de_validacao_exige_a_permissao_tecnica(rota):
    exigidas = _permissoes_exigidas(rota)
    assert Permission.VALIDATE_ANSWERS.value in exigidas, (
        f"{rota.path} não exige {Permission.VALIDATE_ANSWERS.value}"
    )


@pytest.mark.parametrize("rota", _rotas_de_validacao(), ids=lambda r: r.path)
def test_nenhuma_rota_de_validacao_se_contenta_com_permissao_de_vendedor(rota):
    """`VIEW_CATALOG` é o que o vendedor leigo tem. Aceitá-la aqui devolveria
    o julgamento a quem, por definição, não sabe a resposta certa."""
    assert Permission.VIEW_CATALOG.value not in _permissoes_exigidas(rota)


def test_a_permissao_de_validar_e_distinta_da_de_treinar():
    """São riscos de tamanhos diferentes: `approve_training` decide o que o
    agente vai REPETIR como verdade; validar só registra medição, e nada do
    que ela grava volta para o prompt."""
    assert Permission.VALIDATE_ANSWERS is not Permission.APPROVE_TRAINING
    assert Permission.VALIDATE_ANSWERS.value == "validate_answers"


# ---------------------------------------------------------------------------
# O alvo do veredito precisa ser uma resposta do agente
# ---------------------------------------------------------------------------

def test_nao_da_para_julgar_uma_pergunta_do_vendedor():
    """Julgar a mensagem do usuário não mede nada — e contaminaria a taxa de
    acerto com linhas que não dizem respeito ao motor."""
    session = MagicMock()
    session.get.return_value = MagicMock(role="user")

    with pytest.raises(MensagemNaoAvaliavelError):
        registrar_veredito(
            session,
            mensagem_id=uuid.uuid4(),
            avaliador=MagicMock(id=uuid.uuid4()),
            veredito=Veredito.CORRETA,
        )
    session.commit.assert_not_called()


def test_mensagem_inexistente_nao_vira_veredito_orfao_novo():
    session = MagicMock()
    session.get.return_value = None

    with pytest.raises(MensagemNaoAvaliavelError):
        registrar_veredito(
            session,
            mensagem_id=uuid.uuid4(),
            avaliador=MagicMock(id=uuid.uuid4()),
            veredito=Veredito.INCORRETA,
        )
    session.add.assert_not_called()
    session.commit.assert_not_called()
