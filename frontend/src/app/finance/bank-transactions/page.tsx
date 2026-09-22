"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { authenticatedFetch, downloadAuthenticatedFile, type ReconTxn } from "@/lib/api";
import { BankSummaryPanel } from "@/app/finance/bank-transactions/bank-summary-panel";
import { useTabRuntime, useTabScopedState, useTabTitle, useWorkspace } from "@/lib/workspace/tab-store";

type DirectionFilter = "all" | "in" | "out";
type MatchFilter = "all" | "matched" | "unmatched";
type TimeScope = "all" | "period";

type BankRawDetail = {
  id: number;
  txnDate: string;
  transactionTime: string | null;
  accountNo: string;
  serialNo: string;
  voucherNo: string;
  sourceRowNumber: number | null;
  importBatchId: number | null;
  accountCode: string;
  raw: {
    sheet?: string;
    rowNumber?: number;
    headers?: string[];
    values?: unknown[];
    fields?: Record<string, unknown>;
    sourceHistory?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  sourceFile: { id: number; fileName: string; sha256: string; size: number; version: number; downloadUrl?: string } | null;
};

const CARD = "rounded-xl border border-slate-200 bg-white shadow-sm";

function previousMonth() {
  const now = new Date();
  const value = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

function money(value: string | number | null | undefined) {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount)
    ? `¥${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "—";
}

function dateText(value: string) {
  return value ? value.slice(0, 10) : "—";
}

function rawValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

function settlementMatchStatus(row: ReconTxn) {
  // 银行收入只认回款 settlement 域；旧 matched 不参与页面判断。
  return row.settlementMatchStatus || "unmatched";
}

function expenseMatchStatus(row: ReconTxn) {
  // 银行支出只认付款核对域；旧 invoiceMatchStatus / invoiceMatched 不参与页面判断。
  return row.invoicePaymentMatchStatus || "unmatched";
}

function matchText(row: ReconTxn) {
  if (row.direction === "in") return settlementMatchStatus(row) === "matched" ? "已匹配回款" : "待匹配回款";
  const status = expenseMatchStatus(row);
  if (status === "matched") return "已匹配发票";
  if (status === "partial") return "部分匹配发票";
  return "待匹配发票";
}

function matchClass(row: ReconTxn) {
  if (row.direction === "in") {
    return settlementMatchStatus(row) === "matched"
      ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
      : "bg-amber-50 text-amber-700 ring-amber-200";
  }
  const status = expenseMatchStatus(row);
  if (status === "matched") return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  if (status === "partial") return "bg-blue-50 text-blue-700 ring-blue-200";
  return "bg-amber-50 text-amber-700 ring-amber-200";
}

export default function BankTransactionsPage() {
  const runtime = useTabRuntime();
  const workspace = useWorkspace();
  const ownTab = runtime?.tabId ? workspace.tabs.find((tab) => tab.id === runtime.tabId) : null;
  const ownSearch = ownTab?.search || "";
  const bankView = useMemo<"summary" | "transactions">(() => {
    const params = new URLSearchParams(ownSearch);
    return params.get("view") === "summary" ? "summary" : "transactions";
  }, [ownSearch]);
  useTabTitle("银行");

  const [rows, setRows] = useState<ReconTxn[]>([]);
  const [direction, setDirection] = useTabScopedState<DirectionFilter>("bank.direction", "all");
  const [matchFilter, setMatchFilter] = useTabScopedState<MatchFilter>("bank.match", "all");
  const [timeScope, setTimeScope] = useTabScopedState<TimeScope>("bank.timeScope", "all");
  const [query, setQuery] = useTabScopedState("bank.search", "");
  const [period, setPeriod] = useTabScopedState("bank.period", previousMonth);
  const [accountFilter, setAccountFilter] = useTabScopedState("bank.account", "");
  const [manualAccountNo, setManualAccountNo] = useState("ZJRC-001");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [rawDetail, setRawDetail] = useState<BankRawDetail | null>(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [rawError, setRawError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const bankViewHref = (view: "summary" | "transactions") => {
    const params = new URLSearchParams({ view, period });
    if (view === "transactions" && accountFilter) params.set("account", accountFilter);
    return `/finance/bank-transactions?${params.toString()}`;
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [year, month] = period.split("-").map(Number);
      const lastDay = year && month ? new Date(year, month, 0).getDate() : 31;
      const startDate = year && month ? `${year}-${String(month).padStart(2, "0")}-01` : "";
      const endDate = year && month ? `${year}-${String(month).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}` : "";
      const pageSize = 500;
      const allRows: ReconTxn[] = [];
      for (let offset = 0; ; offset += pageSize) {
        const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
        if (timeScope === "period" && startDate && endDate) {
          params.set("start_date", startDate);
          params.set("end_date", endDate);
        }
        const response = await authenticatedFetch(`/api/v1/reconciliation/transactions?${params.toString()}`, { cache: "no-store" });
        const payload = (await response.json().catch(() => [])) as ReconTxn[] | { detail?: string };
        if (!response.ok) {
          throw new Error(!Array.isArray(payload) && payload.detail ? payload.detail : `加载失败（${response.status}）`);
        }
        const batch = Array.isArray(payload) ? payload : [];
        allRows.push(...batch);
        if (batch.length < pageSize) break;
      }
      setRows(allRows);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [period, timeScope]);

  useEffect(() => {
    if (bankView === "transactions") void load();
  }, [bankView, load]);

  useEffect(() => {
    const params = new URLSearchParams(ownSearch);
    const requestedPeriod = params.get("period") || "";
    if (/^\d{4}-\d{2}$/.test(requestedPeriod)) setPeriod(requestedPeriod);
    if (params.get("view") !== "summary") setAccountFilter(params.get("account") || "");
  }, [ownSearch, setAccountFilter, setPeriod]);

  const scopeRows = useMemo(
    () => timeScope === "period"
      ? rows.filter((row) => !period || row.txnDate.slice(0, 7) === period)
      : rows,
    [period, rows, timeScope],
  );

  const accountOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of scopeRows) {
      const key = row.accountNo || "";
      if (!key) continue;
      map.set(key, row.accountName || row.bankName || key);
    }
    return Array.from(map.entries());
  }, [scopeRows]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return scopeRows.filter((row) => {
      if (accountFilter && row.accountNo !== accountFilter) return false;
      if (direction !== "all" && row.direction !== direction) return false;
      const matched = row.direction === "in" ? settlementMatchStatus(row) === "matched" : expenseMatchStatus(row) === "matched";
      if (matchFilter === "matched" && !matched) return false;
      if (matchFilter === "unmatched" && matched) return false;
      if (!needle) return true;
      return [row.txnDate, row.counterpartyName, row.summary, row.serialNo, row.voucherNo, row.accountNo, row.accountName]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [accountFilter, direction, matchFilter, scopeRows, query]);

  const summary = useMemo(() => {
    return scopeRows.reduce(
      (result, row) => {
        const amount = Number(row.amount) || 0;
        result.count += 1;
        if (row.direction === "in") result.income += amount;
        else result.expense += amount;
        const matched = row.direction === "in" ? settlementMatchStatus(row) === "matched" : expenseMatchStatus(row) === "matched";
        if (!matched) result.pending += 1;
        return result;
      },
      { count: 0, income: 0, expense: 0, pending: 0 },
    );
  }, [scopeRows]);

  const directionCounts = useMemo(
    () => ({
      all: scopeRows.length,
      in: scopeRows.filter((row) => row.direction === "in").length,
      out: scopeRows.filter((row) => row.direction === "out").length,
    }),
    [scopeRows],
  );

  async function uploadBank(file: File) {
    const [year, month] = period.split("-").map(Number);
    if (!year || !month) {
      setError("请先选择流水所属账期");
      return;
    }
    setUploading(true);
    setError("");
    setMessage("");
    const form = new FormData();
    form.append("file", file);
    form.append("period_year", String(year));
    form.append("period_month", String(month));
    form.append("category", "bank");
    form.append("original_name", `银行交易明细${file.name.toLowerCase().endsWith(".xlsx") ? ".xlsx" : ""}`);
    if (manualAccountNo.trim()) form.append("account_no", manualAccountNo.trim());
    try {
      const response = await authenticatedFetch("/api/v1/finance/files", { method: "POST", body: form });
      const data = (await response.json().catch(() => ({}))) as {
        detail?: string;
        id?: number;
        version?: number;
        storedPath?: string;
        bankImport?: { created: number; duplicates: number; skipped: number; rawStored?: number; accountNo?: string; accountSource?: string };
        bankImportError?: string;
      };
      if (!response.ok) throw new Error(data.detail || `上传失败（${response.status}）`);
      if (data.bankImportError) {
        setError(`原件已归档 v${data.version ?? ""}（${data.storedPath ?? "原始文件库"}），流水解析失败：${data.bankImportError}`);
      } else if (data.bankImport) {
        const accountText = data.bankImport.accountNo ? `，真实账号 ${data.bankImport.accountNo}` : "";
        const rawText = data.bankImport.rawStored !== undefined ? `，已保存原始行 ${data.bankImport.rawStored} 笔` : "";
        setMessage(`银行交易明细已导入 v${data.version ?? ""}：新增 ${data.bankImport.created} 笔，重复核验 ${data.bankImport.duplicates} 笔，无法识别 ${data.bankImport.skipped} 笔${accountText}${rawText}。`);
      } else {
        setMessage(`银行交易明细已归档 v${data.version ?? ""}，但没有生成流水记录，请检查文件格式。`);
      }
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function openRawDetail(row: ReconTxn) {
    setRawLoading(true);
    setRawError("");
    try {
      const response = await authenticatedFetch(`/api/v1/reconciliation/transactions/${row.id}/raw`, { cache: "no-store" });
      const payload = (await response.json().catch(() => ({}))) as BankRawDetail | { detail?: string };
      if (!response.ok) throw new Error("detail" in payload && payload.detail ? payload.detail : `读取原始记录失败（${response.status}）`);
      setRawDetail(payload as BankRawDetail);
    } catch (caught) {
      setRawError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRawLoading(false);
    }
  }

  async function downloadRawFile(url: string, filename: string) {
    try {
      await downloadAuthenticatedFile(url, filename);
    } catch (caught) {
      setRawError(caught instanceof Error ? caught.message : "下载原始文件失败");
    }
  }

  const bankTabs = (
    <div className="mx-auto w-full max-w-[1600px] pb-3">
      <div className="inline-flex overflow-hidden rounded-lg border border-slate-200 bg-white p-0.5 text-xs shadow-sm" role="tablist" aria-label="银行模块视图">
        <Link href={bankViewHref("summary")} role="tab" aria-selected={bankView === "summary"} className={`rounded-md px-4 py-2 font-medium transition ${bankView === "summary" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
          账户汇总
        </Link>
        <Link href={bankViewHref("transactions")} role="tab" aria-selected={bankView === "transactions"} className={`rounded-md px-4 py-2 font-medium transition ${bankView === "transactions" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
          银行流水
        </Link>
      </div>
    </div>
  );

  if (bankView === "summary") {
    return (
      <>
        {bankTabs}
        <BankSummaryPanel period={period} onPeriodChange={setPeriod} />
      </>
    );
  }

  return (
    <>
      {bankTabs}
      <div className="mx-auto w-full max-w-[1600px] space-y-4 pb-8">
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-2 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="mb-1 text-[11px] font-medium tracking-wide text-blue-600">财务中心 / 银行</div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">银行流水</h1>
            <p className="mt-1 text-xs text-slate-500">这里只展示银行真实流水；收入可关联回款/平台结算，支出可关联进项发票。银行流水本身不叫“付款核对”。</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept=".xlsx" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadBank(file); }} />
            <input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} aria-label="银行流水账期" title="用于当前账期筛选和上传流水归档账期" className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 outline-none focus:border-blue-400" />
            <input value={manualAccountNo} onChange={(event) => setManualAccountNo(event.target.value)} aria-label="无账号文件时填写内部编号或真实银行账号" placeholder="内部编号或真实银行账号" className="w-44 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 outline-none placeholder:text-slate-400 focus:border-blue-400" />
            <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} className="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white shadow-sm hover:bg-blue-700 disabled:cursor-wait disabled:opacity-50">{uploading ? "导入中…" : "上传银行流水"}</button>
            <button type="button" onClick={() => void load()} disabled={loading} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">刷新</button>
          </div>
        </div>
      </header>

      {message && <div role="status" className="rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700">{message}</div>}
      {error && <div role="alert" className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {[
          ["流水笔数", summary.count.toLocaleString("zh-CN"), "已导入全部流水"],
          ["收入合计", money(summary.income), "用于回款对账"],
          ["支出合计", money(summary.expense), "用于进项发票匹配"],
          ["待匹配", summary.pending.toLocaleString("zh-CN"), "需要继续处理"],
        ].map(([label, value, hint]) => (
          <div key={label} className={`${CARD} px-4 py-3`}>
            <div className="text-xs text-slate-400">{label}</div>
            <div className="mt-1 text-xl font-semibold tracking-tight text-slate-900">{value}</div>
            <div className="mt-1 text-[11px] text-slate-400">{hint}</div>
          </div>
        ))}
      </section>

      <section className={`${CARD} overflow-hidden`}>
        <div className="sticky top-0 z-20 flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
          <div className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
            {([ ["all", "全部时间"], ["period", "当前账期"] ] as const).map(([value, label]) => (
              <button key={value} type="button" onClick={() => setTimeScope(value)} className={`rounded-md px-3 py-1.5 text-xs transition ${timeScope === value ? "bg-slate-900 font-medium text-white" : "text-slate-500 hover:bg-slate-50"}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
            {([ ["all", "全部收支"], ["in", "收入"], ["out", "支出"] ] as const).map(([value, label]) => (
              <button key={value} type="button" onClick={() => setDirection(value)} className={`rounded-md px-3 py-1.5 text-xs transition ${direction === value ? "bg-blue-600 font-medium text-white" : "text-slate-500 hover:bg-slate-50"}`}>
                {label} <span className={direction === value ? "text-blue-100" : "text-slate-400"}>{directionCounts[value]}</span>
              </button>
            ))}
          </div>
          <div className="relative min-w-[220px] flex-1">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">⌕</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={timeScope === "all" ? "搜索对方户名 / 摘要 / 流水号 / 账户（全部时间）" : "搜索当前账期的对方户名 / 摘要 / 流水号 / 账户"} className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-3 text-xs outline-none focus:border-blue-400" />
          </div>
          <select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none">
            <option value="">全部账户</option>
            {accountOptions.map(([accountNo, label]) => <option key={accountNo} value={accountNo}>{label} · {accountNo}</option>)}
          </select>
          <select value={matchFilter} onChange={(event) => setMatchFilter(event.target.value as MatchFilter)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none">
            <option value="all">全部关联状态</option>
            <option value="matched">已匹配</option>
            <option value="unmatched">待匹配（含部分匹配）</option>
          </select>
          <Link href={`/finance/monthly-send?tab=match&month=${encodeURIComponent(period)}`} className="ml-auto rounded-lg border border-blue-200 px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50">发起对账</Link>
        </div>

        <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/50 px-4 py-2 text-[11px] text-slate-400">
          <span>{loading ? "正在读取银行流水…" : `当前显示 ${visibleRows.length} / ${scopeRows.length} 笔 · ${timeScope === "all" ? "全部时间" : period}`}</span>
          <span>原始文件与每笔原始行均保留；重复导入只补齐来源，不覆盖原记录</span>
        </div>

        {loading ? <div className="px-4 py-16 text-center text-sm text-slate-400">加载中…</div> : !visibleRows.length ? (
          <div className="px-4 py-16 text-center">
            <div className="text-sm font-medium text-slate-600">{rows.length ? "没有符合条件的流水" : "暂无银行流水"}</div>
            <div className="mt-2 text-xs text-slate-400">{rows.length ? "可以调整搜索或筛选条件" : "请选择账期并上传浙江农信交易明细 XLSX 文件"}</div>
            {!rows.length && <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} className="mt-4 rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">上传银行流水</button>}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1160px] text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">交易日期</th>
                  <th className="px-4 py-3 font-medium">方向</th>
                  <th className="px-4 py-3 text-right font-medium">金额</th>
                  <th className="px-4 py-3 font-medium">对方户名</th>
                  <th className="px-4 py-3 font-medium">摘要</th>
                  <th className="px-4 py-3 font-medium">银行流水号</th>
                  <th className="px-4 py-3 font-medium">凭证号码</th>
                  <th className="px-4 py-3 font-medium">账户</th>
                  <th className="px-4 py-3 font-medium">业务关联状态</th>
                  <th className="px-4 py-3 font-medium">原始记录</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {visibleRows.map((row) => (
                  <tr key={row.id} className="hover:bg-blue-50/30">
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-600">{dateText(row.txnDate)}</td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded-full px-2 py-1 text-[11px] font-medium ring-1 ring-inset ${row.direction === "in" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-violet-50 text-violet-700 ring-violet-200"}`}>{row.direction === "in" ? "收入" : "支出"}</span></td>
                    <td className={`whitespace-nowrap px-4 py-3 text-right font-medium ${row.direction === "in" ? "text-emerald-700" : "text-slate-800"}`}>{row.direction === "in" ? "+" : "−"}{money(row.amount)}</td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-slate-800">{row.counterpartyName || "—"}</td>
                    <td className="max-w-[240px] truncate px-4 py-3 text-xs text-slate-500">{row.summary || "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">{row.serialNo || "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{row.voucherNo || "—"}</td>
                    <td className="px-4 py-3 text-xs text-slate-500"><div>{row.accountName || "浙江农信"}</div><div className="mt-0.5 font-mono text-[10px] text-slate-400">{row.accountNo || "未绑定账号"}{row.accountCode ? ` · ${row.accountCode}` : ""}</div></td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded-full px-2 py-1 text-[11px] font-medium ring-1 ring-inset ${matchClass(row)}`}>{matchText(row)}</span></td>
                    <td className="px-4 py-3">
                      <button type="button" onClick={() => void openRawDetail(row)} disabled={!row.rawAvailable || rawLoading} className="whitespace-nowrap rounded-md border border-blue-200 px-2.5 py-1.5 text-[11px] font-medium text-blue-600 hover:bg-blue-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400">
                        {row.rawAvailable ? "查看原始记录" : "无原始记录"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </div>
      {rawError && <div className="fixed bottom-5 right-5 z-50 rounded-lg border border-red-200 bg-white px-4 py-3 text-xs text-red-600 shadow-lg">{rawError}</div>}
      {rawDetail && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 p-4" role="dialog" aria-modal="true" aria-label="银行流水原始记录">
          <div className="max-h-[88vh] w-full max-w-4xl overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h2 className="text-base font-semibold text-slate-900">原始银行记录</h2>
                <p className="mt-1 text-xs text-slate-500">第 {rawDetail.sourceRowNumber ?? rawDetail.raw.rowNumber ?? "—"} 行 · {rawDetail.accountNo || "未识别账号"}{rawDetail.accountCode ? ` · ${rawDetail.accountCode}` : ""} · 流水号 {rawDetail.serialNo || "—"}</p>
              </div>
              <button type="button" onClick={() => setRawDetail(null)} className="rounded-md px-2 py-1 text-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="关闭">×</button>
            </div>
            <div className="max-h-[calc(88vh-80px)] overflow-y-auto p-5">
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] text-slate-400">交易日期</div><div className="mt-1 text-sm text-slate-700">{rawDetail.txnDate}</div></div>
                <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] text-slate-400">交易时间</div><div className="mt-1 text-sm text-slate-700">{rawDetail.transactionTime || "—"}</div></div>
                <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] text-slate-400">银行流水号</div><div className="mt-1 font-mono text-sm text-slate-700">{rawDetail.serialNo || "—"}</div></div>
                <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[11px] text-slate-400">凭证号码</div><div className="mt-1 font-mono text-sm text-slate-700">{rawDetail.voucherNo || "—"}</div></div>
              </div>
              <div className="mb-4 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-400">
                <span>来源文件：{rawDetail.sourceFile?.fileName || "历史记录/未关联"}</span>
                {rawDetail.sourceFile?.downloadUrl && <button type="button" onClick={() => void downloadRawFile(rawDetail.sourceFile!.downloadUrl!, rawDetail.sourceFile!.fileName)} className="font-medium text-blue-600 hover:text-blue-700">下载原始文件</button>}
                {rawDetail.sourceFile && <span>SHA256：{rawDetail.sourceFile.sha256}</span>}
                <span>历史来源：{rawDetail.raw.sourceHistory?.length ?? 0} 个</span>
              </div>
              <div className="overflow-hidden rounded-xl border border-slate-200">
                <div className="border-b border-slate-200 bg-slate-50 px-4 py-2 text-xs font-medium text-slate-600">上传文件中的完整原始行</div>
                <div className="divide-y divide-slate-100">
                  {Object.entries(rawDetail.raw.fields || {}).map(([label, value]) => (
                    <div key={label} className="grid grid-cols-[minmax(140px,28%)_1fr] gap-3 px-4 py-2.5 text-xs">
                      <div className="font-medium text-slate-500">{label}</div>
                      <div className="break-all text-slate-800">{rawValue(value)}</div>
                    </div>
                  ))}
                  {!Object.keys(rawDetail.raw.fields || {}).length && <pre className="overflow-x-auto p-4 text-xs text-slate-600">{JSON.stringify(rawDetail.raw, null, 2)}</pre>}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
