from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.config import settings
from app.db import get_db
from app.models.bank import BankTransaction
from app.models.tax import TaxInvoice
from app.services import finance_sales_report_service as sales_report_service
from app.services import finance_center_service
from app.services import bank_summary_service
from app.services import finance_corporate_payment_report_service as corporate_payment_report_service
from app.services import finance_closing_service
from app.services import finance_projection_service
from app.services import finance_service
from app.services import monthly_intake_service
from app.services import payment_invoice_match_service as payment_match_service
from app.services import reconciliation as reconciliation_service
from app.utils.uploads import UploadTooLargeError, read_upload_limited

router = APIRouter(prefix="/finance", tags=["finance"])


class SalesReportFieldInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True


class SalesReportTemplateInput(BaseModel):
    company: str = ""
    enabled: bool = True
    fields: list[SalesReportFieldInput] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    auto_send: bool = False
    send_day: int = Field(default=3, ge=1, le=28)
    send_hour: int = Field(default=10, ge=0, le=23)


class UnbilledAdjustmentInput(BaseModel):
    selected_keys: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=500)


class CorporatePaymentAdjustmentInput(BaseModel):
    selected_keys: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=500)


class PaymentMatchLinkBody(BaseModel):
    txn_id: int
    invoice_id: int
    allocated_amount: str | None = None
    note: str = Field(default="", max_length=500)


@router.get("/sales-report/template")
def get_sales_report_template(company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    row = sales_report_service.get_or_create_template(
        db, company or finance_service.DEFAULT_COMPANY
    )
    return sales_report_service.serialize_template(row)


@router.put("/sales-report/template")
def update_sales_report_template(
    payload: SalesReportTemplateInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = sales_report_service.save_template(
            db,
            company=payload.company or finance_service.DEFAULT_COMPANY,
            fields=[field.model_dump() for field in payload.fields],
            rules=payload.rules,
            to_addrs=payload.to_addrs,
            cc_addrs=payload.cc_addrs,
            auto_send=payload.auto_send,
            send_day=payload.send_day,
            send_hour=payload.send_hour,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return sales_report_service.serialize_template(row)


@router.get("/sales-report/preview")
def preview_sales_report(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    template = sales_report_service.get_or_create_template(
        db, company or finance_service.DEFAULT_COMPANY
    )
    report = sales_report_service.build_report(db, year, month, template)
    return {
        **report,
        "rows": report["rows"][:limit],
        "rowCount": len(report["rows"]),
    }


@router.post("/sales-report/generate")
def generate_sales_report(
    request: Request,
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return sales_report_service.generate_and_archive(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            actor=current_actor(request),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{year}/{month}/refresh-business")
def refresh_monthly_business(
    year: int,
    month: int,
    company: str = "",
    legal_entity_id: int | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """月结读取业务库：主体隔离 + FinanceEntry 同步，不再接收业务源文件。"""
    finance_service.validate_period(year, month)
    entity = finance_center_service.resolve_entity(db, legal_entity_id)
    if company and entity.name != company:
        matched = finance_closing_service.resolve_entity_by_name(db, company)
        if matched is None:
            raise HTTPException(status_code=404, detail="公司主体不存在")
        entity = matched
    company_name = entity.name

    # 全量同步是幂等的：内销进入当前默认中国主体，外贸按各自订单/Shipment 的主体归属。
    sync_result = finance_projection_service.sync_business_period(
        db,
        year=year,
        month=month,
        business_scope="all",
    )

    # 现有内销销售主表尚未拆 legal_entity_id，因此只允许默认主体读取国内销售/月结口径；
    # 其他主体只读取自己的 FinanceEntry，避免把浙江公司的销售复制过去。
    domestic_supported = bool(entity.is_default and "domestic" in (entity.business_scopes or []))
    if domestic_supported:
        template = sales_report_service.get_or_create_template(db, company_name)
        report = sales_report_service.build_report(db, year, month, template)
        summary = report.get("summary") or {}
        missing = summary.get("costMissingDetail") or []
    else:
        summary = {
            "orderCount": 0,
            "warehouseCount": 0,
            "totalQuantity": "0",
            "salesAmount": "0",
            "costAmount": "0",
            "costIncomplete": False,
            "costMissingDetail": [],
        }
        missing = []

    foreign_summary = finance_closing_service.foreign_trade_summary(
        db,
        legal_entity_id=entity.id,
        year=year,
        month=month,
    )
    return {
        "ready": not bool(summary.get("costIncomplete")),
        "source": "business_database",
        "legalEntityId": entity.id,
        "company": company_name,
        "businessScopes": entity.business_scopes or [],
        "domesticSupported": domestic_supported,
        "salesSource": "sales_orders" if domestic_supported else "",
        "costSource": "jackyun_goods_documents" if domestic_supported else "",
        "salesOrderCount": int(summary.get("orderCount") or 0),
        "warehouseCount": int(summary.get("warehouseCount") or 0),
        "totalQuantity": str(summary.get("totalQuantity") or "0"),
        "salesAmount": str(summary.get("salesAmount") or "0"),
        "costAmount": str(summary.get("costAmount") or "0"),
        "costIncomplete": bool(summary.get("costIncomplete")),
        "costMissingCount": len(missing),
        "costMissingDetail": missing,
        "foreignEntryCount": foreign_summary["rowCount"],
        "foreignTotalsByCurrency": foreign_summary["totalsByCurrency"],
        "sync": sync_result,
    }


@router.get("/periods")
def list_periods(company: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.period_overview(db, company)


@router.get("/unbilled/preview")
def preview_unbilled_income(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """无票收入预览：销售总金额 − 已开票金额（与交付包第 3 张表同口径）。"""
    return sales_report_service.build_unbilled_income_report(
        db, year, month, company=company or finance_service.DEFAULT_COMPANY,
    )


@router.put("/unbilled/adjustment")
def update_unbilled_adjustment(
    payload: UnbilledAdjustmentInput,
    request: Request,
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存本月无票收入明细选择；新版本保留，历史版本不覆盖。"""
    try:
        return sales_report_service.save_unbilled_adjustment(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            selected_keys=payload.selected_keys,
            actor=current_actor(request),
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/files")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    period_year: int = Form(...),
    period_month: int = Form(...),
    category: str = Form(...),
    original_name: str = Form(""),
    company: str = Form(""),
    account_no: str = Form(""),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """原始资料上传：SHA256 + 版本化归档，同名不覆盖。

    original_name 可选：页面用标准表名（银行交易明细/银行回单详情）归档，
    不依赖用户本地文件名，保证交付包命名映射稳定。
    """
    try:
        content = await read_upload_limited(file, max_bytes=finance_service.settings.MAX_UPLOAD_BYTES)
        row = finance_service.store_upload(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=period_year, month=period_month,
            category=category,
            original_name=original_name.strip() or file.filename or "unnamed",
            content=content, actor=current_actor(request),
        )
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    result: dict[str, Any] = {
        "id": row.id, "version": row.version, "sha256": row.sha256,
        "size": row.size, "storedPath": row.stored_path,
    }
    # 银行交易明细上传即解析入流水表（指纹幂等，重复上传无害）；
    # 回单详情同为 category=bank，必须用 original_name 区分，避免误解析。
    display_name = original_name.strip() or file.filename or ""
    company_name = company or finance_service.DEFAULT_COMPANY
    can_parse_zjrc = company_name == finance_service.DEFAULT_COMPANY
    if (
        category == "bank"
        and "交易明细" in display_name
        and display_name.lower().endswith(".xlsx")
        and can_parse_zjrc
    ):
        try:
            bank_result = reconciliation_service.import_bank_xlsx(
                db, account_no=account_no.strip(), content=content,
                period_year=period_year, period_month=period_month,
                file_name=display_name, archive_file_id=row.id,
                actor=current_actor(request),
            )
            result["bankImport"] = bank_result
        except (ValueError, RuntimeError) as exc:
            # 原件归档已成功，解析失败不让上传整体失败，仅提示
            result["bankImportError"] = str(exc)
    elif category == "bank" and "交易明细" in display_name and not can_parse_zjrc:
        result["bankImportSkipped"] = (
            "当前主体尚未配置独立银行账户解析规则；原件已归档，未写入浙江农信流水。"
        )
    return result


@router.get("/bank-summary")
def bank_summary(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """银行账户汇总：账户余额、本月收支与待对账数量。"""
    try:
        return bank_summary_service.build_summary(db, year=year, month=month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/corporate-payment-report")
def corporate_payment_report(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """月度已收票且通过对公账户付款的发票/采购/商品清单（含当前勾选版本信息）。"""
    try:
        report = corporate_payment_report_service.build_report(db, year, month, company=company)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    adjustment = corporate_payment_report_service.latest_adjustment(
        db, company=company or finance_service.DEFAULT_COMPANY, year=year, month=month
    )
    source_count = len(report.get("invoiceRows", []))
    if adjustment is not None:
        report["adjusted"] = True
        report["version"] = adjustment.version
        try:
            report["selectedKeys"] = corporate_payment_report_service.canonical_report_selection_keys(
                report, list(adjustment.selected_keys or [])
            )
            report["selectionError"] = ""
        except ValueError as exc:
            # 历史版本可能使用 invoice_number。唯一号码自动兼容；歧义号码必须人工重选。
            report["selectedKeys"] = []
            report["selectionError"] = str(exc)
        report["updatedAt"] = adjustment.updated_at.isoformat() if adjustment.updated_at else None
    else:
        report["adjusted"] = False
        report["version"] = None
        report["selectedKeys"] = [
            row.get("invoiceKey") for row in report.get("invoiceRows", []) if row.get("invoiceKey")
        ]
        report["selectionError"] = ""
        report["updatedAt"] = None
    report["selectedCount"] = len(report["selectedKeys"])
    report["sourceCount"] = source_count
    return report


@router.put("/corporate-payment/adjustment")
def update_corporate_payment_adjustment(
    payload: CorporatePaymentAdjustmentInput,
    request: Request,
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """保存本月已收票对公付款清单的发票勾选；新版本保留，历史版本不覆盖。"""
    try:
        return corporate_payment_report_service.save_corporate_payment_adjustment(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            selected_keys=payload.selected_keys,
            actor=current_actor(request),
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/payment-invoice-match/invoices")
def payment_invoice_match_pending_invoices(
    limit: int = Query(500, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """跨账期返回仍有银行付款待核对余额的有效进项发票。"""
    return payment_match_service.pending_invoices(db, limit=limit)


@router.get("/payment-invoice-match/{year}/{month}")
def payment_invoice_match_overview(
    year: int,
    month: int,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """当月银行付款 ↔ 对方进项发票 匹配清单（推导展示，手工标记才落库）。"""
    try:
        return payment_match_service.overview(db, year, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/payment-invoice-match/link")
def payment_invoice_match_link(
    payload: PaymentMatchLinkBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """标记已开票：把银行付款挂到进项发票（tax_invoice_links, target_type=bank_transaction）。"""
    if db.get(BankTransaction, payload.txn_id) is None:
        raise HTTPException(status_code=404, detail="银行流水不存在")
    if db.get(TaxInvoice, payload.invoice_id) is None:
        raise HTTPException(status_code=404, detail="发票不存在")
    try:
        return payment_match_service.link(
            db,
            txn_id=payload.txn_id,
            invoice_id=payload.invoice_id,
            allocated_amount=payload.allocated_amount,
            note=payload.note,
            actor=current_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/payment-invoice-match/auto")
def payment_invoice_match_auto(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    request: Request = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """按账号/供应商、金额与唯一最近日期规则自动匹配；歧义候选不落库，并纠正明显旧版自动错配。"""
    from app.services.payment_invoice_match_service import auto_match
    result = auto_match(db, year=year, month=month, actor=current_actor(request))
    return result


@router.delete("/payment-invoice-match/link/{link_id}")
def payment_invoice_match_unlink(
    link_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """解除标记（软删可审计，同一对可再次标记）。"""
    try:
        return payment_match_service.unlink(db, link_id, actor=current_actor(request))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{year}/{month}/intake")
def monthly_intake_status(
    year: int,
    month: int,
    company: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """返回当前账期两份业务源文件的上传与真实导入状态。"""
    try:
        return monthly_intake_service.status(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{year}/{month}/intake")
async def upload_monthly_intake(
    year: int,
    month: int,
    request: Request,
    source_type: str = Form(...),
    company: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """归档并导入月度业务源文件：采购入库单或销售单查询。"""
    try:
        content = await read_upload_limited(
            file, max_bytes=finance_service.settings.MAX_UPLOAD_BYTES
        )
        return monthly_intake_service.ingest(
            db,
            company=company or finance_service.DEFAULT_COMPANY,
            year=year,
            month=month,
            source_type=source_type,
            content=content,
            original_name=file.filename or "monthly-source.xlsx",
            actor=current_actor(request),
        )
    except UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{year}/{month}/files")
def list_files(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    from app.models.finance import ArchiveFile

    try:
        finance_service.validate_period(year, month)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    q = db.query(ArchiveFile).filter_by(period_year=year, period_month=month)
    if company:
        q = q.filter_by(company=company)
    return [
        {
            "id": r.id, "category": r.category, "originalName": r.original_name,
            "size": r.size, "sha256": r.sha256[:16], "version": r.version,
            "uploader": r.uploader,
            "uploadedAt": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in q.order_by(ArchiveFile.category, ArchiveFile.original_name, ArchiveFile.version).all()
    ]


@router.get("/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)) -> FileResponse:
    """单文件下载：财务核对原始资料用（归档原文，未改动）。"""
    from app.models.finance import ArchiveFile

    row = db.get(ArchiveFile, file_id)
    if row is None:
        raise HTTPException(status_code=404, detail="归档文件不存在")
    try:
        path = finance_service.managed_data_file(row.stored_path, label="归档文件")
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(
        path,
        filename=row.original_name,
        media_type=row.mime or "application/octet-stream",
        headers={"X-Archive-SHA256": row.sha256},
    )


@router.delete("/files/{file_id}")
def remove_archive_file(file_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """删除归档文件（含磁盘文件），页面「归档明细」用。"""
    try:
        return finance_service.delete_archive_file(db, file_id, actor=current_actor(request))
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/packages/{package_id}")
def remove_delivery_package(package_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """删除交付包记录与 ZIP 文件，页面「发送记录」用。"""
    try:
        return finance_service.delete_delivery_package(db, package_id, actor=current_actor(request))
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 409
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.post("/{year}/{month}/check")
def check_period(year: int, month: int, company: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    period = finance_service.refresh_period_status(
        db, company or finance_service.DEFAULT_COMPANY, year, month
    )
    return {"status": period.status, "missing": (period.missing_summary or {}).get("missing", {})}


class PackageInput(BaseModel):
    """手动打包时可选交付表子集；不传或为空列表 = 全部月度交付表。"""
    include: list[str] = Field(default_factory=list)


@router.post("/{year}/{month}/package")
def package_period(year: int, month: int, request: Request, company: str = "",
                   payload: PackageInput | None = None,
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        include = payload.include if payload and payload.include else None
        pkg = finance_service.package_period(
            db, company or finance_service.DEFAULT_COMPANY, year, month,
            actor=current_actor(request), include=include,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": pkg.id, "version": pkg.version, "status": pkg.status,
        "sha256": pkg.zip_sha256,
    }


@router.get("/packages/{pkg_id}/download")
def download_package(pkg_id: int, db: Session = Depends(get_db)) -> FileResponse:
    from app.models.finance import FinanceDeliveryPackage

    pkg = db.get(FinanceDeliveryPackage, pkg_id)
    if not pkg:
        raise HTTPException(404, "交付包不存在")
    try:
        zip_path = finance_service.managed_data_file(pkg.zip_path, label="ZIP 文件")
    except RuntimeError as exc:
        raise HTTPException(410, str(exc))
    return FileResponse(str(zip_path), media_type="application/zip", filename=zip_path.name)


class SendBody(BaseModel):
    version: int | None = None
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    company: str = ""


@router.post("/{year}/{month}/send")
def send(year: int, month: int, body: SendBody, request: Request,
         db: Session = Depends(get_db)) -> dict[str, Any]:
    """发送财务交付包：SMTP 未配置如实失败；已发 V1 重发标记 RESENT。"""
    from app.adapters.base import AdapterNotConfigured

    try:
        result = finance_service.send_delivery(
            db, body.company or finance_service.DEFAULT_COMPANY, year, month,
            version=body.version, to_addrs=body.to_addrs or None, cc_addrs=body.cc_addrs or None,
            actor=current_actor(request),
        )
    except AdapterNotConfigured as exc:
        raise HTTPException(400, str(exc))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **result}


@router.get("/mail-status")
def mail_status() -> dict[str, Any]:
    """财务邮件发送方状态（只读，不含密码），供「设置邮箱」展示当前发件邮箱与 SMTP 配置状态。"""
    return {
        "configured": settings.smtp_configured,
        "host": settings.SMTP_HOST,
        "port": settings.SMTP_PORT,
        "username": settings.SMTP_USERNAME,
        "from": settings.SMTP_FROM,
    }


@router.get("/delivery-logs")
def delivery_logs(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return finance_service.delivery_logs(db)



class LegalEntityInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    country_code: str = Field(default="CN", max_length=8)
    base_currency: str = Field(default="CNY", max_length=8)
    tax_id: str = Field(default="", max_length=128)
    status: str = Field(default="active", max_length=24)
    is_default: bool = False
    business_scopes: list[str] = Field(default_factory=lambda: ["domestic", "foreign_trade"])
    note: str = ""


class FinanceEntryInput(BaseModel):
    legal_entity_id: int
    business_scope: str = Field(default="domestic", max_length=24)
    source_type: str = Field(default="manual", max_length=48)
    source_id: str = Field(default="", max_length=128)
    source_no: str = Field(default="", max_length=128)
    category: str = Field(default="other", max_length=64)
    direction: str = Field(default="expense", max_length=24)
    cash_effect: bool = True
    profit_effect: bool = True
    currency: str = Field(default="CNY", max_length=8)
    amount: str = "0"
    tax_amount: str = "0"
    value_type: str = Field(default="actual", max_length=16)
    settlement_status: str = Field(default="pending", max_length=24)
    invoice_status: str = Field(default="unknown", max_length=24)
    accounting_year: int = Field(ge=1900, le=2999)
    accounting_month: int = Field(ge=1, le=12)
    occurred_at: datetime | None = None
    note: str = ""


@router.get("/entities")
def finance_entities(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": finance_center_service.list_entities(db)}


@router.post("/entities", status_code=201)
def create_finance_entity(payload: LegalEntityInput, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = finance_center_service.save_entity(
            db,
            entity_id=None,
            code=payload.code,
            name=payload.name,
            country_code=payload.country_code,
            base_currency=payload.base_currency,
            tax_id=payload.tax_id,
            status=payload.status,
            is_default=payload.is_default,
            business_scopes=payload.business_scopes,
            note=payload.note,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return finance_center_service.entity_dict(row)


@router.put("/entities/{entity_id}")
def update_finance_entity(
    entity_id: int,
    payload: LegalEntityInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = finance_center_service.save_entity(
            db,
            entity_id=entity_id,
            code=payload.code,
            name=payload.name,
            country_code=payload.country_code,
            base_currency=payload.base_currency,
            tax_id=payload.tax_id,
            status=payload.status,
            is_default=payload.is_default,
            business_scopes=payload.business_scopes,
            note=payload.note,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return finance_center_service.entity_dict(row)


@router.get("/center")
def finance_center(
    legal_entity_id: int | None = None,
    business_scope: str = Query("all", pattern="^(all|domestic|foreign_trade)$"),
    year: int | None = Query(None, ge=1900, le=2999),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return finance_center_service.center_overview(
            db,
            legal_entity_id=legal_entity_id,
            business_scope=business_scope,
            year=year,
            month=month,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/entries")
def finance_entries(
    legal_entity_id: int | None = None,
    business_scope: str = Query("all", pattern="^(all|domestic|foreign_trade)$"),
    year: int | None = Query(None, ge=1900, le=2999),
    month: int | None = Query(None, ge=1, le=12),
    status: str = Query("", max_length=24),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return {
            "items": finance_center_service.list_entries(
                db,
                legal_entity_id=legal_entity_id,
                business_scope=business_scope,
                year=year,
                month=month,
                status=status,
                limit=limit,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/entries", status_code=201)
def create_finance_entry(payload: FinanceEntryInput, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = finance_center_service.save_entry(
            db,
            entry_id=None,
            **payload.model_dump(),
        )
        entity = finance_center_service.resolve_entity(db, row.legal_entity_id)
        return finance_center_service.entry_dict(row, entity)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/entries/{entry_id}")
def update_finance_entry(
    entry_id: int,
    payload: FinanceEntryInput,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        row = finance_center_service.save_entry(
            db,
            entry_id=entry_id,
            **payload.model_dump(),
        )
        entity = finance_center_service.resolve_entity(db, row.legal_entity_id)
        return finance_center_service.entry_dict(row, entity)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc



@router.post("/sync-business")
def sync_business_finance(
    year: int = Query(..., ge=1900, le=2999),
    month: int = Query(..., ge=1, le=12),
    business_scope: str = Query("all", pattern="^(all|domestic|foreign_trade)$"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """把指定账期的业务事实幂等投影到统一财务事项池。"""
    try:
        return finance_projection_service.sync_business_period(
            db,
            year=year,
            month=month,
            business_scope=business_scope,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
