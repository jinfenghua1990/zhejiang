#!/usr/bin/env bash
# 电商经营数据平台 — 非破坏性备份恢复演练
# 优先按最新 manifest 恢复同一时间点的数据库 + data；兼容旧版无 manifest 备份。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_BACKUP_DIR="${BACKUP_DIR:-}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

[[ -n "$RUNTIME_BACKUP_DIR" ]] && export BACKUP_DIR="$RUNTIME_BACKUP_DIR"

PERSIST_ROOT="${PERSIST_ROOT:-$ROOT}"
BACKUP_DIR="${BACKUP_DIR:-$PERSIST_ROOT/backups}"

for cmd in pg_restore createdb dropdb psql; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "缺少命令：$cmd"
    exit 2
  }
done

sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    shasum -a 256 "$file" | awk '{print $1}'
  fi
}

manifest_value() {
  local file="$1"
  local key="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2- || true
}

LATEST_DUMP=""
LATEST_DATA=""
LATEST_MANIFEST="$(ls -1t "$BACKUP_DIR"/backup_*.manifest 2>/dev/null | head -1 || true)"

if [[ -n "$LATEST_MANIFEST" ]]; then
  DB_NAME="$(manifest_value "$LATEST_MANIFEST" db)"
  DB_HASH="$(manifest_value "$LATEST_MANIFEST" db_sha256)"
  DATA_NAME="$(manifest_value "$LATEST_MANIFEST" data)"
  DATA_HASH="$(manifest_value "$LATEST_MANIFEST" data_sha256)"

  if [[ -z "$DB_NAME" || "$DB_NAME" == */* || "$DB_NAME" != db_*.dump ]]; then
    echo "恢复点 manifest 中数据库文件名无效：$LATEST_MANIFEST"
    exit 1
  fi
  LATEST_DUMP="$BACKUP_DIR/$DB_NAME"
  if [[ ! -s "$LATEST_DUMP" ]]; then
    echo "恢复点缺少有效数据库备份：$LATEST_DUMP"
    exit 1
  fi
  if [[ -z "$DB_HASH" || "$(sha256_file "$LATEST_DUMP")" != "$DB_HASH" ]]; then
    echo "数据库备份 SHA256 校验失败：$LATEST_DUMP"
    exit 1
  fi

  if [[ -n "$DATA_NAME" ]]; then
    if [[ "$DATA_NAME" == */* || "$DATA_NAME" != data_*.tar.gz ]]; then
      echo "恢复点 manifest 中 data 文件名无效：$LATEST_MANIFEST"
      exit 1
    fi
    LATEST_DATA="$BACKUP_DIR/$DATA_NAME"
    if [[ ! -s "$LATEST_DATA" ]]; then
      echo "恢复点缺少有效 data 归档：$LATEST_DATA"
      exit 1
    fi
    if [[ -z "$DATA_HASH" || "$(sha256_file "$LATEST_DATA")" != "$DATA_HASH" ]]; then
      echo "data 归档 SHA256 校验失败：$LATEST_DATA"
      exit 1
    fi
  fi
  echo "==> 使用完整恢复点：$LATEST_MANIFEST"
else
  LATEST_DUMP="$(ls -1t "$BACKUP_DIR"/db_*.dump 2>/dev/null | head -1 || true)"
  LATEST_DATA="$(ls -1t "$BACKUP_DIR"/data_*.tar.gz 2>/dev/null | head -1 || true)"
  if [[ -z "$LATEST_DUMP" ]]; then
    echo "未找到数据库备份：$BACKUP_DIR/db_*.dump"
    exit 2
  fi
  echo "==> 未发现 manifest，按旧版备份格式执行兼容恢复演练"
fi

HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"
USER="${POSTGRES_USER:-ecommerce}"
PROD_DB="${POSTGRES_DB:-ecommerce}"
STAMP="$(date +%Y%m%d_%H%M%S)_$$"
CHECK_DB="${PROD_DB}_restorecheck_${STAMP}"
export PGPASSWORD="${POSTGRES_PASSWORD:-}"

cleanup() {
  dropdb -h "$HOST" -p "$PORT" -U "$USER" --if-exists "$CHECK_DB" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "==> 使用数据库备份：$LATEST_DUMP"
echo "==> 创建临时数据库：$CHECK_DB"
createdb -h "$HOST" -p "$PORT" -U "$USER" "$CHECK_DB"

pg_restore \
  -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECK_DB" \
  --no-owner --no-privileges --exit-on-error \
  "$LATEST_DUMP"

TABLE_COUNT="$(psql -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECK_DB" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")"
if [[ -z "$TABLE_COUNT" || "$TABLE_COUNT" -le 0 ]]; then
  echo "恢复失败：临时数据库没有业务表"
  exit 1
fi

echo "==> 恢复成功，public 表数量：$TABLE_COUNT"

ALEMBIC=""
if [[ -x "$ROOT/backend/.venv/bin/alembic" ]]; then
  ALEMBIC="$ROOT/backend/.venv/bin/alembic"
elif command -v alembic >/dev/null 2>&1; then
  ALEMBIC="$(command -v alembic)"
fi

if [[ -n "$ALEMBIC" ]]; then
  export DATABASE_URL="postgresql+psycopg://${USER}:${POSTGRES_PASSWORD:-}@${HOST}:${PORT}/${CHECK_DB}"
  (
    cd "$ROOT/backend"
    "$ALEMBIC" upgrade head
    "$ALEMBIC" current
  )
  echo "==> 当前代码迁移兼容性：通过"
else
  echo "缺少 alembic，无法验证恢复库与当前代码的迁移兼容性"
  exit 2
fi

if [[ -n "$LATEST_DATA" ]]; then
  tar -tzf "$LATEST_DATA" >/dev/null
  echo "==> data 归档完整性：通过 ($(basename "$LATEST_DATA"))"
fi

echo "==> 恢复演练通过；临时数据库将在退出时自动删除"
