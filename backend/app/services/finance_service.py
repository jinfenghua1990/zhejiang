"""财务资料中心（规格 10）：原始资料 收集→完整性→归档→原样ZIP→留版本。

- 原始文件只读、同名不覆盖（version 递增）
- 缺资料禁止打包（INCOMPLETE → 异常）
- 已生成的 ZIP 版本不可覆盖；已发送 V1 不受后续影响
- SMTP 发送在 Phase 6 配置后开放
"""
from __future__ import annotations

import mimetypes
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.adapters.bank_file import sanitize_name, sha256_of
from app.config import settings
from app.core.audit import audit
from app.models.finance import (
    ArchiveFile,
    FinanceDeliveryFile,
    FinanceDeliveryPackage,
    MonthlyFinancePeriod,
)

DEFAULT_COMPANY = "浙江柴本网络科技有限公司"
CATEGORIES = (
    "bank", "jackyun", "invoice", "sales_summary",
    "purchase_inbound", "sales_query", "other",
)
# 只用于新建账期；历史账期继续使用自己已保存的 required_types，不追溯改口径。
# 月度交付表：银行原件 + 系统生成的无票收入 + 已收票对公付款明细；银行类必备 2 个文件。
DELIVERY_TABLES = ("交易明细", "回单详情", "无票收入", "已收票对公付款明细")
# 销售汇总/出库 CSV 为系统自动台账，不作为打包前置条件。
DEFAULT_REQUIRED = {
    "bank": 2,
    "invoice": 0,
    "jackyun": 0,
    "sales_summary": 0,
    "purchase_inbound": 0,
    "sales_query": 0,
    "other": 0,
}


def delivery_required_types(required: dict[str, int] | None) -> dict[str, int]:
    """财务交付只要求交付资料；采购/销售业务源由各自业务模块负责。

    兼容旧账期：即使历史 required_types 曾把 purchase_inbound / sales_query
    设为必填，也不再把它们作为月结发送门禁。
    """
    normalized = {**DEFAULT_REQUIRED, **(required or {})}
    normalized["purchase_inbound"] = 0
    normalized["sales_query"] = 0
    normalized["sales_summary"] = 0
    return normalized


def validate_period(year: int, month: int) -> None:
    """统一约束账期，避免异常年份进入文件路径或数据库。"""
    if not (1900 <= year <= 2999):
        raise ValueError(f"year 不在合理范围内，给定 {year}")
    if not (1 <= month <= 12):
        raise ValueError("非法月份")


def managed_data_file(path: str | Path, *, label: str) -> Path:
    """仅允许读取 DATA_DIR 内真实存在的普通文件，拒绝越界路径和外链符号链接。

    兼容历史数据：早期部署（Docker）写入的绝对路径根可能已迁移（如 /data →
    本地 DATA_DIR）。若原路径失效，按 `finance/` 之后的相对分支在 DATA_DIR 下
    重查，避免历史归档文件无法下载、打包或发送。
    """
    root = Path(settings.DATA_DIR).resolve()
    p = Path(path)
    if p.is_absolute():
        try:
            resolved = p.resolve()
            if resolved.is_relative_to(root) and resolved.is_file():
                return resolved
        except (OSError, RuntimeError):
            pass
        if "finance" in p.parts:
            candidate = root.joinpath(*p.parts[p.parts.index("finance"):])
            try:
                candidate_resolved = candidate.resolve()
                if candidate_resolved.is_relative_to(root) and candidate_resolved.is_file():
                    return candidate_resolved
            except (OSError, RuntimeError):
                pass
        if p.exists():
            raise RuntimeError(f"{label}不在受管数据目录内")
    raise RuntimeError(f"{label}缺失（存储被移动或删除）")


def _write_new_file(target: Path, content: bytes) -> None:
    """用 O_EXCL 语义创建文件，两个并发上传绝不会覆盖彼此。"""
    with target.open("xb") as output:
        output.write(content)


def get_or_create_period(db: Session, company: str, year: int, month: int) -> MonthlyFinancePeriod:
    validate_period(year, month)
    row = (
        db.query(MonthlyFinancePeriod)
        .filter_by(company=company, period_year=year, period_month=month)
        .first()
    )
    if not row:
        row = MonthlyFinancePeriod(
            company=company, period_year=year, period_month=month,
            required_types=DEFAULT_REQUIRED,
        )
        db.add(row)
        db.commit()
    return row


def next_version(db: Session, company: str, year: int, month: int, category: str, original_name: str) -> int:
    cur = (
        db.query(func.max(ArchiveFile.version))
        .filter_by(company=company, period_year=year, period_month=month,
                   category=category, original_name=original_name)
        .scalar()
    )
    return (cur or 0) + 1


def evaluate_completeness(files: list[ArchiveFile], required: dict[str, int]) -> tuple[str, dict[str, Any]]:
    """纯函数：状态判定。缺任何必备类别 → INCOMPLETE。"""
    counts = {c: 0 for c in CATEGORIES}
    for f in files:
        counts[f.category] = counts.get(f.category, 0) + 1
    missing = {}
    for cat, need in (required or {}).items():
        lack = int(need) - counts.get(cat, 0)
        if lack > 0:
            missing[cat] = lack
    return ("READY" if not missing else "INCOMPLETE"), {"counts": counts, "missing": missing}


def refresh_period_status(db: Session, company: str, year: int, month: int) -> MonthlyFinancePeriod:
    period = get_or_create_period(db, company, year, month)
    files = db.query(ArchiveFile).filter_by(company=company, period_year=year, period_month=month).all()
    status, summary = evaluate_completeness(
        files, delivery_required_types(period.required_types)
    )
    if period.status != "SENT":  # 已发送状态不被自动覆盖
        period.status = status
    period.missing_summary = summary
    db.commit()
    return period


def store_upload(
    db: Session,
    *,
    company: str,
    year: int,
    month: int,
    category: str,
    original_name: str,
    content: bytes,
    actor: str = "system",
) -> ArchiveFile:
    validate_period(year, month)
    if category not in CATEGORIES:
        raise ValueError(f"非法资料类别: {category}")
    if not content:
        raise ValueError("空文件")
    if len(content) > settings.MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过单文件大小上限（{settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MiB）")

    original_name = original_name or "unnamed"
    version = next_version(db, company, year, month, category, original_name)
    base_dir = (
        Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
        / f"{year:04d}" / f"{month:02d}" / "original" / category
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    clean = sanitize_name(original_name)
    while True:
        target = base_dir / f"{Path(clean).stem}.v{version}{Path(clean).suffix}"
        try:
            _write_new_file(target, content)
            break
        except FileExistsError:
            # 另一个并发上传可能刚写入相同版本；保留原件并尝试下一个版本。
            version += 1

    try:
        mime = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        row = ArchiveFile(
            company=company, category=category, original_name=original_name,
            stored_path=str(target), size=len(content), mime=mime,
            sha256=sha256_of(target), period_year=year, period_month=month,
            version=version, uploader=actor, uploaded_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        # 仅删除本次用排他方式创建的文件；绝不碰已有版本。
        target.unlink(missing_ok=True)
        raise
    audit(db, actor, "finance.file.upload", "archive_files", row.id,
          {"category": category, "period": f"{year}-{month:02d}", "version": version, "sha256": row.sha256[:16]})
    refresh_period_status(db, company, year, month)
    return row


def delete_archive_file(db: Session, file_id: int, actor: str = "system") -> dict[str, Any]:
    """删除归档文件（不可恢复）：数据库记录 + 物理文件（存在时）。

    物理文件缺失或历史路径失效时仅删记录，不视为失败；删除后刷新账期状态。
    """
    row = db.get(ArchiveFile, file_id)
    if row is None:
        raise ValueError(f"归档文件不存在：{file_id}")
    referenced = (
        db.query(FinanceDeliveryFile.id)
        .filter(FinanceDeliveryFile.archive_file_id == file_id)
        .first()
    )
    if referenced is not None:
        raise ValueError("该原始资料已被财务交付包引用，请先删除未发送的交付包；已发送版本不可破坏")
    stored_path, company, year, month = row.stored_path, row.company, row.period_year, row.period_month
    name, version = row.original_name, row.version
    db.delete(row)
    db.commit()
    audit(db, actor, "finance.file.delete", "archive_files", file_id,
          {"name": name, "period": f"{year}-{month:02d}", "version": version})
    file_removed = False
    if stored_path:
        try:
            managed_data_file(stored_path, label="原始归档文件").unlink(missing_ok=True)
            file_removed = True
        except Exception:
            file_removed = False
    refresh_period_status(db, company, year, month)
    return {"ok": True, "id": file_id, "fileRemoved": file_removed}


def delete_delivery_package(db: Session, package_id: int, actor: str = "system") -> dict[str, Any]:
    """只允许删除未发送交付包；SENT 版本永久保留，确保财务发送审计链可追溯。"""
    row = db.get(FinanceDeliveryPackage, package_id)
    if row is None:
        raise ValueError(f"交付包不存在：{package_id}")
    if row.status == "SENT":
        raise ValueError("该交付包已经发送给财务，属于不可变审计版本，禁止删除")
    zip_path, version, status = row.zip_path, row.version, row.status
    db.delete(row)
    db.commit()
    audit(db, actor, "finance.package.delete", "finance_delivery_packages", package_id,
          {"version": version, "status": status, "zip": Path(zip_path).name if zip_path else ""})
    file_removed = False
    if zip_path:
        try:
            managed_data_file(zip_path, label="ZIP 文件").unlink(missing_ok=True)
            file_removed = True
        except Exception:
            file_removed = False
    return {"ok": True, "id": package_id, "fileRemoved": file_removed}


def _delivery_filename(year: int, month: int, f: ArchiveFile) -> str | None:
    """原始归档文件 → 发给财务的中文表名（带月份、去公司全称/版本号）。

    原始归档层只映射银行交易明细、银行回单详情；另外两张月度交付表
    “销售出库-无票收入”“已收票对公付款明细”由系统在打包时实时生成。
    销售汇总/出库 CSV 等内部账期台账不进入交付包。
    """
    name = f.original_name or ""
    ext = Path(name).suffix or ""
    prefix = f"{month}月"
    if "交易明细" in name:
        return f"{prefix}-银行交易明细{ext}"
    if "回单详情" in name:
        return f"{prefix}-银行回单详情{ext}"
    return None


def require_business_cost_ready(
    db: Session, *, company: str, year: int, month: int
) -> dict[str, Any]:
    """校验月结销售成本是否能由业务库完整计算，不再依赖月结页上传源文件。"""
    from app.services import finance_sales_report_service as sales_report_service

    template = sales_report_service.get_or_create_template(db, company)
    report = sales_report_service.build_report(db, year, month, template)
    summary = report.get("summary") or {}
    if summary.get("costIncomplete"):
        missing = summary.get("costMissingDetail") or []
        codes = "、".join(
            str(item.get("skuCode") or item.get("skuName") or "未知SKU")
            for item in missing[:12]
        )
        suffix = "…" if len(missing) > 12 else ""
        raise ValueError(
            "销售成本数据不完整，请先在采购/入库模块补齐以下 SKU 的入库成本："
            + (codes or "存在缺失成本的销售 SKU")
            + suffix
        )
    return summary


def package_period(db: Session, company: str, year: int, month: int,
                   actor: str = "system", include: list[str] | None = None) -> FinanceDeliveryPackage:
    """打包账期交付 ZIP；每次生成新版本号，ZIP 落 output/V{n}，绝不覆盖。

    include 控制银行/无票收入/已收票对公付款交付资料；如果该公司主体当月存在外贸 FinanceEntry，
    系统会额外自动生成「外贸财务汇总.xlsx」，无需用户重复勾选。
    """
    validate_period(year, month)
    selective = include is not None
    if selective:
        include = [x.strip() for x in include if x and x.strip()]
        unknown = [x for x in include if x not in DELIVERY_TABLES]
        if unknown:
            raise ValueError(f"未知的交付内容：{'、'.join(unknown)}")
        if not include:
            raise ValueError("请至少选择一张交付表")
    else:
        include = list(DELIVERY_TABLES)
    want_tx = "交易明细" in include
    want_receipt = "回单详情" in include
    want_unbilled = "无票收入" in include
    want_corporate_payment = "已收票对公付款明细" in include

    period = get_or_create_period(db, company, year, month)
    files = db.query(ArchiveFile).filter_by(company=company, period_year=year, period_month=month).all()
    status, summary = evaluate_completeness(
        files, delivery_required_types(period.required_types)
    )
    if not selective and (status != "READY" or not files):
        raise ValueError(f"资料不完整，禁止打包，缺少: {summary['missing']}")

    # 同名文件保留最高版本（银行交易明细/回单详情等会随重新上传递增版本，
    # 历史版本只留存档供追溯，不进入发给财务的交付包，避免旧格式混淆）。
    latest_by_name: dict[tuple[str, str], ArchiveFile] = {}
    for f in files:
        key = (f.category, f.original_name)
        current = latest_by_name.get(key)
        if current is None or f.version > current.version:
            latest_by_name[key] = f

    # 只取交付需要的表；销售汇总/出库 CSV 等系统台账不进入财务交付包。
    selected: list[tuple[ArchiveFile, str]] = []
    for f in sorted(latest_by_name.values(), key=lambda f: (f.category, f.original_name, f.version)):
        arc_name = _delivery_filename(year, month, f)
        if not arc_name:
            continue
        if "交易明细" in arc_name and not want_tx:
            continue
        if "回单详情" in arc_name and not want_receipt:
            continue
        selected.append((f, arc_name))
    if want_tx and not any("交易明细" in name for _, name in selected):
        raise ValueError("缺少「银行交易明细」文件，请先上传")
    if want_receipt and not any("回单详情" in name for _, name in selected):
        raise ValueError("缺少「银行回单详情」文件，请先上传")

    # 无票收入直接由销售中心 + 采购入库成本事实动态生成。
    # 缺销售成本时禁止生成错误交付包，但不再要求月结页重复上传业务源文件。
    unbilled_content: bytes | None = None
    if want_unbilled:
        require_business_cost_ready(
            db, company=company, year=year, month=month
        )
        from app.services import finance_sales_report_service as sales_report_service
        unbilled_content = sales_report_service.unbilled_income_xlsx(
            sales_report_service.build_unbilled_income_report(db, year, month, company=company)
        )
    unbilled_name = f"{month}月-销售出库-无票收入.xlsx"

    # 已收票且通过对公账户付款：直接读取银行付款↔进项发票事实，并下钻采购订单/商品。
    corporate_payment_content: bytes | None = None
    if want_corporate_payment:
        from app.services import finance_corporate_payment_report_service as corporate_payment_service
        selection = corporate_payment_service.latest_adjustment(
            db, company=company, year=year, month=month
        )
        corporate_payment_report = corporate_payment_service.build_report(
            db, year, month, company=company,
            selected_keys=(list(selection.selected_keys or []) if selection else None),
        )
        corporate_payment_content = corporate_payment_service.corporate_payment_xlsx(
            corporate_payment_report
        )
    corporate_payment_name = f"{month}月-已收票对公付款明细.xlsx"

    # 外贸财务汇总直接来自该公司主体的 FinanceEntry，不复制 Shipment/税费计算逻辑。
    from app.services import finance_closing_service
    foreign_summary_content: bytes | None = None
    entity = finance_closing_service.resolve_entity_by_name(db, company)
    if entity is not None:
        foreign_summary_content = finance_closing_service.foreign_trade_xlsx(
            db,
            legal_entity_id=entity.id,
            year=year,
            month=month,
        )
    foreign_summary_name = f"{month}月-外贸财务汇总.xlsx"

    # 归档表的 stored_path 也属于不可信持久化数据：打包前再次做目录边界校验。
    source_files = [
        (f, arc_name, managed_data_file(f.stored_path, label="原始归档文件"))
        for f, arc_name in selected
    ]

    prev = (
        db.query(func.max(FinanceDeliveryPackage.version))
        .filter_by(period_id=period.id)
        .scalar()
    )
    version = (prev or 0) + 1
    zip_path: Path | None = None
    created_zip = False
    while True:
        out_dir = (
            Path(settings.DATA_DIR) / "finance" / sanitize_name(company)
            / f"{year:04d}" / f"{month:02d}" / "output" / f"V{version}"
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        candidate = out_dir / f"finance_{sanitize_name(company)}_{year:04d}{month:02d}_V{version}.zip"
        try:
            archive = zipfile.ZipFile(candidate, "x", zipfile.ZIP_DEFLATED)
        except FileExistsError:
            version += 1
            continue
        zip_path = candidate
        created_zip = True
        with archive as zf:  # 原样打包，不做二次加工
            for f, arc_name, source_path in source_files:
                zf.write(source_path, arcname=arc_name)
            if unbilled_content is not None:
                zf.writestr(unbilled_name, unbilled_content)
            if corporate_payment_content is not None:
                zf.writestr(corporate_payment_name, corporate_payment_content)
            if foreign_summary_content is not None:
                zf.writestr(foreign_summary_name, foreign_summary_content)
        break

    try:
        pkg = FinanceDeliveryPackage(
            period_id=period.id, version=version, zip_path=str(zip_path),
            zip_sha256=sha256_of(zip_path), status="PACKAGED",
            created_at_src=datetime.now(timezone.utc),
        )
        db.add(pkg)
        db.flush()
        for f, _, _ in source_files:
            db.add(FinanceDeliveryFile(package_id=pkg.id, archive_file_id=f.id))
        period.status = "PACKAGED"
        db.commit()
    except Exception:
        db.rollback()
        if created_zip and zip_path is not None:
            zip_path.unlink(missing_ok=True)
        raise
    audit(db, actor, "finance.package.create", "finance_delivery_packages", pkg.id,
          {
              "version": version,
              "files": len(source_files),
              "generatedUnbilled": unbilled_content is not None,
              "generatedCorporatePayment": corporate_payment_content is not None,
              "generatedForeignSummary": foreign_summary_content is not None,
              "sha256": pkg.zip_sha256[:16],
          })
    return pkg


def period_overview(db: Session, company: str | None = None) -> list[dict[str, Any]]:
    """列出所有账期（来自归档文件与账期表的并集）及状态/交付包。"""
    periods_q = (
        db.query(MonthlyFinancePeriod)
        if not company
        else db.query(MonthlyFinancePeriod).filter_by(company=company)
    )
    periods: dict[tuple[str, int, int], dict[str, Any]] = {}
    for row in periods_q.all():
        periods[(row.company, row.period_year, row.period_month)] = {
            "company": row.company, "year": row.period_year, "month": row.period_month,
            "status": row.status, "missing": (row.missing_summary or {}).get("missing", {}),
            "requiredTypes": delivery_required_types(row.required_types),
        }
    archive_q = db.query(ArchiveFile)
    if company:
        archive_q = archive_q.filter_by(company=company)
    for f in archive_q.all():
        key = (f.company, f.period_year, f.period_month)
        if key not in periods:
            periods[key] = {
                "company": f.company, "year": f.period_year, "month": f.period_month,
                "status": "INCOMPLETE", "missing": {}, "requiredTypes": delivery_required_types(DEFAULT_REQUIRED),
            }

    file_count_rows = db.query(
        ArchiveFile.company,
        ArchiveFile.period_year,
        ArchiveFile.period_month,
        func.count(ArchiveFile.id),
    )
    if company:
        file_count_rows = file_count_rows.filter(ArchiveFile.company == company)
    file_counts: dict[tuple[str, int, int], int] = {
        (c, y, m): n for c, y, m, n in file_count_rows.group_by(
            ArchiveFile.company, ArchiveFile.period_year, ArchiveFile.period_month
        ).all()
    }

    pkg_query = (
        db.query(FinanceDeliveryPackage, MonthlyFinancePeriod.company,
                 MonthlyFinancePeriod.period_year, MonthlyFinancePeriod.period_month)
        .join(MonthlyFinancePeriod, FinanceDeliveryPackage.period_id == MonthlyFinancePeriod.id)
        .order_by(FinanceDeliveryPackage.version)
    )
    if company:
        pkg_query = pkg_query.filter(MonthlyFinancePeriod.company == company)
    pkg_groups: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for pkg, c, y, m in pkg_query.all():
        pkg_groups.setdefault((c, y, m), []).append({
            "id": pkg.id, "version": pkg.version, "status": pkg.status,
            "sha256": pkg.zip_sha256[:16],
            "createdAt": pkg.created_at.isoformat() if pkg.created_at else None,
        })

    out = []
    for key, item in periods.items():
        item["fileCount"] = file_counts.get(key, 0)
        item["packages"] = pkg_groups.get(key, [])
        out.append(item)
    out.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    return out


def send_delivery(db: Session, company: str, year: int, month: int, *,
                  version: int | None = None, to_addrs: list[str] | None = None,
                  cc_addrs: list[str] | None = None, actor: str = "system") -> dict[str, Any]:
    """发送财务交付包；首次发送与重发分别留痕。"""
    from app.adapters.mail import MailAdapter
    from app.models.finance import EmailDeliveryLog
    adapter = MailAdapter()
    adapter.ensure_configured()

    period = get_or_create_period(db, company, year, month)
    q = db.query(FinanceDeliveryPackage).filter_by(period_id=period.id)
    if version:
        q = q.filter_by(version=version)
    pkg = q.order_by(FinanceDeliveryPackage.version.desc()).with_for_update().first()
    if not pkg:
        raise ValueError("该账期还没有交付包，请先打包")

    zip_path = managed_data_file(pkg.zip_path, label="ZIP 文件")
    to_addrs = to_addrs or list((db.get(MonthlyFinancePeriod, period.id).required_types or {}).get("emails", []) or [])
    if not to_addrs:
        raise ValueError("未配置收件人（需在账期 required_types.emails 或发送时传入 to_addrs）")

    first_sent = (
        db.query(EmailDeliveryLog)
        .filter_by(package_id=pkg.id, kind="first", status="sent")
        .first()
    )
    kind = "resent" if first_sent else "first"

    subject = f"{company} {year}年{month:02d}月 财务资料 V{version or pkg.version}"
    body = f"见附件 {zip_path.name}\n（原样资料，SHA256: {pkg.zip_sha256}）"
    try:
        message_id = adapter.send(subject, body, to_addrs, cc_addrs or [], [str(zip_path)])
    except Exception as exc:
        log = EmailDeliveryLog(package_id=pkg.id, kind=kind, to_addrs=list(to_addrs),
                               cc_addrs=list(cc_addrs or []), status="failed", error=str(exc)[:2000])
        db.add(log)
        db.commit()
        audit(db, actor, "finance.delivery.send.failed", "email_delivery_logs", log.id,
              {"packageId": pkg.id, "kind": kind, "error": str(exc)[:500]})
        raise RuntimeError(f"邮件发送失败: {exc}") from exc

    log = EmailDeliveryLog(package_id=pkg.id, kind=kind, to_addrs=list(to_addrs),
                           cc_addrs=list(cc_addrs or []), status="sent",
                           message_id=message_id or "")
    db.add(log)
    pkg.status = "SENT"
    if period.status != "SENT":
        period.status = "SENT"
    db.commit()
    audit(db, actor, "finance.delivery.send", "email_delivery_logs", log.id,
          {"packageId": pkg.id, "kind": kind, "to": to_addrs, "messageId": message_id})
    return {"packageId": pkg.id, "version": pkg.version, "kind": kind,
            "messageId": message_id, "sentAt": log.created_at.isoformat() if log.created_at else None}


def delivery_logs(db: Session, company: str | None = None) -> list[dict[str, Any]]:
    from app.models.finance import EmailDeliveryLog, MonthlyFinancePeriod

    q = db.query(EmailDeliveryLog).order_by(EmailDeliveryLog.id.desc()).limit(200)
    out = []
    for r in q.all():
        pkg = db.get(FinanceDeliveryPackage, r.package_id) if r.package_id else None
        period_label = None
        version = None
        if pkg:
            period = db.get(MonthlyFinancePeriod, pkg.period_id) if pkg.period_id else None
            if period:
                period_label = f"{period.period_year}-{period.period_month:02d}"
            version = pkg.version
        out.append({
            "id": r.id, "packageId": r.package_id, "kind": r.kind,
            "toAddrs": r.to_addrs or [], "ccAddrs": r.cc_addrs or [],
            "status": r.status, "messageId": r.message_id, "error": r.error,
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "period": period_label, "version": version,
        })
    return out
