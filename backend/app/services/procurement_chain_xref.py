"""1688 ↔ 入库单 手动交叉对照表驱动的链路补齐。

源数据默认落在 DATA_DIR/procurement-chain/1688_rk_xref.tsv，也可来自调用方传入的文本（UI 粘贴/上传），
将每条 (1688 订单号, 入库单号) 补建成 procurement_chain_links。

特性：
- 同一 1688 订单 → 多个 RK（拆批到货）→ 多个 link
- 同一 RK → 多个 1688 订单（拼单到货）→ 多个 link
- 组合装行（无 RK）→ 跳过
- 备注含「预付/抵扣/余款」→ 写入 note，便于人工核单
- 幂等：已存在 (order_id, 'inbound', target_id) 的链接不重复创建
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.config import settings
from app.models.alibaba1688_import import Alibaba1688Order
from app.models.jackyun import JackyunGoodsDocument
from app.models.procurement_chain import ProcurementChainLink

XREF_PATH = Path(settings.DATA_DIR).expanduser() / "procurement-chain" / "1688_rk_xref.tsv"

HDR = ["年度", "订货日期", "订单号", "条形码", "分类", "产品名", "规格", "单价",
       "订购件数-盒", "对应单颗", "合计金额", "发票", "外壳", "耗材代码",
       "耗材使用量", "仓库实收-盒", "入库单号", "剩余数量-个", "备注"]


def _parse_xref(content_or_path: str | Path | None = None) -> list[dict]:
    """从文件或文本内容解析 xref 行。

    - ``content_or_path`` 为 None：读 XREF_PATH；
    - 指向已存在路径：读文件；
    - 其他（字符串内容）：直接当文本解析（用户从工作台粘贴的 TSV）。
    """
    if content_or_path is None:
        text = XREF_PATH.read_text(encoding="utf-8")
    elif isinstance(content_or_path, Path) and content_or_path.exists():
        text = content_or_path.read_text(encoding="utf-8")
    else:
        text = str(content_or_path)
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 1 or cells[0].strip() == "年度":
            continue
        cells = cells + [""] * (len(HDR) - len(cells))
        rows.append({
            "year": cells[0].strip(),
            "order_date": cells[1].strip(),
            "order_no": cells[2].strip(),
            "barcode": cells[3].strip(),
            "category": cells[4].strip(),
            "product": cells[5].strip(),
            "unit_price": cells[7].strip(),
            "qty_box": cells[8].strip(),
            "amount": cells[10].strip(),
            "rk_no": cells[16].strip(),
            "note": cells[18].strip() if len(cells) > 18 else "",
        })
    return rows


def _expand_rk(raw: str) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in re.split(r"[/／、，,]", raw) if p.strip()]


def collect_xref_pairs(rows: list[dict] | None = None) -> list[dict]:
    rows = rows if rows is not None else _parse_xref()
    agg: dict[tuple[str, str], dict] = {}
    for r in rows:
        for rk in _expand_rk(r["rk_no"]):
            key = (r["order_no"], rk)
            if key not in agg:
                agg[key] = {
                    "order_no": r["order_no"], "rk_no": rk,
                    "barcodes": [], "products": [], "qty_boxes": 0,
                    "amounts": [], "notes": [], "is_combo": False,
                }
            e = agg[key]
            if r["barcode"] and r["barcode"] not in e["barcodes"]:
                e["barcodes"].append(r["barcode"])
            if r["product"] and r["product"] not in e["products"]:
                e["products"].append(r["product"])
            try:
                e["qty_boxes"] += int(r["qty_box"]) if r["qty_box"] else 0
            except (TypeError, ValueError):
                pass
            if r["amount"]:
                e["amounts"].append(r["amount"])
            if r["note"]:
                e["notes"].append(r["note"])
                if "组合装" in r["note"]:
                    e["is_combo"] = True
    return list(agg.values())


def preview_xref_links(db: Session, content_or_path: str | Path | None = None) -> dict:
    rows = _parse_xref(content_or_path)
    pairs = collect_xref_pairs(rows)
    order_nos = {p["order_no"] for p in pairs}
    rk_nos = {p["rk_no"] for p in pairs}
    orders = {o.external_order_id: o for o in db.scalars(
        select(Alibaba1688Order).where(Alibaba1688Order.external_order_id.in_(order_nos))
    ).all()}
    rk_docs = {d.goodsdoc_no: d for d in db.scalars(
        select(JackyunGoodsDocument).where(JackyunGoodsDocument.goodsdoc_no.in_(rk_nos))
    ).all()}
    existing = db.scalars(select(ProcurementChainLink).where(
        ProcurementChainLink.target_type == "inbound"
    )).all()
    existing_by_order: dict[int, set[int]] = defaultdict(set)
    for l in existing:
        if l.order_id is not None:
            existing_by_order[l.order_id].add(l.target_id)
    to_create, missing_order, missing_rk, already_linked, combo_skipped = [], [], [], [], []
    for p in pairs:
        if p["is_combo"] and not p["rk_no"]:
            combo_skipped.append(p)
            continue
        o = orders.get(p["order_no"])
        d = rk_docs.get(p["rk_no"])
        if o is None:
            missing_order.append(p)
            continue
        if d is None:
            missing_rk.append(p)
            continue
        if d.id in existing_by_order.get(o.id, set()):
            already_linked.append(p)
            continue
        to_create.append({**p, "order_db_id": o.id, "rk_db_id": d.id,
                          "note_extra": " | ".join(p["notes"]) if p["notes"] else ""})
    xref_rk_ids = {rk_docs[r].id for r in rk_nos if r in rk_docs}
    noise = [l for l in existing if l.target_id not in xref_rk_ids
             and l.target_id not in {d.id for d in rk_docs.values()}]
    return {
        "total_xref_rows": len(rows), "total_pairs": len(pairs),
        "to_create": to_create, "missing_order": missing_order,
        "missing_rk": missing_rk, "already_linked": already_linked,
        "combo_skipped": combo_skipped, "existing_noise_links": noise,
        "stats": {
            "to_create": len(to_create), "missing_order": len(missing_order),
            "missing_rk": len(missing_rk), "already_linked": len(already_linked),
            "combo_skipped": len(combo_skipped), "existing_noise": len(noise),
        },
    }


def apply_xref_links(
    db: Session,
    actor: str = "system",
    content_or_path: str | Path | None = None,
    persist_to_file: bool = False,
) -> dict:
    """应用 xref：写入 procurement_chain_links。

    persist_to_file=True 且 content 是字符串文本时：把文本落盘到 XREF_PATH（保持工作台维护
    的版本与文件一致；下次预览/CLI 也会用最新版本）。传文件路径时不重复落盘。
    """
    if persist_to_file and isinstance(content_or_path, str):
        XREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        XREF_PATH.write_text(content_or_path, encoding="utf-8")

    preview = preview_xref_links(db, content_or_path)
    created = 0
    created_links: list[ProcurementChainLink] = []
    for item in preview["to_create"]:
        link = ProcurementChainLink(
            order_id=item["order_db_id"], external_po_id=None,
            target_type="inbound", target_id=item["rk_db_id"],
            match_method="manual", confidence=1.0, confirmed=True,
            note=("来源：手动交叉对照表 1688_rk_xref.tsv"
                  + (f" | 备注：{item['note_extra']}" if item["note_extra"] else "")
                  + (f" | 含 SKU {'+'.join(item['barcodes'])}" if item["barcodes"] else "")),
        )
        db.add(link)
        created_links.append(link)
        created += 1
    db.commit()
    # 与 UI 动线一致：入库单关联成功 → 自动反填 SKU 分配（幂等，人工填优先）
    seeded = 0
    if created_links:
        from app.services.inbound_allocation_seed import seed_for_link_batch
        try:
            seed_result = seed_for_link_batch(db, created_links)
            seeded = seed_result.get("seeded", 0)
        except Exception:
            pass
    audit(db, actor, "purchase.procurement_chain.xref_apply", "procurement_chain_links", 0,
          {"created": created, "allocSeeded": seeded,
           "missingOrder": preview["stats"]["missing_order"],
           "missingRk": preview["stats"]["missing_rk"],
           "alreadyLinked": preview["stats"]["already_linked"],
           "comboSkipped": preview["stats"]["combo_skipped"]})
    return {
        "created": created,
        "alloc_seeded": seeded,
        "total_xref_rows": preview["total_xref_rows"],
        "total_pairs": preview["total_pairs"],
        "missing_order": [p["order_no"] for p in preview["missing_order"]],
        "missing_rk": [p["rk_no"] for p in preview["missing_rk"]],
        "combo_skipped_count": preview["stats"]["combo_skipped"],
        "already_linked_count": preview["stats"]["already_linked"],
        "noise_links": [{"id": l.id, "target_id": l.target_id, "order_id": l.order_id}
                        for l in preview["existing_noise_links"]],
    }
