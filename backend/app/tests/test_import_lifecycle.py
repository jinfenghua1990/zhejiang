"""数据中心导入生命周期（draft / active / deleted）状态机与业务过滤测试。

不依赖真实 xlsx：
- 1688 / 吉客云 仅测状态机（直接构造导入行，验证 confirm/soft_delete/restore 的合法切换与非法拦截）；
- 税务走完整 CSV 导入链路（auto_confirm / draft → confirm → delete → restore），并验证业务查询按 lifecycle 过滤。
"""
import pytest

from app.models.alibaba1688_import import Alibaba1688FileImport, Alibaba1688Order
from app.models.jackyun_import import JackyunFileImport, JackyunFileImportRecord
from app.models.tax import TaxInvoice, TaxInvoiceImport, TaxInvoiceImportRecord
from app.services import (
    alibaba1688_import_service,
    jackyun_file_import_service,
    tax_invoice_service,
)
from app.services.import_lifecycle import LifecycleTransitionError


# ---------- 1688 状态机 ----------

def test_1688_lifecycle_state_machine(db_session):
    row = Alibaba1688FileImport(
        original_name="lifecycle.xlsx", stored_path="/tmp/lifecycle.xlsx",
        sha256="lifecycle-1688", lifecycle="draft",
    )
    db_session.add(row)
    db_session.flush()

    confirmed = alibaba1688_import_service.confirm_import(db_session, row.id, actor="pytest")
    assert confirmed.lifecycle == "active"

    deleted = alibaba1688_import_service.soft_delete_import(db_session, row.id, actor="pytest")
    assert deleted.lifecycle == "deleted"

    restored = alibaba1688_import_service.restore_import(db_session, row.id, actor="pytest")
    assert restored.lifecycle == "draft"

    # draft 不能直接 confirm-after-delete 路径外，再次 confirm 应成功（draft→active）
    again = alibaba1688_import_service.confirm_import(db_session, row.id, actor="pytest")
    assert again.lifecycle == "active"

    # active 不能直接 soft_delete 之后再 restore？restore 只允许从 deleted
    with pytest.raises(LifecycleTransitionError):
        alibaba1688_import_service.restore_import(db_session, row.id, actor="pytest")

    db_session.delete(row)
    db_session.commit()


def test_1688_soft_delete_blocks_active_then_restore(db_session):
    row = Alibaba1688FileImport(
        original_name="lc2.xlsx", stored_path="/tmp/lc2.xlsx",
        sha256="lifecycle-1688-2", lifecycle="active",
    )
    db_session.add(row)
    db_session.flush()

    deleted = alibaba1688_import_service.soft_delete_import(db_session, row.id, actor="pytest")
    assert deleted.lifecycle == "deleted"
    # active→deleted 不允许再 confirm（只允许 draft→active）
    with pytest.raises(LifecycleTransitionError):
        alibaba1688_import_service.confirm_import(db_session, row.id, actor="pytest")

    restored = alibaba1688_import_service.restore_import(db_session, row.id, actor="pytest")
    assert restored.lifecycle == "draft"

    db_session.delete(row)
    db_session.commit()


# ---------- 吉客云 状态机 ----------

def test_jackyun_lifecycle_state_machine(db_session):
    row = JackyunFileImport(
        original_name="lifecycle.xlsx", stored_path="/tmp/lifecycle-j.xlsx",
        sha256="lifecycle-jackyun", report_type="purchase", lifecycle="draft",
    )
    db_session.add(row)
    db_session.flush()

    assert jackyun_file_import_service.confirm_import(db_session, row.id, actor="pytest").lifecycle == "active"
    # active 不允许直接 restore（只允许从 deleted 恢复）
    with pytest.raises(LifecycleTransitionError):
        jackyun_file_import_service.restore_import(db_session, row.id, actor="pytest")
    assert jackyun_file_import_service.soft_delete_import(db_session, row.id, actor="pytest").lifecycle == "deleted"
    assert jackyun_file_import_service.restore_import(db_session, row.id, actor="pytest").lifecycle == "draft"
    # 已在 draft，再次 restore 为幂等（不报错）
    assert jackyun_file_import_service.restore_import(db_session, row.id, actor="pytest").lifecycle == "draft"

    db_session.delete(row)
    db_session.commit()


# ---------- 税务 状态机（直接构造行） ----------

def test_tax_lifecycle_state_machine(db_session):
    row = TaxInvoiceImport(
        original_name="lifecycle.csv", stored_path="/tmp/lifecycle-tax.csv",
        sha256="lifecycle-tax", period_year=2026, period_month=9, lifecycle="draft",
    )
    db_session.add(row)
    db_session.flush()

    assert tax_invoice_service.confirm_import(db_session, row.id, actor="pytest").lifecycle == "active"
    with pytest.raises(LifecycleTransitionError):
        tax_invoice_service.restore_import(db_session, row.id, actor="pytest")
    assert tax_invoice_service.soft_delete_import(db_session, row.id, actor="pytest").lifecycle == "deleted"
    assert tax_invoice_service.restore_import(db_session, row.id, actor="pytest").lifecycle == "draft"
    assert tax_invoice_service.restore_import(db_session, row.id, actor="pytest").lifecycle == "draft"

    db_session.delete(row)
    db_session.commit()


# ---------- 税务 完整 CSV 链路 + 业务过滤 ----------

TAX_CSV_ACTIVE = """税务系统发票清单
发票号码,发票代码,开票日期,销售方名称,销售方识别号,购买方名称,购买方识别号,金额,税额,价税合计,发票状态,进销项,关联订单号
INV-LC-AAA, CODE-A,2026-09-01,供应商甲,91330000000000001A,本公司,91330000000000002B,100,13,113,正常,进项,PO-LC-001
"""

TAX_CSV_DRAFT = """税务系统发票清单
发票号码,发票代码,开票日期,销售方名称,销售方识别号,购买方名称,购买方识别号,金额,税额,价税合计,发票状态,进销项,关联订单号
INV-LC-BBB, CODE-B,2026-09-02,供应商乙,91330000000000004D,本公司,91330000000000002B,200,26,226,正常,进项,PO-LC-002
"""


def _cleanup(db, import_id, invoice_no):
    db.query(TaxInvoiceImportRecord).filter_by(import_id=import_id).delete()
    inv = db.query(TaxInvoice).filter_by(invoice_number=invoice_no).first()
    if inv is not None:
        db.query(TaxInvoice).filter_by(id=inv.id).delete()
    imp = db.query(TaxInvoiceImport).filter_by(id=import_id).first()
    if imp is not None:
        db.delete(imp)
    db.commit()


def test_tax_auto_confirm_and_draft_business_filtering(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(tax_invoice_service.settings, "DATA_DIR", str(tmp_path))

    # 1) 自动确认导入：直接 active，发票立即进入台账
    active_batch, _ = tax_invoice_service.import_export(
        db_session, content=TAX_CSV_ACTIVE.encode(), original_name="active.csv",
        actor="pytest", auto_confirm=True,
    )
    assert active_batch.lifecycle == "active"
    active_inv = db_session.query(TaxInvoice).filter_by(invoice_number="INV-LC-AAA").one()
    assert active_inv.source_import_id == active_batch.id

    listed = tax_invoice_service.list_invoices(db_session)
    listed_nos = {i["invoiceNumber"] for i in listed}
    assert "INV-LC-AAA" in listed_nos

    # 2) 默认 draft 导入：发票不应出现在业务台账
    draft_batch, _ = tax_invoice_service.import_export(
        db_session, content=TAX_CSV_DRAFT.encode(), original_name="draft.csv",
        actor="pytest", auto_confirm=False,
    )
    assert draft_batch.lifecycle == "draft"
    db_session.query(TaxInvoice).filter_by(invoice_number="INV-LC-BBB").one()  # 确认 draft 导入后发票已入库

    listed = tax_invoice_service.list_invoices(db_session)
    listed_nos = {i["invoiceNumber"] for i in listed}
    assert "INV-LC-AAA" in listed_nos
    assert "INV-LC-BBB" not in listed_nos

    # 3) 确认 draft：发票现在出现在台账
    tax_invoice_service.confirm_import(db_session, draft_batch.id, actor="pytest")
    listed = tax_invoice_service.list_invoices(db_session)
    listed_nos = {i["invoiceNumber"] for i in listed}
    assert "INV-LC-BBB" in listed_nos

    # 4) 软删除该导入：发票再次从台账隐藏（回收站可恢复）
    tax_invoice_service.soft_delete_import(db_session, draft_batch.id, actor="pytest")
    listed = tax_invoice_service.list_invoices(db_session)
    listed_nos = {i["invoiceNumber"] for i in listed}
    assert "INV-LC-BBB" not in listed_nos
    # 回收站（按 lifecycle=deleted 列出）仍可见，且可恢复
    trash = tax_invoice_service.list_imports(db_session, lifecycle="deleted")
    assert any(t["id"] == draft_batch.id for t in trash)
    restored = tax_invoice_service.restore_import(db_session, draft_batch.id, actor="pytest")
    assert restored.lifecycle == "draft"

    _cleanup(db_session, active_batch.id, "INV-LC-AAA")
    _cleanup(db_session, draft_batch.id, "INV-LC-BBB")


def test_filter_lifecycle_lists_only_requested(db_session):
    a = Alibaba1688FileImport(original_name="f.xlsx", stored_path="/t/f.xlsx", sha256="flt-a", lifecycle="active")
    d = Alibaba1688FileImport(original_name="f2.xlsx", stored_path="/t/f2.xlsx", sha256="flt-d", lifecycle="draft")
    db_session.add_all([a, d])
    db_session.flush()

    drafts = alibaba1688_import_service.list_imports(db_session, lifecycle="draft")
    actives = alibaba1688_import_service.list_imports(db_session, lifecycle="active")
    draft_ids = {x["id"] for x in drafts}
    active_ids = {x["id"] for x in actives}
    # 真实库里可能存在其它导入，只校验本次构造的两条被正确归类
    assert d.id in draft_ids and a.id not in draft_ids
    assert a.id in active_ids and d.id not in active_ids

    db_session.delete(a)
    db_session.delete(d)
    db_session.commit()


# ---------- 行级明细核对（row_status） ----------

def test_tax_row_delete_hides_invoice_per_row(db_session, monkeypatch, tmp_path):
    """同批次两行发票：删除其中一行的导入明细，只有该行发票从台账隐藏；恢复后回归。"""
    monkeypatch.setattr(tax_invoice_service.settings, "DATA_DIR", str(tmp_path))
    csv_body = """税务系统发票清单
发票号码,发票代码,开票日期,销售方名称,销售方识别号,购买方名称,购买方识别号,金额,税额,价税合计,发票状态,进销项,关联订单号
INV-ROW-AAA, CODE-R1,2026-09-01,供应商甲,91330000000000001A,本公司,91330000000000002B,100,13,113,正常,进项,PO-R1
INV-ROW-BBB, CODE-R2,2026-09-02,供应商乙,91330000000000004D,本公司,91330000000000002B,200,26,226,正常,进项,PO-R2
"""
    batch, _ = tax_invoice_service.import_export(
        db_session, content=csv_body.encode(), original_name="rows.csv",
        actor="pytest", auto_confirm=True,
    )
    try:
        def visible_nos():
            return {i["invoiceNumber"] for i in tax_invoice_service.list_invoices(db_session)}

        assert {"INV-ROW-AAA", "INV-ROW-BBB"} <= visible_nos()

        # 删除第 1 行（row_index=1，对应 AAA）
        row = tax_invoice_service.delete_row(db_session, batch.id, 1, actor="pytest")
        assert row.row_status == "deleted"
        assert "INV-ROW-AAA" not in visible_nos()
        assert "INV-ROW-BBB" in visible_nos()  # 同批次另一行不受影响

        # 已删行不因重复删除报错（幂等）
        assert tax_invoice_service.delete_row(db_session, batch.id, 1, actor="pytest").row_status == "deleted"

        # 恢复后两张都可见
        assert tax_invoice_service.restore_row(db_session, batch.id, 1, actor="pytest").row_status == "active"
        assert {"INV-ROW-AAA", "INV-ROW-BBB"} <= visible_nos()

        # 不存在的行抛 LookupError
        with pytest.raises(LookupError):
            tax_invoice_service.delete_row(db_session, batch.id, 999, actor="pytest")
    finally:
        _cleanup(db_session, batch.id, "INV-ROW-AAA")
        _cleanup(db_session, batch.id, "INV-ROW-BBB")


def test_1688_row_delete_restore(db_session):
    imp = Alibaba1688FileImport(
        original_name="rows1688.xlsx", stored_path="/t/rows1688.xlsx",
        sha256="rowstatus-1688", lifecycle="draft",
    )
    db_session.add(imp)
    db_session.flush()
    order = Alibaba1688Order(
        external_order_id="ROW-1688-001", import_id=imp.id,
        buyer_company_name="买方", seller_company_name="卖方", actual_payment=99,
    )
    db_session.add(order)
    db_session.commit()
    try:
        assert order.row_status == "active"
        deleted = alibaba1688_import_service.delete_order_row(db_session, imp.id, order.id, actor="pytest")
        assert deleted.row_status == "deleted"
        detail = alibaba1688_import_service.get_import_detail(db_session, imp.id)
        assert detail["orders"][0]["rowStatus"] == "deleted"  # 已删行仍返回，前端置灰
        restored = alibaba1688_import_service.restore_order_row(db_session, imp.id, order.id, actor="pytest")
        assert restored.row_status == "active"
        # 跨导入删除应被拒绝（行不属于该导入）
        with pytest.raises(LookupError):
            alibaba1688_import_service.delete_order_row(db_session, imp.id + 999999, order.id, actor="pytest")
    finally:
        db_session.delete(order)
        db_session.delete(imp)
        db_session.commit()


def test_jackyun_row_delete_restore_list_records(db_session):
    imp = JackyunFileImport(
        original_name="rows-j.xlsx", stored_path="/t/rows-j.xlsx",
        sha256="rowstatus-jackyun", report_type="purchase", lifecycle="draft",
    )
    db_session.add(imp)
    db_session.flush()
    recs = [
        JackyunFileImportRecord(import_id=imp.id, row_index=1, payload={"商品编码": "S1"}),
        JackyunFileImportRecord(import_id=imp.id, row_index=2, payload={"商品编码": "S2"}),
    ]
    db_session.add_all(recs)
    db_session.commit()
    try:
        assert jackyun_file_import_service.delete_row(db_session, imp.id, 1, actor="pytest").row_status == "deleted"
        rows = jackyun_file_import_service.list_records(db_session, imp.id)
        by_idx = {r["rowIndex"]: r for r in rows}
        assert by_idx[1]["rowStatus"] == "deleted"   # 已删行仍返回（前端置灰+可恢复）
        assert by_idx[2]["rowStatus"] == "active"
        assert jackyun_file_import_service.restore_row(db_session, imp.id, 1, actor="pytest").row_status == "active"
        rows = jackyun_file_import_service.list_records(db_session, imp.id)
        assert {r["rowStatus"] for r in rows} == {"active"}
    finally:
        for r in recs:
            db_session.delete(r)
        db_session.delete(imp)
        db_session.commit()


def test_purchase_orders_excludes_deleted_1688_rows(db_session):
    """审计补丁：删除的 1688 订单号，其工作流副本（EPO）不再出现在 /purchase/orders 列表。"""
    from app.api.v1.purchase import list_orders
    from app.models.purchase import ExternalPurchaseOrder

    imp = Alibaba1688FileImport(
        original_name="audit1688.xlsx", stored_path="/t/audit1688.xlsx",
        sha256="rowstatus-audit-1688", lifecycle="active",
    )
    db_session.add(imp)
    db_session.flush()
    ord_a = Alibaba1688Order(external_order_id="AUDIT1688A", import_id=imp.id,
                             seller_company_name="供方", actual_payment=100)
    ord_b = Alibaba1688Order(external_order_id="AUDIT1688B", import_id=imp.id,
                             seller_company_name="供方", actual_payment=200)
    db_session.add_all([ord_a, ord_b])
    db_session.flush()
    po_a = ExternalPurchaseOrder(external_order_id="AUDIT1688A", platform="1688", supplier_name="供方")
    po_b = ExternalPurchaseOrder(external_order_id="AUDIT1688B", platform="1688", supplier_name="供方")
    po_manual = ExternalPurchaseOrder(external_order_id="AUDIT-MANUAL", platform="manual", supplier_name="手工")
    db_session.add_all([po_a, po_b, po_manual])
    db_session.commit()
    try:
        def list_nos():
            return {o["externalOrderId"] for o in list_orders(status=None, limit=200, offset=0, db=db_session)}

        nos = list_nos()
        assert {"AUDIT1688A", "AUDIT1688B", "AUDIT-MANUAL"} <= nos

        # 删除 A：列表里 A 消失（EPO 副本不残留），B 与手工单仍在
        alibaba1688_import_service.delete_order_row(db_session, imp.id, ord_a.id, actor="pytest")
        nos = list_nos()
        assert "AUDIT1688A" not in nos
        assert "AUDIT1688B" in nos and "AUDIT-MANUAL" in nos
    finally:
        for m in (po_a, po_b, po_manual, ord_a, ord_b):
            db_session.delete(m)
        db_session.delete(imp)
        db_session.commit()


def test_inbound_file_preserves_rows_and_registers_non_1688_order(db_session):
    """入库申请单一行一行保留；非 1688 订单号落到工作流主档并只建一条关系。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import ExternalPurchaseOrder

    rk_no = "RK-OTHER-ORDER-001"
    order_no = "OTHER-ORDER-20260501002"
    imp = JackyunFileImport(
        original_name="other-order.xlsx", stored_path="/t/other-order.xlsx",
        sha256="other-order-import-001", report_type="inbound", lifecycle="active",
        headers=["申请单号", "往来单位", "创建时间", "货品编号", "申请数量", "含税单价", "含税金额", "1688采购订单", "采购总金额"],
    )
    db_session.add(imp)
    db_session.flush()
    document = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no=rk_no, supplier_name="淘宝临时采购",
    )
    db_session.add(document)
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="SKU-OTHER-001", quantity=2,
    )
    db_session.add(item)
    db_session.flush()
    payload = {
        "申请单号": rk_no, "往来单位": "淘宝临时采购", "创建时间": "2026-05-01 10:00:00",
        "货品编号": "SKU-OTHER-001", "申请数量": "1", "含税单价": "10",
        "含税金额": "10", "1688采购订单": order_no, "采购总金额": "10",
    }
    db_session.add_all([
        JackyunFileImportRecord(import_id=imp.id, row_index=1, payload=payload),
        JackyunFileImportRecord(import_id=imp.id, row_index=2, payload={**payload, "采购总金额": "12"}),
    ])
    db_session.commit()

    try:
        result = jackyun_file_import_service.map_inbound_items(db_session, imp.id, actor="pytest")
        assert result["sourceRows"] == 2
        assert result["relationshipRows"] == 2
        assert result["uniqueRelationships"] == 1
        assert result["createdExternalOrders"] == 1
        assert result["externalOrderNos"] == [order_no]
        assert result["createdLinks"] == 1
        po = db_session.query(ExternalPurchaseOrder).filter_by(external_order_id=order_no).one()
        assert po.platform == "taobao"
        assert po.order_amount is None
        assert po.raw["inboundAmountTotal"] == "22"
        link = db_session.query(ProcurementChainLink).filter_by(external_po_id=po.id, target_id=document.id).one()
        assert link.confirmed is True
        # 没有正品↔耗材映射时不能伪造“本次不使用”；保持待维护，补齐映射后自动补扣。
        assert link.consumable_usage_decided is False
        assert link.consumable_usage_enabled is None
        assert "待维护耗材映射" in (link.note or "")
        # 重复来源行复用同一入库明细，不因“用过一次”而丢掉第二行。
        assert result["matched"] == 2
        assert db_session.get(JackyunGoodsDocumentItem, item.id).apply_quantity == 1
    finally:
        po = db_session.query(ExternalPurchaseOrder).filter_by(external_order_id=order_no).first()
        if po:
            db_session.query(ProcurementChainLink).filter_by(external_po_id=po.id).delete(synchronize_session=False)
            db_session.delete(po)
        db_session.delete(item)
        db_session.delete(document)
        db_session.query(JackyunFileImportRecord).filter_by(import_id=imp.id).delete(synchronize_session=False)
        db_session.delete(imp)
        db_session.commit()


def test_inbound_file_does_not_auto_link_mismatched_sku_to_existing_order(db_session):
    """订单已有采购明细时，SKU 完全不重合的入库申请不能自动挂单。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import ExternalPurchaseOrder, PurchaseAllocationItem

    rk_no = "RK-MISMATCH-SKU-001"
    order_no = "ORDER-MISMATCH-SKU-001"
    imp = JackyunFileImport(
        original_name="mismatch-sku.xlsx", stored_path="/t/mismatch-sku.xlsx",
        sha256="mismatch-sku-import-001", report_type="inbound", lifecycle="active",
    )
    db_session.add(imp)
    db_session.flush()
    po = ExternalPurchaseOrder(
        external_order_id=order_no, platform="1688", supplier_name="供方",
    )
    db_session.add(po)
    db_session.flush()
    allocation = PurchaseAllocationItem(
        po_id=po.id, sku_id=1001, sku_code="SKU-EXPECTED", goods_name="订单货品",
        quantity=1, unit_price=10, amount=10, source="manual",
    )
    document = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no=rk_no, supplier_name="供方",
    )
    db_session.add_all([allocation, document])
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="SKU-OTHER", sku_barcode="SKU-OTHER",
        quantity=1, matched_sku_id=1002,
    )
    expected_item = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=2, goods_no="SKU-EXPECTED", sku_barcode="SKU-EXPECTED",
        quantity=1, matched_sku_id=1001,
    )
    db_session.add(item)
    db_session.add(expected_item)
    db_session.add(JackyunFileImportRecord(
        import_id=imp.id,
        row_index=1,
        payload={
            "申请单号": rk_no, "货品编号": "SKU-OTHER", "入库数量": "1",
            "含税单价": "10", "含税金额": "10", "1688采购订单": order_no,
        },
    ))
    db_session.commit()

    try:
        result = jackyun_file_import_service.map_inbound_items(db_session, imp.id, actor="pytest")
        assert result["createdLinks"] == 0
        assert any("SKU" in row["reason"] and "不匹配" in row["reason"] for row in result["skipped"])
        assert db_session.query(ProcurementChainLink).filter_by(
            external_po_id=po.id, target_id=document.id,
        ).count() == 0
    finally:
        db_session.query(ProcurementChainLink).filter_by(external_po_id=po.id).delete(synchronize_session=False)
        db_session.delete(allocation)
        db_session.delete(item)
        db_session.delete(expected_item)
        db_session.delete(document)
        db_session.query(JackyunFileImportRecord).filter_by(import_id=imp.id).delete(synchronize_session=False)
        db_session.delete(po)
        db_session.delete(imp)
        db_session.commit()


def test_inbound_file_does_not_auto_link_shared_document_without_allocations(db_session):
    """同一入库单同时标记多个订单且没有采购明细时，必须留给人工确认。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import ExternalPurchaseOrder

    rk_no = "RK-SHARED-DOC-001"
    order_a = "ORDER-SHARED-A-001"
    order_b = "ORDER-SHARED-B-001"
    imp = JackyunFileImport(
        original_name="shared-document.xlsx", stored_path="/t/shared-document.xlsx",
        sha256="shared-document-import-001", report_type="inbound", lifecycle="active",
    )
    db_session.add(imp)
    db_session.flush()
    po_a = ExternalPurchaseOrder(external_order_id=order_a, platform="1688", supplier_name="供方")
    po_b = ExternalPurchaseOrder(external_order_id=order_b, platform="1688", supplier_name="供方")
    document = JackyunGoodsDocument(document_type="inbound", goodsdoc_no=rk_no, supplier_name="供方")
    db_session.add_all([po_a, po_b, document])
    db_session.flush()
    item_a = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="SKU-A", sku_barcode="SKU-A",
        quantity=1, matched_sku_id=1101,
    )
    item_b = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=2, goods_no="SKU-B", sku_barcode="SKU-B",
        quantity=1, matched_sku_id=1102,
    )
    db_session.add_all([item_a, item_b])
    db_session.add_all([
        JackyunFileImportRecord(
            import_id=imp.id, row_index=1,
            payload={
                "申请单号": rk_no, "货品编号": "SKU-A", "入库数量": "1",
                "含税单价": "10", "含税金额": "10", "1688采购订单": order_a,
            },
        ),
        JackyunFileImportRecord(
            import_id=imp.id, row_index=2,
            payload={
                "申请单号": rk_no, "货品编号": "SKU-B", "入库数量": "1",
                "含税单价": "10", "含税金额": "10", "1688采购订单": order_b,
            },
        ),
    ])
    db_session.commit()

    try:
        result = jackyun_file_import_service.map_inbound_items(db_session, imp.id, actor="pytest")
        assert result["createdLinks"] == 0
        assert sum("多个订单来源" in row["reason"] for row in result["skipped"]) == 2
        assert db_session.query(ProcurementChainLink).filter(
            ProcurementChainLink.target_id == document.id,
        ).count() == 0
    finally:
        db_session.query(ProcurementChainLink).filter(
            ProcurementChainLink.target_id == document.id,
        ).delete(synchronize_session=False)
        db_session.delete(item_a)
        db_session.delete(item_b)
        db_session.delete(document)
        db_session.delete(po_a)
        db_session.delete(po_b)
        db_session.query(JackyunFileImportRecord).filter_by(import_id=imp.id).delete(synchronize_session=False)
        db_session.delete(imp)
        db_session.commit()


def test_inbound_file_keeps_local_reference_as_staging_only(db_session):
    """日期流水参考号不能被伪造成跨月复用的采购主单。"""
    from app.models.jackyun import JackyunGoodsDocument, JackyunGoodsDocumentItem
    from app.models.procurement_chain import ProcurementChainLink
    from app.models.purchase import ExternalPurchaseOrder

    rk_no = "RK-LOCAL-REFERENCE-001"
    order_no = "20260501001"
    imp = JackyunFileImport(
        original_name="local-reference.xlsx", stored_path="/t/local-reference.xlsx",
        sha256="local-reference-import-001", report_type="inbound", lifecycle="active",
    )
    document = JackyunGoodsDocument(
        document_type="inbound", goodsdoc_no=rk_no, supplier_name="拼多多临时采购",
    )
    db_session.add_all([imp, document])
    db_session.flush()
    item = JackyunGoodsDocumentItem(
        document_id=document.id, line_no=1, goods_no="6901845048049", quantity=10,
    )
    db_session.add_all([
        item,
        JackyunFileImportRecord(
            import_id=imp.id, row_index=1,
            payload={
                "申请单号": rk_no, "往来单位": "拼多多临时采购", "创建时间": "2026-05-01 10:00:00",
                "货品编号": "6901845048049", "入库数量": "10", "采购总金额": "100",
                "1688采购订单": order_no,
            },
        ),
    ])
    db_session.commit()

    try:
        result = jackyun_file_import_service.map_inbound_items(db_session, imp.id, actor="pytest")
        assert result["createdExternalOrders"] == 0
        assert result["createdLinks"] == 0
        assert any(order_no in row["reason"] and "本地参考编号" in row["reason"] for row in result["skipped"])
        assert db_session.query(ExternalPurchaseOrder).filter_by(external_order_id=order_no).count() == 0
        assert db_session.query(ProcurementChainLink).filter_by(target_id=document.id).count() == 0
        assert db_session.get(JackyunGoodsDocumentItem, item.id).quantity == 10
    finally:
        db_session.query(ProcurementChainLink).filter_by(target_id=document.id).delete(synchronize_session=False)
        db_session.query(JackyunFileImportRecord).filter_by(import_id=imp.id).delete(synchronize_session=False)
        db_session.delete(item)
        db_session.delete(document)
        db_session.delete(imp)
        db_session.commit()
