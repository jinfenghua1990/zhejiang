#!/usr/bin/env bash
# 端点 smoke test：枚举 OpenAPI 中所有 GET 路由，按 2xx / 4xx / 5xx 分类。
# RBAC 模式下在本机为现有管理员签发 5 分钟诊断令牌，避免 401 掩盖真实故障。
#
# 用法: ./scripts/smoke.sh                    # 本机 API 8000
#       BASE=http://host:port ./scripts/smoke.sh
#       SMOKE_USER=admin SMOKE_PASS=xxx ./scripts/smoke.sh

set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/backend/.venv"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "FAIL: 未找到后端虚拟环境 $VENV"
  exit 1
fi

set -a
source "$ROOT/.env"
set +a
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}"
export REDIS_URL="redis://localhost:6379/0"
export DATA_DIR="${DATA_DIR:-$ROOT/data}"

echo "--- Smoke @ $BASE (native) ---"
cd "$ROOT/backend"
"$VENV/bin/python" -u - "$BASE" <<'PY'
import json, os, sys, urllib.request, urllib.error

base = sys.argv[1]
SMOKE_TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "20"))

def req(path, method="GET", body=None, token=None, timeout=None):
    timeout = SMOKE_TIMEOUT if timeout is None else timeout
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}".encode()

# ---- 1. 显式凭证存在时先按真实登录链路取令牌 ----
user = os.environ.get("SMOKE_USER") or ""
pwd = os.environ.get("SMOKE_PASS") or ""

# ---- 2. 登录拿令牌；未传凭证时在服务端为现有管理员签发短期诊断令牌 ----
token = None
if user and pwd:
    code, raw = req("/api/v1/auth/login", "POST",
                    {"username": user, "password": pwd})
    if 200 <= code < 300:
        try:
            token = json.loads(raw or b"{}").get("accessToken")
        except Exception:
            token = None
    mode = "RBAC（真实登录）" if token else "RBAC（真实登录失败）"
else:
    mode = ""
if not token and not (user and pwd):
    try:
        from sqlalchemy import select
        from app.core.auth import create_token
        from app.db import SessionLocal
        from app.models.org import Role, User, UserRole

        with SessionLocal() as db:
            smoke_user = db.scalar(
                select(User)
                .join(UserRole, UserRole.user_id == User.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(User.is_active.is_(True), Role.code == "admin")
                .order_by(User.id)
            )
            if smoke_user:
                token = create_token(
                    uid=smoke_user.id, username=smoke_user.username,
                    token_version=smoke_user.token_version, ttl_seconds=300,
                )
                mode = "RBAC（服务端 5 分钟诊断令牌）"
    except Exception as exc:
        mode = f"RBAC（诊断令牌失败: {type(exc).__name__}）"
print(f"  模式: {mode}\n", flush=True)
if not token:
    print("  FAIL: 无法取得鉴权令牌，停止 smoke，避免把 401 误判为通过")
    sys.exit(1)

# ---- 3. 枚举全部 GET 路由并探测 ----
openapi_code, openapi_raw = req("/openapi.json")
if openapi_code != 200:
    detail = openapi_raw.decode("utf-8", errors="replace")[:160]
    print(f"  FAIL [{openapi_code}] /openapi.json: {detail}", flush=True)
    sys.exit(1)
try:
    data = json.loads(openapi_raw)
except json.JSONDecodeError as exc:
    print(f"  FAIL [0] /openapi.json: 返回不是有效 JSON（{exc}）", flush=True)
    sys.exit(1)
get_paths = sorted(p for p in data["paths"] if "get" in data["paths"][p])

allowed_query_4xx = {
    "/api/v1/integrations/alibaba1688/callback",
    "/api/v1/procurement-chain/candidates",
    "/api/v1/profit/compute",
    "/api/v1/finance/sales-report/preview",
    "/api/v1/finance/unbilled/preview",
    "/api/v1/search/global",
    "/api/v1/tax-accounting/monthly-ledger",
    "/api/v1/tax-accounting/finance-summary",
    "/api/v1/tax-accounting/finance-summary.csv",
    "/api/v1/dashboard/inventory/sku-transactions",
    "/api/v1/finance/bank-summary",
    "/api/v1/finance/corporate-payment-report",
}


def is_expected_external_block(path, status, raw):
    """Only allow the documented, truthful 1688 prerequisite block.

    A broad allow-list would hide a regression (for example, an accidental
    server error reported as 400).  The adapter intentionally reports this
    exact condition until an AppKey/AppSecret and callback are configured.
    """
    if path != "/api/v1/integrations/alibaba1688/auth-url" or status != 400:
        return False
    try:
        detail = json.loads(raw or b"{}").get("detail", "")
    except Exception:
        return False
    return "1688" in detail and ("未配置" in detail or "AppKey" in detail)


fail, ok200, unauth, ok4xx = [], 0, 0, 0
for p in get_paths:
    print(f"  GET {p}", flush=True)
    c, raw = req(p, token=token)
    if 200 <= c < 300:
        ok200 += 1
    elif c in (401, 403):
        unauth += 1
        fail.append((c, p))
    elif 400 <= c < 500:
        if "{" in p or p in allowed_query_4xx or is_expected_external_block(p, c, raw):
            ok4xx += 1
        else:
            fail.append((c, p))
    else:
        detail = raw.decode("utf-8", errors="replace")[:160]
        fail.append((c, f"{p}: {detail}" if detail else p))

for c, p in fail:
    print(f"  FAIL [{c}] {p}")

# ---- 4. 生命周期接口可达性（文件无关：对不存在的导入探活 404，禁止 500）----
lifecycle_probes = []
for prefix in ("/api/v1/alibaba1688-imports", "/api/v1/jackyun-files", "/api/v1/tax-invoices"):
    for method, path in (
        ("POST", f"{prefix}/imports/999999/confirm"),
        ("DELETE", f"{prefix}/imports/999999"),
        ("POST", f"{prefix}/imports/999999/restore"),
    ):
        c, raw = req(path, method=method, token=token)
        if c == 500:
            lifecycle_probes.append((c, f"{method} {path}"))
        elif prefix == "/api/v1/tax-invoices" and method == "DELETE" and c == 409:
            # 官方税务导入批次明确禁止删除，未知 id 也由该不可变资源契约返回 409。
            pass
        elif c != 404:
            lifecycle_probes.append((c, f"{method} {path} (期望 404)"))

for c, p in lifecycle_probes:
    print(f"  FAIL [{c}] lifecycle {p}")
fail.extend(lifecycle_probes)

print(f"\nGET endpoints: {len(get_paths)}", flush=True)
print(f"  200 OK:        {ok200}", flush=True)
print(f"  4xx (路径参数/必填查询): {ok4xx}", flush=True)
print(f"  401/403 鉴权:  {unauth}", flush=True)
print(f"  FAIL:          {len(fail)}", flush=True)
sys.exit(1 if fail else 0)
PY
