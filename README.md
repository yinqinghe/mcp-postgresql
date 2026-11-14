# PostgresStore MCP Server (高可控私有化长期记忆服务)

本项目基于 **LangGraph 的 `PostgresStore`** 和 **MCP (Model Context Protocol)** 协议，构建了一个完全私有化、高度可控的外接长期记忆服务。

可部署于 **Vercel**（基于 HTTP-SSE 协议提供无状态 Serverless 端点），也可在本地以 **stdio** 或本地 HTTP 模式运行，直接接入 **Google Antigravity**、Cursor、Claude Desktop 或任何支持 MCP 的智能体（Agent）。

---

## 🌟 特性

1. **完全私有化 & 自主掌控**：数据直存于您的 PostgreSQL（支持 Neon, Supabase, 阿里云 RDS 或自建 PostgreSQL），杜绝第三方数据泄露风险。
2. **LangGraph Store 原生兼容**：底层基于 `langgraph.store.postgres.AsyncPostgresStore`，与 LangGraph 生态完全统一，支持层级分类 (`namespace`)、键值存储与全文检索。
3. **适配 Serverless 环境 (Vercel)**：采用异步连接池 (`psycopg_pool`) 与短连接优化，提供标准的 MCP SSE 端点 `/sse` 与 `/messages`。
4. **双模运行**：
   - **本地 stdio 模式**：无需公网暴露，本地客户端直接拉起 Python 进程。
   - **HTTP-SSE 模式**：部署在 Vercel 或轻量容器中，支持可选的 `AUTH_TOKEN` 鉴权。
5. **为 Agent 长期记忆量身定制的工具集**：
   - `save_memory`: 存储/更新记忆
   - `get_memory`: 读取指定记忆
   - `search_memory`: 关键字或前缀检索相关记忆
   - `delete_memory`: 遗忘/清理记忆
   - `list_memory_namespaces`: 查看记忆分类树

---

## 📁 目录结构

```text
mcp-PostgreSQL/
├── api/
│   └── index.py            # Vercel Serverless 入口 (FastAPI + FastMCP SSE)
├── server.py               # 核心 MCP 工具与 PostgresStore 连接逻辑
├── requirements.txt        # 依赖清单
├── vercel.json             # Vercel 路由配置
├── .env.example            # 环境变量配置模版
└── README.md               # 详细文档
```

---

## 🚀 快速开始

### 1. 本地安装与准备

推荐使用 Python 3.10+：

```bash
# 克隆或进入项目目录
cd mcp-PostgreSQL

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
copy .env.example .env
```

在 `.env` 中填入你的 PostgreSQL 连接串：
```env
DATABASE_URL=postgresql://user:password@localhost:5432/dbname?sslmode=disable
AUTH_TOKEN=my_secret_token
```

> **提示**：PostgresStore 会在首次启动时自动调用 `store.setup()` 创建所需的表结构（`store` 表及索引），无需手动建表。

### 2. 本地测试运行

- **以 stdio 模式启动（供本地 Agent CLI 或 IDE 管道连接）**：
  ```bash
  python server.py stdio
  ```

- **以 SSE 模式启动（本地 HTTP 服务）**：
  ```bash
  python server.py sse
  ```
  服务默认运行在 `http://localhost:8000/sse`。

---

## ☁️ 部署到 Vercel

1. 将代码推送到 GitHub 私有仓库。
2. 在 [Vercel Dashboard](https://vercel.com/) 中点击 **"Add New Project"** 并导入该仓库。
3. 在 Vercel 项目的 **Settings -> Environment Variables** 中添加：
   - `DATABASE_URL`：PostgreSQL 连接串。
     - *若使用 Supabase/Neon*，推荐使用带连接池的连接串（Transaction Pooler，Supabase 端口通常为 6543）。
   - `AUTH_TOKEN`：访问秘钥（用于保护公网接口）。
   - `DB_POOL_MAX`：建议设置为 `3` 或 `5`，避免 Serverless 多实例占满数据库连接。
4. 点击 **Deploy**。部署完成后你将获得一个域名，例如：`https://your-mcp-memory.vercel.app`。
5. MCP SSE 端点地址为：
   `https://your-mcp-memory.vercel.app/sse`
   （若配置了 `AUTH_TOKEN`，可在 Query 中附带：`https://your-mcp-memory.vercel.app/sse?token=my_secret_token`）

---

## 🔌 在各类 Agent 中接入配置

### 1. 接入 Google Antigravity

在 Antigravity 的用户配置或工作区 MCP 配置中（如 `mcp_servers` 配置）添加：

#### 方案 A：远程 Vercel 部署 (SSE)
```json
{
  "mcpServers": {
    "postgres-memory": {
      "url": "https://your-mcp-memory.vercel.app/sse",
      "headers": {
        "Authorization": "Bearer my_secret_token"
      }
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

### 2. 接入 Cursor / Claude Desktop

在 `claude_desktop_config.json` 或 Cursor MCP 设置中配置：

```json
{
  "mcpServers": {
    "postgres_memory": {
      "command": "python",
      "args": ["<绝对路径>/server.py", "stdio"],
      "env": {
        "DATABASE_URL": "postgresql://..."
      }
    }
  }
}
```

---

## 🧠 长期记忆管理最佳实践 (Prompt 建议)

为了让 Agent 更智能地使用此 MCP 服务，你可以在 Agent 的 System Prompt 或 Instructions 中加入如下指引：

```markdown
你有权使用 `postgres-memory` 工具来维护和读取长期记忆：
1. **记忆分类（Namespace 约定）**：
   - 用户画像与偏好：`["user", "profile"]`
   - 项目规范与约束：`["projects", "<project_name>", "rules"]`
   - 经验与教训（Tips）：`["knowledge", "solutions"]`
2. **检索时机**：
   - 遇到新任务时，先使用 `search_memory` 或 `get_memory` 查询是否有过往经验、用户特定偏好或规范。
3. **更新时机**：
   - 当用户纠正了错误、声明了长期有效的个人偏好、或达成重要技术决策时，使用 `save_memory` 主动归档。
```
