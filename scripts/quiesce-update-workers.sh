#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/backend"

stop_matching_celery() {
  local pattern="$1"
  local pid=""
  local command=""
  local cwd=""

  while read -r pid command; do
    [ -n "$pid" ] || continue
    case "$command" in
      *"$pattern"*) ;;
      *) continue ;;
    esac

    cwd=$(/usr/sbin/lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    [ "$cwd" = "$BACKEND" ] || continue

    echo "[update] 暂停 Celery 进程 $pid · $pattern"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "[update] Celery 进程 $pid 未及时退出，强制结束"
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done < <(ps -axo pid=,command=)
}

stop_matching_celery "celery -A app.celery_app worker"
stop_matching_celery "celery -A app.celery_app beat"

echo "[update] worker / beat 已暂停"
