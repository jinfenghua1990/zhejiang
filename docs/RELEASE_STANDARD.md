# 平台发布标准

## 1. 一个代码源，多个环境

本项目只维护一套代码仓库，不为测试版和正式版复制两套代码。

标准发布链路：

```text
feature/* 或 fix/*
        ↓
      develop
        ↓
   STAGING 测试环境
        ↓
   Release Candidate
        ↓
       main
        ↓
 PRODUCTION 正式环境
```

- `develop`：集成分支，测试环境只跟踪该分支。
- `main`：生产分支，只有通过测试的版本可以进入。
- `feature/*`：功能开发。
- `fix/*`：普通缺陷修复。
- `hotfix/*`：生产紧急修复，可直接向 main 提 PR，但修复后必须同步回 develop。
- `codex/*`：AI/Codex 工作分支，完成后必须通过 PR 进入 develop，不能直接作为长期生产分支。

## 2. 测试版与正式版不是两套代码

正常情况下，同一提交先在 STAGING 验证，再晋级到 PRODUCTION。

版本测试通过后，不重新修改代码再发布正式版；如果测试中发现问题，应产生新的提交和新的候选版本重新测试。

## 3. 发布版本号

用户可见版本使用北京时间时间码：

```text
vYYYY.MM.DD.HHmmss
```

Git SHA 继续作为技术追溯标识。

数据库结构版本由 Alembic revision 独立管理。仅 UI 或业务代码变化时，不应制造无意义数据库迁移。

## 4. 发布门禁

进入 main 前必须满足：

1. CI 全绿；
2. Alembic migration check 通过；
3. 后端测试通过；
4. 前端 TypeScript 与生产构建通过；
5. 备份可生成且 restore-check 可恢复；
6. smoke test 通过；
7. 如有数据库迁移，必须先完成备份；
8. 发布说明明确新增、优化、修复、数据库变化与风险。

本机可执行：

```bash
make release-check
```

## 5. 发布与回滚

### 数据库连接职责

- API / worker / beat 使用 `DATABASE_URL`；
- Alembic / migrate 阶段优先使用 `MIGRATION_DATABASE_URL`；
- `MIGRATION_DATABASE_URL` 未配置时暂时兼容回退 `DATABASE_URL`，用于旧实例平滑迁移；
- 一旦配置独立迁移连接，它必须与业务连接指向同一个 host / port / database，仅用户名和权限边界不同；
- 生产环境完成角色拆分后，不再允许 API 容器拥有 DDL/owner 权限。

正式发布固定顺序：

```text
锁定业务写入
→ 暂停 worker/beat
→ PostgreSQL + DATA_DIR 备份
→ 校验备份
→ 切换目标版本
→ 独立 migrate 阶段执行 Alembic migration
→ 构建前端
→ 启动 API
→ 健康检查
→ 恢复 worker/beat
→ 解除维护模式
```

失败时优先恢复到上一个稳定版本。数据库发生不可逆变更时，以发布前备份作为最终灾备边界。

## 6. 环境隔离

STAGING 与 PRODUCTION 必须使用不同的：

- DATABASE_URL / 数据库；
- Redis；
- DATA_DIR；
- BACKUP_DIR；
- LOG_DIR；
- 外部系统测试凭证或沙盒账号（存在时）。

严禁测试环境连接生产数据库做写入测试。

## 7. 系统更新通道

- 正式实例：`SYSTEM_UPDATE_BRANCH=main`
- 测试实例：`SYSTEM_UPDATE_BRANCH=develop`

后台“系统更新”只允许跟踪预先配置的分支，不允许从 UI 临时切换任意 commit。

长期目标是进一步升级为“构建一次、同一不可变构建产物从 staging 晋级到 production”；在本机原生部署阶段，先用同一 Git commit + SHA 校验实现等价控制。
