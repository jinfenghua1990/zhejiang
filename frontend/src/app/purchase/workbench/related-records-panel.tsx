"use client";

import { useState } from "react";
import Link from "next/link";
import { authenticatedFetch, procurementChainApi, taxInvoiceApi, type TaxInvoiceRow, type WorkbenchDetail } from "@/lib/api";

type Invoice = { invoiceId: number; linkId: number; invoiceKind: "tax" | "manual"; invoiceNo: string; amount: number | null; issueDate: string | null; verified: boolean; verifiedMonth: string; matchMethod?: string; confidence?: number | null; note?: string };
const money = (value: number | null | undefined) => value == null ? "未提供" : `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
const inputClass = "h-8 min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs";
const buttonClass = "rounded-md bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-600 hover:bg-indigo-100 disabled:opacity-40";

async function mutate(path: string, method: string, body?: object) {
  const res = await authenticatedFetch(`/api/v1/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  const result = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof result.detail === "string" ? result.detail : `保存失败（${res.status}）`);
  return result;
}

// 发票与认证记录面板：发票导入后由采购链自动匹配；这里只保留错误关联和未匹配时的快速处理。
export function RelatedRecordsPanel({ detail, onChanged }: {
  detail: WorkbenchDetail; onChanged: () => Promise<void>;
}) {
  const { order } = detail;
  const invoices = detail.detail.invoice as Invoice[];
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [taxCandidates, setTaxCandidates] = useState<TaxInvoiceRow[]>([]);

  async function action(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try { await work(); await onChanged(); setMessage(success); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试"); }
    finally { setBusy(false); }
  }

  async function openTaxPicker() {
    setPickerOpen(true); setQuery(""); setError(""); setBusy(true);
    setTaxCandidates([]);
    try {
      setTaxCandidates(await taxInvoiceApi.invoices({ direction: "input", status: "issued" }));
    } catch (e) { setError(e instanceof Error ? e.message : "单据加载失败"); }
    finally { setBusy(false); }
  }

  async function linkTax(invoice: TaxInvoiceRow) {
    await action(async () => {
      await mutate("procurement-chain/invoice-links", "POST", { order_id: order.orderId, invoice_id: invoice.id });
      setPickerOpen(false);
    }, "税务清单发票已关联本单");
  }

  return <section className="space-y-3 rounded-lg border border-slate-200/80 bg-white p-3" aria-label="发票与认证记录">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold text-slate-700">发票与认证</h3>
      <button disabled={busy} onClick={() => void openTaxPicker()} className={buttonClass}>{pickerOpen ? "关闭快速关联" : "快速关联"}</button>
    </div>
    <p className="text-[11px] text-slate-400">发票导入后按订单号、供应商、金额和日期自动匹配；认证状态跟随发票池同步。仅在自动结果不正确或未匹配时处理。</p>
    {invoices.length === 0 && <p className="text-xs text-amber-600">尚未自动匹配发票，可使用右上角「快速关联」</p>}
    {invoices.map(inv => <div key={`${inv.invoiceKind}-${inv.linkId}`} className="space-y-2 rounded bg-slate-50 p-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono">{inv.invoiceNo || "未提供号码"}</span><span>{money(inv.amount)} · {inv.invoiceKind === "tax" ? "税务清单" : "手工记录"}</span><button disabled={busy} className="text-red-500" onClick={() => {
        if (window.confirm("解除发票与本单的关联？原始发票保留。")) void action(() => inv.invoiceKind === "tax" ? procurementChainApi.deleteInvoiceLink(inv.linkId) : mutate(`purchase/invoice-links/${inv.linkId}`, "DELETE"), "发票关联已解除");
      }}>解除关联</button></div>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className={inv.verified ? "text-emerald-600" : "text-slate-500"}>{inv.verified ? `已认证${inv.verifiedMonth ? ` · ${inv.verifiedMonth}` : ""}` : "待认证"}</span>
        {inv.invoiceKind === "tax" && <span className="text-slate-400">· {inv.matchMethod === "source_ref" ? "按关联单号自动匹配" : inv.matchMethod === "auto" ? "系统自动匹配" : "系统关联"}</span>}
        {inv.invoiceKind === "manual" && <span className="text-slate-400">· 手工记录，待税务发票池核对</span>}
      </div>
    </div>)}
    {pickerOpen && <div className="space-y-2 rounded-lg border border-indigo-100 bg-indigo-50/30 p-2">
      <div className="flex items-center justify-between text-xs"><strong>快速关联进项发票</strong><button onClick={() => setPickerOpen(false)}>取消</button></div>
      <p className="text-[11px] text-slate-400">系统已优先自动匹配；从下方选择仅用于自动匹配失败或关联错误的情况。</p>
      <input aria-label="搜索关联单据" placeholder="搜索发票号或供应商" value={query} onChange={e => setQuery(e.target.value)} className={`${inputClass} w-full`} />
      <div className="max-h-60 space-y-1 overflow-y-auto">
        {busy && <p className="text-xs text-slate-400">正在读取…</p>}
        {taxCandidates.filter(row => `${row.invoiceNumber} ${row.sellerName}`.toLowerCase().includes(query.toLowerCase())).map(row => <button key={row.id} disabled={busy || invoices.some(inv => inv.invoiceKind === "tax" && inv.invoiceId === row.id)} className="flex w-full justify-between gap-2 rounded bg-white p-2 text-left text-xs hover:bg-indigo-50 disabled:opacity-40" onClick={() => void linkTax(row)}>
          <span>{row.invoiceNumber}<span className="block text-slate-400">{row.sellerName} · {row.issueDate?.slice(0, 10)}</span></span><span>{row.totalAmount ? money(Number(row.totalAmount)) : "未提供"}</span>
        </button>)}
        {!busy && taxCandidates.length === 0 && <p className="py-3 text-xs text-slate-500">暂无可选发票，请先导入税务清单并确认已开票。</p>}
      </div>
      <Link href="/finance/invoices" className="block text-xs text-indigo-600">进入发票管理导入清单 →</Link>
    </div>}
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {message && <p role="status" className="text-xs text-emerald-600">{message}</p>}
  </section>;
}
