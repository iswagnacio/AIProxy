"""
services/audit_service.py — JSONL 审计日志服务

核心职责：
- 将所有关键事件追加写入 JSONL 格式的日志文件
- 按日期分割日志文件（logs/events-YYYYMMDD.jsonl）
- 支持多种事件类型的结构化记录
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# 确保日志目录存在
LOG_DIR = Path(settings.LOG_DIR)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    """返回 UTC ISO 格式时间戳"""
    return datetime.now(timezone.utc).isoformat()


def _get_log_file_path() -> Path:
    """获取当前日期的 JSONL 日志文件路径"""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return LOG_DIR / f"events-{today}.jsonl"


async def write_audit_log(
    event_type: str,
    session_id: Optional[str] = None,
    turn_idx: Optional[int] = None,
    agent_id: Optional[str] = None,
    user_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    异步写入审计日志到 JSONL 文件。

    Args:
        event_type: 事件类型（如 session_created, turn_completed 等）
        session_id: 相关 Session ID
        turn_idx: 相关轮次索引
        agent_id: 相关 Agent ID
        user_id: 相关用户 ID
        payload: 额外的事件数据
    """
    try:
        log_entry = {
            "timestamp": _now_iso(),
            "event_type": event_type,
        }

        # 添加可选字段
        if session_id:
            log_entry["session_id"] = session_id
        if turn_idx is not None:
            log_entry["turn_idx"] = turn_idx
        if agent_id:
            log_entry["agent_id"] = agent_id
        if user_id:
            log_entry["user_id"] = user_id
        if payload:
            log_entry["payload"] = payload

        # 异步写入文件（使用 asyncio.to_thread 避免阻塞）
        def _write_sync():
            log_file = _get_log_file_path()
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_write_sync)

        logger.debug(f"审计日志已写入: {event_type} - session={session_id}")

    except Exception as e:
        # 审计日志失败不应影响主流程
        logger.error(f"写入审计日志失败: {event_type} - {e}", exc_info=True)


# ────────────────────────────────────────────
# 便捷的事件日志记录函数
# ────────────────────────────────────────────

async def log_session_created(session_id: str, user_id: str, payload: Optional[Dict] = None):
    """记录 Session 创建事件"""
    await write_audit_log("session_created", session_id=session_id, user_id=user_id, payload=payload)


async def log_session_enqueued(session_id: str, queue_reason: str):
    """记录 Session 入队事件"""
    await write_audit_log("session_enqueued", session_id=session_id, payload={"queue_reason": queue_reason})


async def log_session_assigned(session_id: str, agent_id: str):
    """记录 Session 分配给 Agent 事件"""
    await write_audit_log("session_assigned", session_id=session_id, agent_id=agent_id)


async def log_client_turn_created(session_id: str, turn_idx: int, user_id: str):
    """记录客户端发起新轮次事件"""
    await write_audit_log("client_turn_created", session_id=session_id, turn_idx=turn_idx, user_id=user_id)


async def log_read_buffer_written(session_id: str, turn_idx: int, buffer_event_id: int):
    """记录用户消息写入 read_buffer 事件"""
    await write_audit_log(
        "read_buffer_written",
        session_id=session_id,
        turn_idx=turn_idx,
        payload={"buffer_event_id": buffer_event_id},
    )


async def log_read_buffer_consumed(session_id: str, turn_idx: int, agent_id: str, buffer_event_id: int):
    """记录 Agent 消费用户消息事件"""
    await write_audit_log(
        "read_buffer_consumed",
        session_id=session_id,
        turn_idx=turn_idx,
        agent_id=agent_id,
        payload={"buffer_event_id": buffer_event_id},
    )


async def log_write_buffer_written(session_id: str, turn_idx: int, agent_id: str, msg_type: str, buffer_event_id: int):
    """记录 Agent 写入 write_buffer 事件"""
    await write_audit_log(
        "write_buffer_written",
        session_id=session_id,
        turn_idx=turn_idx,
        agent_id=agent_id,
        payload={"msg_type": msg_type, "buffer_event_id": buffer_event_id},
    )


async def log_write_buffer_delivered(session_id: str, turn_idx: int, buffer_event_id: int):
    """记录回复送达浏览器事件"""
    await write_audit_log(
        "write_buffer_delivered",
        session_id=session_id,
        turn_idx=turn_idx,
        payload={"buffer_event_id": buffer_event_id},
    )


async def log_turn_completed(session_id: str, turn_idx: int):
    """记录轮次完成事件"""
    await write_audit_log("turn_completed", session_id=session_id, turn_idx=turn_idx)


async def log_turn_error(session_id: str, turn_idx: int, error_message: str):
    """记录轮次错误事件"""
    await write_audit_log(
        "turn_error",
        session_id=session_id,
        turn_idx=turn_idx,
        payload={"error_message": error_message},
    )


async def log_session_released(session_id: str, agent_id: str, reason: str):
    """记录 Session 释放事件"""
    await write_audit_log(
        "session_released",
        session_id=session_id,
        agent_id=agent_id,
        payload={"reason": reason},
    )


async def log_session_deleted(session_id: str, user_id: Optional[str] = None, deleted_by_admin: bool = False):
    """记录 Session 删除事件"""
    await write_audit_log(
        "session_deleted",
        session_id=session_id,
        user_id=user_id,
        payload={"deleted_by_admin": deleted_by_admin},
    )


async def log_session_expired(session_id: str):
    """记录 Session 过期事件"""
    await write_audit_log("session_expired", session_id=session_id)
