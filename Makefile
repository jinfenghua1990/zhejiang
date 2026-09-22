# 电商工作平台 — 原生 macOS 运维入口
# 用法: make <target>

ROOT := $(CURDIR)
BACKEND := $(ROOT)/backend
VENV := $(BACKEND)/.venv
SYSTEM_UPDATE_LAUNCH_LABEL ?= com.gino.ecommerce-dashboard
LAUNCH_LABEL := gui/$(shell id -u)/$(SYSTEM_UPDATE_LAUNCH_LABEL)
# Native targets must not inherit the container-only `postgres` hostname. Set
# NATIVE_DATABASE_URL explicitly when a non-local native database is intended.
NATIVE_ENV = set -a; . "$(ROOT)/.env"; set +a; export DATABASE_URL="$${NATIVE_DATABASE_URL:-postgresql+psycopg://$${POSTGRES_USER}:$${POSTGRES_PASSWORD}@localhost:5432/$${POSTGRES_DB}}"; export REDIS_URL="$${REDIS_URL:-redis://localhost:6379/0}"; export DATA_DIR="$${DATA_DIR:-$(ROOT)/data}";
MIGRATION_ENV = $(NATIVE_ENV) if [ -n "$$MIGRATION_DATABASE_URL" ]; then export DATABASE_URL="$$MIGRATION_DATABASE_URL"; fi;

.PHONY: help update-guard-check update-guard-install up restart status logs logs-api rebuild rebuild-fe test test-db lint tsc verify release-check secret-scan repo-hygiene smoke migrate migration-check exec-api persistence-migrate backup backup-full backup-r2 backup-r2-daily backup-r2-full cold-backup-kodo backup-webdav restore-check orphan-audit backup-schedule-install backup-schedule-status backup-schedule-uninstall fresh

help: ## 列出所有 target
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-16s\033[0m %s\n", $1, $2}' $(MAKEFILE_LIST)

update-guard-check: ## 检查系统更新全局锁；更新中拒绝人工修改运行目录
	@bash "$(ROOT)/scripts/update-guard.sh" check "$(ROOT)"

update-guard-install: ## 安装 Git 更新保护 hook
	@bash "$(ROOT)/scripts/update-guard.sh" install "$(ROOT)"

up: restart ## 启动或重新加载本地服务

restart: update-guard-check ## 重新加载 API、worker 与 beat
	launchctl kickstart -k "$(LAUNCH_LABEL)"

status: ## 显示本机健康状态
	curl --noproxy '*' -fsS http://127.0.0.1:8000/healthz; echo
	launchctl print "$(LAUNCH_LABEL)" | sed -n '1,45p'

logs: ## 跟踪 API、worker、beat 日志
	tail -n 100 -f /tmp/ecom_api.log /tmp/ecom_worker.log /tmp/ecom_beat.log

logs-api: ## 跟踪 API 日志
	tail -n 100 -f /tmp/ecom_api.log

rebuild: restart ## 后端代码已直接由原生虚拟环境加载，重启即可

rebuild-fe: update-guard-check ## 重建静态前端并重启服务
	cd frontend && npm run build
	$(MAKE) restart

test: ## 在本机虚拟环境运行后端测试（默认连测试库，PYTEST_USE_TEST_DB=0 直连业务库）
	$(NATIVE_ENV) cd "$(BACKEND)" && "$(VENV)/bin/python" -m pytest app/tests -q --tb=line -p no:cacheprovider

test-db: ## 重建并迁移 pytest 专用测试库（<POSTGRES_DB>_test，conftest 默认连它）
	@set -a; . "$(ROOT)/.env"; set +a; export PGPASSWORD="$${POSTGRES_PASSWORD}"; \
	psql -h localhost -U "$${POSTGRES_USER}" -d postgres -q -c "DROP DATABASE IF EXISTS $${POSTGRES_DB}_test;" && \
	psql -h localhost -U "$${POSTGRES_USER}" -d postgres -q -c "CREATE DATABASE $${POSTGRES_DB}_test OWNER $${POSTGRES_USER};" && \
	psql -h localhost -U "$${POSTGRES_USER}" -d "$${POSTGRES_DB}_test" -q -c "ALTER SCHEMA public OWNER TO $${POSTGRES_USER};" && \
	TEST_URL="$${TEST_DATABASE_URL:-}"; \
	if [[ -z "$$TEST_URL" ]]; then echo "缺少 TEST_DATABASE_URL，请在项目根目录 .env 中配置"; exit 2; fi; \
	cd "$(BACKEND)" && DATABASE_URL="$$TEST_URL" "$(VENV)/bin/alembic" upgrade head >/dev/null && echo "测试库 $${POSTGRES_DB}_test 已重建并迁移到 head"

lint: ## 编译检查后端 Python 文件
	cd "$(BACKEND)" && "$(VENV)/bin/python" -m compileall -q app

tsc: ## 前端类型检查
	cd frontend && npx tsc --noEmit

repo-hygiene: ## 禁止 Git 跟踪手工 .bak 源码备份
	bash ./scripts/repo-hygiene.sh

secret-scan: ## 扫描 Git 已跟踪文件中的高置信度 token / 私钥
	bash ./scripts/secret-scan.sh

verify: update-guard-check repo-hygiene secret-scan migration-check orphan-audit lint test tsc ## 本地一键验收：仓库卫生 + 敏感信息 + 真实库引用 + 迁移 + 后端 + 前端静态构建
	cd frontend && npm run build
	@test -f frontend/out/index.html
	@echo "本地验收通过：repo hygiene / secret scan / migration / orphan audit / backend tests / TypeScript / static build 均正常。"

release-check: verify restore-check smoke ## 发布前门禁：完整回归 + 恢复演练 + 运行态 smoke
	@echo "发布门禁通过：该提交可以进入 develop → main 发布流程。"

smoke: ## 枚举公开 API 并做带鉴权 smoke test
	./scripts/smoke.sh

migrate: update-guard-check ## 应用 Alembic 迁移（优先使用 MIGRATION_DATABASE_URL）
	$(MIGRATION_ENV) cd "$(BACKEND)" && "$(VENV)/bin/alembic" upgrade head

migration-check: ## 检查 models 与迁移是否漂移（优先使用 MIGRATION_DATABASE_URL）
	$(MIGRATION_ENV) cd "$(BACKEND)" && "$(VENV)/bin/alembic" check

exec-api: ## 进入后端原生虚拟环境 shell
	$(NATIVE_ENV) cd "$(BACKEND)" && exec "$(SHELL)"

persistence-migrate: ## 一次性把 production 的 data/backups/logs 安全迁移到仓库外并更新 .env
	bash ./scripts/migrate-persistence.sh

backup: update-guard-check ## 备份 PostgreSQL 与 data/ 原始归档
	./scripts/backup.sh

backup-full: update-guard-check ## 生成完整容灾恢复点：应用 + 配置 + PostgreSQL + data/ + 可用 Docker 镜像
	bash ./scripts/full-backup.sh

backup-r2: update-guard-check ## R2 自动策略：每日模块化；到期自动执行全量容灾（默认每 10 天）
	@$(NATIVE_ENV) "$(VENV)/bin/python" "$(ROOT)/scripts/r2-backup.py" --mode auto

backup-r2-daily: update-guard-check ## 立即执行一次 R2 模块化备份
	@$(NATIVE_ENV) "$(VENV)/bin/python" "$(ROOT)/scripts/r2-backup.py" --mode daily

backup-r2-full: update-guard-check ## 立即执行一次 R2 完整容灾备份
	@$(NATIVE_ENV) "$(VENV)/bin/python" "$(ROOT)/scripts/r2-backup.py" --mode full

cold-backup-kodo: update-guard-check ## 生成并上传每日完整容灾恢复点到 Kodo；严格只写入，不下载/取回/远端校验
	@$(NATIVE_ENV) "$(VENV)/bin/python" "$(ROOT)/scripts/kodo-cold-upload.py"

backup-webdav: update-guard-check ## 生成并上传每日完整容灾恢复点到坚果云 WebDAV
	@$(NATIVE_ENV) "$(VENV)/bin/python" "$(ROOT)/scripts/webdav-backup.py"

restore-check: ## 将最新备份恢复到临时库验证，生产库不做任何修改
	bash ./scripts/restore-check.sh

orphan-audit: ## 只读检查关键业务表孤儿引用，补 FK/约束前必须为 0
	bash ./scripts/orphan-audit.sh

backup-schedule-install: ## 安装 macOS 每日备份 + 每周恢复演练 launchd 计划
	bash ./scripts/backup-schedule.sh install

backup-schedule-status: ## 查看 macOS 自动备份计划状态
	bash ./scripts/backup-schedule.sh status

backup-schedule-uninstall: ## 卸载 macOS 自动备份计划
	bash ./scripts/backup-schedule.sh uninstall

fresh: ## 拒绝自动清空真实业务数据
	@echo "拒绝执行：fresh 会销毁真实数据；如确有需要，请先单独确认目标与备份。"
	@exit 2
