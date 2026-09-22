"use client";

import { useEffect, useState } from "react";
import { openingApi, type OpeningData } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  platform_receivable: "平台期初待回款",
  bank: "银行期初余额",
  sku_inventory: "SKU 期初库存",
  sku_cost: "SKU 期初成本",
  deposit: "保证金",
  frozen: "冻结款",
  other: "其他",
};

export default function FinanceOpeningPage() {
  const [opening, setOpening] = useState<OpeningData | null>(null);
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState("platform_receivable");
  const [ref, setRef] = useState("");
  const [amount, setAmount] = useState("");
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const [date, setDate] = useState("");

  async function load() {
    setOpening(await openingApi.list());
  }

  useEffect(() => {
    void load().catch(() => {});
  }, []);

  async function save() {
    const body: Record<string, unknown> = { kind, ref, note };
    if (amount) body.amount = amount;
    if (qty) body.quantity = qty;
    if (date) body.as_of_date = date;
    try {
      await openingApi.upsert(body);
      setMessage("期初数据已保存并写入审计日志");
      setRef("");
      setAmount("");
      setQty("");
      setNote("");
      setDate("");
      await load();
    } catch (caught) {
      setMessage(`保存失败：${caught instanceof Error ? caught.message : String(caught)}`);
    }
    window.setTimeout(() => setMessage(""), 3500);
  }

  return (
    <div>
      <header className="app-page-header -mx-1 pb-3">
        <h1 className="text-xl font-semibold text-slate-900">期初数据</h1>
        <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
          仅用于系统启用时录入历史期初余额、库存与成本。后续日常发生额应由销售、采购、库存和银行流水自动形成，不在这里重复维护。
        </p>
      </header>

      <section className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800">
        期初允许不平：期初 + 本期发生 − 本期结算 = 期末。历史差异进入差异池，不回写或篡改历史采购、销售订单。
      </section>

      <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
        <div className="text-sm font-semibold text-slate-900">新增 / 调整期初</div>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs text-slate-500">
            类别
            <select value={kind} onChange={(e) => setKind(e.target.value)} className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm">
              {Object.entries(KIND_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-500">
            对象
            <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="平台 / 账户 / SKU" className="mt-1 block w-44 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </label>
          <label className="text-xs text-slate-500">
            金额
            <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="金额" className="mt-1 block w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </label>
          <label className="text-xs text-slate-500">
            数量
            <input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="库存数量" className="mt-1 block w-28 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </label>
          <label className="text-xs text-slate-500">
            截止日期
            <input value={date} onChange={(e) => setDate(e.target.value)} type="date" className="mt-1 block rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </label>
          <label className="min-w-[180px] flex-1 text-xs text-slate-500">
            备注
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="备注" className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          </label>
          <button onClick={() => void save()} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">
            保存期初
          </button>
        </div>
        {message && <div className="mt-3 text-xs text-slate-600">{message}</div>}
      </section>

      <section className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-slate-900">期初明细</div>
            <div className="mt-0.5 text-[11px] text-slate-400">所有修改保留审计记录。</div>
          </div>
          {opening && (
            <div className="flex flex-wrap gap-2 text-[11px] text-slate-500">
              <span className="rounded-full bg-slate-100 px-2 py-1">差异池 ¥{opening.summary.differencePool}</span>
              <span className="rounded-full bg-slate-100 px-2 py-1">调整 {opening.summary.adjustmentCount} 次</span>
              <span className="rounded-full bg-slate-100 px-2 py-1">已有成本 SKU {opening.summary.skuWithCost}</span>
            </div>
          )}
        </div>

        {!opening || opening.items.length === 0 ? (
          <div className="py-12 text-center text-sm text-slate-400">暂无期初数据</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr>
                  <th className="px-4 py-2.5 font-medium">类别</th>
                  <th className="px-4 py-2.5 font-medium">对象</th>
                  <th className="px-4 py-2.5 font-medium">金额</th>
                  <th className="px-4 py-2.5 font-medium">数量</th>
                  <th className="px-4 py-2.5 font-medium">备注</th>
                </tr>
              </thead>
              <tbody>
                {opening.items.map((row) => (
                  <tr key={row.id} className="border-t border-slate-100">
                    <td className="px-4 py-2.5">{KIND_LABEL[row.kind] ?? row.kind}</td>
                    <td className="px-4 py-2.5 text-slate-600">{row.ref || "—"}</td>
                    <td className="px-4 py-2.5 tabular-nums">{row.amount !== null ? `¥${row.amount}` : "—"}</td>
                    <td className="px-4 py-2.5 tabular-nums">{row.quantity ?? "—"}</td>
                    <td className="px-4 py-2.5 text-slate-400">{row.note}</td>
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
