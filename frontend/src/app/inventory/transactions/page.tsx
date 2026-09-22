"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { consumablesApi, dashboardApi, type ConsumableRow, type ConsumableTransactionRow, type InventorySkuRow, type SkuTransactionRow } from "@/lib/api";

type SourceFilter = "all" | "purchase" | "inbound_link" | "other";

const TX_LABELS: Record<string, string> = {
  purchase: "采购单收货入库",
  send_factory: "发往工厂",
  factory_receive: "工厂收货",
  consume: "耗材消耗",
  stocktake: "盘点/异常调整",
  loss: "报损",
  manual: "盘点/异常调整",
  adjustment: "盘点/异常调整",
};

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN");
}

function formatQty(value: string | number | null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "—";
}

function sourceMeta(row: ConsumableTransactionRow) {
  if (row.sourceType === "consumable_receipt") {
    return { label: "采购单收货入库", detail: row.purchaseId ? `耗材采购单 #${row.purchaseId}` : "耗材采购收货", className: "bg-emerald-50 text-emerald-700" };
  }
  if (row.sourceType === "inbound_link") {
    const detail = row.inboundDocumentNo
      ? `商品入库单 ${row.inboundDocumentNo}${row.inboundWarehouseName ? ` · ${row.inboundWarehouseName}` : ""}`
      : "商品入库单耗材关联";
    return { label: "商品入库耗用", detail, className: "bg-amber-50 text-amber-700" };
  }
  if (row.sourceType === "production_material_movement") {
    return { label: row.transactionType === "consume" ? "生产耗用" : "生产耗材流转", detail: "生产耗材流转单", className: "bg-indigo-50 text-indigo-700" };
  }
  if (row.sourceType === "manual" || row.transactionType === "manual" || row.transactionType === "stocktake" || row.transactionType === "adjustment") {
    return { label: "盘点/异常调整", detail: "非日常出库来源", className: "bg-slate-100 text-slate-600" };
  }
  return { label: TX_LABELS[row.transactionType] ?? row.transactionType, detail: row.sourceType || "未记录来源", className: "bg-blue-50 text-blue-700" };
}

function sourceGroup(row: ConsumableTransactionRow): Exclude<SourceFilter, "all"> {
  if (row.sourceType === "consumable_receipt") return "purchase";
  if (row.sourceType === "inbound_link") return "inbound_link";
  return "other";
}

function signedQuantity(row: ConsumableTransactionRow) {
  const raw = Number(row.quantity);
  if (!Number.isFinite(raw)) return "—";
  if (row.transactionType === "manual" || row.transactionType === "stocktake" || row.transactionType === "adjustment") {
    return `${raw > 0 ? "+" : ""}${formatQty(raw)}`;
  }
  const negative = row.transactionType === "consume" || row.transactionType === "loss" || row.transactionType === "send_factory";
  return `${negative ? "-" : "+"}${formatQty(Math.abs(raw))}`;
}

function GoodsTransactions({ requestedId }: { requestedId: number | null }) {
  const [skus, setSkus] = useState<InventorySkuRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(requestedId);
  const [rows, setRows] = useState<SkuTransactionRow[]>([]);
  const [balance, setBalance] = useState(0);
  const [skuInfo, setSkuInfo] = useState<{ skuCode: string; skuName: string; unit: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingRows, setLoadingRows] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    dashboardApi.inventorySkus("", 2000)
      .then((items) => {
        setSkus(items);
        setSelectedId((current) => (current && items.some((item) => item.skuId === current)
          ? current
          : (items.find((item) => item.hasMovement) ?? items[0])?.skuId ?? null));
      })
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setRows([]);
      setSkuInfo(null);
      return;
    }
    setLoadingRows(true);
    dashboardApi.skuTransactions(selectedId)
      .then((payload) => {
        setRows(payload.rows);
        setBalance(payload.balance);
        setSkuInfo(payload.sku);
      })
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoadingRows(false));
  }, [selectedId]);

  const inboundCount = useMemo(() => rows.filter((row) => row.direction === "inbound").length, [rows]);
  const outboundCount = rows.length - inboundCount;

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
        <label className="text-xs font-medium text-slate-600" htmlFor="sku-select">正品</label>
        <select id="sku-select" value={selectedId ?? ""} onChange={(event) => setSelectedId(Number(event.target.value) || null)} className="h-9 min-w-72 max-w-[480px] rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-400">
          <option value="">选择货品</option>
          {skus.map((row) => <option key={row.skuId} value={row.skuId}>{row.skuCode} · {row.goodsName || row.skuName}</option>)}
        </select>
        {skuInfo && <span className="text-xs text-slate-400">当前结存 {balance.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} {skuInfo.unit}</span>}
        <div className="ml-auto flex flex-wrap gap-1.5 text-[11px]"><span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">入库 {inboundCount}</span><span className="rounded-full bg-rose-50 px-2 py-1 text-rose-600">出库 {outboundCount}</span></div>
      </div>
      {error && <div role="alert" className="mx-4 mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700">{error}</div>}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-[13px]">
          <thead className="bg-slate-50 text-[11px] font-medium text-slate-500"><tr><th className="px-4 py-3">发生时间</th><th className="px-3 py-3">业务来源</th><th className="px-3 py-3">仓库</th><th className="px-3 py-3 text-right">变动数量</th><th className="px-3 py-3">结存变化</th><th className="px-3 py-3">备注</th></tr></thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row, index) => (
              <tr key={`${row.documentNo}-${index}`} className="hover:bg-slate-50/70">
                <td className="px-4 py-3 text-xs text-slate-500">{formatDate(row.occurredAt)}</td>
                <td className="px-3 py-3">
                  <span className={`rounded-full px-2 py-1 text-[11px] font-medium ${row.direction === "inbound" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-600"}`}>{row.direction === "inbound" ? "入库" : "出库"}</span>
                  <div className="mt-1 max-w-[300px] truncate font-mono text-[11px] text-slate-400" title={row.documentNo}>{row.documentNo || "—"}</div>
                </td>
                <td className="px-3 py-3 text-xs text-slate-600">{row.warehouseName || "—"}</td>
                <td className={`px-3 py-3 text-right font-medium tabular-nums ${row.direction === "inbound" ? "text-emerald-600" : "text-rose-500"}`}>{row.quantity > 0 ? "+" : ""}{formatQty(row.quantity)} {skuInfo?.unit ?? ""}</td>
                <td className="px-3 py-3 text-xs tabular-nums text-slate-500">{formatQty(row.balanceBefore)} → {formatQty(row.balanceAfter)}</td>
                <td className="max-w-[340px] px-3 py-3 text-xs text-slate-500"><div className="truncate" title={[row.supplierName, row.companyName].filter(Boolean).join(" · ")}>{[row.supplierName, row.companyName].filter(Boolean).join(" · ") || "—"}</div></td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <div className="p-12 text-center text-sm text-slate-400">正在加载货品档案…</div>}
        {!loading && loadingRows && <div className="p-12 text-center text-sm text-slate-400">正在加载流水…</div>}
        {!loading && !loadingRows && selectedId && !rows.length && <div className="p-12 text-center text-sm text-slate-400">该货品暂无出入库单据记录</div>}
        {!loading && !selectedId && <div className="p-12 text-center text-sm text-slate-400">请选择一个货品查看流水</div>}
      </div>
      <div className="border-t border-slate-100 bg-blue-50/60 px-4 py-3 text-xs leading-5 text-blue-800">正品流水来自本地吉客云入库/出库单明细，结存按「采购入库 − 销售出库」逐笔累计，与库存总览口径一致。</div>
    </section>
  );
}

export default function InventoryTransactionsPage() {
  const searchParams = useSearchParams();
  const kind = searchParams.get("kind");
  const goodsRequested = kind === "goods";
  const requestedId = Number(searchParams.get("id"));
  const [rows, setRows] = useState<ConsumableRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(Number.isInteger(requestedId) && requestedId > 0 ? requestedId : null);
  const [transactions, setTransactions] = useState<ConsumableTransactionRow[]>([]);
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [loading, setLoading] = useState(true);
  const [loadingTransactions, setLoadingTransactions] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (goodsRequested) {
      setRows([]);
      setSelectedId(null);
      setTransactions([]);
      setLoading(false);
      return;
    }
    consumablesApi.list("")
      .then((items) => {
        setRows(items);
        setSelectedId((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? null);
      })
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoading(false));
  }, [goodsRequested]);

  useEffect(() => {
    if (!selectedId || goodsRequested) {
      setTransactions([]);
      return;
    }
    setLoadingTransactions(true);
    consumablesApi.transactions(selectedId)
      .then(setTransactions)
      .catch((caught) => setError(String(caught)))
      .finally(() => setLoadingTransactions(false));
  }, [goodsRequested, selectedId]);

  const selected = useMemo(() => rows.find((row) => row.id === selectedId) ?? null, [rows, selectedId]);
  const visibleTransactions = useMemo(
    () => transactions.filter((row) => sourceFilter === "all" || sourceGroup(row) === sourceFilter),
    [sourceFilter, transactions],
  );
  const purchaseCount = transactions.filter((row) => row.sourceType === "consumable_receipt").length;
  const usageCount = transactions.filter((row) => row.sourceType === "inbound_link").length;
  const adjustmentCount = transactions.filter((row) => row.sourceType === "manual" || row.transactionType === "stocktake" || row.transactionType === "adjustment").length;

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 pb-8">
      <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><div className="text-[11px] font-medium tracking-wide text-blue-600">库存中心 / 库存流水</div><h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">库存流水</h1><p className="mt-1 text-sm leading-6 text-slate-500">正品流水来自吉客云入库/出库单明细；耗材正常入库来自采购单实际收货，正品实际入库后按货品映射自动生成耗用。</p></div>
          <Link href="/inventory" className="rounded-lg border border-slate-200 px-3.5 py-2 text-xs font-medium text-slate-600 hover:border-blue-200 hover:text-blue-600">返回库存总览</Link>
        </div>
      </header>

      {goodsRequested ? (
        <GoodsTransactions requestedId={Number.isInteger(requestedId) && requestedId > 0 ? requestedId : null} />
      ) : (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
            <label className="text-xs font-medium text-slate-600" htmlFor="consumable-select">耗材</label>
            <select id="consumable-select" value={selectedId ?? ""} onChange={(event) => setSelectedId(Number(event.target.value) || null)} className="h-9 min-w-72 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-400"><option value="">选择耗材</option>{rows.map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}</select>
            {selected && <span className="text-xs text-slate-400">当前库存 {selected.availableQty} {selected.unit}</span>}
            <div className="ml-auto flex flex-wrap gap-1.5 text-[11px]"><span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-700">采购入库 {purchaseCount}</span><span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">自动耗材耗用 {usageCount}</span><span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">盘点/异常 {adjustmentCount}</span></div>
          </div>
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
            <span className="text-xs text-slate-500">来源</span>
            <select aria-label="流水来源筛选" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value as SourceFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">全部来源</option><option value="purchase">采购单收货入库</option><option value="inbound_link">自动耗材耗用</option><option value="other">其他流转或调整</option></select>
            <span className="ml-auto text-xs text-slate-400">显示 {visibleTransactions.length} / {transactions.length} 条</span>
          </div>
          {error && <div role="alert" className="mx-4 mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700">{error}</div>}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-[13px]">
              <thead className="bg-slate-50 text-[11px] font-medium text-slate-500"><tr><th className="px-4 py-3">发生时间</th><th className="px-3 py-3">业务来源</th><th className="px-3 py-3">仓库</th><th className="px-3 py-3 text-right">变动数量</th><th className="px-3 py-3">库存变化</th><th className="px-3 py-3">备注</th></tr></thead>
              <tbody className="divide-y divide-slate-100">{visibleTransactions.map((row) => { const source = sourceMeta(row); return <tr key={row.id} className="hover:bg-slate-50/70"><td className="px-4 py-3 text-xs text-slate-500">{formatDate(row.occurredAt)}</td><td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[11px] font-medium ${source.className}`}>{source.label}</span><div className="mt-1 max-w-[300px] truncate text-[11px] text-slate-400" title={source.detail}>{source.detail}</div></td><td className="px-3 py-3 text-xs text-slate-600">{row.warehouseName || (row.location === "factory" ? "工厂仓" : "自有仓")}</td><td className={`px-3 py-3 text-right font-medium tabular-nums ${row.transactionType === "consume" || row.transactionType === "loss" || row.transactionType === "send_factory" ? "text-amber-600" : "text-emerald-600"}`}>{signedQuantity(row)} {selected?.unit ?? ""}</td><td className="px-3 py-3 text-xs tabular-nums text-slate-500">{row.stockBefore != null ? `${formatQty(row.stockBefore)} → ${formatQty(row.stockAfter)}` : row.factoryBefore != null ? `${formatQty(row.factoryBefore)} → ${formatQty(row.factoryAfter)}` : "—"}</td><td className="max-w-[340px] px-3 py-3 text-xs text-slate-500"><div className="truncate" title={row.note}>{row.note || "—"}</div></td></tr>; })}</tbody>
            </table>
            {loading && <div className="p-12 text-center text-sm text-slate-400">正在加载耗材档案…</div>}
            {!loading && loadingTransactions && <div className="p-12 text-center text-sm text-slate-400">正在加载流水…</div>}
            {!loading && !loadingTransactions && selectedId && !visibleTransactions.length && <div className="p-12 text-center text-sm text-slate-400">该筛选条件下暂无流水记录</div>}
            {!loading && !selectedId && <div className="p-12 text-center text-sm text-slate-400">请选择一个耗材查看流水</div>}
          </div>
          <div className="border-t border-slate-100 bg-blue-50/60 px-4 py-3 text-xs leading-5 text-blue-800">正常链路：耗材采购单实际收货 → 采购入库；正品实际入库 → 按映射自动生成耗材耗用。盘点/异常调整不会被当作日常出库。</div>
        </section>
      )}
    </div>
  );
}
