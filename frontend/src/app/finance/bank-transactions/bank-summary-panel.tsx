"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";
import { useTabScopedState } from "@/lib/workspace/tab-store";

type BankAccountSummary = {
  accountId: number | null;
  accountNo: string;
  accountCode: string;
  accountName: string;
  bankName: string;
  currency: string;
  openingBalance: string;
  systemBalance: string;
  monthIncome: string;
  monthExpense: string;
  monthNet: string;
  monthTxnCount: number;
  pendingIncomeCount: number;
  pendingExpenseCount: number;
  pendingCount: number;
  lastTxnDate: string;
  balanceSource: "opening_plus_imported_transactions" | "imported_transactions" | string;
};

type BankSummary = {
  year: number;
  month: number;
  summary: {
    accountCount: number;
    systemBalance: string;
    monthIncome: string;
    monthExpense: string;
    monthNet: string;
    monthTxnCount: number;
    pendingIncomeCount: number;
    pendingExpenseCount: number;
    pendingCount: number;
  };
  accounts: BankAccountSummary[];
};

const CARD = "rounded-xl border border-slate-200 bg-white shadow-sm";

function previousMonthValue() {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function money(value: string | number | null | undefined) {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount)
    ? `¥${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "—";
}

function accountLabel(row: BankAccountSummary) {
  return row.accountName || row.accountNo || row.accountCode || "未归属账户";
}

export function BankSummaryPanel({
  period: controlledPeriod,
  onPeriodChange,
}: {
  period?: string;
  onPeriodChange?: (value: string) => void;
} = {}) {
  const [storedPeriod, setStoredPeriod] = useTabScopedState("bank.period", previousMonthValue);
  const period = controlledPeriod ?? storedPeriod;
  const setPeriod = onPeriodChange ?? setStoredPeriod;
  const [data, setData] = useState<BankSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const selected = useMemo(() => {
    const [year, month] = period.split("-").map(Number);
    return year && month ? { year, month } : null;
  }, [period]);


  const load = useCallback(async () => {
    if (!selected) return;
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(
        `/api/v1/finance/bank-summary?year=${selected.year}&month=${selected.month}`,
        { cache: "no-store" },
      );
      const payload = (await response.json().catch(() => ({}))) as BankSummary & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || `加载失败（${response.status}）`);
      setData(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    void load();
  }, [load]);

  const matchHref = `/finance/monthly-send?tab=match&month=${encodeURIComponent(period)}`;
  const transactionHref = (row?: BankAccountSummary) => {
    const params = new URLSearchParams({ view: "transactions", period });
    if (row?.accountNo) params.set("account", row.accountNo);
    return `/finance/bank-transactions?${params.toString()}`;
  };

  return (
    <div className="mx-auto w-full max-w-[1600px] space-y-4 pb-8">
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-2 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="mb-1 text-[11px] font-medium tracking-wide text-blue-600">财务中心 / 银行</div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">账户汇总</h1>
            <p className="mt-1 text-xs text-slate-500">这里按银行账户看资金结果和流水；月度财务核对仍以进项发票为主，再核对对应银行付款。</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="month"
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
              aria-label="银行汇总账期"
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 outline-none focus:border-blue-400"
            />
            <Link href={matchHref} className="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white shadow-sm hover:bg-blue-700">
              进入发票核对
            </Link>
            <button type="button" onClick={() => void load()} disabled={loading} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">
              刷新
            </button>
          </div>
        </div>
      </header>

      {error && <div role="alert" className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-5">
        {[
          ["系统流水余额", money(data?.summary.systemBalance), `${data?.summary.accountCount ?? 0} 个账户 · 按已导入流水`],
          ["本月收入", money(data?.summary.monthIncome), `${data?.summary.monthTxnCount ?? 0} 笔流水`],
          ["本月支出", money(data?.summary.monthExpense), "用于月度发票核对的银行依据"],
          ["本月净流入", money(data?.summary.monthNet), Number(data?.summary.monthNet || 0) >= 0 ? "流入大于流出" : "流出大于流入"],
          ["待对账", String(data?.summary.pendingCount ?? 0), `收入 ${data?.summary.pendingIncomeCount ?? 0} · 支出 ${data?.summary.pendingExpenseCount ?? 0}`],
        ].map(([label, value, hint]) => (
          <div key={label} className={`${CARD} px-4 py-3`}>
            <div className="text-xs text-slate-400">{label}</div>
            <div className="mt-1 text-xl font-semibold tracking-tight text-slate-900">{value}</div>
            <div className="mt-1 text-[11px] text-slate-400">{hint}</div>
          </div>
        ))}
      </section>

      <section className={`${CARD} overflow-hidden`}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">对公账户</h2>
            <p className="mt-1 text-xs text-slate-400">系统流水余额不是银行实时余额：有期初余额时按“期初 + 已导入收支”计算；没有期初余额时只显示已导入流水净额。</p>
          </div>
          <Link href={transactionHref()} className="rounded-lg border border-blue-200 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50">全部银行流水</Link>
        </div>

        {loading && !data ? (
          <div className="px-4 py-16 text-center text-sm text-slate-400">正在汇总银行账户…</div>
        ) : !data?.accounts.length ? (
          <div className="px-4 py-16 text-center">
            <div className="text-sm font-medium text-slate-600">暂无银行账户数据</div>
            <div className="mt-2 text-xs text-slate-400">先上传银行交易明细，系统会自动建立账户与流水。</div>
            <Link href="/finance/bank-transactions?view=transactions" className="mt-4 inline-flex rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-blue-700">去银行流水</Link>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1180px] text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">账户</th>
                  <th className="px-4 py-3 font-medium">银行</th>
                  <th className="px-4 py-3 text-right font-medium">系统流水余额</th>
                  <th className="px-4 py-3 text-right font-medium">本月收入</th>
                  <th className="px-4 py-3 text-right font-medium">本月支出</th>
                  <th className="px-4 py-3 text-right font-medium">本月净额</th>
                  <th className="px-4 py-3 text-right font-medium">流水笔数</th>
                  <th className="px-4 py-3 font-medium">待对账</th>
                  <th className="px-4 py-3 font-medium">最后流水</th>
                  <th className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.accounts.map((row, index) => (
                  <tr key={row.accountId ?? `unknown-${index}`} className="hover:bg-blue-50/30">
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-800">{accountLabel(row)}</div>
                      <div className="mt-0.5 font-mono text-[11px] text-slate-400">{row.accountNo || "未绑定账号"}{row.accountCode ? ` · 内部编号 ${row.accountCode}` : ""}</div>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{row.bankName || "—"}</td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-slate-800">{money(row.systemBalance)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-emerald-700">{money(row.monthIncome)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-700">{money(row.monthExpense)}</td>
                    <td className={`px-4 py-3 text-right font-medium tabular-nums ${Number(row.monthNet || 0) >= 0 ? "text-emerald-700" : "text-rose-600"}`}>{money(row.monthNet)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-slate-600">{row.monthTxnCount}</td>
                    <td className="px-4 py-3">
                      {row.pendingCount ? (
                        <div className="text-xs">
                          <span className="inline-flex rounded-full bg-amber-50 px-2 py-1 font-medium text-amber-700 ring-1 ring-inset ring-amber-200">{row.pendingCount} 笔</span>
                          <div className="mt-1 text-[10px] text-slate-400">收入 {row.pendingIncomeCount} · 支出 {row.pendingExpenseCount}</div>
                        </div>
                      ) : <span className="inline-flex rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">已对清</span>}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">{row.lastTxnDate || "—"}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <Link href={transactionHref(row)} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50">查看流水</Link>
                        {row.pendingExpenseCount > 0 && <Link href={matchHref} className="rounded-md border border-blue-200 px-2.5 py-1.5 text-[11px] font-medium text-blue-600 hover:bg-blue-50">核对发票</Link>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
