"""
db.py — 数据库连接管理

使用 aiosqlite 创建异步数据库连接。
提供 get_db() 依赖注入函数。
应用启动时执行建表 SQL（6 张表 + 1 张可选审计表）。
提供事务上下文管理器。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import aiosqlite

from app.config import settings

logger = logging.getLogger(__name__)

# ── 全局数据库连接 ──
_db_connection: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()

# ────────────────────────────────────────────
# 建表 SQL
# ────────────────────────────────────────────

CREATE_TABLES_SQL = """
-- Session 主表
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    owner_user_id     TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    last_client_turn_at     TEXT,
    last_agent_activity_at  TEXT,
    status            TEXT NOT NULL DEFAULT 'new',
    assigned_agent_id TEXT,
    assigned_at       TEXT,
    agent_lease_expires_at  TEXT,
    requeue_on_new_turn     INTEGER NOT NULL DEFAULT 0,
    stream_resume_mode      TEXT NOT NULL DEFAULT 'client_reconnect_required',
    next_turn_idx     INTEGER NOT NULL DEFAULT 1,
    metadata          TEXT,
    deleted           INTEGER NOT NULL DEFAULT 0,
    expired           INTEGER NOT NULL DEFAULT 0
);

-- 轮次记录表
CREATE TABLE IF NOT EXISTS turns (
    session_id        TEXT NOT NULL,
    turn_idx          INTEGER NOT NULL,
    user_message      TEXT,
    assistant_message TEXT,
    turn_status       TEXT NOT NULL DEFAULT 'pending_agent',
    client_request_id TEXT,
    first_chunk_at    TEXT,
    completed_at      TEXT,
    created_at        TEXT,
    PRIMARY KEY (session_id, turn_idx),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- read/write buffer 事件日志
CREATE TABLE IF NOT EXISTS buffer_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    turn_idx          INTEGER NOT NULL,
    direction         TEXT NOT NULL,       -- 'read' | 'write'
    msg_type          TEXT NOT NULL,       -- 'full' | 'chunk' | 'end' | 'error'
    content           TEXT,
    consumed_by_agent INTEGER NOT NULL DEFAULT 0,
    consumed_at       TEXT,
    delivered_to_client INTEGER NOT NULL DEFAULT 0,
    delivered_at      TEXT,
    created_at        TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- 新 Session 待分配队列
CREATE TABLE IF NOT EXISTS new_session_queue (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    enqueued_at       TEXT,
    queue_reason      TEXT NOT NULL DEFAULT 'new_session',
    claimed_by_agent_id TEXT,
    claimed_at        TEXT,
    claim_deadline_at TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

-- Agent 注册表
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id          TEXT PRIMARY KEY,
    agent_token       TEXT NOT NULL,
    last_seen_at      TEXT,
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT
);

-- 审计日志（可选 SQLite 存储）
CREATE TABLE IF NOT EXISTS audit_logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type        TEXT NOT NULL,
    session_id        TEXT,
    agent_id          TEXT,
    user_id           TEXT,
    turn_idx          INTEGER,
    detail            TEXT,
    created_at        TEXT
);

-- 索引优化
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_assigned_agent ON sessions(assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS idx_buffer_events_session_turn ON buffer_events(session_id, turn_idx);
CREATE INDEX IF NOT EXISTS idx_buffer_events_direction ON buffer_events(direction, consumed_by_agent);
CREATE INDEX IF NOT EXISTS idx_queue_status ON new_session_queue(status);
CREATE INDEX IF NOT EXISTS idx_audit_logs_event ON audit_logs(event_type, created_at);
"""


# ────────────────────────────────────────────
# 连接管理
# ────────────────────────────────────────────

async def init_db() -> aiosqlite.Connection:
    """
    初始化数据库连接并执行建表。
    应在 FastAPI startup 事件中调用。
    """
    global _db_connection

    # 确保数据目录存在
    db_path = Path(settings.DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db_connection = await aiosqlite.connect(str(db_path))

    # 启用 WAL 模式以支持并发读
    await _db_connection.execute("PRAGMA journal_mode=WAL;")
    # 启用外键约束
    await _db_connection.execute("PRAGMA foreign_keys=ON;")

    # 执行建表
    await _db_connection.executescript(CREATE_TABLES_SQL)
    await _db_connection.commit()

    # 初始化预注册的 Agent
    await _seed_agents(_db_connection)

    logger.info(f"数据库已初始化: {db_path}")
    return _db_connection


async def close_db() -> None:
    """
    关闭数据库连接。
    应在 FastAPI shutdown 事件中调用。
    """
    global _db_connection
    if _db_connection:
        await _db_connection.close()
        _db_connection = None
        logger.info("数据库连接已关闭")


async def get_db() -> aiosqlite.Connection:
    """
    FastAPI 依赖注入函数：获取当前数据库连接。
    """
    if _db_connection is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    return _db_connection


# ────────────────────────────────────────────
# 事务上下文管理器
# ────────────────────────────────────────────

@asynccontextmanager
async def db_transaction(db: aiosqlite.Connection):
    """
    事务上下文管理器。用于需要原子操作的场景（如 Queue 抢占）。

    用法:
        async with db_transaction(db) as conn:
            await conn.execute(...)
            await conn.execute(...)
        # 退出时自动 commit，异常时自动 rollback
    """
    try:
        await db.execute("BEGIN IMMEDIATE")
        yield db
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ────────────────────────────────────────────
# 辅助：初始化预注册 Agent
# ────────────────────────────────────────────

async def _seed_agents(db: aiosqlite.Connection) -> None:
    """
    从 config.AGENT_TOKENS 中读取预注册 Agent，
    以 INSERT OR IGNORE 方式写入 agent_registry 表。
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    for entry in settings.AGENT_TOKENS:
        if ":" not in entry:
            logger.warning(f"无效的 Agent Token 格式 (应为 'agent_id:token'): {entry}")
            continue
        agent_id, agent_token = entry.split(":", 1)
        await db.execute(
            """
            INSERT OR IGNORE INTO agent_registry (agent_id, agent_token, enabled, created_at)
            VALUES (?, ?, 1, ?)
            """,
            (agent_id.strip(), agent_token.strip(), now),
        )
    await db.commit()
