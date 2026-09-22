# Mac 本地运行

当前阶段以 Mac 原生运行作为主要开发与验收环境，NAS / 极空间部署暂缓。

## 运行原则

- 代码主线：`develop`
- 部署方式：`native`
- 默认端口：`8000`
- PostgreSQL / Redis：Mac 本机服务
- 数据、备份、日志：尽量放在仓库目录之外
- 系统版本中心：显示当前 Git SHA、环境、数据库 Revision
- NAS / GHCR / Compose 配置保留，但当前不作为日常运行入口

## 首次配置

1. 复制 `deploy/mac/.env.example` 到项目根目录 `.env`。
2. 修改数据库密码、管理员密码和持久化目录。
3. 确保 PostgreSQL、Redis、Python 虚拟环境和前端依赖已就绪。
4. 使用现有 LaunchAgent / `scripts/native-start.sh` 启动。

建议的运行身份：

```text
APP_ENV=development
RELEASE_CHANNEL=local
DEPLOYMENT_MODE=native
SYSTEM_UPDATE_BRANCH=develop
```

如果 Mac 当前承担正式业务数据，也可以在真实 `.env` 中继续使用：

```text
APP_ENV=production
RELEASE_CHANNEL=stable
DEPLOYMENT_MODE=native
```

production 必须把 `PERSIST_ROOT / DATA_DIR / BACKUP_DIR / LOG_DIR` 放在 Git 仓库外。旧安装若仍使用项目内 `data/ backups/ logs/`，执行：

```bash
make persistence-migrate
```

脚本会先停止服务并备份，再复制和校验数据、更新 `.env`、重启并做健康检查；旧目录不会自动删除。

物理机器是 Mac 还是 NAS，与逻辑环境是否 production 是两回事，不强制修改现有生产数据环境。

## Codex 开发流程

```text
codex/*
  ↓ PR
develop
  ↓ CI
Mac 本地拉取 / 更新
  ↓
本地业务验收
```

在 NAS 部署恢复之前，`main` 和 PR #44 继续作为未来正式容器发布门禁，不因为本地开发而提前合并。

## 安全

- `.env` 不提交 Git。
- PostgreSQL / Redis 不开放公网。
- 开发期不要把 Mac 的 8000 端口直接映射到公网。
- 每次较大结构调整前继续执行数据库 + DATA_DIR 备份。
