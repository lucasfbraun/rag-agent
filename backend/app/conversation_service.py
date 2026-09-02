"""Operações de histórico de conversas limitadas ao usuário proprietário."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Conversation, ConversationMessage


class ConversationNotFoundError(Exception):
    pass


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
    return [
        {"role": message.role, "content": message.content}
        for message in conversation.messages[-8:]
    ]


def save_exchange(
    session: Session,
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    query: str,
    answer: str,
    sources: list[str] | None = None,
    model_used: str | None = None,
) -> Conversation:
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
                model_used=model_used,
            ),
        ]
    )
    conversation.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(conversation)
    return conversation
