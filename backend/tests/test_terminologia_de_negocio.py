"""
Terminologia de negócio: o apelido da empresa para uma linha do catálogo.

O QUE ESTES TESTES PROTEGEM

1. **Que isto não vire treinamento.** Correção, conhecimento e exemplo são
   indexados no Qdrant e recuperados por SIMILARIDADE para dentro do prompt. Um
   apelido cadastrado assim faria o agente "saber" em prosa que TH é a linha de
   elastômeros, enquanto a consulta estrutural continuaria devolvendo zero —
   armadilha que já custou uma rodada neste projeto. O caminho aqui é
   determinístico, pelo resolvedor.

2. **Que um apelido para uma linha inexistente seja recusado na hora.** É a
   falha silenciosa mais provável deste recurso: cadastrar "FLEXX XYZ" nunca
   resolve, nunca dá erro, e a pessoa fica achando que ensinou algo ao agente.

3. **Que o cadastro promova a pergunta de NATUREZA ao nível mais forte.** O
   ganho não é só responder "produtos da linha elastômero": é "quais produtos
   são elastômeros?" passar de "menção no documento" para "a hierarquia do
   catálogo classifica assim".

Nenhum teste aqui depende de PostgreSQL — o serviço é exercitado com sessão
mockada e o resolvedor com a fonte de aliases injetada.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag import catalog_stats
from app.rag.catalog_stats import (
    _resolver_classificacoes_catalogo,
    _rotulo_estrutural_catalogo,
    listar_produtos_por_tipo,
    registrar_fonte_de_aliases_cadastrados,
)

# As 53 classificações reais do acervo, conferidas no servidor em 21/09/2026.
CLASSIFICACOES_REAIS = [
    "FLEXXBIO12", "FLEXX® AC", "FLEXX® ADT", "FLEXX® AG", "FLEXX® BT",
    "FLEXX® CAT", "FLEXX® RG", "FLEXX® RGE", "FLEXX® RIM", "FLEXX® SOFT",
    "FLEXX® TH", "ISO FLEXX",
]
DISPONIVEIS = {_rotulo_estrutural_catalogo(c) for c in CLASSIFICACOES_REAIS}

RAIZ = r"\\servidor\acervo\Documentação de Produto"


@pytest.fixture(autouse=True)
def _sem_fonte_registrada():
    """Cada teste liga a sua própria fonte; nenhum herda a do anterior."""
    registrar_fonte_de_aliases_cadastrados(None)
    yield
    registrar_fonte_de_aliases_cadastrados(None)


def _ponto(filepath, content="Sistema bicomponente para peças técnicas."):
    p = MagicMock()
    p.payload = {"filepath": filepath, "content": content}
    return p


def _listar(pontos, termo, **kwargs):
    cliente = MagicMock()
    cliente.scroll.return_value = (pontos, None)
    with patch("app.rag.catalog_stats.get_qdrant_client", return_value=cliente):
        return listar_produtos_por_tipo(termo, **kwargs)


# --- o resolvedor lê o cadastro --------------------------------------------

def test_apelido_cadastrado_resolve_na_linha_do_acervo():
    registrar_fonte_de_aliases_cadastrados(lambda: {"rim": {"flexx rim"}})
    assert _resolver_classificacoes_catalogo("RIM", DISPONIVEIS) == {"flexx rim"}


def test_apelido_cadastrado_soma_ao_mapa_de_codigo():
    """Os dois mapas convivem: o de código traz os aliases validados em
    desenvolvimento, o cadastro traz o que a equipe descobriu depois."""
    registrar_fonte_de_aliases_cadastrados(lambda: {"espuma macia": {"flexx soft"}})
    assert _resolver_classificacoes_catalogo("rigidos", DISPONIVEIS) == {"flexx rg"}
    assert _resolver_classificacoes_catalogo("espuma macia", DISPONIVEIS) == {"flexx soft"}


def test_apelido_cadastrado_nao_esconde_a_descoberta_dinamica():
    """Mesma disciplina do mapa de código: o atalho de negócio SOMA, nunca
    passa na frente da fonte de verdade estrutural."""
    registrar_fonte_de_aliases_cadastrados(lambda: {"rg": {"flexx rim"}})
    achadas = _resolver_classificacoes_catalogo("RG", DISPONIVEIS)
    assert "flexx rg" in achadas, "a descoberta dinâmica foi escondida pelo cadastro"
    assert "flexx rim" in achadas, "o apelido cadastrado sumiu"


def test_termo_sem_cadastro_continua_devolvendo_zero():
    """O cadastro não pode virar porta aberta: termo desconhecido continua
    sem resolver, em vez de trazer produtos apenas relacionados."""
    registrar_fonte_de_aliases_cadastrados(lambda: {"rim": {"flexx rim"}})
    assert _resolver_classificacoes_catalogo("coisa inexistente", DISPONIVEIS) == set()


def test_falha_ao_ler_o_cadastro_nao_derruba_a_consulta():
    """Fail-open: sem os apelidos, o resultado é exatamente o comportamento
    anterior a este recurso — melhor que um erro na cara do vendedor."""
    def _explode():
        raise RuntimeError("banco fora do ar")

    registrar_fonte_de_aliases_cadastrados(_explode)
    assert _resolver_classificacoes_catalogo("rigidos", DISPONIVEIS) == {"flexx rg"}


# --- o ganho real: a pergunta de natureza sobe de nível ---------------------

def test_cadastro_promove_a_pergunta_de_natureza_ao_nivel_mais_forte():
    """SEM cadastro, "quais produtos são soft?" só encontra menção no texto.
    COM cadastro, a hierarquia do catálogo responde — a evidência mais forte
    da cascata. É este o ganho que justifica o recurso."""
    pontos = [
        _ponto(
            rf"{RAIZ}\FLEXX® SOFT\FLEXX SOFT 120\Boletim FLEXX SOFT 120.pdf",
            "Sistema para espuma macia de conforto.",
        )
    ]

    sem_cadastro = _listar(pontos, "espuma macia")
    assert sem_cadastro["niveis"]["classificacao_estrutural"]["total"] == 0

    registrar_fonte_de_aliases_cadastrados(lambda: {"espuma macia": {"flexx soft"}})
    com_cadastro = _listar(pontos, "espuma macia")
    assert com_cadastro["nivel_atendido"] == "classificacao_estrutural"
    assert "FLEXX® SOFT" in com_cadastro["classificacoes_encontradas"]


# --- a validação que impede a falha silenciosa ------------------------------

def _sessao_falsa():
    s = MagicMock()
    s.add = MagicMock()
    s.flush = MagicMock()
    return s


def _autor():
    u = MagicMock()
    u.id = "00000000-0000-0000-0000-000000000001"
    return u


def test_recusa_apelido_para_linha_que_nao_existe_no_acervo():
    """A falha silenciosa mais provável deste recurso: um apelido apontando
    para classificação inexistente nunca resolve, nunca dá erro, e quem
    cadastrou fica achando que ensinou algo ao agente."""
    from app.termo_negocio_service import ClassificacaoInexistenteError, criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        with pytest.raises(ClassificacaoInexistenteError) as erro:
            criar(
                _sessao_falsa(),
                classificacao="FLEXX XYZ",
                termo="qualquer coisa",
                observacao=None,
                autor=_autor(),
            )
    assert "não existe na árvore do acervo" in str(erro.value)


def test_guarda_o_rotulo_como_o_acervo_escreve_e_nao_como_foi_digitado():
    """Digitar "flexx th" onde o acervo diz "FLEXX® TH" produziria um apelido
    que nunca casa. A normalização resolve na comparação, e o rótulo exibido
    continua sendo o do acervo."""
    from app.termo_negocio_service import criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        item = criar(
            _sessao_falsa(),
            classificacao="flexx th",
            termo="Elastômero",
            observacao=None,
            autor=_autor(),
        )[0]
    assert item.classificacao == "FLEXX® TH"
    assert item.classificacao_rotulo == "flexx th"
    assert item.termo_normalizado == "elastomero", "o acento não foi normalizado"


@pytest.mark.parametrize(
    "digitado,esperado",
    [
        ("Elastômero", "elastomero"),
        ("moldado-por-reação", "moldado por reacao"),
        ("HRB-H", "hrb h"),
        ("  espuma   macia  ", "espuma macia"),
    ],
)
def test_gravar_e_procurar_usam_a_mesma_normalizacao(digitado, esperado):
    """REGRESSÃO REAL: havia duas funções de normalização neste módulo — uma
    para o termo (minúsculo + sem acento) e outra para a classificação (que
    também troca símbolo por espaço). Concordavam em "elastômero" e divergiam
    em qualquer termo com hífen: "moldado-por-reação" era GRAVADO como
    "moldado-por-reacao" e PROCURADO como "moldado por reacao" — nunca casava,
    e não dava erro em lugar nenhum."""
    from app.rag.catalog_stats import _rotulo_estrutural_catalogo
    from app.termo_negocio_service import criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        item = criar(
            _sessao_falsa(),
            classificacao="FLEXX® TH",
            termo=digitado,
            observacao=None,
            autor=_autor(),
        )[0]

    assert item.termo_normalizado == esperado
    # A ponta da PROCURA: é assim que `_resolver_classificacoes_catalogo`
    # normaliza antes de consultar o mapa.
    assert item.termo_normalizado == _rotulo_estrutural_catalogo(digitado), (
        "gravar e procurar divergiram — o apelido nunca resolveria"
    )


def test_nasce_pendente_de_aprovacao():
    """Afirma um fato sobre o catálogo que o agente repete como verdade — e,
    diferente de uma correção, muda o resultado de consultas ESTRUTURAIS, que
    são apresentadas como a evidência mais forte que existe."""
    from app.models import StatusDocumento
    from app.termo_negocio_service import criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        item = criar(
            _sessao_falsa(),
            classificacao="FLEXX® RIM",
            termo="moldado por reação",
            observacao=None,
            autor=_autor(),
        )[0]
    assert item.status == StatusDocumento.PENDENTE


@pytest.mark.parametrize(
    "classificacao,termo",
    [("FLEXX® TH", "a"), ("F", "elastomero"), ("FLEXX® TH", "   ")],
)
def test_recusa_entrada_curta_demais(classificacao, termo):
    from app.termo_negocio_service import TermoInvalidoError, criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        with pytest.raises(TermoInvalidoError):
            criar(
                _sessao_falsa(),
                classificacao=classificacao,
                termo=termo,
                observacao=None,
                autor=_autor(),
            )


# --- só o que foi aprovado vale ---------------------------------------------

def test_mapa_so_carrega_termos_aprovados():
    """Pendente e recusado não podem influenciar resposta nenhuma — é o que
    torna a aprovação mais que um enfeite de tela."""
    from app.models import StatusDocumento
    from app import termo_negocio_service as servico

    capturado = {}

    class _SessaoFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, consulta):
            capturado["consulta"] = str(consulta)
            return MagicMock(all=lambda: [("elastomero", "flexx th")])

    servico.invalidar_cache()
    with patch("app.db.SessionLocal", _SessaoFalsa):
        mapa = servico.mapa_de_aliases()
    servico.invalidar_cache()

    assert mapa == {"elastomero": {"flexx th"}}
    assert "status" in capturado["consulta"].lower(), "a consulta não filtra por status"


# --- vários apelidos de uma vez, e edição ----------------------------------

def _servico():
    from app import termo_negocio_service as s
    return s


def test_cadastra_varios_apelidos_para_a_mesma_linha_de_uma_vez():
    """Uma linha do catálogo costuma ter mais de um nome de negócio. Sem isto,
    cadastrar os apelidos exige submeter o formulário uma vez por apelido — e
    quem tem 53 linhas para percorrer desiste no meio."""
    from app.termo_negocio_service import criar

    sessao = _sessao_falsa()
    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        itens = criar(
            sessao,
            classificacao="FLEXX® TH",
            termo="elastômero, borracha; TPU",
            observacao="como vendas se refere à linha",
            autor=_autor(),
        )

    assert [i.termo for i in itens] == ["elastômero", "borracha", "TPU"]
    assert {i.classificacao for i in itens} == {"FLEXX® TH"}
    assert {i.termo_normalizado for i in itens} == {"elastomero", "borracha", "tpu"}


def test_duplicata_no_mesmo_envio_e_colapsada():
    """"Elastômero" e "elastomero" são o mesmo apelido escrito de dois jeitos —
    gravar os dois estouraria a constraint de unicidade com um 409 confuso."""
    from app.termo_negocio_service import criar

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        itens = criar(
            _sessao_falsa(),
            classificacao="FLEXX® TH",
            termo="Elastômero, elastomero, ELASTOMERO",
            observacao=None,
            autor=_autor(),
        )
    assert len(itens) == 1


def test_editar_termo_aprovado_devolve_para_aprovacao():
    """DISCIPLINA: um apelido em uso muda o resultado de consultas
    estruturais, apresentadas ao vendedor como a evidência mais forte que
    existe. Trocar "elastômero" por "borracha" sem nova revisão mudaria em
    silêncio o que o agente afirma como verdade da empresa."""
    from app.models import StatusDocumento, TermoDeNegocio
    from app.termo_negocio_service import editar

    item = TermoDeNegocio(
        classificacao="FLEXX® TH", classificacao_rotulo="flexx th",
        termo="elastômero", termo_normalizado="elastomero",
        status=StatusDocumento.APROVADO, decidido_por_id="x", motivo_decisao=None,
    )
    sessao = MagicMock()
    sessao.get.return_value = item

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        editar(sessao, "id", termo="borracha", autor=_autor())

    assert item.termo == "borracha"
    assert item.status == StatusDocumento.PENDENTE
    assert item.decidido_por_id is None


def test_editar_so_a_observacao_nao_derruba_a_aprovacao():
    """Exigir revisão para um ajuste de texto treinaria as pessoas a aprovar
    sem ler. A observação é nota para humano e não entra em resolução nenhuma."""
    from app.models import StatusDocumento, TermoDeNegocio
    from app.termo_negocio_service import editar

    item = TermoDeNegocio(
        classificacao="FLEXX® TH", classificacao_rotulo="flexx th",
        termo="elastômero", termo_normalizado="elastomero",
        status=StatusDocumento.APROVADO,
    )
    sessao = MagicMock()
    sessao.get.return_value = item

    editar(sessao, "id", observacao="corrigindo a nota", autor=_autor())

    assert item.status == StatusDocumento.APROVADO
    assert item.observacao == "corrigindo a nota"


def test_editar_corrigindo_so_a_grafia_nao_derruba_a_aprovacao():
    """"elastomero" → "elastômero" muda o texto exibido, não o que resolve:
    os dois normalizam para a mesma chave."""
    from app.models import StatusDocumento, TermoDeNegocio
    from app.termo_negocio_service import editar

    item = TermoDeNegocio(
        classificacao="FLEXX® TH", classificacao_rotulo="flexx th",
        termo="elastomero", termo_normalizado="elastomero",
        status=StatusDocumento.APROVADO,
    )
    sessao = MagicMock()
    sessao.get.return_value = item

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        editar(sessao, "id", termo="elastômero", autor=_autor())

    assert item.termo == "elastômero"
    assert item.status == StatusDocumento.APROVADO


def test_editar_recusa_lista_de_termos():
    """Editar é sobre UM item. Aceitar lista aqui criaria a expectativa de que
    o item vira vários, que não é o que acontece."""
    from app.termo_negocio_service import TermoInvalidoError, editar

    item = MagicMock()
    sessao = MagicMock()
    sessao.get.return_value = item

    with patch(
        "app.termo_negocio_service.classificacoes_do_acervo",
        return_value=CLASSIFICACOES_REAIS,
    ):
        with pytest.raises(TermoInvalidoError):
            editar(sessao, "id", termo="borracha, TPU", autor=_autor())
