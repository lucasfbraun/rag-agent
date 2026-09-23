"""Operações de histórico de conversas limitadas ao usuário proprietário."""
import re
import unicodedata
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Conversation, ConversationMessage


class ConversationNotFoundError(Exception):
    pass


_PADRAO_PEDIDO_ARQUIVO = re.compile(
    r"\b(?:"
    r"arquivo|arquivos|pdf|download|baixar|baixe|fonte|fontes|"
    r"boletim|boletins|fispq|ficha|documento|documentos"
    r")\b",
    re.IGNORECASE,
)

_TERMOS_GENERICOS_DE_ARQUIVO = {
    "agora", "arquivo", "arquivos", "pdf", "download", "baixar", "baixe",
    "fonte", "fontes", "boletim", "boletins", "fispq", "ficha", "documento",
    "documentos", "traga", "trazer", "manda", "mande", "envia", "envie",
    "me", "o", "a", "os", "as", "um", "uma", "do", "da", "de", "para",
}


def _normalizar_texto(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode(
        "ascii", "ignore"
    ).decode()
    return sem_acento.lower()


def _termos_de_filtro_do_pedido(query: str) -> list[str]:
    termos = re.findall(r"[a-zA-Z0-9]+", _normalizar_texto(query))
    return [
        termo for termo in termos
        if len(termo) >= 2 and termo not in _TERMOS_GENERICOS_DE_ARQUIVO
    ]


def _filtrar_source_refs(source_refs: list[dict], query: str) -> list[dict]:
    termos = _termos_de_filtro_do_pedido(query)
    if not termos:
        return source_refs
    filtradas = []
    for ref in source_refs:
        nome = _normalizar_texto(str(ref.get("nome_arquivo") or ""))
        if all(termo in nome for termo in termos):
            filtradas.append(ref)
    return filtradas


def responder_pedido_de_arquivo(
    conversation: Conversation, query: str
) -> dict | None:
    """Atalho deterministico para "me traga o arquivo".

    Usa a ultima resposta do assistente que ja tenha `source_refs`; nao chama
    RAG nem LLM. Isso evita refazer busca e, principalmente, evita o modelo
    escolher outro arquivo quando o usuario pediu o arquivo da resposta anterior.
    """
    if not _PADRAO_PEDIDO_ARQUIVO.search(query or ""):
        return None

    for message in reversed(conversation.messages):
        if message.role != "assistant":
            continue
        source_refs = list(message.source_refs or [])
        if not source_refs:
            continue
        filtradas = _filtrar_source_refs(source_refs, query)
        if not filtradas:
            return {
                "answer": (
                    "Encontrei arquivos na resposta anterior, mas nenhum deles "
                    "bate com o termo solicitado. Use o botao de download nas "
                    "fontes exibidas ou peca pelo nome do arquivo."
                ),
                "sources": [],
                "source_refs": [],
                "model_used": "atalho-download-fontes",
            }
        nomes = [str(ref.get("nome_arquivo") or "arquivo") for ref in filtradas]
        return {
            "answer": (
                "Encontrei o(s) arquivo(s) consultado(s) na resposta anterior: "
                + ", ".join(nomes)
                + ". Use o download abaixo."
            ),
            "sources": nomes,
            "source_refs": filtradas,
            "model_used": "atalho-download-fontes",
        }

    return {
        "answer": (
            "Ainda nao tenho um arquivo recuperado nesta conversa. Peca primeiro "
            "informacoes sobre um produto ou documento; quando eu consultar o "
            "acervo, o download aparecera junto das fontes."
        ),
        "sources": [],
        "source_refs": [],
        "model_used": "atalho-download-fontes",
    }


def create_conversation(session: Session, *, user_id: uuid.UUID) -> Conversation:
    conversation = Conversation(user_id=user_id, title="Nova conversa")
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def get_conversation(
    session: Session, *, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> Conversation:
    conversation = (
        session.query(Conversation)
        .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .first()
    )
    if conversation is None:
        raise ConversationNotFoundError
    return conversation


def list_conversations(session: Session, *, user_id: uuid.UUID) -> list[Conversation]:
    return (
        session.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
        .all()
    )


def delete_conversation(
    session: Session, *, conversation_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    conversation = get_conversation(
        session, conversation_id=conversation_id, user_id=user_id
    )
    session.delete(conversation)
    session.commit()


def history_for_agent(conversation: Conversation) -> list[dict[str, str]]:
    """Entrega o histórico completo para regras determinísticas.

    O engine limita a janela enviada ao LLM às últimas 8 mensagens, mas
    guardrails precisam enxergar correções explícitas de toda a conversa.
    """
    return [
        {"role": message.role, "content": message.content}
        for message in conversation.messages
    ]


def save_exchange(
    session: Session,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    query: str,
    answer: str,
    sources: list[str] | None = None,
    source_refs: list[dict] | None = None,
    model_used: str | None = None,
    caminho: str | None = None,
    termos_busca: list[str] | None = None,
) -> Conversation:
    """Grava o par pergunta/resposta.

    `caminho` e `termos_busca` (2026-09-18) são o rastro de recuperação: qual
    dos mecanismos do motor atendeu e o que a busca textual efetivamente
    procurou. Ficam na mensagem do assistente porque é ela que o técnico
    julga depois — sem eles, o relatório de validação sabe QUANTO o sistema
    erra mas não ONDE. Default `None` mantém compatível quem chama sem passar
    (e é o valor de toda mensagem anterior a esta mudança).
    """
    if conversation_id is None:
        conversation = Conversation(user_id=user_id, title="Nova conversa")
        session.add(conversation)
        session.flush()
    else:
        conversation = get_conversation(
            session, conversation_id=conversation_id, user_id=user_id
        )

    if not conversation.messages:
        normalized_title = " ".join(query.split())
        conversation.title = normalized_title[:80] or "Nova conversa"

    now = datetime.now(timezone.utc)
    session.add_all(
        [
            ConversationMessage(
                conversation_id=conversation.id,
                role="user",
                content=query,
                sources=[],
                created_at=now,
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
                sources=sources or [],
                source_refs=source_refs or [],
                model_used=model_used,
                caminho=caminho,
                termos_busca=list(termos_busca) if termos_busca else None,
            ),
        ]
    )
    conversation.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(conversation)
    return conversation
