"""
services/buffer_service.py — 消息 Buffer 管理

核心职责：
- read_buffer（客户端 → Agent）的写入与消费
- write_buffer（Agent → 客户端）的写入与消费
- 所有读写均保留原始日志记录
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.models import BufferDirection, BufferMsgType, SessionStatus, TurnStatus
from app.schemas import BufferMessage
from app.services import audit_service

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ────────────────────────────────────────────
# read_buffer（客户端 → Agent）
# ────────────────────────────────────────────

async def write_read_buffer(
    db: aiosqlite.Connection,
    session_id: str,
    turn_idx: int,
    content: str,
) -> int:
    """
    客户端 continue_session 时，将用户消息写入 buffer_events。
    direction = "read", msg_type = "full"

    Returns:
        buffer_event_id
    """
    now = _now_iso()
    cursor = await db.execute(
        """
        INSERT INTO buffer_events
            (session_id, turn_idx, direction, msg_type, content,
             consumed_by_agent, delivered_to_client, created_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (
            session_id,
            turn_idx,
            BufferDirection.READ.value,
            BufferMsgType.FULL.value,
            content,
            now,
        ),
    )
    await db.commit()
    event_id = cursor.lastrowid
    logger.debug(f"read_buffer 写入: session={session_id}, turn={turn_idx}, id={event_id}")
    return event_id


async def consume_read_buffer(
    db: aiosqlite.Connection,
    session_id: str,
    block_timeout_sec: int = 30,
) -> Optional[BufferMessage]:
    """
    Agent 消费 read_buffer 中未消费的消息。
    阻塞等待直到有消息或超时。

    读取后只更新 consumed 游标，不删除原始日志。

    Returns:
        BufferMessage 或 None（超时）
    """
    deadline = asyncio.get_event_loop().time() + block_timeout_sec
    poll_interval = 0.5  # 轮询间隔

    while asyncio.get_event_loop().time() < deadline:
        cursor = await db.execute(
            """
            SELECT id, turn_idx, msg_type, content
            FROM buffer_events
            WHERE session_id = ?
              AND direction = ?
              AND consumed_by_agent = 0
            ORDER BY id ASC
            LIMIT 1
            """,
            (session_id, BufferDirection.READ.value),
        )
        row = await cursor.fetchone()

        if row is not None:
            event_id, turn_idx, msg_type, content = row
            now = _now_iso()
            # 标记已消费
            await db.execute(
                """
                UPDATE buffer_events
                SET consumed_by_agent = 1, consumed_at = ?
                WHERE id = ?
                """,
                (now, event_id),
            )
            await db.commit()

            logger.debug(f"read_buffer 已消费: session={session_id}, event_id={event_id}")
            return BufferMessage(
                msg_type=msg_type,
                content=content,
                buffer_event_id=event_id,
            )

        await asyncio.sleep(poll_interval)

    logger.debug(f"read_buffer 等待超时: session={session_id}")
    return None


# ────────────────────────────────────────────
# write_buffer（Agent → 客户端）
# ────────────────────────────────────────────

async def write_write_buffer(
    db: aiosqlite.Connection,
    session_id: str,
    turn_idx: int,
    msg_type: str,
    content: Optional[str] = None,
) -> int:
    """
    Agent 写入 write_buffer（chunk / full / end / error）。

    同时更新 turn 和 session 的时间戳与状态。

    Returns:
        buffer_event_id
    """
    now = _now_iso()

    # 1. 写入 buffer_events
    cursor = await db.execute(
        """
        INSERT INTO buffer_events
            (session_id, turn_idx, direction, msg_type, content,
             consumed_by_agent, delivered_to_client, created_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?)
        """,
        (
            session_id,
            turn_idx,
            BufferDirection.WRITE.value,
            msg_type,
            content,
            now,
        ),
    )
    event_id = cursor.lastrowid

    # 2. 更新 session 活跃时间
    await db.execute(
        "UPDATE sessions SET last_agent_activity_at = ?, updated_at = ? WHERE session_id = ?",
        (now, now, session_id),
    )

    # 3. 根据 msg_type 更新 turn / session 状态，并累积 assistant 消息
    if msg_type == BufferMsgType.CHUNK.value:
        # 首个 chunk 时更新 turn 状态和 first_chunk_at
        # 同时追加内容到 assistant_message
        if content:
            await db.execute(
                """
                UPDATE turns
                SET turn_status = ?,
                    first_chunk_at = COALESCE(first_chunk_at, ?),
                    assistant_message = COALESCE(assistant_message, '') || ?
                WHERE session_id = ? AND turn_idx = ?
                """,
                (TurnStatus.STREAMING.value, now, content, session_id, turn_idx),
            )
        else:
            await db.execute(
                """
                UPDATE turns
                SET turn_status = ?,
                    first_chunk_at = COALESCE(first_chunk_at, ?)
                WHERE session_id = ? AND turn_idx = ?
                """,
                (TurnStatus.STREAMING.value, now, session_id, turn_idx),
            )
        await db.execute(
            "UPDATE sessions SET status = ? WHERE session_id = ? AND status != ?",
            (SessionStatus.STREAMING.value, session_id, SessionStatus.STREAMING.value),
        )

    elif msg_type in (BufferMsgType.FULL.value, BufferMsgType.END.value):
        # 轮次完成
        # 如果是 FULL 类型，还需要将完整内容写入 assistant_message
        if msg_type == BufferMsgType.FULL.value and content:
            await db.execute(
                """
                UPDATE turns
                SET turn_status = ?,
                    completed_at = ?,
                    first_chunk_at = COALESCE(first_chunk_at, ?),
                    assistant_message = ?
                WHERE session_id = ? AND turn_idx = ?
                """,
                (TurnStatus.COMPLETED.value, now, now, content, session_id, turn_idx),
            )
        else:
            await db.execute(
                """
                UPDATE turns
                SET turn_status = ?,
                    completed_at = ?,
                    first_chunk_at = COALESCE(first_chunk_at, ?)
                WHERE session_id = ? AND turn_idx = ?
                """,
                (TurnStatus.COMPLETED.value, now, now, session_id, turn_idx),
            )
        await db.execute(
            "UPDATE sessions SET status = ? WHERE session_id = ?",
            (SessionStatus.IDLE.value, session_id),
        )

    elif msg_type == BufferMsgType.ERROR.value:
        # 轮次出错
        await db.execute(
            "UPDATE turns SET turn_status = ?, completed_at = ? WHERE session_id = ? AND turn_idx = ?",
            (TurnStatus.ERROR.value, now, session_id, turn_idx),
        )
        await db.execute(
            "UPDATE sessions SET status = ? WHERE session_id = ?",
            (SessionStatus.IDLE.value, session_id),
        )

    await db.commit()
    logger.debug(
        f"write_buffer 写入: session={session_id}, turn={turn_idx}, "
        f"type={msg_type}, id={event_id}"
    )
    return event_id


async def consume_write_buffer(
    db: aiosqlite.Connection,
    session_id: str,
    turn_idx: int,
    timeout_sec: float = 1.0,
) -> Optional[BufferMessage]:
    """
    Stream Bridge 从 write_buffer 中取出当前 turn 的输出数据。
    标记 delivered_to_client。

    Returns:
        BufferMessage 或 None（无数据）
    """
    cursor = await db.execute(
        """
        SELECT id, msg_type, content
        FROM buffer_events
        WHERE session_id = ?
          AND turn_idx = ?
          AND direction = ?
          AND delivered_to_client = 0
        ORDER BY id ASC
        LIMIT 1
        """,
        (session_id, turn_idx, BufferDirection.WRITE.value),
    )
    row = await cursor.fetchone()

    if row is None:
        return None

    event_id, msg_type, content = row
    now = _now_iso()

    # 标记已送达
    await db.execute(
        "UPDATE buffer_events SET delivered_to_client = 1, delivered_at = ? WHERE id = ?",
        (now, event_id),
    )
    await db.commit()

    return BufferMessage(
        msg_type=msg_type,
        content=content,
        buffer_event_id=event_id,
    )
