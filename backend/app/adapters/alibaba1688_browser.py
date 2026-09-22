"""1688 浏览器直采适配器 —— Playwright 驱动真实 Chrome 截获订单 JSON。

原理（参考 1688-cli）：打开「已买到的货品」订单列表页，让 1688 自己的网页正常加载数据，
监听页面产生的 ``mtop.1688.trading.dataline.service`` 网络响应并解析订单 JSON。
不破解签名、不伪造开放平台 API；登录态通过持久化 Profile（user_data_dir）保存。

本模块只管浏览器（打开/导航/登录检测/翻页捕获），不做任何数据库操作；
订单字段路径解析全部交给 ``app.services.alibaba1688_mtop_mapper``（防御式映射）。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator

from app.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

ORDER_LIST_URL = "https://air.1688.com/app/ctf-page/trade-order-list/buyer-order-list.html"
# 页面自身请求订单数据的 mtop 接口关键字（URL 中出现即视为目标响应）。
MTOP_API_KEYWORD = "mtop.1688.trading.dataline.service"
# 登录跳转特征。
LOGIN_URL_KEYWORDS = ("login.1688.com", "login.taobao.com")
# 风控/验证页特征。
RISK_URL_KEYWORDS = ("punish", "captcha", "sec.taobao.com", "x5sec")
# mtop ret 字段分类。
RET_LOGIN_CODES = ("NOT_LOGIN", "SESSION_EXPIRED", "FAIL_SYS_SESSION_EXPIRED", "NOTLOGIN")
RET_RISK_CODES = ("RGV587_ERROR", "FAIL_SYS_ILLEGAL_ACCESS", "FAIL_SYS_USER_VALIDATE")

# JSONP 包装：mtopjsonp123(...) / jsonpCallback(...)
_JSONP_RE = re.compile(r"^[\w.$]*\((.*)\)\s*;?$", re.S)


class BrowserLoginExpiredError(Exception):
    """登录态失效或未登录（需要重新扫码）。"""


class BrowserRiskControlError(Exception):
    """触发 1688 风控/验证页（需人工到服务器窗口处理）。"""


class BrowserCaptureError(Exception):
    """目标 mtop 响应未捕获（页面结构可能变化）。"""


def _parse_mtop_body(body: str) -> dict[str, Any] | None:
    """剥 JSONP 包装并解析 JSON；失败返回 None（不抛）。"""
    if not body:
        return None
    text = body.strip()
    match = _JSONP_RE.match(text)
    if match:
        text = match.group(1).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _classify_mtop_ret(ret: Any) -> str:
    """按 mtop ret 字段分类：success / login_expired / risk_control / unknown。

    mtop 的 ret 可能是字符串或字符串列表（实测订单接口返回 ['SUCCESS::调用成功']），
    统一展平为空格分隔字符串后再判断——str(list) 得到 "['SUCCESS...']" 会破坏
    startswith 前缀匹配，导致成功响应被误判为 unknown。
    """
    if isinstance(ret, (list, tuple)):
        ret = " ".join(str(item) for item in ret if item is not None)
    upper = str(ret or "").upper()
    if not upper or upper.startswith("SUCCESS"):
        return "success"
    for code in RET_LOGIN_CODES:
        if code in upper:
            return "login_expired"
    for code in RET_RISK_CODES:
        if code in upper:
            return "risk_control"
    return "unknown"


class Alibaba1688BrowserAdapter:
    """单实例对应一个持久化 Profile；同一 Profile 同时只允许一个实例（由上层 Redis 锁保证）。"""

    def __init__(
        self,
        *,
        headless: bool | None = None,
        channel: str | None = None,
        profile_dir: str | None = None,
        nav_timeout_ms: int | None = None,
    ) -> None:
        self.headless = settings.ALIBABA_1688_BROWSER_HEADLESS if headless is None else headless
        self.channel = settings.ALIBABA_1688_BROWSER_CHANNEL if channel is None else channel
        self.profile_dir = profile_dir or settings.alibaba_1688_browser_profile_dir
        self.nav_timeout_ms = (
            settings.ALIBABA_1688_BROWSER_NAV_TIMEOUT_MS if nav_timeout_ms is None else nav_timeout_ms
        )
        self._pw = None
        self._context = None
        self._page = None
        self._raw_responses: list[dict[str, Any]] = []
        # 持久监听累积的全部 mtop 响应（含非订单接口），供捕获点排空取用。
        self._pending_responses: list[dict[str, Any]] = []
        # check_login 导航期间收集的 mtop 响应，iter_order_pages 首页直接复用。
        self._probe_responses: list[dict[str, Any]] = []

    # ---------- 生命周期 ----------

    def open(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": self.profile_dir,
            "headless": self.headless,
            "args": ["--disable-blink-features=AutomationControlled"],
            # 关键：剔除 --use-mock-keychain。Playwright 默认加该参数后，macOS 上
            # Chrome 的 cookie 加密密钥存放在内存 mock keychain（每次启动随机生成），
            # 导致上次会话保存的登录 cookie 下次启动无法解密 → 扫码登录永远无法跨启动保留。
            # 剔除后 Chrome 使用真实 Keychain（Chrome Safe Storage，密钥稳定持久）。
            "ignore_default_args": ["--use-mock-keychain"],
        }
        if self.channel:
            launch_kwargs["channel"] = self.channel
        self._context = self._pw.chromium.launch_persistent_context(**launch_kwargs)
        self._context.set_default_timeout(self.nav_timeout_ms)
        self._import_state_cookies()
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        # 持久监听：会话全程累积 mtop 响应。订单页连续加载多次后，SPA 数据缓存
        # 可能让后续 reload 不再发起新请求（实测 2026-09-04 18:22 同步失败原因），
        # 只靠「reload 窗口内的新响应」会漏采；改为任何时刻到达的响应都入缓冲。
        self._page.on("response", self._on_mtop_response)

    def _on_mtop_response(self, resp) -> None:
        """持久 response 监听：解析 mtop 响应并累积（解析失败静默跳过）。"""
        if MTOP_API_KEYWORD not in (resp.url or ""):
            return
        try:
            data = _parse_mtop_body(resp.text())
        except Exception:  # noqa: BLE001 - 个别响应无 body 不影响其余收集
            return
        if data is not None:
            self._pending_responses.append(data)
            self._raw_responses.append(data)

    def _import_state_cookies(self) -> None:
        """启动时注入 storageState 备份的登录 cookie（第二重保险）。

        覆盖场景：Chrome 升级导致加密密钥变化、Keychain 访问受限等造成的
        Profile cookie 不可用。storageState JSON 不依赖 Chrome 加密，注入后
        同名同域的 cookie 直接覆盖 Profile 内旧值。
        """
        state_file = Path(settings.alibaba_1688_browser_state_file)
        if not state_file.exists():
            return
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            cookies = state.get("cookies") or []
            if cookies:
                self._context.add_cookies(cookies)
        except (ValueError, OSError) as exc:  # noqa: BLE001 - 状态文件损坏不影响主流程
            logger.warning("1688 登录态备份文件读取失败（忽略）：%s", exc)

    def export_state(self) -> str | None:
        """导出当前登录态到 storageState JSON（含 session cookie），返回文件路径。"""
        if self._context is None:
            return None
        state_file = Path(settings.alibaba_1688_browser_state_file)
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(state_file))
            state_file.chmod(0o600)
            return str(state_file)
        except Exception as exc:  # noqa: BLE001 - 导出失败不影响登录/同步主流程
            logger.warning("1688 登录态备份导出失败（忽略）：%s", exc)
            return None

    def close(self) -> None:
        # 顺序不可反：先关浏览器上下文，再停 playwright 驱动。
        # 注意：sync API 的驱动对象只有 stop() 没有 close()；若误用 close()，
        # AttributeError 会被吞掉、驱动的事件循环泄漏在 Celery prefork 进程内，
        # 同一 worker 的下一个任务会报 "Sync API inside the asyncio loop"。
        if self._context is not None:
            try:
                self._context.close()
            except Exception:  # noqa: BLE001 - 关闭失败不能掩盖业务结果
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._pw = None
        self._page = None

    def __enter__(self) -> "Alibaba1688BrowserAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @property
    def page(self):
        if self._page is None:
            raise RuntimeError("浏览器未打开：先调用 open() 或使用 with 语句")
        return self._page

    # ---------- URL 状态检测 ----------

    def _classify_current_url(self) -> str:
        url = self.page.url or ""
        low = url.lower()
        if any(k in low for k in RISK_URL_KEYWORDS):
            return "risk"
        if any(k in low for k in LOGIN_URL_KEYWORDS):
            return "login"
        return "ok"

    def _raise_by_url_state(self) -> None:
        state = self._classify_current_url()
        if state == "login":
            raise BrowserLoginExpiredError(f"页面跳转到登录页：{self.page.url}")
        if state == "risk":
            raise BrowserRiskControlError(f"触发 1688 风控/验证页：{self.page.url}")

    # ---------- 登录 ----------

    def _account_from_cookies(self) -> str | None:
        try:
            cookies = self._context.cookies()
        except Exception:  # noqa: BLE001
            return None
        for name in ("tracknick", "cookie17", "lgc"):
            for cookie in cookies or []:
                if cookie.get("name") == name and cookie.get("value"):
                    from urllib.parse import unquote

                    return unquote(str(cookie["value"]))
        return None

    def _account_from_dom(self) -> str | None:
        for selector in (
            "[class*='userName']",
            "[class*='user-name']",
            "[class*='nickName']",
            ".avatar-text",
        ):
            try:
                text = self.page.locator(selector).first.inner_text(timeout=2000)
            except Exception:  # noqa: BLE001
                continue
            text = (text or "").strip()
            if text and 1 <= len(text) <= 40:
                return text
        return None

    def check_login(self) -> tuple[bool, str | None]:
        """探测登录态：返回 (是否登录, 账号名|None)。

        1688 登录框可能内嵌在页面里（URL 不跳转），URL 检查只是快速路径，
        最终以 mtop 响应的 ret 字段为准。导航期间同步收集响应（不再额外
        reload——连续多次页面加载易触发 1688 频控）；登录有效时收集到的
        响应保存到 _probe_responses 供 iter_order_pages 首页复用。
        """
        collected = self._collect_mtop_responses(
            lambda: self.page.goto(ORDER_LIST_URL, wait_until="domcontentloaded"),
            settle_ms=5000,
        )
        state = self._classify_current_url()
        if state == "login":
            return False, None
        if state == "risk":
            raise BrowserRiskControlError(f"触发 1688 风控/验证页：{self.page.url}")
        for data in collected:
            if _classify_mtop_ret(data.get("ret", "")) == "risk_control":
                raise BrowserRiskControlError(f"mtop 返回风控拦截：{data.get('ret')}")
        for data in collected:
            if _classify_mtop_ret(data.get("ret", "")) == "success":
                self._probe_responses = collected
                return True, self._account_from_cookies() or self._account_from_dom()
        return False, None

    def wait_for_login(self, timeout_s: int | None = None) -> str | None:
        """等待用户在服务器窗口扫码；以 mtop 响应成功为登录判据，超时抛 TimeoutError。

        1688 的登录框是「内嵌在订单页里的组件」（URL 不跳转），不能用 URL 判断；
        扫码成功后页面会自动重新请求订单 mtop 接口——被动监听该请求即可，
        不主动刷新页面（避免重绘二维码打断用户扫码）。
        """
        timeout_s = timeout_s or settings.ALIBABA_1688_LOGIN_TIMEOUT_SECONDS
        self.goto_order_list()

        # 阶段一（主动探测）：reload 触发一次 mtop 请求。已登录→直接成功；
        # 未登录→ret=SESSION_EXPIRED；独立登录页跳转场景探测可能超时，同样进入等待。
        logged_in, account = self._probe_login_once()
        if logged_in:
            return account

        # 阶段二（被动等待）：扫码成功后页面自动发起 mtop 请求，这里只监听不触发。
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with self.page.expect_response(
                    lambda resp: MTOP_API_KEYWORD in (resp.url or ""), timeout=15_000
                ) as waiter:
                    pass
                body = waiter.value.text()
            except Exception:  # noqa: BLE001 - 15s 无新响应，继续等扫码
                continue
            data = _parse_mtop_body(body)
            if data is None:
                continue
            category = _classify_mtop_ret(data.get("ret", ""))
            if category == "success":
                self.page.wait_for_timeout(2000)
                # 扫码成功后登录 cookie 可能延迟数百毫秒才写入，最多再等 6s 重试取账号。
                account: str | None = None
                for _ in range(3):
                    account = self._account_from_cookies() or self._account_from_dom()
                    if account:
                        break
                    self.page.wait_for_timeout(2000)
                return account
            if category == "risk_control":
                raise BrowserRiskControlError(f"mtop 返回风控拦截：{data.get('ret')}")
            # 其余（未登录）继续等待用户扫码
        raise TimeoutError(f"等待扫码登录超时（{timeout_s}s）")

    def _probe_login_once(self) -> tuple[bool, str | None]:
        """主动 reload 并收集窗口内全部 mtop 响应判定登录态。

        单响应判定不可靠：页面可能先以过期 token 发一次请求（ret=SESSION_EXPIRED），
        随后自动刷新 token 重试成功——只看第一条会误判为未登录。
        窗口内任一响应 SUCCESS 即视为已登录；全部为未登录/无响应才判定未登录。
        注意 drain=False：响应保留在缓冲里，供随后的订单捕获复用。
        """
        collected = self._collect_mtop_responses(
            lambda: self.page.reload(wait_until="domcontentloaded"),
            drain=False,
        )
        if not collected:
            return False, None
        for data in collected:
            if _classify_mtop_ret(data.get("ret", "")) == "risk_control":
                raise BrowserRiskControlError(f"mtop 返回风控拦截：{data.get('ret')}")
        for data in collected:
            if _classify_mtop_ret(data.get("ret", "")) == "success":
                return True, self._account_from_cookies() or self._account_from_dom()
        return False, None

    def _collect_mtop_responses(
        self, action, *, first_timeout_ms: int | None = None, settle_ms: int = 4000,
        drain: bool = True,
    ) -> list[dict[str, Any]]:
        """执行 action（reload/点击等）并返回窗口内可用的 mtop 响应。

        持久监听已把全部响应累积进 ``_pending_responses``；这里等待 action
        触发的新响应（缓冲增长），静置 settle_ms 收齐重试请求后返回：
        - drain=True：排空缓冲（订单捕获用，翻页间不重复消费）；
        - drain=False：只快照不清空（登录探测用，响应留给随后的订单捕获）。
        缓冲里 action 之前的历史响应同样会返回——这是有意为之：订单页可能
        不再为重复 reload 发新请求，之前导航捕获的响应就是本页数据。
        """
        start_len = len(self._pending_responses)
        action()
        first_timeout_ms = first_timeout_ms or self.nav_timeout_ms
        deadline = time.monotonic() + first_timeout_ms / 1000
        while len(self._pending_responses) <= start_len and time.monotonic() < deadline:
            self.page.wait_for_timeout(500)
        if len(self._pending_responses) > start_len:
            self.page.wait_for_timeout(settle_ms)
        if drain:
            out = self._pending_responses
            self._pending_responses = []
            return out
        return list(self._pending_responses)

    # ---------- 订单捕获 ----------

    def _consume_mtop_bodies(self, bodies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """多条响应统一做风控/登录校验，返回成功子集供 mapper 解析订单。"""
        if not bodies:
            screenshot = self._dump_debug_screenshot("capture-empty")
            raise BrowserCaptureError(
                f"等待订单 mtop 响应失败：窗口内无任何响应（当前页面 {self.page.url}"
                + (f"，诊断截图 {screenshot}" if screenshot else "")
                + "）"
            )
        for data in bodies:
            if _classify_mtop_ret(data.get("ret", "")) == "risk_control":
                raise BrowserRiskControlError(f"mtop 返回风控拦截：{data.get('ret')}")
        successes = [
            data for data in bodies
            if _classify_mtop_ret(data.get("ret", "")) == "success"
        ]
        if not successes:
            for data in bodies:
                if _classify_mtop_ret(data.get("ret", "")) == "login_expired":
                    raise BrowserLoginExpiredError(f"mtop 返回未登录：{data.get('ret')}")
            raise BrowserCaptureError("订单 mtop 响应无成功数据（页面结构可能变化）")
        return successes

    def _dump_debug_screenshot(self, tag: str) -> str | None:
        """捕获失败时落一张诊断截图（频控/验证页肉眼可辨）。"""
        try:
            path = Path(settings.DATA_DIR) / f"alibaba1688-debug-{tag}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(path))
            return str(path)
        except Exception:  # noqa: BLE001 - 诊断失败不影响主流程
            return None

    def _find_next_page_button(self):
        """定位“下一页”按钮；找不到返回 None（视为已到末页）。"""
        for selector in (
            "button.next-btn",
            "[class*='next']",
            "a[data-page]:has-text('下一页')",
            "li.ant-pagination-next button",
        ):
            try:
                button = self.page.locator(selector).first
                if button.is_visible(timeout=1500):
                    return button
            except Exception:  # noqa: BLE001
                continue
        return None

    def iter_order_pages(self, *, max_pages: int | None = None) -> Iterator[list[dict[str, Any]]]:
        """逐页产出一组 mtop 响应 dict（通常 1 个响应含整页订单）；
        调用方（mapper + 同步服务）负责订单提取、增量判重与停止。"""
        max_pages = max_pages or settings.ALIBABA_1688_BROWSER_MAX_PAGES
        # 第一页：优先复用 check_login 导航期间收集的响应（零额外加载，避免
        # 短时间内多次导航触发 1688 频控）；没有则 goto 触发并收集；
        # 仍为空时 reload 兜底重试一次。
        first = self._probe_responses
        self._probe_responses = []
        if not first:
            first = self._collect_mtop_responses(
                lambda: self.page.goto(ORDER_LIST_URL, wait_until="domcontentloaded")
            )
            self._raise_by_url_state()
        if not first:
            first = self._collect_mtop_responses(
                lambda: self.page.reload(wait_until="domcontentloaded")
            )
            self._raise_by_url_state()
        yield self._consume_mtop_bodies(first)

        for _ in range(max_pages - 1):
            self._raise_by_url_state()
            button = self._find_next_page_button()
            if button is None or button.is_disabled():
                return
            # 监听先于点击挂载：翻页响应在点击后数百毫秒内返回，先挂监听避免竞态漏采。
            collected = self._collect_mtop_responses(lambda: button.click())
            self._raise_by_url_state()
            yield self._consume_mtop_bodies(collected)

    def goto_order_list(self) -> None:
        """导航到订单列表页并做登录/风控检测。"""
        self.page.goto(ORDER_LIST_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        self._raise_by_url_state()

    @property
    def raw_responses(self) -> list[dict[str, Any]]:
        """本次会话累计的原始 mtop 响应（去重前全部）。"""
        return list(self._raw_responses)
