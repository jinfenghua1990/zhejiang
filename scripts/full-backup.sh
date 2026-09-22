#!/usr/bin/env bash
# 电商经营数据平台 — 完整容灾恢复点
# 组成：应用源码 + 运行配置 + PostgreSQL + data/ +（可用时）Docker 镜像。
# 所有文件本地计算 SHA256，full_<timestamp>.manifest 最后落盘作为完整恢复点提交标记。
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_BACKUP_DIR="${BACKUP_DIR:-}"
if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi
[[ -n "$RUNTIME_BACKUP_DIR" ]] && export BACKUP_DIR="$RUNTIME_BACKUP_DIR"

PERSIST_ROOT="${PERSIST_ROOT:-$ROOT}"
DATA_DIR="${DATA_DIR:-$PERSIST_ROOT/data}"
BACKUP_DIR="${BACKUP_DIR:-$PERSIST_ROOT/backups}"
FULL_KEEP="${FULL_KEEP:-12}"
RECOVERY_KEY_FILE="${BACKUP_RECOVERY_KEY_FILE:-$PERSIST_ROOT/keys/backup-recovery.key}"
mkdir -p "$BACKUP_DIR"

PYTHON_BIN="$ROOT/backend/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -c 'import cryptography' >/dev/null 2>&1; then
  echo "缺少可用 Python/cryptography，无法安全加密容灾配置包。" >&2
  exit 2
fi

resolve_path() {
  "$PYTHON_BIN" - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

KEY_ABS="$(resolve_path "$RECOVERY_KEY_FILE")"
DATA_ABS="$(resolve_path "$DATA_DIR")"
BACKUP_ABS="$(resolve_path "$BACKUP_DIR")"
case "$KEY_ABS/" in
  "$DATA_ABS/"*|"$BACKUP_ABS/"*)
    echo "BACKUP_RECOVERY_KEY_FILE 不能位于 data/ 或 backups/ 内，否则密钥会和密文一起进入备份。" >&2
    exit 2
    ;;
esac

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

echo "==> [1/5] 生成/复用基础数据恢复点 ..."
BASE_MANIFEST=""
LATEST_BASE="$(ls -1t "$BACKUP_DIR"/backup_*.manifest 2>/dev/null | head -1 || true)"
if [[ -n "$LATEST_BASE" ]]; then
  NOW="$(date +%s)"
  MTIME="$(stat -f %m "$LATEST_BASE" 2>/dev/null || stat -c %Y "$LATEST_BASE" 2>/dev/null || echo 0)"
  AGE=$(( NOW - MTIME ))
  if (( AGE <= 10800 )); then
    BASE_MANIFEST="$LATEST_BASE"
    echo "     复用 3 小时内基础恢复点：$(basename "$BASE_MANIFEST")"
  fi
fi
if [[ -z "$BASE_MANIFEST" ]]; then
  OUTPUT="$(KEEP=30 bash "$ROOT/scripts/backup.sh")"
  printf '%s\n' "$OUTPUT"
  BASE_MANIFEST="$(printf '%s\n' "$OUTPUT" | sed -n 's/^BACKUP_MANIFEST=//p' | tail -1)"
fi
if [[ -z "$BASE_MANIFEST" || ! -f "$BASE_MANIFEST" ]]; then
  echo "无法获得基础恢复点 manifest" >&2
  exit 2
fi

TS="$(manifest_value "$BASE_MANIFEST" timestamp)"
DB_NAME="$(manifest_value "$BASE_MANIFEST" db)"
DB_HASH="$(manifest_value "$BASE_MANIFEST" db_sha256)"
DATA_NAME="$(manifest_value "$BASE_MANIFEST" data)"
DATA_HASH="$(manifest_value "$BASE_MANIFEST" data_sha256)"
if [[ -z "$TS" || -z "$DB_NAME" || -z "$DB_HASH" ]]; then
  echo "基础恢复点 manifest 无效：$BASE_MANIFEST" >&2
  exit 2
fi

APP_FINAL="$BACKUP_DIR/app_$TS.tar.gz"
CONFIG_FINAL="$BACKUP_DIR/config_$TS.tar.gz.fernet"
IMAGE_FINAL="$BACKUP_DIR/docker_image_$TS.tar.gz"
FULL_FINAL="$BACKUP_DIR/full_$TS.manifest"
TMP_APP="$BACKUP_DIR/.app_$TS.tar.gz.tmp"
TMP_CONFIG_RAW="$BACKUP_DIR/.config_$TS.tar.gz.raw"
TMP_CONFIG="$BACKUP_DIR/.config_$TS.tar.gz.fernet.tmp"
TMP_CONFIG_VERIFY="$BACKUP_DIR/.config_$TS.verify.tar.gz"
TMP_IMAGE="$BACKUP_DIR/.docker_image_$TS.tar.gz.tmp"
TMP_FULL="$BACKUP_DIR/.full_$TS.manifest.tmp"
COMMITTED=0

cleanup() {
  rm -f "$TMP_APP" "$TMP_CONFIG_RAW" "$TMP_CONFIG" "$TMP_CONFIG_VERIFY" "$TMP_IMAGE" "$TMP_FULL"
  if [[ "$COMMITTED" != "1" ]]; then
    rm -f "$APP_FINAL" "$CONFIG_FINAL" "$IMAGE_FINAL" "$FULL_FINAL"
  fi
}
trap cleanup EXIT INT TERM

echo "==> [2/5] 归档应用程序 ..."
if ! command -v git >/dev/null 2>&1 || ! git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "完整容灾备份要求项目目录是 Git 工作树，用于生成不含 backups/data/node_modules/.venv 的源码归档。" >&2
  exit 2
fi
GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
git -C "$ROOT" archive --format=tar.gz --prefix="ecommerce-workspace/" HEAD > "$TMP_APP"
tar -tzf "$TMP_APP" >/dev/null
[[ -s "$TMP_APP" ]] || { echo "应用归档为空" >&2; exit 2; }

echo "==> [3/5] 归档并加密运行配置 ..."
CONFIG_FILES=()
for candidate in   ".env"   ".env.production"   "compose.yaml"   "compose.yml"   "docker-compose.override.yml"   "docker-compose.override.yaml"
do
  [[ -f "$ROOT/$candidate" ]] && CONFIG_FILES+=("$candidate")
done
while IFS= read -r file; do
  rel="${file#"$ROOT/"}"
  duplicate=0
  for existing in "${CONFIG_FILES[@]:-}"; do
    [[ "$existing" == "$rel" ]] && duplicate=1 && break
  done
  [[ "$duplicate" == "0" ]] && CONFIG_FILES+=("$rel")
done < <(
  find "$ROOT/deploy" -maxdepth 5 -type f \
    \( -name '.env' -o -name '.env.*' -o -name 'compose*.yml' -o -name 'compose*.yaml' \) \
    2>/dev/null | sort || true
)

if (( ${#CONFIG_FILES[@]} > 0 )); then
  tar -czf "$TMP_CONFIG_RAW" -C "$ROOT" "${CONFIG_FILES[@]}"
  tar -tzf "$TMP_CONFIG_RAW" >/dev/null
else
  TMP_NOTE="$BACKUP_DIR/.config_note_$TS.txt"
  printf '%s\n' "No runtime .env / compose files existed when this recovery point was created." > "$TMP_NOTE"
  tar -czf "$TMP_CONFIG_RAW" -C "$BACKUP_DIR" "$(basename "$TMP_NOTE")"
  rm -f "$TMP_NOTE"
fi

KEY_CREATED=0
[[ -f "$KEY_ABS" ]] || KEY_CREATED=1
CRYPTO_OUTPUT="$("$PYTHON_BIN" "$ROOT/scripts/backup-config-crypto.py" encrypt \
  --key-file "$KEY_ABS" \
  --input "$TMP_CONFIG_RAW" \
  --output "$TMP_CONFIG" \
  --create-key)"
KEY_FINGERPRINT="$(printf '%s\n' "$CRYPTO_OUTPUT" | sed -n 's/^KEY_FINGERPRINT=//p' | tail -1)"
[[ -n "$KEY_FINGERPRINT" && -s "$TMP_CONFIG" ]] || {
  echo "运行配置加密失败，拒绝提交容灾恢复点。" >&2
  exit 2
}

# 提交前立即做一次本地解密 + tar 完整性校验，避免生成无法恢复的密文包。
"$PYTHON_BIN" "$ROOT/scripts/backup-config-crypto.py" decrypt \
  --key-file "$KEY_ABS" \
  --input "$TMP_CONFIG" \
  --output "$TMP_CONFIG_VERIFY" >/dev/null
tar -tzf "$TMP_CONFIG_VERIFY" >/dev/null
rm -f "$TMP_CONFIG_RAW" "$TMP_CONFIG_VERIFY"

if [[ "$KEY_CREATED" == "1" ]]; then
  echo "     已生成独立恢复密钥：$KEY_ABS"
  echo "     请把该密钥复制到独立安全位置；R2/Kodo 不会备份此密钥。"
else
  echo "     使用现有独立恢复密钥：$KEY_ABS"
fi
echo "     恢复密钥指纹：$KEY_FINGERPRINT"

DOCKER_NAME=""
DOCKER_HASH=""
INCLUDE_IMAGE="${FULL_BACKUP_INCLUDE_DOCKER_IMAGE:-auto}"
IMAGE_REF="${APP_IMAGE_REF:-}"
if [[ "$INCLUDE_IMAGE" != "0" && -n "$IMAGE_REF" ]] && command -v docker >/dev/null 2>&1 && docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  echo "==> [4/5] 归档 Docker 镜像：$IMAGE_REF ..."
  docker save "$IMAGE_REF" | gzip -c > "$TMP_IMAGE"
  gzip -t "$TMP_IMAGE"
  [[ -s "$TMP_IMAGE" ]] || { echo "Docker 镜像归档为空" >&2; exit 2; }
  DOCKER_NAME="$(basename "$IMAGE_FINAL")"
  DOCKER_HASH="$(sha256_file "$TMP_IMAGE")"
else
  echo "==> [4/5] 当前无可归档 Docker 镜像，源码 + 锁定依赖作为软件恢复源"
fi

APP_HASH="$(sha256_file "$TMP_APP")"
CONFIG_HASH="$(sha256_file "$TMP_CONFIG")"
BASE_HASH="$(sha256_file "$BASE_MANIFEST")"

cat > "$TMP_FULL" <<EOF
version=2
kind=full
timestamp=$TS
git_sha=$GIT_SHA
base_manifest=$(basename "$BASE_MANIFEST")
base_manifest_sha256=$BASE_HASH
db=$DB_NAME
db_sha256=$DB_HASH
data=$DATA_NAME
data_sha256=$DATA_HASH
app=$(basename "$APP_FINAL")
app_sha256=$APP_HASH
config=$(basename "$CONFIG_FINAL")
config_sha256=$CONFIG_HASH
config_encryption=fernet
config_key_fingerprint=$KEY_FINGERPRINT
config_key_external_required=1
docker_image=$DOCKER_NAME
docker_image_sha256=$DOCKER_HASH
EOF

echo "==> [5/5] 提交完整容灾恢复点 ..."
mv "$TMP_APP" "$APP_FINAL"
mv "$TMP_CONFIG" "$CONFIG_FINAL"
if [[ -n "$DOCKER_NAME" ]]; then
  mv "$TMP_IMAGE" "$IMAGE_FINAL"
fi
mv "$TMP_FULL" "$FULL_FINAL"
COMMITTED=1

count=0
while IFS= read -r old_full; do
  count=$((count + 1))
  if (( count > FULL_KEEP )); then
    old_app="$(manifest_value "$old_full" app)"
    old_config="$(manifest_value "$old_full" config)"
    old_image="$(manifest_value "$old_full" docker_image)"
    [[ -n "$old_app" && "$old_app" != */* ]] && rm -f "$BACKUP_DIR/$old_app"
    [[ -n "$old_config" && "$old_config" != */* ]] && rm -f "$BACKUP_DIR/$old_config"
    [[ -n "$old_image" && "$old_image" != */* ]] && rm -f "$BACKUP_DIR/$old_image"
    rm -f "$old_full"
  fi
done < <(ls -1t "$BACKUP_DIR"/full_*.manifest 2>/dev/null || true)

FULL_ABS="$(cd "$BACKUP_DIR" && pwd)/$(basename "$FULL_FINAL")"
echo "==> 完成。完整容灾恢复点：$FULL_ABS"
echo "==> 配置包已客户端加密；恢复时需要独立密钥（指纹 ${KEY_FINGERPRINT}）。"
echo "FULL_BACKUP_MANIFEST=$FULL_ABS"
