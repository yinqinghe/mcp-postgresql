"""
PostgresStore MCP Server
基于 LangGraph PostgresStore 实现的高可控、私有化长期记忆（Long-Term Memory）MCP 服务。
兼容 MCP 1.x 与 2.x，支持本地 stdio 模式与 Vercel / Cloud Serverless 的 HTTP-SSE 模式。
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# 兼容 mcp 1.x 和 2.x
try:
    from mcp.server import MCPServer
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP as MCPServer

load_dotenv()

# 日志输出配置（输出到标准输出，Vercel 会自动收集为运行时审计日志）
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [AUDIT] %(message)s")
logger = logging.getLogger("postgres-memory-mcp")

# 环境变量
DB_URI = os.getenv(
    "DATABASE_URL",
    os.getenv("DB_URI", "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable")
)
AUTH_TOKEN = os.getenv("AUTH_TOKEN", "").strip()

# 全局单例
_pool = None
_store = None
_setup_done = False


async def get_store():
    """按需初始化 AsyncPostgresStore，具备断线自愈与容错"""
    global _pool, _store, _setup_done
    
    # 动态导入，避免缺失依赖时模块直接崩溃
    try:
        from langgraph.store.postgres import AsyncPostgresStore
        from psycopg_pool import AsyncConnectionPool
    except ImportError as err:
        raise RuntimeError(
            f"缺少核心依赖包: {err}。请确保已安装 'langgraph-checkpoint-postgres' 与 'psycopg[binary,pool]'。"
        )

    if not DB_URI or ("localhost" in DB_URI and os.getenv("VERCEL")):
        raise ValueError(
            "DATABASE_URL 未正确配置！请在 Vercel 环境变量中配置有效的云端 PostgreSQL 连接串（如 Neon 或 Supabase）。"
        )

    if _pool is None:
        logger.info("Initializing Postgres connection pool...")
        # Serverless 连接池配置：短超时、少量连接，防止耗尽 PG 连接
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
        logger.info("Executing store.setup() for LangGraph table initialization...")
        await _store.setup()
        _setup_done = True
        logger.info("LangGraph PostgresStore is initialized and ready.")

    return _store


# 创建 MCP 实例
mcp = MCPServer("postgres-memory-store")


@mcp.tool()
async def save_memory(
    namespace: List[str],
    key: str,
    value: Dict[str, Any]
) -> str:
    """
    保存或更新一条长期记忆到 PostgreSQL 中。

    参数:
    - namespace: 记忆分类层级，例如 ["users", "user_1", "profile"] 或 ["projects", "mcp_app"]
    - key: 该分类下的唯一键名，例如 "code_style" 或 "project_context"
    - value: 记忆的具体内容（字典对象）
    """
    logger.info(f"save_memory called -> namespace={namespace}, key={key}")
    try:
        store = await get_store()
        ns_tuple = tuple(namespace)
        await store.aput(ns_tuple, key, value)
        return f"Successfully saved memory at namespace={list(ns_tuple)}, key='{key}'"
    except Exception as e:
        logger.error(f"Error in save_memory: {str(e)}", exc_info=True)
        return f"Failed to save memory: {str(e)}"


@mcp.tool()
async def get_memory(
    namespace: List[str],
    key: str
) -> Dict[str, Any]:
    """
    读取指定的长期记忆。

    参数:
    - namespace: 命名空间层级，例如 ["users", "user_1", "profile"]
    - key: 键名
    """
    logger.info(f"get_memory called -> namespace={namespace}, key={key}")
    try:
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
    except Exception as e:
        logger.error(f"Error in get_memory: {str(e)}", exc_info=True)
        return {"found": False, "error": str(e)}


@mcp.tool()
async def search_memory(
    namespace_prefix: List[str],
    query: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    在指定的命名空间前缀下检索记忆（支持关键词检索与前缀扫描）。

    参数:
    - namespace_prefix: 命名空间前缀，例如 ["users"] 或 ["projects", "mcp_app"]
    - query: 可选检索关键词
    - limit: 最多返回条数（默认 10）
    """
    logger.info(f"search_memory called -> namespace_prefix={namespace_prefix}, query={query}, limit={limit}")
    try:
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
    except Exception as e:
        logger.error(f"Error in search_memory: {str(e)}", exc_info=True)
        return [{"error": str(e)}]


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
    logger.info(f"delete_memory called -> namespace={namespace}, key={key}")
    try:
        store = await get_store()
        ns_tuple = tuple(namespace)
        await store.adelete(ns_tuple, key)
        return f"Successfully deleted memory for key '{key}' in namespace {list(ns_tuple)}"
    except Exception as e:
        logger.error(f"Error in delete_memory: {str(e)}", exc_info=True)
        return f"Failed to delete memory: {str(e)}"


@mcp.tool()
async def list_memory_namespaces(
    prefix: Optional[List[str]] = None,
    limit: int = 50
) -> List[List[str]]:
    """
    列出已存在的记忆分类目录。

    参数:
    - prefix: 可选前缀
    - limit: 返回的最大分类数量
    """
    logger.info(f"list_memory_namespaces called -> prefix={prefix}, limit={limit}")
    try:
        store = await get_store()
        p_tuple = tuple(prefix) if prefix else None
        namespaces = await store.alist_namespaces(prefix=p_tuple, limit=limit)
        return [list(ns) for ns in namespaces]
    except Exception as e:
        logger.error(f"Error in list_memory_namespaces: {str(e)}", exc_info=True)
        return [[f"Error: {str(e)}"]]


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
