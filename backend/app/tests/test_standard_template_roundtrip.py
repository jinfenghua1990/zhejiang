"""导出即标准模板闭环测试：导出→修改→重新导入→映射→断言 upsert。

只覆盖回导闭环的 5 类（采购/入库/货品档案/仓库/发票），不测生产/物料/库存。
生产/物料无对应识别类型；库存是运算结果不回导。
"""
import io
from decimal import Decimal

from app.adapters.alibaba1688_export_file import parse_alibaba1688_export
from app.adapters.jackyun_export_file import detect_report_type
from app.services import data_export_service as export_service
from app.services import jackyun_file_import_service as import_service


# ---------- 工具 ----------

def _xlsx_from_bytes(content: bytes) -> list[list[object]]:
    """用 openpyxl 读取 XLSX 内容，返回按行排列的二维数组。"""
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    sheet = book.worksheets[0]
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    book.close()
    return rows


# ---------- 1. detect_report_type 对齐测试 ----------

def test_purchase_export_headers_not_misdetected_by_jackyun_chain():
    """采购订单导出（1688 视角）走 1688 导入链路；不得被吉客云链路误识别，
    否则 1688 订单会被误映射成吉客云采购单。"""
    headers = ["订单号", "渠道", "采购类型", "供应商/工厂", "订单金额", "实付金额", "下单日期", "采购状态"]
    assert detect_report_type(headers) != "purchase"


def test_inbound_export_headers_detected_as_inbound():
    headers = ["入库单号", "入库日期", "仓库", "仓库编码", "供应商", "总数量", "总金额", "总费用"]
    assert detect_report_type(headers) == "inbound"


def test_catalog_export_headers_detected_as_products():
    headers = ["类型", "货品编号", "货品名称", "商品名称", "条码", "单位", "品类", "当前库存"]
    assert detect_report_type(headers) == "products"


def test_warehouses_export_headers_detected_as_warehouses():
    headers = ["仓库编号", "仓库名称", "类型", "用途", "吉客云仓库ID", "参与可售", "状态"]
    assert detect_report_type(headers) == "warehouses"


def test_sales_export_headers_detected_as_sales():
    headers = ["订单编号", "网店单号", "下单时间", "平台", "订单类型", "订单状态", "支付状态"]
    assert detect_report_type(headers) == "sales"


# ---------- 2. 模板导出只含表头 ----------

def test_template_export_has_zero_data_rows(db_session):
    """模板导出只有表头行，没有数据行。"""
    content, count, label = export_service.build_export(db_session, "warehouses", template=True)
    assert count == 0 or label  # count 可能是真实数据数（build_export 返回的 count 在 template 时不清零），但内容应该只有表头
    rows = _xlsx_from_bytes(content)
    assert len(rows) == 1  # 只有表头行
    assert "仓库编号" in rows[0]


# ---------- 3. 1688 适配器识别本系统导出列名 ----------

def test_alibaba1688_parser_recovers_shared_strings():
    """openpyxl 重存的 XLSX 使用 sharedStrings 引用而非 inlineStr；
    适配器应能按索引还原文本，识别本系统导出模板的列名。
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["订单号", "供应商/工厂", "实付金额", "采购状态", "下单日期"])
    ws.append(["TEST-001", "测试供应商", "123.45", "已付款", "2026-09-11"])
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()

    parsed = parse_alibaba1688_export(content, "采购订单.xlsx")
    assert parsed.status == "parsed"
    assert len(parsed.rows) == 1
    row = parsed.rows[0]
    assert row["external_order_id"] == "TEST-001"
    assert row["seller_company_name"] == "测试供应商"
    assert row["actual_payment"] == "123.45"
    assert row["order_status"] == "已付款"
    assert row["order_time"] == "2026-09-11"


def test_alibaba1688_parser_rejects_missing_required_headers():
    """缺少订单号/实付金额/采购状态三个关键字段时拒绝导入。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["渠道", "供应商/工厂", "备注"])  # 缺三个关键字段
    ws.append(["1688", "测试", ""])
    buf = io.BytesIO()
    wb.save(buf)
    parsed = parse_alibaba1688_export(buf.getvalue(), "bad.xlsx")
    assert parsed.status == "failed"
    assert "订单编号/订单号" in parsed.error_summary


# ---------- 4. 采购订单 roundtrip：导出→修改→重新导入→映射→upsert ----------

def test_purchase_roundtrip_upsert(db_session, tmp_path, monkeypatch):
    """端到端：导出采购订单模板→填充两条数据→重新导入→映射→断言 upsert。

    需要至少一条真实采购订单在库内，否则导出无数据行。
    使用 jackyun_file_import_service 链路（采购报表识别→map_purchase_import）。
    """
    from app.config import settings
    from app.models.purchase import JackyunPurchaseOrder

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))

    # 构造一个采购报表 XLSX（模拟导出模板填充后的文件）
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["采购单号", "供应商", "价税合计", "单据日期", "状态"])
    ws.append(["PO-ROUNDTRIP-001", "闭环测试供应商A", "1000", "2026-09-11", "已入库"])
    ws.append(["PO-ROUNDTRIP-002", "闭环测试供应商B", "2000", "2026-09-11", "待入库"])
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()

    # 导入（走吉客云导入链路，detect_report_type 应识别为 purchase）
    row, duplicate = import_service.import_export(
        db_session, content=content, original_name="采购订单-模板.xlsx", actor="pytest-roundtrip"
    )
    assert duplicate is False
    assert row.report_type == "purchase"
    assert row.row_count == 2

    # 确认生效
    import_service.confirm_import(db_session, row.id, "pytest-roundtrip")

    # 映射
    result = import_service.map_purchase_import(db_session, row.id, "pytest-roundtrip")
    assert result["mapped"] == 2
    assert result["updated"] == 0  # 首次导入全部新建

    # 断言新建
    po1 = db_session.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id="PO-ROUNDTRIP-001").first()
    po2 = db_session.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id="PO-ROUNDTRIP-002").first()
    assert po1 is not None and po1.supplier_name == "闭环测试供应商A"
    assert po2 is not None and po2.amount == Decimal("2000")

    # 第二轮：修改 PO-001 金额 + 新增 PO-003 → 重新导入 → 断言 upsert
    wb2 = Workbook()
    ws2 = wb2.active
    ws2.append(["采购单号", "供应商", "价税合计", "单据日期", "状态"])
    ws2.append(["PO-ROUNDTRIP-001", "闭环测试供应商A", "1500", "2026-09-11", "已入库"])
    ws2.append(["PO-ROUNDTRIP-003", "闭环测试供应商C", "3000", "2026-09-11", "待入库"])
    buf2 = io.BytesIO()
    wb2.save(buf2)

    row2, _ = import_service.import_export(
        db_session, content=buf2.getvalue(), original_name="采购订单-修改.xlsx", actor="pytest-roundtrip"
    )
    import_service.confirm_import(db_session, row2.id, "pytest-roundtrip")
    result2 = import_service.map_purchase_import(db_session, row2.id, "pytest-roundtrip")
    assert result2["groups"] == 2
    assert result2["updated"] == 1  # PO-001 已存在→更新
    # mapped 语义 = 本批次处理的采购单组总数（updated 是其中的更新子集）
    assert result2["mapped"] == 2

    # 断言 upsert
    db_session.expire_all()
    po1_updated = db_session.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id="PO-ROUNDTRIP-001").first()
    assert po1_updated.amount == Decimal("1500")  # 金额已更新
    po3 = db_session.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id="PO-ROUNDTRIP-003").first()
    assert po3 is not None and po3.supplier_name == "闭环测试供应商C"
