"""
services/queue_service.py — 新 Session 队列调度

核心职责：
- 新 Session 入队
- 失去绑定但收到新轮次的 Session 重新入队
- Agent 原子抢占新 Session
- 抢占超时回收
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.config import settings
from app.db import db_transaction
from app.models import (
    NewSessionQueueItem,
    QueueReason,
    QueueStatus,
    Session,
    SessionStatus,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────
# 入队
# ────────────────────────────────────────────

async def enqueue_new_session(
    db: aiosqlite.Connection,
    session_id: str,
    reason: str = QueueReason.NEW_SESSION.value,
) -> None:
    """
    将 Session 加入待分配队列。
    reason: "new_session" | "requeue_after_release"
    """
    now = _now_iso()
    await db.execute(
        """
        INSERT INTO new_session_queue
            (session_id, enqueued_at, queue_reason, status)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, now, reason, QueueStatus.PENDING.value),
    )
    await db.commit()
    logger.info(f"Session {session_id} 已入队 (reason={reason})")


async def maybe_requeue_session(
    db: aiosqlite.Connection,
    session_id: str,
) -> None:
    """
    如果 Session 当前无 Agent 绑定（状态为 released 或无 assigned_agent_id），
    且队列中没有该 session 的 pending 条目，则重新入队。
    """
    cursor = await db.execute(
        "SELECT assigned_agent_id, status FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return

    assigned_agent_id, status = row

    # 只在无绑定 Agent 时重新入队
    if assigned_agent_id is not None and status not in (
        SessionStatus.RELEASED.value,
        SessionStatus.NEW.value,
    ):
        return

    # 检查是否已在队列中 pending
    cursor2 = await db.execute(
        """
        SELECT COUNT(*) FROM new_session_queue
        WHERE session_id = ? AND status = ?
        """,
        (session_id, QueueStatus.PENDING.value),
    )
    (count,) = await cursor2.fetchone()
    if count > 0:
        return  # 已有 pending 条目，不重复入队

    await enqueue_new_session(
        db=db,
        session_id=session_id,
        reason=QueueReason.REQUEUE_AFTER_RELEASE.value,
    )


# ────────────────────────────────────────────
# Agent 抢占
# ────────────────────────────────────────────

async def claim_session_from_queue(
    db: aiosqlite.Connection,
    agent_id: str,
) -> Optional[Session]:
    """
    从 new_session_queue 中原子取出一个 pending 条目，
    绑定到该 Agent。

    返回被分配的 Session 对象，无可用时返回 None。
    """
    from app.services.session_service import get_session, assign_agent

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    deadline = (now + timedelta(seconds=settings.AGENT_CLAIM_CONFIRM_TIMEOUT)).isoformat()

    async with db_transaction(db) as conn:
        # 1. 回收已过期的 claimed 条目
        await conn.execute(
            """
            UPDATE new_session_queue
            SET status = ?, claimed_by_agent_id = NULL,
                claimed_at = NULL, claim_deadline_at = NULL
            WHERE status = ? AND claim_deadline_at < ?
            """,
            (QueueStatus.PENDING.value, QueueStatus.CLAIMED.value, now_iso),
        )

        # 2. 取出一个 pending 条目（FIFO）
        cursor = await conn.execute(
            """
            SELECT id, session_id FROM new_session_queue
            WHERE status = ?
            ORDER BY enqueued_at ASC
            LIMIT 1
            """,
            (QueueStatus.PENDING.value,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        queue_id, session_id = row

        # 3. 标记为 claimed
        await conn.execute(
            """
            UPDATE new_session_queue
            SET status = ?, claimed_by_agent_id = ?,
                claimed_at = ?, claim_deadline_at = ?
            WHERE id = ?
            """,
            (QueueStatus.CLAIMED.value, agent_id, now_iso, deadline, queue_id),
        )

    # 4. 绑定 Session
    lease_expires = (now + timedelta(seconds=settings.AGENT_LEASE_TIMEOUT)).isoformat()
    await assign_agent(db=db, session_id=session_id, agent_id=agent_id, lease_expires_at=lease_expires)

    # 5. 更新队列条目为 confirmed
    await db.execute(
        "UPDATE new_session_queue SET status = ? WHERE id = ?",
        (QueueStatus.CONFIRMED.value, queue_id),
    )
    await db.commit()

    session = await get_session(db, session_id)
    logger.info(f"Agent {agent_id} 抢占 Session {session_id}")
    return session


# ────────────────────────────────────────────
# 超时回收（供 lease_checker 调用）
# ────────────────────────────────────────────

async def expire_unclaimed_queue_items(db: aiosqlite.Connection) -> int:
    """
    回收队列中 claimed 但已超过 deadline 的条目，
    将其状态重置为 pending。
    返回回收数量。
    """
    now_iso = _now_iso()
    cursor = await db.execute(
        """
        UPDATE new_session_queue
        SET status = ?, claimed_by_agent_id = NULL,
            claimed_at = NULL, claim_deadline_at = NULL
        WHERE status = ? AND claim_deadline_at < ?
        """,
        (QueueStatus.PENDING.value, QueueStatus.CLAIMED.value, now_iso),
    )
    await db.commit()
    count = cursor.rowcount
    if count > 0:
        logger.info(f"回收了 {count} 个超时的队列条目")
    return count
