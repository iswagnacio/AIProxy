"""
tasks/cleanup.py — Session 过期清理

定时任务：查找超过保留期的 Session，标记为 expired。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import SessionStatus

logger = logging.getLogger(__name__)


async def cleanup_expired_sessions_loop() -> None:
    """
    后台循环任务：定期清理过期 Session。
    默认每小时执行一次。
    """
    logger.info("cleanup 任务已启动")

    while True:
        try:
            await asyncio.sleep(settings.CLEANUP_INTERVAL)
            await _do_cleanup()
        except asyncio.CancelledError:
            logger.info("cleanup 任务已取消")
            break
        except Exception as e:
            logger.error(f"cleanup 任务异常: {e}", exc_info=True)
            await asyncio.sleep(60)  # 异常后等 1 分钟重试


async def _do_cleanup() -> None:
    """执行一次过期 Session 清理。"""
    from app.db import get_db
    from app.services.audit_service import log_session_expired

    try:
        db = await get_db()
    except RuntimeError:
        return

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=settings.SESSION_RETENTION_DAYS)).isoformat()
    now_iso = now.isoformat()

    # 查找超过保留期的 Session
    cursor = await db.execute(
        """
        SELECT session_id FROM sessions
        WHERE deleted = 0
          AND expired = 0
          AND (
            (last_agent_activity_at IS NOT NULL AND last_agent_activity_at < ?)
            OR (last_client_turn_at IS NOT NULL AND last_client_turn_at < ?)
            OR (last_agent_activity_at IS NULL AND last_client_turn_at IS NULL AND created_at < ?)
          )
        """,
        (cutoff, cutoff, cutoff),
    )
    rows = await cursor.fetchall()

    if not rows:
        return

    expired_count = 0
    for (session_id,) in rows:
        await db.execute(
            """
            UPDATE sessions
            SET expired = 1, status = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (SessionStatus.EXPIRED.value, now_iso, session_id),
        )
        # 记录过期事件
        await log_session_expired(session_id=session_id)
        expired_count += 1

    await db.commit()

    if expired_count > 0:
        logger.info(f"已清理 {expired_count} 个过期 Session")
