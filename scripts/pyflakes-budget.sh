#!/usr/bin/env bash
# 渐进清理历史 pyflakes 债务：允许现有告警，但禁止数量反弹。
# 用法：bash scripts/pyflakes-budget.sh 17
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAX_WARNINGS="${1:-0}"

cd "$ROOT/backend"
OUTPUT="$(python -m pyflakes app 2>&1 || true)"
COUNT="$(printf '%s\n' "$OUTPUT" | awk 'NF {n++} END {print n+0}')"

if [[ -n "$OUTPUT" ]]; then
  printf '%s\n' "$OUTPUT"
fi

echo "pyflakes warnings: $COUNT / budget: $MAX_WARNINGS"
if (( COUNT > MAX_WARNINGS )); then
  echo "pyflakes 告警超过当前技术债预算；禁止新增 lint 债务。"
  exit 1
fi

if printf '%s\n' "$OUTPUT" | grep -Eq "undefined name|referenced before assignment"; then
  echo "发现运行级静态错误。"
  exit 1
fi
