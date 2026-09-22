"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import StatusBadge from "@/components/status-badge";
import {
  authenticatedFetch,
  consumablesApi,
  dashboardApi,
  fetchMe,
  getOverview,
  type AuthUser,
  logisticsApi,
  type LogisticsWorkbench,
  procurementWorkbenchApi,
  type ConsumableRow,
  type IntegrationStatus,
  type InventorySummary,
  type TrendPoint,
  type WorkbenchSummary,
} from "@/lib/api";

type ProductionOrder = {
  orderNo: string;
  supplier: string;
  stage: string;
  stageLabel: string;
  archiveGroup: "production" | "transit" | "receiving" | "archive";
  itemCount: number;
  quantityTotal: number;
  hasException: boolean;
};

type DashboardState = {
  trend: TrendPoint[];
  procurement: WorkbenchSummary | null;
  completedPurchaseOrders: number;
  inventory: InventorySummary | null;
  consumables: ConsumableRow[];
  logistics: LogisticsWorkbench | null;
  production: ProductionOrder[];
  integrations: IntegrationStatus[];
};

const EMPTY_STATE: DashboardState = {
  trend: [],
  procurement: null,
  completedPurchaseOrders: 0,
  inventory: null,
  consumables: [],
  logistics: null,
  production: [],
  integrations: [],
};

function number(value: string | number | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function money(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return `¥${number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function quantity(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function shortDate(value: string | null | undefined) {
  if (!value) return "—";
  return value.slice(0, 10).replaceAll("-", "/");
}

type DashboardIconName =
  | "sales"
  | "orders"
  | "transit"
  | "warning"
  | "purchase"
  | "import"
  | "production"
  | "warehouse"
  | "product"
  | "report";

function DashboardIcon({ name }: { name: DashboardIconName }) {
  const common = "h-6 w-6";
  if (name === "sales") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M3 5h2l2 10h10.5l2-7H6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /><circle cx="9" cy="19" r="1.5" fill="currentColor" /><circle cx="17" cy="19" r="1.5" fill="currentColor" /></svg>;
  }
  if (name === "orders") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M6 3.5h9l3 3v14H6v-17Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="M15 3.5v3h3M9 11h6M9 15h6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
  }
  if (name === "transit" || name === "product") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="m4 7 8-4 8 4v10l-8 4-8-4V7Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="m4.4 7.2 7.6 4 7.6-4M12 11.2V21" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /></svg>;
  }
  if (name === "warning") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="m12 3 9 17H3L12 3Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="M12 9v5M12 17h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  }
  if (name === "purchase") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3" stroke="currentColor" strokeWidth="1.7" /><path d="M12 8v8M8 12h8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  }
  if (name === "import") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M6 3.5h9l3 3v14H6v-17Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="M12 8v7m0 0-3-3m3 3 3-3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  if (name === "production") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M4 20V9l6 3V9l6 3V5h4v15H4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="M8 16h.01M12 16h.01M16 16h.01" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>;
  }
  if (name === "warehouse") {
    return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M3.5 20V8l8.5-4 8.5 4v12M7 20v-7h10v7M9 9h.01M12 9h.01M15 9h.01" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  }
  return <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
}

function KpiCard({
  label,
  value,
  hint,
  href,
  tone,
  mark,
  iconTone,
}: {
  label: string;
  value: string;
  hint: string;
  href: string;
  tone: string;
  mark: DashboardIconName;
  iconTone: string;
}) {
  return (
    <Link href={href} className={`group rounded-xl border p-4 transition hover:-translate-y-0.5 hover:shadow-md ${tone}`}>
      <div className="flex items-center gap-3">
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-white/75 shadow-sm ${iconTone}`}><DashboardIcon name={mark} /></div>
        <div className="min-w-0">
          <div className="truncate text-xs text-slate-500">{label}</div>
          <div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{value}</div>
          <div className="mt-1 truncate text-[11px] text-slate-500">{hint}</div>
        </div>
      </div>
      <div className="mt-3 h-1 overflow-hidden rounded-full bg-white/70"><div className="h-full w-2/3 rounded-full bg-white/80 transition group-hover:w-5/6" /></div>
    </Link>
  );
}

function Panel({
  title,
  subtitle,
  href,
  children,
}: {
  title: string;
  subtitle?: string;
  href?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          {subtitle && <p className="mt-1 text-[11px] text-slate-400">{subtitle}</p>}
        </div>
        {href && <Link href={href} className="shrink-0 text-xs font-medium text-indigo-600 hover:text-indigo-700">查看全部 →</Link>}
      </div>
      {children}
    </section>
  );
}

type StatusSegment = { label: string; count: number; color: string; href?: string };

function StatusDonut({ segments, centerLabel }: { segments: StatusSegment[]; centerLabel: string }) {
  const total = segments.reduce((sum, segment) => sum + segment.count, 0);
  let cursor = 0;
  const gradient = total
    ? segments.map((segment) => {
        const start = (cursor / total) * 100;
        cursor += segment.count;
        return `${segment.color} ${start}% ${(cursor / total) * 100}%`;
      }).join(", ")
    : "#e2e8f0 0% 100%";

  return (
    <div className="flex flex-wrap items-center gap-6 py-4">
      <div className="relative mx-auto h-36 w-36 shrink-0 rounded-full" style={{ background: `conic-gradient(${gradient})` }}>
        <div className="absolute inset-[18px] flex flex-col items-center justify-center rounded-full bg-white">
          <span className="text-2xl font-semibold text-slate-900">{total}</span>
          <span className="text-[11px] text-slate-400">{centerLabel}</span>
        </div>
      </div>
      <div className="min-w-[150px] flex-1 space-y-2">
        {segments.map((segment) => {
          const content = (
            <>
              <span className="flex items-center gap-2 text-xs text-slate-600"><i className="h-2 w-2 rounded-full" style={{ backgroundColor: segment.color }} />{segment.label}</span>
              <span className="text-xs font-medium tabular-nums text-slate-800">{segment.count}</span>
            </>
          );
          return segment.href ? (
            <Link key={segment.label} href={segment.href} className="flex items-center justify-between rounded-md px-1 py-0.5 hover:bg-slate-50">{content}</Link>
          ) : (
            <div key={segment.label} className="flex items-center justify-between px-1 py-0.5">{content}</div>
          );
        })}
      </div>
    </div>
  );
}

function QuickAction({ href, mark, label, tone, iconTone }: { href: string; mark: DashboardIconName; label: string; tone: string; iconTone: string }) {
  return (
    <Link href={href} className={`flex min-h-24 flex-col items-center justify-center rounded-xl border p-3 text-center transition hover:-translate-y-0.5 hover:shadow-sm ${tone}`}>
      <span className={iconTone}><DashboardIcon name={mark} /></span>
      <span className="mt-2 text-xs font-medium text-slate-700">{label}</span>
    </Link>
  );
}

function PanelLoading({ label }: { label: string }) {
  return <div className="flex h-52 items-center justify-center text-sm text-slate-400">正在加载{label}…</div>;
}

export default function OverviewPage() {
  const [state, setState] = useState<DashboardState>(EMPTY_STATE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const loadRequest = useRef(0);

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    setError("");
    try {
      const [productionResponse, trend, procurement, completed, inventory, consumables, logistics, overview] = await Promise.all([
        authenticatedFetch("/api/v1/supply-chain/production-purchase-view?group=all&limit=500", { cache: "no-store" }),
        dashboardApi.salesTrend(30),
        procurementWorkbenchApi.summary(),
        procurementWorkbenchApi.orders({ status: "done", page: 1, pageSize: 100 }),
        dashboardApi.inventory(),
        consumablesApi.list(),
        logisticsApi.workbench().catch(() => null),
        getOverview(),
      ]);
      if (!productionResponse.ok) throw new Error(`生产执行数据加载失败（${productionResponse.status}）`);
      const productionPayload = (await productionResponse.json()) as { rows?: ProductionOrder[] };
      if (requestId !== loadRequest.current) return;
      setState({
        trend,
        procurement,
        completedPurchaseOrders: completed.total,
        inventory,
        consumables,
        logistics,
        production: productionPayload.rows ?? [],
        integrations: overview.integrations,
      });
    } catch (caught) {
      if (requestId !== loadRequest.current) return;
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (requestId === loadRequest.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    setNow(new Date());
    fetchMe().then(setUser).catch(() => {});
  }, [load]);

  const metrics = useMemo(() => {
    const sales = state.trend.reduce((sum, point) => sum + number(point.salesAmount), 0);
    const orders = state.trend.reduce((sum, point) => sum + point.orders, 0);
    const warnings = state.consumables.filter((row) => row.lowStock);
    return {
      sales,
      orders,
      warnings,
      latestDate: state.trend[state.trend.length - 1]?.date ?? null,
    };
  }, [state]);

  const purchaseSegments: StatusSegment[] = [
    { label: "待完善", count: state.procurement?.pendingSku ?? 0, color: "#f59e0b", href: "/purchase/workbench?view=orders&status=refine" },
    { label: "待入库", count: state.procurement?.pendingInbound ?? 0, color: "#35b9a4", href: "/purchase/workbench?view=orders&status=inbound" },
    { label: "待发票", count: state.procurement?.pendingInvoice ?? 0, color: "#8b6cf6", href: "/purchase/workbench?view=orders&status=invoice" },
    { label: "开票完成", count: state.completedPurchaseOrders, color: "#cbd5e1", href: "/purchase/workbench?view=orders&status=done" },
  ];

  const productionSegments: StatusSegment[] = [
    { label: "待确认", count: state.production.filter((order) => order.stage === "pending").length, color: "#f59e0b", href: "/supply-chain/production?group=production&stage=pending" },
    { label: "待生产", count: state.production.filter((order) => order.stage === "waiting").length, color: "#9bbcf7", href: "/supply-chain/production?group=production&stage=waiting" },
    { label: "生产中", count: state.production.filter((order) => order.stage === "producing").length, color: "#4f8df7", href: "/supply-chain/production?group=production&stage=producing" },
    { label: "在途", count: state.production.filter((order) => order.archiveGroup === "transit").length, color: "#35b9a4", href: "/supply-chain/production?group=transit" },
    { label: "到货/入库", count: state.production.filter((order) => order.archiveGroup === "receiving").length, color: "#8b6cf6", href: "/supply-chain/receiving" },
    { label: "已完成", count: state.production.filter((order) => order.archiveGroup === "archive").length, color: "#cbd5e1", href: "/supply-chain/production?group=archive" },
  ];

  const greeting = now ? (now.getHours() < 12 ? "早上好" : now.getHours() < 18 ? "下午好" : "晚上好") : "你好";
  const displayName = user?.displayName || user?.username || "Admin";
  const latestPoint = state.trend[state.trend.length - 1];
  const latestDataDate = shortDate(metrics.latestDate);
  const todayDataDate = now?.toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  const dayLabel = metrics.latestDate && todayDataDate === metrics.latestDate ? "今日" : "最近数据日";

  return (
    <div className="w-full min-w-0 space-y-5 pb-8">
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-3 backdrop-blur">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-[22px] font-semibold tracking-tight text-[#14213a]">{greeting}，{displayName}</h1>
            <span className="text-lg" aria-hidden="true">👋</span>
          </div>
          <p className="mt-1 text-[13px] text-slate-500">销售、采购、生产、库存、财务与物流的关键经营状态集中在这里</p>
        </div>
      </header>

      {error && <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">首页数据加载失败：{error}</div>}

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        <KpiCard label={`${dayLabel}销售额`} value={loading ? "…" : money(latestPoint?.salesAmount)} hint={`近30天累计 ${money(metrics.sales)} · ${latestDataDate}`} href="/sales?tab=overview" tone="border-blue-100 bg-blue-50/70" mark="sales" iconTone="text-blue-600" />
        <KpiCard label={`${dayLabel}订单数`} value={loading ? "…" : quantity(latestPoint?.orders)} hint={`近30天累计 ${quantity(metrics.orders)} 单 · ${latestDataDate}`} href="/sales?tab=overview" tone="border-emerald-100 bg-emerald-50/70" mark="orders" iconTone="text-emerald-600" />
        <KpiCard label="待入库采购" value={loading ? "…" : quantity(state.procurement?.pendingInbound ?? 0)} hint="采购订单尚未完成真实入库" href="/purchase/workbench?view=orders&status=inbound" tone="border-cyan-100 bg-cyan-50/70" mark="purchase" iconTone="text-cyan-600" />
        <KpiCard label="待开发票" value={loading ? "…" : quantity(state.procurement?.pendingInvoice ?? 0)} hint="采购金额仍有未开票余量" href="/purchase/workbench?view=orders&status=invoice" tone="border-amber-100 bg-amber-50/70" mark="report" iconTone="text-amber-600" />
        <KpiCard label="本月预估物流费" value={loading ? "…" : money(state.logistics?.cards.monthEstimatedAmount)} hint={!state.logistics ? "物流数据暂不可用" : state.logistics.cards.estimateUnitPriceSource === "smart" ? "按历史智能单价预估" : "历史不足时按默认单价预估"} href="/logistics/workbench" tone="border-orange-100 bg-orange-50/70" mark="transit" iconTone="text-orange-500" />
        <KpiCard label="库存预警" value={loading ? "…" : quantity(metrics.warnings.length)} hint="耗材可用库存低于安全库存" href="/inventory?tab=consumables" tone="border-violet-100 bg-violet-50/70" mark="warning" iconTone="text-violet-600" />
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="采购订单状态" subtitle="点击状态进入采购订单对应筛选。" href="/purchase/workbench">
          {loading ? <PanelLoading label="采购状态" /> : <StatusDonut segments={purchaseSegments} centerLabel="采购单" />}
        </Panel>

        <Panel title="生产订单状态" subtitle="正品采购按当前阶段自动归档，最终入库仍回到吉客云事实。" href="/supply-chain/production">
          {loading ? <PanelLoading label="生产状态" /> : state.production.length ? <StatusDonut segments={productionSegments} centerLabel="生产单" /> : <div className="flex h-52 items-center justify-center text-sm text-slate-400">暂无生产订单</div>}
        </Panel>

        <Panel title="库存预警" subtitle={`只展示耗材预警；正品库存=采购入库−销售出库独立运算 · 正品 SKU ${state.inventory?.skuCount ?? "—"} 个`} href="/inventory?tab=consumables">
          {loading ? <PanelLoading label="库存预警" /> : <div className="mt-4 divide-y divide-slate-100">
            {metrics.warnings.slice(0, 5).map((row) => (
              <Link key={row.id} href={`/inventory?tab=consumables&search=${encodeURIComponent(row.code)}`} className="flex items-center justify-between gap-3 py-2.5 hover:bg-amber-50/50">
                <span className="min-w-0 truncate text-xs text-slate-700">{row.name}<span className="ml-2 font-mono text-[10px] text-slate-400">{row.code}</span></span>
                <span className="shrink-0 text-right text-[11px] tabular-nums"><span className="text-amber-700">{quantity(row.availableQty)}</span><span className="text-slate-400"> / {quantity(row.minStockQty)}</span></span>
              </Link>
            ))}
            {!metrics.warnings.length && <div className="py-12 text-center text-sm text-slate-400">当前没有耗材库存预警</div>}
            {metrics.warnings.length > 5 && <Link href="/inventory?tab=consumables" className="block pt-2 text-center text-[11px] text-indigo-600">还有 {metrics.warnings.length - 5} 项，查看全部 →</Link>}
          </div>}
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="待办事项" subtitle="优先处理会影响库存、成本和结账的数据缺口。">
          <div className="mt-4 divide-y divide-slate-100">
            <Link href="/purchase/workbench?view=orders&status=refine" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>待完善采购内容</span><b className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">{state.procurement?.pendingSku ?? 0}</b></Link>
            <Link href="/purchase/workbench?view=orders&status=inbound" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>待入库采购订单</span><b className="rounded-full bg-cyan-50 px-2 py-1 text-cyan-700">{state.procurement?.pendingInbound ?? 0}</b></Link>
            <Link href="/purchase/workbench?view=orders&status=invoice" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>待开发票采购订单</span><b className="rounded-full bg-violet-50 px-2 py-1 text-violet-700">{state.procurement?.pendingInvoice ?? 0}</b></Link>
            <Link href="/inventory?tab=consumables" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>耗材库存预警</span><b className="rounded-full bg-orange-50 px-2 py-1 text-orange-700">{metrics.warnings.length}</b></Link>
            <Link href="/supply-chain/production" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>生产链路异常</span><b className="rounded-full bg-red-50 px-2 py-1 text-red-600">{state.production.filter((order) => order.hasException).length}</b></Link>
            <Link href="/exceptions" className="flex items-center justify-between py-3 text-xs hover:bg-slate-50"><span>异常中心</span><b className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">进入查看</b></Link>
          </div>
        </Panel>

        <Panel title="物流成本" subtitle="未出账用智能预估，账单核销后自动替换为实际成本。" href="/logistics/workbench">
          {loading ? <PanelLoading label="物流成本" /> : state.logistics ? (
            <div className="mt-4 space-y-3">
              <div className="flex items-end justify-between rounded-xl bg-orange-50/70 px-4 py-3">
                <div><div className="text-[11px] text-slate-500">本月预估</div><div className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{money(state.logistics.cards.monthEstimatedAmount)}</div></div>
                <div className="text-right text-[11px] text-slate-500">{quantity(state.logistics.cards.monthShippedCount)} 单<br />{state.logistics.cards.estimateUnitPriceSource === "smart" ? "历史智能" : "默认兜底"}</div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg border border-slate-100 p-3"><div className="text-[10px] text-slate-400">待出账预估</div><div className="mt-1 text-sm font-semibold tabular-nums text-slate-700">{money(state.logistics.cards.pendingEstimatedAmount)}</div></div>
                <div className="rounded-lg border border-slate-100 p-3"><div className="text-[10px] text-slate-400">最近实际单均</div><div className="mt-1 text-sm font-semibold tabular-nums text-slate-700">{state.logistics.cards.latestActualUnitPrice ? money(state.logistics.cards.latestActualUnitPrice) + "/单" : "—"}</div></div>
                <div className="col-span-2 rounded-lg border border-slate-100 p-3"><div className="text-[10px] text-slate-400">本年度物流成本（实际 + 预估）</div><div className="mt-1 text-sm font-semibold tabular-nums text-slate-700">{money(state.logistics.cards.annualLogisticsCost)}</div></div>
              </div>
            </div>
          ) : <div className="flex h-52 items-center justify-center text-sm text-slate-400">物流成本暂不可用</div>}
        </Panel>

        <Panel title="快捷操作" subtitle="常用经营动作直接进入对应业务页面。">
          <div className="mt-4 grid grid-cols-2 gap-3">
            <QuickAction href="/purchase/workbench?view=orders&action=new" mark="purchase" label="新建采购订单" tone="border-blue-100 bg-blue-50/60" iconTone="text-blue-600" />
            <QuickAction href="/data-center-import?tab=alibaba1688" mark="import" label="拉取1688订单" tone="border-indigo-100 bg-indigo-50/60" iconTone="text-indigo-600" />
            <QuickAction href="/supply-chain/production/manual" mark="production" label="新建生产订单" tone="border-violet-100 bg-violet-50/60" iconTone="text-violet-600" />
            <QuickAction href="/logistics/bills" mark="report" label="导入物流账单" tone="border-orange-100 bg-orange-50/60" iconTone="text-orange-500" />
            <QuickAction href="/inventory" mark="warehouse" label="查看库存" tone="border-emerald-100 bg-emerald-50/60" iconTone="text-emerald-600" />
            <QuickAction href="/products" mark="product" label="基础货品" tone="border-slate-200 bg-slate-50" iconTone="text-slate-600" />
          </div>
        </Panel>
      </div>

      <Panel title="数据连接" subtitle="连接状态只反映系统已记录的配置/测试结果，不把未测试通道显示为已连通。" href="/settings">
        <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {loading ? <div className="py-8 text-center text-sm text-slate-400">正在加载数据连接…</div> : state.integrations.map((item) => (
            <Link key={item.id} href="/settings" className="flex items-center justify-between gap-2 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5 hover:border-indigo-100 hover:bg-indigo-50/40">
              <span className="min-w-0 truncate text-xs text-slate-700">{item.name}</span>
              <StatusBadge status={item.status} />
            </Link>
          ))}
          {!loading && !state.integrations.length && <div className="text-sm text-slate-400">暂无数据连接状态</div>}
        </div>
      </Panel>
    </div>
  );
}
