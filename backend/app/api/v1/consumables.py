from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_actor
from app.core.audit import audit
from app.db import get_db
from app.services import consumable_service as svc
from app.services import consumable_purchase_service as purchase_svc
from app.services import warehouse_purchase_view as purchase_view
from app.services import warehouse_receipt_service
from app.models.consumable_purchase import ConsumablePurchase
from app.utils.uploads import read_upload_limited

router = APIRouter(prefix="/consumables", tags=["耗材库"])


@router.get("")
def list_rows(search: str = "", status: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_consumables(db, search=search, status=status)


class ConsumableBody(BaseModel):
    consumable_id: int | None = None
    code: str
    name: str
    category: str = ""
    unit: str = "个"
    purchase_unit_cost: str | None = None
    stock_qty: str | None = None
    min_stock_qty: str | None = None
    barcode: str | None = None
    tax_code: str | None = None
    tax_category_rule_id: int | None = None
    sku_ids: list[int] | None = None


@router.post("")
def save(body: ConsumableBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = svc.upsert_consumable(db, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.save", "consumables", row.id, {"code": row.code})
    return svc.serialize_consumable(row, db)


@router.post("/import-xlsx")
async def import_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        content = await read_upload_limited(file, max_bytes=20 * 1024 * 1024)
        result = svc.import_xlsx(db, content, file.filename or "consumables.xlsx")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.import_xlsx", "consumables", None, result)
    return {"ok": True, **result}


class TransactionBody(BaseModel):
    """流水类型：purchase/send_factory/factory_receive/consume/stocktake/loss/manual（adjustment=盘点旧名）。"""

    transaction_type: Literal[
        "purchase", "send_factory", "factory_receive", "consume", "stocktake", "loss", "manual", "adjustment"
    ]
    quantity: Annotated[Decimal, Field(max_digits=18, decimal_places=4)]
    unit_cost: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=10)] | None = None
    location: Literal["own", "factory"] | None = None
    warehouse_id: int | None = None
    source_type: Literal["manual"] = "manual"
    source_id: None = None
    request_key: UUID | None = None
    note: str = ""


@router.post("/{consumable_id}/transactions")
def transaction(consumable_id: int, body: TransactionBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        payload = body.model_dump(exclude={"request_key"})
        tx = svc.record_transaction(db, consumable_id=consumable_id, request_key=str(body.request_key) if body.request_key else None, **payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.transaction", "consumable_transactions", tx.id, {"type": tx.transaction_type})
    return {"id": tx.id, "quantity": str(tx.quantity)}


@router.get("/{consumable_id}/transactions")
def transactions(consumable_id: int, limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_transactions(db, consumable_id, limit=limit)


class MappingBody(BaseModel):
    mapping_id: int | None = None
    sku_id: int
    consumable_id: int
    usage_per_unit: str = "1"
    note: str = ""


@router.get("/mappings/list")
def mappings(consumable_id: int | None = None, sku_id: int | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return svc.list_mappings(db, consumable_id=consumable_id, sku_id=sku_id)


@router.post("/mappings")
def save_mapping(body: MappingBody, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        row = svc.upsert_mapping(db, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.mapping.save", "consumable_sku_mappings", row.id, {"skuId": row.sku_id, "consumableId": row.consumable_id})
    return {"id": row.id}


@router.delete("/mappings/{mapping_id}")
def delete_mapping(mapping_id: int, request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    try:
        svc.remove_mapping(db, mapping_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    audit(db, current_actor(request), "consumable.mapping.delete", "consumable_sku_mappings", mapping_id, {})
    return {"ok": True}


# 单价 10 位小数（numeric(18,10) 同精度）：总价÷数量除不尽时（如 139.51÷200=0.69755），
# 4 位校验会 422 拦截，导致「保存时报 [object Object]」（2026-09-07）。
PositiveQty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Cost = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=10)]


class PurchaseItemBody(BaseModel):
    consumable_id: int
    quantity: PositiveQty
    unit_cost: Cost


class PurchaseBody(BaseModel):
    request_key: UUID
    supplier_name: str = Field(min_length=1, max_length=256)
    ordered_on: date
    source_order_id: int | None = None
    reference_no: str = Field(default="", max_length=128)
    note: str = ""
    items: list[PurchaseItemBody] = Field(min_length=1, max_length=100)


class PurchaseUpdateBody(BaseModel):
    """耗材采购单编辑入参：所有字段可选，前端按需传。"""
    supplier_name: str | None = Field(default=None, max_length=256)
    ordered_on: date | None = None
    note: str | None = None
    items: list[PurchaseItemBody] | None = None


class ReceiptItemBody(BaseModel):
    item_id: int
    quantity: PositiveQty


class ReceiptBody(BaseModel):
    request_key: UUID
    received_on: date
    warehouse_id: int | None = None
    location: Literal["own", "factory"] = "own"
    note: str = ""
    items: list[ReceiptItemBody] = Field(min_length=1, max_length=100)


@router.get("/purchases/source-orders")
def purchase_sources(search: str = "", db: Session = Depends(get_db)) -> list[dict]:
    return purchase_svc.source_orders(db, search)


@router.get("/purchases")
def purchases(search: str = "", source_order_id: int | None = None,
              order_no: str = "", db: Session = Depends(get_db)) -> list[dict]:
    return purchase_svc.list_purchases(db, search, source_order_id, order_no)


@router.get("/purchases/{purchase_id}")
def purchase_detail(purchase_id: int, db: Session = Depends(get_db)) -> dict:
    row = db.get(ConsumablePurchase, purchase_id)
    if not row:
        raise HTTPException(404, "耗材采购单不存在")
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.post("/purchases")
def create_purchase(body: PurchaseBody, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        row = purchase_svc.create_purchase(db, **{**body.model_dump(), "request_key": str(body.request_key)}, actor=current_actor(request))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.create", "consumable_purchases", row.id, {"number": row.number})
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.post("/purchases/{purchase_id}/receipts")
def receive_purchase(purchase_id: int, body: ReceiptBody, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        row = warehouse_receipt_service.receive_consumable_purchase(
            db,
            purchase_id,
            request_key=str(body.request_key),
            received_on=body.received_on,
            warehouse_id=body.warehouse_id,
            note=body.note,
            items=[item.model_dump() for item in body.items],
            actor=current_actor(request),
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.receive", "consumable_purchases", row.id, {"requestKey": str(body.request_key)})
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.post("/purchases/{purchase_id}/cancel")
def cancel_purchase(purchase_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    try:
        row = purchase_svc.cancel_purchase(db, purchase_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.cancel", "consumable_purchases", row.id)
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.post("/purchases/{purchase_id}/reopen")
def reopen_purchase(purchase_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """把 cancelled 状态的耗材采购单恢复为 ordered（用户取消/录错后反悔专用）。"""
    try:
        row = purchase_svc.reopen_purchase(db, purchase_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.reopen", "consumable_purchases", row.id, {})
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.patch("/purchases/{purchase_id}")
def update_purchase(purchase_id: int, body: PurchaseUpdateBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """编辑耗材采购单：供应商/日期/备注/明细；前端在详情面板就地改。"""
    try:
        kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
        row = purchase_svc.update_purchase(db, purchase_id, **kwargs)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.update", "consumable_purchases", row.id,
          {"fields": sorted(kwargs.keys())})
    return purchase_view.serialize_purchase(db, row, detail=True)


@router.delete("/purchases/{purchase_id}")
def delete_purchase(purchase_id: int, request: Request, db: Session = Depends(get_db)) -> dict:
    """物理删除耗材采购单（任何状态可删；已收货的自动记负数冲销流水回冲库存）。"""
    try:
        purchase_svc.delete_purchase(db, purchase_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc))
    audit(db, current_actor(request), "consumable.purchase.delete", "consumable_purchases", purchase_id,
          {"note": "含收货的单会自动冲销库存流水"})
    return {"ok": True, "purchaseId": purchase_id}
