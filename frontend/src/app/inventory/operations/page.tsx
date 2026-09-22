"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import ConsumablePurchases from "@/app/products/consumable-purchases";
import {
  consumablesApi,
  procurementChainApi,
  type ChainAllocation,
  type ChainInbound,
  type ChainOrderRow,
  type ConsumableRow,
} from "@/lib/api";

type OperationTab = "receipt" | "usage";
type UsageStatus = "all" | "pending" | "confirmed" | "none";
type AllocationForUsage = ChainAllocation & { inboundDocumentId?: number | null };
type InboundTask = {
  key: string;
  order: ChainOrderRow;
  inbound: ChainInbound;
  allocations: AllocationForUsage[];
};

const inputClass = "h-10 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-400";

function formatQuantity(value: string | number | null | undefined) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "—";
}

function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("zh-CN");
}

function allocationDocumentId(row: AllocationForUsage) {
  if (row.inboundDocumentId != null) return row.inboundDocumentId;
  const match = /由入库单\s*#(\d+)/.exec(row.note ?? "");
  return match ? Number(match[1]) : null;
}

function usageStatus(inbound: ChainInbound): UsageStatus {
  if (!inbound.linkId) return "none";
  return inbound.consumableUsageEnabled === true ? "confirmed" : "pending";
}

function buildInboundTasks(orders: ChainOrderRow[]) {
  const tasks: InboundTask[] = [];
  for (const order of orders) {
    const allocations = (order.allocations as AllocationForUsage[]) ?? [];
    for (const inbound of order.inbound ?? []) {
      tasks.push({
        key: `${order.orderId}-${inbound.linkId ?? inbound.targetId}`,
        order,
        inbound,
        allocations: allocations.filter((row) => allocationDocumentId(row) === inbound.targetId),
      });
    }
  }
  return tasks.sort((a, b) => {
    const aPending = usageStatus(a.inbound) === "pending" ? 0 : 1;
    const bPending = usageStatus(b.inbound) === "pending" ? 0 : 1;
    return aPending - bPending || (b.inbound.date ?? "").localeCompare(a.inbound.date ?? "");
  });
}

export default function InventoryOperationsPage() {
  const searchParams = useSearchParams();
  const tab: OperationTab = searchParams.get("tab") === "usage" ? "usage" : "receipt";
  const requestedDocumentId = Number(searchParams.get("document"));
  const [materials, setMaterials] = useState<ConsumableRow[]>([]);
  const [orders, setOrders] = useState<ChainOrderRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([
      consumablesApi.list(),
      tab === "usage" ? procurementChainApi.orders(500) : Promise.resolve({ items: [] as ChainOrderRow[] }),
    ]).then(([materialRows, chainRows]) => {
      if (cancelled) return;
      setMaterials(materialRows);
      setOrders(chainRows.items);
    }).catch((caught) => {
      if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught));
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [tab]);

  async function reloadMaterials() {
    try {
      setMaterials(await consumablesApi.list());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 pb-8">
      <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600">货品中心 / 库存管理</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">库存操作</h1>
            <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-500">耗材按采购单实际收货入库；正品实际入库后，系统按正品与耗材映射自动生成耗材耗用流水。包装损耗请通过盘点调整单独登记。</p>
          </div>
          <Link href="/inventory" className="rounded-lg border border-slate-200 px-3.5 py-2 text-xs font-medium text-slate-600 hover:border-blue-200 hover:text-blue-600">返回库存总览</Link>
        </div>
      </header>

      <nav className="flex w-fit flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm" aria-label="库存操作类型">
        <Link href="/inventory/operations?tab=receipt" className={`rounded-lg px-4 py-2 text-sm font-medium ${tab === "receipt" ? "bg-blue-600 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>耗材采购收货</Link>
        <Link href="/inventory/operations?tab=usage" className={`rounded-lg px-4 py-2 text-sm font-medium ${tab === "usage" ? "bg-amber-500 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>自动耗材流水</Link>
      </nav>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-xs text-blue-800">
        <span className="font-medium">耗材采购收货</span><span className="text-blue-300">→</span><span>正品实际入库</span><span className="text-blue-300">→</span><span className="font-medium">自动生成耗材耗用</span><span className="text-blue-300">→</span><span>盘点调整包装损耗</span>
      </div>

      {error && <div role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      {tab === "receipt" ? (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div><h2 className="text-base font-semibold text-slate-900">耗材采购收货</h2><p className="mt-1 text-xs leading-5 text-slate-500">只有耗材采购单的实际收货数量会增加耗材库存；采购单本身不会直接增加库存。</p></div>
            <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-medium text-emerald-700">入库来源：耗材采购单</span>
          </div>
          {loading && !materials.length ? <div className="py-10 text-center text-sm text-slate-400">正在加载耗材档案…</div> : <ConsumablePurchases materials={materials} reload={() => void reloadMaterials()} />}
        </section>
      ) : (
        <UsageOperations orders={orders} loading={loading} requestedDocumentId={requestedDocumentId} />
      )}
    </div>
  );
}

function UsageOperations({ orders, loading, requestedDocumentId }: { orders: ChainOrderRow[]; loading: boolean; requestedDocumentId: number }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<UsageStatus>("all");
  const [selectedKey, setSelectedKey] = useState("");
  const tasks = useMemo(() => buildInboundTasks(orders), [orders]);
  const visibleTasks = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return tasks.filter((task) => {
      const matchesQuery = !normalized || `${task.order.orderNo} ${task.inbound.goodsdocNo} ${task.inbound.supplier} ${task.inbound.warehouseName}`.toLowerCase().includes(normalized);
      return matchesQuery && (status === "all" || usageStatus(task.inbound) === status);
    });
  }, [query, status, tasks]);
  const selected = tasks.find((task) => task.key === selectedKey) ?? null;

  useEffect(() => {
    const requested = requestedDocumentId > 0 ? tasks.find((task) => task.inbound.targetId === requestedDocumentId) : null;
    if (requested && selectedKey !== requested.key) { setSelectedKey(requested.key); return; }
    if (!selectedKey || !tasks.some((task) => task.key === selectedKey)) setSelectedKey(tasks[0]?.key ?? "");
  }, [requestedDocumentId, selectedKey, tasks]);

  const pendingCount = tasks.filter((task) => usageStatus(task.inbound) === "pending").length;
  const confirmedCount = tasks.filter((task) => usageStatus(task.inbound) === "confirmed").length;

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><h2 className="text-base font-semibold text-slate-900">自动耗材流水</h2><p className="mt-1 text-xs leading-5 text-slate-500">系统以正品实际入库数量为依据，按已维护的正品↔耗材用量自动扣减；本页只读展示，不再提供“使用/不使用”确认。</p></div>
          <div className="flex gap-1.5 text-[11px]"><span className="rounded-full bg-amber-50 px-2.5 py-1 text-amber-700">待维护映射 {pendingCount}</span><span className="rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700">已自动扣减 {confirmedCount}</span></div>
        </div>
      </div>
      <div className="flex flex-wrap gap-2 border-b border-slate-100 bg-slate-50/60 px-5 py-3">
        <input aria-label="搜索商品入库单" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索入库单号、采购单号、供应商或仓库" className={`${inputClass} w-80`} />
        <select aria-label="自动耗材流水状态" value={status} onChange={(event) => setStatus(event.target.value as UsageStatus)} className={inputClass}><option value="all">全部状态</option><option value="pending">待维护耗材映射</option><option value="confirmed">已自动扣减</option><option value="none">历史关联待迁移</option></select>
        <span className="ml-auto self-center text-xs text-slate-400">显示 {visibleTasks.length} / {tasks.length} 张入库单</span>
      </div>
      <div className="grid min-h-[560px] lg:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.5fr)]">
        <div className="border-r border-slate-100 bg-slate-50/40">
          {loading ? <div className="p-10 text-center text-sm text-slate-400">正在加载商品入库单…</div> : !visibleTasks.length ? <div className="p-10 text-center"><p className="text-sm font-medium text-slate-600">暂无商品入库流水</p><p className="mt-2 text-xs leading-5 text-slate-400">请先在采购订单中关联真实入库单。</p></div> : <div className="divide-y divide-slate-100">{visibleTasks.map((task) => <button key={task.key} type="button" onClick={() => setSelectedKey(task.key)} className={`w-full px-5 py-4 text-left transition ${selected?.key === task.key ? "bg-white shadow-[inset_3px_0_0_#f59e0b]" : "hover:bg-white/80"}`}><div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="truncate font-mono text-xs font-semibold text-slate-800">{task.inbound.goodsdocNo || "未记录入库单号"}</div><div className="mt-1 truncate text-xs text-slate-500">{task.order.orderNo} · {task.order.supplier || task.inbound.supplier || "未记录供应商"}</div></div><UsageBadge inbound={task.inbound} /></div><div className="mt-2 flex justify-between text-[11px] text-slate-400"><span>{formatDate(task.inbound.date)} · {task.inbound.warehouseName || "未记录仓库"}</span><span>{task.allocations.length} 行商品</span></div></button>)}</div>}
        </div>
        <UsageDetail task={selected} />
      </div>
    </section>
  );
}

function UsageDetail({ task }: { task: InboundTask | null }) {
  if (!task) return <div className="flex items-center justify-center p-10 text-center text-sm text-slate-400">从左侧选择一张商品入库单查看自动耗用。</div>;
  const usageItems = task.inbound.consumableUsageItems ?? [];
  const status = usageStatus(task.inbound);
  return (
    <div className="p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-[11px] font-medium tracking-wide text-blue-600">商品实际入库</div><h3 className="mt-1 text-lg font-semibold text-slate-900">{task.inbound.goodsdocNo || "未记录入库单号"}</h3><p className="mt-1 text-xs text-slate-500">{task.order.orderNo} · {task.order.supplier || task.inbound.supplier || "未记录供应商"} · {task.inbound.warehouseName || "未记录仓库"}</p></div><UsageBadge inbound={task.inbound} /></div>
      <div className="mt-5 grid gap-4 xl:grid-cols-2">
        <section className="overflow-hidden rounded-xl border border-slate-200"><div className="border-b border-slate-100 bg-slate-50 px-4 py-3 text-sm font-medium text-slate-700">正品入库明细 <span className="ml-1 text-xs font-normal text-slate-400">按实际入库数量</span></div><table className="w-full text-left text-sm"><thead className="text-[11px] text-slate-500"><tr><th className="px-4 py-3">商品 / SKU</th><th className="px-4 py-3 text-right">实际入库数量</th></tr></thead><tbody className="divide-y divide-slate-100">{task.allocations.length ? task.allocations.map((row, index) => <tr key={`${row.id ?? row.skuCode}-${index}`}><td className="px-4 py-3"><div className="font-medium text-slate-700">{row.goodsName || "未命名商品"}</div><div className="mt-1 font-mono text-[11px] text-slate-400">{row.skuCode || "未关联 SKU"}</div></td><td className="px-4 py-3 text-right tabular-nums text-slate-700">{formatQuantity(row.quantity)}</td></tr>) : <tr><td colSpan={2} className="px-4 py-8 text-center text-xs text-slate-400">尚未反填入库商品明细</td></tr>}</tbody></table></section>
        <section className="overflow-hidden rounded-xl border border-emerald-100"><div className="border-b border-emerald-100 bg-emerald-50/60 px-4 py-3 text-sm font-medium text-slate-700">自动耗材耗用 <span className="ml-1 text-xs font-normal text-slate-400">按映射 × 实际入库数量</span></div><table className="w-full text-left text-sm"><thead className="text-[11px] text-slate-500"><tr><th className="px-4 py-3">耗材</th><th className="px-4 py-3 text-right">自动耗用数量</th><th className="px-4 py-3 text-right">状态</th></tr></thead><tbody className="divide-y divide-slate-100">{usageItems.length ? usageItems.map((item) => <tr key={item.id}><td className="px-4 py-3"><div className="font-medium text-slate-700">{item.consumableName}</div><div className="mt-1 font-mono text-[11px] text-slate-400">{item.consumableCode}</div></td><td className="px-4 py-3 text-right tabular-nums text-slate-700">{formatQuantity(item.quantity)} {item.unit}</td><td className="px-4 py-3 text-right"><span className="rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">已自动扣减</span></td></tr>) : <tr><td colSpan={3} className="px-4 py-8 text-center text-xs text-slate-400">{status === "pending" ? "尚未完成耗材映射，补齐货品档案后系统会自动补扣" : "暂无耗材耗用明细"}</td></tr>}</tbody></table></section>
      </div>
      <div className={`mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 text-xs leading-5 ${status === "confirmed" ? "border-emerald-100 bg-emerald-50/60 text-emerald-800" : "border-amber-100 bg-amber-50/60 text-amber-800"}`}><span>{status === "confirmed" ? "本次耗材已按正品实际入库数量自动生成耗用流水。后续包装损耗不修改这条自动流水。" : status === "pending" ? "该入库单暂缺正品↔耗材映射，系统未伪造耗用数量；维护映射后会自动补扣。" : "历史关联暂无可写入的自动耗用流水。"}</span><Link href="/inventory/adjustments" className="shrink-0 font-medium hover:underline">登记盘点损耗 →</Link></div>
    </div>
  );
}

function UsageBadge({ inbound }: { inbound: ChainInbound }) {
  if (!inbound.linkId) return <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[11px] text-slate-500">历史关联</span>;
  if (inbound.consumableUsageEnabled !== true) return <span className="shrink-0 rounded-full bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700">待维护映射</span>;
  return <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-1 text-[11px] font-medium text-emerald-700">已自动扣减</span>;
}
