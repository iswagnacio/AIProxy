"""
main.py — 应用入口

FastAPI 应用初始化与启动：
- 注册三组 Router：client、agent、admin
- startup / shutdown 事件
- 后台任务启动
- CORS 中间件
- Uvicorn 启动入口
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import init_db, close_db

# ── 日志配置 ──
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── 后台任务句柄 ──
_background_tasks: list[asyncio.Task] = []


# ────────────────────────────────────────────
# Lifespan：启动 / 关闭
# ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan 管理：startup 和 shutdown 逻辑。"""
    # ── Startup ──
    logger.info(f"正在启动 {settings.APP_NAME} v{settings.APP_VERSION} ...")

    # 1) 初始化数据库
    await init_db()

    # 2) 确保日志与文件存储目录存在
    Path(settings.LOG_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.FILE_STORAGE_DIR).mkdir(parents=True, exist_ok=True)

    # 3) 启动后台任务
    from app.tasks.cleanup import cleanup_expired_sessions_loop
    from app.tasks.lease_checker import lease_checker_loop

    task_cleanup = asyncio.create_task(
        cleanup_expired_sessions_loop(), name="cleanup"
    )
    task_lease = asyncio.create_task(
        lease_checker_loop(), name="lease_checker"
    )
    _background_tasks.extend([task_cleanup, task_lease])

    logger.info("后台任务已启动: cleanup, lease_checker")
    logger.info(f"{settings.APP_NAME} 启动完毕 ✓")

    yield  # ── 应用运行期间 ──

    # ── Shutdown ──
    logger.info("正在关闭 ...")

    # 取消后台任务
    for task in _background_tasks:
        task.cancel()
    await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    # 关闭数据库
    await close_db()

    logger.info(f"{settings.APP_NAME} 已关闭 ✓")


# ────────────────────────────────────────────
# 创建 FastAPI 应用
# ────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# ── CORS 中间件 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────────────────────────
# 注册路由
# ────────────────────────────────────────────

from app.routers import client, agent, admin  # noqa: E402

app.include_router(client.router, prefix="/api/client", tags=["Client"])
app.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])


# ── 健康检查 ──
@app.get("/")
async def health_check():
    return {
        "ok": True,
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


# ── 静态文件服务（图片、ROI zip 等） ──
# 将 FILE_STORAGE_DIR 挂载到 /files/ 路径
file_storage_path = Path(settings.FILE_STORAGE_DIR)
if file_storage_path.exists():
    app.mount(
        "/files",
        StaticFiles(directory=str(file_storage_path)),
        name="files",
    )


# ────────────────────────────────────────────
# Uvicorn 入口
# ────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
    )
