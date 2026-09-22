#!/usr/bin/env bash
# 一次性把原生生产环境的持久化目录迁移到 Git 仓库之外。
# 安全原则：先停服务和备份，再复制 + 校验；只更新 .env，不删除任何旧目录。
set -euo pipefail
umask 077

ROOT="${PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
TS="$(date +%Y%m%d_%H%M%S)"
SUCCESS=0
ENV_UPDATED=0
SERVICE_WAS_LOADED=0
ENV_BACKUP=""

log() {
  printf '[persistence-migrate] %s\n' "$*"
}

fail() {
  printf '[persistence-migrate] ERROR: %s\n' "$*" >&2
  exit 2
}

if [[ ! -f "$ENV_FILE" ]]; then
  fail "未找到运行配置：$ENV_FILE"
fi

set -a
source "$ENV_FILE"
set +a

APP_ENV="${APP_ENV:-production}"
if [[ "$APP_ENV" != "production" && "${PERSIST_MIGRATION_ALLOW_NON_PROD:-0}" != "1" ]]; then
  fail "当前 APP_ENV=$APP_ENV；本命令默认只迁移 production。若明确需要迁移非生产环境，请设置 PERSIST_MIGRATION_ALLOW_NON_PROD=1"
fi

PYTHON_BIN="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
[[ -n "$PYTHON_BIN" ]] || fail "未找到可用 Python"

resolve_path() {
  "$PYTHON_BIN" - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

ROOT_ABS="$(resolve_path "$ROOT")"
DEFAULT_TARGET="$HOME/ecommerce-workspace-data/$APP_ENV"
TARGET_ROOT_RAW="${1:-${PERSIST_MIGRATION_TARGET:-${PERSIST_ROOT:-$DEFAULT_TARGET}}}"
TARGET_ROOT="$(resolve_path "$TARGET_ROOT_RAW")"

case "$TARGET_ROOT/" in
  "$ROOT_ABS/"*)
    fail "目标持久化目录不能位于代码目录内：$TARGET_ROOT"
    ;;
esac

CURRENT_PERSIST_ROOT="${PERSIST_ROOT:-$ROOT_ABS}"
SOURCE_DATA="$(resolve_path "${DATA_DIR:-$CURRENT_PERSIST_ROOT/data}")"
SOURCE_BACKUPS="$(resolve_path "${BACKUP_DIR:-$CURRENT_PERSIST_ROOT/backups}")"
SOURCE_LOGS="$(resolve_path "${LOG_DIR:-$CURRENT_PERSIST_ROOT/logs}")"
TARGET_DATA="$TARGET_ROOT/data"
TARGET_BACKUPS="$TARGET_ROOT/backups"
TARGET_LOGS="$TARGET_ROOT/logs"

if [[ "$SOURCE_DATA" == "$TARGET_DATA" && "$SOURCE_BACKUPS" == "$TARGET_BACKUPS" && "$SOURCE_LOGS" == "$TARGET_LOGS" && "${PERSIST_ROOT:-}" == "$TARGET_ROOT" ]]; then
  log "持久化目录已经完成外置，无需迁移：$TARGET_ROOT"
  exit 0
fi

command -v rsync >/dev/null 2>&1 || fail "缺少 rsync，无法执行可校验复制"

LABEL_NAME="${SYSTEM_UPDATE_LAUNCH_LABEL:-com.gino.ecommerce-dashboard}"
SERVICE_REF="gui/$(id -u)/$LABEL_NAME"
HEALTH_URL="${SYSTEM_UPDATE_HEALTH_URL:-http://127.0.0.1:8000/healthz}"

restart_service() {
  if [[ "$SERVICE_WAS_LOADED" != "1" ]]; then
    return
  fi
  launchctl enable "$SERVICE_REF" >/dev/null 2>&1 || true
  launchctl kickstart -k "$SERVICE_REF" >/dev/null 2>&1 || true
}

rollback() {
  if [[ "$SUCCESS" == "1" ]]; then
    return
  fi
  if [[ "$ENV_UPDATED" == "1" && -n "$ENV_BACKUP" && -f "$ENV_BACKUP" ]]; then
    log "迁移未完成，恢复原 .env"
    cp -p "$ENV_BACKUP" "$ENV_FILE" || true
  fi
  restart_service || true
}
trap rollback EXIT INT TERM

if command -v launchctl >/dev/null 2>&1 && launchctl print "$SERVICE_REF" >/dev/null 2>&1; then
  SERVICE_WAS_LOADED=1
  log "停止 LaunchAgent，冻结业务写入：$SERVICE_REF"
  launchctl disable "$SERVICE_REF" >/dev/null 2>&1 || true
  launchctl kill SIGTERM "$SERVICE_REF" >/dev/null 2>&1 || true
  sleep 2
  if [[ -f "$ROOT/scripts/quiesce-update-workers.sh" ]]; then
    bash "$ROOT/scripts/quiesce-update-workers.sh" >/dev/null 2>&1 || true
  fi
else
  log "未检测到已加载的 LaunchAgent；继续执行离线迁移，完成后请确认服务启动方式"
fi

log "先生成数据库 + 业务文件恢复点"
bash "$ROOT/scripts/backup.sh"

mkdir -p "$TARGET_DATA" "$TARGET_BACKUPS" "$TARGET_LOGS"

sync_dir() {
  local label="$1"
  local source="$2"
  local target="$3"
  if [[ ! -d "$source" ]]; then
    log "$label 源目录不存在，创建空目标：$target"
    mkdir -p "$target"
    return
  fi
  if [[ "$(resolve_path "$source")" == "$(resolve_path "$target")" ]]; then
    log "$label 已位于目标目录，跳过复制：$target"
    return
  fi
  log "复制 ${label}：${source} -> ${target}"
  rsync -a "$source/" "$target/"
  local verify
  verify="$(rsync -a --checksum --dry-run "$source/" "$target/")"
  if [[ -n "$verify" ]]; then
    printf '%s\n' "$verify" >&2
    fail "$label 复制校验失败"
  fi
  log "$label 复制校验通过"
}

sync_dir "DATA_DIR" "$SOURCE_DATA" "$TARGET_DATA"
sync_dir "BACKUP_DIR" "$SOURCE_BACKUPS" "$TARGET_BACKUPS"
sync_dir "LOG_DIR" "$SOURCE_LOGS" "$TARGET_LOGS"

ENV_BACKUP="$ENV_FILE.before-persistence-$TS"
cp -p "$ENV_FILE" "$ENV_BACKUP"
log "原运行配置已备份：$ENV_BACKUP"

"$PYTHON_BIN" - "$ENV_FILE" "$TARGET_ROOT" "$TARGET_DATA" "$TARGET_BACKUPS" "$TARGET_LOGS" <<'PY'
from __future__ import annotations

import os
import re
import shlex
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
updates = {
    "PERSIST_ROOT": sys.argv[2],
    "DATA_DIR": sys.argv[3],
    "BACKUP_DIR": sys.argv[4],
    "LOG_DIR": sys.argv[5],
}

lines = env_path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
out: list[str] = []
pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

for line in lines:
    match = pattern.match(line)
    key = match.group(1) if match else ""
    if key in updates:
        if key in seen:
            continue
        out.append(f"{key}={shlex.quote(updates[key])}")
        seen.add(key)
    else:
        out.append(line)

if out and out[-1] != "":
    out.append("")
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={shlex.quote(value)}")

tmp = env_path.with_name(env_path.name + ".persistence.tmp")
tmp.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
try:
    os.chmod(tmp, env_path.stat().st_mode & 0o777)
except OSError:
    os.chmod(tmp, 0o600)
os.replace(tmp, env_path)
PY
ENV_UPDATED=1

cat > "$TARGET_ROOT/migration-$TS.txt" <<EOF
timestamp=$TS
app_env=$APP_ENV
repo_root=$ROOT_ABS
source_data=$SOURCE_DATA
source_backups=$SOURCE_BACKUPS
source_logs=$SOURCE_LOGS
target_root=$TARGET_ROOT
target_data=$TARGET_DATA
target_backups=$TARGET_BACKUPS
target_logs=$TARGET_LOGS
old_env_backup=$ENV_BACKUP
old_directories_deleted=0
EOF

log "运行配置已切换到仓库外持久化目录"
log "PERSIST_ROOT=$TARGET_ROOT"
log "DATA_DIR=$TARGET_DATA"
log "BACKUP_DIR=$TARGET_BACKUPS"
log "LOG_DIR=$TARGET_LOGS"

if [[ "$SERVICE_WAS_LOADED" == "1" ]]; then
  log "重新启动服务"
  restart_service

  health_ok=0
  for _ in $(seq 1 45); do
    if curl --noproxy '*' -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      health_ok=1
      break
    fi
    sleep 2
  done
  if [[ "$health_ok" != "1" ]]; then
    fail "新持久化目录启用后健康检查失败：$HEALTH_URL"
  fi
  log "服务健康检查通过"
fi

SUCCESS=1
trap - EXIT INT TERM

log "迁移完成。旧 data/backups/logs 仍保留在原位置，没有自动删除。"
log "请回到系统更新页刷新环境自检；确认业务文件、订单、库存、财务页面正常后，再另行清理旧目录。"
