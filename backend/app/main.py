import asyncio
import os

from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import require_auth
from app.api.v1 import api_router
from app.config import settings
from app.core.logging import configure_logging

# 前端静态产物（next export 输出）。可通过 FRONTEND_OUT 覆盖。
_FRONTEND_OUT = os.environ.get(
    "FRONTEND_OUT",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "out")),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    update_task = None
    if settings.DEPLOYMENT_MODE != "container":
        from app.services import system_update_service
        update_task = asyncio.create_task(system_update_service.poll_loop(), name="system-update-poller")
    try:
        yield
    finally:
        if update_task is not None:
            update_task.cancel()
            with suppress(asyncio.CancelledError):
                await update_task


app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

# CORS：放行 settings.CORS_ALLOW_ORIGINS 显式白名单 + CORS_ALLOW_ORIGIN_REGEX 匹配的源
# （正则用于局域网 IP 上的 8888 聚合中心，DHCP 下 IP 会变，无法写死）。
# 关闭 credentials；转公网必须把正则置空并收紧为严格白名单。
_origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def system_update_maintenance(request, call_next):
    """更新期间暂停普通业务 API，保留更新状态、认证与健康检查。

    静态前端仍可打开，管理员可以持续查看更新进度；业务写操作不会在迁移窗口内继续发生。
    """
    path = request.url.path
    allowed = (
        path == "/healthz"
        or path.startswith("/api/v1/system/update")
        or path.startswith("/api/v1/auth/")
    )
    if path.startswith("/api/v1") and not allowed:
        maintenance_file = os.path.join(settings.DATA_DIR, "system-update", "maintenance.json")
        if os.path.isfile(maintenance_file):
            message = "系统正在更新，业务操作已暂时锁定"
            try:
                import json
                with open(maintenance_file, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                message = str(payload.get("message") or message)
            except Exception:
                pass
            return JSONResponse(
                status_code=503,
                content={"detail": message, "maintenance": True},
                headers={"Retry-After": "15"},
            )
    return await call_next(request)


@app.middleware("http")
async def security_headers(_request, call_next):
    response = await call_next(_request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    # HTML 每次使用前都向服务器校验新鲜度：next build 会清空旧 chunk，
    # 浏览器缓存的旧 index.html 引用已删除的 chunk 会导致「改完没生效」，必须硬刷新才能好。
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# 全局登录鉴权：除登录/1688回调等豁免路径外，全部 /api/v1 需要令牌（见 api/deps.py）
app.include_router(api_router, dependencies=[Depends(require_auth)])


@app.get("/healthz")
def healthz() -> dict:
    return {
        "ok": True,
        "accessMode": settings.ACCESS_MODE,
        "appEnv": settings.APP_ENV,
        "releaseChannel": settings.RELEASE_CHANNEL,
        "deploymentMode": settings.DEPLOYMENT_MODE,
        "gitSha": settings.GIT_SHA[:12] if settings.GIT_SHA else "",
    }


# ---------- 前端静态托管（next export 产物，单口 8000 同服 API + 前端） ----------
def _resolve_static(rel_path: str) -> str | None:
    """把 clean URL 解析到 out/ 下的真实文件：/products -> products.html / products/index.html。"""
    if not rel_path or rel_path == "/":
        index_path = os.path.join(_FRONTEND_OUT, "index.html")
        return index_path if os.path.isfile(index_path) else None
    cand = [
        os.path.join(_FRONTEND_OUT, rel_path),
        os.path.join(_FRONTEND_OUT, rel_path + ".html"),
        os.path.join(_FRONTEND_OUT, rel_path, "index.html"),
    ]
    for c in cand:
        if os.path.isfile(c):
            return c
    return None


# 正式 8000 部署完成前端 build 后照常挂载；纯后端测试/迁移环境没有
# frontend/out 时不应在 import FastAPI 应用阶段直接崩溃。
_next_dir = os.path.join(_FRONTEND_OUT, "_next")
if os.path.isdir(_next_dir):
    app.mount("/_next", StaticFiles(directory=_next_dir), name="next-static")


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # /api 与 /healthz 已在上方路由优先匹配，这里只兜底前端资源与 SPA 路由
    if full_path.startswith("api"):
        raise HTTPException(status_code=404, detail="Not Found")
    file_path = _resolve_static(full_path)
    if file_path is None:
        # 带扩展名的缺失资源（图片/字体等）直接 404，避免误回 index.html
        if "." in full_path:
            raise HTTPException(status_code=404, detail="Not Found")
        # 其余未知路径交给前端路由（SPA fallback）；测试/后端-only 环境
        # 若尚无前端产物，则明确 404，而不是 FileResponse 指向不存在文件。
        index_path = os.path.join(_FRONTEND_OUT, "index.html")
        if not os.path.isfile(index_path):
            raise HTTPException(status_code=404, detail="Frontend build not available")
        file_path = index_path
    # HTML 页面（SPA 路由与 .html）禁用启发式缓存：前端重新构建后浏览器必须
    # 重新拉取页面外壳，否则会一直显示旧版界面（/_next 资源带内容哈希可长缓存）。
    headers = {"Cache-Control": "no-cache"} if file_path.endswith(".html") else None
    return FileResponse(file_path, headers=headers)
