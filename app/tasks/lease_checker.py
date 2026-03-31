"""
tasks/lease_checker.py — Agent 失联检测

周期性检测 Agent 是否失联（超过 2 分钟无活动），
自动释放其绑定的 Session。
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.models import SessionStatus, TurnStatus

logger = logging.getLogger(__name__)


async def lease_checker_loop() -> None:
    """
    后台循环任务：每 30 秒检测一次 Agent 租约。
    """
    logger.info("lease_checker 任务已启动")

    while True:
        try:
            await asyncio.sleep(settings.LEASE_CHECK_INTERVAL)
            await _do_lease_check()
        except asyncio.CancelledError:
            logger.info("lease_checker 任务已取消")
            break
        except Exception as e:
            logger.error(f"lease_checker 任务异常: {e}", exc_info=True)
            await asyncio.sleep(10)


async def _do_lease_check() -> None:
    """执行一次租约检查。"""
    from app.db import get_db
    from app.services.agent_service import find_expired_leases
    from app.services.audit_service import log_session_released

    try:
        db = await get_db()
    except RuntimeError:
        return

    expired_sessions = await find_expired_leases(db)

    if not expired_sessions:
        return

    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    released_count = 0
    for item in expired_sessions:
        session_id = item["session_id"]
        agent_id = item["assigned_agent_id"]

        # 释放 Session
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
            (SessionStatus.RELEASED.value, now_iso, session_id),
        )

        # 若有进行中的 turn，标记为 aborted
        await db.execute(
            """
            UPDATE turns
            SET turn_status = ?, completed_at = ?
            WHERE session_id = ?
              AND turn_status IN (?, ?)
            """,
            (
                TurnStatus.ABORTED.value,
                now_iso,
                session_id,
                TurnStatus.PENDING_AGENT.value,
                TurnStatus.STREAMING.value,
            ),
        )

        # 记录释放事件
        await log_session_released(
            session_id=session_id,
            agent_id=agent_id,
            reason="agent_lease_expired",
        )

        released_count += 1
        logger.warning(
            f"Agent 失联，Session 已释放: session={session_id}, agent={agent_id}"
        )

    await db.commit()

    if released_count > 0:
        logger.info(f"lease_checker 释放了 {released_count} 个 Session")
