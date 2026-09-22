"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { consumablesApi, type ConsumableRow, type ConsumableTransactionRow } from "@/lib/api";
import InventoryTransactionModal from "./transaction-modal";

type InventoryTab = "goods" | "bundle" | "consumables";
type StatusFilter = "all" | "normal" | "low" | "shortage";
type AlertFilter = "all" | "warning" | "normal";

type Props = {
  rows: ConsumableRow[];
  loading: boolean;
  error: string;
  onReload: () => void;
  onTabChange: (tab: InventoryTab) => void;
};

const TABS: Array<{ key: InventoryTab; label: string }> = [
  { key: "goods", label: "正品库存" },
  { key: "bundle", label: "虚拟组合库存" },
  { key: "consumables", label: "耗材库存" },
];

function numberValue(value: string | number | null | undefined) {
  if (value == null || value === "") return 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatQuantity(value: string | number | null | undefined) {
  return numberValue(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function cleanName(name: string) {
  return name.replace(/^耗材[\s-]*/u, "").trim() || name;
}

function mappingDetails(row: ConsumableRow) {
  return row.mappingDetails ?? row.linkedSkus.map((sku) => ({ ...sku, usagePerUnit: null }));
}

function usageRatio(row: ConsumableRow) {
  const total = numberValue(row.totalStockQty ?? row.purchasedQty);
  const used = numberValue(row.totalUsedQty ?? row.usedQty);
  return total > 0 ? (used / total) * 100 : 0;
}

function coverage(row: ConsumableRow) {
  return row.coveragePct == null || row.coveragePct === "" ? null : numberValue(row.coveragePct);
}

function statusLabel(row: ConsumableRow) {
  if (row.inventoryStatus) return row.inventoryStatus;
  const value = coverage(row);
  if (value == null || value >= 100) return "正常";
  return value >= 60 ? "偏低" : "缺货";
}

function statusClass(label: string) {
  if (label === "正常") return "bg-emerald-50 text-emerald-700";
  if (label === "偏低") return "bg-amber-50 text-amber-700";
  return "bg-rose-50 text-rose-700";
}

function coverageClass(value: number | null) {
  if (value == null || value >= 100) return "text-emerald-600";
  if (value >= 60) return "text-amber-600";
  return "text-rose-600";
}

/** 覆盖率单元格：待生产为 0 时无缺口可覆盖，显示「充足」而不是空值。 */
function coverageCell(row: ConsumableRow) {
  const value = coverage(row);
  if (value != null) return <span className={`font-medium tabular-nums ${coverageClass(value)}`}>{value.toFixed(0)}%</span>;
  if (numberValue(row.pendingProductionQty) <= 0) return <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">充足</span>;
  return <span className="text-slate-400">—</span>;
}

function sourceLabel(row: ConsumableTransactionRow) {
  if (row.sourceType === "consumable_receipt") return "采购入库";
  if (row.sourceType === "inbound_link") return "商品入库耗用";
  if (row.transactionType === "loss") return "报损";
  if (["manual", "stocktake", "adjustment"].includes(row.transactionType)) return "盘点调整";
  return row.transactionType || "库存流水";
}

function signedQuantity(row: ConsumableTransactionRow) {
  const value = numberValue(row.quantity);
  const negative = ["consume", "loss", "send_factory"].includes(row.transactionType);
  return `${negative ? "−" : "+"}${formatQuantity(Math.abs(value))}`;
}

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}

export default function ConsumableInventoryView({ rows, loading, error, onReload, onTabChange }: Props) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [alert, setAlert] = useState<AlertFilter>("all");
  const [boundSku, setBoundSku] = useState("all");
  const [selected, setSelected] = useState<ConsumableRow | null>(null);
  const [txTarget, setTxTarget] = useState<{ id: number; name: string; code: string; unit: string } | null>(null);
  const [transactions, setTransactions] = useState<ConsumableTransactionRow[]>([]);
  const [transactionsLoading, setTransactionsLoading] = useState(false);
  const [drawerError, setDrawerError] = useState("");

  const boundOptions = useMemo(() => {
    const items = rows.flatMap((row) => mappingDetails(row));
    return [...new Map(items.map((item) => [item.skuCode, item])).values()].sort((a, b) => a.skuCode.localeCompare(b.skuCode));
  }, [rows]);

  const visibleRows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return rows.filter((row) => {
      const mappings = mappingDetails(row);
      const text = [row.code, row.barcode, cleanName(row.name), ...mappings.flatMap((item) => [item.skuCode, item.skuName])].join(" ").toLowerCase();
      const rowStatus = statusLabel(row);
      const textMatch = !term || text.includes(term);
      const statusMatch = status === "all"
        || (status === "normal" && rowStatus === "正常")
        || (status === "low" && rowStatus === "偏低")
        || (status === "shortage" && rowStatus === "缺货");
      const alertMatch = alert === "all" || (alert === "warning" ? rowStatus !== "正常" : rowStatus === "正常");
      const boundMatch = boundSku === "all" || mappings.some((item) => item.skuCode === boundSku);
      return textMatch && statusMatch && alertMatch && boundMatch;
    });
  }, [alert, boundSku, rows, search, status]);

  const metrics = useMemo(() => ({
    total: rows.reduce((sum, row) => sum + numberValue(row.totalStockQty ?? row.purchasedQty), 0),
    used: rows.reduce((sum, row) => sum + numberValue(row.totalUsedQty ?? row.usedQty), 0),
    current: rows.reduce((sum, row) => sum + numberValue(row.currentStockQty ?? row.availableQty), 0),
    transit: rows.reduce((sum, row) => sum + numberValue(row.transitQty), 0),
    warning: rows.filter((row) => statusLabel(row) !== "正常").length,
    count: rows.length,
  }), [rows]);

  useEffect(() => {
    if (!selected) {
      setTransactions([]);
      return;
    }
    let cancelled = false;
    setTransactionsLoading(true);
    setDrawerError("");
    consumablesApi.transactions(selected.id)
      .then((items) => { if (!cancelled) setTransactions(items); })
      .catch((caught) => { if (!cancelled) setDrawerError(caught instanceof Error ? caught.message : String(caught)); })
      .finally(() => { if (!cancelled) setTransactionsLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  function exportRows() {
    const header = ["耗材编号", "耗材名称", "绑定正品编号", "绑定正品名称", "总库存", "累计已用", "当前库存", "使用占比", "可支撑正品数", "覆盖率", "状态"];
    const lines = visibleRows.map((row) => {
      const mappings = mappingDetails(row);
      const codes = mappings.map((item) => item.skuCode).join(" + ");
      const names = mappings.map((item) => item.skuName).join(" + ");
      const coverageValue = coverage(row);
      return [
        row.code,
        cleanName(row.name),
        codes,
        names,
        numberValue(row.totalStockQty ?? row.purchasedQty),
        numberValue(row.totalUsedQty ?? row.usedQty),
        numberValue(row.currentStockQty ?? row.availableQty),
        `${usageRatio(row).toFixed(2)}%`,
        row.supportQty == null ? "" : numberValue(row.supportQty),
        coverageValue == null ? "" : `${coverageValue.toFixed(2)}%`,
        statusLabel(row),
      ].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",");
    });
    const blob = new Blob(["\ufeff", [header, ...lines].map((line) => Array.isArray(line) ? line.map((value) => `"${String(value).replaceAll('"', '""')}"`).join(",") : line).join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `耗材库存-${new Date().toISOString().slice(0, 10)}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-3 pb-8">
      <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600">库存中心 / 库存总览</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">耗材库存</h1>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Link href="/inventory/operations?tab=receipt" className="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-medium text-white shadow-sm shadow-blue-200 transition hover:bg-blue-700">耗材采购入库</Link>
            <Link href="/inventory/operations?tab=usage" className="rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2 text-xs font-medium text-amber-700 transition hover:bg-amber-100">自动耗材流水</Link>
            <button type="button" onClick={exportRows} className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 transition hover:border-blue-200 hover:text-blue-600">库存导出</button>
            <Link href="/inventory/transactions?kind=consumable" className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 transition hover:border-blue-200 hover:text-blue-600">查看流水</Link>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2 text-xs leading-5 text-blue-800">
          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-blue-600 text-[10px] font-bold text-white">i</span>
          <span>覆盖率说明：覆盖率 = 当前可用耗材 ÷ 待生产所需耗材 × 100%　|　可支撑正品数 = 当前耗材库存 ÷ 单件正品耗材用量　|　≥100% 表示库存充足，&lt;100% 表示存在耗材缺口　|　暂无待生产订单时显示「充足」</span>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6" aria-label="耗材库存指标">
        <ConsumableMetric label="总库存" value={formatQuantity(metrics.total)} hint="历史累计入库总量" tone="blue" icon="▣" />
        <ConsumableMetric label="累计已用" value={formatQuantity(metrics.used)} hint="历史累计领用、消耗数量" tone="emerald" icon="▥" />
        <ConsumableMetric label="当前库存" value={formatQuantity(metrics.current)} hint="当前可用耗材库存" tone="violet" icon="◆" />
        <ConsumableMetric label="在途数量" value={formatQuantity(metrics.transit)} hint="已采购尚未入库数量" tone="sky" icon="▰" />
        <ConsumableMetric label="低库存预警" value={String(metrics.warning)} hint="偏低或缺货的耗材" tone="amber" icon="!" />
        <ConsumableMetric label="耗材 SKU 数" value={String(metrics.count)} hint="当前在用的耗材种类" tone="indigo" icon="≋" />
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-100 px-4 pt-1">
          <div className="flex gap-6 overflow-x-auto">
            {TABS.map((item) => <button key={item.key} type="button" onClick={() => onTabChange(item.key)} className={`whitespace-nowrap border-b-2 px-1 py-3 text-sm font-medium transition ${item.key === "consumables" ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{item.label}</button>)}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索耗材编号 / 耗材名称 / 绑定正品" className="h-9 w-[300px] rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-400" />
          <select aria-label="耗材状态筛选" value={status} onChange={(event) => setStatus(event.target.value as StatusFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">状态：全部</option><option value="normal">状态：正常</option><option value="low">状态：偏低</option><option value="shortage">状态：缺货</option></select>
          <select aria-label="耗材预警筛选" value={alert} onChange={(event) => setAlert(event.target.value as AlertFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">预警：全部</option><option value="warning">预警：已预警</option><option value="normal">预警：未预警</option></select>
          <select aria-label="绑定正品筛选" value={boundSku} onChange={(event) => setBoundSku(event.target.value)} className="h-9 max-w-[250px] rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">绑定正品：全部</option>{boundOptions.map((item) => <option key={item.skuCode} value={item.skuCode}>{item.skuCode} · {item.skuName}</option>)}</select>
          <span className="ml-auto text-xs text-slate-400">显示 {visibleRows.length} / {rows.length} 条</span>
        </div>

        {error && <div role="alert" className="mx-4 mt-3 flex items-center justify-between gap-3 rounded-xl border border-rose-100 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700"><span>{error}</span><button type="button" onClick={onReload} className="shrink-0 font-medium text-rose-700 hover:underline">重新加载</button></div>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1460px] table-fixed text-left text-[13px]">
            <colgroup>
              <col className="w-[10%]" />
              <col className="w-[12%]" />
              <col className="w-[11%]" />
              <col className="w-[14%]" />
              <col className="w-[6%]" />
              <col className="w-[6%]" />
              <col className="w-[7%]" />
              <col className="w-[11%]" />
              <col className="w-[7%]" />
              <col className="w-[6%]" />
              <col className="w-[5%]" />
              <col className="w-[5%]" />
            </colgroup>
            <thead className="bg-slate-50 text-[11px] font-medium text-slate-500">
              <tr className="border-b border-slate-100 text-slate-600">
                <th colSpan={2} className="px-4 py-2.5">耗材信息</th>
                <th colSpan={2} className="border-l border-slate-100 px-3 py-2.5">绑定正品信息</th>
                <th colSpan={7} className="border-l border-slate-100 px-3 py-2.5">库存与覆盖</th>
                <th rowSpan={2} className="border-l border-slate-100 px-4 py-2.5 text-right align-middle">操作</th>
              </tr>
              <tr>
                <th className="px-4 py-2.5">耗材编号</th>
                <th className="px-3 py-2.5">耗材名称</th>
                <th className="border-l border-slate-100 px-3 py-2.5">绑定正品编号</th>
                <th className="px-3 py-2.5">绑定正品名称</th>
                <th className="border-l border-slate-100 px-3 py-2.5 text-right">总库存</th>
                <th className="px-3 py-2.5 text-right">累计已用</th>
                <th className="px-3 py-2.5 text-right">当前库存</th>
                <th className="px-3 py-2.5">使用占比</th>
                <th className="px-3 py-2.5 text-right">可支撑正品数</th>
                <th className="px-3 py-2.5 text-right">覆盖率</th>
                <th className="px-3 py-2.5">状态</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleRows.map((row) => <ConsumableInventoryRow key={row.id} row={row} onOpen={() => setSelected(row)} onTransactions={() => setTxTarget({ id: row.id, name: cleanName(row.name), code: row.code, unit: row.unit })} />)}
            </tbody>
          </table>
          {loading && <div className="p-12 text-center text-sm text-slate-400">正在加载耗材库存…</div>}
          {!loading && !visibleRows.length && <div className="p-12 text-center text-sm text-slate-400">没有匹配的耗材库存记录</div>}
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-xs text-slate-400"><span>共 {visibleRows.length} 条数据</span><span>耗材统一归属耗材库；库存来自采购收货与自动耗材耗用流水。</span></div>
      </section>

      {selected && <ConsumableDetailDrawer row={selected} transactions={transactions} loading={transactionsLoading} error={drawerError} onClose={() => setSelected(null)} onOpenTransactions={() => setTxTarget({ id: selected.id, name: cleanName(selected.name), code: selected.code, unit: selected.unit })} />}
      {txTarget && <InventoryTransactionModal kind="consumable" id={txTarget.id} title={txTarget.name} subtitle={txTarget.code} unit={txTarget.unit} onClose={() => setTxTarget(null)} />}
    </div>
  );
}

function ConsumableInventoryRow({ row, onOpen, onTransactions }: { row: ConsumableRow; onOpen: () => void; onTransactions: () => void }) {
  const mappings = mappingDetails(row);
  const coverageValue = coverage(row);
  const useRatio = usageRatio(row);
  const label = statusLabel(row);
  return (
    <tr className="transition-colors hover:bg-slate-50/70">
      <td className="truncate px-4 py-3 font-mono text-xs text-slate-700" title={row.code}>{row.code}</td>
      <td className="px-3 py-3"><div className="truncate font-medium text-slate-800" title={cleanName(row.name)}>{cleanName(row.name)}</div></td>
      <td className="truncate border-l border-slate-100 px-3 py-3 font-mono text-xs text-slate-600" title={mappings[0]?.skuCode || ""}>{mappings[0]?.skuCode || "—"}{mappings.length > 1 && <span className="ml-1 text-[11px] text-slate-400">+{mappings.length - 1}</span>}</td>
      <td className="px-3 py-3 text-slate-700"><div className="truncate" title={mappings[0]?.skuName}>{mappings[0]?.skuName || "未绑定正品"}</div></td>
      <td className="border-l border-slate-100 px-3 py-3 text-right tabular-nums text-slate-700">{formatQuantity(row.totalStockQty ?? row.purchasedQty)}</td>
      <td className="px-3 py-3 text-right tabular-nums text-slate-700">{formatQuantity(row.totalUsedQty ?? row.usedQty)}</td>
      <td className="px-3 py-3 text-right font-medium tabular-nums text-slate-900">{formatQuantity(row.currentStockQty ?? row.availableQty)}</td>
      <td className="px-3 py-3"><div className="flex items-center gap-2"><span className="h-2 w-20 shrink-0 overflow-hidden rounded-full bg-slate-100"><span className="block h-full rounded-full bg-emerald-500" style={{ width: `${Math.min(Math.max(useRatio, 0), 100)}%` }} /></span><span className="whitespace-nowrap text-xs tabular-nums text-slate-600">{useRatio.toFixed(0)}%</span></div></td>
      <td className="px-3 py-3 text-right tabular-nums text-slate-700">{row.supportQty == null ? "—" : formatQuantity(row.supportQty)}</td>
      <td className="px-3 py-3 text-right">{coverageCell(row)}</td>
      <td className="px-3 py-3"><span className={`whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-medium ${statusClass(label)}`}>{label}</span></td>
      <td className="whitespace-nowrap border-l border-slate-100 px-4 py-3 text-right"><button type="button" onClick={onOpen} className="px-1.5 text-xs font-medium text-blue-600 hover:underline">详情</button><button type="button" onClick={onTransactions} className="px-1.5 text-xs text-slate-500 hover:text-blue-600">流水</button></td>
    </tr>
  );
}

function ConsumableDetailDrawer({ row, transactions, loading, error, onClose, onOpenTransactions }: { row: ConsumableRow; transactions: ConsumableTransactionRow[]; loading: boolean; error: string; onClose: () => void; onOpenTransactions: () => void }) {
  const mappings = mappingDetails(row);
  const coverageValue = coverage(row);
  const coverageDisplay = coverageValue != null ? `${coverageValue.toFixed(2)}%` : numberValue(row.pendingProductionQty) <= 0 ? "充足" : "—";
  const metrics = [
    ["总库存", row.totalStockQty ?? row.purchasedQty],
    ["累计已用", row.totalUsedQty ?? row.usedQty],
    ["当前库存", row.currentStockQty ?? row.availableQty],
    ["在途数量", row.transitQty],
    ["待生产需耗材", row.pendingProductionQty ?? "0"],
    ["可支撑正品数", row.supportQty],
    ["覆盖率", coverageDisplay],
    ["耗材缺口", row.gapQty ?? "0"],
  ];
  return (
    <div className="fixed inset-0 z-modal flex justify-end" role="dialog" aria-modal="true" aria-label="耗材库存详情">
      <button type="button" aria-label="关闭详情" onClick={onClose} className="absolute inset-0 bg-slate-950/25" />
      <aside className="relative h-full w-full max-w-[480px] overflow-y-auto border-l border-slate-200 bg-white shadow-2xl">
        <div className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-100 bg-white px-5 py-4"><div><div className="text-[11px] font-medium tracking-wide text-blue-600">耗材库存 / 详情</div><h2 className="mt-1 text-lg font-semibold text-slate-900">{cleanName(row.name)}</h2><div className="mt-1 font-mono text-xs text-slate-500">{row.code}</div></div><button type="button" onClick={onClose} className="rounded-lg px-2 py-1 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button></div>
        <div className="space-y-4 p-5">
          <section className="rounded-xl border border-slate-200 bg-slate-50/60 p-4"><div className="text-xs font-semibold text-slate-700">绑定正品</div><div className="mt-3 space-y-2">{mappings.length ? mappings.map((item) => <div key={item.skuCode} className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2"><div><div className="font-mono text-xs text-slate-600">{item.skuCode}</div><div className="mt-0.5 text-sm text-slate-800">{item.skuName}</div></div><span className="text-xs text-slate-500">单件用量 {item.usagePerUnit ?? "—"}</span></div>) : <div className="text-sm text-slate-400">尚未绑定正品</div>}</div></section>
          <section className="grid grid-cols-2 gap-2">{metrics.map(([label, value]) => <div key={label} className="rounded-xl border border-slate-200 bg-white px-3 py-3"><div className="text-[11px] text-slate-500">{label}</div><div className={`mt-1 text-base font-semibold tabular-nums ${label === "覆盖率" ? (coverageDisplay === "充足" ? "text-emerald-600" : coverageClass(coverageValue)) : "text-slate-900"}`}>{typeof value === "string" && !Number.isFinite(Number(value)) ? value : value == null || value === "" ? "—" : formatQuantity(value)}</div></div>)}</section>
          <section className="rounded-xl border border-slate-200 bg-white"><div className="border-b border-slate-100 px-4 py-3 text-xs font-semibold text-slate-700">最近库存流水</div>{error && <div role="alert" className="m-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}{loading && <div className="px-4 py-8 text-center text-sm text-slate-400">正在加载流水…</div>}{!loading && !error && !transactions.length && <div className="px-4 py-8 text-center text-sm text-slate-400">暂无库存流水</div>}{!loading && !error && transactions.length > 0 && <div className="divide-y divide-slate-100">{transactions.slice(0, 8).map((item) => <div key={item.id} className="flex items-start justify-between gap-3 px-4 py-3"><div><div className="text-sm text-slate-700">{sourceLabel(item)}</div><div className="mt-1 text-[11px] text-slate-400">{formatDate(item.occurredAt)}{item.inboundDocumentNo ? ` · ${item.inboundDocumentNo}` : ""}</div></div><div className={`whitespace-nowrap text-sm font-medium tabular-nums ${item.transactionType === "consume" || item.transactionType === "loss" ? "text-amber-600" : "text-emerald-600"}`}>{signedQuantity(item)} {row.unit}</div></div>)}</div>}</section>
          <button type="button" onClick={onOpenTransactions} className="block w-full rounded-lg border border-slate-200 px-3 py-2.5 text-center text-sm font-medium text-blue-600 hover:border-blue-200 hover:bg-blue-50">查看全部流水</button>
        </div>
      </aside>
    </div>
  );
}

function ConsumableMetric({ label, value, hint, tone, icon }: { label: string; value: string; hint: string; tone: "blue" | "emerald" | "violet" | "sky" | "amber" | "indigo"; icon: string }) {
  const toneClass = {
    blue: "bg-blue-50 text-blue-600",
    emerald: "bg-emerald-50 text-emerald-600",
    violet: "bg-violet-50 text-violet-600",
    sky: "bg-sky-50 text-sky-600",
    amber: "bg-amber-50 text-amber-600",
    indigo: "bg-indigo-50 text-indigo-600",
  }[tone];
  return <div className="flex min-h-[104px] items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm"><div><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div><div className="mt-1 text-[11px] text-slate-400">{hint}</div></div><span className={`flex h-10 w-10 items-center justify-center rounded-xl text-lg font-semibold ${toneClass}`}>{icon}</span></div>;
}
