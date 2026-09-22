from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.catalog import Warehouse
from app.models.consumable_purchase import ConsumablePurchase, ConsumableReceipt
from app.services import consumable_purchase_service as purchase_svc
from app.services.warehouse_service import display_code


def serialize_purchase(db: Session, row: ConsumablePurchase, *, detail: bool = False) -> dict:
    """在旧耗材采购序列化结果上补充稳定的仓库引用与展示名称。"""
    result = purchase_svc.serialize_purchase(db, row, detail=detail)
    if not detail or not result.get("receipts"):
        return result

    receipt_ids = [int(item["id"]) for item in result["receipts"] if item.get("id") is not None]
    receipts = (
        db.query(ConsumableReceipt)
        .filter(ConsumableReceipt.id.in_(receipt_ids))
        .all()
        if receipt_ids
        else []
    )
    by_id = {receipt.id: receipt for receipt in receipts}
    warehouse_ids = {receipt.warehouse_id for receipt in receipts if receipt.warehouse_id is not None}
    warehouses = (
        db.query(Warehouse).filter(Warehouse.id.in_(warehouse_ids)).all()
        if warehouse_ids
        else []
    )
    warehouse_by_id = {warehouse.id: warehouse for warehouse in warehouses}

    for item in result["receipts"]:
        receipt = by_id.get(int(item["id"]))
        warehouse = warehouse_by_id.get(receipt.warehouse_id) if receipt and receipt.warehouse_id else None
        item["warehouseId"] = warehouse.id if warehouse else None
        item["warehouseCode"] = display_code(warehouse) if warehouse else ""
        item["warehouseName"] = warehouse.name if warehouse else (
            "历史工厂仓" if item.get("location") == "factory" else "历史未归仓"
        )
    return result
