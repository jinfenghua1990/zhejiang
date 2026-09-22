"use client";

import { useCallback, useEffect, useState } from "react";
import { procurementWorkbenchApi } from "@/lib/api";

type CoveredOrder = {
  orderId: number; orderNo: string; platform: string; date: string | null;
  orderAmount: number; allocatedAmount?: number; consumed: number; partial: boolean;
  allocationIssue?: boolean; source?: "manual" | "source_ref" | "auto";
};
type PendingOrder = {
  orderId: number; orderNo: string; platform: string; date: string | null;
  orderAmount: number; remaining: number; partial: boolean;
};
type ReconInvoice = {
  invoiceId: number; invoiceNo: string; issueDate: string | null; seller: string;
  amount: number; covered: CoveredOrder[]; coveredTotal: number; diff: number;
  status: "matched" | "short";
  shortReason?: "date_cutoff" | "insufficient_orders" | "explicit_link_issue" | null;
  explicitLinked?: boolean;
};
type ReconSupplier = {
  supplier: string; supplierNorm: string; hasOrders: boolean;
  orderCount: number; orderTotal: number; invoiceCount: number; invoiceTotal: number;
  matchedTotal: number; remainingOrders: number; remainingOrderTotal: number;
  pendingOrders: PendingOrder[];
  months: { month: string; invoices: ReconInvoice[] }[];
};
type ReconData = {
  tolerance: number; skippedZeroOrders: number;
  suppliers: ReconSupplier[];
  expenseSellers: { seller: string; invoiceCount: number; invoiceTotal: number }[];
};

const money = (v: number | null | undefined) => v == null ? "—" : `¥${v.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  matched: { text: "✓ 已配平", cls: "bg-emerald-50 text-emerald-600" },
  short: { text: "订单不足", cls: "bg-amber-50 text-amber-600" },
};

export default function InvoiceReconciliationView() {
  const [data, setData] = useState<ReconData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState("");

  function toggle(key: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  async function copyOrders(supplier: string, orders: PendingOrder[]) {
    const text = orders.map(o => o.orderNo).filter(Boolean).join("\n");
    try { await navigator.clipboard.writeText(text); setCopied(supplier); setTimeout(() => setCopied(""), 2000); }
    catch { window.prompt("复制失败，请手动复制订单号：", text); }
  }

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try { setData(await procurementWorkbenchApi.invoiceReconciliation()); }
    catch (e) { setError(e instanceof Error ? e.message : "发票对账加载失败"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);

  if (loading) return <div className="py-10 text-center text-sm text-slate-400">正在从头计算发票配平…</div>;
  if (error) return (
    <div className="space-y-2 py-10 text-center">
      <p className="text-sm text-red-500">{error}</p>
      <button onClick={() => void load()} className="rounded-md bg-indigo-50 px-3 py-1.5 text-xs text-indigo-600">重试</button>
    </div>
  );
  if (!data) return null;

  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">发票对账</h2>
        <p className="mt-0.5 text-[11px] text-slate-400">
          以发票为主体：同一供应商的进项发票，从最早订单起按时间顺序自动合计配平（容差 ±{data.tolerance} 元）。
          每家供应商下方列出<b className="font-medium text-amber-600">待开票订单号</b>（可一键复制发给供应商），
          发票行展开可看涉及的订单。每次刷新从头重算，无需手工关联。
        </p>
      </div>
      <div className="flex items-center gap-2 text-[11px] text-slate-400">
        {data.skippedZeroOrders > 0 && <span>{data.skippedZeroOrders} 张订单金额未同步，未参与配平</span>}
        <button onClick={() => void load()} className="rounded-md bg-slate-100 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-200">重新计算</button>
      </div>
    </div>

    {data.suppliers.length === 0 && <div className="rounded-lg border border-dashed border-slate-200 p-8 text-center text-xs text-slate-400">暂无已开票的进项发票可对账</div>}

    {data.suppliers.map(s => {
      const allMatched = s.invoiceTotal > 0 && s.remainingOrderTotal <= 0.05 || s.matchedTotal >= s.invoiceTotal;
      return (
        <section key={s.supplierNorm} className="space-y-3 rounded-lg border border-slate-200/80 bg-white p-3">
          <header className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="flex items-center gap-2 text-xs font-semibold text-slate-800">
              {s.supplier}
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${allMatched ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-500"}`}>
                {allMatched ? "✓ 全部对上" : "待配平"}
              </span>
            </h3>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
              <span>订单 {s.orderCount} 张 · {money(s.orderTotal)}</span>
              <span>发票 {s.invoiceCount} 张 · {money(s.invoiceTotal)}</span>
              <span className="text-emerald-600">已配平 {money(s.matchedTotal)}</span>
            </div>
          </header>
          {s.pendingOrders.length > 0 && (() => {
            const key = `pending-${s.supplierNorm}`;
            const open = expanded.has(key);
            return <div className="rounded-md border border-amber-100 bg-amber-50/50">
              <div className="flex flex-wrap items-center justify-between gap-2 p-2">
                <button onClick={() => toggle(key)} className="flex items-center gap-2 text-xs font-medium text-amber-700">
                  待找供应商开票 · {s.remainingOrders} 单 · {money(s.remainingOrderTotal)}
                  <span className="text-[10px] font-normal text-amber-500">{open ? "收起" : "展开订单号"}</span>
                </button>
                <button onClick={() => void copyOrders(s.supplier, s.pendingOrders)}
                  className="rounded bg-amber-100 px-2 py-1 text-[11px] font-medium text-amber-700 hover:bg-amber-200">
                  {copied === s.supplier ? "✓ 已复制" : "复制订单号"}
                </button>
              </div>
              {open && <div className="max-h-64 space-y-1 overflow-y-auto border-t border-amber-100 p-2">
                {s.pendingOrders.map(o => (
                  <div key={`${o.orderId}-${o.remaining}`} className="flex flex-wrap items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px]">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="font-mono text-slate-600">{o.orderNo}</span>
                      <span className="text-slate-400">{o.date ?? "无日期"}</span>
                      {o.partial && <span className="rounded bg-violet-50 px-1 py-px text-[10px] text-violet-600">部分已开 {money(o.orderAmount - o.remaining)}</span>}
                    </span>
                    <span className="text-slate-500">{money(o.remaining)}</span>
                  </div>
                ))}
              </div>}
            </div>;
          })()}
          {s.months.map(group => (
            <div key={group.month} className="space-y-1.5">
              <div className="flex items-center gap-2 text-[11px] font-medium text-slate-400">
                <span className="h-1.5 w-1.5 rounded-full bg-indigo-300" />{group.month} 开票
              </div>
              {group.invoices.map(inv => {
                const key = `${s.supplierNorm}-${inv.invoiceId}`;
                const open = expanded.has(key);
                const badge = STATUS_BADGE[inv.status] ?? STATUS_BADGE.short;
                return (
                  <div key={key} className="rounded-md border border-slate-100 bg-slate-50/60">
                    <button onClick={() => toggle(key)} className="flex w-full flex-wrap items-center justify-between gap-2 p-2 text-left text-xs">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="font-mono text-slate-700">{inv.invoiceNo}</span>
                        <span className="text-slate-400">{inv.issueDate ?? "无日期"}</span>
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${inv.shortReason === "explicit_link_issue" ? "bg-rose-50 text-rose-600" : badge.cls}`}>{inv.shortReason === "explicit_link_issue" ? "明确关联待核对" : badge.text}</span>
                      </span>
                      <span className="flex items-center gap-3">
                        <span className="font-medium text-slate-700">{money(inv.amount)}</span>
                        <span className="text-[10px] text-slate-400">{open ? "收起" : `覆盖 ${inv.covered.length} 单`}</span>
                      </span>
                    </button>
                    {open && <div className="space-y-1 border-t border-slate-100 p-2">
                      {inv.covered.length === 0 && <p className={`text-[11px] ${inv.shortReason === "explicit_link_issue" ? "text-rose-600" : "text-slate-400"}`}>{inv.shortReason === "explicit_link_issue" ? "已有明确关联，但订单映射或分摊金额不完整；系统未使用 FIFO 猜单。" : "没有可配平的订单（该供应商订单可能未导入或金额未同步）"}</p>}
                      {inv.covered.map(o => (
                        <div key={`${o.orderId}-${o.consumed}`} className="flex flex-wrap items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px]">
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="font-mono text-slate-600">{o.orderNo}</span>
                            <span className="text-slate-400">{o.date ?? "无日期"}</span>
                            {o.source === "manual" && <span className="rounded bg-indigo-50 px-1 py-px text-[10px] text-indigo-600">手动</span>}
                            {o.source === "source_ref" && <span className="rounded bg-sky-50 px-1 py-px text-[10px] text-sky-600">清单</span>}
                            {o.partial && <span className="rounded bg-violet-50 px-1 py-px text-[10px] text-violet-600">部分消耗</span>}
                          </span>
                          <span className="text-slate-500">
                            本次 {money(o.consumed)}{o.allocatedAmount != null && o.allocatedAmount !== o.consumed ? <span className="text-rose-500"> / 关联 {money(o.allocatedAmount)}</span> : null}{o.partial && <span className="text-slate-400"> / 订单 {money(o.orderAmount)}</span>}
                          </span>
                        </div>
                      ))}
                      <div className="flex justify-between px-2 pt-1 text-[11px]">
                        <span className="text-slate-400">合计消耗</span>
                        <span className={inv.status === "matched" ? "font-medium text-emerald-600" : "font-medium text-amber-600"}>
                          {money(inv.coveredTotal)}{inv.status === "short" && <span className="text-slate-400">（距票面还差 {money(inv.amount - inv.coveredTotal)}{inv.shortReason === "explicit_link_issue" ? "，请核对明确关联" : "，可能仍有订单未导入"}）</span>}
                        </span>
                      </div>
                    </div>}
                  </div>
                );
              })}
            </div>
          ))}
        </section>
      );
    })}

    {data.expenseSellers.length > 0 && (
      <details className="rounded-lg border border-slate-200/80 bg-white p-3">
        <summary className="cursor-pointer text-xs font-medium text-slate-600">费用类发票（无对应采购订单，{data.expenseSellers.length} 家 / {money(data.expenseSellers.reduce((a, e) => a + e.invoiceTotal, 0))}）</summary>
        <div className="mt-2 space-y-1">
          {data.expenseSellers.map(e => (
            <div key={e.seller} className="flex justify-between rounded bg-slate-50 px-2 py-1 text-[11px]">
              <span className="text-slate-600">{e.seller}</span>
              <span className="text-slate-400">{e.invoiceCount} 张 · {money(e.invoiceTotal)}</span>
            </div>
          ))}
        </div>
      </details>
    )}
  </div>;
}
