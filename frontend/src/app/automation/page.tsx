"use client";

import { useCallback, useEffect, useState } from "react";
import { automationApi, jkyOrderApi, JkyOrderStatus, ScheduleItem, SyncJobRow, SyncLogRow } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-indigo-50 text-indigo-700",
  success: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
  skipped: "bg-slate-100 text-slate-500",
  pending: "bg-amber-50 text-amber-700",
  unknown: "bg-slate-100 text-slate-500",
};

const JOB_TYPE_LABEL: Record<string, string> = {
  sales: "订单/售后", online_orders: "线上订单", aftersales: "售后", inventory: "库存",
  products: "商品/SKU", price_lists: "价格", warehouses: "仓库",
  purchase: "采购", purchase_settlements: "采购结算", purchase_returns: "采购退货",
  inbound: "入库", outbound: "出库", stock_allocations: "库存调拨", shop_orders: "网店订单/发货",
  connection_test: "连接测试", orders: "吉客云销售订单",
};

export default function AutomationPage() {
  const [schedule, setSchedule] = useState<ScheduleItem[]>([]);
  const [jobs, setJobs] = useState<SyncJobRow[]>([]);
  const [logs, setLogs] = useState<SyncLogRow[]>([]);
  const [err, setErr] = useState("");
  const [message, setMessage] = useState("");
  const [running, setRunning] = useState("");
  const [jkyOrderStatus, setJkyOrderStatus] = useState<JkyOrderStatus | null>(null);

  const load = useCallback(() => {
    Promise.all([automationApi.schedule(), automationApi.jobs(30), automationApi.logs(80), jkyOrderApi.status()])
      .then(([s, j, l, orderStatus]) => {
        setSchedule(s.items);
        setJobs(j);
        setLogs(l);
        setJkyOrderStatus(orderStatus);
      })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  async function runJkyOrders() {
    const runKey = "tasks.sync_jky_orders-";
    setRunning(runKey);
    setMessage("");
    try {
      await automationApi.runJkyOrders();
      setMessage("吉客云订单三通道同步已加入队列；结果以最近同步任务为准");
      window.setTimeout(load, 1200);
    } catch (e) {
      setMessage(`加入队列失败：${String(e)}`);
    } finally {
      setRunning("");
    }
  }

  async function runNow(item: ScheduleItem) {
    if (item.task === "tasks.sync_jky_orders") {
      await runJkyOrders();
      return;
    }
    setRunning(`${item.task}-${item.args}`);
    setMessage("");
    try {
      if (item.task === "tasks.sync_jackyun") {
        await automationApi.runJackyun(item.args);
      } else if (item.task === "tasks.sync_1688") {
        await automationApi.run1688();
      } else {
        return;
      }
      setMessage(`${item.label} 已加入队列；结果以最近同步任务为准`);
      window.setTimeout(load, 1200);
    } catch (e) {
      setMessage(`加入队列失败：${String(e)}`);
    } finally {
      setRunning("");
    }
  }

  return (
    <div>
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-3 backdrop-blur">
        <h1 className="text-xl font-semibold text-slate-900">自动化任务</h1>
        <p className="mt-1 text-sm text-slate-400">
          统一查看定时同步、手工触发和运行日志。只有已配置凭证的数据源才会真正执行；未配置任务会如实跳过。
        </p>
      </header>


      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {message && <div className="mt-4 rounded-lg bg-indigo-50 p-3 text-sm text-indigo-700">{message}</div>}

      <section className="mt-5 rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-slate-700">吉客云订单获取中心</h2>
            <p className="mt-1 text-xs text-slate-400">
              优先级：{jkyOrderStatus?.providerPriority.join(" → ") || "jky_web → jky_rpa → jky_api"}；只推进成功且通过校验的同步游标。
            </p>
          </div>
          <button
            onClick={() => void runJkyOrders()}
            disabled={Boolean(running)}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
          >
            立即同步订单
          </button>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {(jkyOrderStatus?.channels ?? []).map((channel) => (
            <div key={channel.provider} className="rounded-lg border border-slate-100 bg-slate-50 p-2">
              <div className="text-xs font-medium text-slate-700">{channel.label}</div>
              <div className={`mt-1 text-xs ${channel.status === "connected" ? "text-emerald-600" : channel.configured ? "text-amber-600" : "text-slate-400"}`}>
                {channel.status}{channel.verified ? " · 已验证" : " · 未验证"}
              </div>
              {channel.errorSummary && <div className="mt-1 truncate text-[10px] text-slate-400" title={channel.errorSummary}>{channel.errorSummary}</div>}
            </div>
          ))}
        </div>
      </section>

      <section className="mt-5 rounded-xl border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-medium text-slate-700">定时任务（beat schedule）</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-2 font-medium">任务</th>
              <th className="py-2 font-medium">频率</th>
              <th className="py-2 font-medium">执行</th>
              <th className="py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {schedule.map((s) => (
              <tr key={`${s.task}-${s.args}`}>
                <td className="py-2 font-medium">{s.label}</td>
                <td className="py-2 text-slate-500">{s.frequency}</td>
                <td className="py-2 font-mono text-xs text-slate-400">{s.task}({s.args || ""})</td>
                <td className="py-2 text-right">
                  {(s.task === "tasks.sync_jky_orders" || s.task === "tasks.sync_jackyun" || s.task === "tasks.sync_1688") ? (
                    <button
                      onClick={() => runNow(s)}
                      disabled={Boolean(running)}
                      className="rounded-lg border border-indigo-200 px-2.5 py-1 text-xs text-indigo-600 disabled:opacity-40"
                    >
                      {running === `${s.task}-${s.args}` ? "排队中…" : "立即同步"}
                    </button>
                  ) : <span className="text-xs text-slate-300">自动</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-700">最近同步任务</h2>
          {jobs.length === 0 ? (
            <div className="mt-3 py-6 text-center text-sm text-slate-400">暂无任务记录（beat 触发后出现）</div>
          ) : (
            <table className="mt-2 w-full text-sm">
              <tbody className="divide-y divide-slate-100">
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="py-2">
                      <span className="font-mono text-xs">{JOB_TYPE_LABEL[j.jobType] ?? j.jobType}</span>
                      <span className="ml-1 text-[10px] text-slate-400">({j.provider})</span>
                    </td>
                    <td className="py-2">
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[j.status] ?? STATUS_STYLE.unknown}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="py-2 text-right text-xs text-slate-400">
                      {j.startedAt ? new Date(j.startedAt).toLocaleString("zh-CN") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <h2 className="text-sm font-medium text-slate-700">同步日志</h2>
          {logs.length === 0 ? (
            <div className="mt-3 py-6 text-center text-sm text-slate-400">暂无日志</div>
          ) : (
            <div className="mt-2 max-h-96 space-y-1.5 overflow-auto text-xs">
              {logs.map((l) => (
                <div key={l.id} className="flex gap-2">
                  <span className={`shrink-0 ${l.level === "error" ? "text-red-500" : l.level === "warn" ? "text-amber-500" : "text-slate-400"}`}>
                    [{l.level}]
                  </span>
                  <span className="flex-1 break-all text-slate-600">{l.message}</span>
                  <span className="shrink-0 text-slate-300">#{l.id}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
