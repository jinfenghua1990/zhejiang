"use client";

import { useEffect, useMemo, useState } from "react";
import {
  authenticatedFetch,
  procurementChainApi,
  taxInvoiceApi,
  type ChainLinkCandidate,
  type ChainOrderRow,
  type PendingLink,
  type TaxInvoiceRow,
} from "@/lib/api";

type TriageFilter = "todo" | "suggested" | "all";
type PurchaseOrderRow = { id?: number; purchNo?: string; supplierName?: string; amount?: number; status?: string };

function money(value: number | string | null | undefined) {
  const amount = typeof value === "string" ? Number(value) : value;
  return Number.isFinite(amount) ? `¥${Number(amount).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

function date(value: string | null | undefined) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
}

/** Backend uses a negative id to address a workflow-only order and avoid file/workflow id collisions. */
export function canonicalChainOrderId(row: ChainOrderRow) {
  return row.fileOrderId ?? (row.externalPoId ? -row.externalPoId : row.orderId);
}

function contains(value: string | null | undefined, text: string) {
  return (value ?? "").toLocaleLowerCase().includes(text.toLocaleLowerCase());
}

function candidateForOrder<T extends { targetSupplier?: string; targetNo?: string; supplierName?: string; purchNo?: string }>(items: T[], row: ChainOrderRow) {
  const supplier = row.supplier.trim();
  const orderNo = row.orderNo.trim();
  return [...items].sort((a, b) => {
    const aHit = contains(a.targetSupplier ?? a.supplierName, supplier) || contains(a.targetNo ?? a.purchNo, orderNo);
    const bHit = contains(b.targetSupplier ?? b.supplierName, supplier) || contains(b.targetNo ?? b.purchNo, orderNo);
    return Number(bHit) - Number(aHit);
  });
}

export function QuickTriage({
  orders,
  pending,
  busy,
  onConfirm,
  onManualInbound,
  onReload,
  onNotice,
}: {
  orders: ChainOrderRow[];
  pending: PendingLink[];
  busy: boolean;
  onConfirm: (kind: string, linkId: number) => Promise<void>;
  onManualInbound: (orderId: number, targetId: number) => Promise<void>;
  onReload?: () => Promise<void>;
  onNotice?: (text: string) => void;
}) {
  const [filter, setFilter] = useState<TriageFilter>("todo");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [inbound, setInbound] = useState<ChainLinkCandidate[]>([]);
  const [settlement, setSettlement] = useState<ChainLinkCandidate[]>([]);
  const [purchaseOrders, setPurchaseOrders] = useState<PurchaseOrderRow[]>([]);
  const [invoices, setInvoices] = useState<TaxInvoiceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const suggestedOrderNos = useMemo(() => new Set(pending.map((item) => item.orderNo)), [pending]);
  const visible = useMemo(() => orders.filter((row) => {
    const missing = !row.purchaseOrders.length || !row.inbound.length || !row.settlement.length || !row.invoice.length;
    if (filter === "todo") return missing;
    if (filter === "suggested") return row.pendingCount > 0 || suggestedOrderNos.has(row.orderNo);
    return true;
  }), [filter, orders, suggestedOrderNos]);
  const selected = visible.find((row) => canonicalChainOrderId(row) === selectedId) ?? visible[0] ?? null;

  useEffect(() => {
    if (selected && canonicalChainOrderId(selected) !== selectedId) setSelectedId(canonicalChainOrderId(selected));
  }, [selected, selectedId]);

  useEffect(() => {
    if (!selected || !expanded) return;
    let live = true;
    setLoading(true);
    Promise.all([
      procurementChainApi.candidates(canonicalChainOrderId(selected), "inbound", "", 8),
      procurementChainApi.candidates(canonicalChainOrderId(selected), "settlement", "", 8),
      taxInvoiceApi.invoices({ direction: "input", status: "issued" }),
      authenticatedFetch("/api/v1/jackyun-files/purchase-orders?limit=200").then(async (res) => {
        if (!res.ok) throw new Error(`吉客云采购单候选加载失败（${res.status}）`);
        return res.json() as Promise<PurchaseOrderRow[]>;
      }),
    ]).then(([inboundResult, settlementResult, invoiceRows, poRows]) => {
      if (!live) return;
      setInbound(inboundResult.items);
      setSettlement(settlementResult.items);
      setInvoices(invoiceRows);
      setPurchaseOrders(poRows ?? []);
    }).catch((error) => {
      if (live) onNotice?.(error instanceof Error ? error.message : "候选单据加载失败");
    }).finally(() => {
      if (live) setLoading(false);
    });
    return () => { live = false; };
  }, [expanded, selected ? canonicalChainOrderId(selected) : null]); // eslint-disable-line react-hooks/exhaustive-deps

  const recommendedPending = pending.filter((item) => item.confidence !== null && item.confidence >= 0.9);
  const invoiceCandidates = useMemo(() => {
    if (!selected) return [];
    const selectedAmount = selected.amount ?? 0;
    return [...invoices].sort((a, b) => {
      const aSupplier = contains(a.sellerName, selected.supplier) ? 1 : 0;
      const bSupplier = contains(b.sellerName, selected.supplier) ? 1 : 0;
      const aDiff = Math.abs(Number(a.totalAmount ?? 0) - selectedAmount);
      const bDiff = Math.abs(Number(b.totalAmount ?? 0) - selectedAmount);
      return bSupplier - aSupplier || aDiff - bDiff;
    }).slice(0, 6);
  }, [invoices, selected]);

  async function afterSaved(message: string) {
    onNotice?.(message);
    await onReload?.();
  }

  async function linkSettlement(candidate: ChainLinkCandidate) {
    if (!selected) return;
    const key = `settlement-${candidate.targetId}`;
    setSaving(key);
    try {
      await procurementChainApi.manualLink(canonicalChainOrderId(selected), "settlement", candidate.targetId, "分拣中心人工关联吉客云结算单");
      await afterSaved(`已关联结算单 ${candidate.targetNo}`);
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "关联结算单失败");
    } finally {
      setSaving(null);
    }
  }

  async function linkInvoice(invoice: TaxInvoiceRow) {
    if (!selected) return;
    const key = `invoice-${invoice.id}`;
    setSaving(key);
    try {
      await procurementChainApi.manualInvoiceLink(canonicalChainOrderId(selected), invoice.id, "分拣中心人工关联税务进项发票");
      await afterSaved(`已关联进项发票 ${invoice.invoiceNumber || invoice.invoiceCode}`);
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "关联发票失败");
    } finally {
      setSaving(null);
    }
  }

  async function linkPurchaseOrder(po: PurchaseOrderRow) {
    if (!selected?.externalPoId || !po.purchNo) return;
    const key = `po-${po.id ?? po.purchNo}`;
    setSaving(key);
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${selected.externalPoId}/jackyun-link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purch_no: po.purchNo }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail ?? `关联采购单失败（${res.status}）`);
      }
      await afterSaved(`已关联吉客云采购单 ${po.purchNo}`);
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "关联采购单失败");
    } finally {
      setSaving(null);
    }
  }

  async function confirmRecommended() {
    if (!recommendedPending.length) return;
    if (!window.confirm(`确认 ${recommendedPending.length} 条高置信建议？确认后会立即写入正式关联。`)) return;
    setSaving("batch");
    try {
      for (const item of recommendedPending) await onConfirm(item.kind, item.linkId);
      await onReload?.();
      onNotice?.(`已确认 ${recommendedPending.length} 条高置信建议`);
    } catch (error) {
      onNotice?.(error instanceof Error ? error.message : "批量确认失败");
    } finally {
      setSaving(null);
    }
  }

  const list = (items: ChainLinkCandidate[], kind: "inbound" | "settlement") => (
    <div className="space-y-1.5">
      {candidateForOrder(items, selected ?? ({} as ChainOrderRow)).slice(0, 4).map((item) => {
        const key = `${kind}-${item.targetId}`;
        const act = kind === "inbound"
          ? async () => { setSaving(key); try { await onManualInbound(canonicalChainOrderId(selected!), item.targetId); await afterSaved(`已关联入库单 ${item.targetNo}`); } catch (error) { onNotice?.(error instanceof Error ? error.message : "关联入库单失败"); } finally { setSaving(null); } }
          : () => linkSettlement(item);
        return <div key={item.targetId} className="flex items-center justify-between gap-2 rounded-md border border-slate-100 bg-slate-50/60 px-2 py-1.5">
          <div className="min-w-0 text-[10px]">
            <div className="truncate font-mono font-medium text-slate-700">{item.targetNo} <span className="font-sans text-slate-400">{money(item.targetAmount)}</span></div>
            <div className="truncate text-slate-400">{item.targetSupplier || "未填写供应商"} · {date(item.targetDate)} · {(item.score * 100).toFixed(0)}% · {item.reason}</div>
            {kind === "inbound" && item.details.length > 0 && <div className="mt-1 flex flex-wrap gap-1 text-[9px] text-slate-500">待分配 SKU：{item.details.slice(0, 4).map((detail, index) => <span key={index} className="rounded bg-white px-1 py-0.5">{detail.goodsName || detail.barcode || "未命名"} × {detail.applyQuantity ?? detail.quantity ?? 0}{detail.unitName || ""}</span>)}{item.details.length > 4 && <span>另有 {item.details.length - 4} 行</span>}</div>}
          </div>
          <button type="button" disabled={busy || item.currentlyLinked || saving === key} onClick={() => void act()} className="shrink-0 rounded border border-indigo-200 bg-white px-1.5 py-0.5 text-[9.5px] font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-50">{item.currentlyLinked ? "已关联" : saving === key ? "保存…" : "关联"}</button>
        </div>;
      })}
      {!items.length && <p className="py-2 text-center text-[10px] text-slate-400">暂无可关联候选，请先从数据接入导入并确认对应单据。</p>}
    </div>
  );

  return (
    <section className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50/60 via-white to-violet-50/50 p-3.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2"><span className="h-3.5 w-1 rounded-full bg-indigo-600" /><h2 className="text-[12px] font-semibold text-slate-800">待关联分拣中心</h2><span className="rounded bg-indigo-100 px-1.5 py-0.5 text-[9.5px] text-indigo-600">人工确认</span></div>
          <p className="mt-0.5 text-[10px] text-slate-500">按订单把吉客云采购/入库/结算单与税务进项发票快速归拢；候选仅供判断，点击关联才会保存。</p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(["todo", "suggested", "all"] as TriageFilter[]).map((key) => <button key={key} onClick={() => setFilter(key)} className={`rounded-md px-2 py-1 text-[10px] font-medium ${filter === key ? "bg-indigo-600 text-white" : "border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"}`}>{key === "todo" ? `待处理 ${visible.length}` : key === "suggested" ? `有建议 ${pending.length}` : "全部订单"}</button>)}
          {recommendedPending.length > 0 && <button type="button" disabled={saving === "batch" || busy} onClick={() => void confirmRecommended()} className="rounded-md bg-amber-500 px-2 py-1 text-[10px] font-medium text-white hover:bg-amber-600 disabled:opacity-50">{saving === "batch" ? "确认中…" : `确认高置信 ${recommendedPending.length}`}</button>}
        </div>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(270px,0.78fr)_minmax(0,1.7fr)]">
        <div className="max-h-[420px] overflow-y-auto rounded-lg border border-slate-200 bg-white">
          {visible.map((row) => {
            const active = canonicalChainOrderId(row) === canonicalChainOrderId(selected ?? row);
            return <button key={`${row.source}-${row.orderId}`} type="button" onClick={() => { setSelectedId(canonicalChainOrderId(row)); setExpanded(false); }} className={`w-full border-b border-slate-100 px-3 py-2 text-left last:border-b-0 ${active ? "bg-indigo-50/80" : "hover:bg-slate-50"}`}>
              <div className="flex items-center justify-between gap-2"><span className="truncate font-mono text-[10.5px] font-semibold text-slate-800">{row.orderNo}</span><span className="shrink-0 text-[10px] font-medium text-slate-700">{money(row.amount)}</span></div>
              <div className="mt-0.5 truncate text-[10px] text-slate-500">{row.supplier || "未填写供应商"} · {date(row.orderDate)}</div>
              <div className="mt-1 flex flex-wrap gap-1 text-[9px]"><span className={row.purchaseOrders.length ? "rounded bg-teal-50 px-1 text-teal-600" : "rounded bg-slate-100 px-1 text-slate-400"}>采购单 {row.purchaseOrders.length || "待"}</span><span className={row.inbound.length ? "rounded bg-teal-50 px-1 text-teal-600" : "rounded bg-slate-100 px-1 text-slate-400"}>入库 {row.inbound.length || "待"}</span><span className={row.settlement.length ? "rounded bg-teal-50 px-1 text-teal-600" : "rounded bg-slate-100 px-1 text-slate-400"}>结算 {row.settlement.length || "待"}</span><span className={row.invoice.length ? "rounded bg-amber-50 px-1 text-amber-600" : "rounded bg-slate-100 px-1 text-slate-400"}>发票 {row.invoice.length || "待"}</span></div>
            </button>;
          })}
          {!visible.length && <p className="p-5 text-center text-[10px] text-slate-400">当前筛选没有订单。</p>}
        </div>
        <div className="min-w-0 rounded-lg border border-slate-200 bg-white p-3">
          {!selected ? <p className="py-10 text-center text-[11px] text-slate-400">选择左侧订单后开始分拣。</p> : <>
            <div className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 pb-2"><div><div className="font-mono text-[12px] font-semibold text-indigo-700">{selected.orderNo}</div><div className="mt-0.5 text-[10px] text-slate-500">{selected.supplier || "未填写供应商"} · {money(selected.amount)} · 下单 {date(selected.orderDate)}</div></div><button type="button" onClick={() => setExpanded((value) => !value)} className="rounded-md bg-indigo-600 px-2 py-1 text-[10px] font-medium text-white hover:bg-indigo-700">{expanded ? "收起候选" : "加载候选并分拣"}</button></div>
            <div className="mt-2 flex flex-wrap gap-1 text-[9.5px] text-slate-500">{selected.purchaseOrders.map((item) => <span key={item.id} className="rounded bg-teal-50 px-1.5 py-0.5 text-teal-700">采购单 {item.purchNo}</span>)}{selected.inbound.map((item) => <span key={item.targetId} className="rounded bg-teal-50 px-1.5 py-0.5 text-teal-700">入库 {item.goodsdocNo}</span>)}{selected.settlement.map((item) => <span key={item.targetId} className="rounded bg-teal-50 px-1.5 py-0.5 text-teal-700">结算 {item.settlementNo}</span>)}{selected.invoice.map((item) => <span key={item.invoiceId} className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-700">发票 {item.invoiceNo}</span>)}{!selected.purchaseOrders.length && !selected.inbound.length && !selected.settlement.length && !selected.invoice.length && <span className="text-slate-400">尚未关联任何下游单据</span>}</div>
            {expanded && <div className="mt-3 grid gap-2 lg:grid-cols-2">
              <article className="rounded-md border border-teal-200 bg-teal-50/20 p-2 lg:col-span-2"><div className="mb-2 flex flex-wrap items-center gap-2"><h3 className="text-[10.5px] font-semibold text-teal-800">吉客云采购链</h3><span className="text-[9.5px] text-teal-600">采购单 → 入库单 → SKU 明细</span><span className="text-[9px] text-slate-400">同一条链路连续处理，不拆成两个关联动作</span></div><div className="grid gap-2 lg:grid-cols-2">
                <div className="rounded-md border border-teal-100 bg-white p-2"><h4 className="mb-1.5 text-[10.5px] font-semibold text-teal-700">采购单</h4>{!selected.externalPoId ? <p className="text-[10px] leading-4 text-amber-600">该订单还没有平台采购工作流。先完成 SKU 分配并生成采购单，才可以关联吉客云采购单。</p> : <div className="space-y-1.5">{candidateForOrder(purchaseOrders, selected).slice(0, 4).map((po) => { const key = `po-${po.id ?? po.purchNo}`; return <div key={key} className="flex items-center justify-between gap-2 rounded bg-slate-50 px-2 py-1.5 text-[10px]"><span className="min-w-0 truncate font-mono text-slate-700">{po.purchNo} <span className="font-sans text-slate-400">{po.supplierName || "未填写供应商"} · {money(po.amount)}</span></span><button disabled={saving === key || busy} onClick={() => void linkPurchaseOrder(po)} className="shrink-0 rounded border border-indigo-200 bg-white px-1.5 py-0.5 text-[9.5px] text-indigo-600 disabled:opacity-50">{saving === key ? "保存…" : "关联"}</button></div>; })}{!purchaseOrders.length && <p className="py-2 text-center text-[10px] text-slate-400">暂无采购单候选</p>}</div>}</div>
                <div className="rounded-md border border-teal-100 bg-white p-2"><h4 className="mb-1.5 text-[10.5px] font-semibold text-teal-700">入库单及待分配 SKU</h4>{loading ? <p className="py-2 text-center text-[10px] text-slate-400">加载候选中…</p> : list(inbound, "inbound")}</div>
              </div></article>
              <article className="rounded-md border border-teal-100 p-2"><h3 className="mb-1.5 text-[10.5px] font-semibold text-teal-700">吉客云结算单</h3>{loading ? <p className="py-2 text-center text-[10px] text-slate-400">加载候选中…</p> : list(settlement, "settlement")}</article>
              <article className="rounded-md border border-amber-100 p-2"><h3 className="mb-1.5 text-[10.5px] font-semibold text-amber-700">税务进项发票</h3>{loading ? <p className="py-2 text-center text-[10px] text-slate-400">加载候选中…</p> : <div className="space-y-1.5">{invoiceCandidates.map((invoice) => { const key = `invoice-${invoice.id}`; const diff = Math.abs(Number(invoice.totalAmount ?? 0) - Number(selected.amount ?? 0)); return <div key={invoice.id} className="flex items-center justify-between gap-2 rounded bg-slate-50 px-2 py-1.5 text-[10px]"><div className="min-w-0"><div className="truncate font-mono text-slate-700">{invoice.invoiceNumber || invoice.invoiceCode} <span className="font-sans text-slate-400">{money(invoice.totalAmount)}</span></div><div className="truncate text-[9px] text-slate-400">{invoice.sellerName || "未填写销售方"} · {date(invoice.issueDate)} · 差额 {money(diff)}</div></div><button disabled={saving === key || busy} onClick={() => void linkInvoice(invoice)} className="shrink-0 rounded border border-amber-200 bg-white px-1.5 py-0.5 text-[9.5px] text-amber-700 disabled:opacity-50">{saving === key ? "保存…" : "关联"}</button></div>; })}{!invoiceCandidates.length && <p className="py-2 text-center text-[10px] text-slate-400">暂无已确认的进项发票候选</p>}</div>}</article>
            </div>}
          </>}
        </div>
      </div>
    </section>
  );
}
