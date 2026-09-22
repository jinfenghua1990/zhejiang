from functools import lru_cache
from importlib.util import find_spec
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。所有秘密只从环境变量/.env 读取，禁止进入 Git 与前端。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "电商经营数据平台"
    APP_SECRET_KEY: str = ""

    # 运行时版本/部署元数据：容器由 GitHub Actions + Compose 注入，原生模式保持兼容。
    APP_ENV: Literal["production", "staging", "development"] = "development"
    RELEASE_CHANNEL: str = "local"
    DEPLOYMENT_MODE: Literal["native", "container"] = "native"
    GIT_SHA: str = ""
    APP_IMAGE_REF: str = ""
    # rbac = 账号密码 + 服务器端 RBAC；open = 仅适合受控局域网的直达模式。
    ACCESS_MODE: Literal["rbac", "open"] = "rbac"

    # 初始管理员（仅首次启动创建时生效；改密码后不会被覆盖，除非 FORCE_ADMIN_PASSWORD=1）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    FORCE_ADMIN_PASSWORD: bool = False

    # CORS：默认允许同机 Next dev (3000) + 本平台前端 (8000) + 8888 聚合中心。
    # 聚合中心需跨域读 /healthz 渲染状态点，故纳入白名单。
    # 转公网前必须改成严格白名单并启用 RBAC。
    CORS_ALLOW_ORIGINS: str = (
        "http://localhost:3000,http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:8888,http://127.0.0.1:8888"
    )

    # CORS 正则：放行局域网 IP 上的 8888 聚合中心（宿主机 IP 由 DHCP 分配，无法逐个写死）。
    # 仅覆盖 RFC1918 私网段且只匹配 8888 端口；转公网必须置空并改回严格白名单。
    CORS_ALLOW_ORIGIN_REGEX: str = (
        r"^http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}):8888$"
    )

    DATABASE_URL: str = "postgresql+psycopg://ecommerce:CHANGE_ME@postgres:5432/ecommerce"
    # 可选：Alembic/发布迁移专用连接。为空时兼容沿用 DATABASE_URL。
    # production 推荐使用独立 migrator 账号，日常 API/worker 只使用 DATABASE_URL。
    MIGRATION_DATABASE_URL: str = ""
    REDIS_URL: str = "redis://redis:6379/0"

    # 持久化目录与应用代码分离。production 自更新会把这组配置作为硬门禁检查。
    PERSIST_ROOT: str = ""
    DATA_DIR: str = "/data"
    BACKUP_DIR: str = ""
    LOG_DIR: str = ""
    # 上传文件按字节流分段读取，避免单个请求占满应用内存；如财务原件确实更大可只在 .env 调整。
    MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024
    # 吉客云客户端导出文件在 HTTP 请求内同步解析，单独收紧体积和行数上限。
    MAX_JACKYUN_IMPORT_BYTES: int = 25 * 1024 * 1024
    MAX_JACKYUN_IMPORT_ROWS: int = 20_000
    TZ: str = "Asia/Shanghai"

    # 系统自更新：代码来源固定在受控 Git remote/branch，UI 只允许调整检查/安装模式。
    SYSTEM_UPDATE_ENABLED: bool = True
    SYSTEM_UPDATE_REMOTE: str = "origin"
    SYSTEM_UPDATE_BRANCH: str = "main"
    SYSTEM_UPDATE_REPO_ROOT: str = ""
    SYSTEM_UPDATE_HEALTH_URL: str = "http://127.0.0.1:8000/healthz"
    SYSTEM_UPDATE_LAUNCH_LABEL: str = "com.gino.ecommerce-dashboard"

    # 数据中心导入回收站保留天数：软删除的导入超过该天数后由 Celery 定时任务硬删除。
    RECYCLE_BIN_RETENTION_DAYS: int = 30

    # 吉客云（Phase 1）
    JACKYUN_MCP_URL: str = ""
    JACKYUN_APP_KEY: str = ""
    JACKYUN_MCP_TOKEN: str = ""
    # 吉客云同步模式（beat 调度档位）：
    #   manual = 默认。月度经营系统不主动拉取吉客云，仅在导入/月结/人工触发时更新。
    #   test   = 低频验证模式，仅在排查连接问题时临时启用。
    #   auto   = 高频运营模式，保留兼容但不是本项目默认经营口径。
    JACKYUN_SYNC_MODE: str = "manual"

    # 1688 开放平台（Phase 4，未提供前显示未配置；OAuth 通道保留为浏览器直采的备用）
    ALIBABA_1688_APP_KEY: str = ""
    ALIBABA_1688_APP_SECRET: str = ""
    ALIBABA_1688_REDIRECT_URI: str = ""

    # 1688 浏览器直采（主通道）：服务器端 Playwright 驱动真实 Chrome，
    # 打开「已买到的货品」订单页并监听页面自身的 mtop 响应，截获订单 JSON。
    ALIBABA_1688_BROWSER_ENABLED: bool = True
    # 持久化登录 Profile 目录；空 = {DATA_DIR}/alibaba1688-browser-profile
    ALIBABA_1688_BROWSER_PROFILE_DIR: str = ""
    # Mac mini 常驻桌面会话时有头浏览器更不易触发风控；Docker/无 GUI 环境可改 True。
    ALIBABA_1688_BROWSER_HEADLESS: bool = False
    # "chrome" = 驱动本机安装的 Google Chrome（指纹更真实）；"" = playwright 内置 chromium。
    ALIBABA_1688_BROWSER_CHANNEL: str = "chrome"
    # 增量同步安全上限：单次最多翻页数 + 连续已知订单阈值（订单列表按时间倒序）。
    ALIBABA_1688_BROWSER_MAX_PAGES: int = 10
    ALIBABA_1688_BROWSER_STOP_AFTER_KNOWN: int = 15
    # 安全回看窗口：订单时间早于该窗口且当前页没有新订单时停止，避免只依赖已知订单阈值。
    ALIBABA_1688_BROWSER_LOOKBACK_DAYS: int = 7
    ALIBABA_1688_BROWSER_NAV_TIMEOUT_MS: int = 30_000
    # 首捕调试模式：只把原始 mtop 响应落库/落盘，不写订单表（用于回填字段映射路径）。
    ALIBABA_1688_BROWSER_CAPTURE_ONLY: bool = False
    # 服务器弹窗扫码的最长等待时间（秒）。
    ALIBABA_1688_LOGIN_TIMEOUT_SECONDS: int = 300
    # mtop 字段映射路径覆盖（JSON 字符串），免改码调整 mapper 候选路径。
    ALIBABA_1688_MTOP_FIELD_PATHS_JSON: str = ""

    # 吉客云 Web Adapter（V1 主通道）：复用网页登录态直读数据。
    # adapter 选择：web（当前主通道）/ openapi（开放平台恢复后切换）/ excel（兜底文件导入）。
    JKY_ADAPTER: str = "web"
    # 网页端点域名（吉客云网页版实际环境）。
    JKY_WEB_BASE_URL: str = "https://env3.jkyservice.com"
    # 网页接口签名密钥（部署级机密，来自吉客云前端 JS；参考项目 JKY_WEB_SIGN_SECRET 同名变量）。
    JKY_WEB_SIGN_SECRET: str = ""
    # 销售订单/明细/采购入库增量回看窗口（天）。
    JKY_WEB_SYNC_LOOKBACK_DAYS: int = 30
    # 直连接口分页大小。
    JKY_WEB_PAGE_SIZE: int = 200
    # 销售导出任务轮询超时与间隔（秒）。
    JKY_WEB_EXPORT_TIMEOUT_SECONDS: int = 900
    JKY_WEB_EXPORT_POLL_SECONDS: int = 10
    # 单次导出窗口拆分阈值：超过则对半拆分重导，避免导出上限截断。
    JKY_WEB_EXPORT_SPLIT_ROWS: int = 400_000

    # 吉客云销售订单三通道：Web → Windows RPA → OpenAPI/MCP。
    # 顺序可通过 .env 调整；manual 模式下不会被 beat 自动触发，仍可人工运行。
    JKY_ORDER_PROVIDER_PRIORITY: str = "jky_web,jky_rpa,jky_api"
    JKY_ORDER_SYNC_INTERVAL_MINUTES: int = 30
    JKY_ORDER_SYNC_OVERLAP_MINUTES: int = 30
    JKY_ORDER_INITIAL_LOOKBACK_DAYS: int = 3
    JKY_ORDER_STALE_RUN_MINUTES: int = 180
    JKY_ORDER_LOW_COUNT_RATIO: float = 0.25

    # Windows RPA Agent：Agent 运行在 Windows 桌面机，API 服务只通过内网调用。
    JKY_RPA_AGENT_URL: str = ""
    JKY_RPA_AGENT_TOKEN: str = ""
    JKY_RPA_TIMEOUT_SECONDS: int = 900
    JKY_RPA_HEALTH_TIMEOUT_SECONDS: int = 5
    JKY_API_MAX_RETRIES: int = 2

    # 财务邮件（Phase 6）
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""

    @property
    def jackyun_configured(self) -> bool:
        return bool(self.JACKYUN_MCP_URL and self.JACKYUN_MCP_TOKEN)

    @property
    def alibaba_1688_configured(self) -> bool:
        return bool(self.ALIBABA_1688_APP_KEY and self.ALIBABA_1688_APP_SECRET)

    @property
    def smtp_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USERNAME and self.SMTP_PASSWORD)

    @property
    def alibaba_1688_browser_profile_dir(self) -> str:
        if self.ALIBABA_1688_BROWSER_PROFILE_DIR:
            return self.ALIBABA_1688_BROWSER_PROFILE_DIR
        return f"{self.DATA_DIR.rstrip('/')}/alibaba1688-browser-profile"

    @property
    def alibaba_1688_browser_state_file(self) -> str:
        """登录态 storageState JSON（兜底）：Chrome 升级/Keychain 异常时仍可恢复会话。"""
        return f"{self.DATA_DIR.rstrip('/')}/alibaba1688-browser-state.json"

    @property
    def alibaba_1688_browser_ready(self) -> bool:
        """Playwright 依赖可用（Docker/未安装环境优雅降级为不可用）。"""
        return find_spec("playwright") is not None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
