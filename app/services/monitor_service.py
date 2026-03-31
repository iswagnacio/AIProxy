"""
services/monitor_service.py — 监控服务

核心职责：提供系统运行状态的聚合查询。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.models import SessionStatus, TurnStatus
from app.schemas import AgentInfo, MonitorResponse
from app.services.agent_service import get_all_agents

logger = logging.getLogger(__name__)


async def get_system_stats(db: aiosqlite.Connection) -> MonitorResponse:
    """聚合系统监控指标。"""
    now = datetime.now(timezone.utc)
    one_hour_ago = (now - timedelta(hours=1)).isoformat()

    # ── 总 Session 数 ──
    cur = await db.execute("SELECT COUNT(*) FROM sessions WHERE deleted = 0")
    (total_sessions,) = await cur.fetchone()

    # ── 活跃 Session（1 小时内有客户端新轮次） ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM sessions WHERE deleted = 0 AND last_client_turn_at > ?",
        (one_hour_ago,),
    )
    (active_sessions,) = await cur.fetchone()

    inactive_sessions = total_sessions - active_sessions

    # ── 队列中等待分配 ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM new_session_queue WHERE status = 'pending'"
    )
    (queued_new_sessions,) = await cur.fetchone()

    # ── 已绑定 Agent ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM sessions WHERE assigned_agent_id IS NOT NULL AND deleted = 0"
    )
    (assigned_sessions,) = await cur.fetchone()

    # ── 已释放 ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM sessions WHERE status = ? AND deleted = 0",
        (SessionStatus.RELEASED.value,),
    )
    (released_sessions,) = await cur.fetchone()

    # ── 未消费的 read_buffer ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM buffer_events WHERE direction = 'read' AND consumed_by_agent = 0"
    )
    (read_buffer_pending,) = await cur.fetchone()

    # ── 未送达的 write_buffer ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM buffer_events WHERE direction = 'write' AND delivered_to_client = 0"
    )
    (write_buffer_pending,) = await cur.fetchone()

    # ── 最近 1 小时轮次数 ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM turns WHERE created_at > ?",
        (one_hour_ago,),
    )
    (recent_1h_turns,) = await cur.fetchone()

    # ── 最近 1 小时失败轮次 ──
    cur = await db.execute(
        "SELECT COUNT(*) FROM turns WHERE created_at > ? AND turn_status IN (?, ?)",
        (one_hour_ago, TurnStatus.ERROR.value, TurnStatus.ABORTED.value),
    )
    (failed_turns_1h,) = await cur.fetchone()

    # ── Agent 列表 ──
    agents_raw = await get_all_agents(db)
    agents = [
        AgentInfo(
            agent_id=a["agent_id"],
            bound_sessions=a["bound_sessions"],
            last_seen_at=a["last_seen_at"],
            enabled=a["enabled"],
        )
        for a in agents_raw
    ]

    # ── 活跃 / 不活跃 Session IDs ──
    cur = await db.execute(
        "SELECT session_id FROM sessions WHERE deleted = 0 AND last_client_turn_at > ?",
        (one_hour_ago,),
    )
    active_session_ids = [row[0] for row in await cur.fetchall()]

    cur = await db.execute(
        """
        SELECT session_id FROM sessions
        WHERE deleted = 0
          AND (last_client_turn_at IS NULL OR last_client_turn_at <= ?)
        """,
        (one_hour_ago,),
    )
    inactive_session_ids = [row[0] for row in await cur.fetchall()]

    return MonitorResponse(
        total_sessions=total_sessions,
        active_sessions=active_sessions,
        inactive_sessions=inactive_sessions,
        queued_new_sessions=queued_new_sessions,
        assigned_sessions=assigned_sessions,
        released_sessions=released_sessions,
        read_buffer_pending=read_buffer_pending,
        write_buffer_pending=write_buffer_pending,
        recent_1h_turns=recent_1h_turns,
        failed_turns_1h=failed_turns_1h,
        agents=agents,
        active_session_ids=active_session_ids,
        inactive_session_ids=inactive_session_ids,
    )
