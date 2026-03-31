"""
auth.py — 鉴权模块

处理浏览器（JWT）和 Agent（X-Agent-Id + X-Agent-Token）的身份验证。
提供 FastAPI 依赖注入函数。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request, status

import aiosqlite

from app.config import settings
from app.db import get_db

logger = logging.getLogger(__name__)

# 管理员用户 ID（简易实现，生产环境建议扩展）
ADMIN_USER_IDS = {"admin", "admin_test"}


# ────────────────────────────────────────────
# JWT 工具函数
# ────────────────────────────────────────────

def create_jwt_token(
    user_id: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    创建 JWT Token。

    Args:
        user_id: 用户标识
        expires_delta: 过期时间间隔，默认使用配置值

    Returns:
        编码后的 JWT 字符串
    """
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.JWT_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_jwt_token(token: str) -> dict:
    """
    解码并验证 JWT Token。

    Args:
        token: JWT 字符串

    Returns:
        解码后的 payload dict

    Raises:
        HTTPException: Token 无效或过期
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 已过期",
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token 无效: {e}",
        )


# ────────────────────────────────────────────
# FastAPI 依赖注入：浏览器鉴权
# ────────────────────────────────────────────

async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> str:
    """
    从 Authorization Header 中提取并验证 JWT，返回 user_id。

    支持格式:
        Authorization: Bearer <token>
        Authorization: <token>

    在开发模式（DEBUG=True）下，如无 Authorization Header，
    则回退使用查询参数 ?user_id=xxx 或默认 'anonymous'。
    """
    token = None

    if authorization:
        # 支持 "Bearer xxx" 和直接 "xxx"
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]

    if token:
        payload = decode_jwt_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token 中缺少 sub 字段",
            )
        return user_id

    # 开发模式回退
    if settings.DEBUG:
        user_id = request.query_params.get("user_id", "anonymous")
        logger.debug(f"[DEBUG 模式] 使用回退 user_id: {user_id}")
        return user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="缺少 Authorization Header",
    )


# ────────────────────────────────────────────
# FastAPI 依赖注入：Agent 鉴权
# ────────────────────────────────────────────

async def get_current_agent(
    x_agent_id: str = Header(..., alias="X-Agent-Id"),
    x_agent_token: str = Header(..., alias="X-Agent-Token"),
    db: aiosqlite.Connection = Depends(get_db),
) -> str:
    """
    从请求 Header 中提取 X-Agent-Id 和 X-Agent-Token，
    在 agent_registry 表中验证。

    Returns:
        验证通过的 agent_id

    Raises:
        HTTPException: Agent 身份验证失败
    """
    cursor = await db.execute(
        """
        SELECT agent_id, agent_token, enabled
        FROM agent_registry
        WHERE agent_id = ?
        """,
        (x_agent_id,),
    )
    row = await cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Agent '{x_agent_id}' 未注册",
        )

    db_agent_id, db_agent_token, db_enabled = row

    if not db_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent '{x_agent_id}' 已被禁用",
        )

    if db_agent_token != x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent Token 验证失败",
        )

    # 更新 last_seen_at
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE agent_registry SET last_seen_at = ? WHERE agent_id = ?",
        (now, x_agent_id),
    )
    await db.commit()

    return x_agent_id


# ────────────────────────────────────────────
# FastAPI 依赖注入：管理员鉴权
# ────────────────────────────────────────────

async def require_admin(
    user_id: str = Depends(get_current_user),
) -> str:
    """
    验证当前用户是否为管理员。

    Returns:
        管理员 user_id

    Raises:
        HTTPException: 非管理员
    """
    if user_id not in ADMIN_USER_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user_id
