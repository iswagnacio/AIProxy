"""
routers/client.py — 浏览器侧 API 路由

处理浏览器发来的所有请求：
- POST /sessions                        → 创建新 Session
- POST /sessions/{session_id}/continue   → 发送消息 + 流式响应
- GET  /sessions/{session_id}/history    → 查询轮次历史
- DELETE /sessions/{session_id}          → 删除 Session
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.auth import get_current_user
from app.db import get_db
from app.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    ContinueSessionRequest,
    ErrorDetail,
    HistoryResponse,
    OkResponse,
    TurnRecord,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ────────────────────────────────────────────
# POST /sessions — 创建新 Session
# ────────────────────────────────────────────

@router.post("/sessions", response_model=CreateSessionResponse)
async def client_create_new_session(
    req: CreateSessionRequest,
    user_id: str = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    创建新 Session，入队等待 Agent 领取。
    """
    from app.services.session_service import create_session
    from app.services.queue_service import enqueue_new_session
    from app.services.audit_service import log_session_created, log_session_enqueued

    session = await create_session(
        db=db,
        owner_user_id=user_id,
        stream_resume_mode=req.stream_resume_mode,
        metadata=json.dumps(req.client_meta) if req.client_meta else None,
    )

    # 记录 Session 创建事件
    await log_session_created(
        session_id=session.session_id,
        user_id=user_id,
        payload={"stream_resume_mode": req.stream_resume_mode},
    )

    await enqueue_new_session(db=db, session_id=session.session_id)

    # 记录入队事件
    await log_session_enqueued(session_id=session.session_id, queue_reason="new_session")

    return CreateSessionResponse(
        session_id=session.session_id,
        created_at=session.created_at,
        status=session.status,
    )


# ────────────────────────────────────────────
# POST /sessions/{session_id}/continue — 发送消息 + 流式响应
# ────────────────────────────────────────────

@router.post("/sessions/{session_id}/continue")
async def client_continue_session(
    session_id: str,
    req: ContinueSessionRequest,
    user_id: str = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    客户端发送用户消息，创建新轮次，打开 NDJSON 流式响应。
    """
    from app.services.session_service import (
        get_session,
        verify_session_owner,
        advance_turn,
    )
    from app.services.buffer_service import write_read_buffer
    from app.services.queue_service import maybe_requeue_session
    from app.services.stream_service import stream_bridge
    from app.services.audit_service import log_client_turn_created, log_read_buffer_written

    # 1. 校验
    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_session_owner(session, user_id)

    # 2. 创建新轮次
    turn_idx = await advance_turn(
        db=db,
        session=session,
        user_message=req.message,
        client_request_id=req.client_request_id,
    )

    # 记录轮次创建事件
    await log_client_turn_created(session_id=session_id, turn_idx=turn_idx, user_id=user_id)

    # 3. 用户消息写入 read_buffer
    buffer_event_id = await write_read_buffer(
        db=db,
        session_id=session_id,
        turn_idx=turn_idx,
        content=req.message,
    )

    # 记录 read_buffer 写入事件
    await log_read_buffer_written(
        session_id=session_id, turn_idx=turn_idx, buffer_event_id=buffer_event_id
    )

    # 4. 若无 Agent 绑定，尝试重新入队
    await maybe_requeue_session(db=db, session_id=session_id)

    # 5. 返回 NDJSON 流式响应
    return StreamingResponse(
        stream_bridge(
            db=db,
            session_id=session_id,
            turn_idx=turn_idx,
        ),
        media_type="application/x-ndjson",
    )


# ────────────────────────────────────────────
# GET /sessions/{session_id}/history — 查询历史
# ────────────────────────────────────────────

@router.get("/sessions/{session_id}/history", response_model=HistoryResponse)
async def get_history(
    session_id: str,
    start_idx: int = Query(default=1, ge=1),
    end_idx: Optional[int] = Query(default=None),
    user_id: str = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    查询指定范围的轮次历史。
    """
    from app.services.session_service import get_session, verify_session_owner

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_session_owner(session, user_id)

    query = """
        SELECT turn_idx, user_message, assistant_message, turn_status,
               created_at, completed_at
        FROM turns
        WHERE session_id = ? AND turn_idx >= ?
    """
    params: list = [session_id, start_idx]

    if end_idx is not None:
        query += " AND turn_idx <= ?"
        params.append(end_idx)

    query += " ORDER BY turn_idx ASC"

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    turns = [
        TurnRecord(
            turn_idx=row[0],
            user_message=row[1],
            assistant_message=row[2],
            turn_status=row[3],
            created_at=row[4],
            completed_at=row[5],
        )
        for row in rows
    ]

    return HistoryResponse(session_id=session_id, turns=turns)


# ────────────────────────────────────────────
# DELETE /sessions/{session_id} — 删除 Session
# ────────────────────────────────────────────

@router.delete("/sessions/{session_id}", response_model=OkResponse)
async def del_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    删除 Session（普通用户仅限自己的）。
    """
    from app.services.session_service import get_session, verify_session_owner, delete_session
    from app.services.audit_service import log_session_deleted

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_session_owner(session, user_id)

    await delete_session(db=db, session_id=session_id)

    # 记录删除事件
    await log_session_deleted(session_id=session_id, user_id=user_id, deleted_by_admin=False)

    return OkResponse()
