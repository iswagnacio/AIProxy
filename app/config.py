"""
config.py — 全局配置

集中管理所有可配置参数，使用 pydantic-settings 从环境变量 / .env 文件加载。
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from typing import List, Optional


# 项目根目录（ZJ_AiDataProxy/）
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用全局配置，可通过环境变量或 .env 文件覆盖。"""

    # ── 应用基础 ──
    APP_NAME: str = "ZJ_AiDataProxy"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # ── 数据库 ──
    DB_PATH: str = str(BASE_DIR / "data" / "proxy.db")

    # ── JSONL 审计日志目录 ──
    LOG_DIR: str = str(BASE_DIR / "logs")

    # ── JWT 鉴权 ──
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60 * 24  # 默认 24 小时

    # ── Agent 鉴权 ──
    # 预注册的 Agent Token 列表，格式为 "agent_id:token"
    # 实际部署时可通过环境变量以逗号分隔传入
    AGENT_TOKENS: List[str] = [
        "agent_default:default_secret_token",
    ]

    # ── CORS ──
    CORS_ORIGINS: List[str] = ["*"]

    # ── 超时参数（秒） ──

    # Agent 从队列抢占 Session 后，需在此时间内 keep_alive 或 read_msg 确认接单
    AGENT_CLAIM_CONFIRM_TIMEOUT: int = 30

    # client_continue_session 等待 Agent 写入第一个 chunk 的最大时间
    STREAM_FIRST_BYTE_TIMEOUT: int = 60

    # 流式推送中，两个 chunk 之间的最大间隔
    STREAM_CHUNK_INTERVAL_TIMEOUT: int = 90

    # Agent 失联检测：超过此时间无 keep_alive / read / write 活动则视为失联
    AGENT_LEASE_TIMEOUT: int = 120  # 2 分钟

    # Agent keep_alive 建议调用间隔
    AGENT_KEEPALIVE_INTERVAL: int = 20

    # Session 无新轮次后 Agent 可释放绑定的等待时间
    SESSION_IDLE_RELEASE_TIMEOUT: int = 3600  # 1 小时

    # Session 历史保留期（按最后活跃时间），超期后标记 expired
    SESSION_RETENTION_DAYS: int = 10

    # ── 后台任务间隔 ──
    LEASE_CHECK_INTERVAL: int = 30       # lease_checker 每 30 秒执行一次
    CLEANUP_INTERVAL: int = 3600         # cleanup 每 1 小时执行一次

    # ── CV 后端 API 基地址 ──
    CV_BACKEND_BASE_URL: str = "http://localhost:9000"

    # ── 文件存储根目录 ──
    FILE_STORAGE_DIR: str = str(BASE_DIR / "data" / "files")

    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


# 全局单例
settings = Settings()
