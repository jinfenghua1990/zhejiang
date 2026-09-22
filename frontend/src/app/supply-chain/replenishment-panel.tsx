"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type ReplenishmentRow = {
  skuId: number;
  skuCode: string;
  skuName: string;
  goodsName: string;
  barcode: string;
  unit: string;
  currentInventory: string | null;
  soldQuantity: string | null;
  salesSource: "sales_outbound" | "sales_order_fallback" | "none";
  averageDailySales: string | null;
  openSupplyQuantity: string | null;
  targetStock: string | null;
  suggestedReplenishment: string | null;
  currentCoverDays: string | null;
  effectiveCoverDays: string | null;
  estimatedStockoutDate: string | null;
  risk: "urgent" | "attention" | "ok" | "no_data" | "no_sales";
  reason: string;
};

type ReplenishmentResponse = {
  policy: {
    salesWindowDays: number;
    leadDays: number;
    safetyDays: number;
    targetCoverDays: number;
    formula: string;
  };
  data: {
    lastDocumentAt: string | null;
    inventoryPositionSource?: string;
    salesSince: string;
    outboundDocumentCount?: number;
    matchedOutboundItemCount?: number;
    unmatchedOutboundItemCount?: number;
    generatedAt: string;
  };
  summary: {
    skuCount: number;
    urgentCount: number;
    attentionCount: number;
    suggestedCount: number;
  };
  rows: ReplenishmentRow[];
};

const RISK_META: Record<ReplenishmentRow["risk"], { label: string; cls: string }> = {
  urgent: { label: "紧急补货", cls: "bg-red-50 text-red-700 ring-red-200" },
  attention: { label: "需要关注", cls: "bg-amber-50 text-amber-700 ring-amber-200" },
  ok: { label: "库存正常", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  no_data: { label: "缺库存数据", cls: "bg-slate-100 text-slate-600 ring-slate-200" },
  no_sales: { label: "无近销依据", cls: "bg-blue-50 text-blue-700 ring-blue-200" },
};

function numberText(value: string | null, digits = 1) {
  if (value === null || value === "") return "—";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export default function ReplenishmentPanel() {
  const [data, setData] = useState<ReplenishmentResponse | null>(null);
  const [days, setDays] = useState(30);
  const [leadDays, setLeadDays] = useState(14);
  const [safetyDays, setSafetyDays] = useState(7);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const q = new URLSearchParams({
        days: String(days),
        lead_days: String(leadDays),
        safety_days: String(safetyDays),
        search: search.trim(),
        limit: "1000",
      });
      const response = await authenticatedFetch(`/api/v1/supply-chain/replenishment?${q}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `补货数据加载失败（${response.status}）`);
      }
      setData(await response.json());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [days, leadDays, safetyDays, search]);

  useEffect(() => {
    const timer = window.setTimeout(load, 250);
    return () => window.clearTimeout(timer);
  }, [load]);

  const rows = useMemo(() => {
    if (!data) return [];
    const priority: Record<ReplenishmentRow["risk"], number> = { urgent: 0, attention: 1, no_data: 2, no_sales: 3, ok: 4 };
    return [...data.rows].sort((a, b) => {
      const risk = priority[a.risk] - priority[b.risk];
      if (risk !== 0) return risk;
      return Number(b.suggestedReplenishment || 0) - Number(a.suggestedReplenishment || 0);
    });
  }, [data]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-slate-900">补货工作台</h2>
            <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[10px] font-medium text-indigo-700">真实数据</span>
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            建议补货 = 日均销量 ×（交期 + 安全天数）- 当前库存 - 待供应。无库存数据或无近销时不自动给数量。
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-slate-500">
            销量周期
            <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="mt-1 block rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-700">
              <option value={14}>近14天</option>
              <option value={30}>近30天</option>
              <option value={60}>近60天</option>
              <option value={90}>近90天</option>
            </select>
          </label>
          <label className="text-[11px] text-slate-500">
            生产/采购交期
            <input type="number" min={1} max={120} value={leadDays} onChange={(e) => setLeadDays(Math.max(1, Number(e.target.value) || 1))} className="mt-1 block w-24 rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-700" />
          </label>
          <label className="text-[11px] text-slate-500">
            安全天数
            <input type="number" min={0} max={90} value={safetyDays} onChange={(e) => setSafetyDays(Math.max(0, Number(e.target.value) || 0))} className="mt-1 block w-24 rounded-lg border border-slate-200 px-2 py-1.5 text-xs text-slate-700" />
          </label>
          <label className="text-[11px] text-slate-500">
            搜索
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="SKU / 货品 / 条码" className="mt-1 block w-52 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-indigo-400" />
          </label>
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
            {loading ? "计算中…" : "重新计算"}
          </button>
        </div>
      </div>

      {data && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Summary title="SKU 数" value={data.summary.skuCount} note="当前启用货品" />
          <Summary title="紧急" value={data.summary.urgentCount} note="库存覆盖低于交期" tone="danger" />
          <Summary title="关注" value={data.summary.attentionCount} note="库存+待供应仍偏低" tone="warning" />
          <Summary title="建议补货" value={data.summary.suggestedCount} note={`目标覆盖 ${data.policy.targetCoverDays} 天`} tone="primary" />
        </div>
      )}

      {error && <div className="mt-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-100">
        <table className="w-full min-w-[1180px] text-sm">
          <thead className="bg-slate-50 text-left text-[11px] font-medium text-slate-500">
            <tr>
              <th className="px-3 py-2.5">状态</th>
              <th className="px-3 py-2.5">货品 / SKU</th>
              <th className="px-3 py-2.5 text-right">当前库存</th>
              <th className="px-3 py-2.5 text-right">近销数量</th>
              <th className="px-3 py-2.5 text-right">日均</th>
              <th className="px-3 py-2.5 text-right">待供应</th>
              <th className="px-3 py-2.5 text-right">库存可售天数</th>
              <th className="px-3 py-2.5 text-right">建议补货</th>
              <th className="px-3 py-2.5">预计缺货</th>
              <th className="px-3 py-2.5">判断</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => {
              const meta = RISK_META[row.risk];
              return (
                <tr key={row.skuId} className={row.risk === "urgent" ? "bg-red-50/30" : "bg-white"}>
                  <td className="px-3 py-3 align-top">
                    <span className={`inline-flex rounded-full px-2 py-1 text-[10px] font-medium ring-1 ring-inset ${meta.cls}`}>{meta.label}</span>
                  </td>
                  <td className="max-w-[260px] px-3 py-3 align-top">
                    <div className="truncate font-medium text-slate-800">{row.goodsName || row.skuName || row.skuCode}</div>
                    <div className="mt-1 font-mono text-[10px] text-slate-400">{row.skuCode}{row.barcode ? ` · ${row.barcode}` : ""}</div>
                  </td>
                  <td className="px-3 py-3 text-right align-top font-medium tabular-nums text-slate-800">{numberText(row.currentInventory)}</td>
                  <td className="px-3 py-3 text-right align-top tabular-nums text-slate-700">
                    <div>{numberText(row.soldQuantity)}</div>
                    <div className="mt-1 text-[10px] text-slate-400">
                      {row.salesSource === "sales_outbound" ? "销售出库单" : row.salesSource === "sales_order_fallback" ? "销售订单兜底" : "无销量来源"}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right align-top tabular-nums text-slate-600">{numberText(row.averageDailySales, 2)}</td>
                  <td className="px-3 py-3 text-right align-top tabular-nums text-slate-700">{numberText(row.openSupplyQuantity)}</td>
                  <td className="px-3 py-3 text-right align-top tabular-nums text-slate-700">{numberText(row.currentCoverDays)}{row.currentCoverDays ? " 天" : ""}</td>
                  <td className="px-3 py-3 text-right align-top">
                    {row.suggestedReplenishment === null ? <span className="text-slate-300">—</span> : <span className={`font-semibold tabular-nums ${Number(row.suggestedReplenishment) > 0 ? "text-indigo-700" : "text-slate-400"}`}>{numberText(row.suggestedReplenishment)}</span>}
                  </td>
                  <td className="px-3 py-3 align-top text-xs text-slate-600">{row.estimatedStockoutDate || "—"}</td>
                  <td className="max-w-[260px] px-3 py-3 align-top text-xs leading-5 text-slate-500" title={row.reason}>{row.reason}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loading && !data && <div className="p-8 text-center text-sm text-slate-400">正在读取库存、销售和待供应数据…</div>}
        {!loading && data && rows.length === 0 && <div className="p-8 text-center text-sm text-slate-400">当前没有匹配的启用 SKU</div>}
      </div>

      {data && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[10px] leading-5 text-slate-400">
          <span>
            数据更新时间：{data.data.lastDocumentAt ? new Date(data.data.lastDocumentAt).toLocaleString("zh-CN") : "暂无"}
            {data.data.outboundDocumentCount !== undefined ? ` · 出库单 ${data.data.outboundDocumentCount} 张` : ""}
          </span>
          <span>{data.policy.formula}</span>
        </div>
      )}
    </section>
  );
}

function Summary({ title, value, note, tone = "default" }: { title: string; value: number; note: string; tone?: "default" | "danger" | "warning" | "primary" }) {
  const toneClass = tone === "danger" ? "text-red-700" : tone === "warning" ? "text-amber-700" : tone === "primary" ? "text-indigo-700" : "text-slate-900";
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/70 px-4 py-3">
      <div className="text-[11px] text-slate-500">{title}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      <div className="mt-1 text-[10px] text-slate-400">{note}</div>
    </div>
  );
}
