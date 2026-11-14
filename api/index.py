"""
Vercel Serverless Function 入口
将 FastMCP 暴露为 ASGI 应用 (SSE 传输)，适配 Vercel Python Runtime。
"""

import os
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from server import mcp, AUTH_TOKEN

# 创建主 FastAPI 容器应用
app = FastAPI(
    title="PostgresStore MCP Service",
    description="LangGraph PostgresStore 包装的高可控长期记忆 MCP 服务",
    version="1.0.0"
)

# 允许跨域（方便 Web 客户端与各类 Agent 工具链调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 可选鉴权中间件（保护公网部署的 Serverless 接口）
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 健康检查与根路径不鉴权
        if request.url.path in ["/", "/health", "/docs", "/openapi.json"]:
            return await call_next(request)

        token = os.getenv("AUTH_TOKEN", "").strip()
        if token:
            auth_header = request.headers.get("Authorization", "")
            query_token = request.query_params.get("token", "")
            
            valid = False
            if auth_header.startswith("Bearer ") and auth_header[7:].strip() == token:
                valid = True
            elif query_token == token:
                valid = True

            if not valid:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or missing authentication token"
                )

        return await call_next(request)

app.add_middleware(AuthMiddleware)


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "PostgresStore Long-Term Memory MCP Server",
        "sse_endpoint": "/sse",
        "docs": "https://github.com/modelcontextprotocol"
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


# 挂载 FastMCP 的 SSE Starlette 应用程序
# FastMCP 提供了 sse_app() 方法直接生成 ASGI 应用程序
sse_subapp = mcp.sse_app()
app.mount("/", sse_subapp)
