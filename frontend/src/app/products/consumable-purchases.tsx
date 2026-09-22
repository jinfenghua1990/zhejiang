"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch, consumablesApi, type ConsumablePurchaseDetail, type ConsumablePurchaseRow, type ConsumablePurchaseSource, type ConsumableRow } from "@/lib/api";
import { newRequestKey } from "@/lib/request-key";

type Props = { materials: ConsumableRow[]; reload: () => void; initialPurchaseId?: number; sourceOrderId?: number };
type WarehouseOption = { id: number; code: string; name: string; warehouseType: string; purpose: string; isSellable: boolean; status: string };
const STATUS = { ordered: "待收货", partial: "部分收货", received: "已收齐", cancelled: "已取消" };
const money = (value: string | number) => `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const qty = (value: string | number) => Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
const inputClass = "h-10 w-full min-w-0 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-50";
const today = () => { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`; };
let lineSeq = 0;
const blankLine = () => { lineSeq += 1; return { _key: `line-${lineSeq}`, consumable_id: "", quantity: "", unit_cost: "" }; };

async function fetchWarehouses(): Promise<WarehouseOption[]> {
  const res = await authenticatedFetch("/api/v1/warehouses?include_inactive=false", { cache: "no-store" });
  if (!res.ok) throw new Error(`仓库加载失败（${res.status}）`);
  return res.json();
}

async function receiveToWarehouse(purchaseId: number, body: Record<string, unknown>): Promise<ConsumablePurchaseDetail> {
  const res = await authenticatedFetch(`/api/v1/warehouses/consumable-purchases/${purchaseId}/receipts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof data.detail === "string" ? data.detail : `收货失败（${res.status}）`);
  }
  return res.json();
}

export default function ConsumablePurchases({ materials, reload, initialPurchaseId, sourceOrderId }: Props) {
  const [orders, setOrders] = useState<ConsumablePurchaseRow[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [detail, setDetail] = useState<ConsumablePurchaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [creating, setCreating] = useState(false);
  const [sources, setSources] = useState<ConsumablePurchaseSource[]>([]);
  const [sourceQuery, setSourceQuery] = useState("");
  const [draft, setDraft] = useState({ supplier_name: "", ordered_on: "", source_order_id: "", reference_no: "", note: "", request_key: "" });
  const [lines, setLines] = useState([blankLine()]);
  const [receiving, setReceiving] = useState(false);
  const [receipt, setReceipt] = useState({ received_on: "", note: "", request_key: "" });
  const [receiptQtys, setReceiptQtys] = useState<Record<number, string>>({});
  const [warehouses, setWarehouses] = useState<WarehouseOption[]>([]);
  const [receiptWarehouseId, setReceiptWarehouseId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try { setOrders(await consumablesApi.purchases(query, sourceOrderId)); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  }, [query, sourceOrderId]);
  useEffect(() => { const timer = setTimeout(() => void load(), 200); return () => clearTimeout(timer); }, [load]);
  useEffect(() => {
    fetchWarehouses().then(setWarehouses).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    if (!initialPurchaseId) return;
    let cancelled = false;
    consumablesApi.purchase(initialPurchaseId).then((row) => { if (!cancelled) setDetail(row); }).catch((e) => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [initialPurchaseId]);
  useEffect(() => {
    if (!creating) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      consumablesApi.purchaseSources(sourceQuery).then((rows) => { if (!cancelled) setSources(rows); }).catch((e) => { if (!cancelled) setError(String(e)); });
    }, 200);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [creating, sourceQuery]);

  async function openNew() {
    setError(""); setNotice(""); setLines([blankLine()]); setSourceQuery("");
    let source: ConsumablePurchaseSource | undefined;
    if (sourceOrderId) {
      try {
        const context = await consumablesApi.purchaseSources();
        source = context.find((row) => row.id === sourceOrderId);
        setSources(context);
      } catch (e) { setError(String(e)); return; }
    }
    setDraft({ supplier_name: source?.supplierName ?? "", ordered_on: today(), source_order_id: source ? String(source.id) : "", reference_no: "", note: "", request_key: newRequestKey() });
    setCreating(true);
  }
  async function openDetail(id: number) {
    setError(""); setBusy(true);
    try { setDetail(await consumablesApi.purchase(id)); setReceiving(false); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function savePurchase(event: React.FormEvent) {
    event.preventDefault(); if (busy) return; setBusy(true); setError("");
    try {
      const result = await consumablesApi.createPurchase({ ...draft, source_order_id: draft.source_order_id ? Number(draft.source_order_id) : null,
        items: lines.map((line) => ({ consumable_id: Number(line.consumable_id), quantity: line.quantity, unit_cost: line.unit_cost })) });
      setCreating(false); setDetail(result); setReceiving(false);
      setNotice("采购单已建立，确认实收数量后才增加库存。"); await load();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function startReceipt() {
    if (!detail) return;
    const allowed = warehouses.filter((row) => row.status === "active" && (row.purpose === "consumable" || row.purpose === "both"));
    const preferred = allowed.find((row) => row.warehouseType === "factory") ?? allowed[0];
    setReceiptWarehouseId(preferred ? String(preferred.id) : "");
    setReceipt({ received_on: today(), note: "", request_key: newRequestKey() });
    setReceiptQtys(Object.fromEntries(detail.items.map((line) => [line.id, ""])));
    setError(""); setReceiving(true);
  }
  async function saveReceipt(event: React.FormEvent) {
    event.preventDefault(); if (!detail || busy) return;
    const items = detail.items.filter((line) => receiptQtys[line.id]?.trim() && Number(receiptQtys[line.id]) !== 0)
      .map((line) => ({ item_id: line.id, quantity: receiptQtys[line.id] }));
    if (!items.length) { setError("请填写本次实收数量，未到货的耗材留空。"); return; }
    if (!receiptWarehouseId) { setError("请选择本次收货进入哪个仓库。"); return; }
    setBusy(true); setError("");
    try {
      const selected = warehouses.find((row) => row.id === Number(receiptWarehouseId));
      setDetail(await receiveToWarehouse(detail.id, { ...receipt, warehouse_id: Number(receiptWarehouseId), items }));
      setReceiving(false); setNotice(`收货已登记，实收数量已进入${selected ? `“${selected.name}”` : "所选仓库"}。`); reload(); await load();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function cancelOrder() {
    if (!detail || !window.confirm("确认取消这张尚未收货的耗材采购单？单据会保留。")) return;
    setBusy(true); setError("");
    try { setDetail(await consumablesApi.cancelPurchase(detail.id)); setNotice("采购单已取消。"); await load(); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }

  const visible = orders.filter((row) => filter === "all" || row.status === filter);
  const receiptWarehouses = warehouses.filter((row) => row.status === "active" && (row.purpose === "consumable" || row.purpose === "both"));
  return <section className="mt-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h2 className="text-lg font-semibold text-slate-800">耗材采购</h2><p className="mt-1 text-sm text-slate-500">建耗材采购单 → 分批确认收货 → 进入耗材库存；绑定正品的商品入库单确认耗用后才形成耗材出库。仓库可在货品中心维护。</p></div>
      <button onClick={() => void openNew()} disabled={!materials.length || busy} className="rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">+ 新建耗材采购单</button>
    </div>
    {sourceOrderId && <div className="mt-3 flex items-center justify-between rounded-lg bg-indigo-50 px-4 py-3 text-sm text-indigo-700"><span>正在查看所选 1688 订单关联的耗材采购</span><Link href="/products?kind=consumable" className="underline">前往耗材档案</Link></div>}
    {notice && <div role="status" className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</div>}
    {error && !creating && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    <div className="mt-5 flex flex-wrap gap-3">
      <input aria-label="搜索耗材采购单" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索采购单、供应商或来源订单号" className={`${inputClass} max-w-md`} />
      <div className="flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1">
        {[["all", "全部"], ...Object.entries(STATUS)].map(([key, label]) => <button key={key} onClick={() => setFilter(key)} className={`rounded-md px-3 py-1.5 text-sm ${filter === key ? "bg-white font-medium text-indigo-700 shadow-sm" : "text-slate-500"}`}>{label}</button>)}
      </div>
    </div>
    <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
      <table className="w-full min-w-[780px] text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr><th className="px-4 py-3">采购单 / 日期</th><th>供应商 / 来源</th><th>采购内容</th><th className="text-right">耗材金额</th><th className="px-5">收货进度</th><th className="pr-4">操作</th></tr></thead>
        <tbody className="divide-y divide-slate-100">{visible.map((row) => <tr key={row.id} className={detail?.id === row.id ? "bg-indigo-50/50" : "hover:bg-slate-50"}>
          <td className="px-4 py-4"><button onClick={() => void openDetail(row.id)} className="font-mono text-xs font-medium text-indigo-600">{row.number}</button><div className="mt-1 text-xs text-slate-400">{row.orderedOn}</div></td>
          <td className="pr-3"><div>{row.supplierName}</div><div className="mt-1 text-xs text-slate-400">{row.sourceOrderNo ? `1688 · ${row.sourceOrderNo}` : row.referenceNo || "独立耗材采购"}</div></td>
          <td className="max-w-[260px] pr-3"><div className="truncate">{row.items.map((line) => line.name).join("、")}</div><div className="mt-1 text-xs text-slate-400">{row.items.length} 种耗材</div></td>
          <td className="text-right font-medium tabular-nums">{money(row.amount)}</td>
          <td className="px-5"><span className={`rounded-full px-2.5 py-1 text-xs ${row.status === "received" ? "bg-emerald-50 text-emerald-700" : row.status === "cancelled" ? "bg-slate-100 text-slate-500" : "bg-amber-50 text-amber-700"}`}>{STATUS[row.status]}</span></td>
          <td className="pr-4"><button disabled={busy} onClick={() => void openDetail(row.id)} className="text-indigo-600">查看</button></td>
        </tr>)}</tbody>
      </table>
      {loading && <p className="p-8 text-center text-sm text-slate-400">正在加载采购单…</p>}
      {!loading && !visible.length && <div className="p-12 text-center"><p className="font-medium text-slate-600">{orders.length ? "暂无符合条件的采购单" : "还没有耗材采购单"}</p><p className="mt-2 text-sm text-slate-400">已有耗材库存请到货品中心库存总览查看，新采购从这里登记。</p></div>}
    </div>

    {detail && <section className="mt-5 rounded-xl border border-indigo-100 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="font-semibold text-slate-800">{detail.number}</h3><p className="mt-1 text-sm text-slate-500">{detail.supplierName} · {detail.orderedOn} · {STATUS[detail.status]}</p></div><div className="flex items-center gap-3">
        {detail.status === "ordered" && <button disabled={busy} onClick={() => void cancelOrder()} className="text-sm text-slate-500">取消采购单</button>}
        {(detail.status === "ordered" || detail.status === "partial") && !receiving && <button disabled={busy} onClick={startReceipt} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white">登记收货</button>}
        <button aria-label="关闭采购单详情" disabled={busy} onClick={() => { setDetail(null); setReceiving(false); }} className="px-2 text-xl text-slate-400">×</button>
      </div></div>
      {detail.sourceOrderId && <Link className="mt-3 inline-block text-sm text-indigo-600" href={`/purchase/workbench?view=orders&order=${detail.sourceOrderId}`}>查看来源 1688 订单：{detail.sourceOrderNo} ↗</Link>}
      {detail.referenceNo && <p className="mt-2 text-sm text-slate-500">外部单号：{detail.referenceNo}</p>}
      {detail.note && <p className="mt-2 text-sm text-slate-500">备注：{detail.note}</p>}
      <form onSubmit={saveReceipt}>
        <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[650px] text-left text-sm"><thead className="border-y border-slate-100 text-xs text-slate-500"><tr><th className="py-3">耗材</th><th className="text-right">采购量</th><th className="text-right">已收</th><th className="text-right">待收</th><th className="text-right">单价</th>{receiving && <th className="w-36 pl-4">本次实收</th>}</tr></thead>
          <tbody className="divide-y divide-slate-100">{detail.items.map((line) => { const remaining = Number(line.quantity) - Number(line.receivedQty); return <tr key={line.id}><td className="py-4"><div>{line.name}</div><div className="mt-1 font-mono text-xs text-slate-400">{line.code}</div></td><td className="text-right">{qty(line.quantity)} {line.unit}</td><td className="text-right">{qty(line.receivedQty)}</td><td className="text-right font-medium">{qty(remaining)}</td><td className="text-right">{money(line.unitCost)}</td>{receiving && <td className="pl-4"><input aria-label={`${line.name}本次实收`} type="number" min="0" max={remaining} step="0.0001" disabled={remaining === 0 || busy} value={receiptQtys[line.id] ?? ""} onChange={(e) => setReceiptQtys({ ...receiptQtys, [line.id]: e.target.value })} placeholder="未到货留空" className={inputClass} /></td>}</tr>; })}</tbody></table></div>
        {receiving && <div className="mt-4 rounded-lg bg-indigo-50/50 p-4"><div className="grid gap-3 sm:grid-cols-[180px_220px_1fr]"><label className="text-xs text-slate-600">实际收货日期<input aria-label="实际收货日期" type="date" required min={detail.orderedOn} value={receipt.received_on} onChange={(e) => setReceipt({ ...receipt, received_on: e.target.value })} className={`mt-1 ${inputClass}`} /></label><label className="text-xs text-slate-600">入库仓库<select required value={receiptWarehouseId} onChange={(e) => setReceiptWarehouseId(e.target.value)} className={`mt-1 ${inputClass}`}><option value="">请选择仓库</option>{receiptWarehouses.map((row) => <option key={row.id} value={row.id}>{row.name} · {row.warehouseType === "factory" ? "工厂仓" : row.warehouseType === "b2c" ? "B2C仓" : "其他"}</option>)}</select></label><label className="text-xs text-slate-600">收货备注<input value={receipt.note} onChange={(e) => setReceipt({ ...receipt, note: e.target.value })} placeholder="例如签收人、送货单号" className={`mt-1 ${inputClass}`} /></label></div><div className="mt-3 flex items-center justify-between gap-3"><p className="text-xs text-slate-500">默认优先选择工厂仓；仓库名称和用途可在 <Link href="/settings/warehouses" className="text-indigo-600 underline">设置 → 仓库配置</Link> 修改。</p><div className="flex gap-2"><button type="button" disabled={busy} onClick={() => setReceiving(false)} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm">取消</button><button disabled={busy || !receiptWarehouseId} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">{busy ? "正在入库…" : "确认收货入库"}</button></div></div></div>}
      </form>
      <div className="mt-5 border-t border-slate-100 pt-4"><h4 className="text-sm font-semibold text-slate-700">收货记录 <span className="ml-1 font-normal text-slate-400">{detail.receipts.length} 次</span></h4>
        {detail.receipts.map((item) => <div key={item.id} className="mt-3 rounded-lg bg-slate-50 px-4 py-3"><div className="flex flex-wrap justify-between gap-2 text-xs"><span className="font-mono text-slate-600">{item.number}</span><span className="text-slate-400">{item.receivedOn} · {item.createdBy}</span></div><p className="mt-2 text-sm text-slate-700">{item.items.map((line) => `${line.name} × ${qty(line.quantity)} ${line.unit}`).join("；")}</p>{item.note && <p className="mt-1 text-xs text-slate-400">{item.note}</p>}</div>)}
        {!detail.receipts.length && <p className="mt-3 text-sm text-slate-400">尚未收货，库存未增加。</p>}
      </div>
    </section>}

    {creating && <div className="fixed inset-0 z-modal flex items-center justify-center bg-slate-900/35 p-5" role="dialog" aria-modal="true" aria-label="新建耗材采购单">
      <form onSubmit={savePurchase} className="max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"><div className="flex justify-between"><div><h3 className="text-lg font-semibold">新建耗材采购单</h3><p className="mt-1 text-sm text-slate-500">填写采购内容，后续按实际到货分批入库。</p></div><button type="button" disabled={busy} aria-label="关闭新建采购单" onClick={() => setCreating(false)} className="self-start px-2 text-2xl text-slate-400">×</button></div>
        {error && <div role="alert" className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
        <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="text-xs text-slate-600">供应商<input required value={draft.supplier_name} onChange={(e) => setDraft({ ...draft, supplier_name: e.target.value })} className={`mt-1 ${inputClass}`} /></label>
          <label className="text-xs text-slate-600">采购日期<input required type="date" value={draft.ordered_on} onChange={(e) => setDraft({ ...draft, ordered_on: e.target.value })} className={`mt-1 ${inputClass}`} /></label>
          <div className="sm:col-span-2"><label className="text-xs text-slate-600">关联 1688 订单（可选）<div className="mt-1 grid gap-2 sm:grid-cols-[220px_1fr]"><input aria-label="搜索来源1688订单" value={sourceQuery} onChange={(e) => setSourceQuery(e.target.value)} placeholder="输入订单号或供应商查找" className={inputClass} /><select aria-label="关联1688订单" value={draft.source_order_id} onChange={(e) => { const source = sources.find((row) => row.id === Number(e.target.value)); setDraft({ ...draft, source_order_id: e.target.value, supplier_name: source?.supplierName || draft.supplier_name }); }} className={inputClass}><option value="">独立采购，不关联 1688 订单</option>{sources.map((row) => <option key={row.id} value={row.id}>{row.orderNo} · {row.supplierName}</option>)}</select></div></label></div>
          <label className="text-xs text-slate-600">外部采购单号（可选）<input value={draft.reference_no} onChange={(e) => setDraft({ ...draft, reference_no: e.target.value })} className={`mt-1 ${inputClass}`} /></label>
          <label className="text-xs text-slate-600">备注<input value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} className={`mt-1 ${inputClass}`} /></label>
        </div>
        <div className="mt-6 flex justify-between"><h4 className="text-sm font-semibold">采购明细</h4><button type="button" onClick={() => setLines([...lines, blankLine()])} className="text-sm text-indigo-600">+ 添加耗材</button></div>
        <div className="mt-3 space-y-3">{lines.map((line, index) => <div key={line._key} className="grid grid-cols-[minmax(0,1fr)_110px_120px_32px] items-end gap-2">
          <label className="text-xs text-slate-500">耗材<select required aria-label={`第${index + 1}行耗材`} value={line.consumable_id} onChange={(e) => { const material = materials.find((row) => row.id === Number(e.target.value)); setLines(lines.map((item, i) => i === index ? { ...item, consumable_id: e.target.value, unit_cost: material?.purchaseUnitCost ?? "" } : item)); }} className={`mt-1 ${inputClass}`}><option value="">选择耗材</option>{materials.filter((row) => row.status === "active").map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}（{row.unit}）</option>)}</select></label>
          <label className="text-xs text-slate-500">采购数量<input required type="number" min="0.0001" step="0.0001" value={line.quantity} onChange={(e) => setLines(lines.map((item, i) => i === index ? { ...item, quantity: e.target.value } : item))} className={`mt-1 ${inputClass}`} /></label>
          <label className="text-xs text-slate-500">采购单价<input required type="number" min="0" step="0.0001" value={line.unit_cost} onChange={(e) => setLines(lines.map((item, i) => i === index ? { ...item, unit_cost: e.target.value } : item))} className={`mt-1 ${inputClass}`} /></label>
          <button type="button" disabled={lines.length === 1} aria-label={`移除第${index + 1}行`} onClick={() => setLines(lines.filter((_, i) => i !== index))} className="h-10 text-xl text-slate-400 disabled:opacity-20">×</button>
        </div>)}</div>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border-t border-slate-100 pt-4"><p className="text-sm text-slate-600">耗材合计 <strong className="ml-2 text-lg">{money(lines.reduce((sum, line) => sum + Number(line.quantity || 0) * Number(line.unit_cost || 0), 0))}</strong></p><div className="flex gap-2"><button type="button" disabled={busy} onClick={() => setCreating(false)} className="rounded-lg border px-4 py-2 text-sm">取消</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">{busy ? "保存中…" : "建立采购单"}</button></div></div>
      </form>
    </div>}
  </section>;
}
