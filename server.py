"""
PostgresStore MCP Server (纯文本/全文检索 - Token 极致优化版)
优化措施：
1. 压缩工具 Schema 与 Docstring，大幅削减每次对话被强制注入的 Prompt Token
2. 过滤无用元数据，仅返回极简键值，默认收窄至 top 3
"""

import os
import logging
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# 兼容 MCP 1.x & 2.x
try:
    from mcp.server import MCPServer
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [AUDIT] %(message)s")
logger = logging.getLogger("postgres-memory-mcp")

DB_URI = os.getenv(
    "DATABASE_URL",
    os.getenv("DB_URI", "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable")
)
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip()

_pool = None
_store = None
_setup_done = False


async def get_store():
    global _pool, _store, _setup_done

    try:
        from langgraph.store.postgres import AsyncPostgresStore
        from psycopg_pool import AsyncConnectionPool
    except ImportError as err:
        raise RuntimeError(f"缺少核心依赖: {err}")

    if not DB_URI or ("localhost" in DB_URI and os.getenv("VERCEL")):
        raise ValueError("请在环境变量中配置有效的 DATABASE_URL。")

    if _pool is None:
        logger.info("初始化 PostgreSQL 连接池...")
        _pool = AsyncConnectionPool(
            conninfo=DB_URI,
            min_size=1,
            max_size=int(os.getenv("DB_POOL_MAX", "4")),
            open=False,
            kwargs={"autocommit": True}
        )
        await _pool.open()
        _store = AsyncPostgresStore(_pool)

    if not _setup_done and _store is not None:
        logger.info("执行 store.setup() 初始化数据库表...")
        await _store.setup()
        _setup_done = True
        logger.info("LangGraph PostgresStore 就绪。")

    return _store


mcp = MCPServer("postgres-memory-store")

# =========================================================================
# 极致 Token 优化策略：
# 1. 紧凑简洁的 Docstring（大幅减小每次对话中注入 System Prompt 的 Schema 体积）
# 2. 默认返回 top 3，剔除冗余字段（不返回多余的时间戳/复杂层级元数据）
# =========================================================================

@mcp.tool()
async def save_memory(
    ns: List[str],
    key: str,
    content: str
) -> str:
    """Save long-term memory. ns: path tags like ['user', 'pref']; key: item name; content: text."""
    try:
        store = await get_store()
        await store.aput(tuple(ns), key, {"content": content, "text": content})
        return f"OK: saved '{key}'"
    except Exception as e:
        logger.error(f"save_memory failed: {e}")
        return f"Err: {e}"


@mcp.tool()
async def search_memory(
    ns: List[str],
    query: Optional[str] = None,
    limit: int = 3
) -> List[Dict[str, str]]:
    """Search memories by keyword query or prefix. Default limit 3 to save tokens."""
    try:
        store = await get_store()
        clamped_limit = min(max(1, limit), 5)
        items = await store.asearch(tuple(ns), query=query, limit=clamped_limit)

        results = []
        for it in items:
            val = it.value.get("content") or it.value.get("text") or str(it.value)
            results.append({"k": it.key, "v": val})
        return results
    except Exception as e:
        logger.error(f"search_memory failed: {e}")
        return [{"err": str(e)}]


@mcp.tool()
async def get_memory(
    ns: List[str],
    key: str
) -> Optional[str]:
    """Get single memory by exact key. Returns content text only."""
    try:
        store = await get_store()
        item = await store.aget(tuple(ns), key)
        if not item:
            return None
        return item.value.get("content") or item.value.get("text") or str(item.value)
    except Exception as e:
        logger.error(f"get_memory failed: {e}")
        return f"Err: {e}"


@mcp.tool()
async def delete_memory(
    ns: List[str],
    key: str
) -> str:
    """Delete a memory item."""
    try:
        store = await get_store()
        await store.adelete(tuple(ns), key)
        return f"OK: deleted '{key}'"
    except Exception as e:
        logger.error(f"delete_memory failed: {e}")
        return f"Err: {e}"


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "sse":
        port = int(os.getenv("PORT", "8000"))
        logger.info(f"Starting MCP Server on SSE at port {port}...")
        mcp.run(transport="sse")
    else:
        logger.info("Starting MCP Server on STDIO...")
        mcp.run(transport="stdio")
