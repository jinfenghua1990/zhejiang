"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { authenticatedFetch, procurementChainApi } from "@/lib/api";
import { useTabActive, useTabDirty } from "@/lib/workspace/tab-store";

type InboundItem = {
  id: number;
  lineNo: number;
  sku: string;
  goodsName: string;
  rawGoodsName?: string;
  spec: string;
  unit: string;
  arrivedQuantity: number;
  actualQuantity: number;
  difference: number;
  unitPrice?: number;
  amountTax?: number;
  matchStatus: string;
  note: string;
};

type InboundRef = {
  orderId: number | null;
  orderNo: string;
  supplier: string;
  platform: string;
  linkId: number;
  matchMethod: string;
  confirmed: boolean;
  note: string;
};

type InboundOperation = {
  action: string;
  at: string | null;
  note: string;
};

type InboundInvoice = {
  invoiceId: number;
  invoiceNo: string;
  sellerName: string;
  amount: number | null;
  matchedAmount: number | null;
  issueDate: string | null;
  status: string;
  matchStatus: string;
  matchMethod: string;
  confirmed: boolean;
  verified: boolean;
  verifiedMonth: string;
  linkId: number;
  linkIds: number[];
  note: string;
};

type InboundDocumentRow = {
  id: number;
  inboundNo: string;
  source: string;
  isLocal: boolean;
  supplier: string;
  warehouse: string;
  warehouseCode: string;
  productCount: number;
  productSummary: string;
  arrivedQuantity: number;
  actualQuantity: number;
  difference: number;
  status: string;
  inboundAt: string | null;
  createdAt: string | null;
  relatedInboundNos: string[];
  platformPurchaseOrderNo: string;
  orderNos: string[];
  refs: InboundRef[];
  invoiceStatus: string;
  invoiceExpectedAmount: number | null;
  invoiceReceivedAmount: number;
  invoiceOutstandingAmount: number | null;
  invoiceCount: number;
  invoiceVerifiedCount: number;
  invoices: InboundInvoice[];
  items: InboundItem[];
  operations: InboundOperation[];
};

type Payload = {
  total: number;
  stats: { all: number; pending: number; done: number; exception: number };
  warehouses: string[];
  rows: InboundDocumentRow[];
};

const PAGE_SIZE = 8;

const STATUS_STYLE: Record<string, string> = {
  已入库: "border-emerald-200 bg-emerald-50 text-emerald-700",
  部分入库: "border-amber-200 bg-amber-50 text-amber-700",
  待入库: "border-blue-200 bg-blue-50 text-blue-700",
  异常: "border-red-200 bg-red-50 text-red-700",
};

const MATCH_METHOD_LABELS: Record<string, string> = {
  consumable_receipt: "耗材入库",
};

function formatDate(value: string | null, withTime = false) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16).replace("T", " ");
  return date.toLocaleString("zh-CN", withTime
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatQuantity(value: number) {
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function statusClass(status: string) {
  return STATUS_STYLE[status] ?? "border-slate-200 bg-slate-50 text-slate-600";
}

function invoiceStatusClass(status: string) {
  if (status === "已开票") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "部分开票") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-orange-200 bg-orange-50 text-orange-700";
}

function formatMoney(value: number | null) {
  if (value == null) return "—";
  return `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function toLocalInputValue(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function previewAmount(item: InboundItem, draftPrice: string) {
  const price = Number(draftPrice);
  if (!draftPrice.trim() || !Number.isFinite(price) || price < 0) return "—";
  return `≈${formatMoney(price * item.actualQuantity)}`;
}

function sourceBadge(row: InboundDocumentRow): { label: string; cls: string } {
  if (row.source === "consumable") return { label: "耗材入库", cls: "border-teal-200 bg-teal-50 text-teal-700" };
  if (row.isLocal) return { label: "本系统入库", cls: "border-indigo-200 bg-indigo-50 text-indigo-700" };
  return { label: "吉客云入库单", cls: "border-orange-200 bg-orange-50 text-orange-700" };
}

type DetailTabKey = "items" | "links" | "invoices" | "operations";

export default function InboundDocumentsBoard() {
  const [rows, setRows] = useState<InboundDocumentRow[]>([]);
  const [search, setSearch] = useState("");
  const [warehouseTab, setWarehouseTab] = useState("");
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<DetailTabKey>("items");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ limit: "500" });
      if (search.trim()) params.set("q", search.trim());
      const response = await authenticatedFetch(`/api/v1/supply-chain/inbound-documents?${params}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `入库单加载失败（${response.status}）`);
      }
      const payload = (await response.json()) as Payload;
      const nextRows = payload.rows ?? [];
      setRows(nextRows);
      setPage(1);
      setSelectedId((current) => nextRows.some((row) => row.id === current) ? current : null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    void load();
    const refresh = () => void load();
    window.addEventListener("inbound-documents-updated", refresh);
    return () => window.removeEventListener("inbound-documents-updated", refresh);
  }, [load]);

  // 仓库 tab 在前端过滤：tab 数量始终与当前搜索结果一致，切换不需要再请求。
  const warehouseTabs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of rows) counts.set(row.warehouse, (counts.get(row.warehouse) ?? 0) + 1);
    return [
      { label: "全部", value: "", count: rows.length },
      ...[...counts.keys()].sort((a, b) => a.localeCompare(b, "zh-CN")).map((name) => ({ label: name, value: name, count: counts.get(name) ?? 0 })),
    ];
  }, [rows]);

  const filteredRows = useMemo(
    () => warehouseTab ? rows.filter((row) => row.warehouse === warehouseTab) : rows,
    [rows, warehouseTab],
  );
  const selected = useMemo(() => rows.find((row) => row.id === selectedId) ?? null, [rows, selectedId]);
  const pageCount = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const visibleRows = useMemo(() => filteredRows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [page, filteredRows]);

  useEffect(() => {
    setPage((current) => Math.min(current, pageCount));
  }, [pageCount]);

  function openDetail(row: InboundDocumentRow) {
    setSelectedId(row.id);
    setActiveTab("items");
  }

  return (
    <section className="space-y-3">
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        {/* 仓库 tab：全部 + 各仓库（数量随当前搜索结果实时统计） */}
        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 bg-white px-4 pt-3 pb-2.5">
          {warehouseTabs.map((tab) => (
            <button
              key={tab.value || "__all__"}
              type="button"
              onClick={() => { setWarehouseTab(tab.value); setPage(1); }}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${warehouseTab === tab.value ? "border-indigo-600 bg-indigo-600 text-white shadow-sm" : "border-slate-200 bg-white text-slate-600 hover:border-indigo-300 hover:text-indigo-600"}`}
            >
              {tab.label}
              <span className={`rounded-full px-1.5 py-0.5 text-[10px] tabular-nums ${warehouseTab === tab.value ? "bg-white/20 text-white" : "bg-slate-100 text-slate-500"}`}>{tab.count}</span>
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-white px-4 py-2.5">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void load()}
            placeholder="入库单号 / 采购订单号 / 供应商 / 耗材编码 / SKU"
            className="h-8 min-w-[220px] flex-1 rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400"
          />
          <button type="button" onClick={() => void load()} disabled={loading} className="h-8 rounded-lg bg-indigo-600 px-4 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60">{loading ? "查询中…" : "查询"}</button>
        </div>

        {error && <div className="m-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

        <div className="overflow-auto">
          <table className="w-full min-w-[1080px] border-collapse text-[11px] leading-tight">
            <thead className="sticky top-0 z-10 bg-slate-50/95 text-left text-[10px] font-medium text-slate-500">
              <tr>
                <th className="border-b border-slate-100 px-4 py-2">入库单号</th>
                <th className="border-b border-slate-100 px-3 py-2">供应商 / 工厂</th>
                <th className="border-b border-slate-100 px-3 py-2">入库仓库</th>
                <th className="border-b border-slate-100 px-3 py-2">品项</th>
                <th className="border-b border-slate-100 px-3 py-2 text-right">实收数量</th>
                <th className="border-b border-slate-100 px-3 py-2">状态</th>
                <th className="border-b border-slate-100 px-3 py-2">入库时间</th>
                <th className="border-b border-slate-100 px-4 py-2 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleRows.map((row) => {
                const badge = sourceBadge(row);
                return (
                  <tr key={row.id} onClick={() => openDetail(row)} className="cursor-pointer align-top transition hover:bg-indigo-50/40">
                    <td className="border-b border-slate-100 px-4 py-2">
                      <div className="font-mono text-[11px] font-semibold text-slate-800">{row.inboundNo || "未记录入库单号"}</div>
                      <span className={`mt-0.5 inline-flex rounded border px-1.5 py-0.5 text-[9px] ${badge.cls}`}>{badge.label}</span>
                      {(row.orderNos ?? []).map((no) => (
                        <button
                          key={no}
                          type="button"
                          title={`按订单号 ${no} 过滤入库单`}
                          onClick={(event) => { event.stopPropagation(); setSearch(no); }}
                          className="mt-0.5 block max-w-[150px] truncate text-left font-mono text-[10px] font-medium text-indigo-600 hover:text-indigo-800 hover:underline"
                        >{no}</button>
                      ))}
                    </td>
                    <td className="max-w-[190px] border-b border-slate-100 px-3 py-2"><div className="truncate font-medium text-slate-700" title={row.supplier}>{row.supplier}</div></td>
                    <td className="border-b border-slate-100 px-3 py-2 text-[11px] text-slate-600">{row.warehouse}</td>
                    <td className="border-b border-slate-100 px-3 py-2">
                      <div className="max-w-[260px] truncate text-slate-700" title={row.productSummary}>{row.productCount} 个品项</div>
                      <div className="mt-0.5 max-w-[260px] truncate text-[10px] text-slate-400" title={row.items.map((item) => item.goodsName).join("、")}>{row.items.slice(0, 2).map((item) => item.goodsName).join("、") || "暂无明细"}{row.items.length > 2 ? ` 等 ${row.items.length} 项` : ""}</div>
                    </td>
                    <td className="border-b border-slate-100 px-3 py-2 text-right font-medium tabular-nums text-slate-700">{formatQuantity(row.actualQuantity)}</td>
                    <td className="border-b border-slate-100 px-3 py-2"><span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-medium ${statusClass(row.status)}`}>{row.status}</span></td>
                    <td className="whitespace-nowrap border-b border-slate-100 px-3 py-2 text-[11px] text-slate-500">{formatDate(row.inboundAt, true)}</td>
                    <td className="border-b border-slate-100 px-4 py-2 text-right"><button type="button" onClick={(event) => { event.stopPropagation(); openDetail(row); }} className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800">查看明细</button></td>
                  </tr>
                );
              })}
              {!loading && !visibleRows.length && <tr><td colSpan={8} className="px-4 py-14 text-center text-sm text-slate-400">{rows.length ? "当前仓库下没有入库单" : "没有符合搜索条件的入库单"}</td></tr>}
              {loading && <tr><td colSpan={8} className="px-4 py-14 text-center text-sm text-slate-400">正在加载入库单…</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-400">
          <span>共 {filteredRows.length} 张入库单{filteredRows.length ? `，当前显示第 ${(page - 1) * PAGE_SIZE + 1}-${Math.min(page * PAGE_SIZE, filteredRows.length)} 张` : ""}</span>
          {pageCount > 1 && <div className="flex items-center gap-2"><button type="button" disabled={page === 1} onClick={() => setPage((current) => Math.max(1, current - 1))} className="rounded border border-slate-200 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40">上一页</button><span>第 {page} / {pageCount} 页</span><button type="button" disabled={page === pageCount} onClick={() => setPage((current) => Math.min(pageCount, current + 1))} className="rounded border border-slate-200 px-2 py-1 disabled:cursor-not-allowed disabled:opacity-40">下一页</button></div>}
        </div>
      </div>

      {selected && <InboundDetailModal row={selected} activeTab={activeTab} onTabChange={setActiveTab} onClose={() => setSelectedId(null)} onDeleted={() => { setSelectedId(null); void load(); }} onSaved={() => void load()} />}
    </section>
  );
}

function InboundDetailModal({ row, activeTab, onTabChange, onClose, onDeleted, onSaved }: { row: InboundDocumentRow; activeTab: DetailTabKey; onTabChange: (tab: DetailTabKey) => void; onClose: () => void; onDeleted: () => void; onSaved: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionError, setActionError] = useState("");
  const [editingDate, setEditingDate] = useState(false);
  const [draftDate, setDraftDate] = useState("");
  const [draftDateNote, setDraftDateNote] = useState("");
  const [editingItemId, setEditingItemId] = useState<number | null>(null);
  const [draftPrice, setDraftPrice] = useState("");
  const [draftPriceNote, setDraftPriceNote] = useState("");
  // 改价 / 改入库时间还没提交时，关 Tab 或刷新会给出「存在未保存内容」提示
  useTabDirty(editingDate || editingItemId !== null);
  // 工作区下所有 Tab 都常驻挂载：隐藏 Tab 不能响应全局 Esc，
  // 否则在别的 Tab 按 Esc 会把这里正在改价/改入库时间的详情弹窗关掉。
  const tabActive = useTabActive();

  useEffect(() => {
    if (!tabActive) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, tabActive]);

  const badge = sourceBadge(row);
  // 耗材入库是流水聚合出的虚拟单据（id 为负），不落吉客云入库单，不能改日期/单价。
  const canCorrect = row.source !== "consumable";

  async function saveDate() {
    if (!draftDate) {
      setActionError("请选择入库时间");
      return;
    }
    setSaving(true);
    setActionError("");
    try {
      await procurementChainApi.correctInboundDate(row.id, new Date(draftDate).toISOString(), draftDateNote.trim());
      setEditingDate(false);
      setDraftDateNote("");
      onSaved();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function saveItemPrice(itemId: number) {
    if (!draftPrice.trim()) {
      setActionError("请填写含税单价");
      return;
    }
    setSaving(true);
    setActionError("");
    try {
      await procurementChainApi.correctInboundItemPrice(row.id, itemId, draftPrice.trim(), draftPriceNote.trim());
      setEditingItemId(null);
      setDraftPriceNote("");
      onSaved();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4 backdrop-blur-[2px]" role="dialog" aria-modal="true" aria-label={`入库单明细 ${row.inboundNo}`} onClick={onClose}>
      <section className="flex max-h-[calc(100vh-4rem)] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="font-mono text-sm font-semibold text-slate-900">{row.inboundNo || "未记录入库单号"}</h2>
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${badge.cls}`}>{badge.label}</span>
              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusClass(row.status)}`}>{row.status}</span>
            </div>
            <div className="mt-1.5 grid gap-x-6 gap-y-1 text-xs text-slate-500 sm:grid-cols-3">
              <span>采购订单号：<b className="font-mono font-medium text-indigo-700">{(row.orderNos ?? []).join("、") || "—"}</b></span>
              <span>供应商 / 工厂：<b className="font-medium text-slate-700">{row.supplier}</b></span>
              <span>入库仓库：<b className="font-medium text-slate-700">{row.warehouse}</b></span>
              <span>入库时间：<b className="font-medium text-slate-700">{formatDate(row.inboundAt, true)}</b></span>
              <span>到货数量：<b className="font-medium text-slate-700">{formatQuantity(row.arrivedQuantity)}</b></span>
              <span>实际入库：<b className="font-medium text-slate-700">{formatQuantity(row.actualQuantity)}</b></span>
              <span>创建时间：<b className="font-medium text-slate-700">{formatDate(row.createdAt, true)}</b></span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {canCorrect && <button type="button" onClick={() => {
              setEditingDate((current) => !current);
              setDraftDate((current) => current || toLocalInputValue(row.inboundAt));
              setActionError("");
            }} className="rounded-lg border border-indigo-200 px-3 py-1.5 text-xs font-medium text-indigo-600 hover:bg-indigo-50">{editingDate ? "收起改时间" : "改入库时间"}</button>}
            {row.source === "local_purchase_inbound" && <button type="button" disabled={deleting} onClick={async () => {
              if (!window.confirm(`确定删除本系统入库单 ${row.inboundNo}？删除后会同步回滚耗材自动扣减。`)) return;
              setDeleting(true);
              setActionError("");
              try {
                await procurementChainApi.deleteLocalInbound(row.id);
                onDeleted();
              } catch (caught) {
                setActionError(caught instanceof Error ? caught.message : String(caught));
                setDeleting(false);
              }
            }} className="rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-50">{deleting ? "删除中…" : "删除入库单"}</button>}
            <button type="button" onClick={onClose} aria-label="关闭明细" className="rounded-lg px-2 py-1 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
          </div>
        </div>
        {editingDate && canCorrect && (
          <div className="shrink-0 border-b border-indigo-100 bg-indigo-50/50 px-5 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-slate-600">更正入库时间</span>
              <input type="datetime-local" value={draftDate} onChange={(event) => setDraftDate(event.target.value)} className="h-8 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none focus:border-indigo-400" />
              <input value={draftDateNote} onChange={(event) => setDraftDateNote(event.target.value)} placeholder="更正原因（可选，写入审计日志）" className="h-8 min-w-[200px] flex-1 rounded-lg border border-slate-200 bg-white px-2 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400" />
              <button type="button" onClick={() => void saveDate()} disabled={saving} className="h-8 rounded-lg bg-indigo-600 px-3 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60">{saving ? "保存中…" : "保存"}</button>
              <button type="button" onClick={() => { setEditingDate(false); setDraftDateNote(""); }} className="h-8 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 hover:bg-slate-50">取消</button>
            </div>
            <p className="mt-1.5 text-[11px] text-slate-400">入库时间决定这批成本算进哪个报告期的加权成本：纠正日期后，该月月结/利润口径会自动重算。</p>
          </div>
        )}
        {actionError && <div className="shrink-0 border-b border-red-100 bg-red-50 px-5 py-2 text-xs text-red-700">{actionError}</div>}
        <div className="flex shrink-0 items-center gap-5 border-b border-slate-100 px-5 pt-1.5">
          <DetailTab active={activeTab === "items"} onClick={() => onTabChange("items")}>明细（{row.items.length}）</DetailTab>
          <DetailTab active={activeTab === "links"} onClick={() => onTabChange("links")}>采购关联（{row.refs.length}）</DetailTab>
          <DetailTab active={activeTab === "invoices"} onClick={() => onTabChange("invoices")}>发票（{row.invoiceCount}）</DetailTab>
          <DetailTab active={activeTab === "operations"} onClick={() => onTabChange("operations")}>操作记录（{row.operations.length}）</DetailTab>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {activeTab === "items" && (
            <table className="w-full min-w-[820px] text-xs">
              <thead className="bg-slate-50 text-left text-slate-500"><tr><th className="px-3 py-2">#</th><th className="px-3 py-2">SKU / 编码</th><th className="px-3 py-2">名称</th><th className="px-3 py-2">规格</th><th className="px-3 py-2">单位</th><th className="px-3 py-2 text-right">数量</th><th className="px-3 py-2 text-right">单价</th><th className="px-3 py-2 text-right">金额</th><th className="px-3 py-2">备注</th><th className="px-3 py-2 text-right">操作</th></tr></thead>
              <tbody className="divide-y divide-slate-100">
                {row.items.map((item) => {
                  const editing = editingItemId === item.id;
                  return (
                  <tr key={item.id}>
                    <td className="px-3 py-2.5 text-slate-400">{item.lineNo}</td>
                    <td className="px-3 py-2.5 font-mono text-slate-600">{item.sku}</td>
                    <td className="px-3 py-2.5 font-medium text-slate-700">{item.goodsName}{item.rawGoodsName ? <div className="mt-0.5 text-[10px] font-normal text-slate-400" title="吉客云原录入的货品名称">吉客云原录：{item.rawGoodsName}</div> : null}</td>
                    <td className="px-3 py-2.5 text-slate-500">{item.spec}</td>
                    <td className="px-3 py-2.5 text-slate-500">{item.unit}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{formatQuantity(item.actualQuantity)}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums text-slate-600">
                      {editing
                        ? <input value={draftPrice} onChange={(event) => setDraftPrice(event.target.value)} inputMode="decimal" autoFocus className="h-7 w-24 rounded border border-indigo-300 bg-white px-2 text-right text-xs text-slate-700 outline-none focus:border-indigo-500" />
                        : formatMoney(item.unitPrice ?? null)}
                    </td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-800">{editing ? previewAmount(item, draftPrice) : formatMoney(item.amountTax ?? null)}</td>
                    <td className="max-w-[180px] px-3 py-2.5 text-slate-400" title={item.note}>
                      {editing
                        ? <input value={draftPriceNote} onChange={(event) => setDraftPriceNote(event.target.value)} placeholder="更正原因（可选）" className="h-7 w-full rounded border border-slate-200 px-2 text-xs text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400" />
                        : (item.note || "—")}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 text-right">
                      {editing ? (
                        <span className="flex items-center justify-end gap-2">
                          <button type="button" onClick={() => void saveItemPrice(item.id)} disabled={saving} className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">{saving ? "保存中…" : "保存"}</button>
                          <button type="button" onClick={() => { setEditingItemId(null); setDraftPriceNote(""); }} className="text-[11px] text-slate-400 hover:text-slate-600">取消</button>
                        </span>
                      ) : canCorrect ? (
                        <button type="button" title="更正含税单价，保存后自动重算本行金额和单据金额" onClick={() => {
                          setEditingItemId(item.id);
                          setDraftPrice(item.unitPrice == null ? "" : String(item.unitPrice));
                          setDraftPriceNote("");
                          setActionError("");
                        }} className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800">改单价</button>
                      ) : null}
                    </td>
                  </tr>
                  );
                })}
                {!row.items.length && <tr><td colSpan={10} className="px-3 py-10 text-center text-slate-400">没有明细</td></tr>}
              </tbody>
            </table>
          )}
          {activeTab === "links" && (
            <div className="space-y-2">
              {row.refs.length ? row.refs.map((ref) => (
                <div key={ref.linkId} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3">
                  <div>
                    <div className="font-mono text-sm font-medium text-slate-800">{ref.orderNo}</div>
                    <div className="mt-1 text-xs text-slate-500">{ref.supplier || "未记录供应商"} · {ref.platform} · {MATCH_METHOD_LABELS[ref.matchMethod] || ref.matchMethod || "历史关联"}</div>
                    {ref.note && <div className="mt-0.5 text-[11px] text-slate-400">{ref.note}</div>}
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[11px] ${ref.confirmed ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{ref.confirmed ? "已确认" : "待确认"}</span>
                </div>
              )) : <div className="py-10 text-center text-sm text-slate-400">暂无采购单关联</div>}
            </div>
          )}
          {activeTab === "invoices" && <InvoiceTrace row={row} />}
          {activeTab === "operations" && (
            <div className="space-y-3">
              {row.operations.map((operation, index) => (
                <div key={`${operation.action}-${index}`} className="flex gap-3 text-xs">
                  <div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />
                  <div>
                    <div className="font-medium text-slate-700">{operation.action}</div>
                    <div className="mt-1 text-slate-400">{formatDate(operation.at, true)}{operation.note ? ` · ${operation.note}` : ""}</div>
                  </div>
                </div>
              ))}
              {!row.operations.length && <div className="py-10 text-center text-sm text-slate-400">暂无操作记录</div>}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function InvoiceTrace({ row }: { row: InboundDocumentRow }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-100 bg-indigo-50/50 px-4 py-3">
        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
          <span>应开票 <b className="font-semibold text-slate-800">{formatMoney(row.invoiceExpectedAmount)}</b></span>
          <span>已收票 <b className="font-semibold text-emerald-700">{formatMoney(row.invoiceReceivedAmount)}</b></span>
          <span>未开票 <b className="font-semibold text-orange-700">{formatMoney(row.invoiceOutstandingAmount)}</b></span>
          <span>认证 <b className="font-semibold text-slate-800">{row.invoiceVerifiedCount} / {row.invoiceCount}</b></span>
        </div>
        <Link href="/data-center-import?tab=tax" className="rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50">去发票台账</Link>
      </div>
      {row.invoices.length ? (
        <div className="overflow-x-auto rounded-xl border border-slate-200">
          <table className="min-w-[900px] w-full text-xs">
            <thead className="bg-slate-50 text-left text-slate-500"><tr><th className="px-3 py-2">发票号码</th><th className="px-3 py-2">开票方</th><th className="px-3 py-2 text-right">票面金额</th><th className="px-3 py-2 text-right">本单匹配金额</th><th className="px-3 py-2">开票日期</th><th className="px-3 py-2">匹配状态</th><th className="px-3 py-2">认证状态</th></tr></thead>
            <tbody className="divide-y divide-slate-100">{row.invoices.map((invoice) => <tr key={invoice.invoiceId}><td className="px-3 py-2.5 font-mono font-medium text-slate-700">{invoice.invoiceNo || "未记录号码"}</td><td className="max-w-[220px] truncate px-3 py-2.5 text-slate-600" title={invoice.sellerName}>{invoice.sellerName || "未记录开票方"}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{formatMoney(invoice.amount)}</td><td className="px-3 py-2.5 text-right tabular-nums text-indigo-700">{formatMoney(invoice.matchedAmount)}</td><td className="whitespace-nowrap px-3 py-2.5 text-slate-500">{formatDate(invoice.issueDate)}</td><td className="px-3 py-2.5"><span className={`rounded-full px-2 py-0.5 ${invoice.confirmed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{invoice.matchStatus}</span></td><td className="px-3 py-2.5">{invoice.verified ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700">已认证 {invoice.verifiedMonth}</span> : <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-700">待认证</span>}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-orange-200 bg-orange-50/40 px-4 py-10 text-center text-sm text-orange-700">
          当前入库单还没有匹配到进项发票。{row.source === "consumable" ? "耗材入库不参与进项发票追溯。" : "请先在发票台账导入供应商发票，系统会按明确订单号或已确认采购链路匹配；没有匹配依据时保留“待开票”，不会自动猜测。"}
        </div>
      )}
    </div>
  );
}

function DetailTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" onClick={onClick} className={`border-b-2 px-1 py-2.5 text-xs font-medium ${active ? "border-indigo-600 text-indigo-700" : "border-transparent text-slate-400 hover:text-slate-600"}`}>{children}</button>;
}
