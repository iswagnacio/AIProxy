"""
services/agent_service.py — Agent 管理

核心职责：
- Agent 注册/查询
- keep_alive 处理（刷新租约）
- Agent 失联检测辅助
- Agent-Session 绑定统计
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────
# 租约管理
# ────────────────────────────────────────────

async def refresh_lease(
    db: aiosqlite.Connection,
    session_id: str,
) -> str:
    """
    刷新 Session 的 Agent 租约（agent_lease_expires_at）。
    同时更新 last_agent_activity_at。

    Returns:
        新的 lease_expires_at ISO 时间字符串
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_expires = (now + timedelta(seconds=settings.AGENT_LEASE_TIMEOUT)).isoformat()

    await db.execute(
        """
        UPDATE sessions
        SET agent_lease_expires_at = ?,
            last_agent_activity_at = ?,
            updated_at = ?
        WHERE session_id = ?
        """,
        (lease_expires, now_iso, now_iso, session_id),
    )
    await db.commit()

    logger.debug(f"租约已刷新: session={session_id}, expires={lease_expires}")
    return lease_expires


# ────────────────────────────────────────────
# 失联检测辅助
# ────────────────────────────────────────────

async def find_expired_leases(db: aiosqlite.Connection) -> List[dict]:
    """
    查找所有租约已过期的 Session。
    （assigned / streaming 状态且 agent_lease_expires_at 已过期）

    Returns:
        [{"session_id": ..., "assigned_agent_id": ..., "status": ...}, ...]
    """
    now_iso = _now_iso()
    cursor = await db.execute(
        """
        SELECT session_id, assigned_agent_id, status
        FROM sessions
        WHERE assigned_agent_id IS NOT NULL
          AND agent_lease_expires_at IS NOT NULL
          AND agent_lease_expires_at < ?
          AND status IN ('assigned', 'streaming', 'waiting', 'idle')
          AND deleted = 0
        """,
        (now_iso,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "session_id": row[0],
            "assigned_agent_id": row[1],
            "status": row[2],
        }
        for row in rows
    ]


# ────────────────────────────────────────────
# Agent 绑定统计
# ────────────────────────────────────────────

async def get_agent_bound_count(
    db: aiosqlite.Connection,
    agent_id: str,
) -> int:
    """查询 Agent 当前绑定的 Session 数量。"""
    cursor = await db.execute(
        """
        SELECT COUNT(*) FROM sessions
        WHERE assigned_agent_id = ? AND deleted = 0 AND expired = 0
        """,
        (agent_id,),
    )
    (count,) = await cursor.fetchone()
    return count


async def get_all_agents(db: aiosqlite.Connection) -> List[dict]:
    """获取所有注册的 Agent 信息。"""
    cursor = await db.execute(
        "SELECT agent_id, last_seen_at, enabled FROM agent_registry"
    )
    rows = await cursor.fetchall()
    result = []
    for row in rows:
        agent_id, last_seen_at, enabled = row
        bound = await get_agent_bound_count(db, agent_id)
        result.append({
            "agent_id": agent_id,
            "last_seen_at": last_seen_at,
            "enabled": bool(enabled),
            "bound_sessions": bound,
        })
    return result
