"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { authenticatedFetch, warehousesApi, type WarehouseRow } from "@/lib/api";

// 与后端 _CONSUMABLE_SELLER_KEYWORDS 同口径：供应商名命中即自动带出「耗材」类型。
const CONSUMABLE_SUPPLIER_KEYWORDS = ["包装", "印刷", "印务", "耗材", "包材"];

function parseOrderedAt(value: string) {
  const normalized = value.trim()
    .replace(/[年/.]/g, "-")
    .replace(/月/g, "-")
    .replace(/日/g, "")
    .replace(/[Tt]/g, " ")
    .replace(/\s+/g, " ");
  const matched = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/.exec(normalized);
  if (!matched) throw new Error("采购时间格式不正确，请输入 2026-09-16 15:58");
  const year = Number(matched[1]);
  const month = Number(matched[2]);
  const day = Number(matched[3]);
  const hour = Number(matched[4] ?? 0);
  const minute = Number(matched[5] ?? 0);
  const second = Number(matched[6] ?? 0);
  const date = new Date(year, month - 1, day, hour, minute, second);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day || date.getHours() !== hour || date.getMinutes() !== minute || date.getSeconds() !== second) {
    throw new Error("采购时间无效，请检查年月日和时分");
  }
  return date.toISOString();
}

export function NewPurchaseModal({ onClose, onCreated, createdOrderId, renderCreated }: {
  onClose: () => void;
  onCreated: (orderId: number) => void;
  createdOrderId?: number | null;
  renderCreated?: (orderId: number) => ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [orderKind, setOrderKind] = useState("goods");
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [warehouseId, setWarehouseId] = useState("");
  useEffect(() => {
    void warehousesApi.list(false).then((rows) => {
      const activeRows = rows.filter((row) => row.status === "active");
      setWarehouses(activeRows);
      const defaultWarehouse = activeRows.find((row) => row.code?.toUpperCase() === "B2C-CZ" || row.name.replace(/\s/g, "").includes("B2C-常州仓"));
      if (defaultWarehouse) setWarehouseId(String(defaultWarehouse.id));
    }).catch(() => {
      // 仓库是可选项；加载失败不阻塞其他采购字段保存。
    });
  }, []);
  function onSupplierChange(value: string) {
    // 按货品类型自动带出：供应商名含包装/印刷/耗材等关键词 → 自动选耗材（仍可手改）。
    const hit = CONSUMABLE_SUPPLIER_KEYWORDS.some((k) => value.includes(k));
    setOrderKind(hit ? "consumable" : "goods");
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const response = await authenticatedFetch("/api/v1/purchase/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          external_order_id: String(form.get("orderNo")).trim(),
          platform: String(form.get("platform") || "1688"),
          supplier_name: String(form.get("supplier")).trim(),
          title: String(form.get("title") || "").trim(),
          ordered_at: parseOrderedAt(String(form.get("orderedAt"))),
          order_amount: String(form.get("amount")),
          paid_amount: String(form.get("amount")),
          warehouse_id: form.get("warehouseId") ? Number(form.get("warehouseId")) : null,
          order_kind: orderKind,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "采购记录保存失败");
      onCreated(body.workbenchOrderId ?? -body.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "采购记录保存失败");
    } finally {
      setBusy(false);
    }
  }

  const configuring = createdOrderId != null && renderCreated != null;
  return (
    <div className="fixed inset-0 z-modal flex items-start justify-center overflow-y-auto bg-slate-950/35 p-3 sm:p-6">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={configuring ? "采购订单详情 / 全量录入" : "新建采购订单 / 全量录入"}
        className="my-auto flex h-[90vh] max-h-[calc(100vh-24px)] w-full max-w-[1440px] flex-col overflow-hidden rounded-xl border border-slate-200 bg-[#f7f9fd] shadow-2xl"
      >
        {configuring ? (
          <div className="min-h-0 flex-1 overflow-y-auto">{createdOrderId != null && renderCreated?.(createdOrderId)}</div>
        ) : (
          <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col">
            <header className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
              <div>
                <div className="flex items-center gap-3">
                  <h2 className="text-lg font-semibold text-slate-900">新建采购订单 / 全量录入</h2>
                  <span className="rounded-md bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-600">采购录入</span>
                </div>
                <p className="mt-1 text-xs text-slate-500">同一个窗口完成采购参数、商品明细、入库单、耗材与发票处理。</p>
              </div>
              <button type="button" onClick={onClose} disabled={busy} aria-label="关闭新建采购" className="rounded-lg px-2 py-1 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-6">
              <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold text-slate-900">采购参数</h3>
                    <p className="mt-1 text-xs text-slate-400">订单级信息只维护一次，商品明细不重复填写仓库和入库单号。</p>
                  </div>
                  <span className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-600">新建草稿</span>
                </div>

                <div className="mt-4 grid gap-3 text-sm md:grid-cols-4">
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">供应商</span><input autoFocus required name="supplier" onChange={(event) => onSupplierChange(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" /></label>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">入库仓库</span><select name="warehouseId" value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100">
                    <option value="">暂不指定</option>
                    {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}{warehouse.code ? `（${warehouse.code}）` : ""}</option>)}
                  </select></label>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">采购金额</span><input required type="number" name="amount" min="0.01" step="0.01" placeholder="采购总金额" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" /></label>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">采购时间</span><input required type="text" name="orderedAt" inputMode="text" autoComplete="off" placeholder="2026-09-16 15:58" title="支持 2026-09-16 15:58、2026/09/16 15:58、2026年9月16日 15:58" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" /></label>
                </div>

                <div className="mt-3 grid gap-3 text-sm md:grid-cols-4">
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">采购渠道</span><select name="platform" defaultValue="1688" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"><option value="1688">1688</option><option value="pdd">拼多多</option><option value="taobao">淘宝 / 天猫</option><option value="other">其他渠道</option></select></label>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[8em] shrink-0 whitespace-nowrap">订单号</span><input required name="orderNo" placeholder="没有原始单时，以此单号为准" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 p-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" /></label>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">货品类型</span><select value={orderKind} onChange={(event) => setOrderKind(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100">
                    <option value="goods">正品（正常货品采购）</option>
                    <option value="consumable">耗材（包材采购）</option>
                  </select></label>
                  <div className="flex min-w-0 items-center gap-2"><span className="w-[6.5em] shrink-0 whitespace-nowrap">发票匹配状态</span><div className="flex h-9 min-w-0 flex-1 items-center rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-slate-500">保存后自动匹配</div></div>
                </div>

                <div className="mt-3 grid gap-3 text-sm md:grid-cols-[1.2fr_1fr_1fr]">
                  <div className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">入库单号</span><div className="flex h-9 min-w-0 flex-1 items-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-2.5 text-slate-400">保存后在当前窗口输入或自动生成</div></div>
                  <div className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">付款状态</span><div className="flex h-9 min-w-0 flex-1 items-center rounded-lg border border-slate-200 bg-slate-50 px-2.5 text-amber-600">待核对</div></div>
                  <label className="flex min-w-0 items-center gap-2"><span className="w-[4.5em] shrink-0 whitespace-nowrap">采购说明</span><input name="title" placeholder="可填写采购备注" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 p-2.5 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" /></label>
                </div>
              </section>

              <section className="mt-4 flex min-h-[300px] flex-col rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-4 py-3">
                  <div><h3 className="font-semibold text-slate-900">商品明细 <span className="ml-2 text-sm font-normal text-slate-400">共 0 条</span></h3><p className="mt-1 text-xs text-slate-400">创建基础信息后，仍在当前窗口继续录入 SKU、数量、总价与耗材匹配。</p></div>
                  <span className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-sm text-indigo-500">创建后可新增商品</span>
                </div>
                <div className="flex flex-1 items-center justify-center px-6 py-10 text-center">
                  <div><div className="text-sm font-medium text-slate-600">商品明细将在当前窗口展开</div><div className="mt-2 text-xs text-slate-400">不会打开第二个页面；保存后直接进入 SKU、入库单号、耗材和发票处理。</div></div>
                </div>
              </section>
            </div>

            <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-white px-5 py-3">
              {error ? <p role="alert" className="min-w-0 text-sm text-red-600">{error}</p> : <p className="text-xs text-slate-400">保存基础信息后，当前窗口会继续完成全部采购录入。</p>}
              <div className="flex shrink-0 gap-2"><button type="button" disabled={busy} onClick={onClose} className="rounded-lg border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">取消</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50">{busy ? "保存中…" : "保存并录入商品明细"}</button></div>
            </footer>
          </form>
        )}
      </div>
    </div>
  );
}
