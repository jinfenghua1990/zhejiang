"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { authenticatedFetch } from "@/lib/api";
import ProductionPurchaseBoard from "../production/production-purchase-board";

type Allocation = {
  inboundDocumentId: number | null;
  skuCode: string;
  goodsName: string;
  quantity: number | string | null;
  unitPrice: number | string | null;
  amount: number | string | null;
  warehouseName: string | null;
  currentStock: number | string | null;
};

type UsageItem = {
  consumableId: number;
  consumableCode: string;
  consumableName: string;
  unit: string;
  quantity: number | string | null;
};

type Inbound = {
  documentId: number;
  goodsdocNo: string;
  date: string | null;
  warehouseName: string | null;
  consumableUsageDecided: boolean;
  consumableUsageEnabled: boolean;
  consumableUsageItems: UsageItem[];
};

type WorkbenchPayload = {
  order: {
    orderId: number;
    orderNo: string;
    platform: string;
    supplier: string | null;
    title: string | null;
    orderDate: string | null;
    purchaseStatus: string;
    amount: number | string | null;
    paidAmount: number | string | null;
  };
  detail: {
    allocations: Allocation[];
    inbound: Inbound[];
    consumable?: { items?: Array<{ code: string; name: string; unit: string; quantity: number | string | null }> } | null;
  };
};

type DetailRow = {
  inboundNo: string;
  inboundDate: string | null;
  skuCode: string;
  goodsName: string;
  quantity: number | string | null;
  unitPrice: number | string | null;
  amount: number | string | null;
  consumableName: string;
  consumableCode: string;
  consumableQty: number | string | null;
  consumableUnit: string;
  usageStatus: string;
  warehouseName: string;
  currentStock: number | string | null;
};

function numberValue(value: number | string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function quantity(value: number | string | null | undefined) {
  return numberValue(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function money(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  return `¥${numberValue(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function dateText(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString("zh-CN");
}

function purchaseStatus(order: WorkbenchPayload["order"], inbound: Inbound[]) {
  if (inbound.length) return "已入库";
  const labels: Record<string, string> = {
    pending_refine: "待确认",
    confirmed: "待生产",
    jackyun_linked: "待生产",
    producing: "生产中",
    shipped: "已发货",
    arrived: "已到货",
    inbound: "已入库",
    done: "已完成",
  };
  return labels[order.purchaseStatus] ?? "待确认";
}

function usageStatus(inbound: Inbound | undefined) {
  if (!inbound) return "待入库";
  if (!inbound.consumableUsageDecided || inbound.consumableUsageEnabled !== true) return "待维护映射";
  return "已自动扣减";
}

function DetailPage({ orderId }: { orderId: number }) {
  const [payload, setPayload] = useState<WorkbenchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/procurement-workbench/orders/${orderId}/workbench`, { cache: "no-store" });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `采购订单加载失败（${response.status}）`);
      }
      setPayload((await response.json()) as WorkbenchPayload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      setPayload(null);
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo<DetailRow[]>(() => {
    if (!payload) return [];
    const inboundById = new Map((payload.detail.inbound ?? []).map((item) => [item.documentId, item]));
    const result: DetailRow[] = [];

    for (const allocation of payload.detail.allocations ?? []) {
      const inbound = allocation.inboundDocumentId ? inboundById.get(allocation.inboundDocumentId) : undefined;
      const usageItems = inbound?.consumableUsageItems ?? [];
      const shared = {
        inboundNo: inbound?.goodsdocNo || "待关联",
        inboundDate: inbound?.date ?? null,
        skuCode: allocation.skuCode || "—",
        goodsName: allocation.goodsName || "—",
        quantity: allocation.quantity,
        unitPrice: allocation.unitPrice,
        amount: allocation.amount,
        usageStatus: usageStatus(inbound),
        warehouseName: allocation.warehouseName || inbound?.warehouseName || "—",
        currentStock: allocation.currentStock,
      };
      if (!usageItems.length) {
        result.push({
          ...shared,
          consumableName: "—",
          consumableCode: "",
          consumableQty: null,
          consumableUnit: "",
        });
        continue;
      }
      for (const usage of usageItems) {
        result.push({
          ...shared,
          consumableName: usage.consumableName || "—",
          consumableCode: usage.consumableCode || "",
          consumableQty: usage.quantity,
          consumableUnit: usage.unit || "",
        });
      }
    }

    if (!result.length && payload.detail.consumable?.items?.length) {
      for (const item of payload.detail.consumable.items) {
        result.push({
          inboundNo: "耗材采购入库",
          inboundDate: null,
          skuCode: "—",
          goodsName: "—",
          quantity: item.quantity,
          unitPrice: null,
          amount: null,
          consumableName: item.name,
          consumableCode: item.code,
          consumableQty: item.quantity,
          consumableUnit: item.unit,
          usageStatus: "已入库",
          warehouseName: "—",
          currentStock: null,
        });
      }
    }
    return result;
  }, [payload]);

  if (loading) {
    return <div className="rounded-xl border border-slate-200 bg-white p-10 text-center text-sm text-slate-400">正在读取采购入库与耗材关联…</div>;
  }
  if (error || !payload) {
    return (
      <div className="space-y-3">
        <Link href="/supply-chain/material-flow" className="inline-flex rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">返回耗材流转</Link>
        <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error || "采购订单不存在"}</div>
      </div>
    );
  }

  const status = purchaseStatus(payload.order, payload.detail.inbound ?? []);
  const usedCount = rows.filter((row) => row.usageStatus === "已使用").length;

  return (
    <div className="space-y-3">
      <div className="app-page-header flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <div className="text-[10px] font-medium text-indigo-600">耗材流转 · 采购入库事实</div>
          <h1 className="mt-1 text-lg font-semibold text-slate-900">{payload.order.orderNo}</h1>
          <p className="mt-1 text-xs text-slate-500">{payload.order.supplier || "—"} · {status} · {rows.length} 条关联明细，{usedCount} 条已使用</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/supply-chain/material-flow" className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">返回列表</Link>
          <Link href={`/purchase/workbench?view=orders&order=${payload.order.orderId}`} className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-medium text-indigo-700 hover:bg-indigo-100">打开采购主单</Link>
          <Link href="/inventory?tab=consumables" className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">耗材库存</Link>
          <button type="button" onClick={() => void load()} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">刷新</button>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">入库单 · SKU · 耗材一一对应</h2>
            <p className="mt-1 text-[11px] text-slate-500">以本系统采购入库主单为主；确认入库后由 SKU 映射自动关联耗材使用，历史吉客云单据只作参考。</p>
          </div>
          <span className="text-xs text-slate-400">订单日期 {dateText(payload.order.orderDate)}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1380px] text-xs">
            <thead className="bg-slate-50 text-left text-[10px] font-medium text-slate-500">
              <tr>
                <th className="px-4 py-2.5">入库单</th>
                <th className="px-3 py-2.5">SKU</th>
                <th className="px-3 py-2.5">商品</th>
                <th className="px-3 py-2.5 text-right">数量</th>
                <th className="px-3 py-2.5 text-right">单价</th>
                <th className="px-3 py-2.5 text-right">金额</th>
                <th className="px-3 py-2.5">耗材名称</th>
                <th className="px-3 py-2.5 text-right">用量</th>
                <th className="px-3 py-2.5">状态</th>
                <th className="px-4 py-2.5">仓库 / 库存</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row, index) => (
                <tr key={`${row.inboundNo}:${row.skuCode}:${row.consumableCode}:${index}`} className="hover:bg-slate-50/60">
                  <td className="px-4 py-2.5"><div className="font-medium text-slate-800">{row.inboundNo}</div><div className="mt-0.5 text-[10px] text-slate-400">{dateText(row.inboundDate)}</div></td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-indigo-600">{row.skuCode}</td>
                  <td className="max-w-[280px] px-3 py-2.5 text-slate-700">{row.goodsName}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{quantity(row.quantity)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{money(row.unitPrice)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{money(row.amount)}</td>
                  <td className="px-3 py-2.5"><div className="text-slate-700">{row.consumableName}</div>{row.consumableCode && <div className="mt-0.5 font-mono text-[10px] text-slate-400">{row.consumableCode}</div>}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums">{row.consumableQty === null ? "—" : `${quantity(row.consumableQty)} ${row.consumableUnit}`}</td>
                  <td className="px-3 py-2.5"><span className={`rounded px-2 py-1 text-[10px] ${row.usageStatus === "已使用" ? "bg-emerald-50 text-emerald-700" : row.usageStatus === "待确认" ? "bg-red-50 text-red-700" : "bg-slate-100 text-slate-500"}`}>{row.usageStatus}</span></td>
                  <td className="px-4 py-2.5"><div className="text-slate-700">{row.warehouseName}</div><div className="mt-0.5 text-[10px] text-slate-400">当前库存 {row.currentStock === null ? "—" : quantity(row.currentStock)}</div></td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={10} className="px-4 py-10 text-center text-slate-400">该采购单暂未形成入库或耗材关联明细</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function MaterialFlowPage() {
  const searchParams = useSearchParams();
  const rawOrderId = Number(searchParams.get("order"));
  const orderId = Number.isInteger(rawOrderId) && rawOrderId !== 0 ? rawOrderId : null;

  if (orderId !== null) return <DetailPage orderId={orderId} />;

  return (
    <div className="mx-auto max-w-[1650px] space-y-3">
      <div className="app-page-header flex flex-wrap items-end justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div>
          <div className="text-[10px] font-medium text-indigo-600">库存中心 / 耗材流转</div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-slate-900">耗材流转</h1>
          <p className="mt-1 text-xs text-slate-500">统一读取采购主单和本系统入库单；正品入库后按 SKU 映射自动关联耗材使用。</p>
        </div>
      </div>
      <ProductionPurchaseBoard
        title="正品采购耗材关联"
        description="这里显示当前真实采购主单；点击“查看耗材明细”后，按入库单、SKU 和耗材逐行核对。"
        actionLabel="查看耗材明细"
        actionHref={(row) => `/supply-chain/material-flow?order=${row.orderId}`}
      />
    </div>
  );
}
