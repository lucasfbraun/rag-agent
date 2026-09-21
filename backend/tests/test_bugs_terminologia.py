"""
Defeitos encontrados na terminologia de negocio (revisao cetica de 21/09/2026).

Todos os testes daqui sao do mesmo genero dos dois defeitos ja corrigidos no
commit f72cf7a: nao aparecem numa suite verde e so se manifestam em producao —
um derruba o container no boot, os outros fazem o recurso mentir em silencio.

Nenhum depende de PostgreSQL nem de Qdrant.
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from app import termo_negocio_service as servico
from app.models import StatusDocumento
from app.rag import catalog_stats
from app.rag.catalog_stats import (
    _resolver_classificacoes_catalogo,
    _rotulo_estrutural_catalogo,
    registrar_fonte_de_aliases_cadastrados,
)

RAIZ = r"\\servidor\acervo\Documentação de Produto"
CLASSIFICACOES_REAIS = [
    "FLEXXBIO12", "FLEXX® AC", "FLEXX® ADT", "FLEXX® AG", "FLEXX® BT",
    "FLEXX® CAT", "FLEXX® RG", "FLEXX® RGE", "FLEXX® RIM", "FLEXX® SOFT",
    "FLEXX® TH", "ISO FLEXX",
]
DISPONIVEIS = {_rotulo_estrutural_catalogo(c) for c in CLASSIFICACOES_REAIS}

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "a3d6f81c47e9_termos_de_negocio.py"
)


@pytest.fixture(autouse=True)
def _ambiente_limpo():
    registrar_fonte_de_aliases_cadastrados(None)
    servico.invalidar_cache()
    servico.invalidar_cache_de_classificacoes()
    yield
    registrar_fonte_de_aliases_cadastrados(None)
    servico.invalidar_cache()
    servico.invalidar_cache_de_classificacoes()


# ---------------------------------------------------------------------------
# 1. A migration derruba o deploy: ela RECRIA um enum que ja existe
# ---------------------------------------------------------------------------

def _ddl_da_migration() -> list[str]:
    """Roda a migration contra um engine PostgreSQL de mentira e devolve o SQL.

    E a unica forma de provar isto sem um Postgres: o erro nao esta no Python
    da migration, esta no DDL que ela manda para o banco.
    """
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    emitido: list[str] = []
    engine = sa.create_mock_engine(
        "postgresql://",
        lambda sql, *a, **kw: emitido.append(
            str(sql.compile(dialect=engine.dialect)).strip()
        ),
    )
    # SEM `as_sql=True`: com ele o Alembic ESCREVE o SQL em stdout em vez de
    # passar pelo executor do engine, e `emitido` volta vazia — o teste
    # passaria a falhar por StopIteration em vez de pelo defeito que investiga.
    contexto = MigrationContext.configure(connection=engine)
    Operations(contexto)._install_proxy()

    spec = importlib.util.spec_from_file_location("_mig_termos", MIGRATION)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    modulo.upgrade()
    return emitido


def test_migration_nao_recria_o_enum_compartilhado():
    """CRASH-LOOP NO DEPLOY.

    A migration referencia o enum com `create_type=False`, mas usava
    `sa.Enum(...)` — e `sa.Enum` ACEITA e DESCARTA `create_type` em silencio
    (ele cai no `**kw` e some). Quem honra o parametro e' `postgresql.ENUM`.

    Resultado: o `op.create_table` emite `CREATE TYPE status_documento` com
    `checkfirst=False` (ver `alembic.ddl.impl.DefaultImpl.create_table`) num
    banco onde o tipo ja existe desde c2f8a05b71d4 -> DuplicateObject ->
    `alembic upgrade head` falha -> `app/startup.py` roda com `check=True` ->
    o container morre antes do uvicorn subir, em loop.

    Exatamente o mesmo genero do `sa.dialects.postgresql.UUID` ja corrigido,
    e igualmente invisivel para a suite: nenhum teste toca DDL.
    """
    criacoes = [s for s in _ddl_da_migration() if s.upper().startswith("CREATE TYPE")]
    assert criacoes == [], (
        "a migration recria o enum compartilhado e o deploy entra em crash-loop: "
        f"{criacoes}"
    )


def test_migration_usa_os_rotulos_reais_do_enum_no_banco():
    """FALHA NO DEPLOY, mesmo que o CREATE TYPE fosse evitado.

    O tipo `status_documento` foi criado por c2f8a05b71d4 com os rotulos em
    MAIUSCULA ("PENDENTE", "APROVADO", "REJEITADO") — a convencao do
    SQLAlchemy, que grava o NOME do membro do enum, nao o valor. O model
    concorda: `TermoDeNegocio.__table__.c.status.type.enums` e' MAIUSCULA.

    A migration declarava minuscula e `server_default="pendente"`. O
    `DEFAULT 'pendente'` nao e' rotulo valido do tipo no banco ->
    `invalid input value for enum status_documento` -> migration falha.
    """
    from app.models import TermoDeNegocio

    rotulos_do_model = set(TermoDeNegocio.__table__.c.status.type.enums)
    assert rotulos_do_model == {"PENDENTE", "APROVADO", "REJEITADO"}

    ddl = "\n".join(_ddl_da_migration())
    linha_status = next(
        linha for linha in ddl.splitlines() if "status status_documento" in linha
    )
    assert "DEFAULT 'PENDENTE'" in linha_status, (
        "o server_default nao e' um rotulo valido do enum que existe no banco: "
        f"{linha_status.strip()}"
    )
    for minusculo in ("'pendente'", "'aprovado'", "'rejeitado'"):
        assert minusculo not in ddl, f"rotulo minusculo {minusculo} no DDL"


# ---------------------------------------------------------------------------
# 2. A aprovacao pode nao valer por ate 60 segundos, sem erro nenhum
# ---------------------------------------------------------------------------

def _sessao_de_requisicao(item):
    sessao = MagicMock()
    sessao.get.return_value = item
    return sessao


def _termo_pendente():
    from app.models import TermoDeNegocio

    return TermoDeNegocio(
        classificacao="FLEXX® TH",
        classificacao_rotulo="flexx th",
        termo="elastômero",
        termo_normalizado="elastomero",
        status=StatusDocumento.PENDENTE,
    )


def _patch_banco(estado):
    """SessionLocal de mentira: so enxerga a linha DEPOIS do commit."""

    class _Sessao:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, _consulta):
            linhas = [("elastomero", "flexx th")] if estado["commitado"] else []
            return MagicMock(all=lambda: linhas)

    return patch("app.db.SessionLocal", _Sessao)


def test_aprovar_so_vale_depois_do_commit():
    """FALHA SILENCIOSA: a tela mostra "Em uso" e o agente segue dizendo que
    nao encontrou.

    `aprovar()` chama `invalidar_cache()` logo apos o `flush()` — ou seja,
    ANTES do `session.commit()`, que quem faz e' o `_commit_traduzindo_erros`
    do router. Na janela entre um e outro, qualquer pergunta em curso chama
    `mapa_de_aliases()`, que abre OUTRA conexao, nao enxerga a linha ainda nao
    commitada, e regrava o cache vazio com carimbo novo — valido por 60s.

    Com dois workers de uvicorn e alguem perguntando, isso nao e' teorico: e' o
    caso normal. E nao aparece em log nenhum.
    """
    from app.termo_negocio_router import _commit_traduzindo_erros

    estado = {"commitado": False}
    item = _termo_pendente()
    sessao = _sessao_de_requisicao(item)
    sessao.commit.side_effect = lambda: estado.__setitem__("commitado", True)

    with _patch_banco(estado):
        with _commit_traduzindo_erros(sessao):
            servico.aprovar(sessao, item.id, aprovador=MagicMock())
            # Uma pergunta concorrente cai exatamente aqui.
            durante = servico.mapa_de_aliases()
        assert durante == {}, "a leitura concorrente nao deveria ver o nao-commitado"

        depois = servico.mapa_de_aliases()

    assert depois == {"elastomero": {"flexx th"}}, (
        "o termo aprovado ficou preso fora do cache — o agente vai continuar "
        "respondendo como se ele nao existisse por ate 60 segundos"
    )


def test_aprovacao_que_pousa_durante_o_carregamento_nao_fica_presa():
    """A segunda metade da mesma corrida, agora dentro de `mapa_de_aliases`.

    A leitura larga o lock para ir ao banco. Se a aprovacao commitar e
    invalidar NESSE intervalo, a leitura volta com o mapa velho e o grava com
    carimbo novo — a invalidacao e' perdida e o termo fica invisivel por 60s.
    """
    chamadas = {"n": 0}

    def _carregar():
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            servico.invalidar_cache()  # aprovacao concorrente commita aqui
            return {}
        return {"elastomero": {"flexx th"}}

    servico.invalidar_cache()
    with patch.object(servico, "_carregar_mapa", _carregar):
        assert servico.mapa_de_aliases() == {}  # esta leitura e' legitimamente velha
        assert servico.mapa_de_aliases() == {"elastomero": {"flexx th"}}, (
            "a invalidacao foi perdida: o mapa velho foi regravado com carimbo novo"
        )


def test_mapa_devolvido_nao_envenena_o_cache():
    """O dict devolvido era o PROPRIO cache. Um chamador distraido mutando-o
    contaminaria todas as respostas do processo por 60s, sem rastro."""
    with patch.object(servico, "_carregar_mapa", lambda: {"elastomero": {"flexx th"}}):
        mapa = servico.mapa_de_aliases()
    with pytest.raises(TypeError):
        mapa["qualquer coisa"] = {"flexx rg"}


# ---------------------------------------------------------------------------
# 3. Termo que normaliza para vazio: casa com qualquer pergunta sem letras
# ---------------------------------------------------------------------------

def _autor():
    u = MagicMock()
    u.id = "00000000-0000-0000-0000-000000000001"
    return u


def test_recusa_termo_que_normaliza_para_vazio():
    """`len("---") >= 2` passava na validacao, mas `normalizar("---") == ""`.

    O apelido gravado com chave vazia nunca resolve para o que a pessoa quis —
    e, pior, resolve para QUALQUER pergunta cujo termo tambem normalize para
    vazio. Falha silenciosa nas duas pontas.
    """
    from app.termo_negocio_service import TermoInvalidoError, criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        for termo in ("---", "###", "«»", "-- --"):
            with pytest.raises(TermoInvalidoError):
                criar(
                    MagicMock(),
                    classificacao="FLEXX® TH",
                    termo=termo,
                    observacao=None,
                    autor=_autor(),
                )


def test_chave_vazia_no_mapa_nao_casa_com_pergunta_sem_letras():
    """Cinto e suspensorio: mesmo que uma linha dessas ja esteja gravada de
    antes, o resolvedor nao pode devolver a linha inteira para "???" — e o
    faria pelo nivel `classificacao_estrutural`, o mais forte da cascata."""
    registrar_fonte_de_aliases_cadastrados(lambda: {"": {"flexx th"}})
    for pergunta in ("???", "---", "  ", "®"):
        assert _resolver_classificacoes_catalogo(pergunta, DISPONIVEIS) == set(), (
            f"a pergunta {pergunta!r} trouxe a linha TH inteira como evidencia forte"
        )


# ---------------------------------------------------------------------------
# 4. Desempenho: varredura completa da colecao a cada abertura da aba
# ---------------------------------------------------------------------------

def _cliente_que_conta(contador, total=3000):
    ponto = MagicMock()
    ponto.payload = {
        "filepath": rf"{RAIZ}\FLEXX® TH\FLEXX TH 220\Boletim FLEXX TH 220.pdf",
        "content": "Sistema bicomponente.",
    }
    pontos = [ponto] * total

    def _novo():
        cliente = MagicMock()

        def scroll(**kw):
            contador["scrolls"] += 1
            limite = kw.get("limit", 1000)
            inicio = kw.get("offset") or 0
            fatia = pontos[inicio:inicio + limite]
            proximo = inicio + limite if inicio + limite < len(pontos) else None
            return fatia, proximo

        cliente.scroll.side_effect = scroll
        return cliente

    return _novo


def test_lista_de_classificacoes_nao_varre_a_colecao_a_cada_chamada():
    """~0,9s de CPU por chamada num acervo de 11.000 pontos, medido.

    A tela do Streamlit re-executa o script inteiro a cada interacao, e
    `st.tabs` renderiza TODAS as abas — entao aprovar um ensinamento qualquer
    na outra aba ja custa uma varredura completa do Qdrant. Somado ao
    `criar()`, que varre de novo, a aba fica visivelmente lenta sem que nada
    indique o porque.
    """
    contador = {"scrolls": 0}
    with patch(
        "app.rag.catalog_stats.get_qdrant_client",
        side_effect=_cliente_que_conta(contador),
    ):
        primeira = servico.classificacoes_do_acervo()
        for _ in range(5):
            servico.classificacoes_do_acervo()

    assert primeira == ["FLEXX® TH"]
    assert contador["scrolls"] <= 3, (
        f"6 chamadas varreram a colecao {contador['scrolls']} vezes — "
        "cada varredura e' a colecao inteira"
    )


def test_classificacao_recem_indexada_nao_fica_escondida_pelo_cache():
    """O cache nao pode inventar a falha silenciosa que ele deveria evitar:
    uma linha indexada agora tem que ser cadastravel agora, e nao daqui a
    cinco minutos com um "essa linha nao existe" na cara de quem cadastra."""
    from app.termo_negocio_service import criar

    contador = {"scrolls": 0}
    with patch(
        "app.rag.catalog_stats.get_qdrant_client",
        side_effect=_cliente_que_conta(contador, total=10),
    ):
        servico.classificacoes_do_acervo()  # aquece o cache com FLEXX® TH

        acervo_novo = [*CLASSIFICACOES_REAIS, "FLEXX® NOVA"]
        with patch(
            "app.rag.catalog_stats.listar_produtos_por_classificacao_catalogo",
            return_value={"classificacoes_disponiveis": acervo_novo},
        ):
            item = criar(
                MagicMock(),
                classificacao="FLEXX® NOVA",
                termo="linha nova",
                observacao=None,
                autor=_autor(),
            )[0]
    assert item.classificacao == "FLEXX® NOVA"


def test_falha_no_acervo_nao_deixa_o_cache_de_classificacoes_envenenado():
    """Qdrant fora do ar durante a primeira chamada nao pode gravar uma lista
    vazia por cinco minutos — seria "nenhuma classificacao encontrada" na tela
    muito depois do Qdrant ter voltado."""
    from app.rag.catalog_stats import RetrievalIndisponivelError

    with patch(
        "app.rag.catalog_stats.listar_produtos_por_classificacao_catalogo",
        side_effect=RetrievalIndisponivelError("qdrant fora"),
    ):
        with pytest.raises(RetrievalIndisponivelError):
            servico.classificacoes_do_acervo()

    contador = {"scrolls": 0}
    with patch(
        "app.rag.catalog_stats.get_qdrant_client",
        side_effect=_cliente_que_conta(contador, total=10),
    ):
        assert servico.classificacoes_do_acervo() == ["FLEXX® TH"]
