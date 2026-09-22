# 极空间 ZSpace 部署标准

本目录用于极空间 Q4 的 STAGING / PRODUCTION 双环境部署。

## 目标结构

```text
GitHub develop
  -> CI
  -> ghcr.io/...:sha-<commit>
  -> STAGING

develop -> main PR 合并
  -> 不重新构建
  -> 将已测试的 sha 镜像晋级为 vYYYY.MM.DD.HHmmss + stable
  -> PRODUCTION
```

生产环境与测试环境使用同一个应用镜像体系，但数据库、Redis、DATA_DIR、端口必须完全隔离。

## 极空间目录

不要把数据放在 Git 仓库目录中。先在极空间创建类似目录，再用“查询路径”取得真实宿主机路径：

```text
ecommerce/
  staging/
    postgres/
    redis/
    data/
    backups/
  production/
    postgres/
    redis/
    data/
    backups/
```

实际宿主机绝对路径因极空间存储池而异，本仓库不硬编码。

## 私有镜像登录

仓库和 GHCR 镜像保持 Private。

在极空间 Docker 的镜像仓库凭证中添加：

- Registry: ghcr.io
- Username: GitHub 用户名
- Token: 只授予读取 Packages 所需权限的 GitHub token

不要把 Registry 凭证写进应用 .env，也不要提交到 Git。

## STAGING

1. 创建独立 Compose 项目目录。
2. 复制本目录的 docker-compose.yml。
3. 将 staging.env.example 复制为 .env。
4. 填写独立数据库密码、APP_SECRET_KEY、管理员密码和极空间真实目录。
5. APP_IMAGE 默认可以先使用 candidate。
6. 首次启用数据库最小权限时，先备份，再初始化 app / migrator 两个角色；已有数据卷不要修改原来的 POSTGRES_USER：

```bash
docker compose up -d postgres redis
docker compose --profile ops run --rm db-roles
docker compose --profile ops run --rm migrate
docker compose up -d api worker beat
```

`db-roles` 会把业务数据库与 public schema/现有业务对象 ownership 交给 migrator，并只给 app 账号授予业务 DML。它是显式运维动作，不会跟随 API 自动执行；角色密码变更时可再次执行。

7. 浏览器访问 NAS_IP:8100。
8. 做采购、库存、耗材、财务、B2B/B2C 等业务回归。

在准备正式发布前，最好将 APP_IMAGE 从 candidate 改为本次候选的不可变 SHA 标签：

```text
ghcr.io/jinfenghua1990/ecommerce-workspace:sha-<commit>
```

这样最终验收对象不会随着下一次 develop 推送变化。

## PRODUCTION

main 只接受 develop 或 hotfix/* 的 PR。

develop -> main 合并后，GitHub Actions 不会重建镜像，而是把已经测试过的 sha 镜像原样晋级，并生成：

```text
ghcr.io/jinfenghua1990/ecommerce-workspace:vYYYY.MM.DD.HHmmss
ghcr.io/jinfenghua1990/ecommerce-workspace:stable
```

正式环境建议将 APP_IMAGE 固定到具体版本：

```text
APP_IMAGE=ghcr.io/jinfenghua1990/ecommerce-workspace:v2026.09.19.153000
```

不要长期只写 stable。stable 用于发现最新正式版，具体版本标签用于可追踪、可回滚部署。

发布顺序：

```text
确认数据库/文件备份
-> 拉取指定版本镜像
-> 暂停 worker/beat
-> （首次拆分账号或轮换账号密码时）docker compose --profile ops run --rm db-roles
-> docker compose --profile ops run --rm migrate
-> 更新 api
-> /healthz 验证
-> 恢复 worker/beat
-> 业务 smoke test
```

## 回滚

应用回滚优先切换 APP_IMAGE 到上一版本，不重新构建：

```text
v2026.09.19.153000
->
v2026.09.18.221500
```

如果该版本包含数据库迁移，必须按照迁移兼容性决定是 downgrade 还是从发布前备份恢复。禁止直接删除 PostgreSQL 数据目录。

## 更新中心

极空间容器模式下，应用内 Git 自更新关闭：

```text
SYSTEM_UPDATE_ENABLED=0
```

版本发布由 GitHub + GHCR + Compose 管理。系统「版本中心」直接读取运行时身份，展示：
- APP_ENV / RELEASE_CHANNEL
- 实际 Git SHA
- 当前 APP_IMAGE_REF
- Alembic 数据库 Revision
- 容器托管状态

容器模式不会在应用内执行 git pull，也不会启动旧的 Git 更新轮询器。

## 安全边界

- PostgreSQL 不映射到 NAS 局域网端口。
- API / worker / beat 使用 `DATABASE_URL` 的 app 账号；migrate 使用 `MIGRATION_DATABASE_URL` 的 migrator 账号。
- `POSTGRES_USER` 只作为数据库部署/兜底管理员；已有数据卷不要通过改 .env 用户名来“重建管理员”。
- 更新前会校验业务连接与迁移连接必须指向同一个数据库。
- Redis 不映射到 NAS 局域网端口。
- 只暴露 Web/API 端口。
- .env 不提交 Git。
- STAGING 永远不用 PRODUCTION 数据库密码和 DATA_DIR。
- 生产数据至少保留 NAS 本机备份 + 第二份异地/异盘备份。
