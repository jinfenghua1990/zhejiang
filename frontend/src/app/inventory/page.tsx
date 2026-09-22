"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  consumablesApi,
  dashboardApi,
  type ConsumableRow,
  type InventorySkuRow,
  type SkuTransactionRow,
  warehousesApi,
  type WarehouseRow,
} from "@/lib/api";
import { useTabScopedState } from "@/lib/workspace/tab-store";
import ConsumableInventoryView from "./consumables-view";
import InventoryTransactionModal from "./transaction-modal";

type InventoryTab = "goods" | "bundle" | "consumables";
type StatusFilter = "all" | "active" | "inactive";
type AlertFilter = "all" | "normal" | "warning" | "critical" | "pending";
type StockStatus = "normal" | "warning" | "critical" | "pending" | "inactive";

type InventoryViewRow = {
  key: string;
  id: number;
  sourceKind: "goods" | "consumable";
  typeLabel: "正品" | "耗材" | "虚拟组合";
  name: string;
  code: string;
  barcode: string;
  warehouse: string;
  warehouseNames: string[];
  warehouseQuantities: Array<{ name: string; quantity: number }>;
  boundConsumables: Array<{ code: string; name: string; usagePerUnit: string | null }>;
  available: number | null;
  lastChanged: string | null;
  status: StockStatus;
  statusLabel: string;
  alert: AlertFilter;
  unit: string;
};

const TABS: Array<{ key: InventoryTab; label: string }> = [
  { key: "goods", label: "正品库存" },
  { key: "bundle", label: "虚拟组合库存" },
  { key: "consumables", label: "耗材库存" },
];

function quantity(value: string | number | null | undefined) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatQuantity(value: number | null) {
  return value == null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString("zh-CN");
}

/** 单件用量：去掉 "1.0000" 这类无意义的小数尾巴。 */
function formatUsage(value: string | null | undefined) {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : value;
}

function statusFor(quantityValue: number | null, active: boolean, lowStock = false): { status: StockStatus; label: string; alert: AlertFilter } {
  if (!active) return { status: "inactive", label: "停用", alert: "all" };
  if (quantityValue == null) return { status: "pending", label: "待核算", alert: "pending" };
  if (quantityValue < 0) return { status: "critical", label: "紧张", alert: "critical" };
  if (lowStock || quantityValue === 0) return { status: "warning", label: "预警", alert: "warning" };
  return { status: "normal", label: "正常", alert: "normal" };
}

function typeOfGoods(row: InventorySkuRow): { label: "正品" | "虚拟组合" } {
  return row.productType === "bundle" || row.productType === "virtual_bundle"
    ? { label: "虚拟组合" }
    : { label: "正品" };
}

function toRows(goods: InventorySkuRow[], consumables: ConsumableRow[], warehouses: WarehouseRow[]): InventoryViewRow[] {
  const consumableWarehouseNames = warehouses
    .filter((row) => row.status === "active" && (row.purpose === "consumable" || row.purpose === "both"))
    .map((row) => row.name);
  const consumablesBySku = new Map<string, Array<{ code: string; name: string; usagePerUnit: string | null }>>();
  consumables.forEach((row) => {
    const details = row.mappingDetails ?? row.linkedSkus.map((sku) => ({ ...sku, usagePerUnit: null }));
    const shortName = row.name.replace(/^耗材[\s-]*/u, "").trim() || row.name;
    details.forEach((detail) => {
      if (!detail.skuCode) return;
      const list = consumablesBySku.get(detail.skuCode) ?? [];
      list.push({ code: row.code, name: shortName, usagePerUnit: detail.usagePerUnit });
      consumablesBySku.set(detail.skuCode, list);
    });
  });
  const goodsRows = goods.map((row) => {
    const type = typeOfGoods(row);
    const available = row.hasMovement ? quantity(row.quantity) : null;
    const status = statusFor(available, row.status === "active");
    const warehouseNames = row.warehouses.map((warehouse) => warehouse.warehouseName || "未映射仓库");
    return {
      key: `goods-${row.skuId}`,
      id: row.skuId,
      sourceKind: "goods" as const,
      typeLabel: type.label,
      name: row.goodsName || row.skuName || row.skuCode,
      code: row.skuCode,
      barcode: row.barcode,
      warehouse: warehouseNames.length ? warehouseNames.join("、") : "未发生库存变动",
      warehouseNames,
      warehouseQuantities: row.warehouses.map((warehouse) => ({ name: warehouse.warehouseName || "未映射仓库", quantity: quantity(warehouse.quantity) ?? 0 })),
      boundConsumables: consumablesBySku.get(row.skuCode) ?? [],
      available,
      lastChanged: row.lastDocumentAt,
      status: status.status,
      statusLabel: status.label,
      alert: status.alert,
      unit: row.unit,
    };
  });
  const consumableRows = consumables.map((row) => {
    const available = quantity(row.availableQty);
    const status = statusFor(available, row.status === "active", row.lowStock);
    return {
      key: `consumable-${row.id}`,
      id: row.id,
      sourceKind: "consumable" as const,
      typeLabel: "耗材" as const,
      name: row.name,
      code: row.code,
      barcode: row.barcode,
      warehouse: consumableWarehouseNames.length ? consumableWarehouseNames.join("、") : "耗材台账",
      warehouseNames: consumableWarehouseNames,
      warehouseQuantities: [],
      boundConsumables: [],
      available,
      lastChanged: null,
      status: status.status,
      statusLabel: status.label,
      alert: status.alert,
      unit: row.unit,
    };
  });
  return [...goodsRows, ...consumableRows];
}

type TransactionTarget = { kind: "goods" | "consumable"; id: number; name: string; code: string; unit: string };

export default function InventoryPage() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [tab, setTab] = useTabScopedState<InventoryTab>("inventory.tab", () => {
    const raw = searchParams.get("tab");
    return raw === "bundle" || raw === "consumables" ? raw : "goods";
  });
  const [search, setSearch] = useTabScopedState("inventory.search", () => searchParams.get("search") ?? "");
  const [warehouseFilter, setWarehouseFilter] = useTabScopedState("inventory.warehouse", "all");
  const [statusFilter, setStatusFilter] = useTabScopedState<StatusFilter>("inventory.status", "all");
  const [alertFilter, setAlertFilter] = useTabScopedState<AlertFilter>("inventory.alert", "all");
  const [goods, setGoods] = useState<InventorySkuRow[]>([]);
  const [consumables, setConsumables] = useState<ConsumableRow[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [detailRow, setDetailRow] = useState<InventoryViewRow | null>(null);
  const [txTarget, setTxTarget] = useState<TransactionTarget | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    Promise.all([dashboardApi.inventorySkus(""), consumablesApi.list(""), warehousesApi.list(false)])
      .then(([goodsRows, consumableRows, warehouseRows]) => {
        setGoods(goodsRows);
        setConsumables(consumableRows);
        setWarehouses(warehouseRows);
      })
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => toRows(goods, consumables, warehouses), [consumables, goods, warehouses]);
  const warehouseOptions = useMemo(
    () => [...new Set(rows.flatMap((row) => row.warehouseNames))].filter(Boolean).sort(),
    [rows],
  );
  const metrics = useMemo(() => ({
    inStockSku: rows.filter((row) => row.available != null && row.available > 0).length,
    goodsStock: rows.filter((row) => row.sourceKind === "goods" && row.typeLabel === "正品").reduce((sum, row) => sum + (row.available ?? 0), 0),
    bundleStock: rows.filter((row) => row.sourceKind === "goods" && row.typeLabel === "虚拟组合").reduce((sum, row) => sum + (row.available ?? 0), 0),
    consumableStock: rows.filter((row) => row.sourceKind === "consumable").reduce((sum, row) => sum + (row.available ?? 0), 0),
    lowStock: rows.filter((row) => row.alert === "warning" || row.alert === "critical").length,
  }), [rows]);
  const totalStock = metrics.goodsStock + metrics.bundleStock + metrics.consumableStock;
  const visibleRows = useMemo(() => rows.filter((row) => {
    const term = search.trim().toLowerCase();
    const textMatch = !term || `${row.name} ${row.code} ${row.barcode}`.toLowerCase().includes(term);
    const tabMatch = (tab === "goods" && row.sourceKind === "goods" && row.typeLabel === "正品")
      || (tab === "bundle" && row.sourceKind === "goods" && row.typeLabel === "虚拟组合")
      || (tab === "consumables" && row.sourceKind === "consumable");
    const warehouseMatch = warehouseFilter === "all" || row.warehouseNames.includes(warehouseFilter);
    const statusMatch = statusFilter === "all" || (statusFilter === "active" ? row.status !== "inactive" : row.status === "inactive");
    const alertMatch = alertFilter === "all" || row.alert === alertFilter;
    return textMatch && tabMatch && warehouseMatch && statusMatch && alertMatch;
  }), [alertFilter, rows, search, statusFilter, tab, warehouseFilter]);

  function changeTab(next: InventoryTab) {
    setTab(next);
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    syncWorkspaceUrl(`${pathname}${params.toString() ? `?${params}` : ""}`);
  }

  function openTransactions(row: InventoryViewRow) {
    setTxTarget({ kind: row.sourceKind, id: row.id, name: row.name, code: row.code, unit: row.unit });
  }

  if (tab === "consumables") {
    return <ConsumableInventoryView rows={consumables} loading={loading} error={error} onReload={load} onTabChange={changeTab} />;
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-3 pb-8">
      <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600">库存中心 / 库存总览</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">正品库存</h1>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Link href="/inventory/adjustments" className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 transition hover:border-blue-200 hover:text-blue-600">库存调整</Link>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2 text-xs leading-5 text-blue-800">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white">i</span>
          <span>正品库存 = 采购入库 − 销售出库，来自本地吉客云出入库单明细逐笔运算；点击「流水」可逐笔核对，口径与库存总览一致。「绑定耗材」为该正品入库时按映射自动耗用的耗材及单件用量。</span>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-label="库存指标">
        <InventoryMetric label="正品库存" value={formatQuantity(metrics.goodsStock)} hint="采购入库 − 销售出库" tone="indigo" icon="◆" />
        <InventoryMetric label="虚拟组合库存" value={formatQuantity(metrics.bundleStock)} hint="已有库存流水的组合货品" tone="blue" icon="≋" />
        <InventoryMetric label="耗材库存" value={formatQuantity(metrics.consumableStock)} hint="采购收货 − 自动耗材耗用" tone="amber" icon="▣" />
        <InventoryMetric label="库存总合计" value={formatQuantity(totalStock)} hint="正品 + 虚拟组合 + 耗材" tone="emerald" icon="Σ" />
        <InventoryMetric label="在库 SKU" value={String(metrics.inStockSku)} hint="当前可用库存大于 0" tone="violet" icon="#" />
        <InventoryMetric label="低库存预警" value={String(metrics.lowStock)} hint="预警或紧张" tone="rose" icon="!" />
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-4 pt-1">
          <div className="flex gap-6 overflow-x-auto">
            {TABS.map((item) => <button key={item.key} type="button" onClick={() => changeTab(item.key)} className={`whitespace-nowrap border-b-2 px-1 py-3 text-sm font-medium transition ${tab === item.key ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{item.label}</button>)}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索货品名称、编码或条码" className="h-9 w-[300px] rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-400" />
          <select aria-label="仓库筛选" value={warehouseFilter} onChange={(event) => setWarehouseFilter(event.target.value)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">仓库：全部</option>{warehouseOptions.map((warehouse) => <option key={warehouse} value={warehouse}>{warehouse}</option>)}</select>
          <select aria-label="状态筛选" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">状态：全部</option><option value="active">状态：启用</option><option value="inactive">状态：停用</option></select>
          <select aria-label="预警状态筛选" value={alertFilter} onChange={(event) => setAlertFilter(event.target.value as AlertFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">预警：全部</option><option value="normal">预警：正常</option><option value="warning">预警：预警</option><option value="critical">预警：紧张</option><option value="pending">预警：待核算</option></select>
          <span className="ml-auto text-xs text-slate-400">显示 {visibleRows.length} / {rows.length} 条</span>
        </div>

        {error && <div role="alert" className="mx-4 mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700">{error}</div>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1320px] table-fixed text-left text-[13px]">
            <colgroup>
              <col className="w-[10%]" />
              <col className="w-[17%]" />
              <col className="w-[11%]" />
              <col className="w-[13%]" />
              <col className="w-[7%]" />
              <col className="w-[15%]" />
              <col className="w-[7%]" />
              <col className="w-[8%]" />
              <col className="w-[6%]" />
              <col className="w-[6%]" />
            </colgroup>
            <thead className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <tr className="border-b border-slate-100 text-slate-600">
                <th colSpan={2} className="px-4 py-2.5">货品信息</th>
                <th colSpan={3} className="border-l border-slate-100 px-3 py-2.5">绑定耗材信息</th>
                <th colSpan={4} className="border-l border-slate-100 px-3 py-2.5">库存信息</th>
                <th rowSpan={2} className="border-l border-slate-100 px-4 py-2.5 text-right align-middle">操作</th>
              </tr>
              <tr>
                <th className="px-4 py-2.5">编码</th>
                <th className="px-3 py-2.5">货品名称</th>
                <th className="border-l border-slate-100 px-3 py-2.5">耗材编号</th>
                <th className="px-3 py-2.5">耗材名称</th>
                <th className="px-3 py-2.5 text-right">单件用量</th>
                <th className="border-l border-slate-100 px-3 py-2.5">仓库</th>
                <th className="px-3 py-2.5 text-right">可用库存</th>
                <th className="px-3 py-2.5">最近变动</th>
                <th className="px-3 py-2.5">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleRows.map((row) => (
                <InventoryRow key={row.key} row={row} onOpenDetail={() => setDetailRow(row)} onOpenTransactions={() => openTransactions(row)} />
              ))}
            </tbody>
          </table>
          {loading && <div className="p-12 text-center text-sm text-slate-400">正在加载库存…</div>}
          {!loading && !visibleRows.length && <div className="p-12 text-center text-sm text-slate-400">没有匹配的库存记录</div>}
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs text-slate-400"><span>共 {visibleRows.length} 条数据</span><span>正品按「采购入库 − 销售出库」独立运算；耗材统一归属耗材库。</span></div>
      </section>

      {detailRow && <GoodsDetailDrawer row={detailRow} onClose={() => setDetailRow(null)} onOpenTransactions={(inbound, outbound, balance) => setTxTarget({ kind: "goods", id: detailRow.id, name: detailRow.name, code: detailRow.code, unit: detailRow.unit })} />}
      {txTarget && <InventoryTransactionModal kind={txTarget.kind} id={txTarget.id} title={txTarget.name} subtitle={txTarget.code} unit={txTarget.unit} onClose={() => setTxTarget(null)} />}
    </div>
  );
}

function InventoryRow({ row, onOpenDetail, onOpenTransactions }: { row: InventoryViewRow; onOpenDetail: () => void; onOpenTransactions: () => void }) {
  const statusClass = row.status === "normal" ? "bg-emerald-50 text-emerald-700" : row.status === "warning" ? "bg-amber-50 text-amber-700" : row.status === "critical" ? "bg-rose-50 text-rose-700" : "bg-slate-100 text-slate-500";
  const bound = row.boundConsumables;
  const boundTitle = bound.map((item) => `${item.code} · ${item.name} · 单件用量 ${formatUsage(item.usagePerUnit)}`).join("\n");
  return (
    <tr className="transition-colors hover:bg-slate-50/70">
      <td className="truncate px-4 py-3 font-mono text-xs text-slate-700" title={row.code}>{row.code}</td>
      <td className="px-3 py-3"><div className="truncate font-medium text-slate-800" title={row.name}>{row.name}</div><div className="mt-0.5 truncate text-[11px] text-slate-400" title={`${row.barcode || "暂无条码"} · ${row.unit || "—"}`}>{row.barcode || "暂无条码"} · {row.unit || "—"}</div></td>
      <td className="truncate border-l border-slate-100 px-3 py-3 font-mono text-xs text-slate-600" title={boundTitle || ""}>{bound[0]?.code || "—"}{bound.length > 1 && <span className="ml-1 text-[11px] text-slate-400">+{bound.length - 1}</span>}</td>
      <td className="px-3 py-3 text-slate-700"><div className="truncate" title={boundTitle || ""}>{bound[0]?.name || "未绑定耗材"}</div></td>
      <td className="px-3 py-3 text-right tabular-nums text-slate-700" title={boundTitle || ""}>{formatUsage(bound[0]?.usagePerUnit)}</td>
      <td className="border-l border-slate-100 px-3 py-3 text-xs text-slate-600"><span className="line-clamp-2" title={row.warehouse}>{row.warehouse}</span></td>
      <td className="px-3 py-3 text-right font-medium tabular-nums text-slate-900">{formatQuantity(row.available)}</td>
      <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-500">{formatDate(row.lastChanged)}</td>
      <td className="px-3 py-3"><span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-medium ${statusClass}`}>{row.statusLabel}</span></td>
      <td className="whitespace-nowrap border-l border-slate-100 px-4 py-3 text-right">
        <button type="button" onClick={onOpenDetail} className="px-1.5 text-xs font-medium text-blue-600 hover:underline">详情</button>
        <button type="button" onClick={onOpenTransactions} className="px-1.5 text-xs text-slate-500 hover:text-blue-600">流水</button>
      </td>
    </tr>
  );
}

function GoodsDetailDrawer({ row, onClose, onOpenTransactions }: { row: InventoryViewRow; onClose: () => void; onOpenTransactions: (inbound: number, outbound: number, balance: number) => void }) {
  const [rows, setRows] = useState<SkuTransactionRow[]>([]);
  const [balance, setBalance] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    dashboardApi.skuTransactions(row.id)
      .then((payload) => {
        if (cancelled) return;
        setRows(payload.rows);
        setBalance(payload.balance);
      })
      .catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [row.id]);

  const inbound = rows.filter((item) => item.direction === "inbound").reduce((sum, item) => sum + item.quantity, 0);
  const outbound = rows.filter((item) => item.direction === "outbound").reduce((sum, item) => sum + Math.abs(item.quantity), 0);
  const metrics: Array<[string, number | null]> = [
    ["可用库存", row.available],
    ["累计入库", inbound],
    ["累计出库", outbound],
    ["当前结存", balance],
  ];
  return (
    <div className="fixed inset-0 z-modal flex justify-end" role="dialog" aria-modal="true" aria-label="正品库存详情">
      <button type="button" aria-label="关闭详情" onClick={onClose} className="absolute inset-0 bg-slate-950/25" />
      <aside className="relative h-full w-full max-w-[480px] overflow-y-auto border-l border-slate-200 bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-100 bg-white px-5 py-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600">正品库存 / 详情</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-900">{row.name}</h2>
            <div className="mt-1 font-mono text-xs text-slate-500">{row.code}</div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg px-2 py-1 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
        </div>
        <div className="space-y-4 p-5">
          <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="text-xs font-semibold text-slate-700">仓库分布</div>
            <div className="mt-3 space-y-2">
              {row.warehouseQuantities.length
                ? row.warehouseQuantities.map((item) => (
                  <div key={item.name} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2">
                    <span className="text-sm text-slate-800">{item.name}</span>
                    <span className="text-xs tabular-nums text-slate-500">{formatQuantity(item.quantity)} {row.unit}</span>
                  </div>
                ))
                : <div className="text-sm text-slate-400">未发生库存变动</div>}
            </div>
          </section>
          <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
            <div className="text-xs font-semibold text-slate-700">绑定耗材</div>
            <div className="mt-3 space-y-2">
              {row.boundConsumables.length
                ? row.boundConsumables.map((item) => (
                  <div key={item.code} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2">
                    <div>
                      <div className="font-mono text-xs text-slate-600">{item.code}</div>
                      <div className="mt-0.5 text-sm text-slate-800">{item.name}</div>
                    </div>
                    <span className="text-xs text-slate-500">单件用量 {formatUsage(item.usagePerUnit)}</span>
                  </div>
                ))
                : <div className="text-sm text-slate-400">尚未绑定耗材</div>}
            </div>
          </section>
          <section className="grid grid-cols-2 gap-2">
            {metrics.map(([label, value]) => (
              <div key={label} className="rounded-xl border border-slate-200 bg-white px-3 py-3">
                <div className="text-[11px] text-slate-500">{label}</div>
                <div className="mt-1 text-base font-semibold tabular-nums text-slate-900">{value == null ? "—" : formatQuantity(value)}</div>
              </div>
            ))}
          </section>
          <section className="rounded-xl border border-slate-200 bg-white">
            <div className="border-b border-slate-100 px-4 py-3 text-xs font-semibold text-slate-700">最近库存流水</div>
            {error && <div role="alert" className="m-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}
            {loading && <div className="px-4 py-8 text-center text-sm text-slate-400">正在加载流水…</div>}
            {!loading && !error && !rows.length && <div className="px-4 py-8 text-center text-sm text-slate-400">暂无库存流水</div>}
            {!loading && !error && rows.length > 0 && (
              <div className="divide-y divide-slate-100">
                {rows.slice(0, 8).map((item, index) => (
                  <div key={`${item.documentNo}-${index}`} className="flex items-start justify-between gap-3 px-4 py-3">
                    <div>
                      <div className="text-sm text-slate-700">{item.direction === "inbound" ? "入库" : "出库"}{item.documentNo ? <span className="ml-1.5 font-mono text-[11px] text-slate-400">{item.documentNo}</span> : null}</div>
                      <div className="mt-1 text-[11px] text-slate-400">{formatDate(item.occurredAt)}{item.warehouseName ? ` · ${item.warehouseName}` : ""}</div>
                    </div>
                    <div className={`whitespace-nowrap text-sm font-medium tabular-nums ${item.direction === "inbound" ? "text-emerald-600" : "text-rose-500"}`}>{item.quantity > 0 ? "+" : ""}{formatQuantity(item.quantity)} {row.unit}</div>
                  </div>
                ))}
              </div>
            )}
          </section>
          <button type="button" onClick={() => onOpenTransactions(inbound, outbound, balance ?? 0)} className="block w-full rounded-lg border border-slate-200 px-3 py-2.5 text-center text-sm font-medium text-blue-600 hover:border-blue-200 hover:bg-blue-50">查看全部流水</button>
        </div>
      </aside>
    </div>
  );
}

function InventoryMetric({ label, value, hint, tone, icon }: { label: string; value: string; hint: string; tone: "blue" | "indigo" | "amber" | "rose" | "emerald" | "violet"; icon: string }) {
  const toneClass = {
    blue: "bg-blue-50 text-blue-600",
    indigo: "bg-indigo-50 text-indigo-600",
    amber: "bg-amber-50 text-amber-600",
    rose: "bg-rose-50 text-rose-600",
    emerald: "bg-emerald-50 text-emerald-600",
    violet: "bg-violet-50 text-violet-600",
  }[tone];
  return <div className="flex min-h-[104px] items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"><div><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div><div className="mt-1 text-[11px] text-slate-400">{hint}</div></div><span className={`flex h-10 w-10 items-center justify-center rounded-xl text-lg font-semibold ${toneClass}`}>{icon}</span></div>;
}
