#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/Users/gino/ecommerce-workspace}"
REMOTE="${SYSTEM_UPDATE_REMOTE:-origin}"
BRANCH="${SYSTEM_UPDATE_BRANCH:-main}"
LABEL_NAME="${SYSTEM_UPDATE_LAUNCH_LABEL:-com.gino.ecommerce-dashboard}"
LABEL="gui/$(id -u)/$LABEL_NAME"
VENV="$ROOT/backend/.venv"
SUCCESS=0
PREVIOUS_SHA=""
TARGET_SHA=""

log() {
  printf '[bootstrap-update] %s\n' "$*"
}

restart_service() {
  launchctl enable "$LABEL" >/dev/null 2>&1 || true
  launchctl kickstart -k "$LABEL" >/dev/null 2>&1 || true
}

rollback_code() {
  if [[ -n "$PREVIOUS_SHA" ]]; then
    log "恢复代码到 $PREVIOUS_SHA"
    ECOMMERCE_ALLOW_MANUAL_GIT=1 git -C "$ROOT" reset --hard "$PREVIOUS_SHA" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  if [[ "$SUCCESS" != "1" ]]; then
    log "引导更新未完成，尝试恢复原代码并重新启动原服务"
    rollback_code
    restart_service
  fi
}
trap cleanup EXIT

cd "$ROOT"
PREVIOUS_SHA="$(git rev-parse HEAD)"
log "当前版本：$PREVIOUS_SHA"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "工作区存在未提交修改，停止引导更新。" >&2
  exit 2
fi

log "临时停止 LaunchAgent，避免旧 API / scheduler 与本次更新并发"
launchctl disable "$LABEL" >/dev/null 2>&1 || true
launchctl kill SIGTERM "$LABEL" >/dev/null 2>&1 || true
sleep 2
bash "$ROOT/scripts/quiesce-update-workers.sh" >/dev/null 2>&1 || true

log "读取远端最新 main"
ECOMMERCE_ALLOW_MANUAL_GIT=1 git fetch --quiet "$REMOTE" "$BRANCH"
TARGET_SHA="$(git rev-parse FETCH_HEAD)"
log "目标版本：$TARGET_SHA"

if [[ "$PREVIOUS_SHA" == "$TARGET_SHA" ]]; then
  log "当前已经是远端最新版本"
  SUCCESS=1
  restart_service
  exit 0
fi

log "确认目标版本包含重复外键修复"
if ! ECOMMERCE_ALLOW_MANUAL_GIT=1 git show "$TARGET_SHA:backend/alembic/versions/drift20260920_align_supplier_nullable.py" | grep -q "WHEN duplicate_object"; then
  echo "目标版本不包含 finance FK 重复对象修复，停止更新。" >&2
  exit 3
fi

log "更新前执行数据库与业务文件备份"
bash "$ROOT/scripts/backup.sh"

log "切换代码到目标版本"
ECOMMERCE_ALLOW_MANUAL_GIT=1 git reset --hard "$TARGET_SHA"

ACTUAL_SHA="$(git rev-parse HEAD)"
if [[ "$ACTUAL_SHA" != "$TARGET_SHA" ]]; then
  echo "代码切换后 HEAD 与目标版本不一致，停止更新。" >&2
  exit 4
fi

log "校验本机实际加载的迁移脚本"
if ! grep -q "WHEN duplicate_object" "$ROOT/backend/alembic/versions/drift20260920_align_supplier_nullable.py"; then
  echo "本机迁移文件不是目标版本内容，停止更新。" >&2
  exit 5
fi

set -a
source "$ROOT/.env"
set +a
export DATABASE_URL="postgresql+psycopg://$POSTGRES_USER:$POSTGRES_PASSWORD@localhost:5432/$POSTGRES_DB"
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"
export DATA_DIR="${DATA_DIR:-$ROOT/data}"
export PATH="/Users/gino/.workbuddy/binaries/node/versions/22.22.2-2/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

log "执行 Alembic 迁移"
(
  cd "$ROOT/backend"
  "$VENV/bin/python" -m alembic upgrade head
)

log "重建前端"
(
  cd "$ROOT/frontend"
  if [[ -f package-lock.json ]]; then
    npm ci
  else
    npm install
  fi
  npm run build
)

if [[ -f "$ROOT/scripts/update-guard.sh" ]]; then
  log "安装系统更新 Git 保护"
  bash "$ROOT/scripts/update-guard.sh" install "$ROOT" >/dev/null
fi

log "重新启用并启动服务"
SUCCESS=1
restart_service
sleep 3

if ! curl --noproxy '*' -fsS http://127.0.0.1:8000/healthz >/dev/null; then
  echo "新版本已安装，但健康检查暂未通过，请查看 logs/api.log。" >&2
  exit 6
fi

log "引导更新完成：$TARGET_SHA"
