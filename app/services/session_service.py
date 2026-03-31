"""
services/session_service.py — Session 生命周期管理

核心职责：
- Session 创建、状态流转
- Session-Agent 绑定/释放
- 权限校验
- 活跃/不活跃识别
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
from fastapi import HTTPException, status

from app.config import settings
from app.models import Session, SessionStatus, TurnStatus

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


# ────────────────────────────────────────────
# 查询
# ────────────────────────────────────────────

async def get_session(db: aiosqlite.Connection, session_id: str) -> Optional[Session]:
    """根据 session_id 查询 Session，不存在返回 None。"""
    cursor = await db.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND deleted = 0",
        (session_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    cols = [desc[0] for desc in cursor.description]
    data = dict(zip(cols, row))
    # 布尔转换
    data["requeue_on_new_turn"] = bool(data.get("requeue_on_new_turn", 0))
    data["deleted"] = bool(data.get("deleted", 0))
    data["expired"] = bool(data.get("expired", 0))
    return Session(**data)


# ────────────────────────────────────────────
# 创建
# ────────────────────────────────────────────

async def create_session(
    db: aiosqlite.Connection,
    owner_user_id: str,
    stream_resume_mode: str = "client_reconnect_required",
    metadata: Optional[str] = None,
) -> Session:
    """创建新 Session，状态为 new。"""
    now = _now_iso()
    session = Session(
        session_id=_generate_session_id(),
        owner_user_id=owner_user_id,
        created_at=now,
        updated_at=now,
        status=SessionStatus.NEW.value,
        stream_resume_mode=stream_resume_mode,
        metadata=metadata,
    )

    await db.execute(
        """
        INSERT INTO sessions
            (session_id, owner_user_id, created_at, updated_at, status,
             stream_resume_mode, next_turn_idx, metadata, deleted, expired)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            session.session_id,
            session.owner_user_id,
            session.created_at,
            session.updated_at,
            session.status,
            session.stream_resume_mode,
            session.next_turn_idx,
            session.metadata,
        ),
    )
    await db.commit()

    logger.info(f"Session 已创建: {session.session_id} (owner={owner_user_id})")
    return session


# ────────────────────────────────────────────
# 权限校验
# ────────────────────────────────────────────

def verify_session_owner(session: Session, user_id: str) -> None:
    """验证 user_id 是否为 Session Owner。"""
    if session.owner_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作此 Session",
        )


def verify_agent_binding(session: Session, agent_id: str) -> None:
    """验证 Agent 是否绑定了该 Session。"""
    if session.assigned_agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent '{agent_id}' 未绑定此 Session",
        )


# ────────────────────────────────────────────
# 轮次管理
# ────────────────────────────────────────────

async def advance_turn(
    db: aiosqlite.Connection,
    session: Session,
    user_message: str,
    client_request_id: Optional[str] = None,
) -> int:
    """
    创建新轮次，推进 next_turn_idx。
    返回本轮的 turn_idx。
    """
    now = _now_iso()
    turn_idx = session.next_turn_idx

    # 插入 turn 记录
    await db.execute(
        """
        INSERT INTO turns (session_id, turn_idx, user_message, turn_status,
                           client_request_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session.session_id,
            turn_idx,
            user_message,
            TurnStatus.PENDING_AGENT.value,
            client_request_id,
            now,
        ),
    )

    # 更新 session
    await db.execute(
        """
        UPDATE sessions
        SET next_turn_idx = ?,
            last_client_turn_at = ?,
            updated_at = ?,
            status = ?
        WHERE session_id = ?
        """,
        (
            turn_idx + 1,
            now,
            now,
            SessionStatus.WAITING.value,
            session.session_id,
        ),
    )
    await db.commit()

    logger.info(f"Turn 已创建: session={session.session_id}, turn_idx={turn_idx}")
    return turn_idx


# ────────────────────────────────────────────
# 状态流转
# ────────────────────────────────────────────

async def update_session_status(
    db: aiosqlite.Connection,
    session_id: str,
    new_status: str,
) -> None:
    """更新 Session 状态。"""
    now = _now_iso()
    await db.execute(
        "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
        (new_status, now, session_id),
    )
    await db.commit()


async def assign_agent(
    db: aiosqlite.Connection,
    session_id: str,
    agent_id: str,
    lease_expires_at: str,
) -> None:
    """将 Session 绑定到 Agent。"""
    now = _now_iso()
    await db.execute(
        """
        UPDATE sessions
        SET assigned_agent_id = ?,
            assigned_at = ?,
            agent_lease_expires_at = ?,
            status = ?,
            updated_at = ?
        WHERE session_id = ?
        """,
        (agent_id, now, lease_expires_at, SessionStatus.ASSIGNED.value, now, session_id),
    )
    await db.commit()
    logger.info(f"Session {session_id} 已绑定到 Agent {agent_id}")


async def release_session(
    db: aiosqlite.Connection,
    session_id: str,
    reason: Optional[str] = None,
) -> None:
    """释放 Session 的 Agent 绑定。"""
    now = _now_iso()
    await db.execute(
        """
        UPDATE sessions
        SET assigned_agent_id = NULL,
            assigned_at = NULL,
            agent_lease_expires_at = NULL,
            status = ?,
            requeue_on_new_turn = 1,
            updated_at = ?
        WHERE session_id = ?
        """,
        (SessionStatus.RELEASED.value, now, session_id),
    )
    await db.commit()
    logger.info(f"Session {session_id} 已释放 (reason={reason})")


async def delete_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> None:
    """软删除 Session。"""
    now = _now_iso()
    await db.execute(
        """
        UPDATE sessions
        SET deleted = 1, status = ?, updated_at = ?
        WHERE session_id = ?
        """,
        (SessionStatus.DELETED.value, now, session_id),
    )
    await db.commit()
    logger.info(f"Session {session_id} 已删除")
