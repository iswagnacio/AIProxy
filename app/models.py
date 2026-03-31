"""
models.py — 数据模型定义

定义所有数据库表对应的 Python 数据类。
使用 dataclass 风格，与 SQLite 表一一对应。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ────────────────────────────────────────────
# 枚举：Session 状态机
# ────────────────────────────────────────────

class SessionStatus(str, Enum):
    """Session 生命周期状态。"""
    NEW = "new"                  # 刚创建，等待 Agent 领取
    ASSIGNED = "assigned"        # 已被 Agent 绑定，但当前无进行中的轮次
    IDLE = "idle"                # 已分配，等待客户端下一轮
    WAITING = "waiting"          # 客户端已发起新轮次，但 Agent 尚未读取
    STREAMING = "streaming"      # 当前轮次正在流式输出
    RELEASED = "released"        # 原 Agent 已释放，等待重新入队
    EXPIRED = "expired"          # 超过保留期
    DELETED = "deleted"          # 被用户/管理员删除


class TurnStatus(str, Enum):
    """轮次状态。"""
    PENDING_AGENT = "pending_agent"  # 等待 Agent 读取
    STREAMING = "streaming"          # Agent 正在流式输出
    COMPLETED = "completed"          # 正常完成
    ERROR = "error"                  # 出错
    ABORTED = "aborted"              # 因 Agent 失联等原因中止


class BufferDirection(str, Enum):
    """Buffer 方向。"""
    READ = "read"    # 客户端 → Agent
    WRITE = "write"  # Agent → 客户端


class BufferMsgType(str, Enum):
    """Buffer 消息类型。"""
    FULL = "full"      # 单包完整消息
    CHUNK = "chunk"    # 流式 chunk
    END = "end"        # 流式结束标记
    ERROR = "error"    # 错误


class QueueStatus(str, Enum):
    """队列条目状态。"""
    PENDING = "pending"      # 等待 Agent 领取
    CLAIMED = "claimed"      # 已被 Agent 领取（待确认）
    CONFIRMED = "confirmed"  # Agent 已确认（接单完成）
    EXPIRED = "expired"      # 领取超时，回退


class QueueReason(str, Enum):
    """入队原因。"""
    NEW_SESSION = "new_session"
    REQUEUE_AFTER_RELEASE = "requeue_after_release"


# ────────────────────────────────────────────
# 数据模型
# ────────────────────────────────────────────

@dataclass
class Session:
    """Session 主表模型。"""
    session_id: str
    owner_user_id: str
    created_at: str                           # ISO 格式时间戳
    updated_at: str
    last_client_turn_at: Optional[str] = None
    last_agent_activity_at: Optional[str] = None
    status: str = SessionStatus.NEW.value
    assigned_agent_id: Optional[str] = None
    assigned_at: Optional[str] = None
    agent_lease_expires_at: Optional[str] = None
    requeue_on_new_turn: bool = False
    stream_resume_mode: str = "client_reconnect_required"
    next_turn_idx: int = 1
    metadata: Optional[str] = None            # JSON 字符串
    deleted: bool = False
    expired: bool = False


@dataclass
class Turn:
    """轮次记录模型。"""
    session_id: str
    turn_idx: int
    user_message: Optional[str] = None        # JSON 字符串（完整用户消息）
    assistant_message: Optional[str] = None    # JSON 字符串（完整助手回复）
    turn_status: str = TurnStatus.PENDING_AGENT.value
    client_request_id: Optional[str] = None
    first_chunk_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class BufferEvent:
    """read/write buffer 事件日志模型。"""
    id: Optional[int] = None                  # 自增主键
    session_id: str = ""
    turn_idx: int = 0
    direction: str = BufferDirection.READ.value
    msg_type: str = BufferMsgType.FULL.value
    content: Optional[str] = None             # 消息内容
    consumed_by_agent: bool = False
    consumed_at: Optional[str] = None
    delivered_to_client: bool = False
    delivered_at: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class NewSessionQueueItem:
    """新 Session 待分配队列模型。"""
    id: Optional[int] = None                  # 自增主键
    session_id: str = ""
    enqueued_at: Optional[str] = None
    queue_reason: str = QueueReason.NEW_SESSION.value
    claimed_by_agent_id: Optional[str] = None
    claimed_at: Optional[str] = None
    claim_deadline_at: Optional[str] = None
    status: str = QueueStatus.PENDING.value


@dataclass
class AgentRegistry:
    """Agent 注册表模型。"""
    agent_id: str = ""
    agent_token: str = ""
    last_seen_at: Optional[str] = None
    enabled: bool = True
    created_at: Optional[str] = None


@dataclass
class AuditLog:
    """审计日志模型（可选 SQLite 存储）。"""
    id: Optional[int] = None
    event_type: str = ""
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    turn_idx: Optional[int] = None
    detail: Optional[str] = None              # JSON 字符串
    created_at: Optional[str] = None
