"""货品档案编辑和编码完整性保护。"""

from io import BytesIO

import pytest
from openpyxl import Workbook

from app.models.catalog import ProductSku
from app.services import master_data_import_service


def _xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def test_save_product_preserves_external_id_when_edit_payload_is_blank(client, db_session):
    sku = ProductSku(
        jackyun_sku_id="J-GUARD-001",
        sku_code="GUARD-001",
        sku_name="原名称",
    )
    db_session.add(sku)
    db_session.flush()

    response = client.post(
        "/api/v1/dashboard/products/save",
        json={
            "sku_id": sku.id,
            "jackyun_sku_id": "",
            "sku_code": "GUARD-001",
            "product_type": "single",
            "sku_name": "修改后名称",
        },
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    saved = db_session.get(ProductSku, sku.id)
    assert saved is not None
    assert saved.jackyun_sku_id == "J-GUARD-001"
    assert saved.sku_name == "修改后名称"

    db_session.delete(saved)
    db_session.commit()


def test_save_product_rejects_duplicate_sku_code_on_edit(client, db_session):
    first = ProductSku(jackyun_sku_id="J-GUARD-002", sku_code="GUARD-002", sku_name="第一条")
    second = ProductSku(jackyun_sku_id="J-GUARD-003", sku_code="GUARD-003", sku_name="第二条")
    db_session.add_all([first, second])
    db_session.flush()

    response = client.post(
        "/api/v1/dashboard/products/save",
        json={
            "sku_id": first.id,
            "sku_code": second.sku_code,
            "product_type": "single",
            "sku_name": "不应保存",
        },
    )

    assert response.status_code == 409
    db_session.expire_all()
    assert db_session.get(ProductSku, first.id).sku_code == "GUARD-002"

    db_session.query(ProductSku).filter(ProductSku.id.in_([first.id, second.id])).delete(synchronize_session=False)
    db_session.commit()


def test_catalog_import_rejects_existing_sku_code_on_id_update(db_session):
    first = ProductSku(jackyun_sku_id="J-GUARD-004", sku_code="GUARD-004", sku_name="第一条")
    second = ProductSku(jackyun_sku_id="J-GUARD-005", sku_code="GUARD-005", sku_name="第二条")
    db_session.add_all([first, second])
    db_session.flush()

    content = _xlsx(
        ["类型", "编码", "名称", "货品类型", "档案ID", "吉客云SKU ID"],
        [["正品", "GUARD-005", "冲突修改", "单品", first.id, "J-GUARD-004"]],
    )

    with pytest.raises(ValueError, match="货品编码已存在"):
        master_data_import_service.import_catalog(
            db_session,
            content,
            "货品档案-编码冲突.xlsx",
            "pytest",
        )

    db_session.expire_all()
    assert db_session.get(ProductSku, first.id).sku_code == "GUARD-004"
    db_session.query(ProductSku).filter(ProductSku.id.in_([first.id, second.id])).delete(synchronize_session=False)
    db_session.commit()
