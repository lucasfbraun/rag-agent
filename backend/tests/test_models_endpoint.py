"""
`GET /api/models` — a lista de modelos de chat passa a ser servida pelo
backend, em vez de duplicada à mão no selectbox do frontend.

Por que existe: a duplicação cobrou o preço que a própria docstring de
`app.config` previa. Remover os modelos Ollama de `ALLOWED_CHAT_MODELS` sem
reconstruir a imagem do frontend deixou a tela oferecendo `ollama/qwen2.5:3b`
— primeiro item da lista, portanto o selecionado por padrão — e o servidor
passou a recusar com `422 Unprocessable Entity`. Para quem estava usando, toda
pergunta simplesmente falhava, sem nenhuma pista do motivo.

O que estes testes protegem é a invariante que impede a repetição: a lista
oferecida e a lista aceita são a MESMA coisa, derivada de uma fonte só.
"""
import pytest
from fastapi.testclient import TestClient

from app.config import ALLOWED_CHAT_MODELS, DEFAULT_CHAT_MODEL, MODELOS_DE_CHAT_EM_ORDEM
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_allowlist_e_derivada_da_lista_ordenada_nao_escrita_em_paralelo():
    """A invariante central. Se alguém voltar a manter as duas à mão, elas
    divergem de novo — e o sintoma é a tela quebrar por 422."""
    assert set(MODELOS_DE_CHAT_EM_ORDEM) == set(ALLOWED_CHAT_MODELS)
    assert len(MODELOS_DE_CHAT_EM_ORDEM) == len(ALLOWED_CHAT_MODELS), (
        "há modelo repetido na lista ordenada"
    )


def test_modelo_padrao_esta_entre_os_oferecidos():
    """`MatchRequest.model_name` usa DEFAULT_CHAT_MODEL como default e valida
    contra a allowlist: um default fora dela faria toda requisição sem
    `model_name` explícito falhar com 422."""
    assert DEFAULT_CHAT_MODEL in ALLOWED_CHAT_MODELS


def test_nenhum_modelo_ollama_sobrou():
    """O Ollama saiu do projeto em 2026-09-09 (decisão do usuário: só motores
    pagos). Um resquício aqui voltaria a oferecer na tela um modelo que não
    tem backend nenhum atrás."""
    assert not [m for m in MODELOS_DE_CHAT_EM_ORDEM if m.startswith("ollama/")]


def test_endpoint_exige_autenticacao(client):
    assert client.get("/api/models").status_code == 401


def test_endpoint_devolve_a_lista_na_ordem_e_o_padrao(client, monkeypatch):
    """A ordem importa: é a ordem do seletor, e o primeiro item é o que o
    usuário pega sem escolher nada."""
    from app.auth import dependencies
    from app.models import Role, User

    usuario = User(username="v", nome="V", email="v@x.com", perfil=Role.VENDEDOR)
    app.dependency_overrides[dependencies.get_current_user] = lambda: usuario
    try:
        resposta = client.get("/api/models")
    finally:
        app.dependency_overrides.clear()

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["models"] == list(MODELOS_DE_CHAT_EM_ORDEM)
    assert corpo["default"] == DEFAULT_CHAT_MODEL


def test_todo_modelo_oferecido_e_aceito_por_match_request():
    """O contrato completo em uma asserção: se a tela oferece, o servidor
    aceita. É exatamente isto que estava quebrado."""
    from app.main import MatchRequest

    for modelo in MODELOS_DE_CHAT_EM_ORDEM:
        MatchRequest(query="teste", model_name=modelo)  # não levanta
