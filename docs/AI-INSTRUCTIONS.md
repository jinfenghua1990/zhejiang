# AI 开发约定（每次会话必读）

> 把本文件发给任何 AI / Codex，即可避免系统被拆成一堆端口的经典错误。

## 一、端口与部署（最高优先级）

**所有功能统一在现有 8000 端口项目内开发，不允许新开独立端口，功能通过路由区分。**

- 当前整个系统统一运行在本地 **8000 端口**：<http://localhost:8000>
- 后续新增或修改的所有业务模块，都继续集成在这个现有系统内运行。
- **不要**为不同模块单独新建 8001、8002、9001 等端口，**不要**重新创建独立项目。
- 所有功能统一挂载在 8000 端口下面，通过不同页面路由进行区分。例如：
  - `http://localhost:8000/` 系统首页（经营总览）
  - `http://localhost:8000/supply-chain` 供应链中心
  - `http://localhost:8000/finance` 财务资料
  - `http://localhost:8000/products` 货品档案
  - `http://localhost:8000/purchase/workbench` 采购工作台
- 如果当前项目已有自己的路由结构，**优先沿用现有结构**，不要强行重建路径。
- 对 AI 下达部署指令的标准句式：**「端口：8000；页面路径：/xxx」**

## 二、修改前必须先做

1. 读取当前项目完整目录结构，确认：前端框架、后端框架、启动入口、实际监听端口、前端路由位置、导航位置、相关业务模块代码位置、数据模型位置、已有接口位置、数据库结构。
2. **必须基于现有代码继续开发**：优先复用已有组件、数据库和接口，不重复造轮子。
3. 先分析并给出方案，再动手；不做大规模无关重构。

## 三、现有系统速查（2026-09-07 核对）

- **架构**：`backend/`（FastAPI + SQLAlchemy + PostgreSQL，Alembic）+ `frontend/`（Next.js App Router，`output: "export"` 静态导出）。
- **启动**：`cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`（无 --reload，改后端必须 kill 重启）。
- **前端生效**：改前端后必须 `cd frontend && npm run build`（产物 `frontend/out/` 由 FastAPI 同端口托管）+ 浏览器刷新。
- **前端入口**：`frontend/src/app/**/page.tsx`；全局侧边栏：`frontend/src/components/sidebar.tsx`（NAV_ITEMS 数组）。
- **采购工作台**：`/purchase/workbench`，页面内自带二级导航视图系统（`workbench-navigation.ts` + `?view=` 参数）。
- **后端路由**：`backend/app/api/v1/`（25 个路由模块，统一 `/api/v1` 前缀，JWT 鉴权）。
- **数据库**：PostgreSQL，81 张表（Alembic 迁移；⚠ 历史存在多 head 分叉，新增迁移前先 `alembic heads` 检查）。
- **数据源**：1688 订单（浏览器直采/导入）、吉客云（MCP 同步 + 文件导入）、银行流水、税务发票、销售清单导入。

## 四、硬性禁令

- 不能破坏：现有商品档案、库存、采购数据、1688 同步、吉客云模块、现有数据库。
- 不写死模拟数据；暂时没有数据来源的字段/接口，预留并明确标注 `TODO`，不伪造数据。
- 新增数据库字段必须提供 migration。
- 新增接口保持 `/api/v1` REST 风格统一。
- Git：禁止 `git add -A` / `git add .`，逐个文件 add；先备份后删除。
- **每次 `git pull`（或 fetch+merge）前必须先备份**：① 未提交改动先 commit 或 stash（绝不丢弃）；② 在当前 HEAD 建备份分支 `git branch backup/pre-pull-<YYYYMMDD-HHMM>`；③ 再 pull。合并出问题可 `git reset --hard backup/pre-pull-...` 整体回滚。
