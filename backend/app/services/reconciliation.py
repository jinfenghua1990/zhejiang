"""回款匹配框架（规格 8.2 / 16）。

- 银行匹配不能只靠金额：平台 + 日期 + 金额 + 对方户名 + 摘要 + 流水号综合评分
- 对方户名映射规则可配置，规则变更必须写审计日志，不静默重写历史匹配
- 输出 confidence + matched target + status
- 幂等指纹：账户+日期+金额+流水号；流水号为空用可重复 hash fallback
"""
from __future__ import annotations

import calendar
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.models.bank import BankAccount, BankTransaction, CounterpartyMappingRule
from app.models.payment import ReconciliationMatch, SettlementRecord
from app.utils.money import quantize, to_decimal

# 规格 8.2 示例默认规则
DEFAULT_RULES = [
    ("江苏银行-平台交易资金专户（抖音）", "抖音"),
    ("上海得物信息集团有限公司", "得物"),
    ("平安银行电子商务交易资金待清算专户（得物）", "得物"),
    ("上海寻梦信息技术有限公司", "拼多多"),
]


# ---------- 纯函数（可单测） ----------

def build_fingerprint(account_no: str, txn_date: str, amount, voucher_no: str,
                      counterparty_name: str = "", summary: str = "",
                      serial_no: str = "") -> str:
    """真实账户+日期+金额+银行流水号/凭证号；无编号时使用稳定回退值。"""
    amt = f"{quantize(to_decimal(amount), Decimal('0.01')):f}"
    if serial_no:
        raw = f"{account_no}|{txn_date}|{amt}|{serial_no}|{voucher_no}"
    elif voucher_no:
        # 保持旧版人工录入/历史导入的指纹兼容，避免升级后同一笔重新生成。
        raw = f"{account_no}|{txn_date}|{amt}|{voucher_no}"
    else:
        raw = f"{account_no}|{txn_date}|{amt}|{counterparty_name}|{summary}"
    return hashlib.sha256(raw.encode()).hexdigest()


def match_platform(counterparty_name: str, rules: list[tuple[str, str, str]]) -> str | None:
    """按规则把对方户名映射到平台，对规则顺序不敏感。

    - 精确相等(equals)优先且唯一确定；
    - contains 模式按 pattern 长度降序取最具体的命中，避免宽泛规则遮蔽具体规则
      （如「科技」不应遮蔽「某科技有限公司」），从而影响 score_match 的平台评分。
    """
    name = counterparty_name or ""
    if not name:
        return None

    # 1) 精确相等优先
    for pattern, match_type, platform in rules:
        if match_type == "equals" and name == pattern:
            return platform

    # 2) 子串匹配按具体度（pattern 越长越具体）降序，最具体的优先
    contains_hits = [
        (len(pattern), platform)
        for pattern, match_type, platform in rules
        if match_type != "equals" and pattern and pattern in name
    ]
    if contains_hits:
        contains_hits.sort(key=lambda x: x[0], reverse=True)
        return contains_hits[0][1]

    return None


def _period_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def score_match(*, txn_date: date, txn_amount, counterparty_name: str, summary: str,
                settlement_platform: str, settlement_store: str,
                settlement_expected, settlement_year: int, settlement_month: int,
                platform_of_txn: str | None) -> dict[str, Any]:
    """综合评分：金额(40) + 平台规则(30) + 日期(15) + 户名/摘要提示(10)。"""
    reasons: list[str] = []
    score = 0

    amt = to_decimal(txn_amount)
    exp = to_decimal(settlement_expected)
    if exp > 0 and abs(amt - exp) <= Decimal("0.01"):
        score += 40
        reasons.append("金额精确一致 +40")
    elif exp > 0 and abs(amt - exp) <= exp * Decimal("0.01"):
        score += 20
        reasons.append("金额误差<1% +20")

    if platform_of_txn and platform_of_txn == settlement_platform:
        score += 30
        reasons.append(f"对方户名命中平台规则[{settlement_platform}] +30")

    pe = _period_end(settlement_year, settlement_month)
    days = abs((txn_date - pe).days)
    if txn_date.year == settlement_year and txn_date.month == settlement_month:
        score += 15
        reasons.append("交易在结算账期内 +15")
    elif days <= 7:
        score += 10
        reasons.append("交易距账期末≤7天 +10")

    hint = settlement_store or settlement_platform or ""
    if hint and (hint in (counterparty_name or "") or (summary and hint in summary)):
        score += 10
        reasons.append("户名/摘要含店铺或平台提示 +10")

    confidence = "high" if score >= 80 else ("medium" if score >= 55 else "low")
    return {"score": score, "confidence": confidence, "reasons": reasons}


# ---------- DB 层 ----------

def seed_rules_if_empty(db: Session) -> bool:
    if db.query(CounterpartyMappingRule).count() > 0:
        return False
    for pattern, platform in DEFAULT_RULES:
        db.add(CounterpartyMappingRule(match_pattern=pattern, match_type="contains", platform=platform,
                                       note="规格 8.2 默认规则"))
    db.commit()
    audit(db, "system", "reconciliation.rules.seed", "counterparty_mapping_rules", "",
          {"count": len(DEFAULT_RULES)})
    return True


def list_rules(db: Session) -> list[CounterpartyMappingRule]:
    return db.query(CounterpartyMappingRule).order_by(CounterpartyMappingRule.id).all()


def active_rule_tuples(db: Session) -> list[tuple[str, str, str]]:
    return [(r.match_pattern, r.match_type, r.platform)
            for r in list_rules(db) if r.enabled]


def add_rule(db: Session, *, match_pattern: str, match_type: str, platform: str,
             note: str = "", actor: str = "system") -> CounterpartyMappingRule:
    if match_type not in ("contains", "equals"):
        raise ValueError("非法匹配类型")
    if not match_pattern or not platform:
        raise ValueError("匹配模式与平台名必填")
    row = CounterpartyMappingRule(match_pattern=match_pattern, match_type=match_type,
                                  platform=platform, note=note)
    db.add(row)
    db.commit()
    audit(db, actor, "reconciliation.rule.create", "counterparty_mapping_rules", row.id,
          {"pattern": match_pattern, "platform": platform})
    return row


def delete_rule(db: Session, rule_id: int, actor: str = "system") -> None:
    row = db.get(CounterpartyMappingRule, rule_id)
    if row:
        db.delete(row)
        db.commit()
        audit(db, actor, "reconciliation.rule.delete", "counterparty_mapping_rules", rule_id,
              {"pattern": row.match_pattern, "platform": row.platform})


DEFAULT_INTERNAL_ACCOUNT_CODE = "ZJRC-001"


def _is_usable_source_account(value: str) -> bool:
    """Ignore bank export header placeholders masquerading as account values."""
    account_ref = (value or "").strip()
    if not account_ref:
        return False
    if account_ref == DEFAULT_INTERNAL_ACCOUNT_CODE:
        return True
    digits = "".join(char for char in account_ref if char.isdigit())
    return len(digits) >= 8 and digits == account_ref.replace(" ", "")


def resolve_account_reference(db: Session, value: str) -> tuple[str, str, str]:
    """把真实账号或内部编号解析为真实账号。

    返回 ``(real_account_no, internal_code, source)``；account_no 永远不保存内部别名。
    """
    account_ref = (value or "").strip()
    if not account_ref:
        raise ValueError("银行真实账号不能为空；请从流水文件读取或明确填写真实账号")

    row = db.query(BankAccount).filter_by(account_no=account_ref).first()
    if row is not None:
        return row.account_no, row.internal_code or "", "account_no"

    row = db.query(BankAccount).filter_by(internal_code=account_ref).first()
    if row is not None:
        return row.account_no, row.internal_code or account_ref, "internal_code"

    return account_ref, "", "account_no"


def ensure_account(
    db: Session,
    account_no: str,
    account_name: str = "",
    internal_code: str = "",
) -> BankAccount:
    account_no = (account_no or "").strip()
    internal_code = (internal_code or "").strip()
    if not account_no:
        raise ValueError("银行真实账号不能为空；请从流水文件读取或明确填写真实账号")
    row = db.query(BankAccount).filter_by(account_no=account_no).first()
    if not row:
        row = BankAccount(account_no=account_no, account_name=account_name or account_no,
                          bank_name="浙江农信", internal_code=internal_code or None)
        db.add(row)
        db.commit()
    else:
        changed = False
        if internal_code and not row.internal_code:
            row.internal_code = internal_code
            changed = True
        if account_name and (not row.account_name or row.account_name == row.account_no):
            row.account_name = account_name
            changed = True
        if changed:
            db.commit()
    return row


def _merge_raw(previous: dict | None, incoming: dict | None) -> dict:
    """保留当前来源和历史来源，重复导入不丢原始行。"""
    if not incoming:
        return previous or {}
    if not previous:
        return incoming

    same_source = (
        previous.get("archiveFileId") == incoming.get("archiveFileId")
        and previous.get("rowNumber") == incoming.get("rowNumber")
        and previous.get("sheet") == incoming.get("sheet")
    )
    history = list(previous.get("sourceHistory") or [])
    previous_snapshot = {key: value for key, value in previous.items() if key != "sourceHistory"}
    if not same_source and previous_snapshot not in history:
        history.append(previous_snapshot)
    merged = dict(incoming)
    if history:
        merged["sourceHistory"] = history
    return merged


def _parse_transaction_time(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _legacy_candidate(
    db: Session,
    *,
    txn_date: date,
    direction: str,
    amount: Decimal,
    counterparty_name: str,
    counterparty_account: str,
    serial_no: str,
    voucher_no: str,
) -> BankTransaction | None:
    """兼容旧版把真实流水号写进 voucher_no 的历史记录。"""
    query = db.query(BankTransaction).filter(
        BankTransaction.txn_date == txn_date,
        BankTransaction.direction == direction,
        BankTransaction.amount == amount,
        BankTransaction.counterparty_name == (counterparty_name or ""),
    )
    if counterparty_account:
        query = query.filter(BankTransaction.counterparty_account == counterparty_account)
    candidates = query.all()
    matched = []
    for candidate in candidates:
        identifiers = {candidate.serial_no or "", candidate.voucher_no or ""}
        wanted = {serial_no or "", voucher_no or ""} - {""}
        if wanted and identifiers.intersection(wanted):
            matched.append(candidate)
        elif not wanted and len(candidates) == 1:
            matched.append(candidate)
    return matched[0] if len(matched) == 1 else None


def add_transaction(db: Session, *, account_no: str, txn_date: date, direction: str,
                    amount, counterparty_name: str = "", counterparty_account: str = "",
                    summary: str = "", serial_no: str = "", voucher_no: str = "",
                    account_name: str = "", transaction_time: datetime | None = None,
                    source_row_number: int | None = None, raw: dict | None = None,
                    import_batch_id: int | None = None,
                    internal_code: str = "",
                    actor: str = "system") -> tuple[BankTransaction, bool]:
    """登记银行流水并保留原始来源。重复导入会补齐来源，不会清空 raw。"""
    if direction not in ("in", "out"):
        raise ValueError("方向必须为 in/out")
    account_no = (account_no or "").strip()
    serial_no = (serial_no or "").strip()
    voucher_no = (voucher_no or "").strip()
    counterparty_name = counterparty_name or ""
    counterparty_account = counterparty_account or ""
    summary = summary or ""
    requested_account = account_no
    account_no, mapped_internal_code, resolution = resolve_account_reference(db, requested_account)
    if requested_account == DEFAULT_INTERNAL_ACCOUNT_CODE and resolution != "internal_code":
        raise ValueError("内部编号 ZJRC-001 尚未配置真实银行账号，请先配置账户映射")
    internal_code = (internal_code or mapped_internal_code).strip()
    account = ensure_account(
        db, account_no, account_name=account_name, internal_code=internal_code,
    )
    amount_value = quantize(to_decimal(amount), Decimal("0.01"))
    fp = build_fingerprint(account_no, txn_date.isoformat(), amount, voucher_no,
                           counterparty_name, summary, serial_no=serial_no)
    existing = db.query(BankTransaction).filter_by(fingerprint=fp).first()
    if existing is None:
        existing = _legacy_candidate(
            db, txn_date=txn_date, direction=direction, amount=amount_value,
            counterparty_name=counterparty_name, counterparty_account=counterparty_account,
            serial_no=serial_no, voucher_no=voucher_no,
        )
    if existing:
        # 老记录可能使用了系统别名或把流水号放在 voucher_no；重新上传真实文件时
        # 原地补齐来源和账号，避免产生第二条同一笔付款，也不破坏已有匹配关系。
        existing.account_id = account.id
        existing.import_batch_id = import_batch_id or existing.import_batch_id
        existing.transaction_time = transaction_time or existing.transaction_time
        existing.source_row_number = source_row_number or existing.source_row_number
        if serial_no:
            existing.serial_no = serial_no
        if voucher_no:
            existing.voucher_no = voucher_no
        existing.raw = _merge_raw(existing.raw or {}, raw)
        if existing.fingerprint != fp:
            existing.fingerprint = fp
        db.commit()
        return existing, False
    row = BankTransaction(
        account_id=account.id, import_batch_id=import_batch_id,
        txn_date=txn_date, direction=direction,
        transaction_time=transaction_time,
        amount=amount_value,
        counterparty_name=counterparty_name, counterparty_account=counterparty_account,
        summary=summary, serial_no=serial_no, voucher_no=voucher_no,
        source_row_number=source_row_number, fingerprint=fp, raw=raw or {},
    )
    db.add(row)
    db.commit()
    audit(db, actor, "bank.txn.create", "bank_transactions", row.id,
          {"date": txn_date.isoformat(), "amount": str(row.amount), "counterparty": counterparty_name})
    return row, True


def import_bank_xlsx(db: Session, *, account_no: str = "", content: bytes,
                     period_year: int, period_month: int,
                     file_name: str, archive_file_id: int,
                     actor: str = "system") -> dict:
    """解析并导入银行流水，同时保存真实账户和每一行原始来源。

    文件中识别到的我方真实账号优先级高于接口传入值。文件没有我方账号时，
    可以传入已配置的内部编号（例如 ``ZJRC-001``），系统会解析成真实账号；
    数据库最终只保存真实账号，内部编号单独保存。
    """
    from datetime import date

    from app.adapters.bank_file import analyze_xlsx, parse_xlsx
    from app.models.bank import BankImportBatch

    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    batch = BankImportBatch(
        source="zjrc", file_name=file_name, archive_file_id=archive_file_id,
        period_year=period_year, period_month=period_month,
        row_count=0, status="processing",
    )
    db.add(batch)
    db.commit()

    rows = parse_xlsx(content)
    if not rows:
        batch.status = "failed"
        db.commit()
        # 结构诊断：把“文件存到哪、检测到什么列、缺什么列”明确反馈给用户
        diag = analyze_xlsx(content)
        audit(db, actor, "bank.import.xlsx.failed", "bank_import_batches", batch.id,
              {"archiveFileId": archive_file_id, "reason": "未识别到交易明细", "diagnosis": diag})
        col_text = "、".join(diag.get("columns") or []) or "（空表头）"
        missing_labels = {
            "txn_date_or_time": "交易日期或交易时间", "amount": "交易金额/收入金额/支出金额",
            "counterparty": "对方户名", "summary": "摘要", "serial_no": "流水号",
        }
        missing_text = "、".join(missing_labels.get(k, k) for k in (diag.get("missing") or []))
        raise ValueError(
            f"文件已归档但解析失败：工作表「{diag.get('sheet') or '?'}」，共 {diag.get('rows') or 0} 行数据，"
            f"识别到列：{col_text}。缺少关键列：{missing_text}。"
            "请从银行系统导出包含交易日期/交易时间和交易金额的完整流水。"
        )

    source_accounts = sorted({
        account_ref
        for r in rows
        if _is_usable_source_account(account_ref := (r.get("account_no") or "").strip())
    })
    if len(source_accounts) > 1:
        batch.status = "failed"
        db.commit()
        audit(db, actor, "bank.import.xlsx.failed", "bank_import_batches", batch.id,
              {"archiveFileId": archive_file_id, "reason": "单文件包含多个我方账号",
               "accounts": source_accounts})
        raise ValueError("同一个银行明细文件识别到多个我方账号，请按账号拆分后再导入，避免串账")

    manual_account = (account_no or "").strip()
    if source_accounts:
        source_account = source_accounts[0]
        resolved_account_no, internal_code, _ = resolve_account_reference(db, source_account)
        if source_account == DEFAULT_INTERNAL_ACCOUNT_CODE and not internal_code:
            batch.status = "failed"
            db.commit()
            raise ValueError("文件中的我方账号是系统别名，请先配置对应的真实银行账号")
        account_source = "file"
    else:
        if not manual_account:
            batch.status = "failed"
            db.commit()
            audit(db, actor, "bank.import.xlsx.failed", "bank_import_batches", batch.id,
                  {"archiveFileId": archive_file_id, "reason": "文件没有真实我方账号"})
            raise ValueError("文件未识别我方交易账号，请填写真实账号或已配置的内部编号")
        resolved_account_no, internal_code, resolution = resolve_account_reference(db, manual_account)
        if manual_account == DEFAULT_INTERNAL_ACCOUNT_CODE and resolution != "internal_code":
            batch.status = "failed"
            db.commit()
            audit(db, actor, "bank.import.xlsx.failed", "bank_import_batches", batch.id,
                  {"archiveFileId": archive_file_id, "reason": "内部编号未配置真实账号"})
            raise ValueError("内部编号 ZJRC-001 尚未配置真实银行账号，请先配置账户映射")
        account_source = "internal_code" if resolution == "internal_code" else "manual"

    account_names = sorted({(r.get("account_name") or "").strip() for r in rows if r.get("account_name")})
    account_name = account_names[0] if len(account_names) == 1 else ""
    created = duplicates = skipped = raw_stored = 0
    for r in rows:
        if not r.get("txn_date"):
            skipped += 1
            continue
        try:
            amount_in = to_decimal(r["amount_in"]) if r.get("amount_in") is not None else None
            amount_out = to_decimal(r["amount_out"]) if r.get("amount_out") is not None else None
        except (TypeError, ValueError):
            skipped += 1
            continue
        if amount_in is not None and amount_in > 0:
            direction, amount = "in", amount_in
        elif amount_out is not None and amount_out > 0:
            direction, amount = "out", amount_out
        else:
            skipped += 1
            continue
        raw = dict(r.get("raw") or {})
        raw.update({
            "archiveFileId": archive_file_id,
            "importBatchId": batch.id,
            "accountSource": account_source,
            "resolvedAccountNo": resolved_account_no,
            "internalAccountCode": internal_code,
        })
        try:
            txn_date = date.fromisoformat(r["txn_date"])
            _, is_new = add_transaction(
                db, account_no=resolved_account_no, account_name=account_name,
                txn_date=txn_date, direction=direction, amount=amount,
                counterparty_name=r.get("counterparty") or "",
                counterparty_account=r.get("counterparty_account") or "",
                summary=r.get("summary") or "", serial_no=r.get("serial_no") or "",
                voucher_no=r.get("voucher_no") or "",
                transaction_time=_parse_transaction_time(r.get("transaction_time")),
                source_row_number=(r.get("raw") or {}).get("rowNumber"), raw=raw,
                import_batch_id=batch.id, internal_code=internal_code, actor=actor,
            )
        except (TypeError, ValueError):
            skipped += 1
            continue
        raw_stored += 1
        if is_new:
            created += 1
        else:
            duplicates += 1

    batch.row_count = created + duplicates
    batch.status = "done"
    db.commit()
    audit(db, actor, "bank.import.xlsx", "bank_import_batches", batch.id,
          {"account": resolved_account_no, "accountSource": account_source,
           "parsed": len(rows), "created": created, "duplicates": duplicates,
           "rawStored": raw_stored, "skipped": skipped,
           "period": f"{period_year}-{period_month:02d}",
           "archiveFileId": archive_file_id})
    return {
        "batchId": batch.id, "archiveFileId": archive_file_id,
        "accountNo": resolved_account_no, "accountSource": account_source,
        "internalCode": internal_code,
        "sourceAccounts": source_accounts, "parsed": len(rows),
        "created": created, "duplicates": duplicates,
        "rawStored": raw_stored, "skipped": skipped,
    }


def add_settlement(db: Session, *, platform: str, period_year: int, period_month: int,
                   expected_amount, store_name: str = "",
                   actor: str = "system") -> SettlementRecord:
    if not (1 <= period_month <= 12):
        raise ValueError("非法账期")
    row = SettlementRecord(
        platform=platform, store_name=store_name or "",
        period_year=period_year, period_month=period_month,
        expected_amount=quantize(to_decimal(expected_amount), Decimal("0.01")),
        source="manual", status="open",
    )
    db.add(row)
    db.commit()
    audit(db, actor, "settlement.create", "settlement_records", row.id,
          {"platform": platform, "period": f"{period_year}-{period_month:02d}",
           "expected": str(row.expected_amount)})
    return row


def settled_amounts_by_settlement(
    db: Session,
    settlement_ids: list[int] | None = None,
) -> dict[int, Decimal]:
    """一次聚合取各应收的已确认入账，避免列表和总览逐行查询流水。"""
    if settlement_ids is not None and not settlement_ids:
        return {}
    q = (
        db.query(
            ReconciliationMatch.target_id,
            func.coalesce(func.sum(BankTransaction.amount), Decimal("0")),
        )
        .join(BankTransaction, BankTransaction.id == ReconciliationMatch.txn_id)
        .filter(
            ReconciliationMatch.target_type == "settlement",
            ReconciliationMatch.status == "confirmed",
            BankTransaction.direction == "in",
        )
        .group_by(ReconciliationMatch.target_id)
    )
    if settlement_ids is not None:
        q = q.filter(ReconciliationMatch.target_id.in_(settlement_ids))
    return {int(target_id): to_decimal(total) for target_id, total in q.all()}


def settled_amount_of(db: Session, settlement_id: int) -> Decimal:
    return settled_amounts_by_settlement(db, [settlement_id]).get(settlement_id, Decimal("0"))


def refresh_settlement_status(db: Session, settlement: SettlementRecord) -> None:
    settled = settled_amount_of(db, settlement.id)
    expected = to_decimal(settlement.expected_amount)
    if expected > 0 and settled >= expected:
        settlement.status = "settled"
    elif settled > 0:
        settlement.status = "partial"
    else:
        settlement.status = "open"
    db.commit()


def _txn_platform(
    db: Session,
    txn: BankTransaction,
    rules: list[tuple[str, str, str]] | None = None,
) -> str | None:
    return match_platform(txn.counterparty_name, rules if rules is not None else active_rule_tuples(db))


def has_confirmed_match(db: Session, txn_id: int) -> bool:
    return (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, status="confirmed")
        .count()
    ) > 0


def confirmed_txn_ids(db: Session, txn_ids: list[int]) -> set[int]:
    if not txn_ids:
        return set()
    return {
        int(txn_id)
        for (txn_id,) in (
            db.query(ReconciliationMatch.txn_id)
            .filter(ReconciliationMatch.status == "confirmed", ReconciliationMatch.txn_id.in_(txn_ids))
            .distinct()
            .all()
        )
    }


def confirmed_settlement_txn_ids(db: Session, txn_ids: list[int]) -> set[int]:
    """只返回银行收入↔平台结算已确认的流水；其他银行核对类型不能冒充回款对账。"""
    if not txn_ids:
        return set()
    return {
        int(txn_id)
        for (txn_id,) in (
            db.query(ReconciliationMatch.txn_id)
            .filter(
                ReconciliationMatch.status == "confirmed",
                ReconciliationMatch.target_type == "settlement",
                ReconciliationMatch.txn_id.in_(txn_ids),
            )
            .distinct()
            .all()
        )
    }


def suggest_for_txn(
    db: Session,
    txn: BankTransaction,
    top: int = 3,
    *,
    rules: list[tuple[str, str, str]] | None = None,
    settlements: list[SettlementRecord] | None = None,
) -> list[dict[str, Any]]:
    platform_of_txn = _txn_platform(db, txn, rules)
    results = []
    candidates = settlements if settlements is not None else (
        db.query(SettlementRecord).filter(SettlementRecord.status != "settled").all()
    )
    for s in candidates:
        r = score_match(
            txn_date=txn.txn_date, txn_amount=txn.amount,
            counterparty_name=txn.counterparty_name, summary=txn.summary,
            settlement_platform=s.platform, settlement_store=s.store_name,
            settlement_expected=s.expected_amount,
            settlement_year=s.period_year, settlement_month=s.period_month,
            platform_of_txn=platform_of_txn,
        )
        results.append({"settlement": s, **r})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top]


def suggestions(db: Session, limit: int = 50) -> list[dict[str, Any]]:
    """未确认流水 → 最佳匹配建议（不落库，确认才生效）。"""
    txns = (
        db.query(BankTransaction)
        .filter(BankTransaction.direction == "in")
        .order_by(BankTransaction.txn_date.desc())
        .limit(200)
        .all()
    )
    confirmed_ids = confirmed_settlement_txn_ids(db, [txn.id for txn in txns])
    rules = active_rule_tuples(db)
    open_settlements = db.query(SettlementRecord).filter(SettlementRecord.status != "settled").all()
    out = []
    for txn in txns:
        if txn.id in confirmed_ids:
            continue
        best = suggest_for_txn(db, txn, top=1, rules=rules, settlements=open_settlements)
        if not best or best[0]["score"] <= 0:
            continue
        s = best[0]["settlement"]
        out.append({
            "txnId": txn.id, "txnDate": txn.txn_date.isoformat(),
            "counterparty": txn.counterparty_name, "amount": str(txn.amount),
            "settlementId": s.id, "platform": s.platform,
            "period": f"{s.period_year}-{s.period_month:02d}",
            "expectedAmount": str(s.expected_amount),
            "score": best[0]["score"], "confidence": best[0]["confidence"],
            "reasons": best[0]["reasons"],
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def confirm_match(db: Session, *, txn_id: int, settlement_id: int,
                  actor: str = "system") -> ReconciliationMatch:
    txn = db.get(BankTransaction, txn_id)
    settlement = db.get(SettlementRecord, settlement_id)
    if not txn or not settlement:
        raise ValueError("流水或应收记录不存在")
    if txn.direction != "in":
        raise ValueError("仅入账流水可确认为回款")
    db.refresh(txn, with_for_update=True)
    db.refresh(settlement, with_for_update=True)
    confirmed = (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, status="confirmed")
        .first()
    )
    if confirmed:
        if confirmed.target_type == "settlement" and confirmed.target_id == settlement_id:
            raise ValueError("该匹配已确认")
        raise ValueError("该银行流水已确认到其他目标，不可重复确认")
    expected = to_decimal(settlement.expected_amount)
    settled_before = settled_amount_of(db, settlement.id)
    remaining = max(expected - settled_before, Decimal("0"))
    txn_amount = to_decimal(txn.amount)
    if remaining <= Decimal("0.01"):
        raise ValueError("该应收记录已结清，不能继续确认回款")
    if txn_amount > remaining + Decimal("0.01"):
        raise ValueError(
            f"本次到账 {txn_amount} 超过该应收剩余金额 {remaining}，当前模型不允许超额确认"
        )

    rules = active_rule_tuples(db)
    best = suggest_for_txn(db, txn, top=5, rules=rules)
    hit = next((b for b in best if b["settlement"].id == settlement_id), None)
    score = hit["score"] if hit else 0
    confidence = hit["confidence"] if hit else "low"
    platform_of_txn = _txn_platform(db, txn, rules)
    row = ReconciliationMatch(
        txn_id=txn_id, target_type="settlement", target_id=settlement_id,
        score=score, confidence=confidence, status="confirmed",
        matched_platform=platform_of_txn or settlement.platform, matched_by="manual",
    )
    db.add(row)
    txn.matched_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("该银行流水已被其他操作确认，请刷新后查看") from exc
    refresh_settlement_status(db, settlement)
    audit(db, actor, "reconciliation.match.confirm", "reconciliation_matches", row.id,
          {"txnId": txn_id, "settlementId": settlement_id, "score": score,
           "confidence": confidence, "amount": str(txn.amount)})
    return row


def reject_match(db: Session, *, txn_id: int, settlement_id: int,
                 actor: str = "system") -> None:
    row = (
        db.query(ReconciliationMatch)
        .filter_by(txn_id=txn_id, target_type="settlement", target_id=settlement_id)
        .first()
    )
    if row:
        was_confirmed = row.status == "confirmed"
        row.status = "rejected"
        db.flush()
        if was_confirmed and not has_confirmed_match(db, txn_id):
            txn = db.get(BankTransaction, txn_id)
            if txn:
                txn.matched_at = None
        db.commit()
    settlement = db.get(SettlementRecord, settlement_id)
    if settlement:
        refresh_settlement_status(db, settlement)
    audit(db, actor, "reconciliation.match.reject", "reconciliation_matches", row.id if row else "",
          {"txnId": txn_id, "settlementId": settlement_id})


def overview(db: Session) -> dict[str, Any]:
    """应回款 / 已回款 / 待回款 + 分平台。页面刷新只查本地库。"""
    settlements = db.query(SettlementRecord).all()
    settled_amounts = settled_amounts_by_settlement(db, [s.id for s in settlements])
    receivable = Decimal("0")
    per_platform: dict[str, dict[str, Decimal]] = {}
    for s in settlements:
        settled = settled_amounts.get(s.id, Decimal("0"))
        expected = to_decimal(s.expected_amount)
        receivable += expected
        agg = per_platform.setdefault(s.platform, {"expected": Decimal("0"), "settled": Decimal("0")})
        agg["expected"] += expected
        agg["settled"] += settled
    received = sum((v["settled"] for v in per_platform.values()), Decimal("0"))
    pending = max(receivable - received, Decimal("0"))
    overpaid = max(received - receivable, Decimal("0"))
    return {
        "receivable": f"{receivable:f}",
        "received": f"{received:f}",
        "pending": f"{pending:f}",
        "overpaid": f"{overpaid:f}",
        "byPlatform": {
            p: {"expected": f"{v['expected']:f}", "settled": f"{v['settled']:f}"}
            for p, v in per_platform.items()
        },
    }
