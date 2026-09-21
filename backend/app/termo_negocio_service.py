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
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

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
_cache_mapa: Optional[Dict[str, Set[str]]] = None
_cache_carregado_em: float = 0.0
_cache_lock = threading.Lock()


def invalidar_cache() -> None:
    global _cache_mapa, _cache_carregado_em
    with _cache_lock:
        _cache_mapa = None
        _cache_carregado_em = 0.0


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
        mapa.setdefault(termo_normalizado, set()).add(rotulo)
    return mapa


def mapa_de_aliases() -> Dict[str, Set[str]]:
    """Fonte registrada em `catalog_stats`. Nunca levanta para o chamador."""
    global _cache_mapa, _cache_carregado_em
    agora = time.monotonic()
    with _cache_lock:
        if _cache_mapa is not None and (agora - _cache_carregado_em) < _TTL_CACHE_SEGUNDOS:
            return _cache_mapa
    mapa = _carregar_mapa()
    with _cache_lock:
        _cache_mapa = mapa
        _cache_carregado_em = time.monotonic()
    return mapa


def registrar_no_resolvedor() -> None:
    """Liga o cadastro ao motor. Chamado uma vez, no startup."""
    catalog_stats.registrar_fonte_de_aliases_cadastrados(mapa_de_aliases)


# --- classificações disponíveis (para a tela oferecer, não digitar) ---------

def classificacoes_do_acervo() -> List[str]:
    """Rótulos reais da árvore do catálogo, como aparecem no acervo.

    A tela usa isto num seletor em vez de campo livre. Não é conforto: digitar
    "FLEXX TH" onde o acervo diz "FLEXX® TH" produziria um apelido que nunca
    resolve, sem nenhum erro visível.
    """
    resultado = catalog_stats.listar_produtos_por_classificacao_catalogo(
        "__inexistente__"
    )
    return list(resultado.get("classificacoes_disponiveis") or [])


# --- CRUD -------------------------------------------------------------------

def criar(
    session: Session,
    *,
    classificacao: str,
    termo: str,
    observacao: str | None,
    autor: User,
) -> TermoDeNegocio:
    classificacao = (classificacao or "").strip()
    termo = (termo or "").strip()
    if len(classificacao) < 2:
        raise TermoInvalidoError("Escolha a linha do catálogo.")
    if len(termo) < 2:
        raise TermoInvalidoError("O termo precisa ter pelo menos 2 caracteres.")
    if len(termo) > 200 or len(classificacao) > 200:
        raise TermoInvalidoError("Termo ou classificação longos demais (máx. 200).")

    rotulo = normalizar(classificacao)
    disponiveis = {normalizar(c): c for c in classificacoes_do_acervo()}
    if rotulo not in disponiveis:
        raise ClassificacaoInexistenteError(
            f'A linha "{classificacao}" não existe na árvore do acervo. '
            "Escolha uma das classificações listadas — um apelido apontando para "
            "uma linha inexistente nunca resolve, e não dá erro nenhum depois."
        )

    item = TermoDeNegocio(
        id=uuid.uuid4(),
        # Guarda o rótulo COMO O ACERVO ESCREVE, não como a pessoa digitou.
        classificacao=disponiveis[rotulo],
        classificacao_rotulo=rotulo,
        termo=termo,
        termo_normalizado=normalizar(termo),
        observacao=(observacao or "").strip() or None,
        status=StatusDocumento.PENDENTE,
        criado_por_id=autor.id,
    )
    session.add(item)
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
    invalidar_cache()
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
    invalidar_cache()
    return item


def excluir(session: Session, item_id) -> None:
    session.delete(_obter(session, item_id))
    session.flush()
    invalidar_cache()
