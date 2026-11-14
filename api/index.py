"""
Vercel Serverless Function 入口
为 LangGraph PostgresStore 提供安全加固、具备审计日志与公网域名放行的 FastMCP SSE & Streamable HTTP 服务。
"""

import os
import time
import logging
from typing import List, Dict, Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from server import mcp, AUTH_TOKEN

# 审计与运行日志
logger = logging.getLogger("mcp-security-audit")
logger.setLevel(logging.INFO)

recent_access_logs: List[Dict[str, Any]] = []
MAX_AUDIT_LOGS = 100

# 创建主 FastAPI 实例
app = FastAPI(
    title="Private PostgresStore MCP Server",
    description="基于 LangGraph 的高度可控私有化长期记忆服务",
    version="2.0.0"
)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SecurityAndAuditMiddleware(BaseHTTPMiddleware):
    """
    1. 统一拦截鉴权（保护私有记忆数据，仅允许持有 AUTH_TOKEN 的用户访问）
    2. 记录真实来访 IP 与审计日志
    3. 修复 Vercel 路由改写导致的内部路径漂移
    """
    async def dispatch(self, request: Request, call_next):
        # 1. 提取客户端真实 IP 与 User-Agent
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host or "unknown"
        user_agent = request.headers.get("user-agent", "")
        path = request.url.path

        # 2. 公开免密路由（健康检查与欢迎说明）
        if path in ["/", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        # 3. 校验访问 Token
        token = AUTH_TOKEN
        auth_passed = True
        auth_reason = "Token verification disabled (Public)"

        if token:
            auth_header = request.headers.get("authorization", "")
            query_token = request.query_params.get("token", "")

            is_valid = False
            if auth_header.startswith("Bearer ") and auth_header[7:].strip() == token:
                is_valid = True
            elif query_token == token:
                is_valid = True

            if not is_valid:
                auth_passed = False
                auth_reason = "Unauthorized: Invalid or missing token"

        # 4. 记录安全审计日志（输出到 Vercel 日志控制台）
        log_status = "ALLOWED" if auth_passed else "BLOCKED"
        log_level = logging.INFO if auth_passed else logging.WARNING
        logger.log(
            log_level,
            f"[{log_status}] IP: {client_ip} | Method: {request.method} | Path: {path} | UA: {user_agent[:100]}"
        )

        log_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ip": client_ip,
            "method": request.method,
            "path": path,
            "user_agent": user_agent[:120],
            "status": log_status,
            "reason": auth_reason
        }
        recent_access_logs.append(log_entry)
        if len(recent_access_logs) > MAX_AUDIT_LOGS:
            recent_access_logs.pop(0)

        # 5. 若未授权直接返回 401
        if not auth_passed:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "This is a private MCP memory server. Access denied.",
                    "client_ip": client_ip
                }
            )

        return await call_next(request)


# 注册标准中间件
app.add_middleware(SecurityAndAuditMiddleware)


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Private PostgresStore MCP Server",
        "endpoints": {
            "sse": "/sse",
            "streamable_http": "/mcp",
            "audit": "/audit (requires token)",
            "health": "/health"
        },
        "auth_enabled": bool(AUTH_TOKEN)
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/audit")
async def get_audit_logs(token: str = ""):
    """仅允许管理员查看最近的访问记录（包含拦截日志）"""
    if AUTH_TOKEN and token != AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    return {
        "total_recorded": len(recent_access_logs),
        "logs": list(reversed(recent_access_logs))
    }


# =====================================================================
# 核心修复：禁用 MCP 默认的 Localhost 限制，放行公网 Host 避免 421 错误
# 同时挂载 SSE 端点与 Streamable HTTP 端点
# =====================================================================
try:
    from mcp.server.transport_security import TransportSecuritySettings
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp_sse_app = mcp.sse_app(transport_security=transport_security)
    mcp_stream_app = mcp.streamable_http_app(transport_security=transport_security)
    app.mount("/mcp", mcp_stream_app)
except Exception:
    try:
        mcp_sse_app = mcp.sse_app(host="0.0.0.0")
    except Exception:
        mcp_sse_app = mcp.sse_app()

# 将 MCP SSE 端点挂载到主应用
app.mount("/", mcp_sse_app)
