"""
routers/agent.py — Agent 侧 API 路由

处理内网 AI Agent 发来的所有请求：
- POST /sessions/get_new                   → 原子抢占新 Session
- POST /sessions/{session_id}/read         → 阻塞读取客户端消息
- POST /sessions/{session_id}/write        → 写入 chunk/full/end/error
- POST /sessions/{session_id}/keep_alive   → 刷新 Agent 活跃租约
- POST /sessions/{session_id}/release      → 主动释放 Session

所有路由均需 Header: X-Agent-Id + X-Agent-Token
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_agent
from app.db import get_db
from app.schemas import (
    GetNewSessionResponse,
    KeepAliveRequest,
    KeepAliveResponse,
    OkResponse,
    ReadMsgRequest,
    ReadMsgResponse,
    ReleaseSessionRequest,
    SendMsgRequest,
    SendMsgResponse,
    SessionSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ────────────────────────────────────────────
# POST /sessions/get_new — Agent 抢占新 Session
# ────────────────────────────────────────────

@router.post("/sessions/get_new", response_model=GetNewSessionResponse)
async def server_get_new_session(
    agent_id: str = Depends(get_current_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    从 new_session_queue 中原子取出一个未绑定 Session，
    绑定到该 Agent。30 秒内需 keep_alive 或 read_msg，否则回收。
    """
    from app.services.queue_service import claim_session_from_queue
    from app.services.audit_service import log_session_assigned

    session = await claim_session_from_queue(db=db, agent_id=agent_id)

    if session is None:
        return GetNewSessionResponse(session=None)

    # 记录 Session 分配事件
    await log_session_assigned(session_id=session.session_id, agent_id=agent_id)

    return GetNewSessionResponse(
        session=SessionSummary(
            session_id=session.session_id,
            owner_user_id=session.owner_user_id,
            created_at=session.created_at,
            status=session.status,
            next_turn_idx=session.next_turn_idx,
            metadata=session.metadata,
            stream_resume_mode=session.stream_resume_mode,
        )
    )


# ────────────────────────────────────────────
# POST /sessions/{session_id}/read — 读取客户端消息
# ────────────────────────────────────────────

@router.post("/sessions/{session_id}/read", response_model=ReadMsgResponse)
async def server_read_msg_from_client(
    session_id: str,
    req: ReadMsgRequest,
    agent_id: str = Depends(get_current_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    阻塞等待该 Session 未消费的 read_buffer 消息。
    读取后只更新 consumed 游标，不删除原始日志。
    自动刷新 agent_keep_alive。
    """
    from app.services.session_service import get_session, verify_agent_binding
    from app.services.buffer_service import consume_read_buffer
    from app.services.agent_service import refresh_lease
    from app.services.audit_service import log_read_buffer_consumed

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_agent_binding(session, agent_id)

    # 刷新租约
    await refresh_lease(db=db, session_id=session_id)

    # 阻塞等待消息
    message = await consume_read_buffer(
        db=db,
        session_id=session_id,
        block_timeout_sec=req.block_timeout_sec,
    )

    turn_idx = session.next_turn_idx - 1 if session.next_turn_idx > 1 else 1

    # 记录消费事件
    if message:
        await log_read_buffer_consumed(
            session_id=session_id,
            turn_idx=turn_idx,
            agent_id=agent_id,
            buffer_event_id=message.buffer_event_id,
        )

    return ReadMsgResponse(
        session_id=session_id,
        turn_idx=turn_idx,
        message=message,
    )


# ────────────────────────────────────────────
# POST /sessions/{session_id}/write — 写入回复
# ────────────────────────────────────────────

@router.post("/sessions/{session_id}/write", response_model=SendMsgResponse)
async def server_send_msg_to_client(
    session_id: str,
    req: SendMsgRequest,
    agent_id: str = Depends(get_current_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Agent 写入 chunk/full/end/error 到 write_buffer。
    """
    from app.services.session_service import get_session, verify_agent_binding
    from app.services.buffer_service import write_write_buffer
    from app.services.agent_service import refresh_lease
    from app.services.audit_service import log_write_buffer_written, log_turn_completed, log_turn_error
    from app.models import BufferMsgType

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_agent_binding(session, agent_id)

    # 写入 write_buffer
    event_id = await write_write_buffer(
        db=db,
        session_id=session_id,
        turn_idx=req.turn_idx,
        msg_type=req.msg_type,
        content=req.content,
    )

    # 记录写入事件
    await log_write_buffer_written(
        session_id=session_id,
        turn_idx=req.turn_idx,
        agent_id=agent_id,
        msg_type=req.msg_type,
        buffer_event_id=event_id,
    )

    # 如果轮次完成或出错，记录相应事件
    if req.msg_type in (BufferMsgType.FULL.value, BufferMsgType.END.value):
        await log_turn_completed(session_id=session_id, turn_idx=req.turn_idx)
    elif req.msg_type == BufferMsgType.ERROR.value:
        error_msg = req.content or "Agent reported error"
        await log_turn_error(session_id=session_id, turn_idx=req.turn_idx, error_message=error_msg)

    # 刷新租约
    await refresh_lease(db=db, session_id=session_id)

    return SendMsgResponse(buffer_event_id=event_id)


# ────────────────────────────────────────────
# POST /sessions/{session_id}/keep_alive — 心跳
# ────────────────────────────────────────────

@router.post("/sessions/{session_id}/keep_alive", response_model=KeepAliveResponse)
async def agent_keep_alive(
    session_id: str,
    req: KeepAliveRequest,
    agent_id: str = Depends(get_current_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    刷新 Agent 活跃租约。长耗时处理中应每 20 秒调用一次。
    """
    from app.services.session_service import get_session, verify_agent_binding
    from app.services.agent_service import refresh_lease

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_agent_binding(session, agent_id)

    lease_expires_at = await refresh_lease(db=db, session_id=session_id)

    return KeepAliveResponse(lease_expires_at=lease_expires_at)


# ────────────────────────────────────────────
# POST /sessions/{session_id}/release — 主动释放
# ────────────────────────────────────────────

@router.post("/sessions/{session_id}/release", response_model=OkResponse)
async def agent_release_session(
    session_id: str,
    req: ReleaseSessionRequest,
    agent_id: str = Depends(get_current_agent),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    Agent 主动释放 Session 绑定。
    """
    from app.services.session_service import get_session, verify_agent_binding, release_session
    from app.services.audit_service import log_session_released

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")
    verify_agent_binding(session, agent_id)

    await release_session(db=db, session_id=session_id, reason=req.reason)

    # 记录释放事件
    await log_session_released(session_id=session_id, agent_id=agent_id, reason=req.reason)

    return OkResponse()
