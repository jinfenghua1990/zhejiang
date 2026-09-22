#!/usr/bin/env bash
# 仓库卫生检查：版本恢复交给 Git，源码目录禁止继续堆积手工 .bak 文件。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BACKUPS="$(git ls-files | grep -E '(^|/)[^/]+\.bak($|[-.])|\.bak-[0-9]' || true)"
if [[ -n "$BACKUPS" ]]; then
  echo "发现 Git 已跟踪的手工备份文件，禁止提交："
  printf '%s\n' "$BACKUPS"
  echo "请删除这些 .bak 文件；历史版本应通过 Git commit/branch 恢复。"
  exit 1
fi

echo "仓库卫生检查通过：未跟踪手工 .bak 源码备份。"
