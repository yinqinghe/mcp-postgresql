"""
PostgresStore MCP Server
基于 LangGraph PostgresStore 实现的高可控、私有化长期记忆（Long-Term Memory）MCP 服务。
支持本地 stdio 模式与 Vercel / Cloud Serverless 的 HTTP-SSE 模式。
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

load_dotenv()

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("postgres-memory-mcp")

# 数据库连接串
DB_URI = os.getenv(
    "DATABASE_URL",
    os.getenv("DB_URI", "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable")
)

# 认证 Token（可选保护 Vercel 上的公网端点）
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "")

# 全局 store 句柄与连接池
_pool: Optional[AsyncConnectionPool] = None
_store: Optional[AsyncPostgresStore] = None


async def get_store() -> AsyncPostgresStore:
    """获取或初始化 AsyncPostgresStore 单例"""
    global _pool, _store
    if _store is None:
        logger.info("Initializing Postgres connection pool...")
        # 兼容 Serverless: 设置较短超时与合理的连接池大小
        _pool = AsyncConnectionPool(
            conninfo=DB_URI,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "5")),
            open=False,
            kwargs={"autocommit": True}
        )
        await _pool.open()
        _store = AsyncPostgresStore(_pool)
        # 初始化 LangGraph Store 的数据库表结构（幂等操作）
        logger.info("Running PostgresStore setup (creating tables if not exists)...")
        await _store.setup()
        logger.info("PostgresStore is ready.")
    return _store


# 初始化 FastMCP
mcp = FastMCP(
    "postgres-memory-store",
    dependencies=["langgraph-checkpoint-postgres", "psycopg", "fastapi"]
)


@mcp.tool()
async def save_memory(
    namespace: List[str],
    key: str,
    value: Dict[str, Any]
) -> str:
    """
    保存或更新一条长期记忆到 PostgreSQL 中。

    参数:
    - namespace: 记忆的分类层级/命名空间，例如 ["users", "user_123", "profile"] 或 ["project_alpha", "conventions"]
    - key: 该分类下的唯一键名，例如 "coding_preferences", "user_goals", "milestones"
    - value: 记忆的具体内容（字典格式，可包含任意 JSON 字段）
    """
    store = await get_store()
    ns_tuple = tuple(namespace)
    await store.aput(ns_tuple, key, value)
    return f"Memory successfully saved at namespace={list(ns_tuple)}, key='{key}'"


@mcp.tool()
async def get_memory(
    namespace: List[str],
    key: str
) -> Dict[str, Any]:
    """
    根据命名空间和键获取指定的长期记忆。

    参数:
    - namespace: 命名空间层级，例如 ["users", "user_123", "profile"]
    - key: 键名
    """
    store = await get_store()
    ns_tuple = tuple(namespace)
    item = await store.aget(ns_tuple, key)
    if not item:
        return {"found": False, "message": f"No memory found for key '{key}' in namespace {list(ns_tuple)}"}
    
    return {
        "found": True,
        "key": item.key,
        "namespace": list(item.namespace),
        "value": item.value,
        "created_at": item.created_at.isoformat() if hasattr(item, "created_at") and item.created_at else None,
        "updated_at": item.updated_at.isoformat() if hasattr(item, "updated_at") and item.updated_at else None,
    }


@mcp.tool()
async def search_memory(
    namespace_prefix: List[str],
    query: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    在指定的命名空间前缀下检索记忆（支持关键词检索与前缀扫描）。

    参数:
    - namespace_prefix: 命名空间前缀，例如 ["users", "user_123"] 会检索该用户下的所有记忆
    - query: 可选的检索关键词。若提供则进行文本相关性搜索；若为空则列出该前缀下的最近记忆
    - limit: 最多返回的结果数量（默认为 10）
    """
    store = await get_store()
    ns_prefix = tuple(namespace_prefix)
    items = await store.asearch(ns_prefix, query=query, limit=limit)
    
    results = []
    for item in items:
        results.append({
            "namespace": list(item.namespace),
            "key": item.key,
            "value": item.value,
            "created_at": item.created_at.isoformat() if hasattr(item, "created_at") and item.created_at else None,
            "updated_at": item.updated_at.isoformat() if hasattr(item, "updated_at") and item.updated_at else None,
        })
    return results


@mcp.tool()
async def delete_memory(
    namespace: List[str],
    key: str
) -> str:
    """
    删除指定的长期记忆。

    参数:
    - namespace: 命名空间层级
    - key: 要删除的键名
    """
    store = await get_store()
    ns_tuple = tuple(namespace)
    await store.adelete(ns_tuple, key)
    return f"Memory deleted for key '{key}' in namespace {list(ns_tuple)}"


@mcp.tool()
async def list_memory_namespaces(
    prefix: Optional[List[str]] = None,
    limit: int = 50
) -> List[List[str]]:
    """
    列出已存在的记忆命名空间分类目录。

    参数:
    - prefix: 可选的前缀过滤，例如 ["users"] 将仅返回以 users 开头的命名空间
    - limit: 最多返回的分类数量
    """
    store = await get_store()
    p_tuple = tuple(prefix) if prefix else None
    namespaces = await store.alist_namespaces(prefix=p_tuple, limit=limit)
    return [list(ns) for ns in namespaces]


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "sse":
        port = int(os.getenv("PORT", "8000"))
        logger.info(f"Starting MCP Server on SSE mode at http://0.0.0.0:{port}")
        mcp.run(transport="sse")
    else:
        logger.info("Starting MCP Server on STDIO mode...")
        mcp.run(transport="stdio")
