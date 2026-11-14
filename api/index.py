"""
Vercel Serverless Function 入口
为 LangGraph PostgresStore 提供安全加固、具备审计日志与路径自愈的 FastMCP SSE 服务。
"""

import os
import time
import logging
from typing import List, Dict, Any
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from server import mcp, AUTH_TOKEN

# 审计与运行日志
logger = logging.getLogger("mcp-security-audit")
logger.setLevel(logging.INFO)

# 内存简易审计日志缓冲（方便通过管理端点查看最近访问）
recent_access_logs: List[Dict[str, Any]] = []
MAX_AUDIT_LOGS = 100


class VercelPathFixAndAuditMiddleware:
    """
    1. 解决 Vercel 重写后 destination path 变成 /api/index.py 的问题
    2. 记录真实访问 IP 与请求路径（审计日志）
    3. 拦截未授权访问（严格私有化保护）
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        
        # 1. 提取客户端真实 IP 与 User-Agent
        x_forwarded_for = headers.get(b"x-forwarded-for", b"").decode("utf-8")
        client_ip = x_forwarded_for.split(",")[0].strip() if x_forwarded_for else "unknown"
        user_agent = headers.get(b"user-agent", b"").decode("utf-8")

        # 2. 解决 Vercel Internal Rewrites 路径覆盖问题
        # Vercel 会在 x-matched-path 中携带用户原始请求的路径
        matched_path = headers.get(b"x-matched-path", b"").decode("utf-8")
        raw_path = scope.get("path", "")
        
        # 如果当前 path 是入口脚本名（例如 /api/index.py 或 /api/index），替换为原始匹配路径
        if raw_path in ["/api/index.py", "/api/index", "/api/"]:
            if matched_path:
                scope["path"] = matched_path
                raw_path = matched_path

        current_path = scope.get("path", "")

        # 3. 开放无需鉴权的只读路径（如健康检查或欢迎页）
        public_paths = ["/", "/health", "/docs", "/openapi.json"]
        if current_path in public_paths:
            await self.app(scope, receive, send)
            return

        # 4. 身份验证（Token 认证）
        token = AUTH_TOKEN
        auth_passed = True
        auth_reason = "Token verification disabled (Public)"

        if token:
            auth_header = headers.get(b"authorization", b"").decode("utf-8")
            query_string = scope.get("query_string", b"").decode("utf-8")
            
            token_in_query = ""
            for param in query_string.split("&"):
                if param.startswith("token="):
                    token_in_query = param.split("=", 1)[1]
                    break

            is_valid = False
            if auth_header.startswith("Bearer ") and auth_header[7:].strip() == token:
                is_valid = True
            elif token_in_query == token:
                is_valid = True

            if not is_valid:
                auth_passed = False
                auth_reason = "Unauthorized: Invalid or missing token"

        # 5. 记录安全审计日志
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "ip": client_ip,
            "method": scope.get("method", ""),
            "path": current_path,
            "user_agent": user_agent[:120],
            "status": "ALLOWED" if auth_passed else "BLOCKED",
            "reason": auth_reason
        }
        
        # 打印到 Vercel 日志控制台
        log_level = logging.INFO if auth_passed else logging.WARNING
        logger.log(
            log_level,
            f"[{log_entry['status']}] IP: {client_ip} | Path: {current_path} | User-Agent: {log_entry['user_agent']}"
        )
        
        # 缓存日志供 /audit 接口调阅
        recent_access_logs.append(log_entry)
        if len(recent_access_logs) > MAX_AUDIT_LOGS:
            recent_access_logs.pop(0)

        # 6. 若鉴权失败，直接返回 401 拦截
        if not auth_passed:
            response = JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "This is a private MCP memory server. Access denied.",
                    "client_ip": client_ip
                }
            )
            await response(scope, receive, send)
            return

        # 鉴权通过，继续流转
        await self.app(scope, receive, send)


# 创建主应用
app = FastAPI(
    title="Private PostgresStore MCP Server",
    description="基于 LangGraph 的高度可控私有化长期记忆服务",
    version="2.0.0"
)

# 允许跨域（Agent 客户端需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Private PostgresStore MCP Server",
        "endpoints": {
            "sse": "/sse (or /api/sse)",
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


# 获取 FastMCP 的底层 Starlette 应用
mcp_sse_app = mcp.sse_app()

# 同时挂载在根目录和 /api 路径，彻底解决 Vercel 路由前缀问题
app.mount("/api", mcp_sse_app)
app.mount("/", mcp_sse_app)

# 包装安全加固与路径修复中间件（最后挂载的最先执行）
app = VercelPathFixAndAuditMiddleware(app)
