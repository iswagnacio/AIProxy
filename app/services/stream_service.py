"""
services/stream_service.py — 流式桥接 (Stream Bridge)

核心职责：
将 Agent 写入 write_buffer 的数据，以 NDJSON 流式格式推送给浏览器。

每个 client_continue_session 请求创建一个流式桥接任务（协程），
按 session_id + turn_idx 维度独立运行。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

import aiosqlite

from app.config import settings
from app.models import BufferMsgType
from app.schemas import NDJSONFrame
from app.services.buffer_service import consume_write_buffer
from app.services import audit_service

logger = logging.getLogger(__name__)


def _ndjson_line(frame: NDJSONFrame) -> str:
    """将 NDJSONFrame 序列化为一行 NDJSON（末尾 \\n）。"""
    return json.dumps(frame.model_dump(exclude_none=True), ensure_ascii=False) + "\n"


async def stream_bridge(
    db: aiosqlite.Connection,
    session_id: str,
    turn_idx: int,
) -> AsyncGenerator[str, None]:
    """
    NDJSON 流式桥接生成器。

    流程:
    1. 先返回 ack 帧
    2. 持续轮询 write_buffer 中属于当前 turn 的输出
    3. chunk → 持续输出; full → 直接结束; end → 结束; error → 返回错误帧结束
    4. 超时处理：首字节 60s、chunk 间隔 90s

    Yields:
        NDJSON 行字符串
    """
    poll_interval = 0.3  # 轮询间隔

    # ── 1. 发送 ack 帧 ──
    ack = NDJSONFrame(
        type="ack",
        session_id=session_id,
        turn_idx=turn_idx,
        status="accepted",
    )
    yield _ndjson_line(ack)

    # ── 2. 等待首字节 ──
    first_byte_received = False
    first_byte_deadline = asyncio.get_event_loop().time() + settings.STREAM_FIRST_BYTE_TIMEOUT

    while not first_byte_received:
        if asyncio.get_event_loop().time() > first_byte_deadline:
            # 首字节超时
            timeout_frame = NDJSONFrame(
                type="error",
                session_id=session_id,
                turn_idx=turn_idx,
                message="首字节等待超时",
            )
            yield _ndjson_line(timeout_frame)
            logger.warning(f"Stream 首字节超时: session={session_id}, turn={turn_idx}")
            return

        msg = await consume_write_buffer(db, session_id, turn_idx)
        if msg is not None:
            first_byte_received = True
            # 记录送达事件
            await audit_service.log_write_buffer_delivered(
                session_id=session_id,
                turn_idx=turn_idx,
                buffer_event_id=msg.buffer_event_id,
            )
            # 处理首条消息
            done = await _handle_msg_and_yield(msg, session_id, turn_idx)
            frame = _msg_to_frame(msg, session_id, turn_idx)
            yield _ndjson_line(frame)
            if done:
                return
        else:
            await asyncio.sleep(poll_interval)

    # ── 3. 持续接收后续 chunk ──
    chunk_deadline = asyncio.get_event_loop().time() + settings.STREAM_CHUNK_INTERVAL_TIMEOUT

    while True:
        if asyncio.get_event_loop().time() > chunk_deadline:
            # chunk 间隔超时
            timeout_frame = NDJSONFrame(
                type="error",
                session_id=session_id,
                turn_idx=turn_idx,
                message="chunk 间隔超时",
            )
            yield _ndjson_line(timeout_frame)
            logger.warning(f"Stream chunk 间隔超时: session={session_id}, turn={turn_idx}")
            return

        msg = await consume_write_buffer(db, session_id, turn_idx)
        if msg is not None:
            # 重置 chunk 超时
            chunk_deadline = asyncio.get_event_loop().time() + settings.STREAM_CHUNK_INTERVAL_TIMEOUT

            # 记录送达事件
            await audit_service.log_write_buffer_delivered(
                session_id=session_id,
                turn_idx=turn_idx,
                buffer_event_id=msg.buffer_event_id,
            )

            frame = _msg_to_frame(msg, session_id, turn_idx)
            yield _ndjson_line(frame)

            done = _is_terminal_msg(msg)
            if done:
                return
        else:
            await asyncio.sleep(poll_interval)


def _msg_to_frame(msg, session_id: str, turn_idx: int) -> NDJSONFrame:
    """将 BufferMessage 转换为 NDJSONFrame。"""
    if msg.msg_type == BufferMsgType.CHUNK.value:
        return NDJSONFrame(
            type="chunk",
            session_id=session_id,
            turn_idx=turn_idx,
            content=msg.content,
        )
    elif msg.msg_type == BufferMsgType.FULL.value:
        return NDJSONFrame(
            type="chunk",
            session_id=session_id,
            turn_idx=turn_idx,
            content=msg.content,
        )
    elif msg.msg_type == BufferMsgType.END.value:
        return NDJSONFrame(
            type="end",
            session_id=session_id,
            turn_idx=turn_idx,
        )
    elif msg.msg_type == BufferMsgType.ERROR.value:
        return NDJSONFrame(
            type="error",
            session_id=session_id,
            turn_idx=turn_idx,
            message=msg.content,
        )
    else:
        return NDJSONFrame(
            type="chunk",
            session_id=session_id,
            turn_idx=turn_idx,
            content=msg.content,
        )


def _is_terminal_msg(msg) -> bool:
    """判断是否为终止消息。"""
    return msg.msg_type in (
        BufferMsgType.FULL.value,
        BufferMsgType.END.value,
        BufferMsgType.ERROR.value,
    )


async def _handle_msg_and_yield(msg, session_id: str, turn_idx: int) -> bool:
    """处理消息并返回是否应终止流。"""
    return _is_terminal_msg(msg)
