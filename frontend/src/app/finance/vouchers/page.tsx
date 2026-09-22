"use client";

import Link from "@/components/workspace/workspace-link";
import { useEffect, useState, Fragment } from "react";
import {
  financeEntityApi,
  financeVoucherApi,
  type FinanceLegalEntity,
  type FinanceVoucher,
} from "@/lib/api";
import { useTabTitle } from "@/lib/workspace/tab-store";

function currentPeriod() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function parsePeriod(value: string) {
  const [year, month] = value.split("-").map(Number);
  return { year, month };
}

function money(value: number, currency: string) {
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: currency || "CNY",
      maximumFractionDigits: 2,
    }).format(Number(value || 0));
  } catch {
    return `${currency || "CNY"} ${Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
  }
}

function dateText(value: string | null) {
  if (!value) return "—";
  return value.replace("T", " ").slice(0, 16);
}

function statusLabel(status: string) {
  return status === "posted" ? "已过账" : status === "draft" ? "草稿" : status || "未知";
}

export default function FinanceVouchersPage() {
  useTabTitle("自动记账凭证");
  const [entities, setEntities] = useState<FinanceLegalEntity[]>([]);
  const [entityId, setEntityId] = useState<number | null>(null);
  const [period, setPeriod] = useState(currentPeriod());
  const [vouchers, setVouchers] = useState<FinanceVoucher[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"generate" | number | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const periodParts = parsePeriod(period);

  async function loadEntities() {
    const result = await financeEntityApi.list();
    setEntities(result.items);
    setEntityId((current) => current ?? result.items.find((item) => item.isDefault)?.id ?? result.items[0]?.id ?? null);
  }

  async function loadVouchers() {
    if (!entityId || !periodParts.year || !periodParts.month) {
      setVouchers([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setVouchers(await financeVoucherApi.list({ legalEntityId: entityId, ...periodParts }));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "凭证加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadEntities().catch((caught) => setError(caught instanceof Error ? caught.message : "公司主体加载失败"));
  }, []);

  useEffect(() => {
    void loadVouchers();
  }, [entityId, period]);

  async function generate() {
    if (!entityId || !periodParts.year || !periodParts.month) return;
    setBusy("generate");
    setError("");
    setMessage("");
    try {
      const result = await financeVoucherApi.generate({ legalEntityId: entityId, ...periodParts });
      setMessage(`已生成 ${result.generated} 张凭证，跳过 ${result.skipped} 条事项。预计事项和已过账事项不会重复入账。`);
      await loadVouchers();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "自动生成凭证失败");
    } finally {
      setBusy(null);
    }
  }

  async function post(voucher: FinanceVoucher) {
    if (voucher.status === "posted") return;
    setBusy(voucher.id);
    setError("");
    setMessage("");
    try {
      await financeVoucherApi.post(voucher.id);
      setMessage(`${voucher.voucherNo} 已过账。`);
      await loadVouchers();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "凭证过账失败");
    } finally {
      setBusy(null);
    }
  }

  const draftCount = vouchers.filter((voucher) => voucher.status === "draft").length;
  const postedCount = vouchers.filter((voucher) => voucher.status === "posted").length;
  const unbalancedCount = vouchers.filter((voucher) => !voucher.balanced).length;

  return (
    <div>
      <header className="app-page-header -mx-1 flex flex-wrap items-start justify-between gap-3 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-slate-900">自动记账凭证</h1>
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-700">财务底座</span>
          </div>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
            按统一财务事项池生成可核对的借贷凭证。预计事项只用于预测，已过账事项不会在重新生成时重复入账。
          </p>
        </div>
        <Link href="/finance" className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">返回财务中心</Link>
      </header>

      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-slate-500">
            公司主体
            <select value={entityId ?? ""} onChange={(event) => setEntityId(Number(event.target.value) || null)} className="mt-1 block min-w-[240px] rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700">
              <option value="">请选择公司主体</option>
              {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name} · {entity.code}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-500">
            账期
            <input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700" />
          </label>
          <button type="button" onClick={() => void loadVouchers()} disabled={loading || !entityId} className="rounded-lg border border-slate-200 px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40">刷新</button>
          <button type="button" onClick={() => void generate()} disabled={busy !== null || !entityId} className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-40">{busy === "generate" ? "正在生成…" : "生成本期凭证"}</button>
        </div>
        <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
          生成动作会重建本期仍为草稿的自动凭证；已过账凭证及其对应事项保留。生成前建议先确认发票、付款和采购事项已完成核对。
        </div>
        {message && <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{message}</div>}
        {error && <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}
      </section>

      <section className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Summary label="本期凭证" value={vouchers.length} />
        <Summary label="草稿" value={draftCount} />
        <Summary label="已过账" value={postedCount} />
        <Summary label="不平衡" value={unbalancedCount} danger={unbalancedCount > 0} />
      </section>

      <section className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">凭证列表</h2>
            <p className="mt-0.5 text-[11px] text-slate-400">金额按凭证币种展示，借贷不平衡的凭证禁止过账。</p>
          </div>
          <span className="text-[11px] text-slate-400">{period || "未选择账期"}</span>
        </div>

        {loading ? (
          <div className="py-14 text-center text-sm text-slate-400">正在加载凭证…</div>
        ) : vouchers.length === 0 ? (
          <div className="px-4 py-14 text-center">
            <div className="text-sm font-medium text-slate-700">本期还没有自动凭证</div>
            <p className="mt-1 text-xs text-slate-400">确认财务事项池已有实际事项后，点击“生成本期凭证”。</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[880px] text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium">凭证号</th>
                  <th className="px-4 py-3 font-medium">日期</th>
                  <th className="px-4 py-3 font-medium">币种</th>
                  <th className="px-4 py-3 text-right font-medium">借方</th>
                  <th className="px-4 py-3 text-right font-medium">贷方</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {vouchers.map((voucher) => (
                  <Fragment key={voucher.id}>
                    <tr className="border-t border-slate-100">
                      <td className="px-4 py-3 font-mono font-medium text-slate-700">{voucher.voucherNo}</td>
                      <td className="px-4 py-3 text-slate-600">{dateText(voucher.voucherDate)}</td>
                      <td className="px-4 py-3"><span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600">{voucher.currency || "CNY"}</span></td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700">{money(voucher.debitTotal, voucher.currency)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-slate-700">{money(voucher.creditTotal, voucher.currency)}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${voucher.status === "posted" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{statusLabel(voucher.status)}</span>
                        {!voucher.balanced && <span className="ml-1 rounded-full bg-rose-50 px-2 py-0.5 text-[10px] font-medium text-rose-700">不平</span>}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button type="button" onClick={() => setExpandedId((current) => current === voucher.id ? null : voucher.id)} className="mr-2 text-indigo-600 hover:underline">{expandedId === voucher.id ? "收起" : "明细"}</button>
                        {voucher.status !== "posted" && <button type="button" onClick={() => void post(voucher)} disabled={busy !== null || !voucher.balanced} className="rounded border border-emerald-200 px-2 py-1 text-[10px] text-emerald-700 hover:bg-emerald-50 disabled:opacity-40">{busy === voucher.id ? "处理中…" : "过账"}</button>}
                      </td>
                    </tr>
                    {expandedId === voucher.id && (
                      <tr className="border-t border-slate-100 bg-slate-50/60">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="mb-2 text-[11px] text-slate-500">{voucher.note || "自动生成"}</div>
                          <table className="w-full text-[11px]">
                            <thead className="text-slate-400"><tr><th className="pb-2 text-left font-medium">序号</th><th className="pb-2 text-left font-medium">科目</th><th className="pb-2 text-left font-medium">摘要</th><th className="pb-2 text-right font-medium">借贷</th><th className="pb-2 text-right font-medium">金额</th></tr></thead>
                            <tbody>{voucher.lines.map((line) => <tr key={`${voucher.id}-${line.seq}`} className="border-t border-slate-200/70"><td className="py-2 text-slate-400">{line.seq}</td><td className="py-2 text-slate-700">{line.accountCode} · {line.accountName}</td><td className="py-2 text-slate-500">{line.summary || "—"}</td><td className={`py-2 text-right ${line.direction === "debit" ? "text-indigo-700" : "text-emerald-700"}`}>{line.direction === "debit" ? "借" : "贷"}</td><td className="py-2 text-right tabular-nums text-slate-700">{money(line.amount, voucher.currency)}</td></tr>)}</tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function Summary({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return <div className="rounded-xl border border-slate-200 bg-white px-4 py-3"><div className="text-[11px] text-slate-400">{label}</div><div className={`mt-1 text-xl font-semibold ${danger ? "text-rose-600" : "text-slate-900"}`}>{value}</div></div>;
}
