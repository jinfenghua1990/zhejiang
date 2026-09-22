"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type LedgerItem = {
  invoiceId: number;
  invoiceNumber: string;
  direction: string;
  status: string;
  issueDate: string | null;
  sellerName: string;
  buyerName: string;
  amountExclTax: string | null;
  taxAmount: string | null;
  totalAmount: string | null;
  verified: boolean;
  invoiceLine: {
    goodsName: string;
    specification: string;
    unit: string;
    quantity: string | null;
    unitPriceExclTax: string | null;
    taxRate: string | null;
    taxCode: string;
    detailComplete: boolean;
  };
  businessRefs: string[];
  businessAmount: string | null;
  businessQuantity: string | null;
  amountDifference: string | null;
  quantityDifference: string | null;
};

type Ledger = {
  period: string;
  policy: { note: string };
  summary: {
    invoiceCount: number;
    outputInvoiceCount: number;
    inputInvoiceCount: number;
    outputTotalAmount: string | null;
    outputTaxAmount: string | null;
    inputTotalAmount: string | null;
    verifiedInputTaxAmount: string | null;
    estimatedVatBeforeOtherAdjustments: string | null;
    amountDifferenceCount: number;
    quantityDifferenceCount: number;
    blockerCount: number;
    readyForAccountingDraft: boolean;
  };
  blockers: { invoiceId: number; invoiceNumber: string; reasons: string[] }[];
  items: LedgerItem[];
};

type FinanceCategory = {
  accountingCategory: string;
  taxRate: string;
  quantity: string | null;
  unit: string;
  quantityComplete: boolean;
  amountExclTax: string | null;
  taxAmount: string | null;
  totalAmount: string | null;
  invoiceCount: number;
  detailCount: number;
};

type FinanceSummary = {
  period: string;
  readyForFinanceDelivery: boolean;
  summary: {
    categoryRowCount: number;
    detailCount: number;
    amountExclTax: string | null;
    taxAmount: string | null;
    totalAmount: string | null;
    blockerCount: number;
    warningCount: number;
  };
  categories: FinanceCategory[];
  blockers: { invoiceNumber: string; goodsName: string; reasons: string[] }[];
  warnings: { invoiceNumber: string; warning: string }[];
};

function previousMonth() {
  const now = new Date();
  const value = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

function money(value: string | null) {
  if (value === null) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : value;
}

export default function TaxAccountingPage() {
  const [period, setPeriod] = useState(previousMonth);
  const [data, setData] = useState<Ledger | null>(null);
  const [financeSummary, setFinanceSummary] = useState<FinanceSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [year, month] = period.split("-").map(Number);
    if (!year || !month) return;
    setLoading(true);
    setError("");
    try {
      const [ledgerRes, summaryRes] = await Promise.all([
        authenticatedFetch(`/api/v1/tax-accounting/monthly-ledger?year=${year}&month=${month}`, { cache: "no-store" }),
        authenticatedFetch(`/api/v1/tax-accounting/finance-summary?year=${year}&month=${month}`, { cache: "no-store" }),
      ]);
      const [ledgerPayload, summaryPayload] = await Promise.all([ledgerRes.json(), summaryRes.json()]);
      if (!ledgerRes.ok) throw new Error(ledgerPayload?.detail || `做账底稿加载失败（${ledgerRes.status}）`);
      if (!summaryRes.ok) throw new Error(summaryPayload?.detail || `财务汇总加载失败（${summaryRes.status}）`);
      setData(ledgerPayload);
      setFinanceSummary(summaryPayload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { void load(); }, [load]);

  const blockerByInvoice = useMemo(() => {
    const map = new Map<number, string[]>();
    data?.blockers.forEach((row) => map.set(row.invoiceId, row.reasons));
    return map;
  }, [data]);

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">TAX ACCOUNTING</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">税务做账</h1>
            <p className="mt-1 text-sm text-slate-500">财务按大类做账 · 开票为准 · 底层明细永久保留</p>
          </div>
          <div className="flex items-end gap-2">
            <label className="text-xs text-slate-500">账期
              <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} className="mt-1 block rounded-lg border border-slate-200 px-3 py-2 text-sm" />
            </label>
            <button onClick={() => void load()} disabled={loading} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
              {loading ? "核对中…" : "重新核对"}
            </button>
          </div>
        </div>
      </header>

      <section className="rounded-2xl border border-indigo-100 bg-indigo-50/50 px-5 py-4">
        <div className="text-sm font-semibold text-indigo-900">财务交付口径</div>
        <p className="mt-1 text-sm leading-6 text-indigo-800">
          发给财务的销售主表按“财务大类 + 税率”汇总，例如具体饮料统一汇总到“软饮料”。发票号、商品、数量、单价、税额等原始明细全部保留在系统内且不可删除；吉客云、1688、手工数据只用于核对，不覆盖开票数据。
        </p>
      </section>

      {error && <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      {financeSummary && (
        <section className="rounded-2xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="text-base font-semibold text-slate-900">发给财务的分类汇总</h2>
              <div className="mt-1 text-xs text-slate-500">这张表才是默认财务销售主表；下方逐条发票仅供内部追溯。</div>
            </div>
            <span className={`rounded-full px-3 py-1 text-xs font-medium ${financeSummary.readyForFinanceDelivery ? "bg-emerald-50 text-emerald-700" : "bg-amber-100 text-amber-800"}`}>
              {financeSummary.readyForFinanceDelivery ? "可交付财务" : `待处理 ${financeSummary.summary.blockerCount} 项`}
            </span>
          </div>
          <div className="grid gap-3 border-b border-slate-100 p-4 md:grid-cols-4">
            <Metric title="财务大类行数" value={String(financeSummary.summary.categoryRowCount)} />
            <Metric title="不含税金额" value={money(financeSummary.summary.amountExclTax)} />
            <Metric title="税额" value={money(financeSummary.summary.taxAmount)} />
            <Metric title="价税合计" value={money(financeSummary.summary.totalAmount)} />
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-4 py-2.5">财务大类</th>
                  <th className="px-4 py-2.5">税率</th>
                  <th className="px-4 py-2.5 text-right">数量</th>
                  <th className="px-4 py-2.5">单位</th>
                  <th className="px-4 py-2.5 text-right">不含税金额</th>
                  <th className="px-4 py-2.5 text-right">税额</th>
                  <th className="px-4 py-2.5 text-right">价税合计</th>
                  <th className="px-4 py-2.5 text-right">底层明细</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {financeSummary.categories.map((row) => (
                  <tr key={`${row.accountingCategory}-${row.taxRate}`}>
                    <td className="px-4 py-3 font-medium text-slate-900">{row.accountingCategory}</td>
                    <td className="px-4 py-3">{row.taxRate}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{row.quantity ?? "—"}</td>
                    <td className="px-4 py-3 text-slate-500">{row.unit || "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{money(row.amountExclTax)}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{money(row.taxAmount)}</td>
                    <td className="px-4 py-3 text-right font-medium tabular-nums">{money(row.totalAmount)}</td>
                    <td className="px-4 py-3 text-right text-xs text-slate-500">{row.detailCount} 条 · {row.invoiceCount} 张票</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {financeSummary.categories.length === 0 && <div className="p-8 text-center text-sm text-slate-400">暂无可汇总的销项开票数据</div>}
          </div>
          {(financeSummary.blockers.length > 0 || financeSummary.warnings.length > 0) && (
            <div className="border-t border-slate-100 px-5 py-4 text-xs leading-6 text-amber-800">
              {financeSummary.blockers.slice(0, 6).map((row, index) => <div key={`${row.invoiceNumber}-${index}`}>阻塞：{row.invoiceNumber} {row.goodsName ? `· ${row.goodsName}` : ""} · {row.reasons.join("；")}</div>)}
              {financeSummary.warnings.slice(0, 4).map((row, index) => <div key={`w-${row.invoiceNumber}-${index}`} className="text-slate-500">提示：{row.invoiceNumber} · {row.warning}</div>)}
            </div>
          )}
        </section>
      )}

      {data && (
        <>
          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
            <Metric title="销项价税合计" value={money(data.summary.outputTotalAmount)} />
            <Metric title="销项税额" value={money(data.summary.outputTaxAmount)} />
            <Metric title="进项价税合计" value={money(data.summary.inputTotalAmount)} />
            <Metric title="已认证进项税" value={money(data.summary.verifiedInputTaxAmount)} />
            <Metric title="税额初步差额" value={money(data.summary.estimatedVatBeforeOtherAdjustments)} note="未计其他调整" />
            <Metric title="内部核对状态" value={data.summary.readyForAccountingDraft ? "可生成底稿" : "待核对"} note={`${data.summary.blockerCount} 项阻塞`} />
          </section>

          {data.blockers.length > 0 && (
            <section className="rounded-2xl border border-amber-200 bg-amber-50/50 p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-base font-semibold text-amber-900">必须核对</h2>
                <span className="text-xs text-amber-700">金额差异 {data.summary.amountDifferenceCount} · 数量差异 {data.summary.quantityDifferenceCount}</span>
              </div>
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {data.blockers.slice(0, 12).map((row) => (
                  <div key={row.invoiceId} className="rounded-xl border border-amber-100 bg-white px-3 py-3 text-xs leading-5 text-slate-600">
                    <div className="font-medium text-slate-800">发票 {row.invoiceNumber}</div>
                    <div className="mt-1">{row.reasons.join("；")}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <div><h2 className="text-sm font-semibold text-slate-900">底层开票明细</h2><div className="mt-1 text-[11px] text-slate-500">完整保留 · 不随财务主表逐条发送 · 不可删除</div></div>
              <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[10px] font-medium text-slate-600">审计留痕</span>
            </div>
            <table className="w-full min-w-[1500px] text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-3 py-2.5">状态</th>
                  <th className="px-3 py-2.5">发票</th>
                  <th className="px-3 py-2.5">开票项目</th>
                  <th className="px-3 py-2.5 text-right">开票数量</th>
                  <th className="px-3 py-2.5 text-right">开票单价(未税)</th>
                  <th className="px-3 py-2.5 text-right">不含税金额</th>
                  <th className="px-3 py-2.5 text-right">税额</th>
                  <th className="px-3 py-2.5 text-right">价税合计</th>
                  <th className="px-3 py-2.5">业务单</th>
                  <th className="px-3 py-2.5 text-right">业务金额</th>
                  <th className="px-3 py-2.5 text-right">金额差异</th>
                  <th className="px-3 py-2.5 text-right">数量差异</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((row) => {
                  const blocked = blockerByInvoice.has(row.invoiceId);
                  return (
                    <tr key={row.invoiceId} className={blocked ? "bg-amber-50/30" : ""}>
                      <td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-medium ${blocked ? "bg-amber-100 text-amber-800" : "bg-emerald-50 text-emerald-700"}`}>{blocked ? "待核对" : "开票真值"}</span></td>
                      <td className="px-3 py-3"><div className="font-medium text-slate-800">{row.invoiceNumber}</div><div className="mt-1 text-[10px] text-slate-400">{row.direction === "output" ? "销项" : row.direction === "input" ? "进项" : "方向待确认"} · {row.issueDate ? new Date(row.issueDate).toLocaleDateString("zh-CN") : "—"}</div></td>
                      <td className="max-w-[240px] px-3 py-3"><div className="truncate text-slate-700">{row.invoiceLine.goodsName || "—"}</div><div className="mt-1 text-[10px] text-slate-400">{row.invoiceLine.specification || ""}{row.invoiceLine.taxRate ? ` · 税率 ${row.invoiceLine.taxRate}%` : ""}</div></td>
                      <td className="px-3 py-3 text-right tabular-nums">{row.invoiceLine.quantity ?? "—"}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.invoiceLine.unitPriceExclTax)}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.amountExclTax)}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.taxAmount)}</td>
                      <td className="px-3 py-3 text-right font-medium tabular-nums text-slate-900">{money(row.totalAmount)}</td>
                      <td className="max-w-[220px] px-3 py-3 text-xs text-slate-500">{row.businessRefs.join("、") || "未关联"}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-500">{money(row.businessAmount)}</td>
                      <td className={`px-3 py-3 text-right tabular-nums ${Number(row.amountDifference || 0) !== 0 ? "font-medium text-amber-700" : "text-slate-400"}`}>{money(row.amountDifference)}</td>
                      <td className={`px-3 py-3 text-right tabular-nums ${Number(row.quantityDifference || 0) !== 0 ? "font-medium text-amber-700" : "text-slate-400"}`}>{row.quantityDifference ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {data.items.length === 0 && <div className="p-8 text-center text-sm text-slate-400">该账期没有已确认的官方税务发票数据</div>}
          </section>
        </>
      )}
    </div>
  );
}

function Metric({ title, value, note }: { title: string; value: string; note?: string }) {
  return <div className="rounded-xl border border-slate-200 bg-white px-4 py-3"><div className="text-[11px] text-slate-500">{title}</div><div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>{note && <div className="mt-1 text-[10px] text-slate-400">{note}</div>}</div>;
}
