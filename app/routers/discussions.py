"""Discussions: chat with the AI about Research Bank papers. Each person's discussions are their own."""
from __future__ import annotations

from datetime import datetime, timezone

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_current_user, log_activity
from ..database import get_db
from ..models import ResearchChat, ResearchDoc, User
from ..services import research_chat

router = APIRouter(prefix="/api/discussions", tags=["research discussions"])


def _papers(db: Session, ids: list[int]) -> list[ResearchDoc]:
    docs = [db.get(ResearchDoc, int(i)) for i in dict.fromkeys(ids or [])]
    return [d for d in docs if d is not None]


def _mine(db: Session, chat_id: int, user: User) -> ResearchChat:
    chat = db.get(ResearchChat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(404, "Discussion not found")
    return chat


def _out(db: Session, chat: ResearchChat, full: bool = True) -> dict:
    papers = _papers(db, chat.doc_ids or [])
    msgs = chat.messages or []
    out = {"id": chat.id, "title": chat.title, "created_at": chat.created_at, "updated_at": chat.updated_at,
           "papers": [{"id": d.id, "title": d.title, "category": d.category} for d in papers],
           "message_count": len(msgs),
           "last_message": (msgs[-1]["content"][:140] if msgs else None)}
    if full:
        out["messages"] = msgs
    return out


@router.get("")
def list_chats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chats = db.scalars(select(ResearchChat).where(ResearchChat.user_id == user.id)
                       .order_by(ResearchChat.updated_at.desc()).limit(100))
    return [_out(db, c, full=False) for c in chats]


@router.post("", status_code=201)
def start_chat(body: dict, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ids = [int(i) for i in (body.get("paper_ids") or []) if str(i).isdigit()]
    papers = _papers(db, ids)
    if not papers:
        raise HTTPException(422, "Choose at least one paper to discuss")
    if len(papers) > research_chat.MAX_PAPERS:
        raise HTTPException(422, f"Discuss up to {research_chat.MAX_PAPERS} papers at a time")
    title = (body.get("title") or "").strip() or ("Discussion: " + (papers[0].title if len(papers) == 1 else
                                                                    f"{papers[0].title} and {len(papers) - 1} more"))
    chat = ResearchChat(user_id=user.id, title=title[:200], doc_ids=[d.id for d in papers], messages=[])
    db.add(chat)
    db.commit()
    log_activity(db, user, "discussion_started", chat.title[:255], f"papers={chat.doc_ids}", request)
    return _out(db, chat)


@router.get("/{chat_id}")
def get_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _out(db, _mine(db, chat_id, user))


@router.patch("/{chat_id}")
def update_chat(chat_id: int, body: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = _mine(db, chat_id, user)
    if "title" in body and str(body["title"] or "").strip():
        chat.title = str(body["title"]).strip()[:200]
    if "paper_ids" in body:
        papers = _papers(db, [int(i) for i in body["paper_ids"] or [] if str(i).isdigit()])
        if not papers:
            raise HTTPException(422, "Keep at least one paper in the discussion")
        if len(papers) > research_chat.MAX_PAPERS:
            raise HTTPException(422, f"Discuss up to {research_chat.MAX_PAPERS} papers at a time")
        chat.doc_ids = [d.id for d in papers]
    db.commit()
    return _out(db, chat)


@router.post("/{chat_id}/messages")
def send_message(chat_id: int, body: dict, request: Request, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    chat = _mine(db, chat_id, user)
    text = str(body.get("message") or "").strip()
    if not text:
        raise HTTPException(422, "Type a message")
    if len(text) > 4000:
        raise HTTPException(422, "Keep the message under 4,000 characters")
    papers = [{"id": d.id, "title": d.title, "text": d.text or ""} for d in _papers(db, chat.doc_ids or [])]
    if not any(p["text"].strip() for p in papers):
        raise HTTPException(422, "These papers have no readable text to discuss")
    history = list(chat.messages or [])
    now = datetime.now(timezone.utc).isoformat()
    answer = research_chat.reply(papers, history, text)
    mine = {"role": "user", "content": text, "at": now}
    theirs = {"role": "assistant", "content": answer["content"], "quotes": answer["quotes"],
              "method": answer["method"], "warning": answer["warning"], "at": datetime.now(timezone.utc).isoformat()}
    chat.messages = history + [mine, theirs]       # a new list so the JSON column is saved
    if not history and chat.title.startswith("Discussion: "):
        chat.title = text[:80] + ("…" if len(text) > 80 else "")
    db.commit()
    log_activity(db, user, "discussion_message", chat.title[:255], text[:300], request)
    return {"message": mine, "reply": theirs, "title": chat.title}


@router.post("/{chat_id}/messages/stream")
def send_message_stream(chat_id: int, body: dict, request: Request, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """Like /messages, but the reply streams as it is written (server-sent events): 'chunk' events with
    text, then one 'done' event with the saved messages."""
    from ..database import SessionLocal

    chat = _mine(db, chat_id, user)
    text = str(body.get("message") or "").strip()
    if not text:
        raise HTTPException(422, "Type a message")
    if len(text) > 4000:
        raise HTTPException(422, "Keep the message under 4,000 characters")
    papers = [{"id": d.id, "title": d.title, "text": d.text or ""} for d in _papers(db, chat.doc_ids or [])]
    if not any(p["text"].strip() for p in papers):
        raise HTTPException(422, "These papers have no readable text to discuss")
    history = list(chat.messages or [])
    user_id, ip = user.id, (request.client.host if request.client else None)
    mine = {"role": "user", "content": text, "at": datetime.now(timezone.utc).isoformat()}

    def events():
        result = None
        for kind, value in research_chat.stream_reply(papers, history, text):
            if kind == "chunk":
                yield f"data: {json.dumps({'type': 'chunk', 'text': value})}\n\n"
            else:
                result = value
        theirs = {"role": "assistant", "content": result["content"], "quotes": result["quotes"],
                  "method": result["method"], "warning": result["warning"], "at": datetime.now(timezone.utc).isoformat()}
        with SessionLocal() as s:                    # the request's session may already be closed
            c = s.get(ResearchChat, chat_id)
            if c is not None and c.user_id == user_id:
                c.messages = list(c.messages or []) + [mine, theirs]
                if not history and c.title.startswith("Discussion: "):
                    c.title = text[:80] + ("…" if len(text) > 80 else "")
                s.commit()
                title = c.title
                from ..models import ActivityLog
                s.add(ActivityLog(user_id=user_id, user_email=s.get(User, user_id).email, action="discussion_message",
                                  target=title[:255], detail=text[:300], ip=ip))
                s.commit()
            else:
                title = None
        yield f"data: {json.dumps({'type': 'done', 'message': mine, 'reply': theirs, 'title': title})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/{chat_id}", status_code=204)
def delete_chat(chat_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = _mine(db, chat_id, user)
    db.delete(chat)
    db.commit()
