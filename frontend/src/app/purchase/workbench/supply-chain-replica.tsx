"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type DragEvent, type PointerEvent, type ReactNode } from "react";
import type { Alibaba1688BrowserJob, WorkbenchDetail, WorkbenchExceptionInfo, WorkbenchOrderItem, WorkbenchSummary } from "@/lib/api";
import { Alibaba1688BrowserCard } from "@/app/data-center-import/panels/alibaba1688-browser-card";

export type SupplyChainStatusFilter = "all" | "refine" | "po" | "inbound" | "transit" | "invoice" | "exception" | "done";
export type SupplyChainChannelFilter = "all" | "1688" | "pdd" | "taobao" | "other";
export type SupplyChainKindFilter = "all" | "goods" | "consumable";

type Props = {
  summary: WorkbenchSummary | null;
  orders: WorkbenchOrderItem[];
  total: number;
  page: number;
  pageSize: number;
  selectedOrderId: number | null;
  selectedOrderIds: number[];
  detail: WorkbenchDetail | null;
  loading: boolean;
  detailLoading: boolean;
  alibaba1688Job: Alibaba1688BrowserJob | null;
  refreshBusy: boolean;
  exportBusy: boolean;
  statusFilter: SupplyChainStatusFilter;
  channelFilter: SupplyChainChannelFilter;
  kindFilter: SupplyChainKindFilter;
  warehouseFilter: string;
  startDate: string;
  endDate: string;
  searchDraft: string;
  onStatusFilterChange: (value: SupplyChainStatusFilter) => void;
  onOpenExceptions: () => void;
  onChannelFilterChange: (value: SupplyChainChannelFilter) => void;
  onKindFilterChange: (value: SupplyChainKindFilter) => void;
  onWarehouseFilterChange: (value: string) => void;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
  onSearchDraftChange: (value: string) => void;
  onApplySearch: () => void;
  onReset: () => void;
  onNewOrder: () => void;
  onExport: () => void;
  onRefresh: () => void;
  onSelectOrder: (orderId: number) => void;
  onToggleOrder: (orderId: number) => void;
  onToggleAllVisible: () => void;
  onClearOrderSelection: () => void;
  onDeleteSelected: () => void;
  deleteBusy: boolean;
  onPageChange: (page: number) => void;
  onOpenOperations: (orderId?: number) => void;
  onCloseOperations: () => void;
  operationsOpen: boolean;
  onOpenWorkbenchView: (view: "suppliers" | "chain" | "matching") => void;
};

type DetailAllocation = {
  id?: number;
  skuId?: number | string | null;
  skuCode?: string;
  goodsName?: string;
  quantity?: number | string | null;
  unitPrice?: number | string | null;
  amount?: number | string | null;
  inboundDocumentId?: number | null;
  warehouseId?: number | null;
  warehouseName?: string;
  currentStock?: number | null;
  note?: string;
};

type DetailOrderItem = {
  skuId?: string;
  productNumber?: string;
  productName?: string;
  spec?: string;
  quantity?: number | string | null;
  receivedQuantity?: number | string | null;
  unitPrice?: number | string | null;
  amount?: number | string | null;
  statusLabel?: string;
};

type DetailUsageItem = {
  id?: number;
  consumableId?: number;
  consumableName?: string;
  consumableCode?: string;
  quantity?: string;
  unit?: string;
  note?: string;
};

type DetailInbound = {
  linkId?: number | null;
  documentId?: number | null;
  targetId?: number | null;
  goodsdocNo?: string;
  warehouseName?: string;
  date?: string | null;
  consumableUsageEnabled?: boolean | null;
  itemCount?: number;
  consumableUsageDecided?: boolean;
  consumableUsageItems?: DetailUsageItem[];
};

type DetailPurchaseOrder = { purchNo?: string; status?: string };
type DetailWarehouse = {
  warehouseName: string;
  jackyunWarehouseId: string;
  isSellable: boolean | null;
  currentStock: number | null;
  inTransitQty: number;
};
type DetailConsumable = {
  receiptNo?: string;
  location?: string;
  warehouseName?: string;
  received?: boolean;
  items?: Array<{ name?: string; code?: string; quantity?: number | string | null; receivedQty?: number | string | null; unit?: string; unitCost?: number | string | null }>;
};

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function dateText(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10) || "—";
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function datetimeText(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return `${dateText(value)} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function numberText(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const numericValue = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(numericValue)) return "—";
  return numericValue.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function usageQuantityText(value: number | string | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const numericValue = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(numericValue)) return "—";
  return Number.isInteger(numericValue)
    ? numericValue.toLocaleString("zh-CN")
    : numericValue.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function text(value: unknown) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

const STATUS_BY_STEP: Record<string, string> = {
  content: "待确认采购内容",
  sku: "待匹配SKU",
  jackyun_po: "待完成本系统采购单",
  inbound: "待入库",
  invoice: "待开发票",
};

function isClosedOrder(orderStatus?: string | null) {
  return /关闭|已取消|交易取消|closed|canceled|cancelled/i.test((orderStatus || "").trim());
}

function getStatus(item: WorkbenchOrderItem) {
  if (isClosedOrder(item.orderStatus)) return "已关闭";
  if (item.hasException) return "异常";

  // 当前状态以最早未完成环节为准，避免「已入库」覆盖掉后续待开票/待付款状态。
  const step = item.firstUndone || ["content", "sku", "jackyun_po", "inbound", "invoice", "closeout"]
    .find((key) => item.stepStates?.[key] && !item.stepStates[key].done);
  if (step === "closeout") {
    if (item.closeoutStage === "awaiting_inbound") return "待入库";
    if (item.closeoutStage === "awaiting_invoice") return "待开发票";
    if (item.closeoutStage === "awaiting_payment") return "待付款";
    if (item.closeoutStage === "awaiting_verification") return "待认证";
    return "待处理";
  }
  if (step && STATUS_BY_STEP[step]) return STATUS_BY_STEP[step];

  const purchaseStatus = item.purchaseStatus || "";
  if (purchaseStatus === "producing") return "生产中";
  if (purchaseStatus === "shipped") return "已发货";
  if (purchaseStatus === "arrived") return "在途";
  if (item.stepStates?.closeout?.done || item.invoiceDone || purchaseStatus === "done") return "开票完成";
  if (purchaseStatus === "inbound") return "已入库";
  if (purchaseStatus === "confirmed" || purchaseStatus === "jackyun_linked") return "待生产";
  if (purchaseStatus === "pending_refine") return "待确认采购内容";
  if (item.inboundDone) return "已入库";
  return item.firstUndoneLabel && item.firstUndoneLabel !== "入库/发票/付款" ? item.firstUndoneLabel : "待处理";
}

function statusTone(status: string) {
  if (status === "已关闭") return "bg-slate-100 text-slate-500";
  if (status === "异常") return "bg-rose-50 text-rose-600";
  if (status === "开票完成") return "bg-emerald-50 text-emerald-600";
  if (status === "已入库") return "bg-emerald-50 text-emerald-600";
  if (status === "已发货") return "bg-emerald-50 text-emerald-600";
  if (status === "在途") return "bg-violet-50 text-violet-600";
  if (status === "待匹配SKU") return "bg-amber-50 text-amber-700";
  if (status === "待完成本系统采购单") return "bg-blue-50 text-blue-600";
  if (status === "待入库") return "bg-sky-50 text-sky-600";
  if (status === "待开发票") return "bg-violet-50 text-violet-600";
  if (status === "待付款") return "bg-rose-50 text-rose-600";
  if (status === "待认证") return "bg-sky-50 text-sky-600";
  if (status === "待确认采购内容") return "bg-orange-50 text-orange-600";
  if (status === "生产中" || status === "待生产") return "bg-blue-50 text-blue-600";
  return "bg-amber-50 text-amber-700";
}

/** 异常订单的报错原因（多条用「；」连接），无可展示原因时回退标题。 */
function exceptionMessages(info?: WorkbenchExceptionInfo[]) {
  return (info ?? [])
    .map((entry) => entry.message || entry.reason || entry.title || "")
    .filter(Boolean)
    .join("；");
}

function platformMeta(platform?: string) {
  const raw = (platform || "").toLowerCase();
  if (raw.includes("pdd") || raw.includes("拼多多")) return { label: "拼多多", tone: "bg-rose-500" };
  if (raw.includes("taobao") || raw.includes("淘宝") || raw.includes("tmall")) return { label: "淘宝", tone: "bg-orange-500" };
  if (raw === "1688" || raw.includes("阿里")) return { label: "1688", tone: "bg-orange-500" };
  return { label: "其他", tone: "bg-slate-500" };
}

function kindText(item: WorkbenchOrderItem) {
  return item.orderKind === "consumable" ? "耗材" : "正品（生产）";
}

function kindTone(item: WorkbenchOrderItem) {
  return item.orderKind === "consumable" ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-600";
}

type OrderColumnKey = "orderNo" | "platform" | "orderKind" | "productName" | "quantity" | "supplier" | "warehouse" | "orderDate" | "status" | "logisticsNo" | "jackyunInboundNo" | "amount" | "paidAmount" | "expectedArrival" | "remark" | "operation";
type OrderColumnDefinition = { key: OrderColumnKey; label: string; defaultWidth: number; align?: "left" | "right" };

const ORDER_COLUMN_DEFINITIONS: OrderColumnDefinition[] = [
  { key: "orderNo", label: "订单号", defaultWidth: 165 },
  { key: "platform", label: "渠道", defaultWidth: 64 },
  { key: "orderKind", label: "采购类型", defaultWidth: 105 },
  { key: "productName", label: "商品名称", defaultWidth: 145 },
  { key: "quantity", label: "数量", defaultWidth: 76, align: "right" },
  { key: "supplier", label: "供应商/工厂", defaultWidth: 125 },
  { key: "warehouse", label: "仓库", defaultWidth: 100 },
  { key: "orderDate", label: "下单日期", defaultWidth: 102 },
  { key: "status", label: "当前状态", defaultWidth: 168 },
  { key: "logisticsNo", label: "物流单号", defaultWidth: 120 },
  { key: "jackyunInboundNo", label: "吉客云入库单", defaultWidth: 134 },
  { key: "amount", label: "订单金额", defaultWidth: 100, align: "right" },
  { key: "paidAmount", label: "实付金额", defaultWidth: 100, align: "right" },
  { key: "expectedArrival", label: "预计完成", defaultWidth: 102 },
  { key: "remark", label: "备注", defaultWidth: 180 },
  { key: "operation", label: "操作", defaultWidth: 86 },
];

function Icon({ name, size = 20 }: { name: "document" | "box" | "truck" | "alert" | "search" | "bell" | "plus" | "refresh" | "edit" | "chevron" | "more" | "warehouse" | "route" | "material" | "user" | "check"; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", "aria-hidden": true } as const;
  if (name === "search") return <svg {...common}><circle cx="11" cy="11" r="5.5" stroke="currentColor" strokeWidth="1.8" /><path d="m16 16 4 4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "bell") return <svg {...common}><path d="M5 17h14l-1.5-2.2V10a5.5 5.5 0 0 0-11 0v4.8L5 17Zm5 3h4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "plus") return <svg {...common}><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" /></svg>;
  if (name === "refresh") return <svg {...common}><path d="M19 8V4m0 0h-4m4 0a8 8 0 1 0 1 10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "edit") return <svg {...common}><path d="m5 16.5-.5 3.5 3.5-.5L19 8.5 15.5 5 5 16.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="m13.5 7 3.5 3.5" stroke="currentColor" strokeWidth="1.7" /></svg>;
  if (name === "truck" || name === "route") return <svg {...common}><path d="M3 6h11v10H3V6Zm11 4h4l3 3v3h-7v-6Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><circle cx="7" cy="18" r="1.6" stroke="currentColor" strokeWidth="1.7" /><circle cx="18" cy="18" r="1.6" stroke="currentColor" strokeWidth="1.7" /></svg>;
  if (name === "warehouse") return <svg {...common}><path d="M3.5 20V8.2L12 4l8.5 4.2V20M7 20v-6h10v6M8 10h.01M12 10h.01M16 10h.01" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "material") return <svg {...common}><path d="m4 8 8-4 8 4-8 4-8-4Zm0 4 8 4 8-4m-16 4 8 4 8-4" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /></svg>;
  if (name === "alert") return <svg {...common}><path d="M12 4 21 20H3L12 4Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="M12 9v5m0 3h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "user") return <svg {...common}><circle cx="12" cy="8" r="3" stroke="currentColor" strokeWidth="1.7" /><path d="M5 20c.7-3.4 3.2-5 7-5s6.3 1.6 7 5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>;
  if (name === "chevron") return <svg {...common}><path d="m8 10 4 4 4-4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "more") return <svg {...common}><circle cx="5" cy="12" r="1.2" fill="currentColor" /><circle cx="12" cy="12" r="1.2" fill="currentColor" /><circle cx="19" cy="12" r="1.2" fill="currentColor" /></svg>;
  if (name === "check") return <svg {...common}><path d="m5 12.5 4.5 4.5L19 7.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "box") return <svg {...common}><path d="m4 7.5 8-4 8 4v9l-8 4-8-4v-9Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" /><path d="m4 7.5 8 4 8-4M12 11.5V20" stroke="currentColor" strokeWidth="1.7" /></svg>;
  return <svg {...common}><path d="M6 3h9l3 3v15H6V3Zm3 7h6m-6 4h6m-6 4h4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function MetricCard({ label, value, leftHint, rightHint, tone, icon, onClick, active }: { label: string; value: number | string; leftHint: string; rightHint?: string; tone: "blue" | "green" | "orange" | "purple" | "red" | "amber" | "cyan" | "indigo" | "slate"; icon: ReactNode; onClick?: () => void; active?: boolean }) {
  const styles = {
    blue: "border-blue-100 bg-gradient-to-br from-blue-50 to-[#f4f8ff] text-blue-600",
    green: "border-emerald-100 bg-gradient-to-br from-emerald-50 to-[#f2fcf7] text-emerald-600",
    orange: "border-orange-100 bg-gradient-to-br from-orange-50 to-[#fff8ee] text-orange-500",
    purple: "border-violet-100 bg-gradient-to-br from-violet-50 to-[#faf8ff] text-violet-600",
    red: "border-rose-100 bg-gradient-to-br from-rose-50 to-[#fff7f7] text-rose-500",
    amber: "border-amber-100 bg-gradient-to-br from-amber-50 to-[#fffaef] text-amber-600",
    cyan: "border-cyan-100 bg-gradient-to-br from-cyan-50 to-[#f2fbff] text-cyan-600",
    indigo: "border-indigo-100 bg-gradient-to-br from-indigo-50 to-[#f5f6ff] text-indigo-600",
    slate: "border-slate-200 bg-gradient-to-br from-slate-50 to-[#f8fafc] text-slate-600",
  }[tone];
  const interactive = Boolean(onClick);
  return <article
    role={interactive ? "button" : undefined}
    tabIndex={interactive ? 0 : undefined}
    onClick={onClick}
    onKeyDown={interactive ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onClick?.(); } } : undefined}
    className={cx("flex min-h-[66px] items-center gap-2 rounded-lg border px-2.5 py-2", styles, interactive && "cursor-pointer transition-shadow hover:shadow-md focus:outline-none focus:ring-2 focus:ring-rose-200", active && "ring-2 ring-blue-400/70 ring-offset-1")}
  >
    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white/65 shadow-[0_5px_16px_rgba(78,117,178,.08)]">{icon}</span>
    <div className="min-w-0 text-slate-800">
      <div className="text-[12px] font-medium text-slate-600">{label}</div>
      <div className="mt-0.5 text-[21px] font-semibold leading-none tabular-nums text-slate-900">{value}</div>
    </div>
    <div className="ml-auto flex max-w-[96px] shrink-0 flex-col items-end gap-0.5 text-right text-[10px] leading-3 text-slate-500">
      <span>{leftHint}</span>
      {rightHint && <span>{rightHint}</span>}
    </div>
  </article>;
}

function DetailCard({ title, children, action }: { title: ReactNode; children: ReactNode; action?: ReactNode }) {
  return <article className="min-w-0 rounded-lg border border-slate-200 bg-white px-2.5 py-2 shadow-[0_2px_10px_rgba(47,69,112,.025)]"><div className="mb-1.5 flex items-center justify-between gap-2"><div role="heading" aria-level={3} className="min-w-0 text-[13px] font-semibold text-slate-800">{title}</div>{action}</div>{children}</article>;
}

type DetailProductRow = {
  key: string;
  skuId?: number | string | null;
  name: string;
  code: string;
  spec: string;
  quantity: number | string | null;
  receivedQuantity: number | string | null;
  unit: string;
  unitPrice: number | string | null;
  amount: number | string | null;
  sourceLabel: string;
  inboundDocumentId: number | null;
  warehouseName?: string;
  currentStock?: number | null;
  note: string;
};

type DetailColumnKey = "inbound" | "sku" | "product" | "spec" | "quantity" | "unitPrice" | "amount" | "consumableName" | "usage" | "status" | "flowStatus" | "warehouse";
type DetailColumnDefinition = { key: DetailColumnKey; label: string; defaultWidth: number; minWidth: number; maxWidth: number; align?: "left" | "right" };
type DetailColumnPreferences = Record<DetailColumnKey, { visible: boolean; width: number }>;
type PersistedDetailColumnConfig = { order: DetailColumnKey[]; preferences: DetailColumnPreferences };

const DETAIL_COLUMN_DEFINITIONS: DetailColumnDefinition[] = [
  { key: "inbound", label: "入库单", defaultWidth: 180, minWidth: 130, maxWidth: 260 },
  { key: "sku", label: "SKU", defaultWidth: 120, minWidth: 90, maxWidth: 220 },
  { key: "product", label: "商品", defaultWidth: 230, minWidth: 150, maxWidth: 360 },
  { key: "spec", label: "规格 / 来源", defaultWidth: 175, minWidth: 120, maxWidth: 300 },
  { key: "quantity", label: "数量", defaultWidth: 82, minWidth: 60, maxWidth: 130, align: "right" },
  { key: "unitPrice", label: "单价", defaultWidth: 72, minWidth: 60, maxWidth: 120, align: "right" },
  { key: "amount", label: "金额", defaultWidth: 88, minWidth: 72, maxWidth: 140, align: "right" },
  { key: "consumableName", label: "耗材名称", defaultWidth: 220, minWidth: 140, maxWidth: 340 },
  { key: "usage", label: "用量", defaultWidth: 105, minWidth: 70, maxWidth: 150, align: "right" },
  { key: "status", label: "耗材处理状态", defaultWidth: 105, minWidth: 80, maxWidth: 150, align: "right" },
  { key: "flowStatus", label: "订单实时状态", defaultWidth: 320, minWidth: 220, maxWidth: 430 },
  { key: "warehouse", label: "仓库 / 库存", defaultWidth: 330, minWidth: 220, maxWidth: 460 },
];

const DEFAULT_DETAIL_COLUMN_ORDER: DetailColumnKey[] = DETAIL_COLUMN_DEFINITIONS.map((column) => column.key);
const DETAIL_COLUMN_BY_KEY: Record<DetailColumnKey, DetailColumnDefinition> = Object.fromEntries(
  DETAIL_COLUMN_DEFINITIONS.map((column) => [column.key, column])
) as Record<DetailColumnKey, DetailColumnDefinition>;
const DETAIL_COLUMN_STORAGE_KEY = "sc-replica-detail-column-config-v1";

function defaultDetailColumnPreferences(): DetailColumnPreferences {
  return Object.fromEntries(DETAIL_COLUMN_DEFINITIONS.map((column) => [column.key, { visible: true, width: column.defaultWidth }])) as DetailColumnPreferences;
}

function loadPersistedDetailColumns(): PersistedDetailColumnConfig | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(DETAIL_COLUMN_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedDetailColumnConfig>;
    if (!Array.isArray(parsed.order) || !parsed.preferences) return null;
    const valid = parsed.order.filter((key): key is DetailColumnKey => Boolean(DETAIL_COLUMN_BY_KEY[key]));
    const missing = DEFAULT_DETAIL_COLUMN_ORDER.filter((key) => !valid.includes(key));
    const defaults = defaultDetailColumnPreferences();
    const persisted = parsed.preferences as Partial<DetailColumnPreferences>;
    const preferences = Object.fromEntries(
      DETAIL_COLUMN_DEFINITIONS.map((column) => {
        const saved = persisted[column.key];
        return [column.key, { ...defaults[column.key], ...(saved && typeof saved === "object" ? saved : {}) }];
      })
    ) as DetailColumnPreferences;
    return { order: [...valid, ...missing], preferences };
  } catch {
    return null;
  }
}

function inboundDocumentIdOf(item: DetailAllocation) {
  if (item.inboundDocumentId != null) return item.inboundDocumentId;
  const matched = /由入库单\s*#(\d+)/.exec(item.note || "");
  return matched ? Number(matched[1]) : null;
}

function detailProducts(detail: WorkbenchDetail): DetailProductRow[] {
  const allocations = (detail.detail.allocations || []) as DetailAllocation[];
  if (allocations.length) {
    return allocations.map((item, index) => ({
      key: `allocation-${item.id ?? index}`,
      skuId: item.skuId,
      name: item.goodsName || item.skuCode || "未命名货品",
      code: item.skuCode || (item.skuId ? String(item.skuId) : ""),
      spec: "",
      quantity: item.quantity ?? null,
      receivedQuantity: null,
      unit: "",
      unitPrice: item.unitPrice ?? null,
      amount: item.amount ?? null,
      sourceLabel: "",
      inboundDocumentId: inboundDocumentIdOf(item),
      warehouseName: item.warehouseName,
      currentStock: item.currentStock,
      note: item.note || "",
    }));
  }

  const source = (detail.detail.orderItems || []) as DetailOrderItem[];
  if (source.length) {
    return source.map((item, index) => ({
      key: `source-${item.skuId || item.productNumber || index}`,
      skuId: item.skuId,
      name: item.productName || item.productNumber || "未命名货品",
      code: item.productNumber || item.skuId || "",
      spec: item.spec || "",
      quantity: item.quantity ?? null,
      receivedQuantity: item.receivedQuantity ?? null,
      unit: "",
      unitPrice: item.unitPrice ?? null,
      amount: item.amount ?? null,
      sourceLabel: "1688 原始订单明细",
      inboundDocumentId: null,
      note: item.statusLabel || "",
    }));
  }

  const consumable = detail.detail.consumable as DetailConsumable | null;
  const items = consumable?.items || [];
  if (items.length) {
    return items.map((item, index) => ({
      key: `consumable-${item.code || index}`,
      name: item.name || item.code || "未命名耗材",
      code: item.code || "",
      spec: "",
      quantity: item.quantity ?? null,
      receivedQuantity: item.receivedQty ?? null,
      unit: item.unit || "",
      unitPrice: item.unitCost ?? null,
      amount: null,
      sourceLabel: "耗材档案",
      inboundDocumentId: null,
      note: "",
    }));
  }

  return [{
    key: "fallback",
    name: detail.order.title || "未关联货品",
    code: "",
    spec: "",
    quantity: null,
    receivedQuantity: null,
    unit: "",
    unitPrice: null,
    amount: null,
    sourceLabel: "待完善采购内容",
    inboundDocumentId: null,
    note: "",
  }];
}

function selectedProduct(detail: WorkbenchDetail) {
  const first = detailProducts(detail)[0];
  return { name: first?.name || "—", quantity: first?.quantity ?? null, unit: first?.unit || "" };
}

function ProductDetailCard({ orderId, orderKind, products, rawOrderItems, hasAllocations, usage, inbound, purchaseOrders, warehouse, fallbackWarehouse, fallbackReceiptNo, fallbackReceived, unit, detailsHref, currentStatus }: { orderId: number; orderKind?: "goods" | "consumable"; products: DetailProductRow[]; rawOrderItems: DetailOrderItem[]; hasAllocations: boolean; usage: DetailUsageItem[]; inbound: DetailInbound[]; purchaseOrders: DetailPurchaseOrder[]; warehouse: DetailWarehouse; fallbackWarehouse?: string; fallbackReceiptNo?: string; fallbackReceived: boolean; unit: string; detailsHref: string; currentStatus: string }) {
  const rawSummary = rawOrderItems
    .map((item) => item.productName || item.productNumber || item.skuId || "")
    .filter(Boolean)
    .join("、");
  const purchaseNo = purchaseOrders.map((row) => row.purchNo).filter(Boolean).join("、") || "—";
  const defaultWarehouseName = warehouse.warehouseName || fallbackWarehouse || "待指定";
  const inventoryHref = `/inventory${defaultWarehouseName !== "待指定" ? `?warehouse=${encodeURIComponent(defaultWarehouseName)}` : ""}`;
  const [detailPersisted] = useState<PersistedDetailColumnConfig | null>(() => loadPersistedDetailColumns());
  const [detailColumnPreferences, setDetailColumnPreferences] = useState<DetailColumnPreferences>(detailPersisted?.preferences ?? defaultDetailColumnPreferences);
  const [detailColumnOrder, setDetailColumnOrder] = useState<DetailColumnKey[]>(detailPersisted?.order ?? DEFAULT_DETAIL_COLUMN_ORDER);
  const [detailColumnSettingsOpen, setDetailColumnSettingsOpen] = useState(false);
  const [detailDraggedColumn, setDetailDraggedColumn] = useState<DetailColumnKey | null>(null);
  const [detailResizeState, setDetailResizeState] = useState<{ key: DetailColumnKey; startX: number; startWidth: number } | null>(null);
  const visibleDetailColumns = detailColumnOrder
    .map((key) => DETAIL_COLUMN_BY_KEY[key])
    .filter((column) => column && detailColumnPreferences[column.key].visible);
  const detailTableMinWidth = Math.max(1200, visibleDetailColumns.reduce((total, column) => total + detailColumnPreferences[column.key].width, 0));

  function startDetailColumnResize(event: PointerEvent<HTMLSpanElement>, key: DetailColumnKey) {
    event.preventDefault();
    event.stopPropagation();
    setDetailResizeState({ key, startX: event.clientX, startWidth: detailColumnPreferences[key].width });
  }

  function handleDetailColumnDragStart(event: DragEvent<HTMLTableCellElement>, key: DetailColumnKey) {
    setDetailDraggedColumn(key);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", key);
  }

  function handleDetailColumnDrop(event: DragEvent<HTMLTableCellElement>, targetKey: DetailColumnKey) {
    event.preventDefault();
    const sourceKey = detailDraggedColumn || event.dataTransfer.getData("text/plain") as DetailColumnKey;
    if (!sourceKey || sourceKey === targetKey) {
      setDetailDraggedColumn(null);
      return;
    }
    setDetailColumnOrder((current) => {
      const sourceIndex = current.indexOf(sourceKey);
      const targetIndex = current.indexOf(targetKey);
      if (sourceIndex === -1 || targetIndex === -1) return current;
      const next = [...current];
      next.splice(sourceIndex, 1);
      next.splice(targetIndex, 0, sourceKey);
      return next;
    });
    setDetailDraggedColumn(null);
  }

  function updateDetailColumnPreference(key: DetailColumnKey, update: Partial<DetailColumnPreferences[DetailColumnKey]>) {
    setDetailColumnPreferences((current) => ({ ...current, [key]: { ...current[key], ...update } }));
  }

  function moveDetailColumn(key: DetailColumnKey, direction: "up" | "down") {
    setDetailColumnOrder((current) => {
      const index = current.indexOf(key);
      const target = direction === "up" ? index - 1 : index + 1;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function resetDetailColumnPreferences() {
    setDetailColumnPreferences(defaultDetailColumnPreferences());
    setDetailColumnOrder(DEFAULT_DETAIL_COLUMN_ORDER);
  }

  useEffect(() => {
    try {
      window.localStorage.setItem(DETAIL_COLUMN_STORAGE_KEY, JSON.stringify({ order: detailColumnOrder, preferences: detailColumnPreferences }));
    } catch {
      /* localStorage 不可用时静默忽略 */
    }
  }, [detailColumnOrder, detailColumnPreferences]);

  useEffect(() => {
    if (!detailResizeState) return;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;
    const handlePointerMove = (event: globalThis.PointerEvent) => {
      const column = DETAIL_COLUMN_BY_KEY[detailResizeState.key];
      const nextWidth = Math.min(column.maxWidth, Math.max(column.minWidth, detailResizeState.startWidth + event.clientX - detailResizeState.startX));
      setDetailColumnPreferences((current) => current[detailResizeState.key].width === nextWidth
        ? current
        : { ...current, [detailResizeState.key]: { ...current[detailResizeState.key], width: nextWidth } });
    };
    const handlePointerUp = () => setDetailResizeState(null);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
    };
  }, [detailResizeState]);

  const quantityWithUnit = (value: number | string | null | undefined) => {
    const formatted = numberText(value);
    return formatted === "—" ? formatted : `${formatted}${unit ? ` ${unit}` : ""}`;
  };
  type DetailLine = { key: string; product?: DetailProductRow; material?: DetailUsageItem; inbound?: DetailInbound };
  const detailLines: DetailLine[] = [];
  const usedProductKeys = new Set<string>();

  // 与编辑弹窗保持同一口径：先遍历吉客云入库单，再取该单据反填的 SKU 分配和耗材登记。
  if (inbound.length > 0) {
    inbound.forEach((inboundRow, inboundIndex) => {
      const documentId = inboundRow.documentId ?? inboundRow.targetId ?? null;
      const documentProducts = products.filter((product) => documentId != null && product.inboundDocumentId === documentId);
      const documentMaterials = inboundRow.consumableUsageItems || [];
      if (documentProducts.length > 0) {
        documentProducts.forEach((product, productIndex) => {
          usedProductKeys.add(product.key);
          detailLines.push({ key: `${inboundRow.goodsdocNo || inboundIndex}-${product.key}`, product, material: documentMaterials[productIndex], inbound: inboundRow });
        });
      } else {
        detailLines.push({ key: `${inboundRow.goodsdocNo || inboundIndex}-empty`, material: documentMaterials[0], inbound: inboundRow });
      }
    });
    // 人工补录的 SKU 没有 inboundDocumentId，仍保留在表内，避免被入库主视角过滤掉。
    products.filter((product) => !usedProductKeys.has(product.key)).forEach((product) => {
      detailLines.push({ key: `manual-${product.key}`, product });
    });
  } else {
    detailLines.push(...Array.from({ length: Math.max(products.length, usage.length, 1) }, (_, index) => ({
      key: `${products[index]?.key || "material"}-${index}`,
      product: products[index],
      material: usage[index],
    })));
  }
  const rows = detailLines.length > 0 ? detailLines : [{ key: "empty" }];
  const materialCount = inbound.length > 0 ? inbound.reduce((total, row) => total + (row.consumableUsageItems?.length || 0), 0) : usage.length;
  const consumableOnly = orderKind === "consumable";
  const detailTitle = consumableOnly
    ? `采购明细 · ${products.length} 项耗材`
    : `采购明细 · ${products.length} 款货品${materialCount ? ` · ${materialCount} 项耗材` : ""}`;
  return <DetailCard
    title={<div className="flex min-w-0 items-center gap-2"><span className="truncate">{detailTitle}</span><span className={cx("shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-medium", statusTone(currentStatus))}>订单实时状态：{currentStatus}</span></div>}
    action={<div className="flex items-center gap-2"><span className="text-[10px] text-slate-400">{hasAllocations ? "实际采购内容" : "订单原始内容"}</span><span className="hidden text-[9px] text-slate-400 xl:inline">拖动列名排序 · 拖动竖线调宽</span><div className="relative"><button type="button" aria-haspopup="dialog" aria-expanded={detailColumnSettingsOpen} onClick={() => setDetailColumnSettingsOpen((open) => !open)} className="inline-flex h-6 items-center gap-1 rounded border border-slate-200 bg-white px-1.5 text-[10px] font-medium text-slate-500 hover:border-blue-300 hover:text-blue-600">字段 <span className="text-[9px] text-slate-400">{visibleDetailColumns.length}/{DETAIL_COLUMN_DEFINITIONS.length}</span><span aria-hidden>⌄</span></button>{detailColumnSettingsOpen && <div role="dialog" aria-label="采购明细字段设置" className="absolute right-0 top-7 z-dropdown w-[300px] rounded-lg border border-slate-200 bg-white p-2.5 text-left shadow-xl"><div className="mb-1.5 flex items-center justify-between border-b border-slate-100 pb-1.5"><div><div className="text-[11px] font-semibold text-slate-800">采购明细字段</div><div className="mt-0.5 text-[9px] text-slate-400">勾选显示，↑↓ 调整顺序，拖动表头也可排序</div></div><span className="text-[9px] text-slate-400">{visibleDetailColumns.length}/{DETAIL_COLUMN_DEFINITIONS.length}</span></div><div className="max-h-[300px] space-y-1 overflow-y-auto pr-1">{detailColumnOrder.map((key, index) => { const column = DETAIL_COLUMN_BY_KEY[key]; const preference = detailColumnPreferences[key]; return <div key={key} className="flex items-center gap-1.5 rounded border border-slate-100 px-1.5 py-1"><div className="flex shrink-0 gap-0.5"><button type="button" aria-label={`上移 ${column.label}`} disabled={index === 0} onClick={() => moveDetailColumn(key, "up")} className="h-4 w-4 rounded border border-slate-200 text-[8px] text-slate-500 disabled:opacity-30">▲</button><button type="button" aria-label={`下移 ${column.label}`} disabled={index === detailColumnOrder.length - 1} onClick={() => moveDetailColumn(key, "down")} className="h-4 w-4 rounded border border-slate-200 text-[8px] text-slate-500 disabled:opacity-30">▼</button></div><label className="flex min-w-0 flex-1 items-center gap-1.5 text-[10px] text-slate-700"><input type="checkbox" checked={preference.visible} disabled={preference.visible && visibleDetailColumns.length <= 1} onChange={(event) => updateDetailColumnPreference(key, { visible: event.target.checked })} /><span className="truncate">{column.label}</span></label><input type="range" min={column.minWidth} max={column.maxWidth} value={preference.width} onChange={(event) => updateDetailColumnPreference(key, { width: Number(event.target.value) })} aria-label={`${column.label}列宽`} className="w-16 accent-blue-600" /><span className="w-7 text-right text-[8px] tabular-nums text-slate-400">{preference.width}</span></div>; })}</div><div className="mt-1.5 flex items-center justify-between border-t border-slate-100 pt-1.5"><button type="button" onClick={resetDetailColumnPreferences} className="text-[9px] text-slate-500 hover:text-blue-600">恢复默认</button><button type="button" onClick={() => setDetailColumnSettingsOpen(false)} className="rounded bg-blue-600 px-2 py-1 text-[9px] font-medium text-white hover:bg-blue-700">完成</button></div></div>}</div>{!consumableOnly && <Link href={`/supply-chain/material-flow?order=${orderId}`} className="shrink-0 text-[10px] font-medium text-blue-600 hover:text-blue-700">耗材明细 ↗</Link>}<Link href={`${detailsHref}&tab=jackyun`} className="shrink-0 text-[10px] font-medium text-blue-600 hover:text-blue-700">吉客云 ↗</Link><Link href={inventoryHref} className="shrink-0 text-[10px] font-medium text-blue-600 hover:text-blue-700">库存 ↗</Link></div>}
  >
    <div className="max-h-[190px] overflow-auto rounded-md border border-slate-100">
      <table aria-label="采购明细（货品、耗材与入库仓库）" className="w-full table-fixed text-[11px] leading-tight" style={{ minWidth: `${detailTableMinWidth}px` }}>
        <thead className="sticky top-0 z-10 bg-slate-50 text-slate-500">
          <tr>
            {visibleDetailColumns.map((column) => <th key={column.key} draggable onDragStart={(event) => handleDetailColumnDragStart(event, column.key)} onDragOver={(event) => event.preventDefault()} onDrop={(event) => handleDetailColumnDrop(event, column.key)} onDragEnd={() => setDetailDraggedColumn(null)} style={{ width: `${detailColumnPreferences[column.key].width}px` }} className={cx("group relative select-none px-2.5 py-1 font-medium", column.align === "right" ? "text-right" : "text-left", detailDraggedColumn === column.key ? "opacity-40" : "cursor-grab")} title="拖动列名调整顺序"><span>{column.label}</span><span aria-label={`调整${column.label}列宽`} role="separator" onPointerDown={(event) => startDetailColumnResize(event, column.key)} className="absolute inset-y-0 right-0 z-10 w-1 cursor-col-resize bg-slate-200/60 transition-colors hover:bg-blue-400" /></th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ product: item, material: materialRow, inbound: inboundRow }, index) => {
            const productSource = item
              ? [item.spec || item.sourceLabel].filter(Boolean).join(" · ")
              : inboundRow ? `吉客云入库单明细${inboundRow.itemCount ? `（${inboundRow.itemCount} 行，未反填 SKU）` : "（未反填 SKU）"}` : "";
            const materialName = materialRow?.consumableName || materialRow?.consumableCode || "";
            const rowInboundNo = inboundRow?.goodsdocNo || (!inboundRow && fallbackReceiptNo) || "—";
            const rowInboundStatus = inboundRow || fallbackReceived ? "已入库" : "待入库";
            const rowInboundDate = inboundRow?.date ? datetimeText(inboundRow.date) : "—";
            const rowWarehouseName = item?.warehouseName || inboundRow?.warehouseName || defaultWarehouseName;
            const rowCurrentStock = item ? item.currentStock : null;
            const usageStatus = materialRow
              ? "已使用"
              : inboundRow?.consumableUsageDecided
                ? inboundRow.consumableUsageEnabled ? "已自动扣减" : "无需耗材"
                : inboundRow ? "待维护映射" : "—";
            const usageStatusClass = usageStatus === "已使用" || usageStatus === "已自动扣减"
              ? "bg-emerald-50 text-emerald-600"
              : usageStatus === "待维护映射"
                ? "bg-amber-50 text-amber-700"
                : "bg-slate-100 text-slate-500";
            const detailCells: Record<DetailColumnKey, ReactNode> = {
              inbound: <span className={cx("block truncate font-mono font-medium", rowInboundNo === "—" ? "text-slate-400" : "text-blue-600")} title={`入库单号：${rowInboundNo}`}>{rowInboundNo}</span>,
              sku: item ? <span className="block truncate font-mono text-[10px] text-indigo-500" title={item.code || ""}>{item.code || "—"}</span> : <span className="text-slate-300">—</span>,
              product: item ? <span className="block truncate font-medium text-slate-700" title={item.name}>{item.name}</span> : inboundRow ? <span className="text-amber-600">暂无反填 SKU 明细</span> : <span className="text-slate-300">—</span>,
              spec: <span className="block truncate text-slate-500" title={productSource}>{productSource || "—"}</span>,
              quantity: item ? `${numberText(item.quantity)}${item.unit ? ` ${item.unit}` : ""}` : "—",
              unitPrice: item?.unitPrice === null || item?.unitPrice === undefined ? "—" : `¥${numberText(item.unitPrice)}`,
              amount: item?.amount === null || item?.amount === undefined ? "—" : `¥${numberText(item.amount)}`,
              consumableName: <span className="block truncate text-slate-600" title={materialName}>{materialName || "—"}</span>,
              usage: materialRow ? `${usageQuantityText(materialRow.quantity)}${materialRow.unit ? ` ${materialRow.unit}` : ""}` : "—",
              status: usageStatus === "—" ? "—" : <span className={cx("rounded px-1 py-0.5 text-[10px]", usageStatusClass)}>{usageStatus}</span>,
              flowStatus: <span className="block truncate" title={`订单状态：${currentStatus} · 采购单号：${purchaseNo} · 入库状态：${rowInboundStatus} · 创建时间：${rowInboundDate}`}><span className="text-slate-400">订单</span> <span className={cx("rounded px-1 py-0.5 text-[10px]", statusTone(currentStatus))}>{currentStatus}</span> <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">采购单号</span> {purchaseNo} <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">入库</span> <span className={cx("rounded px-1 py-0.5 text-[10px]", rowInboundStatus === "已入库" ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-700")}>{rowInboundStatus}</span> <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">时间</span> {rowInboundDate}</span>,
              warehouse: <span className="block truncate" title={`目标仓库：${rowWarehouseName} · 吉客云仓库 ID：${warehouse.jackyunWarehouseId || "—"} · 是否可售：${warehouse.isSellable == null ? "—" : warehouse.isSellable ? "是" : "否"} · 当前库存：${quantityWithUnit(rowCurrentStock)} · 在途数量：${quantityWithUnit(warehouse.inTransitQty)}`}><span className="text-slate-400">仓库</span> {rowWarehouseName} <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">ID</span> <span className="font-mono">{warehouse.jackyunWarehouseId || "—"}</span> <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">可售</span> {warehouse.isSellable == null ? "—" : warehouse.isSellable ? "是" : "否"} <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">库存</span> {quantityWithUnit(rowCurrentStock)} <span className="mx-1 text-slate-300">·</span><span className="text-slate-400">在途</span> {quantityWithUnit(warehouse.inTransitQty)}</span>,
            };
            return <tr key={`${inboundRow?.goodsdocNo || item?.key || "material"}-${materialRow?.consumableCode || materialRow?.consumableName || index}`} className="border-t border-slate-100 align-middle">
              {visibleDetailColumns.map((column) => <td key={column.key} style={{ width: `${detailColumnPreferences[column.key].width}px` }} className={cx("px-2.5 py-1.5 whitespace-nowrap", column.align === "right" ? "text-right" : "text-left", ["unitPrice", "amount", "quantity", "usage"].includes(column.key) ? "tabular-nums" : "", ["quantity", "unitPrice", "amount"].includes(column.key) ? "text-slate-700" : "text-slate-600")}>{detailCells[column.key]}</td>)}
            </tr>;
          })}
        </tbody>
      </table>
    </div>
    {hasAllocations && rawSummary && <p className="mt-2 truncate text-[10px] text-slate-400" title={rawSummary}>原始订单商品：{rawSummary}</p>}
  </DetailCard>;
}

function SupplyChainDetail({ detail, loading, onRefresh, refreshBusy, onOpenOperations, onCloseOperations, operationsOpen, onOpenWorkbenchView }: Pick<Props, "detail" | "onRefresh" | "refreshBusy" | "onOpenOperations" | "onCloseOperations" | "operationsOpen" | "onOpenWorkbenchView"> & { loading: boolean }) {
  if (loading && !detail) return <section className="flex h-full min-h-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-[13px] text-slate-400">正在读取订单明细…</section>;
  if (!detail) return <section className="flex h-full min-h-0 items-center justify-center rounded-xl border border-slate-200 bg-white text-[13px] text-slate-400">选择上方订单后，在这里查看供应链明细。</section>;

  const { order } = detail;
  const wh: DetailWarehouse = detail.warehouse || { warehouseName: "", jackyunWarehouseId: "", isSellable: null, currentStock: null, inTransitQty: 0 };
  const allocations = (detail.detail.allocations || []) as DetailAllocation[];
  const rawOrderItems = (detail.detail.orderItems || []) as DetailOrderItem[];
  const products = detailProducts(detail);
  const product = selectedProduct(detail);
  const inbound = detail.detail.inbound as DetailInbound[];
  const purchaseOrders = detail.detail.purchaseOrders as DetailPurchaseOrder[];
  const consumable = detail.detail.consumable as DetailConsumable | null;
  const usage = inbound.flatMap((row) => row.consumableUsageItems || []);
  const activeStatus = getStatus({
    orderId: order.orderId, externalPoId: order.externalPoId, orderNo: order.orderNo, supplier: order.supplier || "", amount: order.amount,
    freight: order.freight, orderDate: order.orderDate, orderStatus: order.orderStatus || "", purchaseStatus: order.purchaseStatus,
    hasException: order.hasException, firstUndone: null, firstUndoneLabel: "", firstUndoneShort: "", firstUndoneDimension: "purchase",
    stepStates: detail.stepStates, inboundDone: inbound.length > 0 || Boolean(consumable?.received), invoiceDone: false,
  });
  const detailExceptionText = exceptionMessages(order.exceptionInfo);
  const stageIndex: number = (() => {
    if (inbound.length > 0 || consumable?.received || ["inbound", "done"].includes(order.purchaseStatus)) return 5;
    if (order.purchaseStatus === "arrived") return 4;
    if (order.purchaseStatus === "shipped") return 3;
    if (["producing", "confirmed", "jackyun_linked"].includes(order.purchaseStatus)) return 1;
    return 0;
  })();
  const timeline = [
    { label: "已下单", detail: dateText(order.orderDate) },
    { label: "生产中", detail: stageIndex === 1 ? activeStatus : "" },
    { label: "已发货", detail: stageIndex === 2 ? "运输中" : "" },
    { label: "在途", detail: stageIndex === 3 ? "已发出" : "" },
    { label: "待入库", detail: stageIndex === 4 ? "已到货" : "" },
    { label: "已入库", detail: stageIndex === 5 ? (inbound[0]?.date ? dateText(inbound[0].date) : "已确认") : "" },
  ];
  const detailsHref = `/purchase/workbench?view=orders&order=${order.orderId}`;

  return <section id="purchase-order-detail" className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-[#fbfcff] shadow-[0_3px_16px_rgba(51,78,126,.04)]">
    <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-1.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <h2 className="truncate text-[17px] font-semibold tracking-tight text-slate-900">{order.orderNo}</h2>
        <span className={cx("shrink-0 rounded-md px-2 py-0.5 text-[10px] font-medium", statusTone(activeStatus))}>{activeStatus}</span>
        {activeStatus === "异常" && detailExceptionText ? <span className="min-w-0 truncate text-[11px] text-rose-500" title={detailExceptionText}>{detailExceptionText}</span> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => operationsOpen ? onCloseOperations() : onOpenOperations()} className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-[11px] font-medium text-slate-600 hover:border-blue-300 hover:text-blue-600"><Icon name="edit" size={13} />{operationsOpen ? "收起编辑" : "编辑"}</button>
        <button type="button" onClick={onRefresh} disabled={refreshBusy} className="inline-flex h-7 items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-[11px] font-medium text-slate-600 hover:border-blue-300 hover:text-blue-600 disabled:opacity-50"><Icon name="refresh" size={13} />{refreshBusy ? "同步中…" : "同步最新"}</button>
        <details className="relative"><summary className="flex h-7 cursor-pointer list-none items-center gap-1 rounded-md border border-slate-200 bg-white px-2.5 text-[11px] font-medium text-slate-600 hover:border-blue-300"><span>更多操作</span><Icon name="chevron" size={12} /></summary><div className="absolute right-0 z-20 mt-1 w-36 rounded-lg border border-slate-200 bg-white p-1 shadow-lg"><button type="button" onClick={() => onOpenWorkbenchView("suppliers")} className="w-full rounded px-2.5 py-1.5 text-left text-[11px] text-slate-600 hover:bg-slate-50">供应商视图</button><button type="button" onClick={() => onOpenWorkbenchView("chain")} className="w-full rounded px-2.5 py-1.5 text-left text-[11px] text-slate-600 hover:bg-slate-50">链路建链</button><button type="button" onClick={() => onOpenWorkbenchView("matching")} className="w-full rounded px-2.5 py-1.5 text-left text-[11px] text-slate-600 hover:bg-slate-50">SKU 匹配</button></div></details>
      </div>
    </div>

    <div className="shrink-0 overflow-x-auto border-b border-slate-200 bg-white px-4 py-1.5"><div className="flex min-w-[650px] items-start">{timeline.map((stage, index) => { const done = index <= stageIndex; const active = index === stageIndex; return <div key={stage.label} className="relative flex min-w-[104px] flex-1 flex-col items-center text-center"><span className={cx("relative z-10 flex h-4 w-4 items-center justify-center rounded-full border-2 text-[9px] font-semibold", done ? "border-blue-500 bg-blue-500 text-white" : "border-slate-300 bg-white text-slate-300")}>{done ? "✓" : ""}</span>{index < timeline.length - 1 && <span className={cx("absolute left-1/2 right-[-50%] top-[7px] h-[2px]", index < stageIndex ? "bg-blue-500" : "bg-slate-200")} />}<span className={cx("mt-1 text-[10px]", active ? "font-semibold text-slate-800" : done ? "text-slate-600" : "text-slate-400")}>{stage.label}</span><span className={cx("mt-0.5 h-3 text-[9px] leading-3", active ? "text-blue-600" : "text-slate-400")}>{stage.detail}</span></div>; })}</div></div>

    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="grid grid-cols-1 gap-2.5 p-2.5 xl:grid-cols-5">
      <div className="xl:col-span-5"><ProductDetailCard orderId={order.orderId} orderKind={order.orderKind} products={products} rawOrderItems={rawOrderItems} hasAllocations={allocations.length > 0} usage={usage} inbound={inbound} purchaseOrders={purchaseOrders} warehouse={wh} fallbackWarehouse={consumable?.warehouseName || consumable?.location} fallbackReceiptNo={consumable?.receiptNo} fallbackReceived={Boolean(consumable?.received)} unit={product.unit} detailsHref={detailsHref} currentStatus={activeStatus} /></div>
      </div>
    </div>
  </section>;
}

export function SupplyChainReplica(props: Props) {
  const [orderTabs, setOrderTabs] = useState<number[]>([]);
  const [pull1688Open, setPull1688Open] = useState(false);
  const selectAllOrdersRef = useRef<HTMLInputElement>(null);
  const selectedOrderRowRef = useRef<HTMLTableRowElement>(null);
  // 工作区下同一个组件会在多个 Tab 里同时挂载：定位元素必须走 ref，不能用 document.getElementById（会命中别的 Tab）
  const ordersListRef = useRef<HTMLElement>(null);
  const selectedOrderSet = useMemo(() => new Set(props.selectedOrderIds), [props.selectedOrderIds]);
  const selectedVisibleCount = props.orders.reduce((count, order) => count + (selectedOrderSet.has(order.orderId) ? 1 : 0), 0);
  const allVisibleSelected = props.orders.length > 0 && selectedVisibleCount === props.orders.length;
  const warehouses = Array.from(new Set(props.orders.map((row) => row.warehouseName).filter(Boolean))) as string[];
  const totalPages = Math.max(1, Math.ceil(props.total / props.pageSize));
  const visibleColumns = ORDER_COLUMN_DEFINITIONS;
  const tableMinWidth = 36 + visibleColumns.reduce((total, column) => total + column.defaultWidth, 0);
  const pullJob = props.alibaba1688Job;
  const pullJobActive = ["queued", "pending", "running"].includes(pullJob?.status || "");
  const pullJobSuccess = ["success", "partial"].includes(pullJob?.status || "");
  const pullJobLabel = pullJob?.status === "queued" || pullJob?.status === "pending"
    ? "任务排队中"
    : pullJob?.status === "running"
      ? "1688 采购订单拉取中"
      : pullJob?.status === "success"
        ? "1688 采购订单拉取完成"
        : pullJob?.status === "partial"
          ? "1688 采购订单部分完成"
          : pullJob?.status === "not_found"
            ? "没有找到符合条件的订单"
            : pullJob?.status === "closed_skipped"
              ? "订单已关闭，未写入采购订单"
              : pullJob?.status
                ? "1688 采购订单拉取失败"
                : "";
  const pullJobStats = pullJob?.stats || {};
  const pullJobSummary = pullJobActive
    ? `任务 #${pullJob?.id} · 关闭弹窗后仍会继续执行，系统会自动刷新结果`
    : pullJobSuccess
      ? `新增 ${pullJobStats.created ?? 0} 单 · 重复 ${pullJobStats.duplicates ?? 0} 单 · ${pullJob?.finishedAt ? new Date(pullJob.finishedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "刚刚"}`
      : pullJob?.errorSummary || "请重新打开弹窗检查登录状态和拉取范围";
  const allActive = props.channelFilter === "all"
    && props.kindFilter === "all"
    && !props.warehouseFilter
    && props.statusFilter === "all"
    && !props.startDate
    && !props.endDate
    && !props.searchDraft;

  useEffect(() => {
    if (props.selectedOrderId === null) return;
    setOrderTabs((current) => current.includes(props.selectedOrderId as number) ? current : [...current, props.selectedOrderId as number].slice(-8));
  }, [props.selectedOrderId]);

  useEffect(() => {
    if (props.selectedOrderId === null || !props.orders.some((item) => item.orderId === props.selectedOrderId)) return;
    selectedOrderRowRef.current?.scrollIntoView({ block: "nearest" });
  }, [props.orders, props.selectedOrderId]);

  useEffect(() => {
    if (selectAllOrdersRef.current) {
      selectAllOrdersRef.current.indeterminate = selectedVisibleCount > 0 && !allVisibleSelected;
    }
  }, [allVisibleSelected, selectedVisibleCount]);

  function orderTabLabel(orderId: number) {
    const order = props.orders.find((item) => item.orderId === orderId);
    if (order?.orderNo) return order.orderNo;
    if (props.detail?.order.orderId === orderId) return props.detail.order.orderNo;
    return `订单 ${orderId}`;
  }

  function showOrderTab(orderId: number) {
    props.onSelectOrder(orderId);
  }

  function closeOrderTab(orderId: number) {
    setOrderTabs((current) => current.filter((id) => id !== orderId));
  }

  return <div className="flex h-[calc(100vh-3.5rem)] min-h-0 flex-col bg-[#f7f9fd] text-slate-700">
    {pull1688Open && (
      <div className="fixed inset-0 z-modal flex items-start justify-center overflow-y-auto bg-slate-950/35 p-4 sm:p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) setPull1688Open(false); }} role="dialog" aria-modal="true" aria-label="拉取 1688 采购订单">
        <div className="my-auto w-full max-w-[800px]">
          <Alibaba1688BrowserCard onSynced={props.onRefresh} onClose={() => setPull1688Open(false)} />
        </div>
      </div>
    )}

    <div className="sticky top-0 z-20 shrink-0 border-b border-slate-200 bg-[#f8fafc]/95 px-4 backdrop-blur sm:px-7">
      <div className="flex h-9 min-w-max items-end gap-1 overflow-x-auto [scrollbar-width:thin]">
        <button type="button" aria-label="新建订单工作页" onClick={props.onNewOrder} className="mb-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-[18px] leading-none text-slate-500 hover:border-blue-300 hover:text-blue-600">+</button>
        <button type="button" aria-selected={orderTabs.length === 0} onClick={() => ordersListRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })} className={cx("flex h-8 shrink-0 items-center rounded-t-md border px-3 text-[11px]", orderTabs.length === 0 ? "border-slate-200 border-b-white bg-white font-medium text-slate-700" : "border-transparent text-slate-500 hover:bg-white hover:text-blue-600")}>采购订单</button>
        {orderTabs.map((orderId) => {
          const label = orderTabLabel(orderId);
          const active = orderId === props.selectedOrderId;
          return <div key={orderId} className={cx("group flex h-8 shrink-0 items-center rounded-t-md border", active ? "border-blue-200 border-b-white bg-white" : "border-transparent hover:bg-white")}>
            <button type="button" aria-selected={active} onClick={() => showOrderTab(orderId)} title={label} className={cx("max-w-[180px] truncate px-3 text-[11px]", active ? "font-medium text-blue-600" : "text-slate-500 hover:text-blue-600")}>订单 {label}</button>
            <button type="button" aria-label={`关闭订单标签 ${label}`} onClick={() => closeOrderTab(orderId)} className="mr-1 rounded px-1 text-[13px] leading-none text-slate-300 hover:bg-slate-100 hover:text-slate-600">×</button>
          </div>;
        })}
      </div>
    </div>

    {pullJob && <div className={cx("mx-3 mt-2 flex shrink-0 items-center justify-between gap-3 rounded-lg border px-3 py-2 text-[11px] sm:mx-5 lg:mx-7", pullJobActive ? "border-indigo-200 bg-indigo-50 text-indigo-700" : pullJobSuccess ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-rose-200 bg-rose-50 text-rose-700")} aria-live="polite">
      <div className="flex min-w-0 items-center gap-2"><span className={cx("h-2 w-2 shrink-0 rounded-full", pullJobActive ? "bg-indigo-500 animate-pulse" : pullJobSuccess ? "bg-emerald-500" : "bg-rose-500")} /><span className="shrink-0 font-medium">{pullJobLabel}</span><span className="truncate opacity-80">{pullJobSummary}</span></div>
      {pullJobActive && <button type="button" onClick={() => setPull1688Open(true)} className="shrink-0 rounded-md border border-indigo-200 bg-white px-2.5 py-1 text-[10px] font-medium text-indigo-600 hover:bg-indigo-100">查看配置</button>}
    </div>}

    <div className="flex min-h-0 flex-1 flex-col px-3 py-3 sm:px-5 lg:px-7">
      {/* 指标卡即筛选入口：点击按类型/状态过滤订单，当前选中的卡片高亮（原「订单状态」chip 条已并入此处）。 */}
      <section className="shrink-0 grid grid-cols-2 gap-2.5 md:grid-cols-5 xl:grid-cols-9">
        <MetricCard label="全部采购单" value={props.summary?.totalOrders ?? "—"} leftHint={"进行中 " + Math.max(0, (props.summary?.totalOrders || 0) - (props.summary?.completedOrders || 0))} rightHint={"开票完成 " + (props.summary?.completedOrders ?? 0)} tone="blue" icon={<Icon name="document" size={25} />} active={props.statusFilter === "all" && props.kindFilter === "all"} onClick={() => { props.onStatusFilterChange("all"); props.onKindFilterChange("all"); }} />
        <MetricCard label="正品采购" value={props.summary?.goodsOrders ?? "—"} leftHint={"生产中 " + (props.summary?.goodsProducing ?? 0)} rightHint={"已入库 " + (props.summary?.goodsInbound ?? 0)} tone="green" icon={<Icon name="box" size={25} />} active={props.kindFilter === "goods" && props.statusFilter === "all"} onClick={() => { props.onStatusFilterChange("all"); props.onKindFilterChange("goods"); }} />
        <MetricCard label="耗材采购" value={props.summary?.consumableOrders ?? "—"} leftHint={"在途 " + (props.summary?.consumableTransit ?? 0)} rightHint={"已入库 " + (props.summary?.consumableInbound ?? 0)} tone="orange" icon={<Icon name="material" size={25} />} active={props.kindFilter === "consumable" && props.statusFilter === "all"} onClick={() => { props.onStatusFilterChange("all"); props.onKindFilterChange("consumable"); }} />
        <MetricCard label="待完善" value={props.summary?.pendingSku ?? "—"} leftHint="" tone="amber" icon={<Icon name="edit" size={25} />} active={props.statusFilter === "refine"} onClick={() => { props.onStatusFilterChange("refine"); props.onKindFilterChange("all"); }} />
        <MetricCard label="待入库" value={props.summary?.pendingInbound ?? "—"} leftHint="" tone="indigo" icon={<Icon name="warehouse" size={25} />} active={props.statusFilter === "inbound"} onClick={() => { props.onStatusFilterChange("inbound"); props.onKindFilterChange("all"); }} />
        <MetricCard label="在途订单" value={props.summary?.transitOrders ?? "—"} leftHint={"待入库 " + (props.summary?.pendingInbound ?? 0)} rightHint="物流信息以实际单据为准" tone="purple" icon={<Icon name="truck" size={25} />} active={props.statusFilter === "transit"} onClick={() => { props.onStatusFilterChange("transit"); props.onKindFilterChange("all"); }} />
        <MetricCard label="待开发票" value={props.summary?.pendingInvoice ?? "—"} leftHint="" tone="slate" icon={<Icon name="document" size={25} />} active={props.statusFilter === "invoice"} onClick={() => { props.onStatusFilterChange("invoice"); props.onKindFilterChange("all"); }} />
        <MetricCard label="异常订单" value={props.summary?.exceptionCount ?? "—"} leftHint={"待处理 " + (props.summary?.exceptionCount ?? 0)} tone="red" icon={<Icon name="alert" size={25} />} active={props.statusFilter === "exception"} onClick={() => { props.onStatusFilterChange("exception"); props.onKindFilterChange("all"); }} />
        <MetricCard label="开票完成" value={props.summary?.completedOrders ?? "—"} leftHint="" tone="green" icon={<Icon name="check" size={25} />} active={props.statusFilter === "done"} onClick={() => { props.onStatusFilterChange("done"); props.onKindFilterChange("all"); }} />
      </section>

      <form onSubmit={(event) => { event.preventDefault(); props.onApplySearch(); }} className="mt-2 flex shrink-0 flex-wrap items-center gap-2">
        <select aria-label="采购渠道" value={props.channelFilter} onChange={(event) => props.onChannelFilterChange(event.target.value as SupplyChainChannelFilter)} className="h-8 min-w-[112px] rounded-md border border-slate-200 bg-white px-2.5 text-[11px] text-slate-600 outline-none">
          <option value="all">全部渠道</option>
          <option value="1688">1688</option>
          <option value="pdd">拼多多</option>
          <option value="taobao">淘宝</option>
          <option value="other">其他</option>
        </select>
        <select aria-label="采购类型" value={props.kindFilter} onChange={(event) => props.onKindFilterChange(event.target.value as SupplyChainKindFilter)} className="h-8 min-w-[112px] rounded-md border border-slate-200 bg-white px-2.5 text-[11px] text-slate-600 outline-none">
          <option value="all">全部类型</option>
          <option value="goods">正品（生产）</option>
          <option value="consumable">耗材</option>
        </select>
        <select aria-label="仓库" value={props.warehouseFilter} onChange={(event) => props.onWarehouseFilterChange(event.target.value)} className="h-8 min-w-[112px] rounded-md border border-slate-200 bg-white px-2.5 text-[11px] text-slate-600 outline-none">
          <option value="">全部仓库</option>
          {props.warehouseFilter && !warehouses.includes(props.warehouseFilter) && <option value={props.warehouseFilter}>{props.warehouseFilter}</option>}
          {warehouses.map((warehouse) => <option key={warehouse} value={warehouse}>{warehouse}</option>)}
        </select>
        <div className="flex h-8 items-center rounded-md border border-slate-200 bg-white text-[11px] text-slate-500">
          <input aria-label="开始日期" type="date" value={props.startDate} onChange={(event) => props.onStartDateChange(event.target.value)} className="w-[112px] bg-transparent px-2 outline-none" />
          <span className="text-slate-300">→</span>
          <input aria-label="结束日期" type="date" value={props.endDate} onChange={(event) => props.onEndDateChange(event.target.value)} className="w-[112px] bg-transparent px-2 outline-none" />
        </div>
        <div className="flex h-8 min-w-[210px] flex-1 items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 text-slate-400">
          <Icon name="search" size={14} />
          <input value={props.searchDraft} onChange={(event) => props.onSearchDraftChange(event.target.value)} placeholder="搜索订单号、商品名称、供应商…" className="min-w-0 flex-1 bg-transparent text-[11px] text-slate-700 outline-none placeholder:text-slate-400" />
        </div>
        <button type="button" onClick={props.onReset} disabled={allActive} className="h-8 rounded-md border border-slate-200 bg-white px-3.5 text-[11px] font-medium text-slate-600 hover:border-blue-300 disabled:opacity-45">重置</button>
        <button type="button" onClick={props.onExport} disabled={props.loading || props.exportBusy} className="inline-flex h-8 items-center gap-1 rounded-md border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-600 hover:border-blue-300 hover:text-blue-600 disabled:cursor-wait disabled:opacity-50">{props.exportBusy ? "导出中…" : props.loading ? "准备中…" : "导出核验"}</button>
        <button type="button" aria-haspopup="dialog" aria-expanded={pull1688Open} onClick={() => setPull1688Open(true)} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-indigo-200 bg-indigo-50 px-3.5 text-[11px] font-medium text-indigo-600 hover:border-indigo-300 hover:bg-indigo-100"><Icon name="refresh" size={14} />拉取 1688 采购订单</button>
        <button type="button" onClick={props.onNewOrder} className="inline-flex h-8 items-center gap-1.5 rounded-md bg-blue-600 px-3.5 text-[11px] font-medium text-white shadow-[0_4px_12px_rgba(37,99,235,.22)] hover:bg-blue-700"><Icon name="plus" size={14} />新建采购单</button>
      </form>

      {props.selectedOrderIds.length > 0 && <div className="mt-2 flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-[11px]">
        <span className="font-medium text-blue-700">已选 {props.selectedOrderIds.length} 条采购订单</span>
        <span className="text-blue-500">批量操作仅作用于当前已选订单</span>
        <button type="button" onClick={props.onDeleteSelected} disabled={props.deleteBusy} className="ml-auto rounded-md bg-rose-600 px-3 py-1.5 font-medium text-white hover:bg-rose-700 disabled:cursor-wait disabled:opacity-50">{props.deleteBusy ? "删除中…" : "删除选中"}</button>
        <button type="button" onClick={props.onClearOrderSelection} disabled={props.deleteBusy} className="rounded-md border border-blue-200 bg-white px-3 py-1.5 font-medium text-blue-600 hover:bg-blue-100 disabled:opacity-50">取消选择</button>
      </div>}

      <div className="mt-2.5 flex min-h-0 flex-1 flex-col gap-2.5">
      <section ref={ordersListRef} className="flex min-h-0 basis-1/2 flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_3px_14px_rgba(48,76,124,.035)]">
        <div className="min-h-0 flex-1 overflow-auto">
          <table className="w-full table-fixed border-collapse text-[11px] leading-tight" style={{ minWidth: `${tableMinWidth}px` }}>
            <thead className="sticky top-0 z-10 bg-slate-50/95 text-[10px] font-medium text-slate-500">
              <tr>
                <th className="w-9 border-b border-r border-slate-100 px-2 py-2"><input ref={selectAllOrdersRef} type="checkbox" aria-label="全选当前页订单" checked={allVisibleSelected} onChange={props.onToggleAllVisible} disabled={props.orders.length === 0 || props.loading || props.deleteBusy} className="h-3.5 w-3.5 accent-blue-600" /></th>
                {visibleColumns.map((column) => <th key={column.key} style={{ width: `${column.defaultWidth}px` }} className={cx("border-b border-r border-slate-100 px-2", column.align === "right" ? "text-right" : "text-left")}><span>{column.label}</span></th>)}
              </tr>
            </thead>
            <tbody>
              {props.loading ? <tr><td colSpan={visibleColumns.length + 1} className="h-[150px] text-center text-slate-400">正在加载采购订单…</td></tr>
                : props.orders.length === 0 ? <tr><td colSpan={visibleColumns.length + 1} className="h-[150px] text-center text-slate-400">没有符合筛选条件的采购订单</td></tr>
                : props.orders.map((item) => {
                  const active = item.orderId === props.selectedOrderId;
                  const platform = platformMeta(item.platform);
                  const status = getStatus(item);
                  return <tr ref={active ? selectedOrderRowRef : undefined} key={item.orderId} onClick={() => props.onSelectOrder(item.orderId)} className={cx("cursor-pointer border-b border-slate-100 last:border-b-0", active ? "bg-blue-50/60" : "hover:bg-slate-50/80")}>
                    <td className="border-r border-slate-100 px-2 py-1.5 text-center"><input type="checkbox" checked={selectedOrderSet.has(item.orderId)} onChange={() => props.onToggleOrder(item.orderId)} onClick={(event) => event.stopPropagation()} aria-label={"选择订单 " + item.orderNo} disabled={props.deleteBusy} className="h-3.5 w-3.5 accent-blue-600" /></td>
                    {visibleColumns.map((column) => {
                      const sharedClass = cx("border-r border-slate-100 px-2 py-1.5", column.align === "right" ? "text-right" : "text-left");
                      let cell: ReactNode;
                      switch (column.key) {
                        case "orderNo": cell = <span className="font-medium text-slate-700">{item.orderNo}</span>; break;
                        case "platform": cell = <span className={cx("rounded px-1.5 py-0.5 text-[10px] font-medium text-white", platform.tone)}>{platform.label}</span>; break;
                        case "orderKind": cell = <span className={cx("rounded px-1.5 py-0.5 text-[10px] font-medium", kindTone(item))}>{kindText(item)}</span>; break;
                        case "productName": cell = <span className="block truncate text-slate-700" title={item.productName || ""}>{item.productName || "—"}</span>; break;
                        case "quantity": cell = <span className="tabular-nums text-slate-700">{numberText(item.productQuantity)}{item.productUnit ? " " + item.productUnit : ""}</span>; break;
                        case "supplier": cell = <span className="block truncate text-slate-600" title={item.supplier}>{item.supplier || "—"}</span>; break;
                        case "warehouse": cell = <span className="block truncate text-slate-600" title={item.warehouseName || ""}>{item.warehouseName || "—"}</span>; break;
                        case "orderDate": cell = <span className="tabular-nums text-slate-600">{dateText(item.orderDate)}</span>; break;
                        case "status": (() => {
                          const reason = status === "异常" ? exceptionMessages(item.exceptionInfo) : "";
                          return (
                            <div className="min-w-0" title={reason || undefined}>
                              <span className={cx("inline-block rounded px-2 py-0.5 text-[10px] font-medium", statusTone(status))}>{status}</span>
                              {reason ? <span className="mt-0.5 block truncate text-[10px] leading-tight text-rose-500">{reason}</span> : null}
                              {status === "异常" ? <button type="button" onClick={(event) => { event.stopPropagation(); props.onOpenExceptions(); }} className="mt-0.5 text-[10px] font-medium text-blue-600 hover:underline">去异常中心确认</button> : null}
                            </div>
                          );
                        })(); break;
                        case "logisticsNo": cell = item.logisticsNo ? <Link href={`/supply-chain/production?group=transit&order=${item.orderId}`} onClick={(event) => event.stopPropagation()} className="block truncate font-mono text-[10px] text-blue-600 hover:underline" aria-label={`查看物流轨迹 ${item.logisticsNo}`}>{item.logisticsNo}</Link> : "—"; break;
                        case "jackyunInboundNo": cell = <span className="block truncate font-mono text-[10px] text-slate-600" title={item.jackyunInboundNo || ""}>{item.jackyunInboundNo || "—"}</span>; break;
                        case "amount": cell = item.amount == null ? "—" : <span className="tabular-nums text-slate-700">¥{numberText(item.amount)}</span>; break;
                        case "paidAmount": cell = item.paidAmount == null ? "—" : <span className="tabular-nums text-slate-700">¥{numberText(item.paidAmount)}</span>; break;
                        case "expectedArrival": cell = dateText(item.expectedArrival); break;
                        case "remark": cell = <span className="block truncate text-slate-600" title={item.remark || ""}>{item.remark || "—"}</span>; break;
                        case "operation": cell = <button type="button" onClick={(event) => { event.stopPropagation(); props.onOpenOperations(item.orderId); }} className="text-[11px] font-medium text-blue-600 hover:text-blue-700">编辑</button>; break;
                      }
                      return <td key={column.key} style={{ width: `${column.defaultWidth}px` }} className={cx(sharedClass, column.key === "operation" && "border-r-0")}>{cell}</td>;
                    })}
                  </tr>;
                })}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between border-t border-slate-100 px-4 py-1.5 text-[11px] text-slate-500">
          <span>共 {props.total} 条</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => props.onPageChange(Math.max(1, props.page - 1))} disabled={props.page <= 1} className="flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 disabled:opacity-40">‹</button>
            {Array.from({ length: Math.min(3, totalPages) }, (_, index) => {
              const candidate = Math.min(Math.max(1, props.page - 1) + index, totalPages);
              return <button key={candidate} type="button" onClick={() => props.onPageChange(candidate)} className={cx("flex h-7 w-7 items-center justify-center rounded border text-[11px]", candidate === props.page ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 bg-white text-slate-600")}>{candidate}</button>;
            })}
            <button type="button" onClick={() => props.onPageChange(Math.min(totalPages, props.page + 1))} disabled={props.page >= totalPages} className="flex h-7 w-7 items-center justify-center rounded border border-slate-200 bg-white text-slate-500 disabled:opacity-40">›</button>
            <span className="ml-2 rounded border border-slate-200 bg-white px-2 py-1 text-[10px]">{props.pageSize} 条/页</span>
          </div>
        </div>
      </section>

      <div className="min-h-0 basis-1/2 flex-1">
        <SupplyChainDetail
          detail={props.detail}
          loading={props.detailLoading}
          onRefresh={props.onRefresh}
          refreshBusy={props.refreshBusy}
          onOpenOperations={props.onOpenOperations}
          onCloseOperations={props.onCloseOperations}
          operationsOpen={props.operationsOpen}
          onOpenWorkbenchView={props.onOpenWorkbenchView}
        />
      </div>
      </div>

    </div>
  </div>;
}
