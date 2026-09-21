"""
Terminologia de negócio: o apelido que a empresa usa para uma linha do catálogo.

O PROBLEMA QUE ISTO RESOLVE

A árvore do acervo usa códigos corporativos — `FLEXX® TH`, `FLEXX® RG`,
`FLEXX® RIM`. As pessoas falam "elastômero", "rígidos", "soft". Enquanto essa
tradução morava em `_ALIASES_CLASSIFICACAO_CATALOGO`, cada apelido novo exigia
um commit: alguém descobria que TH é a linha de elastômeros, contava, e um
desenvolvedor escrevia a linha de código. Cinquenta e três classificações e um
punhado de apelidos conhecidos é um gargalo garantido.

Agora quem conhece o catálogo cadastra pela tela.

POR QUE NÃO É UMA QUARTA MODALIDADE DE TREINAMENTO

Correção, conhecimento e exemplo são indexados no Qdrant e recuperados por
SIMILARIDADE para dentro do prompt do LLM. Um apelido de linha cadastrado assim
faria o agente "saber", em prosa, que TH é a linha de elastômeros — enquanto
`listar_produtos_por_classificacao_catalogo`, que lê a árvore de pastas e não o
prompt, continuaria devolvendo zero. Essa armadilha já custou uma rodada neste
projeto (ver `docs/avaliacao_arquitetura_2026-09-18.md`).

Terminologia é lida DETERMINISTICAMENTE pelo resolvedor, via
`catalog_stats.registrar_fonte_de_aliases_cadastrados`.

O EFEITO VAI ALÉM DA PERGUNTA POR LINHA

`listar_produtos_por_tipo` resolve o nível `classificacao_estrutural` pelo mesmo
resolvedor. Cadastrar "FLEXX® TH = elastômero" promove a pergunta de natureza
("quais produtos são elastômeros?") de "menção no documento" — a evidência mais
fraca — para "a hierarquia do catálogo classifica assim", a mais forte.
"""
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Dict, List, Mapping, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import StatusDocumento, TermoDeNegocio, User
from app.rag import catalog_stats

logger = logging.getLogger(__name__)


class TermoInvalidoError(ValueError):
    """Dado recusado antes de tocar o banco."""


class TermoNaoEncontradoError(ValueError):
    pass


class ClassificacaoInexistenteError(ValueError):
    """A linha informada não existe na árvore do acervo.

    É a validação mais importante deste módulo. Um apelido apontando para uma
    classificação inexistente não dá erro em lugar nenhum: ele simplesmente
    nunca resolve, e a pessoa que cadastrou fica achando que ensinou algo ao
    agente. Falha silenciosa é o modo de falha que este projeto mais paga caro.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalizar(texto: str) -> str:
    """Minúsculo, sem acento, sem símbolo — a forma de comparação.

    Mesma regra de `catalog_stats._rotulo_estrutural_catalogo`, reaproveitada
    dali de propósito: se as duas divergirem, o apelido cadastrado deixa de
    casar com o rótulo do acervo e ninguém descobre por quê.
    """
    return catalog_stats._rotulo_estrutural_catalogo(texto or "")


# NÃO existe uma segunda função de normalização neste módulo, e isso é
# deliberado. A primeira versão usava `_sem_acento` (minúsculo + sem acento)
# para o termo e `normalizar` (que também troca símbolo por espaço) para a
# classificação. As duas concordam em "elastômero", e divergem em qualquer
# termo com hífen:
#
#     "moldado-por-reação"  gravado como "moldado-por-reacao"
#                           procurado como "moldado por reacao"   -> nunca casa
#
# Um apelido que nunca casa não dá erro em lugar nenhum — exatamente a falha
# silenciosa que este recurso existe para evitar. Uma função só, usada nas duas
# pontas, é o que garante que gravar e procurar não podem divergir.


# --- cache do mapa aprovado -------------------------------------------------
#
# O resolvedor é chamado dentro do laço de `listar_produtos_por_tipo`, uma vez
# por termo, numa varredura de ~11.000 pontos. Ler o Postgres ali seria
# inaceitável. O cache tem TTL curto E invalidação explícita nas escritas: o
# TTL cobre outra réplica do backend aprovando um termo, a invalidação cobre o
# caso normal de quem aprovou estar no mesmo processo e esperar efeito imediato.
_TTL_CACHE_SEGUNDOS = 60.0
_cache_mapa: Optional[Mapping[str, Set[str]]] = None
_cache_carregado_em: float = 0.0
# CONTADOR DE GERAÇÃO. Sem ele, uma invalidação que acontece ENQUANTO outra
# thread está no banco é perdida: a leitura volta com o mapa velho e o grava
# com carimbo novo, válido por mais 60s. A leitura só publica o que carregou se
# a geração não mudou no meio do caminho.
_geracao_cache: int = 0
_cache_lock = threading.Lock()


def invalidar_cache() -> None:
    global _cache_mapa, _cache_carregado_em, _geracao_cache
    with _cache_lock:
        _cache_mapa = None
        _cache_carregado_em = 0.0
        _geracao_cache += 1


def _carregar_mapa() -> Dict[str, Set[str]]:
    """{termo_normalizado: {rotulo_da_classificacao, ...}} — só APROVADOS."""
    from app.db import SessionLocal

    mapa: Dict[str, Set[str]] = {}
    with SessionLocal() as session:
        linhas = session.execute(
            select(TermoDeNegocio.termo_normalizado, TermoDeNegocio.classificacao_rotulo)
            .where(TermoDeNegocio.status == StatusDocumento.APROVADO)
        ).all()
    for termo_normalizado, rotulo in linhas:
        if not termo_normalizado:
            # Chave vazia casaria com QUALQUER pergunta sem letras, e casaria
            # pelo nível `classificacao_estrutural` — o mais forte da cascata.
            # `criar()` recusa isso hoje; esta linha protege o que já estiver
            # gravado de antes.
            continue
        mapa.setdefault(termo_normalizado, set()).add(rotulo)
    return mapa


def mapa_de_aliases() -> Mapping[str, Set[str]]:
    """Fonte registrada em `catalog_stats`. Devolve um mapa IMUTÁVEL.

    O dict devolvido era o próprio cache: um chamador distraído mutando-o
    contaminaria todas as respostas do processo por 60 segundos, sem rastro.
    """
    global _cache_mapa, _cache_carregado_em
    with _cache_lock:
        agora = time.monotonic()
        if _cache_mapa is not None and (agora - _cache_carregado_em) < _TTL_CACHE_SEGUNDOS:
            return _cache_mapa
        geracao_na_leitura = _geracao_cache

    mapa = MappingProxyType(_carregar_mapa())

    with _cache_lock:
        if geracao_na_leitura == _geracao_cache:
            _cache_mapa = mapa
            _cache_carregado_em = time.monotonic()
        # Geração diferente = alguém aprovou enquanto líamos. O que acabamos de
        # carregar já nasceu velho; não vira cache, e a próxima chamada relê.
    return mapa


# --- cache da lista de classificações do acervo -----------------------------
#
# `classificacoes_do_acervo()` varre a COLEÇÃO INTEIRA (~11.000 pontos no
# acervo real, ~0,9 s de CPU). Ela é chamada a cada `criar()` e a cada
# renderização da aba — e o Streamlit re-executa o script inteiro a cada
# interação, renderizando TODAS as abas. Sem cache, aprovar um ensinamento
# qualquer na aba ao lado já custava uma varredura completa do Qdrant.
#
# TTL mais longo que o dos apelidos porque a árvore do catálogo só muda numa
# ingestão, não numa aprovação. Ainda assim é curto o bastante para uma linha
# recém-indexada ficar cadastrável no mesmo turno de trabalho.
_TTL_CLASSIFICACOES_SEGUNDOS = 300.0
_cache_classificacoes: Optional[List[str]] = None
_cache_classificacoes_em: float = 0.0
_lock_classificacoes = threading.Lock()


def invalidar_cache_de_classificacoes() -> None:
    global _cache_classificacoes, _cache_classificacoes_em
    with _lock_classificacoes:
        _cache_classificacoes = None
        _cache_classificacoes_em = 0.0


def registrar_no_resolvedor() -> None:
    """Liga o cadastro ao motor. Chamado uma vez, no startup."""
    catalog_stats.registrar_fonte_de_aliases_cadastrados(mapa_de_aliases)


# --- classificações disponíveis (para a tela oferecer, não digitar) ---------

def classificacoes_do_acervo(forcar: bool = False) -> List[str]:
    """Rótulos reais da árvore do catálogo, como aparecem no acervo.

    A tela usa isto num seletor em vez de campo livre. Não é conforto: digitar
    "FLEXX TH" onde o acervo diz "FLEXX® TH" produziria um apelido que nunca
    resolve, sem nenhum erro visível.

    Uma falha ao ler o acervo PROPAGA e não vira cache: gravar lista vazia
    porque o Qdrant piscou deixaria "nenhuma classificação encontrada" na tela
    por cinco minutos depois de ele voltar.
    """
    global _cache_classificacoes, _cache_classificacoes_em
    if not forcar:
        with _lock_classificacoes:
            agora = time.monotonic()
            if (
                _cache_classificacoes is not None
                and (agora - _cache_classificacoes_em) < _TTL_CLASSIFICACOES_SEGUNDOS
            ):
                return list(_cache_classificacoes)

    resultado = catalog_stats.listar_produtos_por_classificacao_catalogo(
        "__inexistente__"
    )
    classificacoes = list(resultado.get("classificacoes_disponiveis") or [])

    with _lock_classificacoes:
        _cache_classificacoes = classificacoes
        _cache_classificacoes_em = time.monotonic()
    return list(classificacoes)


# --- CRUD -------------------------------------------------------------------

def separar_termos(texto: str) -> List[str]:
    """Quebra "elastômero, borracha, TPU" em três termos.

    Vírgula, ponto-e-vírgula e quebra de linha — os três jeitos que alguém
    naturalmente separa uma lista ao digitar. Sem isto, cadastrar os apelidos
    de uma linha exige submeter o formulário uma vez por apelido, e quem tem
    cinquenta e três linhas para percorrer desiste no meio.

    Duplicatas dentro do mesmo envio são colapsadas pela forma NORMALIZADA:
    "Elastômero" e "elastomero" são o mesmo apelido escrito de dois jeitos.
    """
    if not texto:
        return []
    # Vírgula, ponto-e-vírgula e quebra de linha — os três jeitos que
    # alguém naturalmente separa uma lista ao digitar. Sem regex de
    # propósito: a classe de caracteres aqui precisaria escapar quebra
    # de linha, e é exatamente o tipo de detalhe que se quebra em
    # silêncio numa edição futura.
    normalizado = texto
    for separador in (';', chr(10), chr(13)):
        normalizado = normalizado.replace(separador, ',')
    brutos = normalizado.split(',')
    vistos, limpos = set(), []
    for bruto in brutos:
        termo = " ".join(bruto.split())
        if not termo:
            continue
        chave = normalizar(termo)
        if chave in vistos:
            continue
        vistos.add(chave)
        limpos.append(termo)
    return limpos


def _validar_termo(termo: str) -> None:
    if len(termo) < 2:
        raise TermoInvalidoError(f'"{termo}": o termo precisa ter pelo menos 2 caracteres.')
    if len(termo) > 200:
        raise TermoInvalidoError(f'"{termo[:40]}…": termo longo demais (máx. 200).')
    # "---" tem 3 caracteres e normaliza para "". A chave vazia nunca resolve
    # para o que a pessoa quis E casa com QUALQUER pergunta cujo termo também
    # normalize para vazio — pelo nível `classificacao_estrutural`, o mais
    # forte da cascata. Falha silenciosa nas duas pontas.
    if len(normalizar(termo)) < 2:
        raise TermoInvalidoError(
            f'"{termo}": precisa ter pelo menos 2 letras ou números — '
            "só símbolos não identificam nada."
        )


def _resolver_classificacao(classificacao: str) -> tuple[str, str]:
    """Devolve (rotulo_normalizado, rotulo_como_o_acervo_escreve)."""
    classificacao = (classificacao or "").strip()
    if len(classificacao) < 2:
        raise TermoInvalidoError("Escolha a linha do catálogo.")
    if len(classificacao) > 200:
        raise TermoInvalidoError("Classificação longa demais (máx. 200).")

    rotulo = normalizar(classificacao)
    disponiveis = {normalizar(c): c for c in classificacoes_do_acervo()}
    if rotulo not in disponiveis:
        # Antes de dizer "essa linha não existe", relê o acervo ignorando o
        # cache. Uma linha indexada há dois minutos tem que ser cadastrável
        # AGORA — recusar por causa de uma lista de cinco minutos atrás seria o
        # cache inventando a falha silenciosa que ele deveria evitar.
        disponiveis = {
            normalizar(c): c for c in classificacoes_do_acervo(forcar=True)
        }
    if rotulo not in disponiveis:
        raise ClassificacaoInexistenteError(
            f'A linha "{classificacao}" não existe na árvore do acervo. '
            "Escolha uma das classificações listadas — um apelido apontando para "
            "uma linha inexistente nunca resolve, e não dá erro nenhum depois."
        )
    return rotulo, disponiveis[rotulo]


def criar(
    session: Session,
    *,
    classificacao: str,
    termo: str,
    observacao: str | None,
    autor: User,
) -> List[TermoDeNegocio]:
    """Cadastra UM OU VÁRIOS apelidos para a mesma linha.

    `termo` aceita lista separada por vírgula, ponto-e-vírgula ou quebra de
    linha: uma linha do catálogo costuma ter mais de um nome de negócio
    (elastômero, borracha, TPU), e cada um vira uma linha própria na tabela.

    Devolve SEMPRE uma lista, mesmo para um termo só — assinatura única evita
    que o chamador precise adivinhar o que voltou.
    """
    rotulo, rotulo_do_acervo = _resolver_classificacao(classificacao)

    termos = separar_termos(termo)
    if not termos:
        raise TermoInvalidoError("Informe ao menos um termo.")
    for candidato in termos:
        _validar_termo(candidato)

    criados = []
    for candidato in termos:
        item = TermoDeNegocio(
            id=uuid.uuid4(),
            # Guarda o rótulo COMO O ACERVO ESCREVE, não como a pessoa digitou.
            classificacao=rotulo_do_acervo,
            classificacao_rotulo=rotulo,
            termo=candidato,
            termo_normalizado=normalizar(candidato),
            observacao=(observacao or "").strip() or None,
            status=StatusDocumento.PENDENTE,
            criado_por_id=autor.id,
        )
        session.add(item)
        criados.append(item)
    session.flush()
    return criados


def editar(
    session: Session,
    item_id,
    *,
    classificacao: str | None = None,
    termo: str | None = None,
    observacao: str | None = None,
    autor: User,
) -> TermoDeNegocio:
    """Corrige um apelido já cadastrado.

    DISCIPLINA DA APROVAÇÃO: mexer no TERMO ou na LINHA de um item já aprovado
    o devolve para PENDENTE. Não é burocracia — um apelido aprovado muda o
    resultado de consultas estruturais, apresentadas ao vendedor como a
    evidência mais forte que existe. Trocar "elastômero" por "borracha" num
    item em uso, sem nova revisão, mudaria em silêncio o que o agente afirma
    como verdade da empresa.

    Corrigir só a OBSERVAÇÃO não derruba a aprovação: ela é nota para humano e
    não entra em resolução nenhuma. Exigir revisão para um ajuste de texto
    treinaria as pessoas a aprovar sem ler.
    """
    item = _obter(session, item_id)
    mudou_o_que_resolve = False

    if classificacao is not None:
        rotulo, rotulo_do_acervo = _resolver_classificacao(classificacao)
        if rotulo != item.classificacao_rotulo:
            item.classificacao = rotulo_do_acervo
            item.classificacao_rotulo = rotulo
            mudou_o_que_resolve = True

    if termo is not None:
        termos = separar_termos(termo)
        if len(termos) != 1:
            raise TermoInvalidoError(
                "Na edição, informe um termo só. Para acrescentar outros "
                "apelidos à mesma linha, use o cadastro."
            )
        _validar_termo(termos[0])
        if normalizar(termos[0]) != item.termo_normalizado or termos[0] != item.termo:
            if normalizar(termos[0]) != item.termo_normalizado:
                mudou_o_que_resolve = True
            item.termo = termos[0]
            item.termo_normalizado = normalizar(termos[0])

    if observacao is not None:
        item.observacao = (observacao or "").strip() or None

    if mudou_o_que_resolve and item.status == StatusDocumento.APROVADO:
        item.status = StatusDocumento.PENDENTE
        item.motivo_decisao = None
        item.decidido_por_id = None
        item.decidido_em = None
        logger.info(
            "Termo %s voltou para aprovação: o que ele resolve mudou.", item.id
        )

    item_autor = autor  # mantido para auditoria futura; hoje só o log acima
    del item_autor
    session.flush()
    return item


def listar(session: Session, *, status: StatusDocumento | None = None) -> List[TermoDeNegocio]:
    consulta = select(TermoDeNegocio).order_by(TermoDeNegocio.created_at.desc())
    if status is not None:
        consulta = consulta.where(TermoDeNegocio.status == status)
    return list(session.execute(consulta).unique().scalars().all())


def _obter(session: Session, item_id) -> TermoDeNegocio:
    item = session.get(TermoDeNegocio, item_id)
    if item is None:
        raise TermoNaoEncontradoError("Termo não encontrado.")
    return item


def aprovar(session: Session, item_id, *, aprovador: User) -> TermoDeNegocio:
    item = _obter(session, item_id)
    item.status = StatusDocumento.APROVADO
    item.decidido_por_id = aprovador.id
    item.decidido_em = _utcnow()
    session.flush()
    # A invalidação NÃO acontece aqui: `flush()` ainda não é `commit()`. Entre
    # um e outro, uma pergunta concorrente abre outra conexão, não enxerga a
    # linha, e regrava o cache VAZIO com carimbo novo — válido por 60 s, com a
    # tela mostrando "Em uso" e o agente dizendo que não encontrou. Quem commita
    # é quem invalida (ver `_commit_traduzindo_erros` no router).
    logger.info(
        "Termo de negócio aprovado: %r -> %s", item.termo, item.classificacao
    )
    return item


def recusar(session: Session, item_id, *, aprovador: User, motivo: str) -> TermoDeNegocio:
    motivo = (motivo or "").strip()
    if len(motivo) < 3:
        raise TermoInvalidoError("Explique o motivo da recusa.")
    item = _obter(session, item_id)
    item.status = StatusDocumento.REJEITADO
    item.motivo_decisao = motivo
    item.decidido_por_id = aprovador.id
    item.decidido_em = _utcnow()
    session.flush()
    return item


def excluir(session: Session, item_id) -> None:
    session.delete(_obter(session, item_id))
    session.flush()
