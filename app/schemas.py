"""
schemas.py — 请求/响应 Schema

定义所有 API 的 Pydantic 输入/输出模型。
分为 Client 侧、Agent 侧、Admin 侧。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ════════════════════════════════════════════
# 通用
# ════════════════════════════════════════════

class OkResponse(BaseModel):
    """通用成功响应。"""
    ok: bool = True


class ErrorDetail(BaseModel):
    """错误详情。"""
    ok: bool = False
    error: str
    code: Optional[str] = None


# ════════════════════════════════════════════
# Client 侧 Schemas
# ════════════════════════════════════════════

class CreateSessionRequest(BaseModel):
    """创建新 Session 的请求体。"""
    stream_resume_mode: str = Field(
        default="client_reconnect_required",
        description="断线续传策略",
    )
    client_meta: Optional[Dict[str, Any]] = Field(
        default=None,
        description="客户端附加元数据",
    )


class CreateSessionResponse(BaseModel):
    """创建新 Session 的响应体。"""
    ok: bool = True
    session_id: str
    created_at: str
    status: str


class ContinueSessionRequest(BaseModel):
    """客户端发送消息继续 Session 的请求体。"""
    message: str = Field(..., description="用户消息内容")
    client_request_id: Optional[str] = Field(
        default=None,
        description="客户端去重 ID",
    )
    attachments: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="附件列表（图片等）",
    )


class NDJSONFrame(BaseModel):
    """
    NDJSON 流式帧格式。
    用于 client_continue_session 的流式响应。
    """
    type: str = Field(
        ...,
        description="帧类型: ack | chunk | end | error",
    )
    session_id: str
    turn_idx: int
    content: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None


class TurnRecord(BaseModel):
    """历史轮次记录。"""
    turn_idx: int
    user_message: Optional[str] = None
    assistant_message: Optional[str] = None
    turn_status: str
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class HistoryResponse(BaseModel):
    """获取历史记录的响应体。"""
    ok: bool = True
    session_id: str
    turns: List[TurnRecord]


class SessionInfoResponse(BaseModel):
    """Session 信息响应。"""
    ok: bool = True
    session_id: str
    status: str
    created_at: str
    updated_at: str
    next_turn_idx: int
    assigned_agent_id: Optional[str] = None


# ════════════════════════════════════════════
# Agent 侧 Schemas
# ════════════════════════════════════════════

class SessionSummary(BaseModel):
    """返回给 Agent 的 Session 摘要。"""
    session_id: str
    owner_user_id: str
    created_at: str
    status: str
    next_turn_idx: int
    metadata: Optional[str] = None
    stream_resume_mode: str = "client_reconnect_required"


class GetNewSessionResponse(BaseModel):
    """Agent 抢占新 Session 的响应。"""
    ok: bool = True
    session: Optional[SessionSummary] = Field(
        default=None,
        description="被分配的 Session，无可用 Session 时为 null",
    )


class ReadMsgRequest(BaseModel):
    """Agent 读取客户端消息的请求体。"""
    block_timeout_sec: int = Field(
        default=30,
        ge=1,
        le=300,
        description="阻塞等待超时秒数",
    )


class BufferMessage(BaseModel):
    """Buffer 中的一条消息。"""
    msg_type: str = Field(..., description="full | chunk | end | error")
    content: Optional[str] = None
    buffer_event_id: Optional[int] = None


class ReadMsgResponse(BaseModel):
    """Agent 读取客户端消息的响应体。"""
    ok: bool = True
    session_id: str
    turn_idx: int
    message: Optional[BufferMessage] = Field(
        default=None,
        description="读取到的消息，超时时为 null",
    )


class SendMsgRequest(BaseModel):
    """Agent 向客户端发送消息的请求体。"""
    turn_idx: int = Field(..., description="当前轮次索引")
    msg_type: str = Field(
        ...,
        description="消息类型: full | chunk | end | error",
    )
    content: Optional[str] = Field(
        default=None,
        description="消息内容",
    )


class SendMsgResponse(BaseModel):
    """Agent 发送消息的响应体。"""
    ok: bool = True
    buffer_event_id: int


class KeepAliveRequest(BaseModel):
    """Agent 心跳请求体。"""
    turn_idx: Optional[int] = Field(
        default=None,
        description="当前处理的轮次索引",
    )
    note: Optional[str] = Field(
        default=None,
        description="Agent 附加说明（调试用）",
    )


class KeepAliveResponse(BaseModel):
    """Agent 心跳响应体。"""
    ok: bool = True
    lease_expires_at: str


class ReleaseSessionRequest(BaseModel):
    """Agent 主动释放 Session 的请求体。"""
    reason: Optional[str] = None


# ════════════════════════════════════════════
# Admin 侧 Schemas
# ════════════════════════════════════════════

class AgentInfo(BaseModel):
    """Agent 信息。"""
    agent_id: str
    bound_sessions: int = 0
    last_seen_at: Optional[str] = None
    enabled: bool = True


class MonitorResponse(BaseModel):
    """系统监控信息响应体。"""
    ok: bool = True
    total_sessions: int = 0
    active_sessions: int = 0
    inactive_sessions: int = 0
    queued_new_sessions: int = 0
    assigned_sessions: int = 0
    released_sessions: int = 0
    read_buffer_pending: int = 0
    write_buffer_pending: int = 0
    recent_1h_turns: int = 0
    failed_turns_1h: int = 0
    agents: List[AgentInfo] = []
    active_session_ids: List[str] = []
    inactive_session_ids: List[str] = []


# ════════════════════════════════════════════
# 兼容旧前端协议的 Schemas（chat_stream 相关）
# ════════════════════════════════════════════

class ChatMessage(BaseModel):
    """聊天消息（兼容旧前端 chatHistory 中的单条消息）。"""
    type: Optional[str] = "text"
    msg: Optional[str] = None
    role: str
    attachment: Optional[List[Dict[str, Any]]] = None


class ChatStreamRequest(BaseModel):
    """
    POST /chat_stream 请求体（兼容旧前端）。
    stage 推荐传 JSON 字符串，后端兼容对象。
    """
    stage: Any = Field(
        default='{"step":"idle"}',
        description="当前阶段状态，JSON 字符串或对象",
    )
    chatHistory: List[ChatMessage] = Field(
        ...,
        min_length=1,
        description="完整历史消息数组",
    )
    dataDict: Dict[str, Any] = Field(
        default_factory=dict,
        description="前后端共享状态字典",
    )


class ImageUploadPayload(BaseModel):
    """图片上传请求体。"""
    tip: Optional[str] = "实验图片"
    imageBase64: str


class ConfirmComponentPayload(BaseModel):
    """组件确认请求体。"""
    symbol: str
    label: Optional[str] = None
    value: Optional[str] = None


class RoiDataRequest(BaseModel):
    """ROI 数据查询请求体。"""
    target: Optional[str] = None
    channel: Optional[str] = None
