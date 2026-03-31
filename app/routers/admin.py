"""
routers/admin.py — 管理员侧 API 路由

系统监控和管理操作：
- GET    /monitor                  → 系统状态（JSON / HTML）
- DELETE /sessions/{session_id}    → 管理员删除任意 Session
"""

from __future__ import annotations

import logging

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.auth import require_admin
from app.db import get_db
from app.schemas import MonitorResponse, OkResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# ────────────────────────────────────────────
# GET /monitor — 系统状态
# ────────────────────────────────────────────

@router.get("/monitor")
async def get_monitor(
    format: str = Query(default="json", pattern="^(json|html)$"),
    admin_id: str = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    返回系统监控信息。
    支持 ?format=json（默认）或 ?format=html。
    """
    from app.services.monitor_service import get_system_stats

    stats = await get_system_stats(db)

    if format == "html":
        # 返回 HTML 格式
        html_content = _generate_monitor_html(stats)
        return HTMLResponse(content=html_content)

    return stats


# ────────────────────────────────────────────
# DELETE /sessions/{session_id} — 管理员删除 Session
# ────────────────────────────────────────────

@router.delete("/sessions/{session_id}", response_model=OkResponse)
async def admin_delete_session(
    session_id: str,
    admin_id: str = Depends(require_admin),
    db: aiosqlite.Connection = Depends(get_db),
):
    """
    管理员可删除任意 Session。
    """
    from app.services.session_service import get_session, delete_session
    from app.services.audit_service import log_session_deleted

    session = await get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session 不存在")

    await delete_session(db=db, session_id=session_id)

    # 记录删除事件
    await log_session_deleted(session_id=session_id, deleted_by_admin=True)

    return OkResponse()


# ────────────────────────────────────────────
# HTML 生成辅助函数
# ────────────────────────────────────────────

def _generate_monitor_html(stats: MonitorResponse) -> str:
    """生成监控页面的 HTML 内容"""
    html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="30">
    <title>ZJ_AiDataProxy 监控面板</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f7fa;
            padding: 20px;
            color: #333;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 32px;
        }}
        .refresh-info {{
            color: #7f8c8d;
            font-size: 14px;
            margin-bottom: 30px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .stat-card {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .stat-card h3 {{
            font-size: 14px;
            color: #7f8c8d;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .stat-card .value {{
            font-size: 36px;
            font-weight: bold;
            color: #2c3e50;
        }}
        .stat-card .value.success {{
            color: #27ae60;
        }}
        .stat-card .value.warning {{
            color: #f39c12;
        }}
        .stat-card .value.danger {{
            color: #e74c3c;
        }}
        .section {{
            background: white;
            border-radius: 8px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .section h2 {{
            color: #2c3e50;
            margin-bottom: 20px;
            font-size: 24px;
            border-bottom: 2px solid #3498db;
            padding-bottom: 10px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th {{
            background: #ecf0f1;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            color: #2c3e50;
            border-bottom: 2px solid #bdc3c7;
        }}
        td {{
            padding: 12px;
            border-bottom: 1px solid #ecf0f1;
        }}
        tr:hover {{
            background: #f8f9fa;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge.success {{
            background: #d5f4e6;
            color: #27ae60;
        }}
        .badge.warning {{
            background: #fef5e7;
            color: #f39c12;
        }}
        .badge.info {{
            background: #ebf5fb;
            color: #3498db;
        }}
        .session-list {{
            max-height: 400px;
            overflow-y: auto;
        }}
        .session-id {{
            font-family: monospace;
            background: #ecf0f1;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 12px;
        }}
        .no-data {{
            text-align: center;
            color: #95a5a6;
            padding: 40px;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 ZJ_AiDataProxy 监控面板</h1>
        <div class="refresh-info">⏱️ 页面每 30 秒自动刷新</div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>总 Session 数</h3>
                <div class="value">{stats.total_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>活跃 Session</h3>
                <div class="value success">{stats.active_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>不活跃 Session</h3>
                <div class="value">{stats.inactive_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>队列中 Session</h3>
                <div class="value warning">{stats.queued_new_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>已分配 Session</h3>
                <div class="value info">{stats.assigned_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>已释放 Session</h3>
                <div class="value">{stats.released_sessions}</div>
            </div>
            <div class="stat-card">
                <h3>待消费 read_buffer</h3>
                <div class="value">{stats.read_buffer_pending}</div>
            </div>
            <div class="stat-card">
                <h3>待送达 write_buffer</h3>
                <div class="value">{stats.write_buffer_pending}</div>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>近 1 小时轮次数</h3>
                <div class="value">{stats.recent_1h_turns}</div>
            </div>
            <div class="stat-card">
                <h3>近 1 小时失败轮次</h3>
                <div class="value {'danger' if stats.failed_turns_1h > 0 else ''}">{stats.failed_turns_1h}</div>
            </div>
        </div>

        <div class="section">
            <h2>🤖 Agent 状态</h2>
            {'<div class="no-data">暂无 Agent</div>' if not stats.agents else f'''
            <table>
                <thead>
                    <tr>
                        <th>Agent ID</th>
                        <th>绑定 Session 数</th>
                        <th>最后心跳时间</th>
                        <th>状态</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join([f'''
                    <tr>
                        <td><span class="session-id">{agent.agent_id}</span></td>
                        <td>{agent.bound_sessions}</td>
                        <td>{agent.last_seen_at or "从未"}</td>
                        <td><span class="badge {'success' if agent.bound_sessions > 0 else 'info'}">{'工作中' if agent.bound_sessions > 0 else '空闲'}</span></td>
                    </tr>
                    ''' for agent in stats.agents])}
                </tbody>
            </table>
            '''}
        </div>

        <div class="section">
            <h2>✅ 活跃 Session (最近 1 小时有新轮次)</h2>
            {'<div class="no-data">暂无活跃 Session</div>' if not stats.active_session_ids else f'''
            <div class="session-list">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Session ID</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f'''
                        <tr>
                            <td>{idx + 1}</td>
                            <td><span class="session-id">{session_id}</span></td>
                        </tr>
                        ''' for idx, session_id in enumerate(stats.active_session_ids)])}
                    </tbody>
                </table>
            </div>
            '''}
        </div>

        <div class="section">
            <h2>💤 不活跃 Session (超过 1 小时无新轮次)</h2>
            {'<div class="no-data">暂无不活跃 Session</div>' if not stats.inactive_session_ids else f'''
            <div class="session-list">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Session ID</th>
                        </tr>
                    </thead>
                    <tbody>
                        {''.join([f'''
                        <tr>
                            <td>{idx + 1}</td>
                            <td><span class="session-id">{session_id}</span></td>
                        </tr>
                        ''' for idx, session_id in enumerate(stats.inactive_session_ids)])}
                    </tbody>
                </table>
            </div>
            '''}
        </div>
    </div>
</body>
</html>
    """
    return html
