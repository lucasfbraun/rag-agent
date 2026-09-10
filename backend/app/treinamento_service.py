"""
Serviço de treinamento do agente (Sessão 38, item 4).

Ver `docs/spec_treinamento.md` para o desenho completo. O resumo das decisões
que este módulo aplica:

  - **Correção e conhecimento passam por aprovação; exemplo entra direto.** A
    razão é a assimetria de dano: os dois primeiros afirmam FATOS que o agente
    vai repetir como verdade da empresa, e um erro ali circula sem ninguém
    notar. Exemplo afeta só a FORMA, e forma ruim é visível na primeira
    resposta.
  - **Só o que está aprovado vai para o índice.** Item pendente ou recusado
    existe no Postgres para auditoria, mas não influencia resposta nenhuma.
  - **Recusar ou excluir tira do índice.** Uma correção errada precisa poder
    ser desfeita apagando uma linha — é o motivo de o aprendizado estar em
    dado, e não em pesos de modelo.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ItemTreinamento, StatusDocumento, TipoTreinamento, User

# Exemplo entra direto; correção e conhecimento esperam aprovação.
TIPOS_QUE_EXIGEM_APROVACAO = (TipoTreinamento.CORRECAO, TipoTreinamento.CONHECIMENTO)


class ItemNaoEncontradoError(ValueError):
    """Nenhum item de treinamento com esse id."""


class ItemInvalidoError(ValueError):
    """Conteúdo insuficiente para virar um item útil."""


class DecisaoInvalidaError(ValueError):
    """Item já decidido, ou decisão que não se aplica a este estado."""


# Abaixo disto o item não ensina nada e ainda ocupa espaço no contexto de toda
# consulta parecida.
TAMANHO_MINIMO = 10


def _validar(pergunta: str, resposta: str) -> None:
    if len((pergunta or "").strip()) < TAMANHO_MINIMO:
        raise ItemInvalidoError(
            "A pergunta/título precisa ter pelo menos 10 caracteres — é por ela que o "
            "item será encontrado quando alguém perguntar algo parecido."
        )
    if len((resposta or "").strip()) < TAMANHO_MINIMO:
        raise ItemInvalidoError("O conteúdo precisa ter pelo menos 10 caracteres.")


def criar(
    session: Session, *, autor: User, tipo: TipoTreinamento,
    pergunta: str, resposta: str, resposta_original: str | None = None,
) -> ItemTreinamento:
    """Cria o item. Exemplo já nasce aprovado e indexado; os outros, pendentes."""
    _validar(pergunta, resposta)

    entra_direto = tipo not in TIPOS_QUE_EXIGEM_APROVACAO
    item = ItemTreinamento(
        tipo=tipo,
        pergunta=pergunta.strip(),
        resposta=resposta.strip(),
        resposta_original=(resposta_original or "").strip() or None,
        status=StatusDocumento.APROVADO if entra_direto else StatusDocumento.PENDENTE,
        criado_por_id=autor.id,
        decidido_por_id=autor.id if entra_direto else None,
        decidido_em=datetime.now(timezone.utc) if entra_direto else None,
    )
    session.add(item)
    session.flush()

    if entra_direto:
        _indexar(session, item)
    return item


def _indexar(session: Session, item: ItemTreinamento) -> None:
    """Indexa e, se falhar, DESFAZ a aprovação.

    Mesma disciplina da fila de documentos: um item marcado como aprovado que
    não está no índice é pior que um pendente — ele aparece como ativo na tela
    e não influencia resposta nenhuma, sem que ninguém perceba."""
    from app.rag import treinamento as indice

    try:
        session.refresh(item)  # garante que `criado_por` está carregado
        indice.indexar(item)
    except Exception as e:
        item.status = StatusDocumento.PENDENTE
        item.decidido_por_id = None
        item.decidido_em = None
        session.flush()
        raise DecisaoInvalidaError(
            f"O item foi salvo mas não pôde ser indexado ({e}). Ele ficou pendente; "
            "tente aprovar de novo quando o serviço de busca voltar."
        ) from e


def listar(
    session: Session, *, tipo: TipoTreinamento | None = None,
    status: StatusDocumento | None = None, autor: User | None = None,
) -> list[ItemTreinamento]:
    consulta = select(ItemTreinamento).order_by(ItemTreinamento.created_at.desc())
    if tipo is not None:
        consulta = consulta.where(ItemTreinamento.tipo == tipo)
    if status is not None:
        consulta = consulta.where(ItemTreinamento.status == status)
    if autor is not None:
        consulta = consulta.where(ItemTreinamento.criado_por_id == autor.id)
    return list(session.execute(consulta).scalars())


def _obter(session: Session, item_id) -> ItemTreinamento:
    item = session.get(ItemTreinamento, item_id)
    if item is None:
        raise ItemNaoEncontradoError(f"Item {item_id} não encontrado.")
    return item


def aprovar(session: Session, item_id, *, aprovador: User) -> ItemTreinamento:
    item = _obter(session, item_id)
    if item.status != StatusDocumento.PENDENTE:
        raise DecisaoInvalidaError(f"Este item já foi {item.status.value}.")

    item.status = StatusDocumento.APROVADO
    item.decidido_por_id = aprovador.id
    item.decidido_em = datetime.now(timezone.utc)
    session.flush()
    _indexar(session, item)
    return item


def recusar(session: Session, item_id, *, aprovador: User, motivo: str) -> ItemTreinamento:
    """Recusa com motivo obrigatório e TIRA DO ÍNDICE.

    A remoção acontece mesmo em item pendente (que não deveria estar indexado):
    é barata, e cobre o caso de recusar algo que foi aprovado antes."""
    from app.rag import treinamento as indice

    if not (motivo or "").strip():
        raise DecisaoInvalidaError("Informe o motivo — quem escreveu precisa saber o que corrigir.")

    item = _obter(session, item_id)
    item.status = StatusDocumento.REJEITADO
    item.decidido_por_id = aprovador.id
    item.decidido_em = datetime.now(timezone.utc)
    item.motivo_decisao = motivo.strip()
    session.flush()
    indice.remover(item.id)
    return item


def excluir(session: Session, item_id) -> None:
    """Apaga de vez — do índice e do banco.

    Diferente de usuário (onde "excluir" é desativar, para preservar
    histórico): um item de treinamento não carrega histórico de ninguém, e
    manter lixo curado atrapalha quem for revisar a base depois."""
    from app.rag import treinamento as indice

    item = _obter(session, item_id)
    indice.remover(item.id)
    session.delete(item)
    session.flush()
