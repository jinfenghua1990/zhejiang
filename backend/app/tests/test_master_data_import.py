"""基础档案模板回导：新增/更新幂等，且不改库存与采购状态。"""

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
import pytest

from app.models.catalog import ProductSku, Warehouse
from app.models.consumable import Consumable
from app.models.purchase import ExternalPurchaseOrder, Supplier
from app.models.tax import TaxAccountingCategoryRule
from app.services import master_data_import_service as service


def _xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_catalog_template_import_updates_and_creates_without_touching_stock(db_session):
    existing_goods = ProductSku(
        jackyun_sku_id="J-MASTER-001",
        sku_code="MASTER-G-001",
        sku_name="旧正品",
        unit="盒",
        default_cost=Decimal("2.00"),
        status="active",
    )
    existing_consumable = Consumable(
        code="MASTER-C-001",
        name="旧耗材",
        unit="个",
        purchase_unit_cost=Decimal("0.50"),
        stock_qty=Decimal("8"),
        factory_qty=Decimal("3"),
        min_stock_qty=Decimal("1"),
        status="active",
    )
    db_session.add_all([existing_goods, existing_consumable])
    db_session.flush()

    content = _xlsx(
        [
            "类型", "编码", "名称", "商品名称", "单位", "货品类型", "品类", "采购单价",
            "售价", "默认成本", "成本方式", "成本容差", "安全库存", "状态", "关联正品SKU", "档案ID", "吉客云SKU ID",
        ],
        [
            ["正品", "MASTER-G-001", "新正品名称", "", "盒", "单品", "咖啡", "", "9.90", "3.20", "fixed", "0.02", "", "启用", "", existing_goods.id, "J-MASTER-001"],
            ["正品", "MASTER-G-002", "新增正品", "", "个", "单品", "配件", "", "5.00", "1.10", "fixed", "0.02", "", "启用", "", "", "J-MASTER-002"],
            ["耗材", "MASTER-C-001", "新耗材名称", "", "个", "", "包材", "0.80", "", "", "", "", "2", "启用", "MASTER-G-001", existing_consumable.id, ""],
        ],
    )

    result = service.import_catalog(db_session, content, "货品档案-模板.xlsx", "pytest")

    assert result["created"] == 1
    assert result["updated"] == 2
    db_session.expire_all()
    goods = db_session.query(ProductSku).filter_by(sku_code="MASTER-G-001").one()
    consumable = db_session.query(Consumable).filter_by(code="MASTER-C-001").one()
    assert goods.sku_name == "新正品名称"
    assert goods.default_cost == Decimal("3.20")
    assert consumable.name == "新耗材名称"
    assert consumable.purchase_unit_cost == Decimal("0.80")
    assert consumable.stock_qty == Decimal("8")
    assert consumable.factory_qty == Decimal("3")


def test_warehouse_supplier_and_external_order_import_upsert(db_session):
    warehouse = Warehouse(code="MASTER-W-001", name="旧仓", warehouse_type="other", purpose="both")
    supplier = Supplier(name="旧供应商", platform="1688", tax_no="TAX-MASTER-001")
    order = ExternalPurchaseOrder(
        external_order_id="MASTER-O-001",
        platform="other",
        supplier_name="旧供应商",
        purchase_status="pending_refine",
    )
    db_session.add_all([warehouse, supplier, order])
    db_session.flush()

    warehouse_result = service.import_warehouses(
        db_session,
        _xlsx(
            ["仓库编号", "仓库名称", "类型", "用途", "吉客云仓库ID", "参与可售", "状态", "备注", "档案ID"],
            [["MASTER-W-001", "新仓", "B2C仓", "仅正品", "JKY-MASTER-W", "是", "启用", "模板更新", warehouse.id],
             ["MASTER-W-002", "新增仓", "工厂仓", "仅耗材", "", "否", "停用", "", ""]],
        ),
        "仓库档案-模板.xlsx",
        "pytest",
    )
    supplier_result = service.import_suppliers(
        db_session,
        _xlsx(
            ["供应商名称", "税号", "平台", "店铺/供应商编码", "联系人", "电话", "地址", "备注", "临时供应商", "供应商ID"],
            [["新供应商", "TAX-MASTER-001", "1688", "SHOP-1", "联系人", "13800000000", "新地址", "更新", "否", supplier.id],
             ["新增供应商", "", "淘宝", "", "", "", "", "", "是", ""]],
        ),
        "供应商档案-模板.xlsx",
        "pytest",
    )
    order_result = service.import_external_orders(
        db_session,
        _xlsx(
            ["订单号", "渠道", "供应商/往来单位", "备注", "采购时间", "订单金额", "实付金额", "买家账号", "订单状态", "订单ID"],
            [["MASTER-O-001", "其他", "新供应商", "更新备注", "2026-09-15", "12.50", "12.00", "buyer", "已完成", order.id],
             ["MASTER-O-002", "淘宝", "新增供应商", "新增订单", "2026-09-15", "8", "8", "", "", ""]],
        ),
        "其他渠道订单-模板.xlsx",
        "pytest",
    )

    assert warehouse_result["created"] == 1 and warehouse_result["updated"] == 1
    assert supplier_result["created"] == 1 and supplier_result["updated"] == 1
    assert order_result["created"] == 1 and order_result["updated"] == 1
    db_session.expire_all()
    assert db_session.query(Warehouse).filter_by(code="MASTER-W-001").one().name == "新仓"
    assert db_session.query(Supplier).filter_by(tax_no="TAX-MASTER-001").one().name == "新供应商"
    imported = db_session.query(ExternalPurchaseOrder).filter_by(external_order_id="MASTER-O-002", platform="taobao").one()
    assert imported.paid_amount == Decimal("8")
    assert imported.purchase_status == "pending_refine"


def test_master_import_api_accepts_supplier_template(client):
    response = client.post(
        "/api/v1/data/import/suppliers",
        files={
            "file": (
                "供应商档案-接口测试.xlsx",
                _xlsx(["供应商名称", "税号", "平台", "临时供应商"], [["接口测试供应商", "", "其他", "否"]]),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["created"] == 1


def test_warehouse_import_rejects_duplicate_external_id_for_new_row(db_session):
    db_session.add(Warehouse(code="MASTER-W-EXISTING", name="已有仓", jackyun_warehouse_id="JKY-DUPLICATE"))
    db_session.commit()

    with pytest.raises(ValueError, match="该吉客云仓库ID已经绑定到其他仓库"):
        service.import_warehouses(
            db_session,
            _xlsx(
                ["仓库编号", "仓库名称", "吉客云仓库ID"],
                [["MASTER-W-NEW", "新仓", "JKY-DUPLICATE"]],
            ),
            "仓库档案-重复测试.xlsx",
            "pytest",
        )

    assert db_session.query(Warehouse).filter_by(code="MASTER-W-NEW").one_or_none() is None


def test_bundle_and_tax_rule_import_are_separate_master_datasets(db_session):
    bundle = ProductSku(
        jackyun_sku_id="MASTER-BUNDLE-001",
        sku_code="ES-MASTER-001",
        sku_name="旧套装",
        product_type="virtual_bundle",
    )
    rule = TaxAccountingCategoryRule(
        category_name="MASTER-软饮料",
        item_name="MASTER-咖啡",
        tax_code="",
        match_keyword="MASTER-咖啡",
        match_mode="contains",
    )
    db_session.add_all([bundle, rule])
    db_session.flush()

    bundle_result = service.import_bundles(
        db_session,
        _xlsx(
            ["类型", "编码", "名称", "货品类型", "单位", "状态", "档案ID", "吉客云SKU ID"],
            [["正品", "ES-MASTER-001", "新套装", "虚拟组合套装", "套", "启用", bundle.id, "MASTER-BUNDLE-001"]],
        ),
        "套装档案-模板.xlsx",
        "pytest",
    )
    tax_result = service.import_tax_rules(
        db_session,
        _xlsx(
            ["分类规则", "税务代码", "匹配关键词", "匹配方式", "优先级", "启用", "备注", "规则ID"],
            [
                ["*MASTER-软饮料*MASTER-咖啡", "1010101010101010101", "MASTER-咖啡", "包含", 10, "是", "已维护", rule.id],
                ["*食品*茶", "", "茶", "包含", 20, "是", "", ""],
            ],
        ),
        "财务分类-模板.xlsx",
        "pytest",
    )

    assert bundle_result["dataset"] == "bundles"
    assert bundle_result["updated"] == 1
    assert tax_result["dataset"] == "tax_rules"
    assert tax_result["created"] == 1 and tax_result["updated"] == 1
    db_session.expire_all()
    assert db_session.query(ProductSku).filter_by(sku_code="ES-MASTER-001").one().sku_name == "新套装"
    updated_rule = db_session.query(TaxAccountingCategoryRule).filter_by(category_name="MASTER-软饮料", item_name="MASTER-咖啡").one()
    assert updated_rule.tax_code == "1010101010101010101"


def test_catalog_import_rejects_bundle_rows(db_session):
    with pytest.raises(ValueError, match="请使用“套装”导入模板"):
        service.import_catalog(
            db_session,
            _xlsx(
                ["类型", "编码", "名称", "货品类型"],
                [["正品", "ES-MASTER-002", "套装", "虚拟组合套装"]],
            ),
            "货品档案-错误范围.xlsx",
            "pytest",
        )
