# PostgresStore MCP Server (高可控私有化长期记忆服务)

本项目基于 **LangGraph 的 `PostgresStore`** 和 **MCP (Model Context Protocol)** 协议，构建了一个完全私有化、零信任鉴权、高度可控的外接长期记忆服务。

可部署于 **Vercel**（基于 HTTP-SSE 协议提供无状态 Serverless 端点），也可在本地以 **stdio** 或本地 HTTP 模式运行，直接接入 **Google Antigravity**、Cursor、Claude Desktop 或任何支持 MCP 的智能体（Agent）。

---

## 🔒 独占访问与安全防护（仅自己可用）

当部署到公网 Vercel 时，为了防止他人探测、盗刷或篡改数据库中的记忆，内置了**零信任安全鉴权机制**：

1. **Token 强制认证**：
   - 在 Vercel 环境变量中配置 `AUTH_TOKEN`（例如一个高强度的随机密钥）。
   - 所有对记忆工具的访问（包括 `/sse` 握手和 `/messages` 消息通道）均受到拦截检查。
   - 任何未携带或携带错误 Token 的请求，均会被直接拦截并返回 `401 Unauthorized`，不会接触任何数据库。
2. **双通道鉴权支持**：
   - **Header 认证**：`Authorization: Bearer <AUTH_TOKEN>`（适用于支持自定义 HTTP 头的客户端）。
   - **URL Query 认证**：`https://your-domain.vercel.app/sse?token=<AUTH_TOKEN>`（适用于仅支持在 URL 中传参的 SSE 客户端）。
3. **实时访问监控与审计日志 (Audit Logs)**：
   - 记录每一次请求的 **时间戳、真实客户端 IP、请求路径、User-Agent 以及认证结果 (`ALLOWED` / `BLOCKED`)**。
   - **在 Vercel 控制台查看**：在 Vercel 的 **Project -> Logs** 页面可实时查阅所有访问与非法探测记录。
   - **专属审计接口**：管理员可直接请求 `GET /audit?token=<AUTH_TOKEN>` 查看最近 100 次的访问记录。

---

## 🛠️ 故障排查与修复说明 (Vercel 部署)

### 1. `WARNING! Internal rewrites...` 警告修复
- **原因**：Vercel 最新版本的路由机制中，使用 `rewrites` 将所有路径重写到 `/api/index.py` 时，后端框架接收到的内部路径会被覆盖为 destination（即 `/api/index.py`），导致无法匹配 `/sse`。
- **解决**：改用 `routes` 配置，并在 `api/index.py` 中内置了 ASGI 路径自愈中间件，自动提取请求头中的原始路径（`x-matched-path`），同时将 MCP 应用并行挂载在 `/` 与 `/api`，确保无论何种路由形式均可 100% 正确命中。

### 2. HTTP 500 (Non-200 status code) 修复
- **原因**：
  1. `mcp` 升级到 2.x 后内部模块重构，直接引用 `FastMCP` 会导致 `ModuleNotFoundError`。
  2. 依赖项中缺少 `langgraph` 主包。
  3. 数据库连接串未配置或异常时在顶层模块抛出异常导致进程崩溃。
- **解决**：
  1. 增加了 `MCPServer` (2.x) 与 `FastMCP` (1.x) 的无缝向下兼容层。
  2. 补齐 `requirements.txt`。
  3. 数据库连接改为**惰性延迟初始化**与优雅容错，即使数据库短暂波动也不会导致整个服务直接 Crash 500。

---

## 📁 目录结构

```text
mcp-PostgreSQL/
├── api/
│   └── index.py            # Vercel Serverless 入口 (FastAPI + SSE + 安全拦截与审计)
├── server.py               # 核心 MCP 工具与 PostgresStore 连接逻辑 (支持 MCP 1.x & 2.x)
├── requirements.txt        # 依赖清单
├── vercel.json             # Vercel 路由配置 (消除 rewrites warning)
├── .env.example            # 环境变量配置模版
└── README.md               # 详细文档
```

---

## 🚀 部署到 Vercel 步骤

1. 将代码推送到 GitHub 私有仓库。
2. 在 [Vercel Dashboard](https://vercel.com/) 中点击 **"Add New Project"** 并导入该仓库。
3. 在 Vercel 项目的 **Settings -> Environment Variables** 中添加：
   - `DATABASE_URL`：PostgreSQL 连接串（推荐使用 Neon 或 Supabase 的连接池串）。
   - `AUTH_TOKEN`：**必填**，你的专属私钥（例如 `my_super_secret_key_123`）。
   - `DB_POOL_MAX`：建议设置为 `3` 或 `4`。
4. 点击 **Deploy**。

---

## 🔌 在各类 Agent 中接入配置

### 1. 接入 Google Antigravity

在 Antigravity 的用户/工作区配置中（如 `settings.json` 或 MCP 扩展设置）添加：

#### 方案 A：远程 Vercel 部署 (SSE) - 推荐
如果客户端支持自定义 Headers：
```json
{
  "mcpServers": {
    "postgres-memory": {
      "url": "https://your-domain.vercel.app/sse",
      "headers": {
        "Authorization": "Bearer my_super_secret_key_123"
      }
    }
  }
}
```

如果客户端不支持自定义 Headers，可以直接在 URL 中携带：
```json
{
  "mcpServers": {
    "postgres-memory": {
      "url": "https://your-domain.vercel.app/sse?token=my_super_secret_key_123"
    }
  }
}
```

#### 方案 B：本地直接运行 (stdio)
```json
{
  "mcpServers": {
    "postgres-memory": {
      "command": "python",
      "args": ["d:/Code/AI/MCP/mcp-PostgreSQL/server.py", "stdio"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/postgres?sslmode=disable"
      }
    }
  }
}
```

---

## 🧠 记忆管理常用工具

- `save_memory(namespace, key, value)`: 保存或更新结构化记忆
- `get_memory(namespace, key)`: 读取指定记忆
- `search_memory(namespace_prefix, query, limit)`: 检索记忆（支持关键词检索）
- `delete_memory(namespace, key)`: 删除/遗忘记忆
- `list_memory_namespaces(prefix, limit)`: 查看所有记忆分类目录
