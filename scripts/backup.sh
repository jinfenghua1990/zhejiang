#!/usr/bin/env bash
# 电商经营数据平台 — 本地基础备份
# 备份对象：PostgreSQL（pg_dump 逻辑备份）+ /data 归档文件（tar.gz）。
# 每次成功备份会最后写入 backup_<timestamp>.manifest，manifest 是“完整恢复点”的提交标记。
# 数据库/文件任一环节失败时，不会留下可被恢复程序误认成完整恢复点的半套备份。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_BACKUP_DIR="${BACKUP_DIR:-}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP="${KEEP:-30}"
TS="$(date +%Y%m%d_%H%M%S)"
SKIP_FILES=0
[[ "${1:-}" == "--no-files" ]] && SKIP_FILES=1

RUNTIME_DATABASE_URL="${DATABASE_URL:-}"
RUNTIME_DATA_DIR="${DATA_DIR:-}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

[[ -n "$RUNTIME_DATABASE_URL" ]] && export DATABASE_URL="$RUNTIME_DATABASE_URL"
[[ -n "$RUNTIME_DATA_DIR" ]] && export DATA_DIR="$RUNTIME_DATA_DIR"
[[ -n "$RUNTIME_BACKUP_DIR" ]] && export BACKUP_DIR="$RUNTIME_BACKUP_DIR"

PERSIST_ROOT="${PERSIST_ROOT:-$ROOT}"
DATA_DIR="${DATA_DIR:-$PERSIST_ROOT/data}"
BACKUP_DIR="${BACKUP_DIR:-$PERSIST_ROOT/backups}"

mkdir -p "$BACKUP_DIR"

DB_FINAL="$BACKUP_DIR/db_$TS.dump"
DATA_FINAL="$BACKUP_DIR/data_$TS.tar.gz"
MANIFEST_FINAL="$BACKUP_DIR/backup_$TS.manifest"
TMP_DUMP="$BACKUP_DIR/.db_$TS.dump.tmp"
TMP_DATA="$BACKUP_DIR/.data_$TS.tar.gz.tmp"
TMP_MANIFEST="$BACKUP_DIR/.backup_$TS.manifest.tmp"
HAS_DATA=0
COMMITTED=0

cleanup() {
  rm -f "$TMP_DUMP" "$TMP_DATA" "$TMP_MANIFEST"
  if [[ "$COMMITTED" != "1" ]]; then
    rm -f "$DB_FINAL" "$DATA_FINAL" "$MANIFEST_FINAL"
  fi
}
trap cleanup EXIT INT TERM

echo "==> 备份到 $BACKUP_DIR (保留 $KEEP 个完整恢复点) @ $TS"

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

check_local_pg_client() {
  command -v pg_dump >/dev/null 2>&1 || {
    echo "缺少 pg_dump，无法执行数据库备份。"
    exit 2
  }
  command -v psql >/dev/null 2>&1 || {
    echo "缺少 psql，无法核对 PostgreSQL 客户端/服务器版本。"
    exit 2
  }

  local host="${POSTGRES_HOST:-localhost}"
  local port="${POSTGRES_PORT:-5432}"
  local user="${POSTGRES_USER:-ecommerce}"
  local db="${POSTGRES_DB:-ecommerce}"
  local server_num client_major server_major

  server_num="$(PGPASSWORD="${POSTGRES_PASSWORD:-}" psql \
    -h "$host" -p "$port" -U "$user" -d "$db" -Atc "SHOW server_version_num" 2>/dev/null || true)"
  client_major="$(pg_dump --version | awk '{print $3}' | cut -d. -f1)"

  if [[ "$server_num" =~ ^[0-9]+$ && "$client_major" =~ ^[0-9]+$ ]]; then
    server_major=$((server_num / 10000))
    if (( client_major < server_major )); then
      echo "PostgreSQL 客户端版本过旧：服务器=$server_major，pg_dump=$client_major。"
      echo "请先安装/切换到 PostgreSQL $server_major 对应客户端，再执行备份。"
      exit 2
    fi
  fi
}

echo "==> [1/3] pg_dump ..."
COMPOSE_POSTGRES_RUNNING=0
if command -v docker >/dev/null 2>&1 \
  && docker compose -f "$ROOT/docker-compose.yml" ps --status running postgres 2>/dev/null | grep -q postgres; then
  COMPOSE_POSTGRES_RUNNING=1
fi

DB_HINT="${DATABASE_URL:-}"
if [[ "$COMPOSE_POSTGRES_RUNNING" == "1" && ( -z "$DB_HINT" || "$DB_HINT" == *"@postgres:"* || "$DB_HINT" == *"@postgres/"* ) ]]; then
  docker compose -f "$ROOT/docker-compose.yml" exec -T postgres \
    pg_dump -U "${POSTGRES_USER:-ecommerce}" -Fc "${POSTGRES_DB:-ecommerce}" \
    > "$TMP_DUMP"
else
  check_local_pg_client
  PGPASSWORD="${POSTGRES_PASSWORD:-}" pg_dump \
    -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" \
    -U "${POSTGRES_USER:-ecommerce}" -Fc "${POSTGRES_DB:-ecommerce}" \
    > "$TMP_DUMP"
fi

if [[ ! -s "$TMP_DUMP" ]]; then
  echo "数据库备份为空，拒绝提交恢复点。"
  exit 1
fi

echo "==> [2/3] 归档业务文件 ..."
if [[ "$SKIP_FILES" == "0" && -d "$DATA_DIR" ]]; then
  DATA_PARENT="$(cd "$(dirname "$DATA_DIR")" && pwd)"
  DATA_NAME="$(basename "$DATA_DIR")"
  tar --exclude="$DATA_NAME/system-update" -czf "$TMP_DATA" -C "$DATA_PARENT" "$DATA_NAME"
  tar -tzf "$TMP_DATA" >/dev/null
  if [[ ! -s "$TMP_DATA" ]]; then
    echo "data 归档为空，拒绝提交恢复点。"
    exit 1
  fi
  HAS_DATA=1
elif [[ "$SKIP_FILES" == "1" ]]; then
  echo "     (--no-files：仅数据库恢复点)"
else
  echo "     (无 $DATA_DIR，本次恢复点不含 data)"
fi

DB_HASH="$(sha256_file "$TMP_DUMP")"
DATA_HASH=""
DATA_BASENAME=""
if [[ "$HAS_DATA" == "1" ]]; then
  DATA_HASH="$(sha256_file "$TMP_DATA")"
  DATA_BASENAME="$(basename "$DATA_FINAL")"
fi

cat > "$TMP_MANIFEST" <<EOF
version=1
timestamp=$TS
db=$(basename "$DB_FINAL")
db_sha256=$DB_HASH
data=$DATA_BASENAME
data_sha256=$DATA_HASH
EOF

echo "==> [3/3] 提交完整恢复点 ..."
mv "$TMP_DUMP" "$DB_FINAL"
if [[ "$HAS_DATA" == "1" ]]; then
  mv "$TMP_DATA" "$DATA_FINAL"
fi
# manifest 最后落盘；只有存在 manifest 的备份才视为完整恢复点。
mv "$TMP_MANIFEST" "$MANIFEST_FINAL"
COMMITTED=1

# 按完整恢复点清理，避免数据库与 data 分别滚动导致时间点错配。
count=0
while IFS= read -r old_manifest; do
  count=$((count + 1))
  if (( count > KEEP )); then
    old_db="$(manifest_value "$old_manifest" db)"
    old_data="$(manifest_value "$old_manifest" data)"
    if [[ -n "$old_db" && "$old_db" != */* ]]; then
      rm -f "$BACKUP_DIR/$old_db"
    fi
    if [[ -n "$old_data" && "$old_data" != */* ]]; then
      rm -f "$BACKUP_DIR/$old_data"
    fi
    rm -f "$old_manifest"
  fi
done < <(ls -1t "$BACKUP_DIR"/backup_*.manifest 2>/dev/null || true)

MANIFEST_ABS="$(cd "$BACKUP_DIR" && pwd)/$(basename "$MANIFEST_FINAL")"
echo "==> 完成。恢复点：$MANIFEST_ABS"
echo "BACKUP_MANIFEST=$MANIFEST_ABS"
ls -1t "$BACKUP_DIR" | head -30
