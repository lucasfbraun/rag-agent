"""
Treinamento do agente (Sessão 38, item 4) — ver docs/spec_treinamento.md.

Três modalidades com papéis diferentes, e é a diferença entre elas que estes
testes protegem:

  - **correção** afirma qual é a resposta certa → passa por aprovação;
  - **conhecimento** afirma um fato da empresa → passa por aprovação;
  - **exemplo** ensina só a FORMA → entra direto.

A assimetria não é arbitrária: os dois primeiros viram coisas que o agente
repete como verdade, e um erro ali circula sem ninguém notar. Forma ruim é
visível na primeira resposta.

Os modos de falha cobertos, que são os que realmente causam dano:
  1. item pendente influenciando resposta (só aprovado vai ao índice);
  2. item aprovado que NÃO está no índice (falha ao indexar desfaz a aprovação);
  3. exemplo apresentado como fonte de fato — o prompt tem de dizer que os
     números dele não valem;
  4. orientação interna apresentada como se fosse conteúdo de boletim.

Seam: Postgres real; o índice (`app.rag.treinamento`) é mockado — ele é
exercitado à parte, e indexar de verdade a cada teste custaria uma chamada de
embedding por item.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.auth.permissions import Permission, has_permission
from app.auth.user_service import create_user
from app.db import SessionLocal
from app.models import ItemTreinamento, StatusDocumento, TipoTreinamento, User
from app.treinamento_service import (
    DecisaoInvalidaError,
    ItemInvalidoError,
    aprovar,
    criar,
    excluir,
    listar,
    recusar,
)


@pytest.fixture
def session():
    s = SessionLocal()
    lixo = {"usuarios": [], "itens": []}
    try:
        with patch("app.rag.treinamento.indexar"), patch("app.rag.treinamento.remover"):
            yield s, lixo
    finally:
        s.rollback()
        for item_id in lixo["itens"]:
            registro = s.get(ItemTreinamento, item_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        for user_id in lixo["usuarios"]:
            registro = s.get(User, user_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        s.close()


def _usuario(s, lixo, perfil="vendedor"):
    sufixo = uuid.uuid4().hex[:8]
    user = create_user(
        s, username=f"treino.{sufixo}", nome=f"Treinador {sufixo}",
        email=f"treino.{sufixo}@grupoflexivel.com.br",
        password="SenhaLocal123", perfil=perfil,
    )
    s.commit()
    lixo["usuarios"].append(user.id)
    return user


def _criar(s, lixo, autor, tipo, pergunta="Qual cola usar para rolha de cortiça?",
           resposta="O FLEXX AG 2066, conforme o boletim dele."):
    item = criar(s, autor=autor, tipo=tipo, pergunta=pergunta, resposta=resposta)
    s.commit()
    lixo["itens"].append(item.id)
    return item


# --- a assimetria entre as modalidades --------------------------------------

@pytest.mark.parametrize("tipo", [TipoTreinamento.CORRECAO, TipoTreinamento.CONHECIMENTO])
def test_correcao_e_conhecimento_nascem_pendentes(session, tipo):
    """Os dois afirmam FATOS que o agente repete como verdade da empresa. Um
    erro ali circula sem ninguém notar."""
    s, lixo = session
    item = _criar(s, lixo, _usuario(s, lixo), tipo)
    assert item.status == StatusDocumento.PENDENTE


def test_exemplo_entra_direto(session):
    """Exemplo afeta só a FORMA, e forma ruim é visível na primeira resposta —
    não justifica a fricção de uma fila."""
    s, lixo = session
    item = _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.EXEMPLO)
    assert item.status == StatusDocumento.APROVADO
    assert item.decidido_em is not None


def test_pendente_nao_vai_para_o_indice(session):
    """O elo que faz a fila valer alguma coisa: se pendente fosse indexado, a
    aprovação seria decorativa."""
    s, lixo = session
    with patch("app.rag.treinamento.indexar") as indexar:
        _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.CORRECAO)
    indexar.assert_not_called()


def test_exemplo_vai_para_o_indice_na_criacao(session):
    s, lixo = session
    with patch("app.rag.treinamento.indexar") as indexar:
        _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.EXEMPLO)
    indexar.assert_called_once()


# --- aprovação --------------------------------------------------------------

def test_aprovar_indexa_e_registra_quem_decidiu(session):
    s, lixo = session
    autor = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    item = _criar(s, lixo, autor, TipoTreinamento.CORRECAO)

    with patch("app.rag.treinamento.indexar") as indexar:
        aprovar(s, item.id, aprovador=aprovador)
    s.commit()

    assert item.status == StatusDocumento.APROVADO
    assert item.decidido_por_id == aprovador.id
    indexar.assert_called_once()


def test_falha_ao_indexar_desfaz_a_aprovacao(session):
    """Um item marcado como aprovado que não está no índice é PIOR que um
    pendente: aparece como ativo na tela e não influencia resposta nenhuma,
    sem que ninguém perceba."""
    s, lixo = session
    autor = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    item = _criar(s, lixo, autor, TipoTreinamento.CONHECIMENTO)

    with patch("app.rag.treinamento.indexar", side_effect=RuntimeError("qdrant fora")):
        with pytest.raises(DecisaoInvalidaError, match="não pôde ser indexado"):
            aprovar(s, item.id, aprovador=aprovador)

    assert item.status == StatusDocumento.PENDENTE
    assert item.decidido_por_id is None


def test_aprovar_duas_vezes_e_recusado(session):
    s, lixo = session
    autor = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    item = _criar(s, lixo, autor, TipoTreinamento.CORRECAO)

    with patch("app.rag.treinamento.indexar"):
        aprovar(s, item.id, aprovador=aprovador)
        s.commit()
        with pytest.raises(DecisaoInvalidaError, match="já foi"):
            aprovar(s, item.id, aprovador=aprovador)


# --- recusa e exclusão ------------------------------------------------------

def test_recusa_exige_motivo(session):
    s, lixo = session
    item = _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.CORRECAO)
    with pytest.raises(DecisaoInvalidaError, match="motivo"):
        recusar(s, item.id, aprovador=_usuario(s, lixo, "admin_ti"), motivo="  ")


def test_recusar_tira_do_indice(session):
    """Cobre o caso de recusar algo que já tinha sido aprovado antes."""
    s, lixo = session
    item = _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.EXEMPLO)  # nasce aprovado
    with patch("app.rag.treinamento.remover") as remover:
        recusar(s, item.id, aprovador=_usuario(s, lixo, "admin_ti"), motivo="forma ruim")
    s.commit()
    remover.assert_called_once_with(item.id)
    assert item.status == StatusDocumento.REJEITADO


def test_excluir_apaga_do_indice_e_do_banco(session):
    """Diferente de usuário, onde "excluir" é desativar para preservar
    histórico: um item de treinamento não carrega histórico de ninguém, e
    manter lixo curado atrapalha quem for revisar a base depois."""
    s, lixo = session
    item = _criar(s, lixo, _usuario(s, lixo), TipoTreinamento.EXEMPLO)
    item_id = item.id
    with patch("app.rag.treinamento.remover") as remover:
        excluir(s, item_id)
    s.commit()
    lixo["itens"].remove(item_id)
    remover.assert_called_once_with(item_id)
    assert s.get(ItemTreinamento, item_id) is None


# --- validação --------------------------------------------------------------

def test_item_curto_demais_e_recusado(session):
    """Um item de duas palavras não ensina nada e ainda ocupa espaço no
    contexto de toda consulta parecida."""
    s, lixo = session
    autor = _usuario(s, lixo)
    with pytest.raises(ItemInvalidoError):
        criar(s, autor=autor, tipo=TipoTreinamento.CONHECIMENTO,
              pergunta="oi", resposta="uma resposta suficientemente longa")
    with pytest.raises(ItemInvalidoError):
        criar(s, autor=autor, tipo=TipoTreinamento.CONHECIMENTO,
              pergunta="uma pergunta suficientemente longa", resposta="ok")


# --- quem vê o quê ----------------------------------------------------------

def test_listagem_filtra_por_autor(session):
    """Sem o recorte, alguém enxergaria correções de colegas ainda não
    revisadas — conteúdo que a empresa ainda não validou."""
    s, lixo = session
    um, outro = _usuario(s, lixo), _usuario(s, lixo)
    _criar(s, lixo, um, TipoTreinamento.CORRECAO)
    _criar(s, lixo, outro, TipoTreinamento.CORRECAO)

    do_um = listar(s, autor=um)
    assert all(i.criado_por_id == um.id for i in do_um)
    assert len(do_um) == 1


def test_permissoes_de_treinar_e_aprovar_sao_separadas(session):
    s, lixo = session
    vendedor = _usuario(s, lixo, "vendedor")
    admin = _usuario(s, lixo, "admin_ti")

    assert has_permission(vendedor, Permission.TRAIN_AGENT) is True
    assert has_permission(vendedor, Permission.APPROVE_TRAINING) is False
    assert has_permission(admin, Permission.APPROVE_TRAINING) is True


def test_aprovador_sem_permissao_de_criar_pode_acessar_a_fila():
    from app.treinamento_router import _require_acesso_treinamento

    perfil = MagicMock(nome="Aprovador")
    perfil.nomes_de_permissoes.return_value = {Permission.APPROVE_TRAINING.value}
    usuario = MagicMock(perfil=perfil)
    assert _require_acesso_treinamento(usuario) is usuario


# --- o bloco que vai para o prompt -----------------------------------------

def _payload(tipo, pergunta="Qual cola para cortiça?", resposta="FLEXX AG 2066."):
    return {"tipo": tipo, "pergunta": pergunta, "resposta": resposta,
            "autor": "Ana Silva", "data": "03/2026"}


def test_bloco_marca_exemplo_como_forma_e_nao_como_fato():
    """O modo de falha clássico de few-shot com conteúdo técnico: o LLM copia
    os NÚMEROS do exemplo para a resposta real. Sem esta marcação explícita,
    um exemplo com "densidade 35" contamina uma pergunta sobre outro produto."""
    from app.rag.treinamento import montar_bloco

    with patch("app.rag.treinamento.buscar", return_value={
        "correcao": [], "conhecimento": [], "exemplo": [_payload("exemplo")],
    }):
        bloco = montar_bloco("qualquer pergunta")

    assert "FORMA" in bloco
    assert "NÃO valem como fato" in bloco or "não valem como fato" in bloco.lower()


def test_bloco_manda_apresentar_conhecimento_como_orientacao_interna():
    """O agente precisa poder dizer "segundo orientação interna" em vez de
    atribuir aquilo a um boletim que não diz isso."""
    from app.rag.treinamento import montar_bloco

    with patch("app.rag.treinamento.buscar", return_value={
        "correcao": [], "conhecimento": [_payload("conhecimento")], "exemplo": [],
    }):
        bloco = montar_bloco("qualquer pergunta")

    assert "ORIENTAÇÃO INTERNA" in bloco
    assert "NÃO está em boletim" in bloco
    assert "03/2026" in bloco  # a data entra para o leitor pesar a idade


def test_bloco_manda_conferir_se_a_correcao_cabe_no_caso():
    """Correção casa por SIMILARIDADE, e duas perguntas parecidas podem ter
    respostas diferentes por um detalhe. Sem este aviso, o agente repetiria a
    correção como se coubesse."""
    from app.rag.treinamento import montar_bloco

    with patch("app.rag.treinamento.buscar", return_value={
        "correcao": [_payload("correcao")], "conhecimento": [], "exemplo": [],
    }):
        bloco = montar_bloco("qualquer pergunta")

    assert "CONFIRA" in bloco
    assert "densidade, norma, aplicação" in bloco


def test_sem_treinamento_o_bloco_e_vazio():
    """Cabeçalho anunciando conhecimento treinado sem nenhum item é convite a
    alucinação — o mesmo cuidado da leitura estruturada de especificações."""
    from app.rag.treinamento import montar_bloco

    with patch("app.rag.treinamento.buscar", return_value={
        "correcao": [], "conhecimento": [], "exemplo": [],
    }):
        assert montar_bloco("qualquer pergunta") == ""


def test_busca_indisponivel_nao_derruba_a_consulta():
    """Treinamento é complemento: perder o complemento não pode derrubar uma
    consulta que o acervo responderia sozinho."""
    from app.rag.treinamento import buscar

    with patch("app.rag.ingestion.get_qdrant_client", side_effect=ConnectionError("fora")):
        resultado = buscar("qualquer pergunta")

    assert resultado == {"correcao": [], "conhecimento": [], "exemplo": []}


def test_correcao_de_codigo_diferente_e_descartada_mesmo_com_score_alto():
    from app.rag.treinamento import buscar, COLECAO_TREINAMENTO

    client = MagicMock()
    client.get_collections.return_value.collections = [MagicMock(name=COLECAO_TREINAMENTO)]
    # MagicMock(name=...) não define o atributo usado pelo código.
    client.get_collections.return_value.collections[0].name = COLECAO_TREINAMENTO
    hit = MagicMock(score=0.99)
    hit.score = 0.99
    hit.payload = _payload(
        "correcao", pergunta="Qual a densidade do FLEXX AG 2062?",
        resposta="Densidade corrigida.",
    ) | {"produto": "FLEXX AG 2062"}
    client.search.side_effect = [[hit], [], []]

    with patch("app.rag.treinamento.get_qdrant_client", return_value=client), \
         patch("app.rag.treinamento.get_embedding", return_value=[0.1]):
        resultado = buscar("Qual a densidade do FLEXX AG 2032?")

    assert resultado["correcao"] == []


def test_correcao_sem_escopo_de_produto_depende_apenas_da_similaridade():
    from app.rag.treinamento import _correcao_aplicavel

    item = _payload(
        "correcao", pergunta="Preciso de dureza 60 Shore A",
        resposta="Use o produto indicado no boletim.",
    )
    assert _correcao_aplicavel("Preciso de dureza 80 Shore A", item) is True


def test_correcao_exibe_escopo_e_fonte_para_conferencia():
    from app.rag.treinamento import montar_bloco

    item = _payload("correcao") | {
        "produto": "FLEXX AG 2066", "aplicacao": "rolha de cortiça",
        "fonte": "Boletim AG 2066 rev. 03",
    }
    with patch("app.rag.treinamento.buscar", return_value={
        "correcao": [item], "conhecimento": [], "exemplo": [],
    }):
        bloco = montar_bloco("cola para cortiça")

    assert "FLEXX AG 2066" in bloco
    assert "rolha de cortiça" in bloco
    assert "Boletim AG 2066 rev. 03" in bloco
