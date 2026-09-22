"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { authenticatedFetch } from "@/lib/api";

type ProductionItem = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  unit: string;
  quantity: string;
  completedQty: string;
  shippedQty: string;
  arrivedQty: string;
  pendingInboundQty: string;
  inboundQty: string;
};

type ProductionOrder = {
  id: number;
  orderNo: string;
  factoryName: string;
  status: string;
  expectedDeliveryDate: string | null;
  items: ProductionItem[];
};

type PendingRow = { order: ProductionOrder; item: ProductionItem };

type Candidate = {
  productionOrderItemId: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  productionPendingInboundQty: string;
  inboundDocumentId: number;
  inboundDocumentNo: string;
  inboundDocumentAt: string | null;
  warehouseName: string;
  supplierName: string;
  inboundItemId: number;
  inboundItemQuantity: string;
  inboundItemAvailableQty: string;
  suggestedLinkQty: string;
};

type Allocation = {
  id: number;
  productionOrderId: number;
  productionOrderItemId: number;
  skuCode: string;
  skuName: string;
  quantity: string;
  inboundDocumentNo: string;
  inboundDocumentAt: string | null;
  warehouseName: string;
  inboundItemId: number;
  linkedAt: string | null;
};

function number(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function qty(value: string | number, digits = 1) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function requestKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `rcv-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  return typeof payload?.detail === "string" ? payload.detail : `${fallback}（${response.status}）`;
}

export default function ReceivingPage() {
  const searchParams = useSearchParams();
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  const [quantities, setQuantities] = useState<Record<number, string>>({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [workingId, setWorkingId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch("/api/v1/supply-chain/production-orders?limit=1000", { cache: "no-store" });
      if (!response.ok) throw new Error(await responseError(response, "待入库数据加载失败"));
      const payload = await response.json();
      setOrders(payload.rows ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const pendingRows = useMemo<PendingRow[]>(() => {
    const term = search.trim().toLowerCase();
    return orders
      .flatMap((order) => (order.items ?? []).map((item) => ({ order, item })))
      .filter(({ order, item }) => {
        if (number(item.pendingInboundQty) <= 0 || order.status === "cancelled") return false;
        if (!term) return true;
        return `${order.orderNo} ${order.factoryName} ${item.skuCode} ${item.skuName}`.toLowerCase().includes(term);
      });
  }, [orders, search]);

  useEffect(() => {
    if (!pendingRows.length) {
      setSelectedKey("");
      return;
    }
    if (!pendingRows.some(({ order, item }) => `${order.id}:${item.id}` === selectedKey)) {
      // 工作区下读本 Tab 冻结的 searchParams，隐藏 Tab 读 window.location.search 会命中别的 Tab
      const requestedOrderId = Number(searchParams.get("orderId") ?? 0);
      const requested = pendingRows.find(({ order }) => order.id === requestedOrderId);
      const first = requested ?? pendingRows[0];
      setSelectedKey(`${first.order.id}:${first.item.id}`);
    }
  }, [pendingRows, selectedKey]);

  const selected = useMemo(() => pendingRows.find(({ order, item }) => `${order.id}:${item.id}` === selectedKey) ?? null, [pendingRows, selectedKey]);

  const loadCandidates = useCallback(async (row: PendingRow | null) => {
    if (!row) {
      setCandidates([]);
      setAllocations([]);
      return;
    }
    setLoadingCandidates(true);
    setError("");
    try {
      const [candidateResponse, allocationResponse] = await Promise.all([
        authenticatedFetch(`/api/v1/supply-chain/production-orders/${row.order.id}/inbound-candidates?limit=1000`, { cache: "no-store" }),
        authenticatedFetch(`/api/v1/supply-chain/production-inbound-allocations?order_id=${row.order.id}&limit=1000`, { cache: "no-store" }),
      ]);
      if (!candidateResponse.ok) throw new Error(await responseError(candidateResponse, "吉客云入库候选加载失败"));
      if (!allocationResponse.ok) throw new Error(await responseError(allocationResponse, "已关联入库记录加载失败"));
      const candidatePayload = await candidateResponse.json();
      const allocationPayload = await allocationResponse.json();
      const nextCandidates: Candidate[] = (candidatePayload.rows ?? []).filter((candidate: Candidate) => candidate.productionOrderItemId === row.item.id);
      const nextAllocations: Allocation[] = (allocationPayload.rows ?? []).filter((allocation: Allocation) => allocation.productionOrderItemId === row.item.id);
      setCandidates(nextCandidates);
      setAllocations(nextAllocations);
      setQuantities((current) => {
        const next = { ...current };
        for (const candidate of nextCandidates) {
          if (!next[candidate.inboundItemId]) next[candidate.inboundItemId] = candidate.suggestedLinkQty;
        }
        return next;
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoadingCandidates(false);
    }
  }, []);

  useEffect(() => {
    void loadCandidates(selected);
  }, [selected, loadCandidates]);

  async function linkInbound(candidate: Candidate) {
    if (!selected) return;
    const quantity = quantities[candidate.inboundItemId] || candidate.suggestedLinkQty;
    if (number(quantity) <= 0) {
      setError("关联入库数量必须大于 0");
      return;
    }
    setWorkingId(candidate.inboundItemId);
    setError("");
    setNotice("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${selected.order.id}/inbound-links`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: requestKey(),
          items: [{
            production_order_item_id: selected.item.id,
            inbound_item_id: candidate.inboundItemId,
            quantity,
          }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "关联吉客云入库失败"));
      setNotice(`${selected.item.skuName || selected.item.skuCode} 已关联 ${candidate.inboundDocumentNo}：${qty(quantity)} ${selected.item.unit}`);
      setQuantities((current) => ({ ...current, [candidate.inboundItemId]: "" }));
      await load();
      await loadCandidates(selected);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorkingId(null);
    }
  }

  const pendingTotal = pendingRows.reduce((sum, row) => sum + number(row.item.pendingInboundQty), 0);

  return (
    <div className="mx-auto max-w-[1600px] space-y-4">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / RECEIVING</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">到货入库</h1>
            <p className="mt-1 text-sm text-slate-500">已到货数量与吉客云真实入库明细建立数量关联</p>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4 text-xs text-slate-500">
          <span>待入库 SKU <b className="ml-1 text-slate-900">{pendingRows.length}</b></span>
          <span>已到货待关联数量 <b className="ml-1 text-amber-700">{qty(pendingTotal)}</b></span>
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-medium text-emerald-700">库存事实仍以吉客云为准</span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索生产单 / 工厂 / SKU" className="ml-auto w-64 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-indigo-400" />
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">{loading ? "刷新中…" : "刷新"}</button>
        </div>
      </header>

      {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {notice && <div className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(520px,0.85fr)_minmax(720px,1.15fr)]">
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-900">已到货待入库</h2>
            <p className="mt-1 text-[11px] text-slate-400">选择一行，右侧直接显示可匹配的吉客云入库明细。</p>
          </div>
          <div className="max-h-[720px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-[11px] text-slate-500">
                <tr><th className="px-3 py-2.5">生产单 / SKU</th><th className="px-3 py-2.5">到货</th><th className="px-3 py-2.5">已入库</th><th className="px-3 py-2.5">待入库</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {pendingRows.map((row) => {
                  const key = `${row.order.id}:${row.item.id}`;
                  const active = key === selectedKey;
                  return <tr key={key} onClick={() => setSelectedKey(key)} className={`cursor-pointer ${active ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
                    <td className="px-3 py-3"><div className="font-mono text-[11px] font-semibold text-slate-700">{row.order.orderNo}</div><div className="mt-1 max-w-[260px] truncate text-xs font-medium text-slate-700">{row.item.skuName || row.item.skuCode}</div><div className="mt-1 truncate text-[10px] text-slate-400">{row.order.factoryName} · {row.item.skuCode}</div></td>
                    <td className="px-3 py-3 tabular-nums text-slate-700">{qty(row.item.arrivedQty)}</td>
                    <td className="px-3 py-3 tabular-nums text-emerald-700">{qty(row.item.inboundQty)}</td>
                    <td className="px-3 py-3 font-semibold tabular-nums text-amber-700">{qty(row.item.pendingInboundQty)}</td>
                  </tr>;
                })}
              </tbody>
            </table>
            {!loading && pendingRows.length === 0 && <div className="p-10 text-center text-sm text-slate-400">当前没有已到货待入库的生产成品。</div>}
          </div>
        </section>

        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">吉客云真实入库候选</h2>
              <p className="mt-1 text-[11px] text-slate-400">只展示同 SKU、真实 inbound 类型、且仍有未被其他生产单认领数量的明细。</p>
            </div>
            {selected && <div className="text-right text-[11px] text-slate-500"><div className="font-medium text-slate-700">{selected.item.skuName || selected.item.skuCode}</div><div>待入库 {qty(selected.item.pendingInboundQty)} {selected.item.unit}</div></div>}
          </div>

          {!selected ? <div className="p-10 text-center text-sm text-slate-400">请先选择左侧待入库商品。</div> : <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr><th className="px-3 py-2.5">吉客云入库单</th><th className="px-3 py-2.5">仓库 / 供应商</th><th className="px-3 py-2.5">入库明细量</th><th className="px-3 py-2.5">尚可关联</th><th className="px-3 py-2.5">本次关联</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {candidates.map((candidate) => <tr key={candidate.inboundItemId}>
                    <td className="px-3 py-3"><div className="font-mono text-xs font-semibold text-slate-700">{candidate.inboundDocumentNo}</div><div className="mt-1 text-[10px] text-slate-400">{candidate.inboundDocumentAt ? new Date(candidate.inboundDocumentAt).toLocaleString("zh-CN") : "时间未提供"}</div></td>
                    <td className="px-3 py-3"><div className="text-xs text-slate-700">{candidate.warehouseName || "—"}</div><div className="mt-1 max-w-[180px] truncate text-[10px] text-slate-400">{candidate.supplierName || "供应商未提供"}</div></td>
                    <td className="px-3 py-3 tabular-nums text-slate-600">{qty(candidate.inboundItemQuantity)}</td>
                    <td className="px-3 py-3 font-semibold tabular-nums text-emerald-700">{qty(candidate.inboundItemAvailableQty)}</td>
                    <td className="px-3 py-3"><div className="flex items-center gap-1.5"><input type="number" min="0.0001" step="1" value={quantities[candidate.inboundItemId] ?? candidate.suggestedLinkQty} onChange={(e) => setQuantities((current) => ({ ...current, [candidate.inboundItemId]: e.target.value }))} className="w-24 rounded-md border border-slate-200 px-2 py-1.5 text-xs" /><button onClick={() => linkInbound(candidate)} disabled={workingId !== null} className="rounded-md bg-indigo-600 px-2.5 py-1.5 text-[11px] font-medium text-white disabled:opacity-40">{workingId === candidate.inboundItemId ? "关联中…" : "确认关联"}</button></div><div className="mt-1 text-[10px] text-slate-400">建议 {qty(candidate.suggestedLinkQty)}</div></td>
                  </tr>)}
                </tbody>
              </table>
              {!loadingCandidates && candidates.length === 0 && <div className="p-8 text-center text-sm text-slate-400">暂未找到可匹配的吉客云真实入库明细。请先同步或导入吉客云入库单，并确认 SKU 匹配。</div>}
              {loadingCandidates && <div className="p-8 text-center text-sm text-slate-400">正在读取吉客云入库候选…</div>}
            </div>

            <div className="border-t border-slate-100 px-4 py-3">
              <div className="mb-2 text-xs font-semibold text-slate-700">最近已关联</div>
              <div className="space-y-1.5">
                {allocations.slice(0, 8).map((allocation) => <div key={allocation.id} className="grid grid-cols-[1fr_auto_auto] gap-3 rounded-lg bg-slate-50 px-3 py-2 text-[11px]"><span className="font-mono text-slate-600">{allocation.inboundDocumentNo}</span><span className="text-slate-500">{allocation.warehouseName || "—"}</span><span className="font-medium tabular-nums text-emerald-700">{qty(allocation.quantity)} {selected.item.unit}</span></div>)}
                {!allocations.length && <div className="text-[11px] text-slate-400">尚无关联记录。</div>}
              </div>
            </div>
          </>}
        </section>
      </div>

      <div className="rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">确认关联只表示“这批生产到货对应这张吉客云真实入库单”；库存由出入库单据独立运算（每张单只计一次），关联动作本身不额外增加库存。</div>
    </div>
  );
}
