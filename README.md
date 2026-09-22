# 电商工作平台

> GitHub 仓库：`jinfenghua1990/ecommerce-workspace`

这是一个面向实际经营的电商工作平台，不是单纯的数据看板。当前系统同时承载 **内销、外贸、供应链、库存、物流、财务与月结**，并通过统一业务数据与财务事项底座把各模块串起来。

## 当前定位

平台分为三个主要工作入口：

### 内销工作台

- 经营总览
- 销售中心
- 基础货品
- 库存中心
- 供应链中心
- 快递物流
- 财务中心

### 外贸工作台

- 外贸总览
- 外贸订单
- B2B 客户 / 经销商
- 渠道管理
- 海外 SKU 映射
- 国际出运 / Shipment
- 外贸财务
- Alsvid 业务

### 财务中心

财务中心是独立的统一财务中枢。

第一维度是 **公司主体**，第二维度是 **业务范围**：

```text
公司主体
  ↓
全部 / 内销 / 外贸
  ↓
财务事项 FinanceEntry
  ↓
收支 / 发票税务 / 利润 / 月结
```

当前默认主体为浙江公司；以后新增奥地利、德国、香港等主体，不需要重做一套财务系统。

## 核心业务链路

### 内销

```text
销售订单
  ├─ 销售收入
  ├─ 售后退款
  └─ 销售成本

采购 / 入库
  └─ 库存采购 / 应付

快递物流
  ├─ 平时按预估成本
  └─ 实际账单核销后替换预估

以上统一进入 FinanceEntry
```

采购入库和销售数据在各自业务模块完成，不在月结页重复上传业务源文件。

### 外贸

```text
外贸订单
  ↓
Shipment 出运单
  ↓
中国出口
  ↓
国际运输
  ↓
欧盟进口 / 清关
  ↓
海外末端配送
  ↓
财务事项 / 月结
```

Shipment 可记录：

- 出口公司主体
- 进口责任方
- 进口公司主体
- 提单 / 柜号 / Tracking
- 出口报关 / 进口报关
- 普通关税
- 反倾销税
- 反补贴税
- 进口 VAT
- 清关 / 港杂 / 末端配送
- 出口退税
- 预计与实际成本

进口税费只有在 **我方公司主体承担进口责任** 时才进入对应主体财务。海外客户、经销商或第三方代理承担的进口费用不会错误记入中国公司账。

## 财务口径

统一财务事项模型：`FinanceEntry`。

主要维度包括：

- legal entity / 公司主体
- business scope / 内销或外贸
- 来源模块与来源单号
- 财务类别
- 收入 / 支出
- 币种
- 金额 / 税额
- 预计 / 实际
- 结算状态
- 发票状态
- 会计账期
- 是否影响现金
- 是否影响利润

现金与利润分开处理。例如：

- 可抵扣进口 VAT：影响现金，不直接影响利润
- Shipment 货品成本：影响利润，不重复计算采购付款现金
- 采购入库：先形成库存采购 / 应付事实，不在入库时直接扣利润
- 物流：实际账单到达后替换原预估，不重复计算

## 月结中心

月结中心按 **公司主体** 切换。

它不再承担采购入库、销售数据导入，而是直接读取业务数据库：

- 业务数据完整性检查
- 销售金额与销售成本检查
- 银行交易明细
- 银行回单
- 无票收入
- 外贸财务汇总
- 月度 ZIP 打包
- 邮件发送给财务
- 历史版本归档

如果销售 SKU 缺采购入库成本，系统会明确提示缺失 SKU，不会用虚构成本生成错误利润。

## 主要外部数据源

当前支持或预留：

- 吉客云
- 1688
- 浙江农信文件导入
- 税务发票清单
- SMTP 财务邮件
- 外贸渠道 / Shopify 后续接入

外部系统未配置时必须如实显示未配置，不使用模拟数据伪装真实连接。

## 技术栈

### 后端

- Python 3.12
- FastAPI
- SQLAlchemy 2
- Alembic
- Pydantic
- PostgreSQL
- Celery
- Redis

### 前端

- Next.js 16
- React 19
- Tailwind CSS 4
- 静态导出，由 FastAPI 同端口托管

### 运行方式

当前主要开发与验收环境：

```text
Mac 本地原生运行
```

NAS / 极空间部署暂缓，容器化与发布配置继续保留。

默认服务端口：

```text
http://127.0.0.1:8000
```

PostgreSQL 与 Redis 不对公网开放。

## 分支与开发流程

当前开发主线（**直提模式**：改完直接提交推送，不再新建 `codex/*` 分支、不再开 PR）：

```text
本地直接修改
        ↓
develop 直提 / 直接推送
        ↓
       CI（push 触发）
        ↓
Mac 本地拉取 / 验收
```

> 2026-09-22 起恢复直提：日常开发直接写 `develop`。仓库内遗留的历史功能分支
> 内容均已合入 `develop`，只保留极少数备份；新改动一律直提、一次提交一次推送。

`main` 作为正式发布主线；当前日常开发不要直接把 `develop` 无条件合并到 `main`，
上线前按发布流程走完整验收。

GitHub 仓库重命名后的远程地址：

```bash
git@github.com:jinfenghua1990/ecommerce-workspace.git
```

本地更新远程地址：

```bash
git remote set-url origin git@github.com:jinfenghua1990/ecommerce-workspace.git
git fetch origin
git switch develop
git pull origin develop
```

## Mac 本地运行

首次准备：

```bash
cp deploy/mac/.env.example .env
```

然后配置：

- PostgreSQL
- Redis
- 管理员账号
- 数据持久化目录
- SMTP
- 吉客云 / 1688 等按需凭证

常用命令：

```bash
make status          # 查看运行状态
make restart         # 重启 API / worker / beat
make rebuild-fe      # 前端重建并重启
make migrate         # Alembic 迁移
make test            # 后端测试
make tsc             # 前端类型检查
make verify          # 本地完整验收
make backup          # 数据库 + DATA_DIR 备份
```

## 持久化原则

代码、数据库、业务文件、备份、日志应尽量分离。

推荐：

```text
代码：
/Users/<user>/ecommerce-workspace

业务数据：
/Users/<user>/ecommerce-workspace-data/<environment>/

其中：
data/
backups/
logs/
```

具体以实际 `.env` 中：

- `PERSIST_ROOT`
- `DATA_DIR`
- `BACKUP_DIR`
- `LOG_DIR`

为准。

不要因为 Git 更新或切换分支覆盖业务数据。

## 目录结构

```text
backend/        FastAPI / SQLAlchemy / Alembic / Celery
frontend/       Next.js 前端
deploy/         Mac / 容器 / NAS 部署配置
docs/           架构、发布、持久化、集成说明
scripts/        启动、备份、恢复、检查脚本
data/           兼容本地默认数据目录；正式环境建议放仓库外
backups/        兼容本地默认备份目录；正式环境建议放仓库外
```

## 安全原则

- `.env` 不提交 Git
- PostgreSQL / Redis 不开放公网
- Mac 开发阶段不要把 8000 直接暴露公网
- 1688 只同步已发生采购，不自动下单 / 付款
- 原始财务资料保留版本，不覆盖历史交付包
- 数据库结构变更必须走 Alembic
- 较大结构调整前先做数据库与业务文件备份

## 系统更新

系统支持原生模式更新与容器发布身份识别。

Mac 当前使用：

```text
DEPLOYMENT_MODE=native
SYSTEM_UPDATE_BRANCH=develop
```

系统更新会检查：

- Git 分支 / 远端
- 工作区状态
- Python / Node 环境
- Alembic
- 备份能力
- 前端产物
- 健康检查
- 磁盘空间

现有 Mac 如果已经安装旧的 LaunchAgent label（例如 `com.gino.ecommerce-dashboard`），可以继续沿用；该 label 是运行配置，不要求与 GitHub 仓库名称完全一致。不要只为仓库改名而直接改 LaunchAgent，除非同时完成本机服务迁移。

## 进一步文档

- `deploy/mac/README.md`
- `docs/ARCHITECTURE.md`
- `docs/RELEASE_STANDARD.md`
- `docs/PERSISTENCE_STANDARD.md`
- `docs/IMPLEMENTATION_STATUS.md`

---

项目名称统一使用：

**电商工作平台**

GitHub 仓库统一使用：

**`ecommerce-workspace`**
