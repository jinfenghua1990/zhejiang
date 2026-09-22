import io
from uuid import uuid4

import pytest

from app.services import monthly_intake_service as svc


def _sales_workbook() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    orders = workbook.active
    orders.title = "销售单"
    orders.append(["订单编号", "销售渠道", "订单状态", "订单类型", "付款时间", "实付金额", "货品数量"])
    orders.append(["JY-MONTHLY-001", "1688", "已完成", "零售业务", "2026-08-08 10:00:00", 20, 2])
    items = workbook.create_sheet("销售单货品")
    items.append(["订单编号", "货品编号", "货品名称", "数量", "单价", "金额"])
    items.append(["JY-MONTHLY-001", "SKU-MONTHLY-001", "测试商品", 2, 10, 20])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_monthly_sales_source_is_archived_and_imported(db_session, tmp_path, monkeypatch):
    from app.models.catalog import ProductSku

    monkeypatch.setattr(svc.finance_service.settings, "DATA_DIR", str(tmp_path))
    # 销售导入现要求明细编码先建档（与 /sales-file/import 同一服务校验）。
    sku = db_session.query(ProductSku).filter_by(jackyun_sku_id="SKU-MONTHLY-001").one_or_none()
    if sku is None:
        db_session.add(ProductSku(
            jackyun_sku_id="SKU-MONTHLY-001",
            sku_code="SKU-MONTHLY-001",
            sku_name="测试商品",
            product_type="single",
        ))
        db_session.flush()
    company = f"monthly-{uuid4().hex}"

    result = svc.ingest(
        db_session,
        company=company,
        year=2026,
        month=8,
        source_type="sales_query",
        content=_sales_workbook(),
        original_name="销售单查询.xlsx",
        actor="pytest",
    )

    assert result["ok"] is True
    assert result["status"] == "IMPORTED"
    current = svc.status(db_session, company=company, year=2026, month=8)
    assert current["sourceReady"] is False
    assert current["readyCount"] == 1
    sales = next(row for row in current["sources"] if row["sourceType"] == "sales_query")
    assert sales["status"] == "IMPORTED"
    assert sales["archiveFile"]["category"] == "sales_query"

    # ingest 成功路径内部会 commit（绕过事务回滚），显式清理本次痕迹。
    db_session.query(ProductSku).filter(ProductSku.jackyun_sku_id == "SKU-MONTHLY-001").delete(
        synchronize_session=False,
    )
    db_session.commit()


def test_monthly_intake_require_ready_lists_each_missing_source(db_session):
    company = f"monthly-missing-{uuid4().hex}"

    with pytest.raises(ValueError, match="采购入库单.*销售单查询"):
        svc.require_ready(db_session, company=company, year=2026, month=8)
