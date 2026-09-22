"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import type { DetailInitial } from "./detail-view";
import SalesAnalyticsView from "./analytics-view";
import { useTabRuntime, useTabScopedState, useWorkspace } from "@/lib/workspace/tab-store";

const SalesDetailView = dynamic(() => import("./detail-view"), {
  ssr: false,
  loading: () => <div className="p-10 text-center text-[12px] text-slate-400">正在加载销售明细…</div>,
});

export default function SalesPage() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("q")?.trim() ?? "";
  const runtime = useTabRuntime();
  const ws = useWorkspace();
  const tabId = runtime?.tabId;
  const [tab, setTab] = useTabScopedState<"overview" | "detail">("sales.tab", "overview");
  // 穿透参数 + 自增 key：每次穿透强制重挂明细组件，保证初始筛选生效
  const [drill, setDrill] = useTabScopedState<DetailInitial | undefined>("sales.drill", undefined);
  const [drillSeq, setDrillSeq] = useState(0);

  /**
   * 概览穿透到明细：除了重挂明细组件，还要把钻取条件写进明细页的 Tab 快照，
   * 否则恢复现场时旧快照会盖掉这一次的钻取筛选。
   */
  const drillInto = useCallback((init: DetailInitial) => {
    if (tabId) {
      ws.setScope(tabId, "sales.detail.q", init.q ?? "");
      ws.setScope(tabId, "sales.detail.platform", init.platform ?? "");
      ws.setScope(tabId, "sales.detail.sku", init.sku ?? "");
      ws.setScope(tabId, "sales.detail.startDate", init.start ?? "");
      ws.setScope(tabId, "sales.detail.endDate", init.end ?? "");
      ws.setScope(tabId, "sales.detail.page", 1);
    }
    setDrill(init);
    setDrillSeq((n) => n + 1);
    setTab("detail");
  }, [setDrill, setTab, tabId, ws]);

  useEffect(() => {
    if (!initialQuery) return;
    drillInto({ q: initialQuery });
  }, [initialQuery, drillInto]);

  // 左侧二级菜单深链：/sales?tab=overview | detail 直接切换页签
  const tabParam = searchParams.get("tab");
  useEffect(() => {
    if (tabParam === "overview" || tabParam === "detail") setTab(tabParam);
  }, [tabParam]);

  function handleDrill(init: DetailInitial) {
    drillInto(init);
  }

  return (
    <div>
      <header className="app-page-header -mx-6 -mt-5 border-b border-slate-200 bg-[#f4f7fb]/95 px-6 pb-3 pt-4 backdrop-blur xl:-mx-8 xl:-mt-6 xl:px-8">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-indigo-500">SALES / ANALYTICS</div>
            <h1 className="mt-1 text-xl font-bold tracking-tight text-slate-900">{tab === "overview" ? "销售渠道分析（含利润成本）" : "销售明细"}</h1>
            <p className="mt-1 text-[11px] text-slate-500">{tab === "overview" ? "按销售渠道 / 店铺查看销售、成本、利润等核心数据" : "按订单与货品行查看销售记录，筛选结果与业绩总览使用同一数据源"}</p>
          </div>
          {tab === "overview" ? (
            <button onClick={() => handleDrill({})} className="rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-xs font-medium text-indigo-600 shadow-sm hover:bg-indigo-50">查看销售明细</button>
          ) : (
            <button onClick={() => setTab("overview")} className="rounded-lg border border-indigo-200 bg-white px-3.5 py-2 text-xs font-medium text-indigo-600 shadow-sm hover:bg-indigo-50">返回业绩总览</button>
          )}
        </div>
        <div className="mt-3 flex w-fit overflow-hidden rounded-lg border border-slate-200 bg-white text-[12px] shadow-sm">
          <button onClick={() => setTab("overview")} className={`px-4 py-1.5 ${tab === "overview" ? "bg-[#5664f5] font-medium text-white" : "text-slate-600 hover:bg-slate-50"}`}>业绩概览</button>
          <button onClick={() => { setDrill(undefined); setDrillSeq((n) => n + 1); setTab("detail"); }} className={`border-l border-slate-200 px-4 py-1.5 ${tab === "detail" ? "bg-[#5664f5] font-medium text-white" : "text-slate-600 hover:bg-slate-50"}`}>全部销售明细</button>
        </div>
      </header>

      {tab === "overview" ? <SalesAnalyticsView onDrill={handleDrill} /> : <SalesDetailView key={`detail-${drillSeq}`} initial={drill} />}
    </div>
  );
}
