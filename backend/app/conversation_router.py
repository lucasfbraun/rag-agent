"""API autenticada do histórico de conversas."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.conversation_service import (
    ConversationNotFoundError,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
)
from app.db import get_session
from app.models import Conversation, User


router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    sources: list[str]
    model_used: str | None
    created_at: datetime


class ConversationResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessageResponse]


class ConversationSummaryResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


def _response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=str(conversation.id),
        title=conversation.title,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        messages=[
            ConversationMessageResponse(
                id=str(message.id),
                role=message.role,
                content=message.content,
                sources=message.sources or [],
                model_used=message.model_used,
                created_at=message.created_at,
            )
            for message in conversation.messages
        ],
    )


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create(
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    return _response(create_conversation(session, user_id=current_user.id))


@router.get("", response_model=list[ConversationSummaryResponse])
def list_all(
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    return [
        ConversationSummaryResponse(
            id=str(conversation.id),
            title=conversation.title,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )
        for conversation in list_conversations(session, user_id=current_user.id)
    ]


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_by_id(
    conversation_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    try:
        return _response(
            get_conversation(
                session, conversation_id=conversation_id, user_id=current_user.id
            )
        )
    except ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_by_id(
    conversation_id: uuid.UUID,
    current_user: User = Depends(require_permission(Permission.VIEW_CATALOG)),
    session: Session = Depends(get_session),
):
    try:
        delete_conversation(
            session, conversation_id=conversation_id, user_id=current_user.id
        )
    except ConversationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversa não encontrada.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
