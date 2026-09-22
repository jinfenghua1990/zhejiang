"use client";

import { useEffect, useState } from "react";
import { consumablesApi, dashboardApi, type ConsumableTransactionRow, type SkuTransactionRow } from "@/lib/api";
import { useTabActive } from "@/lib/workspace/tab-store";

type Props = {
  kind: "goods" | "consumable";
  id: number | null;
  title: string;
  subtitle?: string;
  unit?: string;
  onClose: () => void;
};

function formatDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}

function formatQty(value: string | number | null | undefined) {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 }) : "—";
}

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

const NEGATIVE_TX = new Set(["consume", "loss", "send_factory"]);
const MANUAL_TX = new Set(["manual", "stocktake", "adjustment"]);

function consumableSource(row: ConsumableTransactionRow) {
  if (row.sourceType === "consumable_receipt") {
    return { label: "采购单收货入库", detail: row.purchaseId ? `耗材采购单 #${row.purchaseId}` : "耗材采购收货" };
  }
  if (row.sourceType === "inbound_link") {
    return { label: "商品入库耗用", detail: row.inboundDocumentNo ? `商品入库单 ${row.inboundDocumentNo}` : "商品入库单耗材关联" };
  }
  if (row.sourceType === "production_material_movement") {
    return { label: row.transactionType === "consume" ? "生产耗用" : "生产耗材流转", detail: "生产耗材流转单" };
  }
  if (row.sourceType === "manual" || MANUAL_TX.has(row.transactionType)) {
    return { label: "盘点/异常调整", detail: "非日常出库来源" };
  }
  return { label: TX_LABELS[row.transactionType] ?? row.transactionType, detail: row.sourceType || "未记录来源" };
}

function consumableSigned(row: ConsumableTransactionRow) {
  const raw = Number(row.quantity);
  if (!Number.isFinite(raw)) return "—";
  if (MANUAL_TX.has(row.transactionType)) return `${raw > 0 ? "+" : ""}${formatQty(raw)}`;
  return `${NEGATIVE_TX.has(row.transactionType) ? "-" : "+"}${formatQty(Math.abs(raw))}`;
}

/** 库存流水弹窗：正品取吉客云出入库单明细，耗材取消耗台账流水。 */
export default function InventoryTransactionModal({ kind, id, title, subtitle, unit, onClose }: Props) {
  const [goodsRows, setGoodsRows] = useState<SkuTransactionRow[]>([]);
  const [txRows, setTxRows] = useState<ConsumableTransactionRow[]>([]);
  const [goodsBalance, setGoodsBalance] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // 工作区下所有 Tab 都常驻挂载：隐藏 Tab 不能响应全局 Esc，否则在别的 Tab 按 Esc 会关掉这里的弹窗
  const tabActive = useTabActive();

  useEffect(() => {
    if (!tabActive) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, tabActive]);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    setGoodsRows([]);
    setTxRows([]);
    setGoodsBalance(null);
    if (kind === "goods") {
      dashboardApi.skuTransactions(id)
        .then((payload) => {
          if (cancelled) return;
          setGoodsRows(payload.rows);
          setGoodsBalance(payload.balance);
        })
        .catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught)); })
        .finally(() => { if (!cancelled) setLoading(false); });
    } else {
      consumablesApi.transactions(id)
        .then((items) => { if (!cancelled) setTxRows(items); })
        .catch((caught) => { if (!cancelled) setError(caught instanceof Error ? caught.message : String(caught)); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    return () => { cancelled = true; };
  }, [kind, id]);

  const isGoods = kind === "goods";
  const total = isGoods ? goodsRows.length : txRows.length;

  return (
    <div className="fixed inset-0 z-modal flex items-center justify-center p-6" role="dialog" aria-modal="true" aria-label="库存流水">
      <button type="button" aria-label="关闭流水弹窗" onClick={onClose} className="absolute inset-0 bg-slate-950/25" />
      <div className="relative flex h-full max-h-[720px] w-full max-w-[1080px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
        <div className="flex shrink-0 items-start justify-between border-b border-slate-100 bg-white px-5 py-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600">库存管理 / 库存流水</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-900">{title}</h2>
            {subtitle && <div className="mt-0.5 font-mono text-xs text-slate-500">{subtitle}</div>}
          </div>
          <button type="button" onClick={onClose} className="rounded-lg px-2 py-1 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-2.5 text-xs text-slate-500">
          <span>共 <b className="tabular-nums text-slate-700">{total}</b> 条</span>
          {isGoods && goodsBalance != null && <span>当前结存 <b className="tabular-nums text-slate-700">{formatQty(goodsBalance)}</b> {unit || ""}</span>}
          {error && <span role="alert" className="text-rose-600">{error}</span>}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full min-w-[880px] text-left text-[13px]">
            <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-medium text-slate-500">
              {isGoods
                ? <tr><th className="px-4 py-2.5">发生时间</th><th className="px-3 py-2.5">业务来源</th><th className="px-3 py-2.5">仓库</th><th className="px-3 py-2.5 text-right">变动数量</th><th className="px-3 py-2.5">结存变化</th><th className="px-3 py-2.5">备注</th></tr>
                : <tr><th className="px-4 py-2.5">发生时间</th><th className="px-3 py-2.5">业务来源</th><th className="px-3 py-2.5">仓库</th><th className="px-3 py-2.5 text-right">变动数量</th><th className="px-3 py-2.5">库存变化</th><th className="px-3 py-2.5">备注</th></tr>}
            </thead>
            <tbody className="divide-y divide-slate-100">
              {isGoods && goodsRows.map((row, index) => (
                <tr key={`${row.documentNo}-${index}`} className="hover:bg-slate-50/70">
                  <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">{formatDate(row.occurredAt)}</td>
                  <td className="px-3 py-3">
                    <span className={`rounded-full px-2 py-1 text-[11px] font-medium ${row.direction === "inbound" ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-600"}`}>{row.direction === "inbound" ? "入库" : "出库"}</span>
                    <div className="mt-1 max-w-[220px] truncate font-mono text-[11px] text-slate-400" title={row.documentNo}>{row.documentNo || "—"}</div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-600">{row.warehouseName || "—"}</td>
                  <td className={`whitespace-nowrap px-3 py-3 text-right font-medium tabular-nums ${row.direction === "inbound" ? "text-emerald-600" : "text-rose-500"}`}>{row.quantity > 0 ? "+" : ""}{formatQty(row.quantity)} {unit || ""}</td>
                  <td className="whitespace-nowrap px-3 py-3 text-xs tabular-nums text-slate-500">{formatQty(row.balanceBefore)} → {formatQty(row.balanceAfter)}</td>
                  <td className="max-w-[260px] px-3 py-3 text-xs text-slate-500"><div className="truncate" title={[row.supplierName, row.companyName].filter(Boolean).join(" · ")}>{[row.supplierName, row.companyName].filter(Boolean).join(" · ") || "—"}</div></td>
                </tr>
              ))}
              {!isGoods && txRows.map((row) => {
                const source = consumableSource(row);
                const negative = NEGATIVE_TX.has(row.transactionType);
                return (
                  <tr key={row.id} className="hover:bg-slate-50/70">
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">{formatDate(row.occurredAt)}</td>
                    <td className="px-3 py-3">
                      <span className={`rounded-full px-2 py-1 text-[11px] font-medium ${row.sourceType === "consumable_receipt" ? "bg-emerald-50 text-emerald-700" : row.sourceType === "inbound_link" ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600"}`}>{source.label}</span>
                      <div className="mt-1 max-w-[220px] truncate text-[11px] text-slate-400" title={source.detail}>{source.detail}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-600">{row.warehouseName || (row.location === "factory" ? "工厂仓" : "自有仓")}</td>
                    <td className={`whitespace-nowrap px-3 py-3 text-right font-medium tabular-nums ${negative ? "text-amber-600" : "text-emerald-600"}`}>{consumableSigned(row)} {unit || ""}</td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs tabular-nums text-slate-500">{row.stockBefore != null && (row.stockBefore !== row.stockAfter || row.factoryBefore == null) && <div>自有 {formatQty(row.stockBefore)} → {formatQty(row.stockAfter)}</div>}{row.factoryBefore != null && <div>工厂 {formatQty(row.factoryBefore)} → {formatQty(row.factoryAfter)}</div>}{row.stockBefore == null && row.factoryBefore == null && "—"}</td>
                    <td className="max-w-[260px] px-3 py-3 text-xs text-slate-500"><div className="truncate" title={row.note}>{row.note || "—"}</div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {loading && <div className="p-12 text-center text-sm text-slate-400">正在加载流水…</div>}
          {!loading && !total && !error && <div className="p-12 text-center text-sm text-slate-400">暂无库存流水记录</div>}
        </div>
        <div className="shrink-0 border-t border-slate-100 bg-blue-50/60 px-5 py-2.5 text-[11px] leading-5 text-blue-800">
          {isGoods ? "正品流水来自本地吉客云入库/出库单明细，结存按「采购入库 − 销售出库」逐笔累计，与库存总览口径一致。" : "正常链路：耗材采购单实际收货 → 采购入库；正品实际入库 → 按映射自动生成耗材耗用。"}
        </div>
      </div>
    </div>
  );
}
