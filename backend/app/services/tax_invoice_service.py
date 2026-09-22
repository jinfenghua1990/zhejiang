"""税务系统官方清单导入与发票台账服务。"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name
from app.adapters.tax_invoice_file import ParsedTaxInvoiceExport, parse_tax_invoice_export
from app.config import settings
from app.core.audit import audit
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.bank import BankTransaction
from app.models.jackyun import JackyunGoodsDocument
from app.models.procurement_chain import ProcurementChainLink
from app.models.purchase import ExternalPurchaseOrder, JackyunPurchaseOrder, JackyunPurchaseOrderLink
from app.models.sales import SalesOrder
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord, TaxInvoiceLink
from app.services.import_lifecycle import (
    filter_active_import,
    filter_lifecycle,
    transition_lifecycle,
    transition_row_status,
)
from app.services.sales_scope import deal_orders_condition, is_deal_status
from app.utils.money import quantize, to_decimal
from sqlalchemy import and_, case, cast, exists, func, or_, String
from sqlalchemy.orm import aliased


def _root() -> Path:
    root = Path(settings.DATA_DIR).resolve() / "tax-invoices"
    root.mkdir(parents=True, exist_ok=True)
    return root


# 进项发票分类 v2（终版）：category 与 processing_status 合并为一个 6+1 值分类字段。
# key → {label: 中文文案, group: 派生组}；group 决定统计口径：
# operating=计入运营成本，reimburse=计入报销成本，excluded=不计入任何报销运营，
# 空（待判断）不属于任何组，单独以 "pending" 口径统计。
CATEGORY_V2 = {
    "goods": {"label": "运营成本：货款发票", "group": "operating"},
    "platform_fee": {"label": "运营成本：平台服务费", "group": "operating"},
    "operating_other": {"label": "运营成本其他", "group": "operating"},
    "reimburse_advance": {"label": "报销：代付", "group": "reimburse"},
    "reimburse_operating": {"label": "报销：运营成本", "group": "reimburse"},
    "excluded": {"label": "不计入任何报销运营", "group": "excluded"},
}
CATEGORY_V2_KEYS = tuple(CATEGORY_V2)
CATEGORY_V2_LABELS = {key: item["label"] for key, item in CATEGORY_V2.items()}
CATEGORY_V2_OPERATING = tuple(key for key, item in CATEGORY_V2.items() if item["group"] == "operating")
CATEGORY_V2_REIMBURSE = tuple(key for key, item in CATEGORY_V2.items() if item["group"] == "reimburse")
CATEGORY_V2_EMPTY_LABEL = "待判断（未分类）"

# 销项发票分类（独立枚举，与进项 6+1 互斥）：给买家开发票 / 给平台开服务费；空 = 待判断。
OUTPUT_CATEGORY = {
    "buyer_sales": {"label": "给买家开发票"},
    "platform_service": {"label": "给平台开服务费"},
}
OUTPUT_CATEGORY_KEYS = tuple(OUTPUT_CATEGORY)
OUTPUT_CATEGORY_LABELS = {key: item["label"] for key, item in OUTPUT_CATEGORY.items()}
OUTPUT_CATEGORY_EMPTY_LABEL = "待判断"
# 全量合法 key（列表 category 筛选参数是方向无关的，取两枚举并集）。
CATEGORY_ALL_KEYS = CATEGORY_V2_KEYS + OUTPUT_CATEGORY_KEYS


def category_label(category: str | None) -> str:
    """进项分类 key 的中文文案；空 = 待判断（未分类）。"""
    if not category:
        return CATEGORY_V2_EMPTY_LABEL
    return CATEGORY_V2_LABELS.get(category, category)


def output_category_label(category: str | None) -> str:
    """销项分类 key 的中文文案；空 = 待判断。"""
    if not category:
        return OUTPUT_CATEGORY_EMPTY_LABEL
    return OUTPUT_CATEGORY_LABELS.get(category, category)


def category_label_for_direction(direction: str | None, category: str | None) -> str:
    """按发票方向取分类文案：销项走 OUTPUT_CATEGORY，其余（input/unknown）走进项 6+1。"""
    if direction == "output":
        return output_category_label(category)
    return category_label(category)


def validate_category_for_direction(direction: str | None, category: str) -> None:
    """按方向校验分类 key：进项只收 6+1，销项只收 buyer_sales/platform_service/空；互混即拒绝。"""
    if not category:
        return
    if direction == "output":
        if category in OUTPUT_CATEGORY_KEYS:
            return
        if category in CATEGORY_V2_KEYS:
            raise ValueError(
                "销项发票不能使用进项分类，合法值："
                + "、".join(OUTPUT_CATEGORY_KEYS) + " 或空（待判断）"
            )
        raise ValueError(
            "无效的销项发票类别，合法值：" + "、".join(OUTPUT_CATEGORY_KEYS) + " 或空（待判断）"
        )
    if category in CATEGORY_V2_KEYS:
        return
    if category in OUTPUT_CATEGORY_KEYS:
        raise ValueError(
            "进项发票不能使用销项分类，合法值："
            + "、".join(CATEGORY_V2_KEYS) + " 或空（待判断）"
        )
    raise ValueError(
        "无效的进项发票类别，合法值：" + "、".join(CATEGORY_V2_KEYS) + " 或空（待判断）"
    )


def category_group(category: str | None) -> str:
    """分类的派生组：operating / reimburse / excluded；空或无法识别 → pending（待判断）。"""
    if category in CATEGORY_V2:
        return CATEGORY_V2[category]["group"]
    return "pending"


# 报销类：餐饮/住宿/交通等差旅与费用报销；平台服务类：软件/云服务/仓储/会员等平台服务费。
_REIMBURSEMENT_KEYWORDS = re.compile(
    r"餐饮|餐费|住宿|运输|票价|退票|机票|停车|演唱会|客运|房租|物业|水费|电费|话费"
)
_PLATFORM_SERVICE_KEYWORDS = re.compile(
    r"软件|增值服务|云服务|仓储|会员|商标|经纪代理|广告|信息技术|技术服务|信息系统|信息服务|服务器|网络费"
)


def classify_category_from_items(goods_names: list[str]) -> str:
    """按开票明细归类进项发票；无法识别时返回空（待判断）等待人工调整。"""
    text = " ".join(goods_names or []).strip().lower()
    if not text:
        return ""
    if _REIMBURSEMENT_KEYWORDS.search(text):
        return "reimburse_operating"
    if _PLATFORM_SERVICE_KEYWORDS.search(text):
        return "platform_fee"
    return "goods"


def _derive_processing_status(category: str | None) -> str:
    """category 是唯一事实，processing_status 由类别派生（兼容缓存）：
    运营成本三分类→required；报销两分类与 excluded→not_required；空→pending（待判断）。"""
    if category in CATEGORY_V2_OPERATING:
        return "required"
    if category in CATEGORY_V2_REIMBURSE or category == "excluded":
        return "not_required"
    return "pending"


def _effective_processing_status(row: TaxInvoice) -> str:
    """派生处理结论；历史行类别为空时回退旧 processing_status，兼容既有数据。"""
    if row.category:
        return _derive_processing_status(row.category)
    return row.processing_status or "pending"


def _store_new_file(content: bytes, original_name: str, sha256: str) -> tuple[Path, bool]:
    target = _root() / sha256[:2] / f"{sha256}_{sanitize_name(original_name)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("xb") as output:
            output.write(content)
        return target, True
    except FileExistsError:
        return target, False


def _value(row: dict[str, str], parsed: ParsedTaxInvoiceExport, field: str) -> str:
    header = parsed.mapping.get(field)
    value = str(row.get(header, "") or "").strip() if header else ""
    if value or field != "invoice_number":
        return value
    # 税务数字发票导出常把“发票号码”列留空，把实际号码放在“数电发票号码”。
    for fallback in ("数电发票号码", "电子发票号码", "发票号", "发票编号"):
        value = str(row.get(fallback, "") or "").strip()
        if value:
            return value
    return ""


def _decimal(value: str) -> Decimal | None:
    text = str(value or "").strip().replace(",", "").replace("，", "")
    text = text.replace("¥", "").replace("￥", "").replace("元", "").replace(" ", "")
    if not text or text in {"-", "--", "/"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def _datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(settings.TZ))
        except ValueError:
            pass
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, pattern).replace(tzinfo=ZoneInfo(settings.TZ))
            except ValueError:
                continue
    return None


def _direction(value: str, header_hint: str) -> str:
    text = f"{value} {header_hint}".lower()
    if any(token in text for token in ("进项", "购进", "收票", "供应商")):
        return "input"
    if any(token in text for token in ("销项", "销售", "开给", "客户")):
        return "output"
    return "unknown"


def _infer_batch_directions(
    rows: list[dict[str, str]], parsed: ParsedTaxInvoiceExport
) -> dict[int, str]:
    """从单批次购销方税号结构补足“进销项”未提供方向的官方清单。

    仅在一侧税号占比至少 80%、另一侧明显变化时推断；不满足条件就保持 unknown。
    这样可识别同一批次的进项/销项文件，但不会把混合批次强行归类。
    """
    seller_counts = Counter(
        _value(row, parsed, "seller_tax_id")
        for row in rows
        if _value(row, parsed, "seller_tax_id")
    )
    buyer_counts = Counter(
        _value(row, parsed, "buyer_tax_id")
        for row in rows
        if _value(row, parsed, "buyer_tax_id")
    )
    total = len(rows)
    if total < 2 or not seller_counts or not buyer_counts:
        return {}
    seller_tax, seller_count = seller_counts.most_common(1)[0]
    buyer_tax, buyer_count = buyer_counts.most_common(1)[0]
    seller_ratio = seller_count / total
    buyer_ratio = buyer_count / total
    if buyer_ratio >= 0.8 and (len(seller_counts) > 1 or seller_ratio < 0.8):
        return {index: "input" for index in range(len(rows)) if buyer_tax}
    if seller_ratio >= 0.8 and (len(buyer_counts) > 1 or buyer_ratio < 0.8):
        return {index: "output" for index in range(len(rows)) if seller_tax}
    return {}


def _status(value: str, total: Decimal | None = None, is_positive: str | None = None, remark: str | None = None) -> str:
    """底层票据状态只表达“这张凭证本身”的性质，不再把蓝字票的红冲生命周期混入 status。

    - issued：蓝字有效发票（即使后续被部分/全额红冲，蓝字凭证本身仍保留）；
    - red：红字发票/负数发票；
    - void：作废；
    - unknown：待确认。

    “蓝字已部分/全额红冲”由 _red_trace_context 动态派生。
    """
    text = str(value or "")
    remark_text = str(remark or "")
    pos = str(is_positive).strip() if is_positive is not None else ""

    if any(token in text for token in ("作废", "无效", "失效")):
        return "void"

    if pos in ("否", "负数", "红字", "红冲"):
        return "red"
    if pos in ("是", "正数", "正常"):
        return "issued"

    # 金额是最稳定的凭证颜色兜底：负数=红字，正数=蓝字。
    if total is not None:
        if total < 0:
            return "red"
        if total > 0:
            return "issued"

    # 缺金额时才退回状态/备注文字判断。备注里的“被红冲蓝字发票号码”
    # 通常出现在红字凭证上，但没有金额时不能把它当作蓝字票“已红冲”的唯一事实。
    if any(token in text for token in ("红字", "红冲", "冲红")):
        return "red"
    if any(token in remark_text for token in ("红字发票信息确认单", "被红冲蓝字", "对应蓝字")):
        return "red"
    if any(token in text for token in ("正常", "有效", "已开", "开具")):
        return "issued"
    return "unknown"


# 红字票通常在备注中记录对应蓝字发票号码和《红字发票信息确认单》编号。
# 兼容数电/全电/纸票导出中常见字段文案差异；不做名称/金额猜配。
_RED_BLUE_REF_PATTERNS = (
    re.compile(r"(?:被红冲|对应)(?:蓝字)?(?:数电|全电)?(?:发票|票)?号码[：:]\s*([A-Za-z0-9_-]+)"),
    re.compile(r"蓝字(?:数电|全电)?发票号码[：:]\s*([A-Za-z0-9_-]+)"),
)
_RED_NOTICE_RE = re.compile(r"红字发票信息确认单(?:编号|号码)?[：:]\s*([A-Za-z0-9_-]+)")
_RED_HINT_RE = re.compile(r"已红冲|部分红冲|全额红冲|被红冲|红字发票")
_RED_TOLERANCE = Decimal("0.01")
MANUAL_RED_BLUE_TARGET_TYPE = "red_blue_invoice"
RED_BANK_REFUND_TARGET_TYPE = "bank_refund_transaction"
RED_FUTURE_OFFSET_TARGET_TYPE = "future_invoice_offset"
RED_PERSONAL_REFUND_TARGET_TYPE = "personal_refund"
RED_OTHER_SETTLEMENT_TARGET_TYPE = "other_red_settlement"
RED_SETTLEMENT_TARGET_TYPES = (
    RED_BANK_REFUND_TARGET_TYPE,
    RED_FUTURE_OFFSET_TARGET_TYPE,
    RED_PERSONAL_REFUND_TARGET_TYPE,
    RED_OTHER_SETTLEMENT_TARGET_TYPE,
)
_NON_DEDUCTIBLE_VAT_RE = re.compile(r"餐饮|餐费|娱乐|居民日常服务|贷款服务|贷款利息")

_FINANCE_META_KEY = "_finance_meta"
_VAT_DEDUCTIBLE_MANUAL_VALUES = ("pending", "deductible", "non_deductible")
_INPUT_VAT_TRANSFER_MANUAL_VALUES = (
    "required_confirmation", "completed", "not_required_unverified_blue", "not_applicable"
)


def _finance_meta(row: TaxInvoice) -> dict:
    raw = row.raw if isinstance(row.raw, dict) else {}
    meta = raw.get(_FINANCE_META_KEY)
    return dict(meta) if isinstance(meta, dict) else {}


def _write_finance_meta(row: TaxInvoice, updates: dict) -> None:
    raw = dict(row.raw) if isinstance(row.raw, dict) else {}
    meta = _finance_meta(row)
    for key, value in updates.items():
        if value in (None, ""):
            meta.pop(key, None)
        else:
            meta[key] = value
    raw[_FINANCE_META_KEY] = meta
    row.raw = raw




def _invoice_number_text(row: TaxInvoice) -> str:
    return f"{row.invoice_code or ''}{row.invoice_number or ''}"


def _normalize_invoice_ref(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _raw_invoice_text(row: TaxInvoice) -> str:
    raw = row.raw if isinstance(row.raw, dict) else {}
    return " ".join(
        str(value or "")
        for key, value in raw.items()
        if key != _FINANCE_META_KEY
    )


def _red_reference(row: TaxInvoice) -> str:
    raw = row.raw if isinstance(row.raw, dict) else {}
    direct_keys = (
        "被红冲蓝字发票号码", "被红冲蓝字数电发票号码", "被红冲蓝字全电发票号码",
        "对应蓝字发票号码", "蓝字发票号码",
    )
    for key in direct_keys:
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    text = _raw_invoice_text(row)
    for pattern in _RED_BLUE_REF_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def _red_notice_number(row: TaxInvoice) -> str:
    raw = row.raw if isinstance(row.raw, dict) else {}
    for key in ("红字发票信息确认单编号", "红字发票信息确认单号码", "确认单编号"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    match = _RED_NOTICE_RE.search(_raw_invoice_text(row))
    return match.group(1) if match else ""


def _invoice_color(row: TaxInvoice) -> str:
    """凭证颜色：blue / red / unknown。颜色与后续是否被红冲是两个维度。"""
    raw = row.raw if isinstance(row.raw, dict) else {}
    pos = str(raw.get("是否正数发票") or raw.get("是否正数") or "").strip()
    total = Decimal(str(row.total_amount)) if row.total_amount is not None else None
    if pos in ("否", "负数", "红字", "红冲"):
        return "red"
    if pos in ("是", "正数", "正常"):
        return "blue"
    # 历史数据没有“是否正数发票”时，已有 status=red 是比金额正负更强的红冲事实；
    # 不能仅因金额为正就擅自恢复成蓝字有效票。
    if row.status == "red":
        return "red"
    if row.status == "issued":
        return "blue"
    if total is not None:
        if total < 0:
            return "red"
        if total > 0:
            return "blue"
    return "unknown"


def _blue_has_red_hint(row: TaxInvoice) -> bool:
    if _invoice_color(row) != "blue":
        return False
    raw = row.raw if isinstance(row.raw, dict) else {}
    text = " ".join(
        str(raw.get(key) or "")
        for key in ("发票状态", "状态", "备注", "发票备注")
    )
    return bool(_RED_HINT_RE.search(text))


def is_effective_for_accounting(invoice: TaxInvoice) -> bool:
    """静态会计凭证资格：有效蓝字/红字可入账，作废/待确认不入净额。

    红蓝配对、部分/全额红冲和异常状态需要数据库上下文，使用 red_accounting_context。
    """
    return invoice.status not in {"void", "unknown"} and _invoice_color(invoice) in {"blue", "red"}


def red_accounting_context(db: Session, rows: list[TaxInvoice]) -> dict[int, dict]:
    """按税务凭证口径建立红蓝发票关系，并给出可审计的净额元数据。

    原则：
    - 蓝字原票与红字冲销票都永久保留；
    - 仅按红字票明确记录的蓝字号码建立关系，不按名称/金额猜配；
    - 蓝票可被多张红票部分冲销；
    - 红字合计超过蓝字原额时标记异常，不静默截断事实金额；
    - 蓝票显示已红冲但系统尚未取得对应红字票时，先标异常待补，不把该蓝票继续当正常有效金额。
    """
    if not rows:
        return {}

    # 配对必须跨筛选、跨月份、跨分页查找，因此使用整个可见发票池，而不是只看当前 rows。
    try:
        pool = filter_visible_invoices(db.query(TaxInvoice)).all()
    except Exception:
        pool = db.query(TaxInvoice).all()

    color_by_id = {row.id: _invoice_color(row) for row in pool}
    pool_by_id = {row.id: row for row in pool}
    manual_red_links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type == MANUAL_RED_BLUE_TARGET_TYPE,
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .order_by(TaxInvoiceLink.id.desc())
        .all()
    )
    manual_blue_by_red: dict[int, TaxInvoice] = {}
    for link in manual_red_links:
        red = pool_by_id.get(link.invoice_id)
        blue = pool_by_id.get(link.target_id)
        if red is None or blue is None:
            continue
        if color_by_id.get(red.id) != "red" or color_by_id.get(blue.id) != "blue":
            continue
        # 每张红字票只接受最新一条有效人工确认关系；设置接口会把旧关系软撤销。
        manual_blue_by_red.setdefault(red.id, blue)

    blue_index: dict[str, list[TaxInvoice]] = {}
    for row in pool:
        if color_by_id.get(row.id) != "blue":
            continue
        for candidate in (row.invoice_number, _invoice_number_text(row)):
            key = _normalize_invoice_ref(candidate)
            if key:
                blue_index.setdefault(key, []).append(row)

    red_to_blue: dict[int, TaxInvoice] = {}
    red_pair_state: dict[int, str] = {}
    reds_by_blue: dict[int, list[TaxInvoice]] = {}
    for red in pool:
        if color_by_id.get(red.id) != "red":
            continue
        manual_blue = manual_blue_by_red.get(red.id)
        if manual_blue is not None:
            red_to_blue[red.id] = manual_blue
            red_pair_state[red.id] = "paired_manual"
            reds_by_blue.setdefault(manual_blue.id, []).append(red)
            continue
        ref = _normalize_invoice_ref(_red_reference(red))
        if not ref:
            red_pair_state[red.id] = "unpaired"
            continue
        candidate_rows = blue_index.get(ref, [])
        seen_candidate_ids: set[int] = set()
        candidates = []
        for candidate in candidate_rows:
            if candidate.id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate.id)
            candidates.append(candidate)
        # 同号歧义时只用购销方税号/方向缩窄；仍不唯一就拒绝猜测。
        if len(candidates) > 1:
            narrowed = [
                blue for blue in candidates
                if blue.direction == red.direction
                and (not red.seller_tax_id or not blue.seller_tax_id or red.seller_tax_id == blue.seller_tax_id)
                and (not red.buyer_tax_id or not blue.buyer_tax_id or red.buyer_tax_id == blue.buyer_tax_id)
            ]
            if len(narrowed) == 1:
                candidates = narrowed
        if len(candidates) == 1:
            blue = candidates[0]
            red_to_blue[red.id] = blue
            red_pair_state[red.id] = "paired"
            reds_by_blue.setdefault(blue.id, []).append(red)
        else:
            red_pair_state[red.id] = "ambiguous" if candidates else "unpaired"

    result: dict[int, dict] = {}
    for row in rows:
        color = _invoice_color(row)
        amount = Decimal(str(row.total_amount or 0))
        base = {
            "invoiceColor": color,
            "redStatus": "none",
            "redPairStatus": "none",
            "redPairMethod": "",
            "redRelatedInvoiceId": None,
            "redRelatedInvoiceNo": "",
            "redRelatedInvoiceIds": [],
            "redRelatedInvoiceNos": [],
            "redNoticeNo": _red_notice_number(row),
            "redRelatedInvoiceDate": None,
            "redRelatedInvoicePeriod": "",
            "redCrossPeriod": False,
            "redOffsetAmount": "0.00",
            "remainingAfterRedAmount": str(max(amount, Decimal("0")).quantize(Decimal("0.01"))),
            "accountingNetAmount": "0.00",
            "accountingNetIncluded": False,
            "accountingException": "",
        }

        if row.status == "void":
            result[row.id] = {
                **base,
                "invoiceStatusLabel": "作废发票",
                "redStatus": "void",
                "remainingAfterRedAmount": "0.00",
            }
            continue
        if row.status == "unknown" or color == "unknown":
            result[row.id] = {
                **base,
                "invoiceStatusLabel": "待确认发票",
                "redStatus": "unknown",
                "remainingAfterRedAmount": "0.00",
            }
            continue

        if color == "red":
            blue = red_to_blue.get(row.id)
            pair_state = red_pair_state.get(row.id, "unpaired")
            label = ("红字发票（人工确认冲销）" if pair_state == "paired_manual" else "红字发票（冲销）") if blue else (
                "红字发票（关联歧义）" if pair_state == "ambiguous" else "红字发票（待关联蓝字）"
            )
            exception = "" if blue else (
                "红字发票对应蓝字号码命中多张发票，需人工确认"
                if pair_state == "ambiguous"
                else "红字发票未识别到对应蓝字发票，请补充/核对蓝字票"
            )
            result[row.id] = {
                **base,
                "invoiceStatusLabel": label,
                "redStatus": "red_invoice" if blue else f"red_invoice_{pair_state}",
                "redPairMethod": "manual" if pair_state == "paired_manual" else ("official_ref" if blue else ""),
                "redPairStatus": pair_state,
                "redRelatedInvoiceId": blue.id if blue else None,
                "redRelatedInvoiceNo": _invoice_number_text(blue) if blue else _red_reference(row),
                "redRelatedInvoiceIds": [blue.id] if blue else [],
                "redRelatedInvoiceNos": [_invoice_number_text(blue)] if blue else (
                    [_red_reference(row)] if _red_reference(row) else []
                ),
                "redRelatedInvoiceDate": blue.issue_date.isoformat() if blue and blue.issue_date else None,
                "redRelatedInvoicePeriod": blue.issue_date.strftime("%Y-%m") if blue and blue.issue_date else "",
                "redCrossPeriod": bool(
                    blue and blue.issue_date and row.issue_date
                    and (blue.issue_date.year, blue.issue_date.month) != (row.issue_date.year, row.issue_date.month)
                ),
                "remainingAfterRedAmount": "0.00",
                # 红字凭证本身按负数参与净额；即使系统暂未找到蓝票也保留真实负数事实并同时报警。
                "accountingNetAmount": str(amount.quantize(Decimal("0.01"))),
                "accountingNetIncluded": True,
                "accountingException": exception,
            }
            continue

        # 蓝字发票：根据所有明确指向它的红字票汇总冲销额。
        reds = reds_by_blue.get(row.id, [])
        offset = sum(
            (abs(Decimal(str(red.total_amount or 0))) for red in reds),
            Decimal("0"),
        )
        remaining = max(amount - offset, Decimal("0"))
        related_ids = [red.id for red in reds]
        related_nos = [_invoice_number_text(red) for red in reds]
        notice_nos = [value for value in (_red_notice_number(red) for red in reds) if value]

        if offset > amount + _RED_TOLERANCE:
            red_status = "over_red_offset"
            label = "蓝字发票（红冲金额异常）"
            pair_state = "over_offset"
            exception = f"累计红字金额 {offset} 超过蓝字原额 {amount}"
        elif offset >= amount - _RED_TOLERANCE and offset > 0:
            red_status = "fully_red_offset"
            label = "蓝字发票（已全额红冲）"
            pair_state = "paired"
            exception = ""
        elif offset > _RED_TOLERANCE:
            red_status = "partially_red_offset"
            label = "蓝字发票（部分红冲）"
            pair_state = "paired"
            exception = ""
        elif _blue_has_red_hint(row):
            red_status = "blue_red_pending"
            label = "蓝字发票（已红冲，待关联红字票）"
            pair_state = "counterpart_missing"
            exception = "税务原始状态显示该蓝字票已红冲，但当前系统未找到对应红字发票"
        else:
            red_status = "none"
            label = "蓝字发票（有效）"
            pair_state = "none"
            exception = ""

        # 如果官方原始状态已经提示红冲但缺红字票，不能继续把蓝票当正常有效金额；
        # 先从“有效净额”中隔离，直到红字票补齐后由红蓝签名金额自动抵消。
        include = red_status != "blue_red_pending"
        result[row.id] = {
            **base,
            "invoiceStatusLabel": label,
            "redStatus": red_status,
            "redPairStatus": pair_state,
            "redPairMethod": "manual" if any(red_pair_state.get(red.id) == "paired_manual" for red in reds) else ("official_ref" if reds else ""),
            "redRelatedInvoiceId": related_ids[0] if len(related_ids) == 1 else None,
            "redRelatedInvoiceNo": "、".join(related_nos),
            "redRelatedInvoiceIds": related_ids,
            "redRelatedInvoiceNos": related_nos,
            "redNoticeNo": "、".join(dict.fromkeys(notice_nos)),
            "redOffsetAmount": str(offset.quantize(Decimal("0.01"))),
            "remainingAfterRedAmount": str(remaining.quantize(Decimal("0.01"))),
            # 蓝字原额仍作为正数入账；配对红字票以负数入账，汇总时自然形成净额。
            "accountingNetAmount": str(amount.quantize(Decimal("0.01"))) if include else "0.00",
            "accountingNetIncluded": include,
            "accountingException": exception,
        }
    return result


def _red_trace_context(rows: list[TaxInvoice], db: Session | None = None) -> dict[int, dict]:
    """兼容入口；有 db 时使用跨账期、跨分页的标准红蓝配对。"""
    if db is not None:
        return red_accounting_context(db, rows)

    # 无数据库上下文时只做单票静态标识，不猜红蓝关系。
    result: dict[int, dict] = {}
    for row in rows:
        color = _invoice_color(row)
        amount = Decimal(str(row.total_amount or 0))
        if row.status == "void":
            label, red_status = "作废发票", "void"
        elif color == "red":
            label, red_status = "红字发票（待关联蓝字）", "red_invoice_unpaired"
        elif color == "blue":
            label, red_status = "蓝字发票（有效）", "none"
        else:
            label, red_status = "待确认发票", "unknown"
        result[row.id] = {
            "invoiceColor": color,
            "invoiceStatusLabel": label,
            "redStatus": red_status,
            "redPairStatus": "unpaired" if color == "red" else "none",
            "redRelatedInvoiceId": None,
            "redRelatedInvoiceNo": _red_reference(row) if color == "red" else "",
            "redRelatedInvoiceIds": [],
            "redRelatedInvoiceNos": [],
            "redNoticeNo": _red_notice_number(row),
            "redOffsetAmount": "0.00",
            "remainingAfterRedAmount": str(max(amount, Decimal("0")).quantize(Decimal("0.01"))),
            "accountingNetAmount": str(amount.quantize(Decimal("0.01"))) if is_effective_for_accounting(row) else "0.00",
            "accountingNetIncluded": is_effective_for_accounting(row),
            "accountingException": "红字发票未在当前上下文关联蓝字票" if color == "red" else "",
        }
    return result



def red_blue_candidates(
    db: Session, red_invoice_id: int, keyword: str = "", limit: int = 30
) -> list[dict]:
    red = db.get(TaxInvoice, red_invoice_id)
    if red is None:
        raise LookupError(f"tax_invoices #{red_invoice_id} 不存在")
    if _invoice_color(red) != "red":
        raise ValueError("只有红字发票需要人工指定对应蓝字发票")
    query = filter_visible_invoices(
        db.query(TaxInvoice).filter(
            TaxInvoice.id != red.id,
            TaxInvoice.direction == red.direction,
        )
    )
    rows = query.order_by(TaxInvoice.issue_date.desc().nullslast(), TaxInvoice.id.desc()).limit(300).all()
    needle = str(keyword or "").strip().lower()
    result = []
    red_amount = abs(Decimal(str(red.total_amount or 0)))
    for blue in rows:
        if _invoice_color(blue) != "blue":
            continue
        haystack = " ".join([
            blue.invoice_number or "", blue.invoice_code or "", blue.seller_name or "",
            blue.buyer_name or "", blue.seller_tax_id or "", blue.buyer_tax_id or "",
        ]).lower()
        if needle and needle not in haystack:
            continue
        tax_match = (
            (not red.seller_tax_id or not blue.seller_tax_id or red.seller_tax_id == blue.seller_tax_id)
            and (not red.buyer_tax_id or not blue.buyer_tax_id or red.buyer_tax_id == blue.buyer_tax_id)
        )
        amount = Decimal(str(blue.total_amount or 0))
        result.append({
            "invoiceId": blue.id,
            "invoiceNumber": blue.invoice_number or "",
            "invoiceCode": blue.invoice_code or "",
            "issueDate": blue.issue_date.isoformat() if blue.issue_date else None,
            "sellerName": blue.seller_name or "",
            "buyerName": blue.buyer_name or "",
            "totalAmount": str(quantize(amount)),
            "taxPartyMatched": tax_match,
            "amountCanCoverRed": amount + _RED_TOLERANCE >= red_amount,
        })
    result.sort(
        key=lambda item: (
            not item["taxPartyMatched"],
            not item["amountCanCoverRed"],
            abs(Decimal(item["totalAmount"]) - red_amount),
        )
    )
    return result[: max(1, min(limit, 100))]


def set_manual_red_blue_relation(
    db: Session, red_invoice_id: int, blue_invoice_id: int, note: str = "", actor: str = "system"
) -> dict:
    red = db.get(TaxInvoice, red_invoice_id)
    blue = db.get(TaxInvoice, blue_invoice_id)
    if red is None or blue is None:
        raise LookupError("红字或蓝字发票不存在")
    if _invoice_color(red) != "red":
        raise ValueError("指定来源必须是红字发票")
    if _invoice_color(blue) != "blue":
        raise ValueError("指定目标必须是蓝字发票")
    if red.direction != blue.direction:
        raise ValueError("红字与蓝字发票方向不一致")
    if red.seller_tax_id and blue.seller_tax_id and red.seller_tax_id != blue.seller_tax_id:
        raise ValueError("红字与蓝字发票销方税号不一致")
    if red.buyer_tax_id and blue.buyer_tax_id and red.buyer_tax_id != blue.buyer_tax_id:
        raise ValueError("红字与蓝字发票购方税号不一致")

    existing = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id == red.id,
        TaxInvoiceLink.target_type == MANUAL_RED_BLUE_TARGET_TYPE,
    ).all()
    chosen = None
    for link in existing:
        if link.target_id == blue.id:
            chosen = link
            link.match_method = "manual"
            link.confirmed = True
            link.note = note or "人工确认红蓝发票关系"
        elif link.match_method != "rejected" or link.confirmed:
            link.match_method = "rejected"
            link.confirmed = False
            link.note = "被新的人工红蓝关联替代"
    if chosen is None:
        chosen = TaxInvoiceLink(
            invoice_id=red.id,
            target_type=MANUAL_RED_BLUE_TARGET_TYPE,
            target_id=blue.id,
            allocated_amount=None,
            match_method="manual",
            confirmed=True,
            note=note or "人工确认红蓝发票关系",
        )
        db.add(chosen)
        db.flush()
    audit(
        db, actor, "tax.invoice.red_blue_relation.set", "tax_invoices", red.id,
        {"blueInvoiceId": blue.id, "note": note}, commit=False,
    )
    db.commit()
    return red_accounting_context(db, [red]).get(red.id, {})


def clear_manual_red_blue_relation(
    db: Session, red_invoice_id: int, actor: str = "system"
) -> dict:
    red = db.get(TaxInvoice, red_invoice_id)
    if red is None:
        raise LookupError(f"tax_invoices #{red_invoice_id} 不存在")
    links = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id == red.id,
        TaxInvoiceLink.target_type == MANUAL_RED_BLUE_TARGET_TYPE,
        TaxInvoiceLink.match_method != "rejected",
    ).all()
    for link in links:
        link.match_method = "rejected"
        link.confirmed = False
        link.note = "人工解除红蓝关联"
    audit(
        db, actor, "tax.invoice.red_blue_relation.clear", "tax_invoices", red.id,
        {"count": len(links)}, commit=False,
    )
    db.commit()
    return red_accounting_context(db, [red]).get(red.id, {})


def _red_settlement_context(db: Session, rows: list[TaxInvoice]) -> dict[int, dict]:
    red_rows = [row for row in rows if _invoice_color(row) == "red"]
    if not red_rows:
        return {}
    ids = [row.id for row in red_rows]
    links = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id.in_(ids),
        TaxInvoiceLink.target_type.in_(RED_SETTLEMENT_TARGET_TYPES),
        TaxInvoiceLink.match_method != "rejected",
        TaxInvoiceLink.confirmed.is_(True),
    ).order_by(TaxInvoiceLink.id).all()
    bank_ids = {
        link.target_id for link in links if link.target_type == RED_BANK_REFUND_TARGET_TYPE
    }
    banks = {
        row.id: row
        for row in db.query(BankTransaction).filter(BankTransaction.id.in_(bank_ids)).all()
    } if bank_ids else {}
    invoice_ids = {
        link.target_id for link in links if link.target_type == RED_FUTURE_OFFSET_TARGET_TYPE
    }
    target_invoices = {
        row.id: row
        for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    } if invoice_ids else {}
    grouped: dict[int, list[TaxInvoiceLink]] = {}
    for link in links:
        grouped.setdefault(link.invoice_id, []).append(link)
    result = {}
    for red in red_rows:
        target = abs(Decimal(str(red.total_amount or 0)))
        items = []
        settled = Decimal("0")
        for link in grouped.get(red.id, []):
            amount = max(Decimal(str(link.allocated_amount or 0)), Decimal("0"))
            settled += amount
            item = {
                "linkId": link.id,
                "type": link.target_type,
                "amount": str(quantize(amount)),
                "note": link.note or "",
            }
            if link.target_type == RED_BANK_REFUND_TARGET_TYPE:
                txn = banks.get(link.target_id)
                item.update({
                    "targetId": link.target_id,
                    "targetLabel": (
                        f"{txn.txn_date.isoformat()} 收款 {quantize(to_decimal(txn.amount))} {txn.counterparty_name}"
                        if txn else f"银行入账 #{link.target_id}"
                    ),
                    "txnDate": txn.txn_date.isoformat() if txn else None,
                    "serialNo": txn.serial_no if txn else "",
                    "voucherNo": txn.voucher_no if txn else "",
                })
            elif link.target_type == RED_FUTURE_OFFSET_TARGET_TYPE:
                invoice = target_invoices.get(link.target_id)
                item.update({
                    "targetId": link.target_id,
                    "targetLabel": f"后续发票 {invoice.invoice_number}" if invoice else f"后续发票 #{link.target_id}",
                })
            elif link.target_type == RED_PERSONAL_REFUND_TARGET_TYPE:
                item.update({"targetId": None, "targetLabel": "退回个人垫付款人"})
            else:
                item.update({"targetId": None, "targetLabel": "其他人工处理"})
            items.append(item)
        remaining = max(target - settled, Decimal("0"))
        over = max(settled - target, Decimal("0"))
        if target <= _RED_TOLERANCE:
            status = "not_applicable"
        elif over > _RED_TOLERANCE:
            status = "over_settled"
        elif remaining <= _RED_TOLERANCE:
            status = "settled"
        elif settled > 0:
            status = "partial"
        else:
            status = "unsettled"
        result[red.id] = {
            "redSettlementStatus": status,
            "redSettlementTargetAmount": str(quantize(target)),
            "redSettledAmount": str(quantize(settled)),
            "redSettlementRemainingAmount": str(quantize(remaining)),
            "redSettlementOverAmount": str(quantize(over)),
            "redSettlements": items,
        }
    return result


def red_refund_candidates(
    db: Session, red_invoice_id: int, keyword: str = "", limit: int = 30
) -> list[dict]:
    red = db.get(TaxInvoice, red_invoice_id)
    if red is None:
        raise LookupError(f"tax_invoices #{red_invoice_id} 不存在")
    if _invoice_color(red) != "red" or red.direction != "input":
        raise ValueError("只有进项红字发票需要匹配供应商退款")
    query = db.query(BankTransaction).filter(BankTransaction.direction == "in")
    needle = str(keyword or "").strip()
    if needle:
        query = query.filter(
            or_(
                BankTransaction.counterparty_name.ilike(f"%{needle}%"),
                BankTransaction.serial_no.ilike(f"%{needle}%"),
                BankTransaction.voucher_no.ilike(f"%{needle}%"),
                BankTransaction.summary.ilike(f"%{needle}%"),
            )
        )
    txns = query.order_by(BankTransaction.txn_date.desc(), BankTransaction.id.desc()).limit(200).all()
    supplier = re.sub(r"\s+", "", red.seller_name or "").lower()
    target = abs(Decimal(str(red.total_amount or 0)))
    rows = []
    for txn in txns:
        name = re.sub(r"\s+", "", txn.counterparty_name or "").lower()
        amount = Decimal(str(txn.amount or 0))
        rows.append({
            "txnId": txn.id,
            "txnDate": txn.txn_date.isoformat(),
            "amount": str(quantize(amount)),
            "counterpartyName": txn.counterparty_name or "",
            "serialNo": txn.serial_no or "",
            "voucherNo": txn.voucher_no or "",
            "summary": txn.summary or "",
            "supplierMatched": bool(supplier and name and (supplier in name or name in supplier)),
            "amountMatched": abs(amount - target) <= _RED_TOLERANCE,
        })
    rows.sort(key=lambda row: (not row["supplierMatched"], not row["amountMatched"], row["txnDate"]), reverse=False)
    return rows[: max(1, min(limit, 100))]


def add_red_settlement(
    db: Session,
    red_invoice_id: int,
    settlement_type: str,
    amount: Decimal | str | int,
    target_id: int | None = None,
    note: str = "",
    actor: str = "system",
) -> dict:
    red = db.get(TaxInvoice, red_invoice_id)
    if red is None:
        raise LookupError(f"tax_invoices #{red_invoice_id} 不存在")
    if _invoice_color(red) != "red":
        raise ValueError("只有红字发票可以登记红冲结算")
    if settlement_type not in RED_SETTLEMENT_TARGET_TYPES:
        raise ValueError("无效的红冲结算类型")
    value = quantize(to_decimal(amount))
    if value <= 0:
        raise ValueError("结算金额必须大于 0")
    if settlement_type == RED_BANK_REFUND_TARGET_TYPE:
        if target_id is None:
            raise ValueError("银行退款必须指定入账流水")
        txn = db.get(BankTransaction, target_id)
        if txn is None or txn.direction != "in":
            raise ValueError("只能关联公司银行账户的收入流水")
        already_used = sum(
            (
                Decimal(str(link.allocated_amount or 0))
                for link in db.query(TaxInvoiceLink).filter(
                    TaxInvoiceLink.target_type == RED_BANK_REFUND_TARGET_TYPE,
                    TaxInvoiceLink.target_id == target_id,
                    TaxInvoiceLink.match_method != "rejected",
                    TaxInvoiceLink.confirmed.is_(True),
                ).all()
            ),
            Decimal("0"),
        )
        txn_amount = max(Decimal(str(txn.amount or 0)), Decimal("0"))
        if already_used + value - txn_amount > _RED_TOLERANCE:
            raise ValueError(
                f"该银行退款流水可用金额不足：流水 {quantize(txn_amount)}，已占用 {quantize(already_used)}"
            )
    elif settlement_type == RED_FUTURE_OFFSET_TARGET_TYPE:
        if target_id is None:
            raise ValueError("后续货款抵扣必须指定后续蓝字进项发票")
        future = db.get(TaxInvoice, target_id)
        if future is None or future.direction != "input" or _invoice_color(future) != "blue":
            raise ValueError("后续抵扣目标必须是蓝字进项发票")
        if red.seller_tax_id and future.seller_tax_id and red.seller_tax_id != future.seller_tax_id:
            raise ValueError("后续抵扣发票与红字发票供应商税号不一致")
        if red.issue_date and future.issue_date and future.issue_date < red.issue_date:
            raise ValueError("后续货款抵扣目标的开票日期不能早于红字发票")
        already_offset = sum(
            (
                Decimal(str(link.allocated_amount or 0))
                for link in db.query(TaxInvoiceLink).filter(
                    TaxInvoiceLink.target_type == RED_FUTURE_OFFSET_TARGET_TYPE,
                    TaxInvoiceLink.target_id == target_id,
                    TaxInvoiceLink.match_method != "rejected",
                    TaxInvoiceLink.confirmed.is_(True),
                ).all()
            ),
            Decimal("0"),
        )
        future_available = max(effective_invoice_amount_after_red(db, future) - already_offset, Decimal("0"))
        if value - future_available > _RED_TOLERANCE:
            raise ValueError(
                f"后续发票可抵扣余额不足：有效金额 {quantize(effective_invoice_amount_after_red(db, future))}，已占用 {quantize(already_offset)}"
            )
    else:
        target_id = red.id

    current = _red_settlement_context(db, [red]).get(red.id, {})
    remaining = Decimal(str(current.get("redSettlementRemainingAmount") or abs(Decimal(str(red.total_amount or 0)))))
    if value - remaining > _RED_TOLERANCE:
        raise ValueError(f"结算金额超过红字发票尚未处理金额 {quantize(remaining)}")

    link = TaxInvoiceLink(
        invoice_id=red.id,
        target_type=settlement_type,
        target_id=int(target_id or red.id),
        allocated_amount=value,
        match_method="manual",
        confirmed=True,
        note=note,
    )
    # 同一银行流水/后续发票只能登记一条；个人退款/其他处理复用唯一行并累加更新。
    existing = db.query(TaxInvoiceLink).filter(
        TaxInvoiceLink.invoice_id == red.id,
        TaxInvoiceLink.target_type == settlement_type,
        TaxInvoiceLink.target_id == int(target_id or red.id),
    ).first()
    if existing is not None:
        if existing.match_method == "rejected":
            existing.match_method = "manual"
            existing.confirmed = True
            existing.allocated_amount = value
            existing.note = note
            link = existing
        else:
            raise ValueError("该红冲结算对象已经登记")
    else:
        db.add(link)
        db.flush()
    audit(
        db, actor, "tax.invoice.red_settlement.add", "tax_invoices", red.id,
        {"type": settlement_type, "targetId": target_id, "amount": str(value), "note": note},
        commit=False,
    )
    db.commit()
    return _red_settlement_context(db, [red]).get(red.id, {})


def remove_red_settlement(
    db: Session, red_invoice_id: int, link_id: int, actor: str = "system"
) -> dict:
    red = db.get(TaxInvoice, red_invoice_id)
    link = db.get(TaxInvoiceLink, link_id)
    if red is None or link is None or link.invoice_id != red.id or link.target_type not in RED_SETTLEMENT_TARGET_TYPES:
        raise LookupError("红冲结算记录不存在")
    link.match_method = "rejected"
    link.confirmed = False
    audit(
        db, actor, "tax.invoice.red_settlement.remove", "tax_invoices", red.id,
        {"linkId": link.id, "type": link.target_type}, commit=False,
    )
    db.commit()
    return _red_settlement_context(db, [red]).get(red.id, {})


def _vat_context(db: Session, rows: list[TaxInvoice], red_context: dict[int, dict]) -> dict[int, dict]:
    """派生进项抵扣/红冲转出状态；人工财务确认优先于自动推断。

    自动规则只负责提出“待确认”事实，不擅自声称已完成申报。
    人工覆盖写入 raw[_finance_meta]，原始税务导入字段仍原样保留。
    """
    if not rows:
        return {}
    row_map = {row.id: row for row in rows}
    related_ids = {
        int(ctx["redRelatedInvoiceId"])
        for ctx in red_context.values()
        if ctx.get("redRelatedInvoiceId")
    }
    for related in db.query(TaxInvoice).filter(TaxInvoice.id.in_(related_ids)).all() if related_ids else []:
        row_map.setdefault(related.id, related)
    line_map = invoice_line_summaries(db, list(row_map.values()))
    result: dict[int, dict] = {}
    for row in rows:
        if row.direction != "input":
            result[row.id] = {
                "vatDeductibleStatus": "not_applicable",
                "vatDeductibleAmount": "0.00",
                "vatDeductibleManual": False,
                "inputVatTransferStatus": "not_applicable",
                "inputVatTransferAmount": "0.00",
                "inputVatTransferManual": False,
            }
            continue

        source = row
        ctx = red_context.get(row.id, {})
        if ctx.get("invoiceColor") == "red" and ctx.get("redRelatedInvoiceId"):
            source = row_map.get(int(ctx["redRelatedInvoiceId"]), row)

        line_text = " ".join(
            str(item.get("goodsName") or "")
            for item in (line_map.get(source.id, {}).get("lineItems") or [])
        )
        text = f"{_raw_invoice_text(source)} {line_text}"
        source_tax_amount = abs(Decimal(str(source.tax_amount or 0)))

        source_meta = _finance_meta(source)
        manual_deductible = str(source_meta.get("vatDeductibleStatus") or "")
        if manual_deductible in _VAT_DEDUCTIBLE_MANUAL_VALUES:
            deductible_status = manual_deductible
            deductible_manual = True
            deductible_amount = (
                source_tax_amount if manual_deductible == "deductible" else Decimal("0")
            )
        elif _NON_DEDUCTIBLE_VAT_RE.search(text):
            deductible_status = "non_deductible"
            deductible_manual = False
            deductible_amount = Decimal("0")
        elif source.verified:
            deductible_status = "verified_pending_tax_filing"
            deductible_manual = False
            deductible_amount = source_tax_amount
        else:
            deductible_status = "pending"
            deductible_manual = False
            deductible_amount = Decimal("0")

        transfer_status = "not_applicable"
        transfer_amount = Decimal("0")
        transfer_manual = False
        if ctx.get("invoiceColor") == "red" and ctx.get("redRelatedInvoiceId"):
            blue = source
            if blue.verified:
                transfer_status = "required_confirmation"
                transfer_amount = abs(Decimal(str(row.tax_amount or blue.tax_amount or 0)))
            else:
                transfer_status = "not_required_unverified_blue"

            row_meta = _finance_meta(row)
            manual_transfer = str(row_meta.get("inputVatTransferStatus") or "")
            if manual_transfer in _INPUT_VAT_TRANSFER_MANUAL_VALUES:
                transfer_status = manual_transfer
                transfer_manual = True
                manual_amount = row_meta.get("inputVatTransferAmount")
                if manual_amount not in (None, ""):
                    transfer_amount = abs(to_decimal(manual_amount))

        result[row.id] = {
            "vatDeductibleStatus": deductible_status,
            "vatDeductibleAmount": str(quantize(deductible_amount)),
            "vatDeductibleManual": deductible_manual,
            "inputVatTransferStatus": transfer_status,
            "inputVatTransferAmount": str(quantize(transfer_amount)),
            "inputVatTransferManual": transfer_manual,
        }
    return result


def set_vat_review(
    db: Session,
    invoice_id: int,
    *,
    vat_deductible_status: str | None = None,
    input_vat_transfer_status: str | None = None,
    input_vat_transfer_amount: Decimal | str | int | None = None,
    note: str = "",
    actor: str = "system",
) -> dict:
    """人工确认进项抵扣/红冲进项税转出事实，并保留审计。

    deductible 状态写在蓝字原票；红字转出状态写在红字凭证自身。
    空值表示不修改该维度。
    """
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    if invoice.direction != "input":
        raise ValueError("只有进项发票可以维护进项税状态")

    updates: dict[str, object] = {}
    if vat_deductible_status is not None:
        if vat_deductible_status not in _VAT_DEDUCTIBLE_MANUAL_VALUES:
            raise ValueError("进项抵扣状态仅支持 pending / deductible / non_deductible")
        if _invoice_color(invoice) == "red":
            raise ValueError("红字发票的可抵扣属性继承对应蓝字票，请在蓝字原票维护")
        if vat_deductible_status == "deductible":
            line_text = " ".join(
                str(item.get("goodsName") or "")
                for item in (
                    invoice_line_summaries(db, [invoice]).get(invoice.id, {}).get("lineItems") or []
                )
            )
            if _NON_DEDUCTIBLE_VAT_RE.search(f"{_raw_invoice_text(invoice)} {line_text}"):
                raise ValueError("该发票内容属于当前规则明确不可抵扣的进项项目，不能人工改为可抵扣")
        updates["vatDeductibleStatus"] = vat_deductible_status

    if input_vat_transfer_status is not None:
        if input_vat_transfer_status not in _INPUT_VAT_TRANSFER_MANUAL_VALUES:
            raise ValueError("无效的进项税转出状态")
        if _invoice_color(invoice) != "red":
            raise ValueError("进项税转出确认只适用于红字进项发票")
        updates["inputVatTransferStatus"] = input_vat_transfer_status
        if input_vat_transfer_amount is not None:
            amount = abs(quantize(to_decimal(input_vat_transfer_amount)))
            updates["inputVatTransferAmount"] = str(amount)

    if note:
        updates["vatReviewNote"] = note
    if not updates:
        raise ValueError("没有需要更新的进项税状态")

    _write_finance_meta(invoice, updates)
    audit(
        db, actor, "tax.invoice.vat_review.set", "tax_invoices", invoice.id,
        {
            "vatDeductibleStatus": vat_deductible_status,
            "inputVatTransferStatus": input_vat_transfer_status,
            "inputVatTransferAmount": (
                str(input_vat_transfer_amount) if input_vat_transfer_amount is not None else None
            ),
            "note": note,
        },
        commit=False,
    )
    db.commit()
    red_ctx = red_accounting_context(db, [invoice])
    return {
        **_vat_context(db, [invoice], red_ctx).get(invoice.id, {}),
        "invoiceId": invoice.id,
    }


def effective_invoice_amount_after_red(db: Session, invoice: TaxInvoice) -> Decimal:
    """业务匹配/付款核对可继续使用的蓝字净额。

    红字发票本身返回 0；蓝字部分红冲返回未冲余额；全额红冲、待补红字凭证、
    作废/待确认、红冲超额异常均返回 0，避免继续进入采购/银行自动匹配。
    """
    context = red_accounting_context(db, [invoice]).get(invoice.id, {})
    if context.get("invoiceColor") != "blue":
        return Decimal("0")
    if context.get("redStatus") in {
        "fully_red_offset", "blue_red_pending", "over_red_offset", "void", "unknown"
    }:
        return Decimal("0")
    try:
        return max(Decimal(str(context.get("remainingAfterRedAmount") or "0")), Decimal("0"))
    except Exception:
        return Decimal("0")


def is_bank_payment_reconciliation_eligible(
    invoice: TaxInvoice, db: Session | None = None
) -> bool:
    """银行付款核对只允许仍有有效蓝字净额的进项发票。"""
    total = Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else Decimal("0")
    if invoice.direction != "input" or invoice.status in {"void", "unknown"}:
        return False
    if _invoice_color(invoice) != "blue" or total <= 0:
        return False
    if db is None:
        return True
    return effective_invoice_amount_after_red(db, invoice) > 0


def bank_payment_reconciliation_ineligible_reason(
    invoice: TaxInvoice, db: Session | None = None
) -> str:
    """返回不参与银行付款核对的明确财务原因。"""
    if invoice.status == "void":
        return "作废发票不参与银行付款核对"
    if invoice.status == "unknown":
        return "待确认发票不参与银行付款核对"
    if _invoice_color(invoice) == "red":
        return "红字/红冲相关发票不参与银行付款核对；红字发票用于冲减对应蓝字发票"
    total = Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else Decimal("0")
    if total <= 0:
        return "非正数金额发票不参与银行付款核对"
    if invoice.direction != "input":
        return "非进项发票不参与银行付款核对"
    if db is not None:
        context = red_accounting_context(db, [invoice]).get(invoice.id, {})
        red_status = context.get("redStatus")
        if red_status == "fully_red_offset":
            return "蓝字发票已全额红冲，不再参与银行付款核对"
        if red_status == "blue_red_pending":
            return "蓝字发票已显示红冲但缺少对应红字发票，待补齐后再核对"
        if red_status == "over_red_offset":
            return "蓝字发票红冲金额异常，需先完成红蓝发票核对"
    return "该发票当前不参与银行付款核对"


def _normalize_row(
    row: dict[str, str], parsed: ParsedTaxInvoiceExport, direction_override: str = ""
) -> tuple[dict, str]:
    number = _value(row, parsed, "invoice_number")
    if not number:
        return {}, "缺少发票号码"
    code = _value(row, parsed, "invoice_code")
    amount = _decimal(_value(row, parsed, "amount_excl_tax"))
    tax_amount = _decimal(_value(row, parsed, "tax_amount"))
    total = _decimal(_value(row, parsed, "total_amount"))
    if total is None and amount is not None and tax_amount is not None:
        total = amount + tax_amount
    if amount is None and total is not None and tax_amount is not None:
        amount = total - tax_amount
    issue_date = _datetime(_value(row, parsed, "issue_date"))
    seller_name = _value(row, parsed, "seller_name")
    seller_tax_id = _value(row, parsed, "seller_tax_id")
    buyer_name = _value(row, parsed, "buyer_name")
    buyer_tax_id = _value(row, parsed, "buyer_tax_id")
    if issue_date is None and total is None and not (seller_name or seller_tax_id or buyer_name or buyer_tax_id):
        return {}, "仅有发票号码，缺少日期、金额或购销方信息"
    direction = _direction(_value(row, parsed, "direction"), parsed.direction_hint)
    if direction == "unknown" and direction_override in ("input", "output"):
        direction = direction_override
    # "是否正数发票"直接判定整张发票是否红冲；备注含"被红冲蓝字"也作为红冲依据
    is_positive = row.get("是否正数发票") or row.get("是否正数") or None
    remark = row.get("备注") or row.get("remark") or ""
    return {
        "invoice_key": f"{code}|{number}",
        "direction": direction,
        "invoice_code": code,
        "invoice_number": number,
        "invoice_type": _value(row, parsed, "invoice_type"),
        "status": _status(_value(row, parsed, "status"), total, is_positive, remark),
        "is_positive": is_positive,
        "issue_date": issue_date,
        "seller_name": seller_name,
        "seller_tax_id": seller_tax_id,
        "buyer_name": buyer_name,
        "buyer_tax_id": buyer_tax_id,
        "amount_excl_tax": amount,
        "tax_amount": tax_amount,
        "total_amount": total,
        "currency": _value(row, parsed, "currency") or "CNY",
        "related_order_ref": _value(row, parsed, "related_order_ref"),
    }, ""


def _deactivate_source_ref_links(db: Session, invoice: TaxInvoice, reason: str) -> None:
    """来源订单号失去唯一/有效依据时，只撤销系统 source_ref 确认；人工关联保持不动。"""
    rows = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id == invoice.id,
            TaxInvoiceLink.match_method == "source_ref",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .all()
    )
    for link in rows:
        link.confirmed = False
        link.confidence = None
        link.note = reason


def _sync_after_source_ref_deactivation(
    db: Session,
    invoice: TaxInvoice,
    *,
    reason: str,
    fallback_status: str,
) -> str:
    """撤销自动来源证据后，以仍然有效的业务链接重新计算状态。

    人工 confirmed 链接是比导入文件中的来源提示更强的业务事实，不能因为
    source_ref 缺失、歧义或失效而被覆盖。若没有任何有效业务链接，才使用
    调用方给出的 fallback_status。
    """
    db.flush()
    remaining = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id == invoice.id,
            TaxInvoiceLink.target_type.in_(ALLOWED_LINK_TARGET_TYPES),
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .order_by(TaxInvoiceLink.id)
        .first()
    )
    if remaining is not None:
        status = sync_business_match_status(db, invoice, remaining.target_type, note=reason)
    else:
        status = fallback_status
        invoice.match_status = status
        if reason and reason not in (invoice.match_note or ""):
            invoice.match_note = f"{invoice.match_note}；{reason}" if invoice.match_note else reason
    return status


def _auto_link(db: Session, invoice: TaxInvoice, related_ref: str, direction: str) -> bool:
    """按税务清单明确订单号自动关联。

    保持历史布尔返回契约：True=已自动关联，False=未关联；
    是否需要人工确认由 invoice.match_status == "needs_review" 表达。
    只有整张发票金额和状态最终确定后才能调用。
    """
    ref = related_ref.strip()
    if not ref:
        return False

    candidates: list[tuple[str, int]] = []
    if direction in ("input", "unknown"):
        # 税务清单只给订单号时，必须把所有渠道同号采购单都纳入候选。
        # 不能用 .first() 猜一个，否则 1688 / 拼多多 / 淘宝同号时会错误自动关联。
        for external in db.query(ExternalPurchaseOrder).filter_by(external_order_id=ref).all():
            candidates.append(("external_purchase_order", external.id))
        for jpo in db.query(JackyunPurchaseOrder).filter_by(purch_no=ref).all():
            candidates.append(("jackyun_purchase_order", jpo.id))
    if direction in ("output", "unknown"):
        for sales in (
            db.query(SalesOrder)
            .filter(SalesOrder.order_no == ref, deal_orders_condition())
            .all()
        ):
            candidates.append(("sales_order", sales.id))

    if len(candidates) != 1:
        reason = (
            f"关联单号 {ref} 未找到业务对象，待人工确认"
            if not candidates
            else f"关联单号 {ref} 命中多个采购渠道或业务对象，待人工确认"
        )
        _deactivate_source_ref_links(db, invoice, reason)
        _sync_after_source_ref_deactivation(
            db,
            invoice,
            reason=reason,
            fallback_status="needs_review",
        )
        return False

    invoice_total = quantize(to_decimal(invoice.total_amount))
    if invoice.status != "issued" or invoice_total <= 0:
        # 红冲、作废、待确认和非正数票不参与采购/销售订单金额匹配；
        # 若历史版本曾自动挂过 source_ref，要撤销自动确认，避免旧链接继续计入业务匹配。
        reason = (
            "票据状态待确认，不执行来源订单自动关联"
            if invoice.status == "unknown"
            else "票据已非有效正数发票，撤销来源订单自动关联"
        )
        _deactivate_source_ref_links(db, invoice, reason)
        _sync_after_source_ref_deactivation(
            db,
            invoice,
            reason=reason,
            fallback_status="needs_review" if invoice.status == "unknown" else "unmatched",
        )
        return False

    target_type, target_id = candidates[0]
    exists = (
        db.query(TaxInvoiceLink)
        .filter_by(invoice_id=invoice.id, target_type=target_type, target_id=target_id)
        .first()
    )
    if exists is not None and exists.match_method == "rejected":
        reason = f"关联单号 {ref} 曾被人工解除，待人工确认后重新关联"
        _sync_after_source_ref_deactivation(
            db,
            invoice,
            reason=reason,
            fallback_status="needs_review",
        )
        return False

    if exists:
        exists.allocated_amount = invoice_total
        exists.match_method = "source_ref"
        exists.confidence = Decimal("1.0000")
        exists.confirmed = True
        exists.note = "税务官方清单明确提供关联单号"
    else:
        db.add(TaxInvoiceLink(
            invoice_id=invoice.id,
            target_type=target_type,
            target_id=target_id,
            allocated_amount=invoice_total,
            match_method="source_ref",
            confidence=Decimal("1.0000"),
            confirmed=True,
            note="税务官方清单明确提供关联单号",
        ))
    db.flush()
    sync_business_match_status(db, invoice, target_type)
    invoice.match_note = f"按清单关联单号自动匹配：{target_type}"
    return True


def _serialize_import(row: TaxInvoiceImport) -> dict:
    return {
        "id": row.id,
        "originalName": row.original_name,
        "size": row.size,
        "sha256": row.sha256[:16],
        "sourceSystem": row.source_system,
        "period": f"{row.period_year:04d}-{row.period_month:02d}" if row.period_year and row.period_month else "",
        "sheetName": row.sheet_name,
        "headers": row.headers or [],
        "mapping": row.mapping or {},
        "status": row.status,
        "lifecycle": row.lifecycle,
        "lifecycleChangedAt": row.lifecycle_changed_at.isoformat() if row.lifecycle_changed_at else None,
        "rowCount": row.row_count,
        "recognizedRowCount": row.recognized_row_count,
        "matchedRowCount": row.matched_row_count,
        "needsReviewCount": row.needs_review_count,
        "errorSummary": row.error_summary or "",
        "uploader": row.uploader,
        "importedAt": row.imported_at.isoformat() if row.imported_at else None,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_import(row: TaxInvoiceImport) -> dict:
    return _serialize_import(row)


def _payment_method_context(row: TaxInvoice, bank_status: str) -> dict[str, str]:
    """付款方式唯一派生入口；银行事实优先，平台扣款由人工明确标记。

    财务口径：
    - 全额银行匹配 = 对公；
    - 部分银行匹配 = 对公 + 个人；
    - 无银行匹配 = 个人，除非人工标记平台自动扣款货款；
    - 红冲/作废/待确认等不适用付款核对的票据不强行分类。
    payment_method 只保留人工审计事实；平台自动扣款货款在无银行流水时决定最终展示。
    """
    manual_method = (
        row.payment_method
        if row.direction == "input" and row.payment_method in {"personal", "platform_auto_debit"}
        else ""
    )
    if row.direction != "input":
        final_method = ""
    elif bank_status in {"matched", "overpaid_after_red", "red_overpayment_settled"}:
        final_method = "corporate"
    elif bank_status == "partial":
        final_method = "mixed"
    elif bank_status == "unmatched":
        final_method = manual_method or "personal"
    else:
        final_method = ""
    return {
        "manualPaymentMethod": manual_method,
        "paymentMethod": final_method,
    }


def payment_method_context(row: TaxInvoice, bank_status: str) -> dict[str, str]:
    """公开的付款方式派生入口，供财务报表复用。"""
    return _payment_method_context(row, bank_status)


def _serialize_invoice(row: TaxInvoice, context: dict | None = None, db: Session | None = None) -> dict:
    payload = {
        "id": row.id,
        "direction": row.direction,
        "invoiceCode": row.invoice_code,
        "invoiceNumber": row.invoice_number,
        "invoiceType": row.invoice_type,
        "status": row.status,
        "issueDate": row.issue_date.isoformat() if row.issue_date else None,
        "sellerName": row.seller_name,
        "sellerTaxId": row.seller_tax_id,
        "buyerName": row.buyer_name,
        "buyerTaxId": row.buyer_tax_id,
        "amountExclTax": str(row.amount_excl_tax) if row.amount_excl_tax is not None else None,
        "taxAmount": str(row.tax_amount) if row.tax_amount is not None else None,
        "totalAmount": str(row.total_amount) if row.total_amount is not None else None,
        "currency": row.currency,
        # matchStatus 保留兼容；新代码必须使用 businessMatchStatus，明确这是发票↔业务单据状态。
        "matchStatus": row.match_status,
        "businessMatchStatus": row.match_status,
        "matchNote": row.match_note,
        "businessMatchNote": row.match_note,
        "processingStatus": _effective_processing_status(row),
        "category": row.category or "",
        "sourceImportId": row.source_import_id,
        "sourceRowIndex": row.source_row_index,
        "verified": bool(row.verified),
        "verifiedMonth": row.verified_month or "",
    }
    if context is not None:
        payload.update(context)
    # 兼容字段也必须镜像实时业务匹配状态，禁止旧调用读到数据库缓存旧值。
    payload["matchStatus"] = payload.get("businessMatchStatus", row.match_status)
    # 红字发票的会计分类必须继承对应蓝字原票，避免红字冲减被记到另一个费用科目。
    payload["categoryInherited"] = False
    payload["categoryInheritedFromInvoiceId"] = None
    if db is not None and payload.get("invoiceColor") == "red" and payload.get("redRelatedInvoiceId"):
        blue = db.get(TaxInvoice, int(payload["redRelatedInvoiceId"]))
        if blue is not None:
            payload["category"] = blue.category or ""
            payload["processingStatus"] = _effective_processing_status(blue)
            payload["categoryInherited"] = True
            payload["categoryInheritedFromInvoiceId"] = blue.id

    # categoryLabel 跟随最终 category；销项走独立 OUTPUT_CATEGORY 文案（空 = 待判断）。
    payload["categoryLabel"] = category_label_for_direction(row.direction, payload.get("category") or "")
    # 银行付款核对与发票业务匹配是两个独立域。
    if "bankPaymentStatus" not in payload:
        if row.direction == "input" and db is not None:
            payload.update(_invoice_bank_payment_context(db, [row]).get(row.id, {}))
        else:
            payload.update({
                "bankPaymentStatus": "not_applicable",
                "bankPaidAmount": "0.00",
                "bankRemainingAmount": "0.00",
                "bankOverpaidAmount": "0.00",
                "bankEffectiveInvoiceAmount": "0.00",
            })

    # 付款方式只由“银行付款事实 + 人工付款方式标记”派生。
    # 即使上游 context 带旧字段也在这里覆盖，保证列表/详情/单票一个事实源。
    payload.update(
        _payment_method_context(row, str(payload.get("bankPaymentStatus") or "not_applicable"))
    )
    return payload


def serialize_invoice(row: TaxInvoice, db: Session | None = None) -> dict:
    """序列化单张发票时也实时计算两个独立域，避免返回数据库旧缓存状态。

    - businessMatchStatus：只来自采购/销售业务链接；
    - bankPaymentStatus：只来自 bank_transaction 付款链接。
    """
    if db is None:
        return _serialize_invoice(row)

    red_ctx = _red_trace_context([row], db=db)
    context = {
        **_invoice_business_context(db, [row]).get(row.id, {
            "links": [],
            "purchaseOrderNos": [],
            "inboundNos": [],
            "businessMatchStatus": row.match_status,
            "businessMatchedAmount": "0.00",
            "businessRemainingAmount": str(quantize(to_decimal(row.total_amount))),
            "businessOvermatchedAmount": "0.00",
            "businessMatchException": "",
        }),
        **red_ctx.get(row.id, {}),
        **invoice_line_summaries(db, [row]).get(row.id, {"lineItems": [], "lineItemCount": 0}),
        **_invoice_bank_payment_context(db, [row]).get(row.id, {
            "bankPaymentStatus": "not_applicable",
            "bankPaidAmount": "0.00",
            "bankRemainingAmount": "0.00",
            "bankOverpaidAmount": "0.00",
            "bankEffectiveInvoiceAmount": "0.00",
        }),
        **_red_settlement_context(db, [row]).get(row.id, {}),
        **_vat_context(db, [row], red_ctx).get(row.id, {}),
    }
    if row.direction == "input":
        context["paymentMethod"] = (
            "corporate"
            if context["bankPaymentStatus"] in {"partial", "matched"}
            else ""
        )
        if not row.category:
            context["category"] = classify_category_from_items(
                [str(item.get("goodsName") or "") for item in context.get("lineItems", [])]
            )
    elif row.direction == "output":
        context["paymentMethod"] = row.payment_method or ""

    return _serialize_invoice(row, context, db=db)


def _invoice_bank_payment_context(db: Session, rows: list[TaxInvoice]) -> dict[int, dict]:
    """独立计算进项发票银行付款状态，并把红冲后的退款/冲抵结算回写到“未解决超额”。

    bankOverpaidAmount 是历史超额付款事实；bankOverpaidUnsettledAmount 才是当前仍需处理金额。
    已处理完成后保留历史金额，但状态变为 red_overpayment_settled。
    """
    input_rows = [row for row in rows if row.direction == "input"]
    if not input_rows:
        return {}

    invoice_ids = [row.id for row in input_rows]
    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id.in_(invoice_ids),
            TaxInvoiceLink.target_type == "bank_transaction",
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .all()
    )
    allocated_by_invoice: dict[int, Decimal] = {}
    invoice_map = {row.id: row for row in input_rows}
    for link in links:
        invoice = invoice_map.get(link.invoice_id)
        if invoice is None:
            continue
        amount = (
            Decimal(str(link.allocated_amount))
            if link.allocated_amount is not None
            else Decimal(str(invoice.total_amount or 0))
        )
        allocated_by_invoice[link.invoice_id] = allocated_by_invoice.get(
            link.invoice_id, Decimal("0")
        ) + amount

    red_context = red_accounting_context(db, input_rows)
    related_red_ids = sorted({
        int(red_id)
        for row in input_rows
        for red_id in (red_context.get(row.id, {}).get("redRelatedInvoiceIds") or [])
    })
    related_red_rows = (
        db.query(TaxInvoice).filter(TaxInvoice.id.in_(related_red_ids)).all()
        if related_red_ids else []
    )
    settlement_by_red = _red_settlement_context(db, related_red_rows) if related_red_rows else {}

    result: dict[int, dict] = {}
    tolerance = Decimal("0.01")
    for row in input_rows:
        total = effective_invoice_amount_after_red(db, row)
        actual_paid = max(allocated_by_invoice.get(row.id, Decimal("0")), Decimal("0"))
        overpaid = max(actual_paid - total, Decimal("0"))
        remaining = max(total - actual_paid, Decimal("0"))

        related_ids = red_context.get(row.id, {}).get("redRelatedInvoiceIds") or []
        red_settled = sum(
            (
                Decimal(str(settlement_by_red.get(int(red_id), {}).get("redSettledAmount") or "0"))
                for red_id in related_ids
            ),
            Decimal("0"),
        )
        overpaid_settled = min(overpaid, max(red_settled, Decimal("0")))
        overpaid_unsettled = max(overpaid - overpaid_settled, Decimal("0"))

        if actual_paid > 0 and overpaid > tolerance and _invoice_color(row) == "blue":
            status = (
                "red_overpayment_settled"
                if overpaid_unsettled <= tolerance
                else "overpaid_after_red"
            )
        elif not is_bank_payment_reconciliation_eligible(row, db=db):
            status = "not_applicable"
        elif actual_paid <= 0:
            status = "unmatched"
        elif remaining <= tolerance:
            status = "matched"
        else:
            status = "partial"

        result[row.id] = {
            "bankPaymentStatus": status,
            "bankPaidAmount": str(actual_paid.quantize(Decimal("0.01"))),
            "bankRemainingAmount": str(remaining.quantize(Decimal("0.01"))),
            "bankOverpaidAmount": str(overpaid.quantize(Decimal("0.01"))),
            "bankOverpaidSettledAmount": str(overpaid_settled.quantize(Decimal("0.01"))),
            "bankOverpaidUnsettledAmount": str(overpaid_unsettled.quantize(Decimal("0.01"))),
            "bankEffectiveInvoiceAmount": str(total.quantize(Decimal("0.01"))),
        }
    return result


def _invoice_business_context(db: Session, rows: list[TaxInvoice]) -> dict[int, dict]:
    """只补充发票↔采购/销售业务关联；银行付款链接严禁混入业务 links。"""
    invoice_ids = [row.id for row in rows]
    if not invoice_ids:
        return {}
    business_target_types = (
        "alibaba1688_order",
        "external_purchase_order",
        "jackyun_purchase_order",
        "sales_order",
    )
    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id.in_(invoice_ids),
            TaxInvoiceLink.target_type.in_(business_target_types),
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .order_by(TaxInvoiceLink.id)
        .all()
    )
    by_type: dict[str, set[int]] = {}
    links_by_invoice: dict[int, list[TaxInvoiceLink]] = {}
    for link in links:
        by_type.setdefault(link.target_type, set()).add(link.target_id)
        links_by_invoice.setdefault(link.invoice_id, []).append(link)

    alibaba = {
        row.id: row
        for row in db.query(Alibaba1688Order).filter(Alibaba1688Order.id.in_(by_type.get("alibaba1688_order", set()))).all()
    }
    external = {
        row.id: row
        for row in db.query(ExternalPurchaseOrder).filter(ExternalPurchaseOrder.id.in_(by_type.get("external_purchase_order", set()))).all()
    }
    jackyun = {
        row.id: row
        for row in db.query(JackyunPurchaseOrder).filter(JackyunPurchaseOrder.id.in_(by_type.get("jackyun_purchase_order", set()))).all()
    }
    sales = {
        row.id: row
        for row in db.query(SalesOrder).filter(SalesOrder.id.in_(by_type.get("sales_order", set()))).all()
    }

    # 入库单通过采购链路关联到 1688 订单或本系统采购单；吉客云采购单再通过采购单映射回去。
    external_ids = set(external)
    jpo_links = (
        db.query(JackyunPurchaseOrderLink)
        .filter(JackyunPurchaseOrderLink.jackyun_po_id.in_(by_type.get("jackyun_purchase_order", set())))
        .all()
    )
    external_ids.update(link.po_id for link in jpo_links)
    chain_links = []
    if external_ids or by_type.get("alibaba1688_order"):
        query = db.query(ProcurementChainLink).filter(ProcurementChainLink.target_type == "inbound")
        if external_ids and by_type.get("alibaba1688_order"):
            query = query.filter(
                or_(
                    ProcurementChainLink.external_po_id.in_(external_ids),
                    ProcurementChainLink.order_id.in_(by_type["alibaba1688_order"]),
                )
            )
        elif external_ids:
            query = query.filter(ProcurementChainLink.external_po_id.in_(external_ids))
        else:
            query = query.filter(ProcurementChainLink.order_id.in_(by_type["alibaba1688_order"]))
        chain_links = query.all()
    inbound_ids = {link.target_id for link in chain_links if link.match_method != "rejected"}
    inbound = {
        row.id: row
        for row in db.query(JackyunGoodsDocument).filter(JackyunGoodsDocument.id.in_(inbound_ids)).all()
    }
    inbound_by_target: dict[tuple[str, int], list[str]] = {}
    for link in chain_links:
        if link.match_method == "rejected":
            continue
        target_key = (
            "external_purchase_order", link.external_po_id
        ) if link.external_po_id is not None else ("alibaba1688_order", link.order_id)
        document = inbound.get(link.target_id)
        if document is not None:
            inbound_by_target.setdefault(target_key, []).append(document.goodsdoc_no)
    for link in jpo_links:
        for inbound_no in inbound_by_target.get(("external_purchase_order", link.po_id), []):
            inbound_by_target.setdefault(("jackyun_purchase_order", link.jackyun_po_id), []).append(inbound_no)

    target_maps = {
        "alibaba1688_order": alibaba,
        "external_purchase_order": external,
        "jackyun_purchase_order": jackyun,
        "sales_order": sales,
    }
    invoice_row_map = {row.id: row for row in rows}
    result: dict[int, dict] = {}
    for invoice_id in invoice_ids:
        invoice = invoice_row_map[invoice_id]
        domain_types = (
            (SALES_LINK_TARGET_TYPE,)
            if invoice.direction == "output"
            else PURCHASE_LINK_TARGET_TYPES
        )
        invoice_links = [
            link for link in links_by_invoice.get(invoice_id, [])
            if link.target_type in domain_types
        ]
        purchase_nos: list[str] = []
        inbound_nos: list[str] = []
        link_rows: list[dict] = []
        valid_invoice_links: list[TaxInvoiceLink] = []
        invalid_link_count = 0
        for link in invoice_links:
            target = target_maps.get(link.target_type, {}).get(link.target_id)
            target_exists = target is not None
            target_valid = target_exists and (
                link.target_type != SALES_LINK_TARGET_TYPE
                or is_deal_status(target.order_status)
            )
            if target_valid:
                valid_invoice_links.append(link)
            else:
                invalid_link_count += 1
            if link.target_type == "alibaba1688_order":
                target_no = target.external_order_id if target else f"#{link.target_id}"
                target_label = f"1688采购单 {target_no}"
                if target_valid:
                    purchase_nos.append(target_no)
            elif link.target_type == "external_purchase_order":
                target_no = target.external_order_id if target else f"#{link.target_id}"
                target_label = f"采购单 {target_no}"
                if target_valid:
                    purchase_nos.append(target_no)
            elif link.target_type == "jackyun_purchase_order":
                target_no = (target.purch_no or target.jackyun_purch_id) if target else f"#{link.target_id}"
                target_label = f"吉客云采购单 {target_no}"
                if target_valid:
                    purchase_nos.append(target_no)
            elif link.target_type == "sales_order":
                target_no = target.order_no if target else f"#{link.target_id}"
                target_label = f"销售单 {target_no}"
            else:
                target_no = f"#{link.target_id}"
                target_label = f"业务单 {target_no}"
            if target_valid:
                inbound_nos.extend(inbound_by_target.get((link.target_type, link.target_id), []))
            link_rows.append({
                "id": link.id,
                "targetType": link.target_type,
                "targetId": link.target_id,
                "targetNo": target_no,
                "targetLabel": target_label,
                "targetExists": target_exists,
                "targetValid": target_valid,
                "allocatedAmount": float(link.allocated_amount) if link.allocated_amount is not None else None,
                "matchMethod": link.match_method,
                "confirmed": bool(link.confirmed),
                "note": link.note or "",
            })
        invoice_total = quantize(effective_invoice_amount_after_red(db, invoice))
        explicit_allocated = sum(
            (
                quantize(to_decimal(link.allocated_amount))
                for link in valid_invoice_links
                if link.allocated_amount is not None
            ),
            Decimal("0"),
        )
        has_unknown_allocation = any(link.allocated_amount is None for link in valid_invoice_links)
        duplicate_logical_links = (
            _duplicate_purchase_logical_link_count(db, valid_invoice_links, target_maps)
            if invoice.direction != "output"
            else 0
        )
        business_overmatched = max(explicit_allocated - max(invoice_total, Decimal("0")), Decimal("0"))
        if invoice_total > 0:
            business_matched = min(max(explicit_allocated, Decimal("0")), invoice_total)
            business_remaining = max(invoice_total - business_matched, Decimal("0"))
            if business_overmatched > Decimal("0.01"):
                business_status = "needs_review"
            elif invalid_link_count or has_unknown_allocation or duplicate_logical_links:
                business_status = "needs_review"
            elif business_matched <= Decimal("0"):
                business_status = "needs_review" if invoice.match_status == "needs_review" else "unmatched"
            elif business_remaining <= Decimal("0.01"):
                business_status = "matched"
            else:
                business_status = "partial"
        else:
            business_matched = Decimal("0")
            business_remaining = Decimal("0")
            business_status = (
                "needs_review"
                if business_overmatched > Decimal("0.01")
                or invalid_link_count
                or duplicate_logical_links
                else invoice.match_status
            )

        business_exceptions: list[str] = []
        if duplicate_logical_links:
            business_exceptions.append(
                f"同一逻辑采购单存在 {duplicate_logical_links} 条重复业务链接"
            )
        if business_overmatched > Decimal("0.01"):
            business_exceptions.append(
                f"红冲后有效金额 {quantize(max(invoice_total, Decimal('0')))}，"
                f"历史业务已关联 {quantize(explicit_allocated)}，超额 {quantize(business_overmatched)}"
            )

        result[invoice_id] = {
            "links": link_rows,
            "purchaseOrderNos": list(dict.fromkeys(purchase_nos)),
            "inboundNos": list(dict.fromkeys(inbound_nos)),
            "businessMatchStatus": business_status,
            "businessMatchedAmount": str(quantize(business_matched)),
            "businessRemainingAmount": str(quantize(business_remaining)),
            "businessOvermatchedAmount": str(quantize(business_overmatched)),
            "businessMatchException": "；".join(business_exceptions),
            "duplicateLogicalLinkCount": duplicate_logical_links,
            "invalidLinkCount": invalid_link_count,
        }
    return result


def _ingest_rows(
    db: Session, batch: TaxInvoiceImport, parsed: ParsedTaxInvoiceExport
) -> tuple[int, int, int]:
    """把解析行写入标准台账；新导入与历史批次重处理共用这一段。"""
    direction_overrides = _infer_batch_directions(parsed.rows, parsed)
    records_by_index = {
        record.row_index: record
        for record in db.query(TaxInvoiceImportRecord).filter_by(import_id=batch.id).all()
    }
    # 明细 Sheet 属于可重建的辅助层；重处理时旧明细行保留审计痕迹，但不再参与展示合计。
    for record in records_by_index.values():
        payload = record.payload if isinstance(record.payload, dict) else {}
        if payload.get("__sheetRole") == "detail":
            record.row_status = "deleted"
    recognized = 0
    matched = 0
    needs_review = 0
    # 一票多行（正数行 + 折扣行）时，金额需累加所有明细行；否则最后一行（通常是折扣行）
    # 会把发票总额覆盖成负数，误判为红冲。按 invoice_key 累计不含税金额、税额、价税合计。
    amounts_by_invoice: dict[str, dict] = {}
    processed_invoices: dict[str, TaxInvoice] = {}
    row_indices_by_invoice: dict[str, list[int]] = {}
    related_refs_by_invoice: dict[str, set[str]] = {}
    for row_index, raw in enumerate(parsed.rows, start=1):
        normalized, error = _normalize_row(
            raw, parsed, direction_override=direction_overrides.get(row_index - 1, "")
        )
        record = records_by_index.get(row_index)
        if record is None:
            record = TaxInvoiceImportRecord(import_id=batch.id, row_index=row_index)
            db.add(record)
            records_by_index[row_index] = record
        previous_invoice = db.get(TaxInvoice, record.invoice_id) if record.invoice_id else None
        record.payload = raw
        record.recognition_status = "recognized" if not error else "needs_review"
        record.error_summary = error
        if error:
            record.invoice_id = None
            needs_review += 1
            continue

        invoice_key = normalized["invoice_key"]
        invoice = db.query(TaxInvoice).filter_by(invoice_key=invoice_key).first()
        if (
            invoice is None
            and previous_invoice is not None
            and previous_invoice.source_import_id == batch.id
            and previous_invoice.source_row_index == row_index
        ):
            # 兼容历史批次：解析规则从空的“发票号码”切换到“数电发票号码”时，
            # 沿用原行已有的台账记录，避免同一行产生第二张可见发票。
            invoice = previous_invoice
            invoice.invoice_key = invoice_key
        if invoice is None:
            invoice = TaxInvoice(
                invoice_key=invoice_key,
                invoice_number=normalized["invoice_number"],
            )
            db.add(invoice)
            db.flush()
        for field in (
            "direction", "invoice_code", "invoice_number", "invoice_type", "status", "issue_date",
            "seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id", "currency",
        ):
            value = normalized[field]
            if value not in (None, ""):
                setattr(invoice, field, value)
        # 金额类字段按明细行累加，跳过“合计”行（货物名称为空或为合计/总计）。
        # 只有文件确实带货物明细列时才做这个判断：税务清单类导出是每行一张发票、
        # 没有货物列，若也按“货物名称为空”过滤，会把整张发票的金额全部丢掉。
        goods_keys = (
            "货物或应税劳务名称", "货物或应税劳务、服务名称", "项目名称", "货物名称",
        )
        has_goods_column = any(key in raw for key in goods_keys)
        goods_name = (
            raw.get("货物或应税劳务名称")
            or raw.get("货物或应税劳务、服务名称")
            or raw.get("项目名称")
            or raw.get("货物名称")
            or ""
        )
        is_total_row = has_goods_column and str(goods_name).strip() in ("", "合计", "总计")
        if invoice_key not in amounts_by_invoice:
            amounts_by_invoice[invoice_key] = {
                "amount_excl_tax": Decimal("0"),
                "tax_amount": Decimal("0"),
                "total_amount": Decimal("0"),
                "has_amount": False, "has_tax": False, "has_total": False,
            }
        if not is_total_row:
            acc = amounts_by_invoice[invoice_key]
            amt = normalized["amount_excl_tax"]
            tax = normalized["tax_amount"]
            tot = normalized["total_amount"]
            if isinstance(amt, Decimal):
                acc["amount_excl_tax"] += amt
                acc["has_amount"] = True
            if isinstance(tax, Decimal):
                acc["tax_amount"] += tax
                acc["has_tax"] = True
            if isinstance(tot, Decimal):
                acc["total_amount"] += tot
                acc["has_total"] = True
        invoice.source_import_id = batch.id
        invoice.source_row_index = row_index
        invoice.source_system = "tax_export"
        invoice.raw = raw
        invoice.match_status = invoice.match_status or "unmatched"
        processed_invoices[invoice_key] = invoice
        row_indices_by_invoice.setdefault(invoice_key, []).append(row_index)
        related_ref = str(normalized.get("related_order_ref") or "").strip()
        if related_ref:
            related_refs_by_invoice.setdefault(invoice_key, set()).add(related_ref)
        record.invoice_id = invoice.id
        recognized += 1

    # 所有行处理完后，把累加金额写回发票，并重新判定票据状态（红冲与否看发票总额，不看单行）
    for invoice_key, invoice in processed_invoices.items():
        acc = amounts_by_invoice.get(invoice_key)
        if acc is None:
            continue
        if acc["has_amount"]:
            invoice.amount_excl_tax = acc["amount_excl_tax"]
        if acc["has_tax"]:
            invoice.tax_amount = acc["tax_amount"]
        if acc["has_total"]:
            invoice.total_amount = acc["total_amount"]
        # 状态重判：红冲必须是整张发票（是否正数发票=否），不能因单行折扣误判
        raw = invoice.raw if isinstance(invoice.raw, dict) else {}
        raw_status = raw.get("发票状态") or ""
        is_positive = raw.get("是否正数发票") or raw.get("是否正数")
        remark = raw.get("备注") or raw.get("remark") or ""
        invoice.status = _status(raw_status, invoice.total_amount, is_positive, remark)

    # 金额与票据状态全部最终确定后，才允许按清单订单号自动关联。
    # 同一发票多行只处理一次；多行出现多个不同订单号时严禁逐行乱挂。
    for invoice_key, invoice in processed_invoices.items():
        row_indices = row_indices_by_invoice.get(invoice_key, [])
        refs = related_refs_by_invoice.get(invoice_key, set())
        outcome = "skipped"
        review_reason = ""
        if len(refs) > 1:
            review_reason = "同一张发票的多行出现多个不同关联订单号，待人工确认"
            _deactivate_source_ref_links(db, invoice, review_reason)
            status = _sync_after_source_ref_deactivation(
                db,
                invoice,
                reason=review_reason,
                fallback_status="needs_review",
            )
            outcome = "matched" if status == "matched" else "needs_review"
        elif len(refs) == 1:
            linked = _auto_link(db, invoice, next(iter(refs)), invoice.direction)
            if linked:
                outcome = "matched"
            elif invoice.match_status == "needs_review":
                outcome = "needs_review"
                review_reason = invoice.match_note or "关联订单待人工确认"

        if outcome == "matched":
            matched += len(row_indices)
        elif outcome == "needs_review":
            needs_review += len(row_indices)
            for row_index in row_indices:
                record = records_by_index.get(row_index)
                if record is not None:
                    record.recognition_status = "needs_review"
                    record.error_summary = review_reason

    detail_needs_review = 0
    if parsed.detail_rows:
        detail_parsed = replace(
            parsed,
            sheet_name=parsed.detail_sheet_name,
            headers=parsed.detail_headers,
            mapping=parsed.detail_mapping,
            rows=parsed.detail_rows,
        )
        detail_start = len(parsed.rows)
        for detail_index, raw in enumerate(parsed.detail_rows, start=1):
            row_index = detail_start + detail_index
            record = records_by_index.get(row_index)
            if record is None:
                record = TaxInvoiceImportRecord(import_id=batch.id, row_index=row_index)
                db.add(record)
                records_by_index[row_index] = record
            normalized, error = _normalize_row(raw, detail_parsed)
            record.payload = {
                **raw,
                "__sheetRole": "detail",
                "__sheetName": parsed.detail_sheet_name,
            }
            record.row_status = "active"
            record.error_summary = error
            if error:
                record.recognition_status = "needs_review"
                record.invoice_id = None
                detail_needs_review += 1
                continue
            invoice = processed_invoices.get(normalized["invoice_key"])
            if invoice is None:
                invoice = db.query(TaxInvoice).filter_by(invoice_key=normalized["invoice_key"]).first()
            if invoice is None:
                record.recognition_status = "needs_review"
                record.invoice_id = None
                record.error_summary = "明细行未找到对应的发票基础信息"
                detail_needs_review += 1
                continue
            record.recognition_status = "detail"
            record.invoice_id = invoice.id
            record.error_summary = ""
    needs_review += detail_needs_review

    batch.sheet_name = parsed.sheet_name
    batch.headers = parsed.headers
    batch.mapping = {
        **parsed.mapping,
        "__ignoredRowCount": parsed.ignored_row_count,
        "__sheetRoles": {
            "primary": parsed.sheet_name,
            "summary": parsed.summary_sheet_name,
            "detail": parsed.detail_sheet_name,
        },
        "__summaryValidation": parsed.summary_validation,
    }
    if parsed.detail_sheet_name:
        batch.mapping["__detailHeaders"] = parsed.detail_headers
    batch.row_count = len(parsed.rows)
    batch.recognized_row_count = recognized
    batch.matched_row_count = matched
    batch.needs_review_count = needs_review
    batch.status = "needs_review" if needs_review else "parsed"
    errors: list[str] = []
    if needs_review:
        errors.append(f"{needs_review} 行需要人工核对")
    if parsed.summary_validation.get("status") == "mismatch":
        mismatches = parsed.summary_validation.get("mismatches") or []
        errors.append("税局数据校验异常：" + "；".join(str(item) for item in mismatches))
        batch.status = "needs_review"
    batch.error_summary = "；".join(errors)
    return recognized, matched, needs_review


def import_export(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    actor: str,
    period_year: int = 0,
    period_month: int = 0,
    auto_confirm: bool = False,
) -> tuple[TaxInvoiceImport, bool]:
    if not content:
        raise ValueError("空文件")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        limit = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValueError(f"税务发票清单超过单文件上限（{limit} MiB）")
    original_name = original_name or "tax-invoices.xlsx"
    sha256 = hashlib.sha256(content).hexdigest()
    existing = db.query(TaxInvoiceImport).filter_by(sha256=sha256).first()
    if existing:
        return existing, True

    parsed = parse_tax_invoice_export(content, original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS)
    target, created_file = _store_new_file(content, original_name, sha256)
    now = datetime.now(ZoneInfo(settings.TZ))
    batch = TaxInvoiceImport(
        original_name=original_name,
        stored_path=str(target),
        sha256=sha256,
        size=len(content),
        mime=mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        period_year=period_year,
        period_month=period_month,
        sheet_name=parsed.sheet_name,
        headers=parsed.headers,
        mapping=parsed.mapping,
        uploader=actor,
        lifecycle="active" if auto_confirm else "draft",
        lifecycle_changed_at=now,
        imported_at=now,
    )
    db.add(batch)
    db.flush()

    recognized, matched, needs_review = _ingest_rows(db, batch, parsed)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if created_file:
            target.unlink(missing_ok=True)
        raise
    audit(db, actor, "tax.invoice_import.upload", "tax_invoice_imports", batch.id, {
        "rows": batch.row_count,
        "recognized": batch.recognized_row_count,
        "needsReview": batch.needs_review_count,
    })
    return batch, False


def reprocess_import(db: Session, import_id: int, actor: str = "system") -> TaxInvoiceImport:
    """用当前解析规则重处理一个已保存批次，保留原始行和用户行级删除状态。"""
    batch = db.get(TaxInvoiceImport, import_id)
    if batch is None:
        raise LookupError(f"tax_invoice_imports #{import_id} 不存在")
    if batch.lifecycle == "deleted":
        raise ValueError("回收站批次不能重处理，请先恢复")
    path = Path(batch.stored_path)
    if not path.is_file():
        raise ValueError("找不到该导入的原始文件，无法重处理")
    parsed = parse_tax_invoice_export(
        path.read_bytes(), batch.original_name, max_rows=settings.MAX_JACKYUN_IMPORT_ROWS
    )
    recognized, matched, needs_review = _ingest_rows(db, batch, parsed)
    db.commit()
    audit(
        db,
        actor,
        "tax.invoice_import.reprocess",
        "tax_invoice_imports",
        batch.id,
        {"rows": len(parsed.rows), "recognized": recognized, "matched": matched, "needsReview": needs_review},
    )
    return batch


def list_imports(db: Session, lifecycle: str | None = None, limit: int = 50) -> list[dict]:
    query = db.query(TaxInvoiceImport).order_by(TaxInvoiceImport.id.desc())
    query = filter_lifecycle(query, TaxInvoiceImport, lifecycle)
    rows = query.limit(min(max(limit, 1), 200)).all()
    return [_serialize_import(row) for row in rows]


def confirm_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="active", allowed_from=("draft",),
        actor=actor, audit_action="tax.invoice_import.confirm",
    )


def soft_delete_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="deleted", allowed_from=("draft", "active"),
        actor=actor, audit_action="tax.invoice_import.soft_delete",
    )


def restore_import(db: Session, import_id: int, actor: str) -> TaxInvoiceImport:
    return transition_lifecycle(
        db, TaxInvoiceImport, import_id,
        target="draft", allowed_from=("deleted",),
        actor=actor, audit_action="tax.invoice_import.restore",
    )


def delete_row(db: Session, import_id: int, row_index: int, actor: str) -> TaxInvoiceImportRecord:
    """明细核对：删除单行（可恢复）；对应发票同步从台账隐藏。"""
    return transition_row_status(
        db, TaxInvoiceImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="deleted", actor=actor, audit_action="tax.invoice_import.delete_row",
    )


def restore_row(db: Session, import_id: int, row_index: int, actor: str) -> TaxInvoiceImportRecord:
    return transition_row_status(
        db, TaxInvoiceImportRecord,
        lookup={"import_id": import_id, "row_index": row_index},
        target="active", actor=actor, audit_action="tax.invoice_import.restore_row",
    )


def filter_visible_invoices(query):
    """发票台账可见口径 = 导入 active 且来源明细行未被用户删除。

    供本服务与采购链路共用，避免两处口径漂移。
    无来源行（历史遗留 NULL）的发票保留。
    """
    record = aliased(TaxInvoiceImportRecord)
    query = filter_active_import(query, TaxInvoice, TaxInvoiceImport, TaxInvoice.source_import_id)
    return (
        query.outerjoin(
            record,
            and_(
                record.import_id == TaxInvoice.source_import_id,
                record.row_index == TaxInvoice.source_row_index,
            ),
        )
        .filter(or_(record.id.is_(None), record.row_status != "deleted"))
    )


def list_invoices(
    db: Session,
    *,
    direction: str | None = None,
    status: str | None = None,
    match_status: str | None = None,
    processing_status: str | None = None,
    category: str | None = None,
    verified: bool | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    query = filter_visible_invoices(db.query(TaxInvoice)).order_by(
        TaxInvoice.issue_date.desc().nullslast(), TaxInvoice.id.desc()
    )
    if direction:
        query = query.filter(TaxInvoice.direction == direction)
    if status:
        query = query.filter(TaxInvoice.status == status)
    if match_status:
        if match_status not in {"matched", "partial", "unmatched", "needs_review"}:
            raise ValueError("无效的业务匹配状态")

        purchase_target_valid = or_(
            and_(
                TaxInvoiceLink.target_type == "alibaba1688_order",
                exists().where(Alibaba1688Order.id == TaxInvoiceLink.target_id),
            ),
            and_(
                TaxInvoiceLink.target_type == "external_purchase_order",
                exists().where(ExternalPurchaseOrder.id == TaxInvoiceLink.target_id),
            ),
            and_(
                TaxInvoiceLink.target_type == "jackyun_purchase_order",
                exists().where(JackyunPurchaseOrder.id == TaxInvoiceLink.target_id),
            ),
        )
        sales_target_valid = and_(
            TaxInvoiceLink.target_type == SALES_LINK_TARGET_TYPE,
            exists().where(
                and_(
                    SalesOrder.id == TaxInvoiceLink.target_id,
                    deal_orders_condition(),
                )
            ),
        )

        def _business_stats(target_types: tuple[str, ...], target_valid):
            return (
                db.query(
                    TaxInvoiceLink.invoice_id.label("invoice_id"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    target_valid,
                                    func.coalesce(TaxInvoiceLink.allocated_amount, Decimal("0")),
                                ),
                                else_=Decimal("0"),
                            )
                        ),
                        Decimal("0"),
                    ).label("allocated"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(target_valid, TaxInvoiceLink.allocated_amount.is_(None)),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("unknown_count"),
                    func.coalesce(
                        func.sum(case((target_valid, 0), else_=1)),
                        0,
                    ).label("invalid_count"),
                )
                .filter(
                    TaxInvoiceLink.target_type.in_(target_types),
                    TaxInvoiceLink.match_method != "rejected",
                    TaxInvoiceLink.confirmed.is_(True),
                )
                .group_by(TaxInvoiceLink.invoice_id)
                .subquery()
            )

        purchase_stats = _business_stats(PURCHASE_LINK_TARGET_TYPES, purchase_target_valid)
        sales_stats = _business_stats((SALES_LINK_TARGET_TYPE,), sales_target_valid)
        query = (
            query.outerjoin(
                purchase_stats, purchase_stats.c.invoice_id == TaxInvoice.id
            )
            .outerjoin(
                sales_stats, sales_stats.c.invoice_id == TaxInvoice.id
            )
        )

        purchase_allocated = func.coalesce(
            purchase_stats.c.allocated, Decimal("0")
        )
        sales_allocated = func.coalesce(
            sales_stats.c.allocated, Decimal("0")
        )
        allocated = case(
            (TaxInvoice.direction == "output", sales_allocated),
            else_=purchase_allocated,
        )
        purchase_unknown = func.coalesce(purchase_stats.c.unknown_count, 0)
        sales_unknown = func.coalesce(sales_stats.c.unknown_count, 0)
        unknown_count = case(
            (TaxInvoice.direction == "output", sales_unknown),
            else_=purchase_unknown,
        )
        purchase_invalid = func.coalesce(purchase_stats.c.invalid_count, 0)
        sales_invalid = func.coalesce(sales_stats.c.invalid_count, 0)
        invalid_count = case(
            (TaxInvoice.direction == "output", sales_invalid),
            else_=purchase_invalid,
        )
        invoice_total = func.coalesce(TaxInvoice.total_amount, Decimal("0"))
        zero_status = case(
            (TaxInvoice.match_status == "needs_review", "needs_review"),
            else_="unmatched",
        )
        business_status = case(
            (invoice_total <= 0, TaxInvoice.match_status),
            (invalid_count > 0, "needs_review"),
            (unknown_count > 0, "needs_review"),
            (allocated <= 0, zero_status),
            (invoice_total - allocated <= Decimal("0.01"), "matched"),
            else_="partial",
        )
        query = query.filter(business_status == match_status)
    if processing_status:
        query = query.filter(TaxInvoice.processing_status == processing_status)
    if category is not None:
        # category 按分类枚举过滤（空字符串 = 待判断/未分类）；筛选参数方向无关，收进项+销项并集。
        if category and category not in CATEGORY_ALL_KEYS:
            raise ValueError(
                "无效的发票类别，合法值：进项 " + "、".join(CATEGORY_V2_KEYS)
                + "；销项 " + "、".join(OUTPUT_CATEGORY_KEYS) + "；或空（待判断）"
            )
        query = query.filter(TaxInvoice.category == category)
    if verified is not None:
        query = query.filter(TaxInvoice.verified == verified)
    rows = query.limit(min(max(limit, 1), 500)).offset(max(offset, 0)).all()
    context = _invoice_business_context(db, rows)
    red_context = _red_trace_context(rows, db=db)
    line_context = invoice_line_summaries(db, rows)
    bank_payment_context = _invoice_bank_payment_context(db, rows)
    settlement_context = _red_settlement_context(db, rows)
    vat_context = _vat_context(db, rows, red_context)
    result = []
    for row in rows:
        ctx = {
            **context.get(row.id, {"links": [], "purchaseOrderNos": [], "inboundNos": []}),
            **red_context.get(row.id, {}),
            **line_context.get(row.id, {"lineItems": [], "lineItemCount": 0}),
            **bank_payment_context.get(row.id, {
                "bankPaymentStatus": "not_applicable",
                "bankPaidAmount": "0.00",
                "bankRemainingAmount": "0.00",
                "bankOverpaidAmount": "0.00",
                "bankEffectiveInvoiceAmount": "0.00",
            }),
            **settlement_context.get(row.id, {}),
            **vat_context.get(row.id, {}),
        }
        # 明细兜底识别只面向进项（6+1）；销项分类不按货物名推断，空即待判断。
        if not row.category and row.direction != "output":
            ctx["category"] = classify_category_from_items(
                [str(item.get("goodsName") or "") for item in ctx.get("lineItems", [])]
            )
        result.append(_serialize_invoice(row, ctx, db))
    return result


def summary(db: Session) -> dict:
    rows = filter_visible_invoices(db.query(TaxInvoice)).all()
    red_context = _red_trace_context(rows, db=db)
    business_context = _invoice_business_context(db, rows)
    active_import_ids = [
        row.id for row in db.query(TaxInvoiceImport.id).filter(TaxInvoiceImport.lifecycle == "active").all()
    ]
    source_row_count = (
        db.query(TaxInvoiceImportRecord)
        .filter(
            TaxInvoiceImportRecord.import_id.in_(active_import_ids),
            TaxInvoiceImportRecord.row_status != "deleted",
            TaxInvoiceImportRecord.invoice_id.isnot(None),
        )
        .count()
        if active_import_ids else 0
    )
    raw_input_total = sum(
        (Decimal(str(row.total_amount)) for row in rows if row.direction == "input" and row.total_amount is not None),
        Decimal("0"),
    )
    effective_input_total = sum(
        (
            Decimal(str(red_context.get(row.id, {}).get("accountingNetAmount") or "0"))
            for row in rows
            if row.direction == "input"
        ),
        Decimal("0"),
    )
    # 保留兼容字段名：这里表示“税务状态已显示红冲，但红字凭证尚未入库/关联”的蓝票原额。
    excluded_red_amount = sum(
        (
            Decimal(str(row.total_amount or 0))
            for row in rows
            if row.direction == "input"
            and red_context.get(row.id, {}).get("redStatus") == "blue_red_pending"
        ),
        Decimal("0"),
    )
    out = {
        "total": len(rows),
        "activeBatchCount": len(active_import_ids),
        "sourceRowCount": source_row_count,
        "duplicateRowCount": max(source_row_count - len(rows), 0),
        "byDirection": {"input": 0, "output": 0, "unknown": 0},
        "byStatus": {"issued": 0, "void": 0, "red": 0, "unknown": 0},
        "byMatchStatus": {"matched": 0, "partial": 0, "unmatched": 0, "needs_review": 0},
        "byProcessing": {"pending": 0, "required": 0, "not_required": 0},
        "byCategory": {key: 0 for key in CATEGORY_V2_KEYS} | {"": 0},
        "byCategoryGroup": {"operating": 0, "reimburse": 0, "excluded": 0, "pending": 0},
        "byVerification": {"verified": 0, "unverified": 0},
        "inputVerification": {"verified": 0, "unverified": 0},
        "inputTotalAmount": float(effective_input_total),
        "rawInputTotalAmount": float(raw_input_total),
        "excludedRedAmount": float(excluded_red_amount),
        "byRedStatus": {},
        "redPairExceptionCount": sum(
            1 for row in rows
            if red_context.get(row.id, {}).get("accountingException")
        ),
        "redInvoiceCount": sum(
            1 for row in rows if red_context.get(row.id, {}).get("invoiceColor") == "red"
        ),
        "partiallyRedOffsetBlueCount": sum(
            1 for row in rows if red_context.get(row.id, {}).get("redStatus") == "partially_red_offset"
        ),
        "fullyRedOffsetBlueCount": sum(
            1 for row in rows if red_context.get(row.id, {}).get("redStatus") == "fully_red_offset"
        ),
    }
    for row in rows:
        out["byDirection"][row.direction] = out["byDirection"].get(row.direction, 0) + 1
        out["byStatus"][row.status] = out["byStatus"].get(row.status, 0) + 1
        red_status = str(red_context.get(row.id, {}).get("redStatus") or "none")
        out["byRedStatus"][red_status] = out["byRedStatus"].get(red_status, 0) + 1
        business_status = business_context.get(row.id, {}).get(
            "businessMatchStatus", row.match_status
        )
        out["byMatchStatus"][business_status] = out["byMatchStatus"].get(business_status, 0) + 1
        if row.direction == "input":
            category_source = row
            related_blue_id = red_context.get(row.id, {}).get("redRelatedInvoiceId")
            if red_context.get(row.id, {}).get("invoiceColor") == "red" and related_blue_id:
                category_source = db.get(TaxInvoice, int(related_blue_id)) or row
            processing_status = _effective_processing_status(category_source)
            out["byProcessing"][processing_status] = out["byProcessing"].get(processing_status, 0) + 1
            # 红字发票分类继承对应蓝字票，保证冲减和原费用科目一致。
            category = category_source.category or ""
            out["byCategory"][category] = out["byCategory"].get(category, 0) + 1
            out["byCategoryGroup"][category_group(category)] += 1
        out["byVerification"]["verified" if row.verified else "unverified"] += 1
        if row.direction == "input" and row.status == "issued":
            out["inputVerification"]["verified" if row.verified else "unverified"] += 1
    return out


def list_import_records(db: Session, import_id: int, limit: int = 200) -> list[dict]:
    rows = (
        db.query(TaxInvoiceImportRecord)
        .filter_by(import_id=import_id)
        .order_by(TaxInvoiceImportRecord.row_index)
        .limit(min(max(limit, 1), 1000))
        .all()
    )
    return [{
        "rowIndex": row.row_index,
        "recognitionStatus": row.recognition_status,
        "invoiceId": row.invoice_id,
        "errorSummary": row.error_summary,
        "rowStatus": row.row_status,
        "payload": row.payload,
    } for row in rows]


_LINE_PLACEHOLDERS = {"-", "--", "/", "—", "――", "－"}


def _line_text(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _line_number(payload: dict, key: str) -> Decimal | str | None:
    """数字字段尝试解析成 Decimal（兼容 ¥、逗号、括号负数）；解析失败保留原文本。"""
    text = _line_text(payload, key)
    if text is None or text in _LINE_PLACEHOLDERS:
        return None
    parsed = _decimal(text)
    return parsed if parsed is not None else text


def _line_out(value: Decimal | str | None) -> str | None:
    return str(value) if isinstance(value, Decimal) else value


def _line_text_first(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = _line_text(payload, key)
        if value is not None:
            return value
    return None


def _line_number_first(payload: dict, *keys: str) -> Decimal | str | None:
    for key in keys:
        value = _line_number(payload, key)
        if value is not None:
            return value
    return None


def _parse_line_item(
    payload: dict,
) -> tuple[dict, Decimal | str | None, Decimal | str | None, Decimal | str | None] | None:
    goods_name = _line_text_first(
        payload, "货物或应税劳务名称", "货物或应税劳务、服务名称", "项目名称"
    )
    if goods_name is None:
        return None
    amount = _line_number_first(payload, "金额", "不含税金额")
    tax_amount = _line_number_first(payload, "税额")
    total_amount = _line_number_first(payload, "价税合计", "含税金额")
    return (
        {
            "goodsName": goods_name,
            "spec": _line_text(payload, "规格型号"),
            "unit": _line_text(payload, "单位"),
            "quantity": _line_out(_line_number_first(payload, "数量")),
            "unitPrice": _line_out(_line_number_first(payload, "单价", "不含税单价")),
            "amount": _line_out(amount),
            "taxRate": _line_text(payload, "税率"),
            "taxAmount": _line_out(tax_amount),
            "totalAmount": _line_out(total_amount),
            "remark": _line_text(payload, "备注"),
        },
        amount,
        tax_amount,
        total_amount,
    )


def _norm_num(value) -> str:
    """把 31 / 31.0 / 31.00 归一化为同一字符串，避免重复行因小数尾巴不同而漏去重。"""
    if value in (None, ""):
        return ""
    try:
        return str(Decimal(str(value)).normalize())
    except Exception:
        return str(value)


def _dedupe_line_items(items: list[dict]) -> list[dict]:
    """税务导出清单偶有完全相同的明细行重复出现（同一货物、数量、单价、金额），
    导致明细合计翻倍而与发票头价税合计不一致。按核心字段去重，只保留首次出现。"""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for item in items:
        key = (
            str(item.get("goodsName") or ""),
            str(item.get("spec") or ""),
            str(item.get("unit") or ""),
            _norm_num(item.get("quantity")),
            _norm_num(item.get("unitPrice")),
            _norm_num(item.get("amount")),
            str(item.get("taxRate") or ""),
            _norm_num(item.get("taxAmount")),
            _norm_num(item.get("totalAmount")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def invoice_detail_lines(db: Session, invoice_id: int) -> dict:
    """一张发票的货物明细行 = 清单导入记录中 invoice_id 指向该发票且 active 的全部行。

    一票多行（同一 invoice_key 的多行清单）都指向同一张台账发票；payload 里没有
    「货物或应税劳务名称」键的行不是货物明细行，跳过。
    """
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    records = (
        db.query(TaxInvoiceImportRecord)
        .filter(
            TaxInvoiceImportRecord.invoice_id == invoice_id,
            TaxInvoiceImportRecord.row_status == "active",
        )
        .order_by(TaxInvoiceImportRecord.id)
        .all()
    )
    items: list[dict] = []
    sum_amount = Decimal("0")
    sum_tax = Decimal("0")
    sum_total = Decimal("0")
    has_amount = has_tax = has_total = False
    for record in records:
        payload = record.payload if isinstance(record.payload, dict) else {}
        parsed = _parse_line_item(payload)
        if parsed is None:
            continue
        item, amount, tax_amount, total_amount = parsed
        if isinstance(amount, Decimal):
            sum_amount += amount
            has_amount = True
        if isinstance(tax_amount, Decimal):
            sum_tax += tax_amount
            has_tax = True
        if isinstance(total_amount, Decimal):
            sum_total += total_amount
            has_total = True
        items.append(item)
    items = _dedupe_line_items(items)
    # 去重后重新合计，避免重复行把金额翻倍
    sum_amount = Decimal("0")
    sum_tax = Decimal("0")
    sum_total = Decimal("0")
    has_amount = has_tax = has_total = False
    for item in items:
        amt = item.get("amount")
        tax = item.get("taxAmount")
        tot = item.get("totalAmount")
        if amt not in (None, ""):
            sum_amount += Decimal(str(amt))
            has_amount = True
        if tax not in (None, ""):
            sum_tax += Decimal(str(tax))
            has_tax = True
        if tot not in (None, ""):
            sum_total += Decimal(str(tot))
            has_total = True
    return {
        "items": items,
        "total": len(items),
        "sumAmount": float(sum_amount) if has_amount else None,
        "sumTax": float(sum_tax) if has_tax else None,
        "sumTotal": float(sum_total) if has_total else None,
    }


def invoice_line_summaries(db: Session, rows: list[TaxInvoice], max_items: int = 3) -> dict[int, dict]:
    """为发票列表一次性补充当前导入批次的明细摘要，避免列表逐票请求明细接口。"""
    invoice_ids = [row.id for row in rows]
    import_ids = {row.source_import_id for row in rows if row.source_import_id is not None}
    if not invoice_ids or not import_ids:
        return {}
    row_by_id = {row.id: row for row in rows}
    records = (
        db.query(TaxInvoiceImportRecord)
        .filter(
            TaxInvoiceImportRecord.invoice_id.in_(invoice_ids),
            TaxInvoiceImportRecord.import_id.in_(import_ids),
            TaxInvoiceImportRecord.row_status == "active",
        )
        .order_by(TaxInvoiceImportRecord.invoice_id, TaxInvoiceImportRecord.id)
        .all()
    )
    result = {row.id: {"lineItems": [], "lineItemCount": 0} for row in rows}
    # 先按发票收集全部明细，再统一去重，避免重复行抬高计数
    collected: dict[int, list[dict]] = {row.id: [] for row in rows}
    for record in records:
        row = row_by_id.get(record.invoice_id)
        if row is None or record.import_id != row.source_import_id:
            continue
        payload = record.payload if isinstance(record.payload, dict) else {}
        parsed = _parse_line_item(payload)
        if parsed is None:
            continue
        collected[record.invoice_id].append(parsed[0])
    for invoice_id, items in collected.items():
        unique = _dedupe_line_items(items)
        result[invoice_id]["lineItemCount"] = len(unique)
        result[invoice_id]["lineItems"] = unique[:max_items]
    return result


def set_processing_status(
    db: Session, invoice_id: int, processing_status: str, actor: str = "system"
) -> TaxInvoice:
    """旧状态按钮兼容接口：按状态映射写 v2 分类（required→goods、not_required→reimburse_operating、
    pending→空即待判断），再由分类派生 processing_status；原始发票和采购匹配关系保持不变。"""
    if processing_status not in {"pending", "required", "not_required"}:
        raise ValueError("无效的进项发票处理状态")
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    if invoice.direction != "input":
        raise ValueError("只有进项发票可以设置处理状态")
    status_to_category = {"required": "goods", "not_required": "reimburse_operating", "pending": ""}
    invoice.category = status_to_category[processing_status]
    invoice.processing_status = _derive_processing_status(invoice.category)
    audit(
        db,
        actor,
        "tax.invoice.set_processing_status",
        "tax_invoices",
        invoice.id,
        {"processingStatus": processing_status, "category": invoice.category},
        commit=False,
    )
    db.commit()
    return invoice


def set_invoice_categories(
    db: Session, invoice_ids: list[int], category: str, actor: str = "system"
) -> list[TaxInvoice]:
    """批量设置发票分类，幂等：进项 6+1（空=待判断未分类），销项 buyer_sales/platform_service/空（待判断）；
    分类 key 按发票方向校验，互混（进项传销项 key、销项传进项 key）直接拒绝。"""
    invoices = db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    if not invoices:
        return []
    red_context = red_accounting_context(db, invoices)
    for invoice in invoices:
        if red_context.get(invoice.id, {}).get("invoiceColor") == "red":
            raise ValueError(
                f"红字发票 #{invoice.id} 的财务分类必须继承对应蓝字发票；请先核对红蓝关联，不能单独改红字票类别"
            )
        validate_category_for_direction(invoice.direction, category)
    # 派生处理结论：进项按 6+1 组；销项 key 与空都落 pending（待判断）。
    derived = _derive_processing_status(category)
    for invoice in invoices:
        invoice.category = category
        invoice.processing_status = derived
        audit(
            db, actor, "tax.invoice.set_category", "tax_invoices", invoice.id,
            {"category": category}, commit=False,
        )
    db.commit()
    return invoices


# payment_method 是人工补充事实，不是银行付款事实：
# personal=进项发票个人垫付（或部分对公后的剩余部分个人垫付）；
# platform_auto_debit=平台自动扣款货款；空=未人工标记。
# corporate / mixed 只能由银行付款事实动态派生，禁止人工直接写入。
MANUAL_PAYMENT_METHOD_VALUES = ("personal", "platform_auto_debit", "")
PAYMENT_METHOD_LABELS = {
    "corporate": "对公账户支出",
    "personal": "个人垫付",
    "platform_auto_debit": "平台自动扣款货款",
    "mixed": "对公 + 个人垫付",
    "": "未设置",
}


def set_invoice_payment_methods(
    db: Session, invoice_ids: list[int], payment_method: str, actor: str = "system"
) -> list[TaxInvoice]:
    """人工维护有效正数进项发票的个人或平台自动扣款标记。

    corporate 必须来自已确认银行付款证据；销项不适用。
    部分银行付款后可人工标 personal，表示剩余部分个人垫付，最终派生为 mixed。
    platform_auto_debit 只允许在没有银行付款流水时标记。
    """
    if payment_method not in MANUAL_PAYMENT_METHOD_VALUES:
        raise ValueError("只能人工设置 personal（个人垫付）、platform_auto_debit（平台自动扣款货款）或清除；对公付款必须由已确认银行付款生成")
    invoices = db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()
    if not invoices:
        return []
    for invoice in invoices:
        if invoice.direction != "input":
            if payment_method:
                raise ValueError(f"发票 #{invoice.id} 不是进项发票，不适用个人垫付")
            continue
        if payment_method in {"personal", "platform_auto_debit"}:
            if not is_bank_payment_reconciliation_eligible(invoice, db=db):
                raise ValueError(bank_payment_reconciliation_ineligible_reason(invoice, db=db))
            bank = _invoice_bank_payment_context(db, [invoice]).get(invoice.id, {})
            if bank.get("bankPaymentStatus") == "matched":
                raise ValueError(f"发票 #{invoice.id} 已由银行付款全额核对，不能再标记人工付款方式")
            if payment_method == "platform_auto_debit" and bank.get("bankPaymentStatus") != "unmatched":
                raise ValueError(f"发票 #{invoice.id} 已存在银行付款记录，只能在未匹配银行付款时标记平台自动扣款货款")
    for invoice in invoices:
        invoice.payment_method = payment_method if invoice.direction == "input" else ""
        audit(
            db, actor, "tax.invoice.set_payment_method", "tax_invoices", invoice.id,
            {"manual_payment_method": invoice.payment_method}, commit=False,
        )
    db.commit()
    return invoices


def set_invoice_verified(
    db: Session,
    invoice_id: int,
    verified: bool,
    verified_month: str = "",
    actor: str = "system",
) -> TaxInvoice:
    """统一维护发票认证事实；采购链/API 只能通过本入口修改 TaxInvoice.verified*。"""
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    if verified and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", verified_month):
        raise ValueError("请填写实际认证所属月份，格式 YYYY-MM")
    invoice.verified = bool(verified)
    invoice.verified_month = verified_month if verified else ""
    invoice.verified_at = datetime.now(timezone.utc) if verified else None
    audit(
        db,
        actor,
        "tax.invoice.set_verified",
        "tax_invoices",
        invoice.id,
        {"verified": invoice.verified, "verifiedMonth": invoice.verified_month},
        commit=False,
    )
    db.commit()
    return invoice


PURCHASE_LINK_TARGET_TYPES = ("alibaba1688_order", "external_purchase_order", "jackyun_purchase_order")
SALES_LINK_TARGET_TYPE = "sales_order"
ALLOWED_LINK_TARGET_TYPES = PURCHASE_LINK_TARGET_TYPES + (SALES_LINK_TARGET_TYPE,)
_PURCHASE_LINK_MODELS = {
    "alibaba1688_order": Alibaba1688Order,
    "external_purchase_order": ExternalPurchaseOrder,
    "jackyun_purchase_order": JackyunPurchaseOrder,
}


def _purchase_target_brief(target_type: str, row: Any) -> dict:
    """统一取采购类目标的单号/供应商/日期/金额（金额为原始 Numeric 或 None）。"""
    if target_type == "alibaba1688_order":
        return {
            "orderNo": row.external_order_id or "",
            "supplier": row.seller_company_name or "",
            "orderDate": row.order_time,
            "amount": row.actual_payment,
        }
    if target_type == "external_purchase_order":
        return {
            "orderNo": row.external_order_id or "",
            "supplier": row.supplier_name or "",
            "orderDate": row.ordered_at,
            "amount": row.order_amount if row.order_amount is not None else row.paid_amount,
        }
    return {
        "orderNo": row.purch_no or row.jackyun_purch_id,
        "supplier": row.supplier_name or "",
        "orderDate": _datetime(str((row.raw or {}).get("date") or "")),
        "amount": row.amount,
    }


def _load_purchase_targets(db: Session, target_type: str, target_ids: list[int]) -> dict:
    model = _PURCHASE_LINK_MODELS[target_type]
    if not target_ids:
        return {}
    return {row.id: row for row in db.query(model).filter(model.id.in_(target_ids)).all()}


def purchase_alias_target_refs(
    db: Session,
    target_type: str,
    target_id: int,
    target: Any | None = None,
) -> set[tuple[str, int]]:
    """返回同一逻辑采购单的所有实体引用。

    1688 原始订单 Alibaba1688Order 与采购工作流 ExternalPurchaseOrder(platform=1688)
    只是同一业务事实的两种载体，发票额度/拒绝/占用必须共用。
    """
    refs: set[tuple[str, int]] = {(target_type, target_id)}
    if target_type not in PURCHASE_LINK_TARGET_TYPES:
        return refs
    if target is None:
        model = _PURCHASE_LINK_MODELS[target_type]
        target = db.get(model, target_id)
    if target is None:
        return refs

    if target_type == "alibaba1688_order":
        order_no = str(target.external_order_id or "").strip()
        if order_no:
            aliases = db.query(ExternalPurchaseOrder.id).filter(
                ExternalPurchaseOrder.platform == "1688",
                ExternalPurchaseOrder.external_order_id == order_no,
            ).all()
            refs.update(("external_purchase_order", row[0]) for row in aliases)
    elif target_type == "external_purchase_order":
        platform = str(target.platform or "").strip().lower()
        order_no = str(target.external_order_id or "").strip()
        if platform == "1688" and order_no:
            aliases = db.query(Alibaba1688Order.id).filter(
                Alibaba1688Order.external_order_id == order_no
            ).all()
            refs.update(("alibaba1688_order", row[0]) for row in aliases)
    return refs


def purchase_logical_target_key(
    db: Session,
    target_type: str,
    target_id: int,
    target: Any | None = None,
) -> tuple[tuple[str, int], ...]:
    """稳定表示一笔逻辑采购单；1688 双实体会得到同一个 key。"""
    return tuple(sorted(purchase_alias_target_refs(db, target_type, target_id, target)))


def purchase_alias_links(
    db: Session,
    target_type: str,
    target_id: int,
    *,
    target: Any | None = None,
    invoice_id: int | None = None,
    active_only: bool = False,
) -> list[TaxInvoiceLink]:
    """读取同一逻辑采购单跨实体的发票链接。"""
    refs = purchase_alias_target_refs(db, target_type, target_id, target)
    clauses = [
        and_(
            TaxInvoiceLink.target_type == ref_type,
            TaxInvoiceLink.target_id == ref_id,
        )
        for ref_type, ref_id in refs
    ]
    query = db.query(TaxInvoiceLink).filter(or_(*clauses))
    if invoice_id is not None:
        query = query.filter(TaxInvoiceLink.invoice_id == invoice_id)
    if active_only:
        query = query.filter(
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
    return query.all()


def _duplicate_purchase_logical_link_count(
    db: Session,
    links: list[TaxInvoiceLink],
    target_maps: dict[str, dict[int, Any]] | None = None,
) -> int:
    """同一张发票若通过 1688 双实体重复挂到同一逻辑订单，返回重复链接数。"""
    seen: set[tuple[tuple[str, int], ...]] = set()
    duplicates = 0
    for link in links:
        if link.target_type not in PURCHASE_LINK_TARGET_TYPES:
            continue
        target = (
            target_maps.get(link.target_type, {}).get(link.target_id)
            if target_maps is not None
            else None
        )
        key = purchase_logical_target_key(
            db, link.target_type, link.target_id, target=target
        )
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _purchase_link_occupancy(db: Session) -> dict[tuple[str, int], tuple[int, str]]:
    """业务单占用视图；1688 原始单与工作流副本共享同一占用事实。"""
    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.target_type.in_(ALLOWED_LINK_TARGET_TYPES),
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .all()
    )
    invoice_ids = {link.invoice_id for link in links}
    invoice_map = (
        {row.id: row for row in db.query(TaxInvoice).filter(TaxInvoice.id.in_(invoice_ids)).all()}
        if invoice_ids else {}
    )
    occupancy: dict[tuple[str, int], tuple[int, str]] = {}
    for link in links:
        invoice = invoice_map.get(link.invoice_id)
        if invoice is None:
            continue
        refs = (
            purchase_alias_target_refs(db, link.target_type, link.target_id)
            if link.target_type in PURCHASE_LINK_TARGET_TYPES
            else {(link.target_type, link.target_id)}
        )
        for ref in refs:
            occupancy[ref] = (link.invoice_id, invoice.invoice_number)
    return occupancy


_SALES_BUYER_RAW_KEYS = (
    "customerName", "customer_name", "customerAccount", "customer_account",
    "buyerNick", "buyer_nick", "buyerName", "buyer_name", "buyerAccount",
    "nickName", "shopBuyerAccount", "customerCode", "customer_code",
)


def _issue_month_bounds(invoice: TaxInvoice) -> tuple[datetime | None, datetime | None]:
    """发票开票日期所在月的 [月初, 下月初) 区间；无开票日期返回 (None, None)。"""
    moment = invoice.issue_date
    if moment is None:
        return None, None
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _sales_order_buyer(row: Any) -> str:
    """从 SalesOrder.raw 里尽力取买家/客户（不同采集通道字段名不一）。"""
    raw = row.raw if isinstance(row.raw, dict) else {}
    sources = [raw] + [value for value in raw.values() if isinstance(value, dict)]
    for source in sources:
        for key in _SALES_BUYER_RAW_KEYS:
            text = str(source.get(key) or "").strip()
            if text:
                return text
    return ""


def _sales_link_candidates(db: Session, invoice: TaxInvoice, keyword: str, limit: int) -> list[dict]:
    """销项发票人工关联销售订单的候选；keyword 匹配订单号/买家/客户。

    keyword 为空时优先返回发票开票当月的订单，缺月则回退最近订单。
    排序：订单号精确 > 买家匹配 > 金额接近 > 时间最近；buyer 为买家/客户。
    """
    needle = str(keyword or "").strip()
    lowered = needle.lower()
    red_meta = red_accounting_context(db, [invoice]).get(invoice.id, {})
    if red_meta.get("invoiceColor") == "red":
        return []
    effective_amount = effective_invoice_amount_after_red(db, invoice)
    invoice_amount = effective_amount if effective_amount > 0 else None
    occupancy = _purchase_link_occupancy(db)

    query = db.query(SalesOrder).filter(deal_orders_condition())
    if needle:
        rows = (
            query.filter(
                or_(
                    SalesOrder.order_no.ilike(f"%{lowered}%"),
                    cast(SalesOrder.raw, String).ilike(f"%{lowered}%"),
                )
            )
            .order_by(SalesOrder.ordered_at.desc().nullslast(), SalesOrder.id.desc())
            .limit(200)
            .all()
        )
    else:
        start, end = _issue_month_bounds(invoice)
        month_query = query
        if start is not None:
            month_query = query.filter(SalesOrder.ordered_at >= start, SalesOrder.ordered_at < end)
        rows = (
            month_query.order_by(SalesOrder.ordered_at.desc().nullslast(), SalesOrder.id.desc())
            .limit(50)
            .all()
        )
        if not rows and start is not None:
            rows = (
                query.order_by(SalesOrder.ordered_at.desc().nullslast(), SalesOrder.id.desc())
                .limit(50)
                .all()
            )

    candidates: list[dict] = []
    for row in rows:
        buyer = _sales_order_buyer(row)
        amount = row.order_amount if row.order_amount is not None else row.paid_amount
        amount = Decimal(str(amount)) if amount is not None else None
        diff = abs(amount - invoice_amount) if amount is not None and invoice_amount is not None else None
        linked_invoice_id, linked_invoice_no = occupancy.get((SALES_LINK_TARGET_TYPE, row.id), (None, None))
        ordered_at = row.ordered_at
        if needle and row.order_no == needle:
            rank = 0
        elif needle and buyer and lowered in buyer.lower():
            rank = 1
        else:
            rank = 2
        candidates.append({
            "targetType": SALES_LINK_TARGET_TYPE,
            "targetId": row.id,
            "orderNo": row.order_no,
            "supplier": "",
            "buyer": buyer,
            "orderDate": ordered_at.isoformat()[:10] if ordered_at else None,
            "amount": str(quantize(amount)) if amount is not None else None,
            "amountDiff": str(quantize(diff)) if diff is not None else None,
            "linkedInvoiceId": linked_invoice_id,
            "linkedInvoiceNo": linked_invoice_no,
            "_rank": rank,
            "_diff": diff,
            "_date": ordered_at,
            "_id": row.id,
        })
    candidates.sort(key=lambda item: (
        item["_rank"],
        item["_diff"] if item["_diff"] is not None else Decimal("999999999999"),
        -(item["_date"].timestamp() if item["_date"] else 0),
        -item["_id"],
    ))
    keys = ("targetType", "targetId", "orderNo", "supplier", "buyer", "orderDate", "amount", "amountDiff", "linkedInvoiceId", "linkedInvoiceNo")
    return [{key: item[key] for key in keys} for item in candidates[: min(max(limit, 1), 100)]]


def purchase_link_candidates(
    db: Session,
    invoice_id: int,
    keyword: str = "",
    limit: int = 20,
) -> list[dict]:
    """为一张发票搜人工关联候选。

    销项发票（direction=output）搜销售订单；采购类发票搜采购单
    （1688 文件订单 + 吉客云采购单）。linkedInvoice* 表示该单已被哪张
    发票非 rejected 占用（含本发票自身），用于前端展示占用与「仍要关联」。
    """
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    if invoice.direction == "output":
        return _sales_link_candidates(db, invoice, keyword, limit)

    needle = str(keyword or "").strip()
    lowered = needle.lower()
    invoice_amount = Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else None
    seller = (invoice.seller_name or "").strip()
    occupancy = _purchase_link_occupancy(db)

    def _rank(order_no: str, supplier: str) -> int:
        if needle and order_no == needle:
            return 0
        if seller and supplier and (seller in supplier or supplier in seller):
            return 1
        return 2

    pools: list[tuple[str, list]] = []
    external_query = db.query(ExternalPurchaseOrder)
    if needle:
        external_query = external_query.filter(
            or_(
                ExternalPurchaseOrder.external_order_id.ilike(f"%{lowered}%"),
                ExternalPurchaseOrder.supplier_name.ilike(f"%{lowered}%"),
            )
        ).order_by(ExternalPurchaseOrder.id.desc()).limit(200)
    else:
        if seller:
            external_query = external_query.filter(ExternalPurchaseOrder.supplier_name.ilike(f"%{seller}%"))
        external_query = external_query.order_by(
            ExternalPurchaseOrder.ordered_at.desc().nullslast(), ExternalPurchaseOrder.id.desc()
        ).limit(50)
    pools.append(("external_purchase_order", external_query.all()))

    jackyun_query = db.query(JackyunPurchaseOrder)
    if needle:
        jackyun_query = jackyun_query.filter(
            or_(
                JackyunPurchaseOrder.purch_no.ilike(f"%{lowered}%"),
                JackyunPurchaseOrder.supplier_name.ilike(f"%{lowered}%"),
            )
        ).order_by(JackyunPurchaseOrder.id.desc()).limit(200)
    else:
        if seller:
            jackyun_query = jackyun_query.filter(JackyunPurchaseOrder.supplier_name.ilike(f"%{seller}%"))
        jackyun_query = jackyun_query.order_by(JackyunPurchaseOrder.id.desc()).limit(50)
    pools.append(("jackyun_purchase_order", jackyun_query.all()))

    candidates: list[dict] = []
    for target_type, rows in pools:
        for row in rows:
            brief = _purchase_target_brief(target_type, row)
            amount = Decimal(str(brief["amount"])) if brief["amount"] is not None else None
            diff = abs(amount - invoice_amount) if amount is not None and invoice_amount is not None else None
            linked_invoice_id, linked_invoice_no = occupancy.get((target_type, row.id), (None, None))
            order_date = brief["orderDate"]
            candidates.append({
                "targetType": target_type,
                "targetId": row.id,
                "orderNo": brief["orderNo"],
                "supplier": brief["supplier"],
                "orderDate": order_date.isoformat()[:10] if order_date else None,
                "amount": str(quantize(amount)) if amount is not None else None,
                "amountDiff": str(quantize(diff)) if diff is not None else None,
                "linkedInvoiceId": linked_invoice_id,
                "linkedInvoiceNo": linked_invoice_no,
                "_rank": _rank(brief["orderNo"], brief["supplier"]),
                "_diff": diff,
                "_date": order_date,
                "_id": row.id,
            })
    candidates.sort(key=lambda item: (
        item["_rank"],
        item["_diff"] if item["_diff"] is not None else Decimal("999999999999"),
        -(item["_date"].timestamp() if item["_date"] else 0),
        -item["_id"],
    ))
    keys = ("targetType", "targetId", "orderNo", "supplier", "orderDate", "amount", "amountDiff", "linkedInvoiceId", "linkedInvoiceNo")
    return [{key: item[key] for key in keys} for item in candidates[: min(max(limit, 1), 100)]]


def sync_business_match_status(
    db: Session,
    invoice: TaxInvoice,
    target_type: str,
    note: str = "",
) -> str:
    """按采购/销售业务域的真实 confirmed 分摊重算发票业务匹配缓存。

    这是 TaxInvoice.match_status / match_note 的统一写入口。红冲后若历史明确分摊
    超过当前有效金额，必须进入 needs_review，不能继续伪装成 matched。
    """
    domain_types = (
        PURCHASE_LINK_TARGET_TYPES
        if target_type in PURCHASE_LINK_TARGET_TYPES
        else (SALES_LINK_TARGET_TYPE,)
    )
    links = (
        db.query(TaxInvoiceLink)
        .filter(
            TaxInvoiceLink.invoice_id == invoice.id,
            TaxInvoiceLink.target_type.in_(domain_types),
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        )
        .all()
    )
    invoice_total = quantize(effective_invoice_amount_after_red(db, invoice))
    has_unknown = any(link.allocated_amount is None for link in links)
    allocated = sum(
        (
            quantize(to_decimal(link.allocated_amount))
            for link in links
            if link.allocated_amount is not None
        ),
        Decimal("0"),
    )
    duplicate_logical_links = (
        _duplicate_purchase_logical_link_count(db, links)
        if target_type in PURCHASE_LINK_TARGET_TYPES
        else 0
    )
    overmatched = max(allocated - max(invoice_total, Decimal("0")), Decimal("0"))
    if overmatched > Decimal("0.01"):
        status = "needs_review"
    elif duplicate_logical_links:
        status = "needs_review"
    elif invoice_total <= 0:
        status = "unmatched"
    elif has_unknown:
        status = "needs_review"
    elif allocated <= 0:
        status = "unmatched"
    elif invoice_total - allocated <= Decimal("0.01"):
        status = "matched"
    else:
        status = "partial"
    invoice.match_status = status
    clean_note = (note or "").strip()
    if clean_note and clean_note not in (invoice.match_note or ""):
        invoice.match_note = f"{invoice.match_note}；{clean_note}" if invoice.match_note else clean_note
    if duplicate_logical_links:
        warning = f"同一逻辑采购单存在 {duplicate_logical_links} 条重复业务链接，需人工清理"
        if warning not in (invoice.match_note or ""):
            invoice.match_note = f"{invoice.match_note}；{warning}" if invoice.match_note else warning
    if overmatched > Decimal("0.01"):
        warning = (
            f"红冲后业务关联超额：有效金额 {quantize(max(invoice_total, Decimal('0')))}，"
            f"已关联 {quantize(allocated)}，超额 {quantize(overmatched)}"
        )
        if warning not in (invoice.match_note or ""):
            invoice.match_note = f"{invoice.match_note}；{warning}" if invoice.match_note else warning
    return status


# 兼容旧内部调用；新代码统一使用公开入口。
_sync_business_match_status = sync_business_match_status


def link_purchase_order(
    db: Session,
    invoice_id: int,
    target_type: str,
    target_id: int,
    allocated_amount: Decimal | str | int | None = None,
    note: str = "",
    actor: str = "system",
) -> dict:
    """人工把发票挂到采购单/销售订单（manual+confirmed，软删行复活，同对幂等）。

    销售订单（target_type=sales_order）只允许销项发票；采购类目标不允许销项发票。
    """
    if target_type not in ALLOWED_LINK_TARGET_TYPES:
        raise ValueError("非法关联目标类型")
    invoice = db.get(TaxInvoice, invoice_id)
    if invoice is None:
        raise LookupError(f"tax_invoices #{invoice_id} 不存在")
    if target_type == SALES_LINK_TARGET_TYPE and invoice.direction != "output":
        raise ValueError("只有销项发票可以人工关联销售订单")
    if target_type in PURCHASE_LINK_TARGET_TYPES and invoice.direction == "output":
        raise ValueError("销项发票不能人工关联采购单，请关联销售订单")
    red_meta = red_accounting_context(db, [invoice]).get(invoice.id, {})
    if red_meta.get("invoiceColor") == "red":
        raise ValueError("红字发票用于冲减对应蓝字发票，业务关联继承蓝字票，不应单独关联订单")

    if target_type == SALES_LINK_TARGET_TYPE:
        target = (
            db.query(SalesOrder)
            .filter(SalesOrder.id == target_id)
            .with_for_update()
            .one_or_none()
        )
        if target is None:
            raise LookupError(f"销售订单不存在：#{target_id}")
        if not is_deal_status(target.order_status):
            raise ValueError("已取消/关闭/退款等非成交销售订单不能关联销项发票")
        order_no = target.order_no
        default_note = "人工关联销售订单"
        audit_action = "tax_invoice.link_sales"
    else:
        model = _PURCHASE_LINK_MODELS[target_type]
        target = (
            db.query(model)
            .filter(model.id == target_id)
            .with_for_update()
            .one_or_none()
        )
        if target is None:
            raise LookupError(f"采购单不存在：{target_type} #{target_id}")
        order_no = _purchase_target_brief(target_type, target)["orderNo"]
        default_note = "人工关联采购单"
        audit_action = "tax_invoice.link_purchase"

    # 锁定发票，避免并发手工关联同时通过剩余额度检查。
    db.refresh(invoice, with_for_update=True)
    link = (
        db.query(TaxInvoiceLink)
        .filter_by(invoice_id=invoice.id, target_type=target_type, target_id=target_id)
        .first()
    )

    domain_types = PURCHASE_LINK_TARGET_TYPES if target_type in PURCHASE_LINK_TARGET_TYPES else (SALES_LINK_TARGET_TYPE,)
    invoice_total = quantize(effective_invoice_amount_after_red(db, invoice))
    if invoice_total <= 0:
        raise ValueError("发票已全额红冲、红冲状态异常或无有效蓝字余额，不能继续关联业务单")

    if target_type in PURCHASE_LINK_TARGET_TYPES:
        equivalent_invoice_links = purchase_alias_links(
            db,
            target_type,
            target_id,
            target=target,
            invoice_id=invoice.id,
            active_only=True,
        )
        for equivalent in equivalent_invoice_links:
            if link is None or equivalent.id != link.id:
                raise ValueError(
                    f"同一采购订单 {order_no} 已通过另一数据来源关联当前发票，请先解除重复关联"
                )

    other_invoice_alloc = sum(
        (
            quantize(to_decimal(row.allocated_amount))
            for row in db.query(TaxInvoiceLink).filter(
                TaxInvoiceLink.invoice_id == invoice.id,
                TaxInvoiceLink.target_type.in_(domain_types),
                TaxInvoiceLink.match_method != "rejected",
                TaxInvoiceLink.confirmed.is_(True),
                TaxInvoiceLink.id != (link.id if link is not None else -1),
            ).all()
        ),
        Decimal("0"),
    )

    if target_type == SALES_LINK_TARGET_TYPE:
        target_limit_raw = target.paid_amount if target.paid_amount is not None else target.order_amount
        target_links = db.query(TaxInvoiceLink).filter(
            TaxInvoiceLink.target_type == target_type,
            TaxInvoiceLink.target_id == target_id,
            TaxInvoiceLink.match_method != "rejected",
            TaxInvoiceLink.confirmed.is_(True),
        ).all()
    else:
        target_limit_raw = _purchase_target_brief(target_type, target)["amount"]
        target_links = purchase_alias_links(
            db,
            target_type,
            target_id,
            target=target,
            active_only=True,
        )
    target_limit = quantize(to_decimal(target_limit_raw)) if target_limit_raw is not None else None
    other_target_alloc = sum(
        (
            quantize(to_decimal(row.allocated_amount))
            for row in target_links
            if row.allocated_amount is not None
            and row.id != (link.id if link is not None else -1)
        ),
        Decimal("0"),
    )

    if allocated_amount is None:
        invoice_remaining = invoice_total - other_invoice_alloc
        target_remaining = (
            target_limit - other_target_alloc
            if target_limit is not None and target_limit > 0
            else invoice_remaining
        )
        amount = min(invoice_remaining, target_remaining)
    else:
        amount = quantize(to_decimal(allocated_amount))

    if not amount.is_finite() or amount <= 0:
        raise ValueError("分摊金额必须大于 0")
    if other_invoice_alloc + amount > invoice_total:
        raise ValueError(
            f"发票累计分摊 {other_invoice_alloc + amount} 超过票面金额 {invoice_total}"
        )
    if target_limit is not None and target_limit > 0 and other_target_alloc + amount > target_limit:
        raise ValueError(
            f"该业务单累计发票分摊 {other_target_alloc + amount} 超过订单金额 {target_limit}"
        )

    if link is None:
        link = TaxInvoiceLink(invoice_id=invoice.id, target_type=target_type, target_id=target_id)
        db.add(link)
    link.allocated_amount = amount
    link.match_method = "manual"
    link.confidence = Decimal("1")
    link.confirmed = True
    link.note = note or default_note

    db.flush()
    # 发票业务匹配缓存统一从业务域 confirmed 链接重算，避免 link/unlink 各写一套规则。
    sync_business_match_status(db, invoice, target_type)
    msg = f"{default_note} {order_no}"
    invoice.match_note = f"{invoice.match_note}；{msg}" if invoice.match_note else msg

    db.commit()
    audit(
        db, actor, audit_action, "tax_invoice_links", str(link.id),
        {"invoiceId": invoice.id, "targetType": target_type, "targetId": target_id, "allocatedAmount": str(amount)},
    )
    return {"ok": True, "id": link.id, "orderNo": order_no}


def unlink_purchase(db: Session, link_id: int, actor: str = "system") -> dict:
    """解除人工采购/销售关联（软删 rejected 保审计；同对可再次关联=复活）。"""
    row = db.get(TaxInvoiceLink, link_id)
    if row is None or row.target_type not in ALLOWED_LINK_TARGET_TYPES or row.match_method == "rejected":
        raise LookupError("关联不存在或已解除")
    is_sales = row.target_type == SALES_LINK_TARGET_TYPE
    row.match_method = "rejected"
    row.confirmed = False
    row.confidence = None
    row.note = "解除销售订单关联" if is_sales else "解除采购单关联"
    # 先把解除状态写入数据库，再按 active confirmed 链接重算；避免查询读到旧行。
    db.flush()

    invoice = db.get(TaxInvoice, row.invoice_id)
    if invoice is not None:
        sync_business_match_status(db, invoice, row.target_type)
        msg = "已解除人工销售订单关联" if is_sales else "已解除人工采购关联"
        invoice.match_note = f"{invoice.match_note}；{msg}" if invoice.match_note else msg

    db.commit()
    audit(
        db, actor, "tax_invoice.unlink_purchase", "tax_invoice_links", str(row.id),
        {"invoiceId": row.invoice_id, "targetType": row.target_type, "targetId": row.target_id},
    )
    return {"ok": True}
