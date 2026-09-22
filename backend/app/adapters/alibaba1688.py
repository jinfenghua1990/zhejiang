from typing import Any
from urllib.parse import urlencode

from app.adapters.base import AdapterNotConfigured
from app.config import settings

"""Alibaba1688Adapter —— OAuth 只读买家订单（规格 7.2）。

当前尚未提供真实 AppKey/AppSecret：OAuth/callback/scheduler 骨架就绪，
未配置时明确抛 AdapterNotConfigured，不得用假数据冒充已连接。
真实 API method 以用户创建 1688 应用后的实际权限列表为准。
"""

OAUTH_AUTHORIZE_URL = "https://open.1688.com/oauth/authorize"


class Alibaba1688Adapter:
    provider = "alibaba_1688"

    def ensure_configured(self) -> None:
        if not settings.alibaba_1688_configured:
            raise AdapterNotConfigured("1688 开放平台未配置（等待 AppKey/AppSecret/回调地址）")

    def get_authorization_url(self, state: str) -> str:
        self.ensure_configured()
        query = urlencode(
            {
                "client_id": settings.ALIBABA_1688_APP_KEY,
                "site": "alibaba",
                "redirect_uri": settings.ALIBABA_1688_REDIRECT_URI,
                "state": state,
            }
        )
        return f"{OAUTH_AUTHORIZE_URL}?{query}"

    def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        self.ensure_configured()
        raise NotImplementedError("token 交换待 1688 应用创建后按实际权限实现")

    def get_orders_since(self, since_iso: str) -> list[dict[str, Any]]:
        self.ensure_configured()
        raise NotImplementedError("买家订单查询 method 以实际权限列表为准")

    def get_order_detail(self, order_id: str) -> dict[str, Any]:
        self.ensure_configured()
        raise NotImplementedError

    def get_logistics(self, order_id: str) -> dict[str, Any]:
        self.ensure_configured()
        raise NotImplementedError

    def get_invoice_status(self, order_id: str) -> dict[str, Any]:
        self.ensure_configured()
        raise NotImplementedError("不得假定一定有发票接口（规格 7.2）")
