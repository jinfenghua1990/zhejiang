"""吉客云销售订单三通道 Provider。

Provider 只负责健康检查和获取原始订单行；规范化、身份识别、去重和写入
``sales_orders`` 统一由 ``jky_order_sync_service`` 完成。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx

from app.adapters.base import AdapterError, AdapterNotConfigured, AdapterPermissionError
from app.adapters.jackyun import JackyunAdapter
from app.adapters.jackyun_export_file import parse_jackyun_export
from app.adapters.jky_web import (
    JkyWebClient,
    JkyWebError,
    JkyWebSessionManager,
)
from app.config import settings


@dataclass(frozen=True)
class JkyProviderHealth:
    provider: str
    status: str
    configured: bool
    healthy: bool
    verified: bool
    message: str = ""


class JkyOrderProvider:
    provider = ""
    label = ""

    def health_check(self) -> JkyProviderHealth:
        raise NotImplementedError

    def fetch_order_rows(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        raise NotImplementedError


class JkyOrderProviderError(AdapterError):
    """Provider 返回了可被编排器识别的业务状态。"""

    def __init__(self, message: str, status: str = "error"):
        super().__init__(message)
        self.status = status


class JkyWebOrderProvider(JkyOrderProvider):
    provider = "jky_web"
    label = "Web 网页同步"

    def __init__(self, db):
        self.db = db

    def health_check(self) -> JkyProviderHealth:
        manager = JkyWebSessionManager(self.db)
        if not settings.JKY_WEB_SIGN_SECRET:
            return JkyProviderHealth(
                self.provider, "unconfigured", False, False, False,
                "JKY_WEB_SIGN_SECRET 未配置",
            )
        if not manager.has_auth():
            return JkyProviderHealth(
                self.provider, "needs_login", False, False, False,
                "网页登录态未配置",
            )
        return JkyProviderHealth(
            self.provider, "configured_not_tested", True, True, False,
            "网页登录态已配置，实际请求将在同步时验证",
        )

    def fetch_order_rows(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        from app.services.jky_web_sync_service import (
            SALES_ORDER_CN,
            SALES_ORDER_EN,
            _export_and_parse,
        )

        try:
            auth = JkyWebSessionManager(self.db).load_auth()
            client = JkyWebClient(auth)
            return _export_and_parse(
                client,
                "2",
                "销售单查询",
                SALES_ORDER_EN,
                SALES_ORDER_CN,
                "jsonStr",
                start,
                end,
            )
        except JkyWebError:
            raise


class JkyRpaOrderProvider(JkyOrderProvider):
    provider = "jky_rpa"
    label = "Windows 桌面 RPA 导出"

    def __init__(self):
        self.base_url = settings.JKY_RPA_AGENT_URL.rstrip("/")
        self.token = settings.JKY_RPA_AGENT_TOKEN

    def _ensure_configured(self) -> None:
        if not self.base_url or not self.token:
            raise AdapterNotConfigured("Windows RPA Agent 未配置（JKY_RPA_AGENT_URL / JKY_RPA_AGENT_TOKEN）")

    def _headers(self) -> dict[str, str]:
        # 两个头同时发送，兼容 Agent 的 Bearer 与固定 Token 校验；Token 不写日志。
        return {
            "Authorization": f"Bearer {self.token}",
            "X-JKY-Agent-Token": self.token,
            "Accept": "application/json, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }

    def health_check(self) -> JkyProviderHealth:
        if not self.base_url or not self.token:
            return JkyProviderHealth(
                self.provider, "unconfigured", False, False, False,
                "Windows RPA Agent 未配置",
            )
        try:
            with httpx.Client(
                headers=self._headers(),
                timeout=settings.JKY_RPA_HEALTH_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = client.get(f"{self.base_url}/health")
            if response.status_code in (401, 403):
                return JkyProviderHealth(
                    self.provider, "blocked", True, False, False,
                    f"RPA Agent 鉴权失败（HTTP {response.status_code}）",
                )
            if response.status_code >= 400:
                return JkyProviderHealth(
                    self.provider, "error", True, False, False,
                    f"RPA Agent 健康检查失败（HTTP {response.status_code}）",
                )
            payload = response.json()
            if payload.get("ok") is False or payload.get("ready") is False:
                return JkyProviderHealth(
                    self.provider, "error", True, False, False,
                    str(payload.get("message") or "RPA Agent 未就绪")[:500],
                )
            return JkyProviderHealth(
                self.provider, "connected", True, True, True,
                "RPA Agent 已连接",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return JkyProviderHealth(
                self.provider, "error", True, False, False,
                f"RPA Agent 不可达：{exc}",
            )

    def fetch_order_rows(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        self._ensure_configured()
        body = {
            "startTime": start.isoformat(),
            "endTime": end.isoformat(),
            "reportType": "sales_orders",
            "format": "xlsx",
        }
        try:
            with httpx.Client(
                headers={**self._headers(), "Content-Type": "application/json"},
                timeout=settings.JKY_RPA_TIMEOUT_SECONDS,
                trust_env=False,
            ) as client:
                response = client.post(f"{self.base_url}/sync", json=body)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "json" in content_type:
                    return self._rows_from_json(client, response.json())
                return self._rows_from_file(response.content, "jky-rpa-sales-orders.xlsx")
        except AdapterError:
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403):
                raise AdapterPermissionError("RPA Agent 鉴权失败") from exc
            raise AdapterError(f"RPA Agent 同步失败（HTTP {exc.response.status_code}）") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AdapterError(f"RPA Agent 同步请求失败：{exc}") from exc

    @staticmethod
    def _rows_from_file(content: bytes, original_name: str) -> list[dict[str, Any]]:
        parsed = parse_jackyun_export(
            content,
            original_name,
            max_rows=settings.MAX_JACKYUN_IMPORT_ROWS,
        )
        if parsed.report_type not in ("sales", "unknown"):
            raise AdapterError(f"RPA Agent 返回的文件不是销售订单报表（识别为 {parsed.report_type}）")
        return [dict(row) for row in parsed.rows]

    def _rows_from_json(self, client: httpx.Client, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            raise AdapterError("RPA Agent 返回的 JSON 不是对象")
        if payload.get("ok") is False or str(payload.get("status") or "").lower() in {
            "failed", "error", "need_login", "needs_login", "need_user_verify",
        }:
            status = str(payload.get("status") or "failed").lower()
            if status in {"need_login", "needs_login", "need_user_verify"}:
                normalized_status = "needs_user_verify" if status == "need_user_verify" else "needs_login"
                raise JkyOrderProviderError(
                    str(payload.get("message") or "RPA Agent 需要重新登录"),
                    status=normalized_status,
                )
            raise AdapterError(str(payload.get("message") or "RPA Agent 返回失败"))

        for key in ("orders", "rows"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, dict)]

        data = payload.get("data")
        if isinstance(data, dict):
            return self._rows_from_json(client, data)
        if isinstance(data, list):
            return [dict(row) for row in data if isinstance(row, dict)]

        encoded = payload.get("fileBase64") or payload.get("contentBase64")
        if encoded:
            try:
                content = base64.b64decode(str(encoded), validate=True)
            except (ValueError, TypeError) as exc:
                raise AdapterError("RPA Agent 返回的文件内容不是有效 Base64") from exc
            return self._rows_from_file(content, "jky-rpa-sales-orders.xlsx")

        download_url = payload.get("downloadUrl") or payload.get("fileUrl")
        if download_url:
            url = urljoin(f"{self.base_url}/", str(download_url))
            response = client.get(url)
            response.raise_for_status()
            return self._rows_from_file(response.content, "jky-rpa-sales-orders.xlsx")

        raise AdapterError("RPA Agent 返回中没有 orders/rows 或导出文件")


class JkyApiOrderProvider(JkyOrderProvider):
    provider = "jky_api"
    label = "吉客云 OpenAPI/MCP"

    def __init__(self, db):
        self.adapter = JackyunAdapter(db)

    def health_check(self) -> JkyProviderHealth:
        if not settings.jackyun_configured:
            return JkyProviderHealth(
                self.provider, "unconfigured", False, False, False,
                "吉客云 API/MCP 凭证未配置",
            )
        return JkyProviderHealth(
            self.provider, "configured_not_tested", True, True, False,
            "凭证已配置，业务订单权限将在实际同步时验证",
        )

    def fetch_order_rows(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return self.adapter.fetch_sales_order_rows(start, end)


def build_order_providers(db) -> dict[str, JkyOrderProvider]:
    return {
        "jky_web": JkyWebOrderProvider(db),
        "jky_rpa": JkyRpaOrderProvider(),
        "jky_api": JkyApiOrderProvider(db),
    }
