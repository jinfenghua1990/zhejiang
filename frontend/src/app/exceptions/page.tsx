"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ExceptionRow, getExceptions, updateExceptionStatus } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  pending: "待处理",
  confirmed: "已确认",
  ignored: "已忽略",
  resolved: "已解决",
};

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-amber-50 text-amber-700 ring-amber-200",
  confirmed: "bg-sky-50 text-sky-700 ring-sky-200",
  ignored: "bg-slate-100 text-slate-500 ring-slate-200",
  resolved: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

const SEVERITY: Record<string, string> = { high: "高", medium: "中", low: "低" };

export default function ExceptionsPage() {
  const [rows, setRows] = useState<ExceptionRow[]>([]);
  const [filter, setFilter] = useState<string>("pending");
  const [err, setErr] = useState("");
  const [message, setMessage] = useState("");
  const router = useRouter();

  const load = useCallback(() => {
    getExceptions()
      .then((r) => setRows(filter === "all" ? r : r.filter((x) => x.status === filter)))
      .catch((e) => setErr(String(e)));
  }, [filter]);

  useEffect(load, [load]);

  async function act(id: number, status: string, code: string) {
    setErr("");
    try {
      await updateExceptionStatus(id, status, "");
      setMessage(
        code === "PURCHASE_PAYMENT_GAP" && status === "confirmed"
          ? "金额差异已确认，采购内容已完成，订单已进入待采购单流程。"
          : "异常状态已更新。",
      );
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <div>
      <header className="app-page-header -mx-1 flex items-center justify-between gap-3 bg-[#f4f7fb]/95 pb-3 backdrop-blur">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">异常中心</h1>
          <p className="mt-1 text-sm text-slate-500">集中处理采购、库存、财务和数据同步产生的异常，不再分散到各业务页重复展示。</p>
        </div>
        <div className="flex gap-1.5">
          {["pending", "confirmed", "resolved", "ignored", "all"].map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`rounded-lg px-2.5 py-1 text-xs ${
                filter === s ? "bg-indigo-600 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200"
              }`}
            >
              {s === "all" ? "全部" : STATUS_LABEL[s]}
            </button>
          ))}
        </div>
      </header>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {message && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}

      <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
        {rows.length === 0 ? (
          <div className="p-8 text-center text-sm text-slate-400">当前筛选下没有异常记录</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2.5 font-medium">级别</th>
                <th className="px-4 py-2.5 font-medium">异常</th>
                <th className="px-4 py-2.5 font-medium">状态</th>
                <th className="px-4 py-2.5 font-medium">时间</th>
                <th className="px-4 py-2.5 font-medium">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="px-4 py-2.5">
                    <span className={r.severity === "high" ? "text-red-600" : "text-slate-500"}>
                      {SEVERITY[r.severity] ?? r.severity}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="font-medium">{r.title}</div>
                    {Boolean(r.detail?.latest) && (
                      <div className="mt-0.5 max-w-xl truncate text-xs text-slate-400" title={String(r.detail.latest)}>
                        {String(r.detail.latest)}
                      </div>
                    )}
                    {r.code === "PURCHASE_PAYMENT_GAP" && Boolean(r.detail?.externalOrderId) && (
                      <div className="mt-0.5 text-xs text-amber-700">
                        订单号 {String(r.detail.externalOrderId)} · 1688 实付 ¥{String(r.detail.paidAmount ?? "—")} · 入库 ¥{String(r.detail.inboundAmount ?? "—")} · 差额 ¥{String(r.detail.difference ?? "—")}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`inline-flex rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${
                        STATUS_STYLE[r.status] ?? STATUS_STYLE.ignored
                      }`}
                    >
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-400">
                    {r.createdAt ? new Date(r.createdAt).toLocaleString("zh-CN") : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="flex gap-1.5 text-xs">
                      {(() => {
                        const poId = r.detail?.poId ?? r.detail?.orderId;
                        if (!poId) return null;
                        return (
                          <button onClick={() => router.push(`/purchase/workbench?view=orders&order=${poId}`)} className="rounded bg-indigo-600 px-2 py-0.5 text-white hover:bg-indigo-700">
                            去处理
                          </button>
                        );
                      })()}
                      {r.status !== "resolved" && (
                        <button onClick={() => act(r.id, "resolved", r.code)} className="text-emerald-600 hover:underline">
                          解决
                        </button>
                      )}
                      {r.status === "pending" && (
                        <button onClick={() => act(r.id, "confirmed", r.code)} className="text-sky-600 hover:underline">
                          {r.code === "PURCHASE_PAYMENT_GAP" ? "确认金额并继续" : "确认"}
                        </button>
                      )}
                      {r.status !== "ignored" && (
                        <button onClick={() => act(r.id, "ignored", r.code)} className="text-slate-400 hover:underline">
                          忽略
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
