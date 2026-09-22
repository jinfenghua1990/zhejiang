import hashlib
import io
import re
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import settings

"""BankFileAdapter —— 浙江农信原始文件安全落盘（规格 1.3 / 8.1 / 10）。

- 原始文件长期保存、只读、不覆盖：同名自动 version 递增
- SHA256 指纹
- 路径: {DATA_DIR}/finance/{company}/{YYYY}/{MM}/original/bank/
- XLSX 解析：通用列名检测（日期/摘要/对方户名/收入/支出/余额/流水号），
  字段名以真实样本核对为准；解析结果注册进 bank_transactions（指纹幂等）。
"""

SAFE_NAME = re.compile(r"[^\w.\-一-龥]+")

# 列名 → 标准字段 的候选匹配（顺序敏感，先精确后包含）。
#
# 注意：流水号和凭证号码是两个不同的银行字段，不能再把“流水号”
# 误当成凭证号；交易账号也必须优先使用文件里的真实账号。
_COLUMN_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("txn_time", ("交易日期时间", "交易时间", "交易时间戳", "发生时间")),
    ("txn_date", ("交易日期", "记账日期", "起息日", "日期")),
    ("account_no", ("交易账号", "交易帐号", "我方账号", "本方账号", "付款账号", "子账户账号")),
    ("account_name", ("交易户名", "我方户名", "本方户名", "账户名称", "子账户户名")),
    ("counterparty_bank", ("对方开户行", "对方银行", "对方银行名称", "对方行名")),
    ("counterparty", ("对方户名", "对方账户名称", "对方名称", "对方户名/账号", "交易对方")),
    ("counterparty_account", ("对方账号", "对方账户", "对方卡号")),
    ("currency", ("币种", "货币")),
    ("summary", ("摘要", "交易备注", "备注")),
    ("purpose", ("用途", "交易用途")),
    ("channel", ("交易渠道", "渠道")),
    ("operator", ("操作员", "经办人")),
    ("transaction_status", ("交易状态", "状态")),
    ("amount_in", ("收入金额", "贷方发生额", "存入金额", "收入", "汇入金额")),
    ("amount_out", ("支出金额", "借方发生额", "支取金额", "支出", "汇出金额")),
    ("amount_combined", ("交易金额", "发生额", "金额")),
    ("balance", ("账户余额", "余额", "可用余额")),
    ("serial_no", ("银行流水号", "交易流水号", "流水号")),
    ("voucher_no", ("凭证号码", "凭证号", "凭证编号", "凭证", "序号")),
]


def _match_field(header: str) -> str | None:
    h = re.sub(r"[\s\u3000]+", "", str(header or "").strip())
    if not h:
        return None
    for field, candidates in _COLUMN_PATTERNS:
        for c in candidates:
            if h == c or c in h:
                return field
    return None


def _json_value(value: Any) -> Any:
    """把 openpyxl 返回的值转换成可稳定保存到 JSONB 的值。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _raw_fields(headers: list[Any], values: tuple[Any, ...]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for index, header in enumerate(headers):
        key = str(header).strip() if header is not None else ""
        key = key or f"未命名列{index + 1}"
        value = _json_value(values[index]) if index < len(values) else None
        if key not in fields:
            fields[key] = value
        elif isinstance(fields[key], list):
            fields[key].append(value)
        else:
            fields[key] = [fields[key], value]
    return fields


def _raw_snapshot(sheet: str, row_number: int, headers: list[Any], values: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "sheet": sheet,
        "rowNumber": row_number,
        "headers": [str(header) if header is not None else "" for header in headers],
        "values": [_json_value(value) for value in values],
        "fields": _raw_fields(headers, values),
    }


def _parse_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip().replace("/", "-")
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y%m%d %H:%M:%S", "%Y%m%d %H:%M",
        "%Y%m%d%H%M%S", "%Y%m%d",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat(sep=" ")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).isoformat(sep=" ")
    except ValueError:
        return None


def _direction_marker(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    if re.search(r"(支出|支|借|付款|转出|汇出|-)", text):
        return "out"
    if re.search(r"(收入|收|贷|收款|转入|汇入|\+)", text):
        return "in"
    return None


def analyze_xlsx(content: bytes) -> dict:
    """解析失败时输出文件结构诊断：工作表名、列名、行数、已识别/缺失字段。

    返回 {"sheet": str, "columns": [str], "rows": int,
          "detected": [str], "missing": [str], "valid": bool}
    供导入失败时把具体原因反馈给用户，避免“请检查文件格式”式模糊提示。
    """
    from openpyxl import load_workbook

    info: dict = {"sheet": "", "columns": [], "rows": 0, "detected": [], "missing": [], "valid": False}
    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        return info
    ws = wb.active
    info["sheet"] = ws.title
    if ws.max_row:
        info["rows"] = ws.max_row - 1
    header = [c.value for c in ws[1]] if ws.max_row else None
    if header is not None:
        info["columns"] = [str(c) if c is not None else "" for c in header]
    wb.close()

    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header or []):
        field = _match_field(str(cell) if cell is not None else "")
        if field and field not in mapping:
            mapping[field] = idx
    info["detected"] = list(mapping.keys())
    # 关键列：日期/时间 + 明细金额。缺这些无法落地成流水。
    if "txn_date" not in mapping and "txn_time" not in mapping:
        info["missing"].append("txn_date_or_time")
    if not any(field in mapping for field in ("amount_in", "amount_out", "amount_combined")):
        info["missing"].append("amount")
    info["valid"] = (
        ("txn_date" in mapping or "txn_time" in mapping)
        and any(field in mapping for field in ("amount_in", "amount_out", "amount_combined"))
    )
    return info


def parse_xlsx(content: bytes) -> list[dict]:
    """解析浙江农信交易明细 XLSX。

    返回标准字段和 raw 原始整行快照。raw 同时保留表头、原始值、工作表、
    Excel 行号和按原始表头组织的 fields，后续可以从流水详情还原原始记录。
    第一行是表头；自动检测列名；无法识别的列忽略。
    日期支持 datetime / date / 'YYYY-MM-DD' 字符串。
    非 XLSX 内容 / 无关键列 → 返回 []（如实空，不抛异常）。
    """
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(content), data_only=True)
    except Exception:
        return []
    ws = wb.active

    if not ws.max_row:
        wb.close()
        return []
    rows_iter = ws.iter_rows(min_row=2, values_only=True)
    header = [c.value for c in ws[1]]
    if header is None:
        wb.close()
        return []

    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header):
        field = _match_field(str(cell) if cell is not None else "")
        if field and field not in mapping:
            mapping[field] = idx

    if (
        "txn_date" not in mapping
        and "txn_time" not in mapping
    ) or not any(field in mapping for field in ("amount_in", "amount_out", "amount_combined")):
        wb.close()
        return []  # 无关键列，判定不是交易明细表

    out: list[dict] = []
    for row_number, row in enumerate(rows_iter, start=2):
        rec: dict = {}
        for field, idx in mapping.items():
            if idx < len(row):
                rec[field] = row[idx]
        raw = _raw_snapshot(ws.title, row_number, header, row)
        if rec.get("txn_date") is None and rec.get("txn_time") is None:
            continue  # 合计行/表尾行通常无日期 → 跳过
        if (
            rec.get("amount_in") is None
            and rec.get("amount_out") is None
            and rec.get("amount_combined") is None
        ):
            continue  # 无金额 → 跳过
        out.append(_normalize(rec, raw))
    wb.close()
    return out


def _normalize(rec: dict, raw: dict[str, Any]) -> dict:
    def _num(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return f"{Decimal(str(v)).quantize(Decimal('0.01')):f}"
        s = str(v).replace(",", "").replace("¥", "").replace("￥", "")
        s = re.sub(r"[^0-9.+\-]", "", s)
        if not s or s in ("-", "--"):
            return None
        try:
            return f"{Decimal(s).quantize(Decimal('0.01')):f}"
        except Exception:
            return None

    def _date(v) -> str | None:
        if v is None:
            return None
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d")
        s = str(v).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        parsed = _parse_datetime(v)
        if parsed:
            return parsed[:10]
        return None

    transaction_time = _parse_datetime(rec.get("txn_time"))
    combined = _num(rec.get("amount_combined"))
    combined_direction = _direction_marker(rec.get("amount_combined"))
    amount_in = _num(rec.get("amount_in"))
    amount_out = _num(rec.get("amount_out"))
    if amount_in is None and amount_out is None and combined is not None:
        combined_value = f"{abs(Decimal(combined)):f}"
        if combined_direction == "in":
            amount_in = combined_value
        elif combined_direction == "out":
            amount_out = combined_value

    txn_date = _date(rec.get("txn_date")) or (transaction_time[:10] if transaction_time else None)
    purpose = _text(rec.get("purpose"))
    return {
        "txn_date": txn_date,
        "transaction_time": transaction_time,
        "account_no": _text(rec.get("account_no")),
        "account_name": _text(rec.get("account_name")),
        "counterparty_bank": _text(rec.get("counterparty_bank")),
        "counterparty": _text(rec.get("counterparty")),
        "counterparty_account": _text(rec.get("counterparty_account")),
        "currency": _text(rec.get("currency")),
        "summary": _text(rec.get("summary")) or purpose,
        "purpose": purpose,
        "channel": _text(rec.get("channel")),
        "operator": _text(rec.get("operator")),
        "transaction_status": _text(rec.get("transaction_status")),
        "amount_in": amount_in,
        "amount_out": amount_out,
        "balance": _num(rec.get("balance")),
        "serial_no": _text(rec.get("serial_no")),
        "voucher_no": _text(rec.get("voucher_no")),
        "raw": raw,
    }


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_name(name: str) -> str:
    clean = SAFE_NAME.sub("_", name.replace("..", "_"))
    clean = clean.lstrip(".")[:180] or "unnamed"
    return clean


class BankFileAdapter:
    provider = "zhejiang_rural_credit"

    ALLOWED_EXT = {".xlsx", ".pdf", ".zip"}

    def save_original(
        self,
        company: str,
        period_year: int,
        period_month: int,
        category: str,
        original_name: str,
        content: bytes,
    ) -> dict:
        ext = Path(original_name).suffix.lower()
        if ext not in self.ALLOWED_EXT:
            raise ValueError(f"不支持的文件类型: {ext}（允许 XLSX/PDF/ZIP）")
        if not (1900 <= period_year <= 2999 and 1 <= period_month <= 12):
            raise ValueError("非法账期")
        if not content:
            raise ValueError("空文件")
        if len(content) > settings.MAX_UPLOAD_BYTES:
            raise ValueError(f"文件超过单文件大小上限（{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MiB）")

        base_dir = (
            Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
            / f"{period_year:04d}" / f"{period_month:02d}" / "original" / category
        )
        base_dir.mkdir(parents=True, exist_ok=True)

        clean = sanitize_name(original_name)
        # 同名不覆盖：version 递增
        version = 1
        while True:
            target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
            try:
                with target.open("xb") as output:
                    output.write(content)
                break
            except FileExistsError:
                # 使用排他创建而非 exists()+write，避免并发覆盖同名原件。
                version += 1

        return {
            "stored_path": str(target),
            "original_name": original_name,
            "category": category,
            "size": len(content),
            "sha256": sha256_of(target),
            "version": version,
        }
