from __future__ import annotations

import asyncio
import json
import typing

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import operators
from app.api.routes.common import not_found
from app.core.config import settings
from app.core.database import get_db
from app.core.logging import redact
from app.models import ChatConversation, ChatMessage, User
from app.schemas import ChatConversationCreate, ChatConversationOut, ChatMessageCreate, ChatMessageOut, ChatStatusOut
from app.services.audit import write_audit
from app.services.chat import (Generation, active_generations, chat_status, conversation_context,
                               model_client, start_generation)

router = APIRouter(prefix="/chat", tags=["chat"])


def owned_conversation(db: Session, conversation_id: int, actor: User) -> ChatConversation:
    item = db.scalar(select(ChatConversation).where(ChatConversation.id == conversation_id,
                                                   ChatConversation.user_id == actor.id))
    if item is None:
        raise not_found("对话")
    return item


@router.get("/status", response_model=ChatStatusOut)
def status(actor: User = Depends(operators), db: Session = Depends(get_db)):
    return chat_status(db, actor.id)


@router.get("/conversations", response_model=typing.List[ChatConversationOut])
def conversations(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                  actor: User = Depends(operators), db: Session = Depends(get_db)):
    return db.scalars(select(ChatConversation).where(ChatConversation.user_id == actor.id)
                      .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc()).offset(offset).limit(limit)).all()


@router.post("/conversations", response_model=ChatConversationOut, status_code=201)
def create_conversation(payload: ChatConversationCreate, request: Request,
                        actor: User = Depends(operators), db: Session = Depends(get_db)):
    item = ChatConversation(user_id=actor.id, mode=payload.mode)
    db.add(item)
    db.flush()
    write_audit(db, "chat.create", "chat_conversation", item.id, actor, request)
    db.commit()
    db.refresh(item)
    return item


@router.get("/conversations/{conversation_id}/messages", response_model=typing.List[ChatMessageOut])
def messages(conversation_id: int, before_id: typing.Optional[int] = Query(None, ge=1),
             limit: int = Query(50, ge=1, le=100), actor: User = Depends(operators), db: Session = Depends(get_db)):
    owned_conversation(db, conversation_id, actor)
    query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    if before_id:
        query = query.where(ChatMessage.id < before_id)
    return list(reversed(db.scalars(query.order_by(ChatMessage.id.desc()).limit(limit)).all()))


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: int, request: Request,
                              actor: User = Depends(operators), db: Session = Depends(get_db)):
    item = owned_conversation(db, conversation_id, actor)
    if conversation_id in active_generations:
        raise HTTPException(409, detail={"code": "CHAT_BUSY", "message": "请先停止生成再删除对话"})
    db.delete(item)
    write_audit(db, "chat.delete", "chat_conversation", conversation_id, actor, request)
    db.commit()


@router.post("/conversations/{conversation_id}/cancel")
async def cancel(conversation_id: int, actor: User = Depends(operators), db: Session = Depends(get_db)):
    owned_conversation(db, conversation_id, actor)
    job = active_generations.get(conversation_id)
    if job and job.task:
        job.task.cancel()
    return {"status": "cancelling" if job else "idle"}


@router.post("/conversations/{conversation_id}/messages", response_class=StreamingResponse)
async def send_message(conversation_id: int, payload: ChatMessageCreate, request: Request,
                       actor: User = Depends(operators), db: Session = Depends(get_db)):
    conversation = owned_conversation(db, conversation_id, actor)
    if any(job.user_id == actor.id for job in active_generations.values()):
        raise HTTPException(409, detail={"code": "CHAT_BUSY", "message": "您已有回答正在生成，请先停止或等待完成"})
    if len(active_generations) >= settings.chat_max_concurrent:
        raise HTTPException(429, detail={"code": "CHAT_CAPACITY", "message": "智能助手繁忙，请稍后重试"}, headers={"Retry-After": "5"})
    readiness = chat_status(db, actor.id)
    error = readiness.knowledge_error if conversation.mode == "knowledge" else readiness.general_error
    if error:
        raise HTTPException(409, detail={"code": "CHAT_NOT_READY", "message": error})
    client = model_client(db, "chat", actor.id)
    embedding = model_client(db, "embedding") if conversation.mode == "knowledge" else None
    content = str(redact(payload.content))
    history = conversation_context(db, conversation_id, content)
    user_message = ChatMessage(conversation_id=conversation_id, role="user", content=content)
    assistant = ChatMessage(conversation_id=conversation_id, role="assistant", status="running", model=client.model)
    db.add_all([user_message, assistant])
    if conversation.title == "新对话":
        conversation.title = content[:60]
    db.flush()
    write_audit(db, "chat.send", "chat_conversation", conversation_id, actor, request,
                detail={"message_id": assistant.id, "mode": conversation.mode, "model": client.model})
    db.commit()
    meta = {"type": "meta", "user": ChatMessageOut.model_validate(user_message).model_dump(mode="json"),
            "assistant": ChatMessageOut.model_validate(assistant).model_dump(mode="json")}
    job = Generation(conversation_id, assistant.id, actor.id)
    start_generation(job, history, client, embedding)

    async def events():
        try:
            yield "data: " + json.dumps(meta, ensure_ascii=False) + "\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(job.queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event["type"] in {"done", "error"}:
                    break
        finally:
            if job.task and not job.task.done():
                job.task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
    })
