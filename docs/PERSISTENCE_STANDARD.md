# 持久化与数据分离标准

## 原则

应用代码是可替换的，业务数据是持久化的。更新、回滚、重新克隆仓库都不能依赖代码目录中的业务数据才能恢复。

持久化对象包括：

- PostgreSQL：核心业务数据；
- DATA_DIR：发票、物流账单、Excel、商品附件、导入原件、系统更新状态等；
- Redis：缓存与任务状态；重要业务真相不能只存在 Redis；
- BACKUP_DIR：数据库与文件备份；
- LOG_DIR：运行日志。

## 推荐目录

原生部署建议：

```text
/Users/<user>/ecommerce-workspace/          # 代码，可替换
/Users/<user>/ecommerce-workspace-data/
  production/
    data/
    backups/
    logs/
  staging/
    data/
    backups/
    logs/
```

正式环境与测试环境不得共用 data、backup、log，也不得共用数据库。

## Production 硬门禁

当 `APP_ENV=production` 时：

- `PERSIST_ROOT` 必须显式配置；
- `PERSIST_ROOT`、`DATA_DIR`、`BACKUP_DIR`、`LOG_DIR` 均不得位于 Git 代码目录内；
- `DATABASE_URL` 必须指向独立 PostgreSQL，而不是 SQLite / 代码目录内文件数据库；
- 更新中心环境自检发现上述任一问题时，必须阻止系统升级；
- detached update runner 在真正切换 Git 版本前再次执行持久化目录校验，不能只依赖 UI/API 门禁。

因此，正常目标状态是：整个 `ecommerce-workspace` 代码目录都可以删除并重新 clone，只要 PostgreSQL、持久化目录与部署密钥仍在，业务数据就不丢失。

## 兼容迁移

运行层仍保留旧目录回退逻辑，便于历史实例读取和迁移；但 production 在完成数据外置前不得继续执行系统升级。自动更新不得自行搬迁真实业务数据。

原生 Mac 旧部署使用一次性迁移命令：

```bash
make persistence-migrate
```

默认目标为 `~/ecommerce-workspace-data/production`，也可以显式指定：

```bash
PERSIST_MIGRATION_TARGET=/path/outside/repo make persistence-migrate
```

迁移脚本会：

1. 停止 LaunchAgent / API / worker / beat，冻结业务写入；
2. 先生成数据库与 data 恢复点；
3. 将旧 data / backups / logs 复制到新的持久化目录；
4. 使用 rsync checksum dry-run 校验复制结果；
5. 原子更新 .env 中的 PERSIST_ROOT / DATA_DIR / BACKUP_DIR / LOG_DIR；
6. 重启服务并执行健康检查；
7. 失败时恢复原 .env 并重新启动旧配置；
8. **不会自动删除任何旧目录**。

完成后应回到“系统更新 → 环境自检”确认“程序 / 数据分离”为正常，并人工核对文件、订单、库存、财务页面。旧目录只在业务核对完成后另行清理。
