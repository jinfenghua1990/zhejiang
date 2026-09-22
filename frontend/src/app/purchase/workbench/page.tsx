"use client";

import NextLink from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { WORKBENCH_VIEWS, parseWorkbenchView, workbenchHref, type WorkbenchView } from "@/lib/workbench-navigation";
import { WorkspaceModule } from "./workspace-modules";
import { NewPurchaseModal } from "./new-purchase-modal";
import { RelatedRecordsPanel } from "./related-records-panel";
import { SearchableSelect } from "./searchable-select";
import { QuickTriage, canonicalChainOrderId } from "./quick-triage";
import { SupplyChainReplica, type SupplyChainChannelFilter, type SupplyChainKindFilter, type SupplyChainStatusFilter } from "./supply-chain-replica";
import { newRequestKey } from "@/lib/request-key";
import { useTabActive, useTabDirty, useTabRuntime, useWorkspace } from "@/lib/workspace/tab-store";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";
import {
  authenticatedFetch,
  consumablesApi,
  dashboardApi,
  procurementChainApi,
  procurementWorkbenchApi,
  skuMatchingApi,
  warehousesApi,
  type CatalogSkuRow,
  type ConsumableMappingRow,
  type ConsumablePurchaseItem,
  type ConsumablePurchaseRow,
  type ConsumableRow,
  type InboundMatchSummary,
  type InvoiceReconciliation,
  type PendingAllocation,
  type SkuCandidate,
  type SyncJobRow,
  type ChainOrderRow,
  type ChainLinkCandidate,
  type ChainOverview,
  type ChainPurchaseOrder,
  type PendingLink,
  type WorkbenchOrder as WorkbenchOrderRow,
  type XrefPreview,
  type XrefApplyResult,
  type WorkbenchDetail,
  type WorkbenchOrderItem,
  type WorkbenchStepState,
  type WorkbenchSupplierDetail,
  type WorkbenchSupplierSummary,
  type WorkbenchSummary,
  type WarehouseRow,
} from "@/lib/api";

type ViewMode = WorkbenchView;
type FilterKey = SupplyChainStatusFilter;
type ChainFilter = "all" | "gap" | "full";

// 完整业务页保留自己的页面头和布局，避免再嵌套到采购工作台的第二层壳里。
const STANDALONE_VIEW_ROUTES: Partial<Record<ViewMode, string>> = {
  dashboard: "/",
  sales: "/sales",
  products: "/products",
  inventory_goods: "/inventory?tab=goods",
  inventory_consumables: "/inventory?tab=consumables",
  finance: "/finance/monthly-send",
  exceptions: "/exceptions",
  automation: "/automation",
  settings: "/settings",
  imports: "/data-center-import",
};

/** 采购单状态流转（与 /purchase 执行中心一致） */
const PO_STATUS: Record<string, string> = {
  pending_refine: "待完善", confirmed: "已确认采购内容", jackyun_linked: "已关联吉客云采购单",
  producing: "待发货/生产中", shipped: "已发货", arrived: "已到货", inbound: "已入库", done: "完成",
};
const NEXT_STATUS: Record<string, string> = {
  jackyun_linked: "producing", producing: "shipped",
  shipped: "arrived", arrived: "inbound", inbound: "done",
};
/** 附加费用类型（不伪造成 SKU） */
const EXPENSE_LABEL: Record<string, string> = {
  pack: "包装", processing: "加工", plate: "制版", mold: "模具", freight: "运费", testing: "检测", other: "其他",
};

/** 链路 7 环节：① 订单 → ② SKU → ③ 采购单 → ④ 入库 → ⑤ 发票 → ⑥ 付款 → ⑦ 认证 */
const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦"];
/** 维度色：1688 导出 / 本平台采购中心 / 吉客云 / 税务 */
const DIM_DOT: Record<string, string> = {
  "1688": "bg-indigo-500",
  purchase: "bg-violet-500",
  jackyun: "bg-teal-500",
  tax: "bg-amber-500",
};
/** 环节补数据入口：站内跳导入页，或切到工作台其它视图 */
const STAGE_ACTION: Record<string, { href?: string; view?: ViewMode; act: string }> = {
  order: { href: "/data-center-import?tab=alibaba1688", act: "导入 1688 订单" },
  sku: { view: "matching", act: "配置 SKU 匹配" },
  jackyunPo: { href: "/purchase/workbench?view=orders", act: "查看本系统采购单" },
  inbound: { href: "/data-center-import?tab=jackyun", act: "导入入库单" },
  invoice: { href: "/finance/invoices", act: "导入发票清单" },
  paid: { href: "/data-center-import?tab=jackyun", act: "导入结算单" },
  verified: { href: "/finance/invoices", act: "查看发票清单" },
};
type IconName =
  | "dashboard"
  | "sales"
  | "box"
  | "purchase"
  | "orders"
  | "calendar"
  | "users"
  | "wallet"
  | "reconcile"
  | "receipt"
  | "cloud"
  | "chart"
  | "settings"
  | "import"
  | "sync"
  | "magic"
  | "refresh"
  | "download"
  | "plus"
  | "filter"
  | "search"
  | "copy"
  | "chevron"
  | "history"
  | "alert-circle"
  | "edit"
  | "x";

type AllocationRow = {
  id?: number;
  skuId?: number | null;
  goodsName?: string;
  skuCode?: string;
  quantity?: number;
  unitPrice?: number;
  amount?: number;
  note?: string;
  /** 来源入库单 ID：入库单明细自动反填的行有值；人工新增行为 null（归入手工补录区） */
  inboundDocumentId?: number | null;
};
type ModalConsumableItem = ConsumablePurchaseItem & { purchaseId?: number };
type EditorConsumableDraft = { consumableId: number; quantity: string };
type PendingConsumableUpdate = { documentId: number; allocationId: number; draft: EditorConsumableDraft | null };
type ExpenseRow = { id?: number; expenseType?: string; amount?: number | null };
type SettlementRow = { paidAmount?: number | null; amount?: number | null };
type InboundRow = {
  linkId?: number | null;
  documentId?: number;
  targetId?: number;
  goodsdocNo?: string;
  warehouseName?: string;
  supplier?: string;
  amount?: number | null;
  itemAmount?: number | null;
  itemCount?: number;
  date?: string | null;
  status?: string;
  matchMethod?: string;
  note?: string;
  consumableUsageDecided?: boolean;
  consumableUsageEnabled?: boolean | null;
  consumableUsageItems?: Array<{ consumableId: number; consumableCode: string; consumableName: string; unit: string; quantity: string }>;
};


type ChannelKey = SupplyChainChannelFilter;

/** 采购渠道：1688 / 拼多多 / 淘宝 / 其他。 */
const CHANNELS: Record<string, { key: ChannelKey; label: string; dot: string; badge: string }> = {
  "1688": { key: "1688", label: "1688", dot: "bg-indigo-500", badge: "border-indigo-200 bg-indigo-50 text-indigo-600" },
  pdd: { key: "pdd", label: "拼多多", dot: "bg-red-500", badge: "border-red-200 bg-red-50 text-red-600" },
  taobao: { key: "taobao", label: "淘宝", dot: "bg-orange-500", badge: "border-orange-200 bg-orange-50 text-orange-600" },
  other: { key: "other", label: "其他", dot: "bg-slate-500", badge: "border-slate-200 bg-slate-50 text-slate-600" },
};

function channelOf(value: string | null | undefined): ChannelKey {
  const raw = (value || "").trim().toLowerCase();
  if (/(pdd|拼多多|duoduo)/i.test(raw)) return "pdd";
  if (/(taobao|淘宝|tmall|天猫)/i.test(raw)) return "taobao";
  if (raw === "1688" || /阿里/.test(raw)) return "1688";
  return "other";
}

function PlatformBadge({ value, className }: { value: string | null | undefined; className?: string }) {
  const meta = CHANNELS[channelOf(value)];
  return (
    <span className={cx("inline-flex shrink-0 items-center rounded-md border px-2.5 py-1 text-[11px] font-semibold leading-none tracking-wide shadow-sm", meta.badge, className)}>
      {meta.label}
    </span>
  );
}

/** 订单类型标签：正品 / 耗材（包材）。缺省按 goods（正品）处理，避免旧后端未下发时空白。 */
const ORDER_KIND_META: Record<string, { label: string; badge: string }> = {
  goods: { label: "正品", badge: "border-blue-200 bg-blue-50 text-blue-600" },
  consumable: { label: "耗材", badge: "border-amber-200 bg-amber-50 text-amber-600" },
};
function OrderKindTag({ value, className }: { value?: string | null; className?: string }) {
  const meta = ORDER_KIND_META[value === "consumable" ? "consumable" : "goods"];
  return (
    <span className={cx("inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[8.5px] font-medium leading-none", meta.badge, className)}>
      {meta.label}
    </span>
  );
}

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function Link({ href, ...props }: Omit<ComponentProps<typeof NextLink>, "href"> & { href: string }) {
  const params = useSearchParams();
  let destination = workbenchHref(href);
  if (destination.startsWith("/purchase/workbench") && params.get("order")) {
    const url = new URL(destination, "http://workbench.local");
    if (!url.searchParams.has("order")) url.searchParams.set("order", params.get("order")!);
    destination = url.pathname + url.search;
  }
  return <NextLink href={destination} {...props} />;
}

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function inputDate(value: Date) {
  return value.getFullYear() + "-" + pad(value.getMonth() + 1) + "-" + pad(value.getDate());
}


function fmtMoney(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return "¥" + value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtQty(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 3 });
}

function fmtDate(value: string | null | undefined) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
}

function fmtDateTime(value: string | null | undefined) {
  const date = parseDate(value);
  if (!date) return "未记录";
  return fmtDate(value) + " " + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}

// 1688 源单已关闭/取消：这类订单不再按正常待办推进，界面必须显式标为「已关闭」，
// 而不是沿用 firstUndone 让它看起来还停在某个正常环节上。
function isOrderClosed(orderStatus?: string | null) {
  const value = (orderStatus || "").trim();
  if (!value) return false;
  return /关闭|已取消|交易取消|closed|canceled|cancelled/i.test(value);
}

type StatusInput = {
  orderStatus?: string | null;
  hasException?: boolean;
  firstUndone?: string | null;
  firstUndoneLabel?: string;
  stepStates?: Record<string, WorkbenchStepState>;
  /** 收尾环节的具体卡点（后端统一算好，避免前后端各算一套） */
  closeoutStage?: string | null;
};

/** 收尾子状态 → 状态标签文案 */
const CLOSEOUT_LABELS: Record<string, string> = {
  awaiting_inbound: "待入库",
  awaiting_invoice: "待开发票",
  awaiting_payment: "待付款",
  awaiting_verification: "待认证",
};

const PURCHASE_STEP_ORDER = ["content", "sku", "jackyun_po", "inbound", "invoice"] as const;
const PURCHASE_STEP_LABELS: Record<string, string> = {
  content: "待确认采购内容",
  sku: "待匹配SKU",
  jackyun_po: "待完成本系统采购单",
  inbound: "待入库",
  invoice: "待发票",
  closeout: "待收尾(入库/发票/付款)",
};

function statusLabel(input: StatusInput) {
  if (isOrderClosed(input.orderStatus)) return "已关闭";
  if (input.hasException) return "异常";
  // 优先用后端给的最早未完成步骤；否则在 stepStates 里找明确未完成的采购推进步骤
  //（缺键=该视图不含此维度，不算未完成，避免 workbench 5 步视图误判成「待入库」）。
  let stepKey = input.firstUndone ?? null;
  if (!stepKey && input.stepStates) {
    stepKey =
      PURCHASE_STEP_ORDER.find((key) => {
        const state = input.stepStates?.[key];
        return state !== undefined && !state.done;
      }) ?? null;
    // workbench 视图把收尾合并为 closeout（入库/发票/付款）；税务认证在 7 环节链路中单独展示
    if (!stepKey && input.stepStates.closeout && !input.stepStates.closeout.done) {
      stepKey = "closeout";
    }
  }
  if (!stepKey) return "开票完成";
  // 收尾环节拆到具体卡点：光写「待收尾」看不出是缺入库、缺票还是缺付款
  if (stepKey === "closeout" && input.closeoutStage) {
    const sub = CLOSEOUT_LABELS[input.closeoutStage];
    if (sub) return sub;
  }
  return PURCHASE_STEP_LABELS[stepKey] ?? input.firstUndoneLabel ?? "待处理";
}

function statusClass(label: string) {
  if (label === "已关闭") return "bg-slate-100 text-slate-500";
  if (label === "开票完成") return "bg-emerald-50 text-emerald-600";
  if (label === "异常") return "bg-red-50 text-red-600";
  if (label === "待确认采购内容") return "bg-orange-50 text-orange-600";
  if (label === "待匹配SKU") return "bg-amber-50 text-amber-600";
  if (label === "待完成本系统采购单") return "bg-blue-50 text-blue-600";
  if (label === "待入库") return "bg-emerald-50 text-emerald-600";
  if (label === "待收票" || label === "待开发票") return "bg-violet-50 text-violet-600";
  if (label === "待认证") return "bg-sky-50 text-sky-600";
  if (label === "待付款") return "bg-rose-50 text-rose-600";
  if (label === "待发票" || label === "待收尾(入库/发票/付款)") return "bg-violet-50 text-violet-600";
  return "bg-orange-50 text-orange-600";
}

function exportOrders(items: WorkbenchOrderItem[]) {
  if (typeof window === "undefined" || items.length === 0) return;
  const rows = items.map((item) => [
    fmtDateTime(item.orderDate), CHANNELS[channelOf(item.platform)].label, item.orderKind === "consumable" ? "耗材" : "正品", item.orderNo, item.supplier, item.amount ?? "",
    item.invoiceStatus === "done"
      ? "已开票"
      : item.invoiceStatus === "needs_review"
        ? "发票需复核"
        : (item.invoiceOutstanding ?? "") === ""
          ? ""
          : `未开票 ${item.invoiceOutstanding ?? 0}`,
    statusLabel(item), item.orderStatus,
  ]);
  const csvRows = [["采购时间", "渠道", "类型", "订单号", "供应商", "订单金额", "开票（未开票）", "当前状态", "订单状态"], ...rows];
  const csv = csvRows
    .map((row) => row.map((value) => '"' + String(value).replaceAll('"', '""') + '"').join(","))
    .join("\n");
  const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "采购订单-" + new Date().toISOString().slice(0, 10) + ".csv";
  link.click();
  URL.revokeObjectURL(url);
}

export default function PurchaseWorkbenchPage() {
  const searchParams = useSearchParams();
  const ws = useWorkspace();
  const tabId = useTabRuntime()?.tabId;
  // 隐藏 Tab 不响应全局 Esc：弹层状态要留在原地，等切回来继续用
  const tabActive = useTabActive();
  const [documentVisible, setDocumentVisible] = useState(true);
  const pageActive = tabActive && documentVisible;
  const initialView: ViewMode = (() => {
    const v = searchParams.get("view");
    return parseWorkbenchView(v);
  })();
  const initialOrderId = (() => {
    const raw = searchParams.get("order");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n !== 0 ? n : null;
  })();
  const initialKind: SupplyChainKindFilter = (() => {
    const kind = searchParams.get("kind");
    return kind === "goods" || kind === "consumable" ? kind : "all";
  })();
  const [view, setView] = useState<ViewMode>(initialView);
  const [newOrderOpen, setNewOrderOpen] = useState(false);
  const [newOrderId, setNewOrderId] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  // Keep the active order table light enough for the detail panel and slower LAN clients.
  // Pagination remains unchanged; this only reduces the number of rows mounted at once.
  const pageSize = 20;
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null);
  const [orders, setOrders] = useState<WorkbenchOrderItem[]>([]);
  const [outstandingTotal, setOutstandingTotal] = useState(0);
  const [total, setTotal] = useState(0);
  const [suppliers, setSuppliers] = useState<WorkbenchSupplierSummary[]>([]);
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(initialOrderId);
  const requestedOrder = useRef<number | null>(initialOrderId);
  const [selectedSupplierName, setSelectedSupplierName] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<WorkbenchDetail | null>(null);
  const [supplierDetail, setSupplierDetail] = useState<WorkbenchSupplierDetail | null>(null);
  const [statusFilter, setStatusFilter] = useState<FilterKey>("all");
  const [channelFilter, setChannelFilter] = useState<ChannelKey>("all");
  const [kindFilter, setKindFilter] = useState<SupplyChainKindFilter>(initialKind);
  const [warehouseFilter, setWarehouseFilter] = useState("");
  const [query, setQuery] = useState("");
  const [searchDraft, setSearchDraft] = useState(() => searchParams.get("q") ?? "");
  const [supplierQuery, setSupplierQuery] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [operationsOpen, setOperationsOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [chainOverview, setChainOverview] = useState<ChainOverview | null>(null);
  const [chainOrders, setChainOrders] = useState<ChainOrderRow[]>([]);
  const [chainTotal, setChainTotal] = useState(0);
  const [chainPending, setChainPending] = useState<PendingLink[]>([]);
  const [chainFilter, setChainFilter] = useState<ChainFilter>("all");
  const [chainBusy, setChainBusy] = useState(false);
  const [inboundMatch, setInboundMatch] = useState<InboundMatchSummary | null>(null);
  const [pendingAlloc, setPendingAlloc] = useState<PendingAllocation[]>([]);
  const [matchingBusy, setMatchingBusy] = useState(false);
  // 订单 / 供应商 / 匹配 / 链路四个主视图共用 loading。
  // 用单一序号保证“最后一次请求”才允许写回，避免快速切换视图时旧请求覆盖当前页面。
  const viewRequestSeq = useRef(0);

  // 新建采购单弹窗开着时视为「有未保存内容」，关闭 Tab 需要二次确认
  useTabDirty(newOrderOpen);

  useEffect(() => {
    setView(parseWorkbenchView(searchParams.get("view")));
    const orderId = Number(searchParams.get("order"));
    if (Number.isInteger(orderId) && orderId !== 0) { requestedOrder.current = orderId; setSelectedOrderId(orderId); }
    if (searchParams.get("action") === "new") {
      requestedOrder.current = null;
      setOperationsOpen(false);
      setSelectedOrderId(null);
      setOrderDetail(null);
      setNewOrderOpen(true);
    }
    const status = searchParams.get("status");
    if (["all", "refine", "po", "inbound", "invoice", "exception", "done"].includes(status ?? "")) {
      setStatusFilter(status as FilterKey);
    }
    const kind = searchParams.get("kind");
    setKindFilter(kind === "goods" || kind === "consumable" ? kind : "all");
  }, [searchParams]);

  useEffect(() => {
    const destination = STANDALONE_VIEW_ROUTES[view];
    if (destination) syncWorkspaceUrl(destination);
  }, [view]);

  // 顶部固定栏高度写入 CSS 变量，sticky 详情栏据此定位，避免遮挡或漏缝。
  // 工作区下多个 Tab 同时挂载：限定在本 Tab 面板内查找并写在本面板上，避免量到/影响别的 Tab。
  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(document.visibilityState === "visible");
    updateVisibility();
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);

  // 只让当前可见采购页测量顶部高度，避免隐藏页重复监听 resize。
  useEffect(() => {
    if (!pageActive) return;
    const panel = tabId ? document.querySelector<HTMLElement>(`[data-workspace-panel="${tabId}"]`) : null;
    const host = panel ?? document.documentElement;
    const el = panel?.querySelector<HTMLElement>("header.sticky") ?? panel?.querySelector<HTMLElement>("header") ?? document.querySelector<HTMLElement>("main header");
    if (!el) return;
    const update = () => host.style.setProperty("--wb-header-h", `${el.offsetHeight}px`);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [pageActive, view, tabId]);

  function changeView(next: ViewMode) {
    const standaloneRoute = STANDALONE_VIEW_ROUTES[next];
    if (standaloneRoute) {
      syncWorkspaceUrl(standaloneRoute, "push");
      return;
    }
    setView(next);
    const params = new URLSearchParams(searchParams.toString());
    params.set("view", next);
    params.delete("action");
    // 不要把「自动选中的订单」写进地址：它只是展示默认值，写进去会让切换视图凭空多出一个采购单页签。
    // 用户真正点过的订单在 selectOrder 里已经写进本 Tab 的地址，会原样保留。
    if (next !== "orders") params.delete("status");
    if (next === "suppliers") params.delete("order");
    syncWorkspaceUrl(`/purchase/workbench?${params.toString()}`, "push");
  }

  function selectOrder(orderId: number) {
    const params = new URLSearchParams(searchParams.toString());
    params.set("view", "orders");
    params.set("order", String(orderId));
    params.delete("action");
    syncWorkspaceUrl(`/purchase/workbench?${params.toString()}`);
    setView("orders");
  }

  function changeKind(nextKind: SupplyChainKindFilter) {
    setKindFilter(nextKind);
    const params = new URLSearchParams(searchParams.toString());
    if (nextKind === "all") params.delete("kind");
    else params.set("kind", nextKind);
    syncWorkspaceUrl(`/purchase/workbench?${params.toString()}`);
  }

  useEffect(() => { setPage(1); }, [statusFilter, channelFilter, kindFilter, warehouseFilter, startDate, endDate, query]);

  const loadSummary = useCallback(async () => {
    try { setSummary(await procurementWorkbenchApi.summary()); } catch { setSummary(null); }
  }, []);





  const loadOrders = useCallback(async () => {
    const seq = ++viewRequestSeq.current;
    setLoading(true);
    setError("");
    if (startDate && endDate && startDate > endDate) {
      if (seq === viewRequestSeq.current) {
        setOrders([]);
        setTotal(0);
        setError("开始日期不能晚于结束日期");
        setLoading(false);
      }
      return;
    }
    try {
      const result = await procurementWorkbenchApi.orders({
        status: statusFilter === "all" ? "all" : statusFilter === "refine" ? "sku" : statusFilter,
        q: query || undefined,
        page,
        pageSize,
        startDate: startDate || undefined,
        endDate: endDate || undefined,
        channel: channelFilter,
        kind: kindFilter,
        warehouse: warehouseFilter || undefined,
      });
      if (seq !== viewRequestSeq.current) return;
      const items = result.groups.flatMap((group) => group.items);
      setOrders(items);
      setTotal(result.total);
      setOutstandingTotal(result.invoiceOutstandingTotal ?? 0);
      const requested = requestedOrder.current;
      requestedOrder.current = null;
      setSelectedOrderId((current) => requested ?? items.find(item => item.orderId === current)?.orderId ?? items[0]?.orderId ?? null);
    } catch {
      if (seq !== viewRequestSeq.current) return;
      setOrders([]);
      setTotal(0);
      setOutstandingTotal(0);
      setError("订单数据加载失败，请刷新后重试");
    } finally {
      if (seq === viewRequestSeq.current) setLoading(false);
    }
  }, [channelFilter, endDate, kindFilter, query, startDate, statusFilter, warehouseFilter, page]);

  const loadSuppliers = useCallback(async () => {
    const seq = ++viewRequestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await procurementWorkbenchApi.suppliers(200);
      if (seq !== viewRequestSeq.current) return;
      setSuppliers(result.items);
      setSelectedSupplierName((current) => {
        if (result.items.length === 0) return null;
        return current && result.items.some((item) => item.supplierName === current)
          ? current
          : result.items[0].supplierName;
      });
    } catch {
      if (seq !== viewRequestSeq.current) return;
      setSuppliers([]);
      setSelectedSupplierName(null);
      setError("供应商数据加载失败，请刷新后重试");
    } finally {
      if (seq === viewRequestSeq.current) setLoading(false);
    }
  }, []);

  const loadMatching = useCallback(async () => {
    const seq = ++viewRequestSeq.current;
    setLoading(true);
    setError("");
    try {
      const [match, pending] = await Promise.all([
        skuMatchingApi.inboundSummary(),
        skuMatchingApi.pending(100),
      ]);
      if (seq !== viewRequestSeq.current) return;
      setInboundMatch(match);
      setPendingAlloc(pending);
    } catch {
      if (seq === viewRequestSeq.current) setError("匹配数据加载失败，请刷新后重试");
    } finally {
      if (seq === viewRequestSeq.current) setLoading(false);
    }
  }, []);

  const runInboundAuto = useCallback(async () => {
    setMatchingBusy(true);
    setError("");
    try {
      const result = await skuMatchingApi.runInboundAuto();
      const s = result.stats;
      setNotice(`入库自动匹配完成：共 ${s.total} 条，金额校验通过 ${s.price_ok}，金额异常 ${s.price_mismatch}，人工结果保留 ${s.manual ?? 0}`);
      await loadMatching();
    } catch {
      setError("自动匹配执行失败，请重试");
    } finally {
      setMatchingBusy(false);
    }
  }, [loadMatching]);

  const loadChain = useCallback(async () => {
    const seq = ++viewRequestSeq.current;
    setLoading(true);
    setError("");
    try {
      const result = await procurementChainApi.workspace(500);
      if (seq !== viewRequestSeq.current) return;
      setChainOverview(result.overview);
      setChainOrders(result.orders.items);
      setChainTotal(result.orders.total);
      setChainPending(result.pending.items);
    } catch {
      if (seq !== viewRequestSeq.current) return;
      setChainOverview(null);
      setChainOrders([]);
      setChainPending([]);
      setError("链路数据加载失败，请刷新后重试");
    } finally {
      if (seq === viewRequestSeq.current) setLoading(false);
    }
  }, []);

  async function confirmChainLink(kind: string, linkId: number) {
    setChainBusy(true);
    try {
      if (kind === "invoice") await procurementChainApi.confirmInvoiceLink(linkId);
      else await procurementChainApi.confirmLink(linkId);
      setNotice("关联已确认");
      await loadChain();
    } catch (caught) {
      setNotice("确认失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  async function rejectChainLink(kind: string, linkId: number) {
    setChainBusy(true);
    try {
      if (kind === "invoice") await procurementChainApi.deleteInvoiceLink(linkId);
      else await procurementChainApi.deleteLink(linkId);
      setNotice("关联建议已拒绝");
      await loadChain();
    } catch (caught) {
      setNotice("拒绝失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  useEffect(() => {
    if (!pageActive) return;
    // summary 与当前子视图无关；只在页面重新激活时加载，业务动作完成后再显式刷新。
    void loadSummary();
  }, [loadSummary, pageActive]);

  useEffect(() => {
    if (!pageActive) return;
    if (view === "orders") void loadOrders();
    else if (view === "chain") void loadChain();
    else if (view === "matching") void loadMatching();
    else if (view === "suppliers") void loadSuppliers();
  }, [loadChain, loadMatching, loadOrders, loadSuppliers, pageActive, view]);

  useEffect(() => {
    if (!pageActive) return;
    if (view !== "orders" || selectedOrderId === null) {
      setOrderDetail(null);
      return;
    }
    let cancelled = false;
    setOrderDetail(null);
    setDetailLoading(true);
    procurementWorkbenchApi.workbench(selectedOrderId)
      .then((detail) => { if (!cancelled) setOrderDetail(detail); })
      .catch(() => { if (!cancelled) setOrderDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [pageActive, selectedOrderId, view]);

  useEffect(() => {
    if (!pageActive) return;
    if (view !== "suppliers" || !selectedSupplierName) {
      setSupplierDetail(null);
      return;
    }
    let cancelled = false;
    setSupplierDetail(null);
    setDetailLoading(true);
    procurementWorkbenchApi.supplierDetail(selectedSupplierName)
      .then((detail) => { if (!cancelled) setSupplierDetail(detail); })
      .catch(() => { if (!cancelled) setSupplierDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [pageActive, selectedSupplierName, view]);

  async function runMatch() {
    setBusyAction("match");
    try {
      const result = await procurementChainApi.runMatch();
      const autoConfirm = result.autoConfirm as { confirmedChain?: number; confirmedInvoices?: number; resolvedInboundUsage?: number } | undefined;
      const created = Number(result.created ?? 0);
      const confirmed = Number(autoConfirm?.confirmedChain ?? 0) + Number(autoConfirm?.confirmedInvoices ?? 0);
      const parts = [`自动化完成：新建 ${created} 条关联`];
      if (confirmed > 0) parts.push(`确认 ${confirmed} 条订单/发票关联`);
      if (Number(autoConfirm?.resolvedInboundUsage ?? 0) > 0) parts.push(`自动处理 ${autoConfirm?.resolvedInboundUsage} 条入库耗材流水`);
      setNotice(parts.join("，"));
      await Promise.all([
        loadSummary(),
        ...(view === "orders" || view === "chain" ? [] : [loadChain()]),
        view === "orders" ? loadOrders() : view === "chain" ? loadChain() : view === "matching" ? loadMatching() : loadSuppliers(),
      ]);
    } catch (caught) {
      setNotice("生成匹配建议失败：" + String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function runPrelink(auto: boolean) {
    setBusyAction("prelink");
    try {
      const result = await procurementChainApi.prelink(auto);
      const s = result.stats;
      const parts: string[] = [];
      if (s.autoLinked > 0) parts.push(`自动关联并确认 ${s.autoLinked} 条`);
      if (s.pendingSuggested > 0) parts.push(`生成 ${s.pendingSuggested} 条待人工确认`);
      if (parts.length === 0) parts.push("无新增候选（订单有 SKU 分配后会继续按重合度自动处理）");
      setNotice("预关联完成：" + parts.join("，"));
      await Promise.all([loadChain(), loadSummary()]);
    } catch (caught) {
      setNotice("预关联失败：" + String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function chainManualLink(orderId: number, targetId: number) {
    setChainBusy(true);
    try {
      await procurementChainApi.manualLink(orderId, "inbound", targetId, "链路建链视图人工选择入库单");
      setNotice("已关联入库单");
      await loadChain();
    } catch (caught) {
      setNotice("关联失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  async function refresh() {
    setBusyAction("refresh");
    await Promise.all([
      loadSummary(),
      ...(view === "orders" || view === "chain" ? [] : [loadChain()]),
      view === "orders" ? loadOrders() : view === "chain" ? loadChain() : view === "matching" ? loadMatching() : loadSuppliers(),
    ]);
    setNotice("采购订单已刷新");
    setBusyAction("");
  }

  const refreshSelectedOrder = useCallback(async () => {
    const targetOrderId = selectedOrderId ?? newOrderId;
    if (targetOrderId === null) return;
    setDetailLoading(true);
    try {
      const nextDetail = await procurementWorkbenchApi.workbench(targetOrderId);
      setOrderDetail(nextDetail);
      await Promise.all([loadSummary(), loadOrders()]);
    } finally {
      setDetailLoading(false);
    }
  }, [loadOrders, loadSummary, newOrderId, selectedOrderId]);

  const openOperations = useCallback((orderId?: number) => {
    if (orderId != null && orderId !== selectedOrderId) {
      selectOrder(orderId);
    }
    setOperationsOpen(true);
  }, [selectedOrderId]);

  useEffect(() => {
    if (!operationsOpen || !tabActive) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOperationsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [operationsOpen, tabActive]);

  const resetSupplyChainFilters = useCallback(() => {
    setStatusFilter("all");
    setChannelFilter("all");
    setKindFilter("all");
    setWarehouseFilter("");
    setStartDate("");
    setEndDate("");
    setQuery("");
    setSearchDraft("");
    setPage(1);
  }, []);

  const exportCurrentPurchaseOrders = useCallback(async () => {
    setBusyAction("export");
    setError("");
    setNotice("");
    try {
      const params = new URLSearchParams({
        status: statusFilter === "refine" ? "sku" : statusFilter,
        channel: channelFilter,
        kind: kindFilter,
      });
      if (query) params.set("q", query);
      if (warehouseFilter) params.set("warehouse", warehouseFilter);
      if (startDate) params.set("start_date", startDate);
      if (endDate) params.set("end_date", endDate);
      const response = await authenticatedFetch(`/api/v1/data/export/purchase_orders?${params.toString()}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `导出失败（${response.status}）`);
      }
      const blob = await response.blob();
      const encodedName = response.headers.get("content-disposition")?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const filename = encodedName ? decodeURIComponent(encodedName) : `采购订单-核验-${new Date().toISOString().slice(0, 10)}.xlsx`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      const count = response.headers.get("x-export-row-count");
      setNotice(`采购订单核验包已导出${count ? `（${count} 条主记录）` : ""}，包含主单、采购明细和入库关联。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusyAction("");
    }
  }, [channelFilter, endDate, kindFilter, query, startDate, statusFilter, warehouseFilter]);

  // 订单被删除后：清掉选中项与 URL 上的 order 参数，重新拉列表（loadOrders 会自动选中下一张）。
  const removeDeletedOrder = useCallback(async () => {
    requestedOrder.current = null;
    setSelectedOrderId(null);
    setOrderDetail(null);
    const params = new URLSearchParams(searchParams.toString());
    params.set("view", "orders");
    params.delete("order");
    params.delete("action");
    // 交给工作区改写当前 Tab 的地址：若工作台列表 Tab 已存在则回到它，否则就地变成列表
    syncWorkspaceUrl(ws.retarget(`/purchase/workbench?${params}`));
    await Promise.all([loadSummary(), loadOrders()]);
  }, [loadOrders, loadSummary, searchParams, ws]);

  const closeNewOrder = useCallback(() => {
    setNewOrderOpen(false);
    setNewOrderId(null);
    setOperationsOpen(false);
    requestedOrder.current = null;
    setSelectedOrderId(null);
    setOrderDetail(null);
    const params = new URLSearchParams(searchParams.toString());
    params.delete("action");
    params.delete("order");
    syncWorkspaceUrl(ws.retarget(`/purchase/workbench${params.toString() ? `?${params}` : ""}`));
  }, [searchParams, ws]);

  const openNewOrder = useCallback(() => {
    setOperationsOpen(false);
    requestedOrder.current = null;
    setSelectedOrderId(null);
    setOrderDetail(null);
    setNewOrderId(null);
    setNewOrderOpen(true);
  }, []);

  const handleNewOrderCreated = useCallback((orderId: number) => {
    // 新建后的完整录入只留在 NewPurchaseModal 内，不能再把同一订单选到列表下方详情区。
    requestedOrder.current = null;
    setNewOrderId(orderId);
    setSelectedOrderId(null);
    setOrderDetail(null);
    setDetailLoading(true);
    resetSupplyChainFilters();
    setNotice("基础信息已保存，当前窗口已进入完整采购订单录入");
    void Promise.all([loadOrders(), loadSummary()]);
    void procurementWorkbenchApi.workbench(orderId)
      .then((detail) => setOrderDetail(detail))
      .catch(() => {
        setOrderDetail(null);
        setError("新建订单详情加载失败，请刷新后重试");
      })
      .finally(() => setDetailLoading(false));
  }, [loadOrders, loadSummary, resetSupplyChainFilters]);

  function renderNewOrderEditor(orderId: number) {
    const createdDetail = orderDetail?.order.orderId === orderId ? orderDetail : null;
    return (
      <OrderDetailPanel
        key={orderId}
        detail={createdDetail}
        loading={detailLoading || createdDetail == null}
        onOrderChanged={refreshSelectedOrder}
        onOrderDeleted={(message) => { setNotice(message); closeNewOrder(); void removeDeletedOrder(); }}
        onOpenSupplier={(name) => { closeNewOrder(); setSelectedSupplierName(name); changeView("suppliers"); }}
        onCloseDialog={closeNewOrder}
      />
    );
  }

  const filteredSuppliers = useMemo(() => {
    const normalized = supplierQuery.trim().toLowerCase();
    return normalized ? suppliers.filter((supplier) => supplier.supplierName.toLowerCase().includes(normalized)) : suppliers;
  }, [supplierQuery, suppliers]);

  const newPurchaseDialog = newOrderOpen ? <NewPurchaseModal
    onClose={closeNewOrder}
    createdOrderId={newOrderId}
    renderCreated={renderNewOrderEditor}
    onCreated={handleNewOrderCreated}
  /> : null;

  if (view === "orders") {
    return (
      <>
        <SupplyChainReplica
          summary={summary}
          orders={orders}
          total={total}
          page={page}
          pageSize={pageSize}
          selectedOrderId={newOrderOpen ? null : selectedOrderId}
          detail={newOrderOpen ? null : orderDetail}
          loading={loading}
          detailLoading={detailLoading}
          refreshBusy={busyAction === "refresh"}
          exportBusy={busyAction === "export"}
          statusFilter={statusFilter}
          channelFilter={channelFilter}
          kindFilter={kindFilter}
          warehouseFilter={warehouseFilter}
          startDate={startDate}
          endDate={endDate}
          searchDraft={searchDraft}
          onStatusFilterChange={setStatusFilter}
          onChannelFilterChange={setChannelFilter}
          onKindFilterChange={changeKind}
          onWarehouseFilterChange={setWarehouseFilter}
          onStartDateChange={setStartDate}
          onEndDateChange={setEndDate}
          onSearchDraftChange={setSearchDraft}
          onApplySearch={() => setQuery(searchDraft.trim())}
          onReset={resetSupplyChainFilters}
          onNewOrder={openNewOrder}
          onExport={exportCurrentPurchaseOrders}
          onRefresh={refresh}
          onSelectOrder={selectOrder}
          onPageChange={(nextPage) => { setSelectedOrderId(null); setPage(nextPage); }}
          onOpenOperations={openOperations}
          onCloseOperations={() => setOperationsOpen(false)}
          operationsOpen={operationsOpen}
          onOpenWorkbenchView={(nextView) => changeView(nextView)}
          selectedOrderIds={[]}
          alibaba1688Job={null}
          onOpenExceptions={() => changeView("exceptions")}
          onToggleOrder={() => {}}
          onToggleAllVisible={() => {}}
          onClearOrderSelection={() => {}}
          onDeleteSelected={() => {}}
          deleteBusy={false}
        />
        {(notice || error) && <div className="fixed bottom-5 right-5 z-toast max-w-md rounded-lg border border-blue-100 bg-white px-4 py-3 text-[12px] text-slate-700 shadow-xl"><div className="flex items-start gap-3"><span className={error ? "text-rose-600" : "text-blue-600"}>{error || notice}</span><button onClick={() => { setNotice(""); setError(""); }} className="shrink-0 text-slate-400 hover:text-slate-700">×</button></div></div>}
        {operationsOpen && <div className="fixed inset-0 z-modal flex items-start justify-center overflow-y-auto bg-slate-950/35 p-3 sm:p-6" onMouseDown={(event) => { if (event.target === event.currentTarget) setOperationsOpen(false); }} role="dialog" aria-modal="true" aria-label="编辑采购订单">
          <div className="my-auto flex h-[90vh] w-full max-w-[1440px] max-h-[calc(100vh-32px)] flex-col overflow-hidden rounded-xl border border-slate-200 bg-[#f7f9fd] shadow-2xl">
            <div className="flex min-h-0 flex-1 flex-col"><OrderDetailPanel key={selectedOrderId} detail={orderDetail} loading={detailLoading} onOrderChanged={refreshSelectedOrder} onOrderDeleted={(message) => { setNotice(message); setOperationsOpen(false); void removeDeletedOrder(); }} onOpenSupplier={(name) => { setOperationsOpen(false); setSelectedSupplierName(name); changeView("suppliers"); }} onCloseDialog={() => setOperationsOpen(false)} /></div>
          </div>
        </div>}
        {newPurchaseDialog}
      </>
    );
  }

  if (STANDALONE_VIEW_ROUTES[view]) return null;

  return (
    <div className="min-h-screen bg-[#f7f8fc] text-[#26324b]">
      <div className="min-w-0 px-5 pb-8 pt-5 sm:px-6 2xl:px-7">
          {!(["orders", "suppliers", "chain", "matching"] as ViewMode[]).includes(view) && (
            <select aria-label="工作台功能导航" className="mb-3 w-full rounded-lg border border-slate-200 bg-white p-2 lg:hidden" value={view} onChange={event => changeView(event.target.value as ViewMode)}>
              {Object.entries(WORKBENCH_VIEWS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          )}
          <WorkbenchHeader
            view={view}
            busyAction={busyAction}
            onViewChange={changeView}
            onNewOrder={openNewOrder}
            onMatch={runMatch}
            onRefresh={refresh}
            onExport={() => exportOrders(orders)}
          />
          {notice && (
            <div className="mt-3 flex items-center justify-between rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-[12px] text-indigo-700">
              <span>{notice}</span>
              <button onClick={() => setNotice("")} className="text-lg leading-none text-indigo-400 hover:text-indigo-700">×</button>
            </div>
          )}
          {error && <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-600">{error}</div>}
          {["orders", "suppliers", "chain", "matching"].includes(view) && (
            <KpiGrid
              summary={summary}
              onOpenChain={() => changeView("chain")}
            />
          )}

          {view === "chain" ? (
            <ChainPanel
              overview={chainOverview}
              orders={chainOrders}
              total={chainTotal}
              pending={chainPending}
              filter={chainFilter}
              loading={loading}
              busy={chainBusy}
              prelinkBusy={busyAction === "prelink"}
              xrefBusy={busyAction === "xref"}
              onFilterChange={setChainFilter}
              onConfirm={confirmChainLink}
              onReject={rejectChainLink}
              onPrelink={runPrelink}
              onManualLink={chainManualLink}
              onReload={loadChain}
              onNotice={setNotice}
              onStageView={changeView}
            />
          ) : view === "matching" ? (
            <MatchingView
              summary={inboundMatch}
              pending={pendingAlloc}
              busy={matchingBusy}
              loading={loading}
              onRunAuto={runInboundAuto}
              onRefresh={loadMatching}
              onNotice={setNotice}
              onOpenOrder={selectOrder}
            />
          ) : view === "suppliers" ? (
            <div className="mt-5 grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(520px,1fr)]">
              <SupplierList
                suppliers={filteredSuppliers}
                loading={loading}
                query={supplierQuery}
                selectedName={selectedSupplierName}
                onQueryChange={setSupplierQuery}
                onSelect={setSelectedSupplierName}
              />
              <SupplierDetailPanel
                detail={supplierDetail}
                loading={detailLoading}
                onRenamed={async (newName) => {
                  setSelectedSupplierName(newName);
                  await loadSuppliers();
                }}
              />
            </div>
          ) : <WorkspaceModule key={view} view={view} />}
      </div>
      {newPurchaseDialog}
    </div>
  );
}

function WorkbenchHeader({ view, busyAction, onViewChange, onNewOrder, onMatch, onRefresh, onExport }: {
  view: ViewMode;
  busyAction: string;
  onViewChange: (view: ViewMode) => void;
  onNewOrder: () => void;
  onMatch: () => void;
  onRefresh: () => void;
  onExport: () => void;
}) {
  if (!["orders", "suppliers", "chain", "matching"].includes(view)) return (
    <header className="flex items-center justify-between gap-4"><div><p className="text-xs text-slate-400">电商经营数据平台 / {WORKBENCH_VIEWS[view]}</p><h1 className="mt-1 text-2xl font-semibold">{WORKBENCH_VIEWS[view]}</h1></div><HeaderButton icon="orders" onClick={() => onViewChange("orders")}>返回采购订单</HeaderButton></header>
  );
  return (
    <header className="sticky top-12 z-30 -mx-5 -mt-5 flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/60 bg-[#f7f8fc] px-5 pb-3 pt-5 sm:-mx-6 sm:px-6 2xl:-mx-7 2xl:px-7">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <div className="mb-1 text-[10px] text-slate-400">采购中心　/　采购订单</div>
          <div className="flex items-baseline gap-4">
            <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">采购订单</h1>
            <p className="hidden text-[12px] text-slate-400 2xl:block">按下单时间组织采购主单，并关联生产、到仓、开票与付款状态</p>
          </div>
          <p className="mt-1 text-[11px] text-slate-400 2xl:hidden">以订单时间为主线，高效推进采购全流程</p>
        </div>
        <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
          <ViewButton active={view === "orders"} onClick={() => onViewChange("orders")} icon="orders">订单视图</ViewButton>
          <ViewButton active={view === "suppliers"} onClick={() => onViewChange("suppliers")} icon="users">供应商维度</ViewButton>
          <ViewButton active={view === "chain"} onClick={() => onViewChange("chain")} icon="chart">链路建链</ViewButton>
          <ViewButton active={view === "matching"} onClick={() => onViewChange("matching")} icon="box">SKU 匹配</ViewButton>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <HeaderLink href="/data-center-import" icon="import">数据接入</HeaderLink>
        <HeaderButton onClick={onNewOrder} icon="plus">新建采购记录</HeaderButton>
        <HeaderButton icon="magic" primary busy={busyAction === "match"} onClick={onMatch}>自动匹配并确认</HeaderButton>
        <HeaderButton icon="download" onClick={onExport}>导出</HeaderButton>
        <HeaderButton icon="refresh" busy={busyAction === "refresh"} onClick={onRefresh}>刷新</HeaderButton>
      </div>
    </header>
  );
}

function ViewButton({ active, icon, onClick, children }: { active: boolean; icon: IconName; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} className={cx(
      "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors",
      active ? "bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-200" : "text-slate-500 hover:bg-slate-50"
    )}><Icon name={icon} size={14} />{children}</button>
  );
}

function HeaderLink({ href, icon, children }: { href: string; icon: IconName; children: ReactNode }) {
  return <Link href={workbenchHref(href)} className="flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-600 shadow-sm hover:bg-slate-50"><Icon name={icon} size={15} />{children}</Link>;
}

function HeaderButton({ icon, primary, busy, disabled, onClick, children }: {
  icon: IconName; primary?: boolean; busy?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  const isDisabled = busy || disabled;
  return (
    <button
      disabled={isDisabled}
      onClick={isDisabled ? undefined : onClick}
      title={disabled ? "今日配额已耗尽，请走文件入库" : undefined}
      className={cx(
        "flex h-9 items-center gap-1.5 rounded-lg px-3 text-[11px] font-medium shadow-sm transition-colors",
        isDisabled ? "cursor-not-allowed border border-slate-200 bg-slate-50 text-slate-400" :
        primary ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white hover:from-indigo-700 hover:to-violet-700" : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
      )}
    ><Icon name={icon} size={15} />{busy ? "处理中…" : children}</button>
  );
}

function KpiGrid({ summary, onOpenChain }: {
  summary: WorkbenchSummary | null;
  onOpenChain?: () => void;
}) {
  const cards: Array<{ label: string; value: number | string; hint: string; icon: IconName; tone: string }> = [
    {
      label: "新订单", value: summary?.newOrders ?? "—", hint: "按采购时间统计", icon: "orders", tone: "blue",
    },
    { label: "待匹配SKU", value: summary?.pendingSku ?? "—", hint: "待完善采购内容", icon: "magic", tone: "violet" },
    { label: "已到货待入库", value: summary?.pendingInbound ?? "—", hint: "等待入库确认", icon: "box", tone: "green" },
    { label: "待开票", value: summary?.pendingInvoice ?? "—", hint: "等待发票清单", icon: "receipt", tone: "amber" },
    { label: "异常数", value: summary?.exceptionCount ?? "—", hint: "需人工处理", icon: "reconcile", tone: "red" },
    {
      label: "已付款率",
      value: summary ? `${summary.paidRate}%` : "—",
      hint: summary ? `已付 ${fmtMoney(summary.paidAmount)} / 应付 ${fmtMoney(summary.totalAmount)}` : "采购摘要未就绪",
      icon: "reconcile",
      tone: "teal",
    },
  ];
  const funnel = summary?.funnel ?? null;
  const todos = summary?.todos ?? null;
  const steps = funnel?.steps ?? [];
  const pending = todos?.pending ?? 0;
  const doneStages = steps.filter((step) => step.pct >= 100).length;
  const stepTotal = funnel?.stepTotal ?? steps.length;
  return (
    <section className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      {cards.map((card) => <KpiCard key={card.label} {...card} />)}
      {steps.length > 0 && (
        <article
          role="button"
          tabIndex={0}
          onClick={onOpenChain}
          onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpenChain?.(); } }}
          title={steps.map((step) => `${CIRCLED[step.no - 1] ?? step.no} ${step.label}：${step.count}/${funnel?.total ?? 0}（${step.pct}%）｜${step.act}`).join("\n")}
          className="flex min-h-[84px] cursor-pointer flex-col justify-center gap-1 rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)] transition-colors hover:border-indigo-200 hover:bg-indigo-50/30 focus:outline-none focus:ring-2 focus:ring-indigo-200"
        >
          <div className="flex items-center gap-1.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-600"><Icon name="magic" size={12} /></span>
            <span className="truncate text-[10.5px] font-medium text-slate-500">环节完成度</span>
          </div>
          <div className="text-[19px] font-semibold leading-6 tabular-nums text-slate-900">{doneStages}/{stepTotal}<span className="ml-1.5 text-[9.5px] font-normal text-slate-400">{pending > 0 ? <span className="font-medium text-amber-600">{pending} 个待办</span> : "暂无待办"}</span></div>
          <div className="mt-2 flex items-end gap-1">
            {steps.map((step) => {
              const dimColor = step.dimension === "1688" ? "bg-indigo-500" : step.dimension === "purchase" ? "bg-violet-500" : step.dimension === "jackyun" ? "bg-teal-500" : "bg-amber-500";
              return (
                <div key={step.key} className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="h-1 overflow-hidden rounded-full bg-slate-100">
                    <div className={cx("h-full", dimColor)} style={{ width: step.pct + "%" }} />
                  </div>
                  <div className="truncate text-center font-mono text-[8px] leading-3 text-slate-400">{CIRCLED[step.no - 1] ?? step.no}{step.pct}%</div>
                </div>
              );
            })}
          </div>
        </article>
      )}
    </section>
  );
}

function KpiCard({ label, value, hint, icon, tone }: {
  label: string; value: number | string; hint: string; icon: IconName; tone: string;
}) {
  const tones: Record<string, string> = {
    blue: "bg-blue-50 text-blue-600", violet: "bg-violet-50 text-violet-600", orange: "bg-orange-50 text-orange-600",
    green: "bg-emerald-50 text-emerald-600", amber: "bg-amber-50 text-amber-600", red: "bg-red-50 text-red-600",
    teal: "bg-teal-50 text-teal-600",
  };
  return (
    <article title={`${label}（${hint}）`} className="flex min-h-[84px] flex-col justify-center gap-0.5 rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
      <div className="flex items-center gap-1.5">
        <span className={cx("flex h-5 w-5 shrink-0 items-center justify-center rounded-md", tones[tone])}><Icon name={icon} size={12} /></span>
        <span className="truncate text-[10.5px] font-medium text-slate-500">{label}</span>
      </div>
      <div className="text-[19px] font-semibold leading-6 tabular-nums text-slate-900">{value}</div>
      <div className="truncate text-[9.5px] text-slate-400" title={hint}>{hint}</div>
    </article>
  );
}

function OrderDetailPanel({ detail, loading, onOrderChanged, onOrderDeleted, onOpenSupplier, onCloseDialog }: {
  detail: WorkbenchDetail | null;
  loading: boolean;
  onOrderChanged: () => Promise<void>;
  onOrderDeleted: (message: string) => void;
  onOpenSupplier: (name: string) => void;
  onCloseDialog?: () => void;
}) {
  const [skuEditorOpen, setSkuEditorOpen] = useState(false);
  const panelSearchParams = useSearchParams();
  const panelWorkspace = useWorkspace();
  const [skuEditorTargetDoc, setSkuEditorTargetDoc] = useState<number | null>(null);
  const [skuCatalog, setSkuCatalog] = useState<CatalogSkuRow[]>([]);
  const [skuCatalogLoading, setSkuCatalogLoading] = useState(false);
  const [selectedSkuId, setSelectedSkuId] = useState<number | null>(null);
  const [skuEntry, setSkuEntry] = useState("");
  const [skuQty, setSkuQty] = useState("1");
  const [skuTotal, setSkuTotal] = useState("");
  const [skuConsumableId, setSkuConsumableId] = useState<number | null>(null);
  const [skuConsumableEntry, setSkuConsumableEntry] = useState("");
  const [skuConsumableQty, setSkuConsumableQty] = useState("");
  const [skuConsumableAutoQty, setSkuConsumableAutoQty] = useState(false);
  const [skuConsumableExempt, setSkuConsumableExempt] = useState(false);
  const [skuConsumableTouched, setSkuConsumableTouched] = useState(false);
  const [pendingConsumableUpdate, setPendingConsumableUpdate] = useState<PendingConsumableUpdate | null>(null);
  const [skuAction, setSkuAction] = useState("");
  const [editingAllocation, setEditingAllocation] = useState<number | null>(null);
  const [skuMessage, setSkuMessage] = useState("");
  const [expenseType, setExpenseType] = useState("pack");
  const [expenseAmount, setExpenseAmount] = useState("");
  const [expenseMessage, setExpenseMessage] = useState("");
  const [adjEditing, setAdjEditing] = useState(false);
  const [adjValue, setAdjValue] = useState("");
  const [adjNote, setAdjNote] = useState("");
  const [inboundEditorOpen, setInboundEditorOpen] = useState(false);
  const [inboundCandidates, setInboundCandidates] = useState<ChainLinkCandidate[]>([]);
  const [inboundCandidateLoading, setInboundCandidateLoading] = useState(false);
  const [inboundQuery, setInboundQuery] = useState("");
  const [selectedInboundId, setSelectedInboundId] = useState<number | null>(null);
  const [replaceInboundLinkId, setReplaceInboundLinkId] = useState<number | null>(null);
  const [inboundMessage, setInboundMessage] = useState("");
  const autoLinkedInboundQuery = useRef("");
  const [editorBusy, setEditorBusy] = useState(false);
  const [usageMaterials, setUsageMaterials] = useState<ConsumableRow[]>([]);
  const [usageMappings, setUsageMappings] = useState<ConsumableMappingRow[]>([]);
  const [usageLoading, setUsageLoading] = useState(false);
  const [docAmountBusy, setDocAmountBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [poBusy, setPoBusy] = useState(false);
  const [poMessage, setPoMessage] = useState("");
  const [poEditorOpen, setPoEditorOpen] = useState(false);
  const [poListOpen, setPoListOpen] = useState(false);
  const [poLinkNo, setPoLinkNo] = useState("");
  const [poRelationKind, setPoRelationKind] = useState("");
  const [poAllocAmount, setPoAllocAmount] = useState("");
  const [mainSaving, setMainSaving] = useState(false);
  const [mainError, setMainError] = useState("");
  const [mainForm, setMainForm] = useState({ orderNo: "", supplier: "", title: "", date: "", goods: "", freight: "", discount: "", paid: "", orderAmount: "", platform: "other", warehouseId: "", buyer: "", purchaseStatus: "" });
  const [inboundNoEntry, setInboundNoEntry] = useState("");
  const [expandedConsumables, setExpandedConsumables] = useState<number[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [consumablePurchaseRows, setConsumablePurchaseRows] = useState<ConsumablePurchaseRow[]>([]);
  const [editingConsumable, setEditingConsumable] = useState<ModalConsumableItem | null>(null);
  const [consumableEditQty, setConsumableEditQty] = useState("");
  const [consumableEditTotal, setConsumableEditTotal] = useState("");
  const [consumableEditBusy, setConsumableEditBusy] = useState(false);



  useEffect(() => {
    setSkuEditorOpen(false);
    setSelectedSkuId(null);
    setSkuEntry("");
    setSkuQty("1");
    setSkuTotal("");
    setSkuConsumableId(null);
    setSkuConsumableEntry("");
    setSkuConsumableQty("");
    setSkuConsumableAutoQty(false);
    setSkuConsumableExempt(false);
    setSkuConsumableTouched(false);
    setPendingConsumableUpdate(null);
    setSkuMessage("");
    setExpenseAmount("");
    setExpenseMessage("");
    setEditingAllocation(null);
    setInboundEditorOpen(false);
    setInboundCandidates([]);
    setInboundQuery("");
    setSelectedInboundId(null);
    setReplaceInboundLinkId(null);
    setInboundMessage("");
    setUsageMaterials([]);
    setUsageMappings([]);
    // 注意：order 在组件后段才解构（detail 非空分支），此处用 detail?.order 避免早退分支下 TDZ 崩溃
    setPoEditorOpen(false);
    setPoListOpen(false);
    setPoMessage("");
    setPoLinkNo("");
    setPoRelationKind("");
    setPoAllocAmount("");
    setMainSaving(false);
    setMainError("");
    setConsumablePurchaseRows([]);
    setEditingConsumable(null);
    setConsumableEditQty("");
    setConsumableEditTotal("");
    const initialOrder = detail?.order;
    const initialWarehouseId = detail?.warehouse?.warehouseId ?? detail?.warehouse?.targetWarehouseId ?? initialOrder?.warehouseId;
    setMainForm({
      orderNo: initialOrder?.orderNo ?? "",
      supplier: initialOrder?.supplier ?? "",
      title: initialOrder?.title ?? "",
      date: initialOrder?.orderDate ? fmtDate(initialOrder.orderDate) : "",
      goods: initialOrder?.goodsTotal != null ? String(initialOrder.goodsTotal) : "",
      freight: initialOrder?.freight != null ? String(initialOrder.freight) : "",
      discount: initialOrder?.discount != null ? String(initialOrder.discount) : "",
      paid: initialOrder?.paidAmount != null ? String(initialOrder.paidAmount) : "",
      orderAmount: initialOrder?.amount != null ? String(initialOrder.amount) : "",
      platform: initialOrder?.platform ?? "other",
      warehouseId: initialWarehouseId != null ? String(initialWarehouseId) : "",
      buyer: initialOrder?.buyer ?? "",
      purchaseStatus: initialOrder?.purchaseStatus ?? "",
    });
    setInboundNoEntry(((detail?.detail?.inbound ?? []) as InboundRow[]).find((row) => row.goodsdocNo)?.goodsdocNo ?? "");
    setExpandedConsumables([]);

  }, [detail?.order.orderId, detail?.order.orderNo]);

  useEffect(() => {
    let cancelled = false;
    const orderNo = detail?.order.orderNo;
    if (!orderNo || detail?.order.orderKind === "goods") {
      setConsumablePurchaseRows([]);
      return () => { cancelled = true; };
    }
    consumablesApi.purchases("", undefined, orderNo)
      .then((rows) => {
        if (!cancelled) setConsumablePurchaseRows(rows);
      })
      .catch(() => { if (!cancelled) setConsumablePurchaseRows([]); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId, detail?.order.orderKind, detail?.order.orderNo]);

  useEffect(() => {
    let cancelled = false;
    setSkuCatalogLoading(true);
    dashboardApi.products("", 500)
      .then(rows => { if (!cancelled) setSkuCatalog(rows); })
      .catch(() => { if (!cancelled) setSkuMessage("吉客云 SKU 主档加载失败，请重新打开分配重试"); })
      .finally(() => { if (!cancelled) setSkuCatalogLoading(false); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId]);

  useEffect(() => {
    const orderId = detail?.order.orderId;
    if (!inboundEditorOpen || !orderId) return;
    let cancelled = false;
    setInboundCandidateLoading(true);
    procurementChainApi.candidates(orderId, "inbound", inboundQuery, 500)
      .then((result) => { if (!cancelled) setInboundCandidates(result.items); })
      .catch(() => { if (!cancelled) setInboundMessage("入库单候选加载失败，请刷新后重试"); })
      .finally(() => { if (!cancelled) setInboundCandidateLoading(false); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId, inboundEditorOpen, inboundQuery]);

  useEffect(() => {
    let cancelled = false;
    setUsageLoading(true);
    Promise.all([consumablesApi.list(), consumablesApi.mappings()])
      .then(([materials, mappings]) => {
        if (!cancelled) { setUsageMaterials(materials); setUsageMappings(mappings); }
      })
      .catch(() => { if (!cancelled) setInboundMessage("耗材档案加载失败，请刷新后重试"); })
      .finally(() => { if (!cancelled) setUsageLoading(false); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId]);

  useEffect(() => {
    let active = true;
    warehousesApi.list(false)
      .then((rows) => { if (active) setWarehouses(rows.filter((row) => row.status === "active")); })
      .catch(() => { if (active) setMainError("仓库配置加载失败，请刷新后重试"); });
    return () => { active = false; };
  }, [detail?.order.orderId]);

  // 详情数据先为空、后加载完成时也必须保持 Hook 顺序不变，否则 React 会报 Hook 顺序错误。
  const inboundCount = detail?.detail.inbound.length ?? 0;
  useEffect(() => {
    const query = inboundQuery.trim().toLowerCase();
    if (!inboundEditorOpen || !query || inboundCandidateLoading || editorBusy || inboundCount > 0) return;
    const exactMatches = inboundCandidates.filter((candidate) => candidate.targetNo.trim().toLowerCase() === query);
    if (exactMatches.length !== 1 || autoLinkedInboundQuery.current === query) return;
    const exact = exactMatches[0];
    autoLinkedInboundQuery.current = query;
    setSelectedInboundId(exact.targetId);
    setInboundNoEntry(exact.targetNo);
    setInboundEditorOpen(false);
    void saveInboundLink(exact.targetId);
  }, [editorBusy, inboundCandidateLoading, inboundCandidates, inboundCount, inboundEditorOpen, inboundQuery]);

  // 保存明细后，如果订单已经满足完整条件，自动落库为“已确认采购内容”。
  // 之前只刷新了详情，没有调用 refine，导致金额已平衡的订单仍长期显示“待完善”。
  const autoRefineAttempted = useRef("");
  useEffect(() => {
    const current = detail;
    if (loading || !current?.order.externalPoId || current.order.purchaseStatus !== "pending_refine") return;
    const allocations = current.detail.allocations ?? [];
    const inbound = current.detail.inbound as InboundRow[];
    const unallocated = current.detail.unallocatedAmount;
    if (
      allocations.length === 0
      || unallocated == null
      || Math.abs(Number(unallocated)) >= 0.005
    ) return;
    const attemptKey = `${current.order.externalPoId}:${allocations.length}:${unallocated}:${inbound.map((row) => `${row.linkId}-${row.consumableUsageDecided}-${row.consumableUsageEnabled}`).join(",")}`;
    if (autoRefineAttempted.current === attemptKey) return;
    autoRefineAttempted.current = attemptKey;
    void authenticatedFetch(`/api/v1/purchase/orders/${current.order.externalPoId}/refine`, { method: "POST" })
      .then(async (res) => {
        if (res.ok) await onOrderChanged();
      })
      .catch(() => {
        // 自动确认失败时保留“待完善”，由页面现有校验提示用户具体处理项。
      });
  }, [detail, loading, onOrderChanged]);

  if (loading && !detail) return <aside className="rounded-xl border border-slate-200 bg-white"><Loading text="正在加载订单详情…" /></aside>;
  if (!detail) return <aside className="flex min-h-[520px] items-center justify-center rounded-xl border border-slate-200 bg-white px-8 text-center text-[12px] text-slate-400">选择左侧订单查看执行详情</aside>;
  const { order, detail: records, stepStates } = detail;
  const isFile = order.fileOrderId != null;
  // 统计实际修改的字段数（与原始 order 对比）
  const allocations = records.allocations as AllocationRow[];
  // 耗材订单的明细由耗材采购单返回，不会落在 SKU allocations 中；展示层仍统一收口到同一张商品明细表。
  const detailConsumableItems = (((records.consumable as { items?: ConsumablePurchaseItem[] } | null)?.items ?? []) as ModalConsumableItem[]);
  const sourceOrderItems = (records.orderItems as Array<{
    productNumber?: string;
    productName?: string;
    skuId?: string;
    quantity?: number;
    unitPrice?: number;
    amount?: number;
    receivedQuantity?: number;
  }> | undefined) ?? [];
  // 某些历史耗材订单的详情接口只返回 orderItems，兜底映射成统一商品明细，避免页面误显示 0 条。
  const fetchedConsumableItems = consumablePurchaseRows.flatMap((purchase) => purchase.items.map((item) => ({ ...item, purchaseId: purchase.id })));
  const consumableItems: ModalConsumableItem[] = fetchedConsumableItems.length > 0
    ? fetchedConsumableItems
    : detailConsumableItems.length > 0
      ? detailConsumableItems
      : order.orderKind === "consumable"
        ? sourceOrderItems.map((item, index) => ({
            id: index,
            consumableId: 0,
            code: item.productNumber || item.skuId || "",
            name: item.productName || "未命名耗材",
            unit: "",
            quantity: String(item.quantity ?? 0),
            receivedQty: String(item.receivedQuantity ?? 0),
            unitCost: String(item.unitPrice ?? (item.amount && item.quantity ? item.amount / item.quantity : 0)),
          }))
        : [];
  const displayRowCount = allocations.length > 0 ? allocations.length : consumableItems.length;
  const displayTotal = allocations.length > 0
    ? allocations.reduce((sum, row) => sum + (row.amount ?? 0), 0)
    : consumableItems.reduce((sum, item) => sum + Number(item.quantity || 0) * Number(item.unitCost || 0), 0);
  const purchaseAmount = order.amount ?? 0;
  const detailAmountGap = purchaseAmount - displayTotal;
  const hasDetailAmountGap = displayRowCount > 0 && Math.abs(detailAmountGap) >= 0.005;
  const expenses = records.expenses as ExpenseRow[];
  const inbound = records.inbound as InboundRow[];
  // SKU 分配归属：后端返回结构化 inboundDocumentId 时直接采用；旧版后端没有该字段时
  // 退化为解析 note（"由入库单 #N 明细自动反填"），保证后端未同步时分组依然正确。
  const docIdOf = (row: AllocationRow): number | null => {
    if (row.inboundDocumentId != null) return row.inboundDocumentId;
    const matched = /由入库单\s*#(\d+)/.exec(row.note ?? "");
    return matched ? Number(matched[1]) : null;
  };
  const linkedDocIds = new Set(inbound.map((row) => row.documentId).filter((id): id is number => id != null));
  const allocationsByDoc = new Map<number, AllocationRow[]>();
  const manualAllocations: AllocationRow[] = [];
  for (const row of allocations) {
    // 归属单据必须仍在本单已关联的入库单内，避免解除关联后残留空分组
    const docId = docIdOf(row);
    if (docId != null && linkedDocIds.has(docId)) {
      const list = allocationsByDoc.get(docId) ?? [];
      list.push(row);
      allocationsByDoc.set(docId, list);
    } else {
      manualAllocations.push(row);
    }
  }
  const manualTotal = manualAllocations.reduce((sum, row) => sum + (row.amount ?? 0), 0);
  const settlements = records.settlement as SettlementRow[];
  const extraAmount = expenses.reduce((sum, row) => sum + (typeof row.amount === "number" ? row.amount : 0), 0);
  const settlementPaid = settlements.reduce((sum, row) => {
    const amount = row.paidAmount;
    return sum + (typeof amount === "number" ? amount : 0);
  }, 0);
  // 原单总额已含运费优惠，分摊费用不再次加到应付；未知付款不算已付。
  const payable = order.paidAmount ?? order.amount ?? 0;
  const platformPaid = order.paidOn1688
    ? (order.paidAmount ?? order.amount ?? 0)
    : order.platform !== "1688" && (order.paidAmount ?? 0) > 0
      ? order.paidAmount ?? 0
      : 0;
  const paidAmount = Math.min(payable, Math.max(settlementPaid, platformPaid));
  const unpaidAmount = Math.max(0, payable - paidAmount);
  const isPaid = order.paidOn1688 || paidAmount > 0;
  const remainingAmount = records.unallocatedAmount ?? order.paidAmount ?? order.amount;
  // 1688 源单已关闭：整单只读，不允许再确认采购内容或修改分配。
  const closed = isOrderClosed(order.orderStatus);
  const editable = !closed && (!order.purchaseStatus || order.purchaseStatus === "pending_refine");
  const detailStatusLabel = statusLabel({ orderStatus: order.orderStatus, stepStates, closeoutStage: order.closeoutStage });
  // 详情页状态必须跟工作台步骤状态一致；旧 purchaseStatus 可能仍停在 jackyun_linked，不能再渲染过时的流转按钮。
  const operationalComplete = stepStates.closeout?.done === true || order.purchaseStatus === "done";
  // 有真实入库事实后，状态机进入收尾阶段；不能再显示“回到生产中”的旧状态按钮。
  const hasActualInbound = inbound.length > 0;
  const nextStatus = !operationalComplete && !hasActualInbound && order.purchaseStatus
    ? NEXT_STATUS[order.purchaseStatus]
    : undefined;
  // 详情页默认全可修改；确认按钮仅作为手动推进快捷入口（SKU 分配保存后也会自动静默尝试确认）
  const canConfirm = Boolean(
    order.externalPoId && editable && allocations.length > 0 &&
    remainingAmount !== null && remainingAmount !== undefined && Math.abs(remainingAmount) < 0.005
  );
  const confirmBlockReason = !canConfirm
    ? (
      !order.externalPoId ? "缺少平台采购记录 ID"
      : closed ? `订单已关闭（1688 源单：${order.orderStatus || "已关闭"}），不可再确认`
      : !editable ? "当前状态不可确认"
      : allocations.length === 0 ? "尚未完成 SKU 分配"
      : order.paidAmount == null && remainingAmount !== null && remainingAmount < -0.005
        ? `实付金额未登记：分配合计 ${fmtMoney(Math.abs(remainingAmount))}，请在「金额与付款分层」的「1688微调」补录实付后再确认`
      : (remainingAmount !== null && Math.abs(remainingAmount) > 0.005)
        ? (remainingAmount < 0 ? "超分配" : "待分配") + ` ${fmtMoney(Math.abs(remainingAmount))}：请在 SKU 分配区把金额调平`
        : "暂不可确认"
    )
    : "";
  const selectedSku = skuCatalog.find((sku) => sku.id === selectedSkuId) ?? null;
  const selectableSkuCatalog = skuCatalog.filter((sku) => sku.productType === "single" && sku.status === "active");
  const normalizedInboundQuery = inboundQuery.trim().toLowerCase();
  const matchingInboundCandidates = inboundCandidates.filter((candidate) => {
    if (!normalizedInboundQuery) return true;
    return `${candidate.targetNo} ${candidate.targetSupplier} ${candidate.warehouseName}`.toLowerCase().includes(normalizedInboundQuery);
  }).slice(0, 30);
  const selectedInbound = inboundCandidates.find((candidate) => candidate.targetId === selectedInboundId) ?? null;
  const editorInboundDoc = skuEditorTargetDoc == null
    ? null
    : inbound.find((row) => row.documentId === skuEditorTargetDoc) ?? null;
  const editorConsumableDisabled = editorInboundDoc?.consumableUsageEnabled === false;
  const editorConsumable = skuConsumableId == null
    ? null
    : usageMaterials.find((material) => material.id === skuConsumableId) ?? null;

  function mappedConsumableQty(quantity: string, mapping?: ConsumableMappingRow) {
    if (!mapping) return "";
    const value = Number(quantity) * Number(mapping.usagePerUnit || 0);
    return Number.isFinite(value) && value > 0 ? String(Number(value.toFixed(3))) : "1";
  }

  function seedSkuConsumable(skuId: number | null, quantity: string, existing?: EditorConsumableDraft | null) {
    const exempt = skuCatalog.find((item) => item.id === skuId)?.consumablePolicy === "none";
    setSkuConsumableExempt(exempt);
    if (exempt) {
      setSkuConsumableId(null);
      setSkuConsumableEntry("");
      setSkuConsumableQty("");
      setSkuConsumableAutoQty(false);
      setSkuConsumableTouched(false);
      return;
    }
    if (existing) {
      setSkuConsumableId(existing.consumableId);
      const material = usageMaterials.find((item) => item.id === existing.consumableId);
      setSkuConsumableEntry(material ? `${material.code} · ${material.name}` : String(existing.consumableId));
      setSkuConsumableQty(existing.quantity);
      setSkuConsumableAutoQty(false);
    } else {
      const mapping = skuId == null ? undefined : usageMappings.find((item) => item.skuId === skuId);
      setSkuConsumableId(mapping?.consumableId ?? null);
      const material = mapping ? usageMaterials.find((item) => item.id === mapping.consumableId) : null;
      setSkuConsumableEntry(material ? `${material.code} · ${material.name}` : "");
      setSkuConsumableQty(mappedConsumableQty(quantity, mapping));
      setSkuConsumableAutoQty(Boolean(mapping));
    }
    setSkuConsumableTouched(false);
  }

  function resetSkuConsumable() {
    setSkuConsumableId(null);
    setSkuConsumableEntry("");
    setSkuConsumableQty("");
    setSkuConsumableAutoQty(false);
    setSkuConsumableExempt(false);
    setSkuConsumableTouched(false);
  }

  const skuListId = `purchase-sku-catalog-${order.orderId}`;
  const consumableListId = `purchase-consumable-catalog-${order.orderId}`;
  const skuLabel = (sku: CatalogSkuRow) => `${sku.skuCode} · ${sku.goodsName || sku.skuName || "未命名商品"}`;
  const consumableLabel = (material: ConsumableRow) => `${material.code} · ${material.name}`;

  function applySkuSelection(sku: CatalogSkuRow) {
    setSelectedSkuId(sku.id);
    setSkuEntry(skuLabel(sku));
    const exempt = sku.consumablePolicy === "none";
    setSkuConsumableExempt(exempt);
    if (exempt) {
      resetSkuConsumable();
      setSkuConsumableExempt(true);
    }
    const mapping = usageMappings.find((item) => item.skuId === sku.id);
    setSkuConsumableId(exempt ? null : mapping?.consumableId ?? null);
    const material = mapping ? usageMaterials.find((item) => item.id === mapping.consumableId) : null;
    setSkuConsumableEntry(exempt ? "" : material ? consumableLabel(material) : "");
    setSkuConsumableQty(exempt ? "" : mappedConsumableQty(skuQty, mapping));
    setSkuConsumableAutoQty(!exempt && Boolean(mapping));
    setSkuConsumableTouched(true);
    // 入库前仅用货品档案成本作预估；实际入库反填后以入库明细含税单价为准。
    const defaultCost = Number(sku.defaultCost);
    setSkuTotal(Number.isFinite(defaultCost) && defaultCost >= 0 ? String(Number((defaultCost * Number(skuQty || 0)).toFixed(4))) : "");
  }

  function handleSkuEntry(value: string) {
    const normalized = value.trim().toLowerCase();
    setSkuEntry(value);
    if (!normalized) {
      setSelectedSkuId(null);
      setSkuTotal("");
      resetSkuConsumable();
      return;
    }
    const sku = selectableSkuCatalog.find((item) => [item.skuCode, item.jackyunSkuId, item.barcode, item.skuName, item.goodsName, skuLabel(item)]
      .some((candidate) => candidate?.trim().toLowerCase() === normalized));
    if (sku) applySkuSelection(sku);
    else {
      setSelectedSkuId(null);
      setSkuTotal("");
      resetSkuConsumable();
    }
  }

  function handleConsumableEntry(value: string) {
    const normalized = value.trim().toLowerCase();
    setSkuConsumableEntry(value);
    setSkuConsumableTouched(true);
    if (normalized === "不使用" || normalized === "无需耗材") {
      void updateSelectedSkuConsumablePolicy("none");
      return;
    }
    if (!normalized) {
      setSkuConsumableId(null);
      setSkuConsumableQty("");
      setSkuConsumableAutoQty(false);
      return;
    }
    const material = usageMaterials.find((item) => [item.code, item.name, consumableLabel(item)]
      .some((candidate) => candidate.trim().toLowerCase() === normalized));
    if (!material) {
      setSkuConsumableId(null);
      setSkuConsumableQty("");
      setSkuConsumableAutoQty(false);
      return;
    }
    setSkuConsumableId(material.id);
    setSkuConsumableEntry(consumableLabel(material));
    const mapping = usageMappings.find((item) => item.skuId === selectedSkuId && item.consumableId === material.id);
    setSkuConsumableQty(mapping ? mappedConsumableQty(skuQty, mapping) : "1");
    setSkuConsumableAutoQty(Boolean(mapping));
  }

  async function updateSelectedSkuConsumablePolicy(policy: "auto" | "none") {
    if (!selectedSku || editorBusy || skuAction) return;
    setEditorBusy(true);
    setSkuMessage("");
    try {
      await dashboardApi.updateConsumablePolicy(selectedSku.id, policy);
      setSkuCatalog((current) => current.map((row) => row.id === selectedSku.id ? { ...row, consumablePolicy: policy } : row));
      if (policy === "none") {
        setSkuConsumableExempt(true);
        setSkuConsumableId(null);
        setSkuConsumableEntry("");
        setSkuConsumableQty("");
        setSkuConsumableAutoQty(false);
        setSkuConsumableTouched(true);
        setSkuMessage("已设置为不使用耗材；保存明细后将显示“无需耗材”，不会扣减耗材库存");
      } else {
        setSkuConsumableExempt(false);
        seedSkuConsumable(selectedSku.id, skuQty);
        setSkuMessage("已恢复该货品的耗材自动匹配");
      }
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "耗材规则保存失败");
    } finally {
      setEditorBusy(false);
    }
  }

  function startConsumableEdit(item: ModalConsumableItem) {
    if (item.purchaseId == null) {
      setSkuMessage("该耗材明细缺少本地采购单 ID，暂不能在当前弹窗修改");
      return;
    }
    setEditingConsumable(item);
    setConsumableEditQty(item.quantity);
    setConsumableEditTotal((Number(item.quantity) * Number(item.unitCost)).toFixed(4));
    setSkuMessage("");
  }

  function cancelConsumableEdit() {
    setEditingConsumable(null);
    setConsumableEditQty("");
    setConsumableEditTotal("");
  }

  async function saveConsumableEdit() {
    const editing = editingConsumable;
    if (consumableEditBusy || !editing || editing.purchaseId == null) return;
    const quantity = Number(consumableEditQty);
    const total = Number(consumableEditTotal);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      setSkuMessage("耗材数量必须大于 0");
      return;
    }
    const receivedQty = Number(editing.receivedQty || 0);
    if (receivedQty > 0 && quantity < receivedQty) {
      setSkuMessage(`耗材数量不能小于已入库数量 ${fmtQty(receivedQty)}`);
      return;
    }
    if (!Number.isFinite(total) || total < 0) {
      setSkuMessage("耗材总价不能为负");
      return;
    }
    const purchase = consumablePurchaseRows.find((row) => row.id === editing.purchaseId);
    if (!purchase) {
      setSkuMessage("耗材采购单明细尚未加载完成，请稍后重试");
      return;
    }
    const unitCost = total / quantity;
    const items = purchase.items.map((item) => item.id === editing.id
      ? { consumable_id: item.consumableId, quantity: String(quantity), unit_cost: String(unitCost) }
      : { consumable_id: item.consumableId, quantity: item.quantity, unit_cost: item.unitCost });
    setConsumableEditBusy(true);
    setSkuMessage("");
    try {
      await consumablesApi.updatePurchase(purchase.id, { items });
      setConsumablePurchaseRows((current) => current.map((row) => row.id === purchase.id
        ? { ...row, items: row.items.map((item) => item.id === editing.id ? { ...item, quantity: String(quantity), unitCost: String(unitCost) } : item) }
        : row));
      cancelConsumableEdit();
      setSkuMessage(`已更新 ${editing.name}，单价已按总价 ÷ 数量自动计算`);
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "耗材明细保存失败");
    } finally {
      setConsumableEditBusy(false);
    }
  }

  // 商品明细表内的添加/修改行：用户填写数量和总价，单价由系统自动计算。
  const skuEditorRow = (
    <tr className="whitespace-nowrap border-t border-indigo-200 bg-indigo-50/60">
      <td className="py-1.5 pl-3 pr-2"><OrderKindTag value={order.orderKind} /></td>
      <td className="py-1.5 pr-2 font-mono text-[11px] text-slate-500">{selectedSku?.barcode || "—"}</td>
      <td className="py-1.5 pr-2">
        {skuCatalogLoading ? (
          <div className="flex h-8 items-center gap-1.5 rounded border border-indigo-200 bg-white px-2 text-[11px] text-slate-400">正在读取 SKU 主档…</div>
        ) : (
          <div className="flex min-w-0 items-center gap-1.5">
            <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold", editingAllocation ? "bg-amber-100 text-amber-700" : "bg-indigo-100 text-indigo-700")}>{editingAllocation ? "编辑" : "新建"}</span>
            <div className="min-w-0 flex-1">
              <input
                list={skuListId}
                value={skuEntry}
                disabled={skuCatalogLoading}
                onChange={(event) => handleSkuEntry(event.target.value)}
                placeholder={skuCatalogLoading ? "正在读取 SKU 主档…" : "输入 SKU 编码或名称，可快速选择"}
                aria-label="SKU 明细"
                className="h-8 w-full min-w-0 rounded border border-indigo-300 bg-white px-1.5 text-[11px] outline-none focus:border-indigo-500 disabled:bg-slate-50"
              />
              <datalist id={skuListId}>
                {selectableSkuCatalog.map((sku) => <option key={sku.id} value={skuLabel(sku)} />)}
              </datalist>
            </div>
          </div>
        )}
      </td>
      <td className="py-1.5 px-1 text-right">
        <input
          type="number" min="0.0001" step="0.0001" aria-label="数量" value={skuQty}
          onChange={(event) => {
            const value = event.target.value;
            setSkuQty(value);
            if (skuConsumableAutoQty && skuConsumableId != null) {
              setSkuConsumableQty(mappedConsumableQty(value, usageMappings.find((item) => item.skuId === selectedSkuId)));
            }
          }}
          className="h-8 w-full max-w-[96px] rounded border border-slate-200 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-300"
        />
      </td>
      <td className="py-1.5 px-1 text-right tabular-nums font-medium text-slate-700">{fmtMoney(Number(skuQty) > 0 && skuTotal !== "" ? Number(skuTotal) / Number(skuQty) : null)}</td>
      <td className="py-1.5 px-1 text-right">
        <input
          type="number" min="0" step="0.0001" aria-label="总价" value={skuTotal}
          onChange={(event) => setSkuTotal(event.target.value)}
          className="h-8 w-full max-w-[104px] rounded border border-slate-200 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-300"
        />
      </td>
      <td className="py-1.5 px-1">
        {order.orderKind === "consumable" || editorConsumableDisabled ? (
          <span className="text-[11px] text-slate-400">{editorConsumableDisabled ? "本次不使用" : "—"}</span>
        ) : skuConsumableExempt ? (
          <div className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="rounded bg-slate-100 px-1.5 py-1 text-[11px] font-medium text-slate-500">无需耗材</span>
            <button type="button" disabled={editorBusy} onClick={() => void updateSelectedSkuConsumablePolicy("auto")} className="text-[10px] text-indigo-600 hover:text-indigo-800 disabled:opacity-40">恢复自动匹配</button>
          </div>
        ) : (
          <div className="flex items-center gap-1">
            <input
              list={consumableListId}
              aria-label="耗材"
              value={skuConsumableEntry}
              disabled={usageLoading || usageMaterials.length === 0}
              onChange={(event) => handleConsumableEntry(event.target.value)}
              placeholder={usageLoading ? "正在读取耗材…" : "输入耗材编码或名称"}
              className="h-6 min-w-0 flex-1 rounded border border-indigo-200 bg-white px-1 text-[11px] outline-none focus:border-indigo-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <datalist id={consumableListId}>
              <option value="不使用" />
              {usageMaterials.map((material) => <option key={material.id} value={consumableLabel(material)} />)}
            </datalist>
            <input
              aria-label="耗材用量"
              type="number"
              min="0.0001"
              step="0.0001"
              value={skuConsumableQty}
              disabled={!skuConsumableId}
              onChange={(event) => { setSkuConsumableTouched(true); setSkuConsumableAutoQty(false); setSkuConsumableQty(event.target.value); }}
              placeholder="用量"
              className="h-6 w-12 shrink-0 rounded border border-slate-200 px-1 text-center text-[11px] tabular-nums outline-none focus:border-amber-400 disabled:bg-slate-50"
            />
            <span className="w-6 shrink-0 text-[11px] text-slate-400">{editorConsumable?.unit ?? "—"}</span>
            <button type="button" disabled={!selectedSku || editorBusy} onClick={() => void updateSelectedSkuConsumablePolicy("none")} className="shrink-0 rounded border border-slate-200 bg-white px-1.5 py-1 text-[10px] text-slate-500 hover:border-indigo-300 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-40">不使用</button>
          </div>
        )}
      </td>
      <td className="py-1.5 px-1 text-center tabular-nums text-[11px] text-slate-500">{skuConsumableId ? skuConsumableQty || "—" : "—"}</td>
      <td className={cx("py-1.5 px-1 text-center text-[11px]", skuConsumableExempt ? "text-slate-400" : "text-amber-600")}>{order.orderKind === "consumable" || skuConsumableExempt ? "—" : skuConsumableId ? "待计算" : "未匹配"}</td>
      <td className="py-1.5 pl-2">
        <div className="flex justify-end gap-1.5">
          <button
            onClick={() => { setSkuEditorOpen(false); setSkuEditorTargetDoc(null); setEditingAllocation(null); resetSkuConsumable(); }}
            className="text-[11px] text-slate-400 hover:text-slate-600"
          >取消</button>
          <button
            disabled={skuAction !== "" || editorBusy || !selectedSku || !skuQty || skuTotal === "" || (!skuConsumableExempt && skuConsumableId != null && (!skuConsumableQty || Number(skuConsumableQty) <= 0))}
            onClick={addSkuAllocation}
            className="rounded bg-indigo-600 px-2 py-1 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {skuAction === "add" ? "保存中…" : editingAllocation ? "保存修改" : skuEditorTargetDoc != null ? "保存到本单据" : "保存到采购内容"}
          </button>
        </div>
      </td>
    </tr>
  );

  async function readMutation(res: Response) {
    const body = await res.json().catch(() => ({})) as { detail?: string; id?: number };
    if (!res.ok) throw new Error(body.detail || `操作失败（${res.status}）`);
    return body;
  }

  async function ensureExternalPoId() {
    if (order.externalPoId) return order.externalPoId;
    const res = await authenticatedFetch("/api/v1/purchase/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_order_id: order.orderNo,
        platform: channelOf(order.platform),
        supplier_name: order.supplier || "",
        title: order.title || "",
        ordered_at: order.orderDate,
        order_amount: order.amount === null ? null : String(order.amount),
        paid_amount: (order.paidAmount ?? order.amount) === null ? null : String(order.paidAmount ?? order.amount),
      }),
    });
    const body = await readMutation(res);
    if (!body.id) throw new Error("采购工作流创建成功，但未返回采购单 ID");
    return body.id;
  }

  async function addSkuAllocation() {
    if (!selectedSku || !skuQty || skuTotal === "") return;
    const quantity = Number(skuQty);
    const total = Number(skuTotal);
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(total) || total < 0) {
      setSkuMessage("请填写有效的数量和总价");
      return;
    }
    const consumableQuantity = Number(skuConsumableQty);
    if (skuEditorTargetDoc != null && skuConsumableId != null && (!Number.isFinite(consumableQuantity) || consumableQuantity <= 0)) {
      setSkuMessage("请填写有效的耗材用量");
      return;
    }
    setSkuAction("add");
    setSkuMessage("");
    const targetDocId = skuEditorTargetDoc;
    const wasEditing = editingAllocation != null;
    const consumableTouched = skuConsumableTouched;
    try {
      const poId = await ensureExternalPoId();
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${poId}/allocations${editingAllocation ? `/${editingAllocation}` : ""}`, {
        method: editingAllocation ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sku_id: selectedSku.id,
          sku_code: selectedSku.skuCode,
          goods_name: selectedSku.goodsName || selectedSku.skuName,
          quantity: skuQty,
          amount: skuTotal,
          note: targetDocId != null ? `由入库单 #${targetDocId} 明细自动反填` : "",
        }),
      });
      const body = await readMutation(res);
      if (targetDocId != null && body.id && (!wasEditing || consumableTouched)) {
        setPendingConsumableUpdate({
          documentId: targetDocId,
          allocationId: body.id,
          draft: skuConsumableId == null || editorConsumableDisabled
            ? null
            : { consumableId: skuConsumableId, quantity: skuConsumableQty },
        });
      }
      setSelectedSkuId(null);
      setSkuEntry("");
      setSkuTotal("");
      resetSkuConsumable();
      setSkuMessage(wasEditing
        ? "SKU 数量/总价已更新，单价已自动计算；若该行原为入库单反填，单据明细与金额已同步更正"
        : (targetDocId != null ? `SKU 已添加到入库单 #${targetDocId}` : "SKU 已加入当前订单"));
      setEditingAllocation(null);
      if (wasEditing) {
        // 编辑保存：就地面板收起
        setSkuEditorTargetDoc(null);
        setSkuEditorOpen(false);
      } else if (targetDocId == null) {
        setSkuEditorTargetDoc(null);
      }
      // 卡片内新增：面板留在卡片内保持打开，选择已清空，可连续添加
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "SKU 分配失败");
    } finally {
      setSkuAction("");
    }
  }

  async function confirmPurchaseContent() {
    if (!order.externalPoId || !canConfirm) return;
    setSkuAction("confirm");
    setSkuMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/confirm-and-resync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "工作台总确认" }),
      });
      const body = await readMutation(res) as { purchaseStatus?: string; resync?: { ok: boolean | null; attempted: boolean; error?: string; stats?: { stockinOrders?: number; stockinItems?: number } } };
      const r = body.resync;
      const parts = ["采购内容已确认，订单已进入待入库阶段"];
      if (r?.attempted) {
        if (r.ok) parts.push(`已尝试回写本地：入库单 ${r.stats?.stockinOrders ?? 0} 张 / 明细 ${r.stats?.stockinItems ?? 0} 行`);
        else parts.push(`本地回写未生效：${r.error ?? "未知原因"}`);
      }
      setSkuMessage(parts.join(" · "));
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "采购内容确认失败");
    } finally {
      setSkuAction("");
    }
  }

  async function deleteOrder() {
    const recoverable = order.orderId > 0;
    const tip = recoverable
      ? "这是一张 1688 导入订单，将执行软删除：仅从工作台隐藏（row_status=deleted），数据仍可恢复。"
      : "这是一张手工登记的订单（没有 1688 原件），将直接删除记录本体及其分配/费用，不可恢复。";
    if (!window.confirm(`确认删除订单 ${order.orderNo}？\n\n${tip}`)) return;
    setDeleteBusy(true);
    setSkuMessage("");
    try {
      const result = await procurementWorkbenchApi.softDeleteOrder(order.orderId);
      onOrderDeleted(result.recoverable
        ? `订单 ${result.orderNo || order.orderNo} 已从工作台移除（软删除，可恢复）`
        : `订单 ${result.orderNo || order.orderNo} 已删除`);
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "删除订单失败");
    } finally {
      setDeleteBusy(false);
    }
  }

  async function saveMainEdit(event?: React.FormEvent) {
    if (event) event.preventDefault();
    if (mainSaving) return;
    const warehouseId = mainForm.warehouseId.trim() ? Number(mainForm.warehouseId) : null;
    setMainSaving(true);
    setMainError("");
    try {
      if (!mainForm.supplier.trim()) throw new Error("供应商不能为空");
      if (warehouseId == null || !Number.isInteger(warehouseId)) throw new Error("请选择采购入库仓库");
      const purchaseAmount = Number(mainForm.orderAmount);
      if (!mainForm.orderAmount.trim() || !Number.isFinite(purchaseAmount) || purchaseAmount < 0) {
        throw new Error("请输入有效的采购金额");
      }
      await procurementWorkbenchApi.editOrderMain(order.orderId, isFile ? {
        supplier_name: mainForm.supplier,
        title: mainForm.title,
        goods_total: mainForm.goods.trim() || undefined,
        freight: mainForm.freight.trim() || undefined,
        discount: mainForm.discount.trim() || undefined,
        actual_payment: mainForm.orderAmount,
        warehouse_id: warehouseId,
      } : {
        external_order_id: mainForm.orderNo.trim(),
        supplier_name: mainForm.supplier,
        title: mainForm.title,
        ordered_at: mainForm.date || undefined,
        order_amount: mainForm.orderAmount,
        paid_amount: mainForm.paid,
        platform: mainForm.platform,
        warehouse_id: warehouseId,
      });

      const requestedInboundNo = inboundNoEntry.trim();
      const createInboundFromAllocations = (inboundNo?: string) => {
        if (allocations.length === 0) throw new Error("请先添加商品明细，系统才能生成入库单");
        const items = allocations.filter((row) => row.id != null && row.skuId != null && Number(row.quantity) > 0);
        if (items.length !== allocations.length) throw new Error("存在未绑定 SKU 的明细，请先完成商品匹配");
        return procurementChainApi.createLocalInbound({
          order_id: order.orderId,
          inbound_no: inboundNo,
          inbound_at: mainForm.date ? `${mainForm.date}T00:00:00` : null,
          warehouse_id: warehouseId,
          note: inboundNo ? "按采购订单填写的入库单号创建" : "采购订单保存时自动生成入库单",
          items: items.map((row) => ({ allocation_id: row.id!, quantity: String(row.quantity), unit_price: row.unitPrice == null ? null : String(row.unitPrice) })),
        });
      };
      if (requestedInboundNo && !inbound.some((row) => row.goodsdocNo === requestedInboundNo)) {
        const result = await procurementChainApi.candidates(order.orderId, "inbound", requestedInboundNo, 30);
        const exact = result.items.find((row) => row.targetNo.trim().toLowerCase() === requestedInboundNo.toLowerCase());
        if (inbound.length > 0) throw new Error(`一张采购订单只能关联一个入库单，当前已有 ${inbound[0].goodsdocNo || "入库单"}`);
        if (exact) {
          setSelectedInboundId(exact.targetId);
          await saveInboundLink(exact.targetId);
        } else {
          const created = await createInboundFromAllocations(requestedInboundNo);
          setInboundNoEntry(created.inboundNo);
          setInboundMessage(`未找到旧入库单，已按填写的编号创建：${created.inboundNo}`);
        }
      } else if (!requestedInboundNo && inbound.length === 0) {
        const created = await createInboundFromAllocations();
        setInboundNoEntry(created.inboundNo);
      }
      await onOrderChanged();
    } catch (caught) {
      setMainError(caught instanceof Error ? caught.message : "订单主档保存失败");
    } finally {
      setMainSaving(false);
    }
  }

  async function linkJackyunPo() {
    const poId = await ensureExternalPoId();
    const no = poLinkNo.trim();
    if (!no) { setPoMessage("请输入要关联的吉客云采购单号"); return; }
    const alloc = poAllocAmount.trim() === "" ? null : poAllocAmount;
    setPoBusy(true);
    setPoMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${poId}/jackyun-link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purch_no: no, relation_kind: poRelationKind, alloc_amount: alloc }),
      }));
      setPoEditorOpen(false);
      setPoLinkNo("");
      setPoRelationKind("");
      setPoAllocAmount("");
      setPoMessage("采购单已关联");
      await onOrderChanged();
    } catch (caught) {
      setPoMessage(caught instanceof Error ? caught.message : "关联采购单失败");
    } finally {
      setPoBusy(false);
    }
  }

  async function unlinkJackyunPo(linkId: number) {
    const poId = order.externalPoId;
    if (!poId || !window.confirm("确认解除这张吉客云采购单与当前订单的关联？")) return;
    setPoBusy(true);
    setPoMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${poId}/jackyun-links/${linkId}`, { method: "DELETE" }));
      setPoMessage("已解除采购单关联");
      await onOrderChanged();
    } catch (caught) {
      setPoMessage(caught instanceof Error ? caught.message : "解除关联失败");
    } finally {
      setPoBusy(false);
    }
  }

  async function reopenPurchase() {
    if (!order.externalPoId || !window.confirm("重新编辑后采购内容需再次确认，原始采购单、入库和发票关联保留。是否继续？")) return;
    setEditorBusy(true);
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/reopen`, { method: "POST" }));
      await onOrderChanged();
      setSkuEditorTargetDoc(null);
      setSkuEditorOpen(true);
      setSkuMessage("已重新打开采购内容，可修改 SKU、数量、单价和费用");
    } catch (caught) { setSkuMessage(caught instanceof Error ? caught.message : "重新编辑失败"); }
    finally { setEditorBusy(false); }
  }

  async function removeAllocation(allocationId: number) {
    if (!order.externalPoId) return;
    if (!window.confirm("确认删除这条 SKU 分配？删除后该明细将从入库单中移除，相关耗材台账会同步调整。")) return;
    setEditorBusy(true);
    setSkuMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/allocations/${allocationId}?po_id=${order.externalPoId}`, { method: "DELETE" });
      await readMutation(res);
      setSkuMessage("SKU 分配已删除");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function saveAdjustment() {
    if (!order.externalPoId) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/adjustment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adjustment_amount: adjValue, note: adjNote }),
      }));
      setAdjEditing(false);
      setAdjValue("");
      setAdjNote("");
      setExpenseMessage(adjValue.trim() === "" ? "1688 微调已清除" : "1688 微调已保存，分配平衡按 实付+微调 重算");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "微调保存失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function addExpense() {
    if (!expenseAmount) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      const poId = await ensureExternalPoId();
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${poId}/expenses`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expense_type: expenseType, amount: expenseAmount }),
      });
      await readMutation(res);
      setExpenseAmount("");
      setExpenseMessage("附加费用已登记");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "费用登记失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function removeExpense(expenseId: number) {
    if (!order.externalPoId) return;
    if (!window.confirm("确认删除这条附加费用？该费用将从本单费用分摊中移除。")) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/expenses/${expenseId}?po_id=${order.externalPoId}`, { method: "DELETE" });
      await readMutation(res);
      setExpenseMessage("附加费用已删除");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function advanceStatus() {
    if (!order.externalPoId || !nextStatus) return;
    if ((nextStatus === "inbound" || nextStatus === "done") && inbound.length === 0) {
      setSkuMessage("请先关联真实采购入库单");
      return;
    }
    setEditorBusy(true);
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      await readMutation(res);
      setSkuMessage("状态已流转到「" + (PO_STATUS[nextStatus] ?? nextStatus) + "」");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "状态流转失败");
    } finally {
      setEditorBusy(false);
    }
  }

  function openInboundPicker(linkId: number | null = null) {
    setReplaceInboundLinkId(linkId);
    setSelectedInboundId(null);
    setInboundQuery("");
    autoLinkedInboundQuery.current = "";
    setInboundMessage("");
    setInboundEditorOpen(true);
  }

  function selectInboundCandidate(candidate: ChainLinkCandidate) {
    setSelectedInboundId(candidate.targetId);
    setInboundNoEntry(candidate.targetNo);
    setInboundQuery(candidate.targetNo);
    setInboundEditorOpen(false);
    void saveInboundLink(candidate.targetId);
  }

  async function saveInboundLink(targetId: number | null = selectedInboundId) {
    setEditorBusy(true);
    setInboundMessage("");
    try {
      if (!targetId) return;
      if (inbound.length > 0 && !inbound.some((row) => row.targetId === targetId || row.documentId === targetId)) {
        throw new Error(`一张采购订单只能关联一个入库单，当前已有 ${inbound[0].goodsdocNo || "入库单"}`);
      }
      if (replaceInboundLinkId) {
        await procurementChainApi.replaceLink(replaceInboundLinkId, targetId, "采购工作台更换入库单，耗材按实际入库数量自动计算");
        setInboundMessage("入库单关联已更换，耗材将按实际入库数量自动计算");
      } else {
        await procurementChainApi.manualLink(order.orderId, "inbound", targetId, "采购工作台关联入库单，耗材按实际入库数量自动计算");
        setInboundMessage("入库单已关联，耗材将按实际入库数量自动计算");
      }
      setInboundEditorOpen(false);
      setSelectedInboundId(null);
      setReplaceInboundLinkId(null);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "入库单关联失败");
      setInboundEditorOpen(Boolean(inboundQuery.trim()));
    } finally {
      setEditorBusy(false);
    }
  }

  async function correctInboundAmount(documentId: number) {
    if (!documentId) return;
    setDocAmountBusy(true);
    setInboundMessage("");
    try {
      const result = await procurementChainApi.recalcInboundAmount(documentId);
      setInboundMessage(`已按明细合计更正入库单金额：${fmtMoney(Number(result.after))}`);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "按明细更正失败");
    } finally {
      setDocAmountBusy(false);
    }
  }

async function saveInboundAmount(documentId: number, value: string, note: string) {
    if (!documentId || !value) return;
    setDocAmountBusy(true);
    setInboundMessage("");
    try {
      const result = await procurementChainApi.correctInboundAmount(documentId, value, note);
      setInboundMessage(`入库单金额已更正为 ${fmtMoney(Number(result.amount))}`);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "更正金额失败");
    } finally {
      setDocAmountBusy(false);
    }
  }

  async function toggleOrderKind() {
    const next = order.orderKind === "consumable" ? "goods" : "consumable";
    const reason = next === "goods"
      ? "确认把该订单改为「正品」？\n\n当前判为耗材的原因：耗材档案 Excel 的「采购订货号」引用了本单号，或供应商名含包装类关键词。人工覆盖后不再受自动判定影响。"
      : "确认把该订单改为「耗材（包材）采购」？人工覆盖后不再受自动判定影响。";
    if (!window.confirm(reason)) return;
    setEditorBusy(true);
    setSkuMessage("");
    try {
      await procurementWorkbenchApi.setOrderKindOverride(order.orderId, next);
      setSkuMessage(next === "goods" ? "已人工改为「正品」" : "已人工改为「耗材」");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "订单类型更新失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function removeInboundLink(linkId: number) {
    if (!window.confirm(`确认解除这张入库单与当前${CHANNELS[channelOf(order.platform)].label}订单的关联？入库原始数据不会删除。`)) return;
    setEditorBusy(true);
    setInboundMessage("");
    try {
      await procurementChainApi.deleteLink(linkId);
      setInboundMessage("入库单关联已解除，原始入库单仍保留在数据库中");
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "解除关联失败");
    } finally {
      setEditorBusy(false);
    }
  }

  const cancelled = isOrderClosed(order.orderStatus);

  // 关闭弹窗：触发父组件卸载右侧详情面板
  function onClose() {
    onOrderChanged().catch(() => {});
    // 关掉详情 = 当前 Tab 交还给工作台列表（列表 Tab 已存在就切回去），地址栏与工作区保持一致
    if (typeof window !== "undefined") {
      const params = new URLSearchParams(panelSearchParams.toString());
      params.delete("order");
      syncWorkspaceUrl(panelWorkspace.retarget(`/purchase/workbench${params.toString() ? `?${params}` : ""}`));
    }
    onCloseDialog?.();
  }

  // 仓库名：实际入库仓库 > 计划仓库 > 订单上记录的仓库
  const actualWarehouseName = detail.warehouse?.warehouseName ?? order.warehouseName ?? "";
  const targetWarehouseName = detail.warehouse?.targetWarehouseName ?? "";

  // 发票匹配状态（由发票池自动处理，详情页只读）
  const invoiceStatusText = (() => {
    switch (order.invoiceStatus) {
      case "done": return "已开票";
      case "partial": return "部分开票";
      case "needs_review": return "发票需复核";
      case "pending": return "待开票";
      case "none": return "无票";
      default: return "待匹配";
    }
  })();
  const invoiceStatusClass = order.invoiceStatus === "done"
    ? "bg-emerald-50 text-emerald-700"
    : order.invoiceStatus === "needs_review"
      ? "bg-rose-50 text-rose-700"
      : order.invoiceStatus === "partial"
        ? "bg-amber-50 text-amber-700"
        : "bg-slate-100 text-slate-500";

  const invoiceOutstanding = order.invoiceOutstanding ?? 0;
  const invoicedAmount = order.invoicedAmount ?? 0;
  const orderAmount = order.amount ?? 0;
  const invoiceHasGap = invoiceOutstanding > 0 || invoicedAmount < orderAmount;
  const topInvoiceStatusText = order.invoiceStatus === "needs_review"
    ? "发票需复核"
    : order.invoiceStatus === "pending"
      ? "待供应商开票"
      : invoiceHasGap || order.invoiceStatus === "partial"
        ? "待开票"
        : invoiceStatusText;
  const topInvoiceStatusClass = order.invoiceStatus === "needs_review"
    ? "bg-rose-50 text-rose-700"
    : invoiceHasGap
      ? "bg-amber-50 text-amber-700"
      : invoiceStatusClass;
  const invoiceReviewTitle = (order.invoiceReviewReasons ?? []).join("；");
  const currentWarehouseName = actualWarehouseName || targetWarehouseName || order.warehouseName || "";
  const currentWarehouseId = detail.warehouse?.warehouseId ?? detail.warehouse?.targetWarehouseId ?? order.warehouseId ?? null;

  function allocationConsumables(row: AllocationRow) {
    if (order.orderKind === "consumable" || row.skuId == null) return [];
    if (skuCatalog.find((item) => item.id === row.skuId)?.consumablePolicy === "none") return [];
    return usageMappings
      .filter((mapping) => mapping.skuId === row.skuId)
      .map((mapping) => {
        const material = usageMaterials.find((item) => item.id === mapping.consumableId) ?? null;
        const requiredQty = Math.max(0, Number(row.quantity ?? 0) * Number(mapping.usagePerUnit || 0));
        const availableRaw = material ? Number(material.availableQty) : NaN;
        const availableQty = Number.isFinite(availableRaw) ? Math.max(0, availableRaw) : null;
        const usedQty = availableQty == null ? null : Math.min(requiredQty, availableQty);
        const shortageQty = availableQty == null ? null : Math.max(0, requiredQty - availableQty);
        const status = !material
          ? { label: "未匹配", className: "bg-orange-50 text-orange-600" }
          : shortageQty == null
            ? { label: "未匹配", className: "bg-orange-50 text-orange-600" }
            : shortageQty > 0
              ? { label: "缺 " + fmtQty(shortageQty), className: "bg-rose-50 text-rose-600" }
              : { label: "已匹配", className: "bg-emerald-50 text-emerald-600" };
        return { mapping, material, requiredQty, availableQty, usedQty, shortageQty, status };
      });
  }

  return (
    <aside className="flex min-h-0 min-w-0 flex-1 flex-col scroll-mt-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl bg-white shadow-[0_8px_32px_rgba(40,53,85,0.10)]">
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-5 py-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-[16px] font-semibold tracking-tight text-slate-900">
                采购订单详情 / 全量录入 <span className="font-mono text-[15px]">{order.orderNo}</span>
              </h2>
              <PlatformBadge value={order.platform} />
              {cancelled && <span className="rounded bg-slate-700 px-2 py-0.5 text-[11px] font-medium text-white">已关闭</span>}
            </div>
            <p className="mt-1 text-[11px] text-slate-400">一张采购订单对应一个供应商、一个仓库、一个入库单号和多条商品明细</p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <span className={cx("rounded-md px-2 py-1 text-[11px] font-medium", isPaid ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-700")}>{isPaid ? "已付款" : "待付款"}</span>
            <span className={cx("rounded-md px-2 py-1 text-[11px] font-medium", topInvoiceStatusClass)}>{topInvoiceStatusText}</span>
            {!closed && !editable && order.externalPoId && <button type="button" onClick={() => void reopenPurchase()} disabled={editorBusy} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-[11px] font-medium text-indigo-600 hover:border-indigo-300 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"><Icon name="edit" size={12} />{editorBusy ? "打开中…" : "编辑"}</button>}
            <button type="button" disabled title="当前后端没有独立草稿接口，保存会直接写入采购单" className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] text-slate-400">保存草稿</button>
            <button type="button" onClick={() => void saveMainEdit()} disabled={!editable || mainSaving} className="rounded-md bg-blue-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50">{mainSaving ? "保存中…" : "保存"}</button>
            <button type="button" onClick={onClose} aria-label="关闭" className="flex items-center justify-center rounded-md border border-slate-200 px-2.5 py-1.5 text-slate-600 hover:border-indigo-200 hover:text-indigo-600"><Icon name="x" size={15} /></button>
          </div>
        </header>

        {(mainError || inboundMessage || skuMessage) && (
          <div className={cx(
            "mx-4 mt-2 rounded-md border px-3 py-1.5 text-[11px]",
            mainError || inboundMessage?.includes("失败") || skuMessage?.includes("失败") || skuMessage?.includes("请")
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-emerald-200 bg-emerald-50 text-emerald-700"
          )}>
            {mainError || inboundMessage || skuMessage}
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto bg-[#f7f9fd] px-4 py-3">
          {cancelled && <div className="mb-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-1.5 text-[11px] text-slate-500">已关闭订单只读，不能修改采购参数或商品明细。</div>}

          <section className="rounded-lg border border-slate-200 bg-white">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2">
              <div>
                <h3 className="text-[13px] font-semibold text-slate-800">采购参数</h3>
                <p className="mt-0.5 text-[10px] text-slate-400">订单级信息只维护一次，商品明细不再重复填写仓库和入库单号</p>
              </div>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] text-indigo-600">一单一仓</span>
            </div>
            <div className="space-y-2 px-4 py-3">
              <div className="grid grid-cols-1 gap-2.5 md:grid-cols-4">
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[4.5em] shrink-0 text-[11px] text-slate-500">供应商<span className="ml-1 text-rose-500">*</span></label>
                  <input list={"purchase-supplier-" + order.orderId} value={mainForm.supplier} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, supplier: event.target.value })} className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-[12px] font-medium text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50" />
                  <datalist id={"purchase-supplier-" + order.orderId}>
                    {detail.supplierHistory?.recentOrders.map((item) => <option key={item.orderId} value={item.supplier || ""} />)}
                  </datalist>
                </div>
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[4.5em] shrink-0 text-[11px] text-slate-500">仓库<span className="ml-1 text-rose-500">*</span></label>
                  <select value={mainForm.warehouseId} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, warehouseId: event.target.value })} className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-[12px] font-medium text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50">
                    <option value="">请选择仓库</option>
                    {mainForm.warehouseId && !warehouses.some((item) => String(item.id) === mainForm.warehouseId) && <option value={mainForm.warehouseId}>{currentWarehouseName || "当前仓库"}</option>}
                    {warehouses.map((warehouse) => <option key={warehouse.id} value={String(warehouse.id)}>{warehouse.name}{warehouse.code ? " · " + warehouse.code : ""}</option>)}
                  </select>
                </div>
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[4.5em] shrink-0 text-[11px] text-slate-500">采购金额<span className="ml-1 text-rose-500">*</span></label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    inputMode="decimal"
                    value={mainForm.orderAmount}
                    disabled={!editable}
                    onChange={(event) => setMainForm({ ...mainForm, orderAmount: event.target.value })}
                    placeholder="请输入采购金额"
                    title="修改后将按新采购金额重新计算发票差额"
                    className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-right text-[12px] font-semibold tabular-nums text-slate-800 outline-none focus:border-indigo-400 disabled:bg-slate-50"
                  />
                </div>
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[4.5em] shrink-0 text-[11px] text-slate-500">采购时间<span className="ml-1 text-rose-500">*</span></label>
                  <input type="date" value={mainForm.date} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, date: event.target.value })} className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-[12px] text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50" />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-2.5 md:grid-cols-3">
                <div className="flex min-w-0 items-center gap-2"><label className="w-[4.5em] shrink-0 whitespace-nowrap text-[11px] text-slate-500">采购渠道</label><select value={mainForm.platform} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, platform: event.target.value })} className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 text-[12px] text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50"><option value="1688">1688</option><option value="pdd">拼多多</option><option value="taobao">淘宝 / 天猫</option><option value="other">其他</option></select></div>
                <div className="flex min-w-0 items-center gap-2"><label className="w-[4.5em] shrink-0 whitespace-nowrap text-[11px] text-slate-500">{isFile ? "订单号" : "采购单号"}</label><input value={mainForm.orderNo} readOnly={isFile} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, orderNo: event.target.value })} placeholder="请输入本系统采购单号" title={isFile ? "1688 原始订单号不可修改" : "本地新建采购单以此单号作为系统主单号"} className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 font-mono text-[11px] text-slate-600 outline-none focus:border-indigo-400 read-only:cursor-not-allowed read-only:bg-slate-50 disabled:bg-slate-50" /></div>
                <div className="flex min-w-0 items-center gap-2"><label className="w-[4.5em] shrink-0 whitespace-nowrap text-[11px] text-slate-500">付款状态</label><div className={cx("flex h-8 min-w-0 flex-1 items-center rounded-md border px-2 text-[12px] font-medium", isPaid ? "border-emerald-100 bg-emerald-50 text-emerald-700" : "border-amber-100 bg-amber-50 text-amber-700")}>{isPaid ? "已付款" : "待核对"}</div></div>
              </div>

              <div className="grid grid-cols-1 gap-2.5 md:grid-cols-[minmax(180px,1fr)_minmax(0,1.4fr)]">
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[5.5em] shrink-0 whitespace-nowrap text-[11px] text-slate-500">发票匹配状态</label>
                  <div
                    title={invoiceReviewTitle || "由发票池自动匹配，只读"}
                    className={cx(
                      "flex min-h-8 min-w-0 flex-1 items-center rounded-md border px-2 text-[11px] font-medium",
                      order.invoiceStatus === "needs_review"
                        ? "border-rose-100 bg-rose-50 text-rose-700"
                        : invoiceHasGap
                          ? "border-amber-100 bg-amber-50 text-amber-700"
                          : invoiceStatusClass,
                    )}
                  >
                    {order.invoiceStatus === "needs_review"
                      ? "需复核" + (invoiceReviewTitle ? "：" + invoiceReviewTitle : "")
                      : invoiceHasGap
                        ? "金额不足 " + fmtMoney(invoicedAmount) + " / " + fmtMoney(orderAmount)
                        : invoiceStatusText}
                  </div>
                </div>
                <div className="flex min-w-0 items-center gap-2">
                  <label className="w-[3.5em] shrink-0 whitespace-nowrap text-[11px] text-slate-500">备注</label>
                  <textarea value={mainForm.title} disabled={!editable} onChange={(event) => setMainForm({ ...mainForm, title: event.target.value })} rows={1} placeholder="可填写采购备注" className="h-8 min-w-0 flex-1 resize-none rounded-md border border-slate-200 bg-white px-2 py-1.5 text-[12px] text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50" />
                </div>
              </div>
            </div>
          </section>

          <section className="mt-3 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-100 px-4 py-2.5 text-[11px]">
              <div className="flex shrink-0 items-center gap-2">
                <span className="text-slate-500">订单状态</span>
                <span className={cx("rounded-md px-2 py-1 font-medium", statusClass(detailStatusLabel))}>{detailStatusLabel}</span>
              </div>
              <div className="relative flex min-w-[280px] flex-[1_1_360px] items-center gap-2">
                <label className="shrink-0 whitespace-nowrap text-slate-500">入库单号</label>
                <input value={inboundNoEntry} disabled={!editable} onFocus={() => {
                  if (editable && inboundQuery.trim()) setInboundEditorOpen(true);
                }} onChange={(event) => {
                  const value = event.target.value;
                  setInboundNoEntry(value);
                  setInboundQuery(value);
                  setInboundMessage("");
                  setInboundEditorOpen(Boolean(value.trim()));
                  if (!value.trim()) { setInboundCandidates([]); setSelectedInboundId(null); }
                }} placeholder="已有单号可关联；新单号按填写值创建，留空自动生成" className="h-8 min-w-0 flex-1 rounded-md border border-slate-200 bg-white px-2 font-mono text-[11px] text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50" />
                {inbound.length > 0 && <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-1 font-medium text-emerald-600">已关联</span>}
                {inboundEditorOpen && inboundQuery.trim() && (
                  <div className="absolute left-0 right-0 top-[36px] z-20 rounded-md border border-slate-200 bg-white p-1.5 shadow-xl">
                    {inboundCandidateLoading ? <div className="px-2 py-2 text-center text-[11px] text-slate-400">正在查询本地入库单…</div> : matchingInboundCandidates.length > 0 ? matchingInboundCandidates.slice(0, 6).map((candidate) => (
                      <button key={candidate.targetId} type="button" onClick={() => selectInboundCandidate(candidate)} className={cx("flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-left hover:bg-indigo-50", selectedInboundId === candidate.targetId ? "bg-indigo-50" : "")}>
                        <span className="min-w-0"><span className="block truncate font-mono text-[11px] text-slate-700">{candidate.targetNo}</span><span className="block truncate text-[10px] text-slate-400">{candidate.targetSupplier || "未注供应商"} · {candidate.warehouseName || "未注仓库"}</span></span>
                        <span className="shrink-0 text-[10px] text-slate-500">{fmtMoney(candidate.targetAmount)}</span>
                      </button>
                    )) : <div className="px-2 py-2 text-center text-[11px] text-amber-600">未找到旧入库单，保存时将按此编号新建</div>}
                  </div>
                )}
              </div>
              <div className="ml-auto flex shrink-0 items-center gap-3 text-slate-500"><span>商品明细 <span className="font-semibold text-slate-800">共 {displayRowCount} 条</span></span>
              <button type="button" disabled={!editable} onClick={() => { setEditingAllocation(null); setSelectedSkuId(null); setSkuEntry(""); setSkuQty("1"); setSkuTotal(""); resetSkuConsumable(); setSkuEditorTargetDoc(null); setSkuEditorOpen(true); }} className="flex shrink-0 items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40"><Icon name="plus" size={12} />新增商品</button>
              </div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1120px] border-collapse text-[11px]">
                <thead><tr className="bg-slate-50 text-slate-500">
                  <th className="border-b border-slate-100 px-3 py-2 text-left font-medium">类型</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-left font-medium">条形码</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-left font-medium">商品名称</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-right font-medium">数量</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-right font-medium">单价</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-right font-medium">金额</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-left font-medium">耗材名称</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-right font-medium">耗材使用量</th>
                  <th className="border-b border-slate-100 px-2 py-2 text-center font-medium">耗材匹配</th>
                  <th className="border-b border-slate-100 px-3 py-2 text-right font-medium">操作</th>
                </tr></thead>
                <tbody>
                  {allocations.length > 0 ? allocations.map((row, index) => {
                    const rowKey = row.id ?? index;
                    const rowType = order.orderKind === "consumable" ? "consumable" : "goods";
                    const catalogSku = skuCatalog.find((item) => item.id === row.skuId);
                    const rowMappings = allocationConsumables(row);
                    const primary = rowMappings[0];
                    const combinedShortage = rowMappings.reduce((sum, item) => sum + (item.shortageQty ?? 0), 0);
                    const hasUnmatched = rowMappings.some((item) => item.status.label === "未匹配");
                    const noConsumable = catalogSku?.consumablePolicy === "none";
                    const rowStatus = rowType === "consumable"
                      ? { label: "—", className: "bg-slate-100 text-slate-400" }
                      : noConsumable
                        ? { label: "无需耗材", className: "bg-slate-100 text-slate-500" }
                      : rowMappings.length === 0
                        ? { label: "未匹配", className: "bg-orange-50 text-orange-600" }
                        : hasUnmatched
                          ? { label: "未匹配", className: "bg-orange-50 text-orange-600" }
                          : combinedShortage > 0
                            ? { label: "缺 " + fmtQty(combinedShortage), className: "bg-rose-50 text-rose-600" }
                            : { label: "已匹配", className: "bg-emerald-50 text-emerald-600" };
                    const rowUnitPrice = row.unitPrice ?? (row.quantity && row.amount != null ? row.amount / row.quantity : null);
                    return (
                      <Fragment key={String(rowKey)}>
                        <tr className="border-b border-slate-50 align-middle hover:bg-slate-50/50">
                          <td className="px-3 py-2"><OrderKindTag value={rowType} /></td>
                          <td className="px-2 py-2 font-mono text-[10px] text-slate-500">{catalogSku?.barcode || row.skuCode || "—"}</td>
                          <td className="max-w-[300px] px-2 py-2"><div className="truncate font-medium text-slate-700" title={row.goodsName}>{row.goodsName || "未命名商品"}</div></td>
                          <td className="px-2 py-2 text-right tabular-nums text-slate-600">{fmtQty(row.quantity ?? null)}</td>
                          <td className="px-2 py-2 text-right tabular-nums text-slate-600">{fmtMoney(rowUnitPrice)}</td>
                          <td className="px-2 py-2 text-right font-semibold tabular-nums text-slate-800">{fmtMoney(row.amount)}</td>
                          <td className="max-w-[250px] px-2 py-2 text-slate-600">
                            {rowType === "consumable" || noConsumable ? <span className="text-slate-400">{noConsumable ? "无需耗材" : "—"}</span> : primary ? <span className="inline-flex max-w-full items-center gap-1"><span className="truncate" title={primary.material?.name || primary.mapping.consumableName}>{primary.material?.name || primary.mapping.consumableName}</span>{rowMappings.length > 1 && <button type="button" onClick={() => setExpandedConsumables((current) => current.includes(rowKey) ? current.filter((value) => value !== rowKey) : [...current, rowKey])} className="shrink-0 rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-600">+{rowMappings.length - 1}</button>}</span> : <span className="text-slate-400">—</span>}
                          </td>
                          <td className="px-2 py-2 text-right tabular-nums text-slate-600">{primary?.usedQty == null ? "—" : fmtQty(primary.usedQty)}</td>
                          <td className="px-2 py-2 text-center"><span className={cx("inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium", rowStatus.className)}>{rowStatus.label}</span></td>
                          <td className="whitespace-nowrap px-3 py-2 text-right">{editable && row.id ? <span className="inline-flex items-center gap-1.5"><button type="button" disabled={editorBusy} onClick={() => { setEditingAllocation(row.id!); setSelectedSkuId(row.skuId ?? null); setSkuEntry((row.skuCode || "") + (row.goodsName ? " · " + row.goodsName : "")); setSkuQty(String(row.quantity ?? 1)); setSkuTotal(String(row.amount ?? (row.unitPrice != null ? Number(row.quantity ?? 0) * row.unitPrice : ""))); seedSkuConsumable(row.skuId ?? null, String(row.quantity ?? 1)); setSkuEditorTargetDoc(null); setSkuEditorOpen(true); }} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-600 hover:border-indigo-300 hover:bg-indigo-100 disabled:opacity-40"><Icon name="edit" size={11} />编辑</button><button type="button" disabled={editorBusy} onClick={() => void removeAllocation(row.id as number)} className="rounded-md px-1 py-1 text-rose-500 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40">删除</button></span> : <span className="text-slate-300">—</span>}</td>
                        </tr>
                        {expandedConsumables.includes(rowKey) && rowMappings.length > 1 && <tr className="border-b border-slate-100 bg-indigo-50/30"><td colSpan={10} className="px-5 py-2"><div className="flex flex-wrap gap-x-5 gap-y-1.5">{rowMappings.map((item) => <span key={item.mapping.id} className="inline-flex items-center gap-1.5 text-[10px] text-slate-600"><span className="font-medium">{item.material?.name || item.mapping.consumableName}</span><span>使用 {item.usedQty == null ? "—" : fmtQty(item.usedQty)}</span><span className={cx("rounded-full px-1.5 py-0.5 font-medium", item.status.className)}>{item.status.label}</span></span>)}</div></td></tr>}
                      </Fragment>
                    );
                  }) : consumableItems.length > 0 ? consumableItems.map((item, index) => {
                    const quantity = Number(item.quantity || 0);
                    const unitCost = Number(item.unitCost || 0);
                    const amount = quantity * unitCost;
                    const receivedQty = Number(item.receivedQty || 0);
                    const isEditing = editingConsumable?.purchaseId === item.purchaseId && editingConsumable?.id === item.id;
                    if (isEditing) return (
                      <tr key={`consumable-${item.id ?? index}`} className="border-b border-indigo-100 bg-indigo-50/60 align-middle">
                        <td className="px-3 py-2"><OrderKindTag value="consumable" /></td>
                        <td className="px-2 py-2 font-mono text-[10px] text-slate-500">{item.code || "—"}</td>
                        <td className="max-w-[300px] px-2 py-2"><div className="truncate font-medium text-slate-700" title={item.name}>{item.name || "未命名耗材"}</div><div className="mt-0.5 text-[10px] text-slate-400">已入库 {fmtQty(receivedQty)} {item.unit || ""}</div></td>
                        <td className="px-2 py-2 text-right"><input type="number" min={receivedQty || 0.0001} step="0.0001" aria-label="耗材数量" value={consumableEditQty} onChange={(event) => setConsumableEditQty(event.target.value)} className="h-8 w-full max-w-[92px] rounded border border-indigo-300 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-500" /></td>
                        <td className="px-2 py-2 text-right tabular-nums font-medium text-slate-700">{fmtMoney(Number(consumableEditQty) > 0 && consumableEditTotal !== "" ? Number(consumableEditTotal) / Number(consumableEditQty) : null)}</td>
                        <td className="px-2 py-2 text-right"><input type="number" min="0" step="0.0001" aria-label="耗材总价" value={consumableEditTotal} onChange={(event) => setConsumableEditTotal(event.target.value)} className="h-8 w-full max-w-[104px] rounded border border-indigo-300 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-500" /></td>
                        <td className="px-2 py-2 text-slate-400">—</td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-400">—</td>
                        <td className="px-2 py-2 text-center"><span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">—</span></td>
                        <td className="whitespace-nowrap px-3 py-2 text-right"><span className="inline-flex items-center gap-1.5"><button type="button" disabled={consumableEditBusy} onClick={() => void saveConsumableEdit()} className="rounded-md bg-indigo-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40">{consumableEditBusy ? "保存中…" : "保存"}</button><button type="button" disabled={consumableEditBusy} onClick={cancelConsumableEdit} className="rounded-md px-1 py-1 text-slate-500 hover:bg-slate-100 disabled:opacity-40">取消</button></span></td>
                      </tr>
                    );
                    return (
                      <tr key={`consumable-${item.id ?? index}`} className="border-b border-slate-50 align-middle hover:bg-slate-50/50">
                        <td className="px-3 py-2"><OrderKindTag value="consumable" /></td>
                        <td className="px-2 py-2 font-mono text-[10px] text-slate-500">{item.code || "—"}</td>
                        <td className="max-w-[300px] px-2 py-2"><div className="truncate font-medium text-slate-700" title={item.name}>{item.name || "未命名耗材"}</div><div className="mt-0.5 text-[10px] text-slate-400">已入库 {fmtQty(receivedQty)} {item.unit || ""}</div></td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-600">{fmtQty(quantity)}</td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-600">{fmtMoney(unitCost)}</td>
                        <td className="px-2 py-2 text-right font-semibold tabular-nums text-slate-800">{fmtMoney(amount)}</td>
                        <td className="px-2 py-2 text-slate-400">—</td>
                        <td className="px-2 py-2 text-right tabular-nums text-slate-400">—</td>
                        <td className="px-2 py-2 text-center"><span className="inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500">—</span></td>
                        <td className="whitespace-nowrap px-3 py-2 text-right">{editable && item.purchaseId != null ? <button type="button" disabled={consumableEditBusy} onClick={() => startConsumableEdit(item)} className="inline-flex items-center gap-1 rounded-md border border-indigo-200 bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-600 hover:border-indigo-300 hover:bg-indigo-100 disabled:opacity-40"><Icon name="edit" size={11} />编辑</button> : <span className="text-[10px] text-slate-400">耗材采购明细</span>}</td>
                      </tr>
                    );
                  }) : <tr><td colSpan={10} className="py-8 text-center text-[11px] text-slate-400">暂无商品明细，请点击右上角“新增商品”</td></tr>}
                  {skuEditorOpen ? skuEditorRow : null}
                </tbody>
              </table>
            </div>
            {displayRowCount > 0 && <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/50 px-4 py-2 text-[11px]"><span className="text-slate-500">明细合计</span><span className="font-semibold tabular-nums text-slate-800">{fmtMoney(displayTotal)}</span>{hasDetailAmountGap && <span className="ml-auto rounded-md bg-amber-50 px-2 py-1 text-[10px] font-medium text-amber-700">与采购金额 {fmtMoney(purchaseAmount)} 相差 {fmtMoney(Math.abs(detailAmountGap))}，请编辑商品明细确认</span>}</div>}
          </section>

        </div>
      </div>
    </aside>
  );
}


function FormField({ label, required, children, className }: {
  label: string;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("min-w-0", className)}>
      <div className="flex items-center gap-1 text-[11px] text-slate-500">
        {label}
        {required && <span className="text-rose-500">*</span>}
      </div>
      <div className="mt-0.5 leading-7 text-[12px] text-slate-700">{children}</div>
    </div>
  );
}

/** 耗材订单专属：直接在本平台登记耗材入库单（HC 单）并收货进耗材库台账。 */
const CONSUMABLE_PO_STATUS: Record<string, { label: string; cls: string }> = {
  ordered: { label: "待收货", cls: "bg-amber-50 text-amber-600" },
  partial: { label: "部分收货", cls: "bg-amber-50 text-amber-600" },
  received: { label: "已收齐", cls: "bg-emerald-50 text-emerald-600" },
  cancelled: { label: "已取消", cls: "bg-slate-100 text-slate-500" },
};

function OrderConsumableSection({ order, materials }: { order: WorkbenchOrderRow; materials: ConsumableRow[] }) {
  const sourceOrderId = order.fileOrderId;
  const [purchases, setPurchases] = useState<ConsumablePurchaseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState<{ orderedOn: string; lines: Array<{ _key: string; consumable_id: string; quantity: string; unit_cost: string; total_amount: string }> }>({
    orderedOn: inputDate(new Date()),
    lines: [{ _key: newRequestKey(), consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }],
  });
  const [receivingId, setReceivingId] = useState<number | null>(null);
  const [receiveDate, setReceiveDate] = useState(inputDate(new Date()));
  const [receiveQtys, setReceiveQtys] = useState<Record<number, string>>({});
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [receiveWarehouseId, setReceiveWarehouseId] = useState("");
  // 单价自动均摊：单价 = 订单实付 ÷ 总数量，让合计与实付精确一致；手动改单价会自动关闭。
  const [autoUnitCost, setAutoUnitCost] = useState(true);
  // 行内编辑（采购量 / 单价 / 总金额）；整单删除 / 恢复 cancelled → ordered 的就地反馈。
  const [editingLine, setEditingLine] = useState<{ purchaseId: number; line: ConsumablePurchaseItem } | null>(null);
  const [editQty, setEditQty] = useState("");
  const [editCost, setEditCost] = useState("");
  const [editTotal, setEditTotal] = useState("");

  const load = useCallback(async () => {
    if (sourceOrderId == null && !order.orderNo) { setPurchases([]); setLoading(false); return; }
    setLoading(true);
    // 统一按订单号圈定：1688 单看 source 原件单号，工作流独有单看 reference_no，
    // 避免 source_order_id 为空时漏出其他订单的耗材入库单。
    try { setPurchases(await consumablesApi.purchases("", undefined, order.orderNo)); setError(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "耗材采购单加载失败"); }
    finally { setLoading(false); }
  }, [sourceOrderId, order.orderNo]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let active = true;
    void warehousesApi.list(false).then((rows) => {
      if (!active) return;
      const usable = rows.filter((row) => row.purpose === "consumable" || row.purpose === "both");
      setWarehouses(usable);
      setReceiveWarehouseId((current) => current || String(usable.find((row) => row.warehouseType === "factory")?.id ?? usable[0]?.id ?? ""));
    }).catch(() => {
      if (active) setError("仓库配置加载失败，请先到仓库管理检查耗材仓");
    });
    return () => { active = false; };
  }, []);

  // 总进度：跨本单全部耗材入库单汇总（已取消的不计）。
  const activePurchases = purchases.filter((row) => row.status !== "cancelled");
  const progress = activePurchases.reduce(
    (acc, row) => {
      for (const line of row.items) {
        acc.qty += Number(line.quantity) || 0;
        acc.received += Number(line.receivedQty) || 0;
        acc.amount += Number(line.quantity) * Number(line.unitCost) || 0;
        acc.receivedAmount += (Number(line.receivedQty) || 0) * Number(line.unitCost) || 0;
      }
      return acc;
    },
    { qty: 0, received: 0, amount: 0, receivedAmount: 0 },
  );
  const progressPct = progress.qty > 0 ? Math.min(100, Math.round((progress.received / progress.qty) * 100)) : 0;

  // 建单表单的自动单价：实付 ÷ 总数量（10 位小数），合计与订单实付严格一致（发票口径）。
  const orderTotal = Number(order.paidAmount ?? order.amount ?? 0);
  const formTotalQty = form.lines.reduce((sum, line) => sum + Number(line.quantity || 0), 0);
  const autoUnit = formTotalQty > 0 && orderTotal > 0 ? Number((orderTotal / formTotalQty).toFixed(10)) : 0;
  function applyAutoUnitCost(lines: Array<{ _key: string; consumable_id: string; quantity: string; unit_cost: string; total_amount: string }>) {
    const totalQty = lines.reduce((sum, line) => sum + Number(line.quantity || 0), 0);
    if (!autoUnitCost || totalQty <= 0 || orderTotal <= 0) return lines;
    const unit = String(Number((orderTotal / totalQty).toFixed(10)));
    return lines.map((line) => {
      const q = Number(line.quantity || 0);
      const cost = line.quantity ? unit : line.unit_cost;
      return { ...line, unit_cost: cost, total_amount: q > 0 ? (q * Number(cost)).toFixed(4) : line.total_amount };
    });
  }
  // 每行三 input（数量/总金额/单价）互相推算，跟 inline 编辑保持一致。
  function setLineField(index: number, patch: Partial<{ consumable_id: string; quantity: string; unit_cost: string; total_amount: string }>) {
    setForm({ ...form, lines: form.lines.map((item, i) => i === index ? { ...item, ...patch } : item) });
  }
  function onCreateQtyChange(index: number, value: string) {
    const q = Number(value);
    const cost = Number(form.lines[index].unit_cost);
    const total = q > 0 && Number.isFinite(cost) ? (q * cost).toFixed(4) : "";
    setLineField(index, { quantity: value, total_amount: total });
  }
  function onCreateTotalChange(index: number, value: string) {
    const t = Number(value);
    const q = Number(form.lines[index].quantity);
    // 单价 10 位小数：让合计与订单实付严格对齐（发票口径）。
    const cost = q > 0 && Number.isFinite(t) ? (t / q).toFixed(10) : form.lines[index].unit_cost;
    setLineField(index, { total_amount: value, unit_cost: String(cost) });
  }
  function onCreateCostChange(index: number, value: string) {
    if (autoUnitCost) setAutoUnitCost(false);
    const c = Number(value);
    const q = Number(form.lines[index].quantity);
    const total = q > 0 && Number.isFinite(c) ? (c * q).toFixed(4) : "";
    setLineField(index, { unit_cost: value, total_amount: total });
  }

  async function createPurchase(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const items = form.lines
      .filter((line) => line.consumable_id && line.quantity)
      .map((line) => ({ consumable_id: Number(line.consumable_id), quantity: line.quantity, unit_cost: line.unit_cost || "0" }));
    if (!items.length) { setError("请至少选择一种耗材并填写数量"); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      await consumablesApi.createPurchase({
        request_key: newRequestKey(),
        supplier_name: order.supplier || "未记录供应商",
        ordered_on: form.orderedOn || inputDate(new Date()),
        source_order_id: sourceOrderId,
        reference_no: order.orderNo,
        note: "采购工作台耗材订单登记",
        items,
      });
      setCreating(false);
      setForm({ orderedOn: inputDate(new Date()), lines: [{ _key: newRequestKey(), consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }] });
      setMessage("耗材入库单已建立；到货后在本区块「登记收货」，实收数量会计入本平台耗材库存台账。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "耗材采购单创建失败");
    } finally {
      setBusy(false);
    }
  }

  function startReceive(row: ConsumablePurchaseRow) {
    setReceivingId(row.id);
    setReceiveWarehouseId((current) => current || String(warehouses.find((warehouse) => warehouse.warehouseType === "factory")?.id ?? warehouses[0]?.id ?? ""));
    setReceiveDate(inputDate(new Date()));
    setReceiveQtys(Object.fromEntries(row.items.map((line) => [line.id, ""])));
    setError(""); setMessage("");
  }

  async function saveReceive(row: ConsumablePurchaseRow) {
    if (busy) return;
    const items = row.items
      .filter((line) => (receiveQtys[line.id] ?? "").trim() !== "" && Number(receiveQtys[line.id]) !== 0)
      .map((line) => ({ item_id: line.id, quantity: receiveQtys[line.id] }));
    if (!items.length) { setError("请填写本次实收数量，未到货的耗材留空"); return; }
    if (!receiveWarehouseId) { setError("请选择收货仓库"); return; }
    setBusy(true); setError("");
    try {
      await warehousesApi.receiveConsumablePurchase(row.id, {
        request_key: newRequestKey(),
        received_on: receiveDate || inputDate(new Date()),
        warehouse_id: Number(receiveWarehouseId),
        note: `来源${CHANNELS[channelOf(order.platform)].label}订单 ${order.orderNo}`,
        items,
      });
      setReceivingId(null);
      setMessage("收货已登记，实收数量已计入耗材库存。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "收货登记失败");
    } finally {
      setBusy(false);
    }
  }

  // ===== 行内编辑（数量/单价/总金额）、整单删除、cancelled 恢复 =====
  function startEditLine(purchaseId: number, line: ConsumablePurchaseItem) {
    setEditingLine({ purchaseId, line });
    setEditQty(line.quantity);
    setEditCost(line.unitCost);
    // 总金额 = 数量 × 单价（4 位小数），与后端 amount 字段对齐。
    const t = (Number(line.quantity) * Number(line.unitCost)).toFixed(4);
    setEditTotal(t);
    setError(""); setMessage("");
  }
  function cancelEditLine() { setEditingLine(null); setEditQty(""); setEditCost(""); setEditTotal(""); }

  // 三个 input 互相推算：改任一，其余自动跟着算。
  // - 改数量 → 总金额 = 数量 × 单价
  // - 改总金额 → 单价 = 总金额 ÷ 数量
  // - 改单价 → 总金额 = 单价 × 数量
  // 存盘仍以「数量 + 单价」提交（与后端 PATCH items 协议一致）。
  function onEditQtyChange(value: string) {
    setEditQty(value);
    const q = Number(value);
    if (q > 0) setEditTotal((Number(editCost) * q).toFixed(4));
  }
  function onEditTotalChange(value: string) {
    setEditTotal(value);
    const t = Number(value);
    const q = Number(editQty);
    // 单价 10 位小数：让合计与订单实付严格对齐（发票口径），如 800 ÷ 1050 = 0.7619047619。
    if (q > 0) setEditCost((t / q).toFixed(10));
  }
  function onEditCostChange(value: string) {
    setEditCost(value);
    const c = Number(value);
    const q = Number(editQty);
    if (q > 0) setEditTotal((c * q).toFixed(4));
  }

  async function saveEditLine(row: ConsumablePurchaseRow) {
    if (busy || !editingLine) return;
    const qty = Number(editQty);
    const cost = Number(editCost);
    if (!Number.isFinite(qty) || qty <= 0) { setError("采购数量必须大于 0"); return; }
    if (!Number.isFinite(cost) || cost < 0) { setError("单价不能为负"); return; }
    // 整张单整体 PATCH 上去——后端按 consumable_id 全量对齐（增量更新会失同步）。
    const items = row.items.map((it) => it.id === editingLine.line.id
      ? { consumable_id: it.consumableId, quantity: String(qty), unit_cost: String(cost) }
      : { consumable_id: it.consumableId, quantity: it.quantity, unit_cost: it.unitCost },
    );
    setBusy(true); setError("");
    try {
      await consumablesApi.updatePurchase(row.id, { items });
      setEditingLine(null);
      setEditQty(""); setEditCost("");
      setMessage(`已更新 ${editingLine.line.name}（${editingLine.line.code}）的采购量/单价`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "耗材明细保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function doDeletePurchase(row: ConsumablePurchaseRow) {
    if (busy) return;
    const hasReceived = row.items.some((it) => Number(it.receivedQty) > 0);
    const tip = hasReceived
      ? `耗材入库单 ${row.number} 已有收货记录：删除时会自动生成负数冲销流水，把已入库存的 ${row.items.reduce((s, it) => s + Number(it.receivedQty || 0), 0)} 件从对应仓回冲（原收货流水保留可查）。确定删除整单？`
      : `耗材入库单 ${row.number} 将被物理删除（无收货记录，可安全删除）。`;
    if (!window.confirm(`确认删除？\n\n${tip}`)) return;
    setBusy(true); setError("");
    try {
      await consumablesApi.deletePurchase(row.id);
      setMessage(`已删除耗材入库单 ${row.number}${hasReceived ? "（库存已自动冲销）" : ""}`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  async function doReopenPurchase(row: ConsumablePurchaseRow) {
    if (busy) return;
    if (!window.confirm(`把已取消的耗材入库单 ${row.number} 恢复为「待收货」？\n\n将恢复 ordered 状态，不影响耗材档案。`)) return;
    setBusy(true); setError("");
    try {
      await consumablesApi.reopenPurchase(row.id);
      setMessage(`已恢复耗材入库单 ${row.number} → 待收货`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  const activeMaterials = materials.filter((material) => material.status === "active");
  const hasActivePurchase = purchases.some((row) => row.status !== "cancelled");
  const inputCls = "h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none focus:border-indigo-400";

  return (
    <DetailSection
      title="耗材入库单"
      badge="本平台耗材库"
    >
      <p className="mb-3 text-[11px] leading-5 text-slate-400">
        该订单是耗材（包材）采购：作为耗材入库单登记，到货后登记收货，实收数量直接计入本平台耗材库存台账，与正品货品走不同的库存体系。
      </p>
      {activePurchases.length > 0 && (
        <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <span className="font-semibold text-slate-700">收货总进度</span>
            <span className="tabular-nums text-slate-500">
              已收 <strong className={progressPct >= 100 ? "text-emerald-600" : "text-slate-700"}>{progress.received.toLocaleString("zh-CN")}</strong>
              {" / "}{progress.qty.toLocaleString("zh-CN")}
              <span className="ml-2 font-semibold text-indigo-600">{progressPct}%</span>
            </span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className={cx("h-full rounded-full transition-all", progressPct >= 100 ? "bg-emerald-500" : "bg-indigo-500")}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-400">
            <span>{activePurchases.length} 张入库单 · 已收货金额 ¥{progress.receivedAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
            <span>入库单合计 ¥{progress.amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
        </div>
      )}
      {message && <div className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-[11px] text-emerald-700">{message}</div>}
      {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-[11px] text-red-600">{error}</div>}
      {loading ? (
        <div className="py-4 text-center text-[11px] text-slate-400">正在加载耗材采购单…</div>
      ) : (
        <div className="space-y-3">
          {purchases.map((row) => {
            const status = CONSUMABLE_PO_STATUS[row.status] ?? { label: row.status, cls: "bg-slate-100 text-slate-500" };
            const receiving = receivingId === row.id && (row.status === "ordered" || row.status === "partial");
            return (
              <div key={row.id} className="rounded-lg border border-slate-200 bg-white">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-medium text-indigo-600">{row.number}</span>
                    <span className={cx("rounded px-1.5 py-0.5 text-[10px] font-medium", status.cls)}>{status.label}</span>
                    <span className="text-[10px] text-slate-400">{row.orderedOn} · ¥{Number(row.amount).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {row.status === "cancelled" && (
                      <button disabled={busy} onClick={() => void doReopenPurchase(row)} title="把已取消的耗材入库单恢复为「待收货」" className="rounded-md border border-emerald-200 bg-white px-2 py-1 text-[11px] font-medium text-emerald-600 hover:bg-emerald-50 disabled:opacity-40">
                        ↺ 恢复
                      </button>
                    )}
                    <button disabled={busy} onClick={() => void doDeletePurchase(row)} title="删除这张耗材入库单（已收货的会自动冲销库存流水）" className="rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] font-medium text-red-500 hover:bg-red-50 disabled:opacity-40">
                      🗑 删除
                    </button>
                    {!receiving && (row.status === "ordered" || row.status === "partial") && (
                      <button onClick={() => startReceive(row)} className="rounded-md bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100">登记收货</button>
                    )}
                  </div>
                </div>
                <table className="w-full text-left text-[11px]">
                  <thead className="text-slate-400">
                    <tr className="border-b border-slate-100">
                      <th className="px-3 py-1.5 font-medium">耗材</th>
                      <th className="px-2 py-1.5 text-right font-medium">采购量</th>
                      <th className="px-2 py-1.5 text-right font-medium">已收</th>
                      <th className="px-2 py-1.5 text-right font-medium">待收</th>
                      <th className="px-2 py-1.5 text-right font-medium">单价</th>
                      <th className="px-2 py-1.5 text-right font-medium">总金额</th>
                      {receiving && <th className="px-3 py-1.5 text-right font-medium">本次实收</th>}
                      <th className="px-2 py-1.5 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {row.items.map((line) => {
                      const remaining = Number(line.quantity) - Number(line.receivedQty);
                      const lineAmount = Number(line.quantity) * Number(line.unitCost);
                      const isEditing = editingLine?.line.id === line.id;
                      return (
                        <Fragment key={line.id}>
                          <tr>
                            <td className="px-3 py-1.5"><span className="text-slate-700">{line.name}</span> <span className="ml-1 font-mono text-[9px] text-slate-400">{line.code}</span></td>
                            <td className="px-2 py-1.5 text-right tabular-nums">{Number(line.quantity).toLocaleString("zh-CN")} {line.unit}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{Number(line.receivedQty).toLocaleString("zh-CN")}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums font-medium">{remaining.toLocaleString("zh-CN")}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">¥{Number(line.unitCost).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums font-medium text-slate-700">¥{lineAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                            {receiving && (
                              <td className="px-3 py-1.5 text-right">
                                <input
                                  aria-label={`${line.name} 本次实收`}
                                  type="number"
                                  min="0"
                                  max={remaining}
                                  step="0.0001"
                                  disabled={remaining === 0 || busy}
                                  value={receiveQtys[line.id] ?? ""}
                                  onChange={(event) => setReceiveQtys({ ...receiveQtys, [line.id]: event.target.value })}
                                  placeholder="未到货留空"
                                  className={cx(inputCls, "ml-auto w-24 text-right")}
                                />
                              </td>
                            )}
                            <td className="px-2 py-1.5 text-right">
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => isEditing ? cancelEditLine() : startEditLine(row.id, line)}
                                  title={isEditing ? "取消编辑" : "编辑这条耗材的采购量 / 单价 / 总金额（已收货的单：数量不能低于已收数）"}
                                  className={cx(
                                    "rounded border px-1.5 py-0.5 text-[10px] font-medium disabled:opacity-40",
                                    isEditing
                                      ? "border-slate-300 bg-white text-slate-500"
                                      : "border-indigo-200 bg-white text-indigo-600 hover:bg-indigo-50",
                                  )}
                                >
                                  {isEditing ? "取消" : "✎"}
                                </button>
                              </td>
                          </tr>
                          {isEditing && (
                            <tr className="bg-amber-50/60">
                              <td colSpan={receiving ? 8 : 7} className="px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-700">
                                  <span className="font-medium text-slate-800">编辑 {line.name}</span>
                                  <span className="font-mono text-[10px] text-slate-400">{line.code}</span>
                                  <span className="ml-2 text-slate-500">采购量</span>
                                  <input
                                    type="number" min="0.0001" step="0.0001"
                                    aria-label={`${line.name} 新采购量`}
                                    value={editQty} onChange={(e) => onEditQtyChange(e.target.value)}
                                    className={cx(inputCls, "w-24 text-right")}
                                    disabled={busy}
                                  />
                                  <span className="text-slate-500">单价</span>
                                  <input
                                    type="number" min="0" step="any"
                                    aria-label={`${line.name} 新单价`}
                                    value={editCost} onChange={(e) => onEditCostChange(e.target.value)}
                                    className={cx(inputCls, "w-24 text-right")}
                                    disabled={busy}
                                  />
                                  <span className="text-slate-500">总金额</span>
                                  <input
                                    type="number" min="0" step="any"
                                    aria-label={`${line.name} 新总金额`}
                                    value={editTotal} onChange={(e) => onEditTotalChange(e.target.value)}
                                    className={cx(inputCls, "w-28 text-right font-medium text-indigo-700")}
                                    disabled={busy}
                                  />
                                  <button type="button" disabled={busy} onClick={() => void saveEditLine(row)} className="rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50">保存</button>
                                  <button type="button" disabled={busy} onClick={cancelEditLine} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-50 disabled:opacity-50">取消</button>
                                  <span className="text-[10px] text-amber-700">已收货的单：数量不能低于已收数；单价改动会同步收货成本</span>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                  {(() => {
                    const totalQty = row.items.reduce((s, it) => s + Number(it.quantity), 0);
                    const totalAmount = row.items.reduce((s, it) => s + Number(it.quantity) * Number(it.unitCost), 0);
                    if (totalAmount === 0 && totalQty === 0) return null;
                    return (
                      <tfoot>
                        <tr className="border-t border-slate-200 bg-slate-50/70 text-[11px]">
                          <td className="px-3 py-1.5 font-semibold text-slate-600">合计</td>
                          <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">{totalQty.toLocaleString("zh-CN")}</td>
                          <td colSpan={2} className="px-2 py-1.5"></td>
                          <td className="px-2 py-1.5 text-right text-[10px] text-slate-400">×{row.items.length} 项</td>
                          <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">¥{totalAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                          {receiving && <td className="px-2 py-1.5"></td>}
                          <td className="px-2 py-1.5"></td>
                        </tr>
                      </tfoot>
                    );
                  })()}
                </table>
                {receiving && (
                  <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-3 py-2">
                    <label className="text-[10px] text-slate-500">收货仓库
                      <select value={receiveWarehouseId} onChange={(event) => setReceiveWarehouseId(event.target.value)} className={cx(inputCls, "ml-1 inline-block min-w-40")}>
                        <option value="">请选择仓库</option>
                        {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name} · {warehouse.code}</option>)}
                      </select>
                    </label>
                    <label className="text-[10px] text-slate-500">收货日期
                      <input type="date" value={receiveDate} onChange={(event) => setReceiveDate(event.target.value)} className={cx(inputCls, "ml-1 inline-block w-32")} />
                    </label>
                    <button disabled={busy} onClick={() => setReceivingId(null)} className="rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-500">取消</button>
                    <button disabled={busy} onClick={() => void saveReceive(row)} className="rounded-md bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50">{busy ? "入库中…" : "确认收货入库"}</button>
                  </div>
                )}
              </div>
            );
          })}

          {!hasActivePurchase && !creating && (
            <button onClick={() => { setCreating(true); setError(""); setMessage(""); }} disabled={!activeMaterials.length} className="rounded-md bg-indigo-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50" title={activeMaterials.length ? "" : "请先在「商品与库存 → 耗材」建立耗材档案"}>
              + 登记耗材入库单
            </button>
          )}

          {creating && (
            <form onSubmit={createPurchase} className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-slate-700">新建耗材入库单（关联{CHANNELS[channelOf(order.platform)].label}订单 {order.orderNo}）</span>
                <button type="button" onClick={() => setCreating(false)} className="text-[11px] text-slate-400 hover:text-slate-600">收起</button>
              </div>
              <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
                <label>采购日期 <input type="date" required value={form.orderedOn} onChange={(event) => setForm({ ...form, orderedOn: event.target.value })} className={cx(inputCls, "ml-1 inline-block w-32")} /></label>
              </div>
              <div className="mt-2 space-y-2">
                {form.lines.map((line, index) => (
                  <div key={line._key} className="grid grid-cols-[minmax(0,1fr)_84px_92px_84px_24px] items-center gap-2">
                    <SearchableSelect
                      ariaLabel={`第${index + 1}行耗材`}
                      placeholder="选择耗材"
                      className="w-full"
                      value={line.consumable_id}
                      onChange={(next) => {
                        const material = activeMaterials.find((row) => row.id === Number(next));
                        const newCost = material?.purchaseUnitCost ?? line.unit_cost;
                        const q = Number(line.quantity || 0);
                        const newTotal = q > 0 && Number(newCost) > 0 ? (q * Number(newCost)).toFixed(4) : line.total_amount;
                        setLineField(index, { consumable_id: next, unit_cost: newCost, total_amount: newTotal });
                      }}
                      options={activeMaterials.map((material) => ({
                        value: String(material.id),
                        label: `${material.code} · ${material.name}（${material.unit}）`,
                        keywords: `${material.code} ${material.name}`,
                      }))}
                    />
                    <input required type="number" min="0.0001" step="0.0001" aria-label={`第${index + 1}行数量`} placeholder="数量" value={line.quantity} onChange={(event) => onCreateQtyChange(index, event.target.value)} className={cx(inputCls, "text-right")} />
                    <input type="number" min="0" step="any" aria-label={`第${index + 1}行单价`} placeholder="单价" value={line.unit_cost} onChange={(event) => onCreateCostChange(index, event.target.value)} className={cx(inputCls, "text-right")} />
                    <input type="number" min="0" step="any" aria-label={`第${index + 1}行总金额`} placeholder="总金额" value={line.total_amount} onChange={(event) => onCreateTotalChange(index, event.target.value)} className={cx(inputCls, "text-right font-medium text-indigo-700")} />
                    <button type="button" disabled={form.lines.length === 1} aria-label={`移除第${index + 1}行`} onClick={() => setForm({ ...form, lines: form.lines.filter((_, i) => i !== index) })} className="text-slate-300 hover:text-slate-500 disabled:opacity-30">×</button>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex items-center justify-between">
                <button type="button" onClick={() => setForm({ ...form, lines: applyAutoUnitCost([...form.lines, { _key: newRequestKey(), consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }]) })} className="text-[11px] font-medium text-indigo-600">+ 添加耗材</button>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-500">合计 <strong className="text-slate-700">¥{form.lines.reduce((sum, line) => sum + Number(line.quantity || 0) * Number(line.unit_cost || 0), 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>
                  <button disabled={busy} className="rounded-md bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50">{busy ? "保存中…" : "建立采购单"}</button>
                </div>
              </div>
              <label className="mt-2 flex items-center gap-1.5 text-[10px] text-slate-500">
                <input type="checkbox" checked={autoUnitCost} onChange={(event) => { setAutoUnitCost(event.target.checked); if (event.target.checked) setForm({ ...form, lines: applyAutoUnitCost(form.lines) }); }} />
                单价按订单实付自动均摊
                {autoUnitCost && formTotalQty > 0 && orderTotal > 0 && (
                  <span className="text-slate-400">＝ 实付 ¥{orderTotal.toLocaleString("zh-CN", { minimumFractionDigits: 2 })} ÷ 总数量 {formTotalQty.toLocaleString("zh-CN")} = ¥{autoUnit}（手动改单价即转为手动模式）</span>
                )}
              </label>
            </form>
          )}

          {!purchases.length && !creating && !loading && (
            <p className="text-[10px] text-slate-400">还没有关联本订单的耗材入库单。上方按钮建档后，收货即入「商品与库存 → 耗材」的库存台账。</p>
          )}
        </div>
      )}
    </DetailSection>
  );
}

function InboundDocCard({ doc, allocations, closed, editable, usageMaterials, usageMappings, busy,
  onChanged, onRequestReplace, onRemove, onCorrectAmount, onSaveAmount,
  onEditAllocation, onRemoveAllocation, onAddSku, onNotify, pendingConsumableUpdate,
  onPendingConsumableUpdateApplied, editor }: {
  doc: InboundRow;
  allocations: AllocationRow[];
  closed: boolean;
  editable: boolean;
  usageMaterials: ConsumableRow[];
  usageMappings: ConsumableMappingRow[];
  busy: boolean;
  onChanged: () => Promise<void>;
  onRequestReplace: (linkId: number) => void;
  onRemove: (linkId: number) => void;
  onCorrectAmount: (documentId: number) => void;
  onSaveAmount: (documentId: number, value: string) => void;
  onEditAllocation: (row: AllocationRow, consumable: EditorConsumableDraft | null) => void;
  onRemoveAllocation: (id: number) => void;
  onAddSku: (documentId: number) => void;
  onNotify: (message: string) => void;
  pendingConsumableUpdate?: PendingConsumableUpdate | null;
  onPendingConsumableUpdateApplied?: () => void;
  editor?: ReactNode;
}) {
  const [amountEditing, setAmountEditing] = useState(false);
  const [amountValue, setAmountValue] = useState("");
  const [usageEnabled, setUsageEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  // 每行耗材的本地草稿：key -> { consumableId, quantity } | null（null = 该行无默认绑定，需手工选）
  const [rowCons, setRowCons] = useState<Record<string, { consumableId: number; quantity: string } | null>>({});

  const linkId = doc.linkId ?? null;
  const decided = Boolean(doc.consumableUsageDecided);
  const items = doc.consumableUsageItems ?? [];
  const mismatch = doc.amount != null && doc.itemAmount != null && Math.abs(doc.amount - doc.itemAmount) > 0.01;
  const skuTotal = allocations.reduce((sum, row) => sum + (row.amount ?? 0), 0);

  const rowKey = (row: AllocationRow, i: number) => (row.id != null ? `a${row.id}` : `c${(row.skuCode ?? "")}-${i}`);
  const round3 = (n: number) => String(Number(n.toFixed(3)));
  const defaultFor = (row: AllocationRow, mapping?: ConsumableMappingRow) =>
    mapping ? { consumableId: mapping.consumableId, quantity: round3(Number(row.quantity ?? 0) * Number(mapping.usagePerUnit || 0)) } : null;
  // 选了某个耗材时，按“该货品→该耗材”的用量比例给出默认数量；无映射则默认 1
  const defaultQtyFor = (row: AllocationRow, consumableId: number) => {
    const m = usageMappings.find((row2) => row2.consumableId === consumableId);
    return round3(m ? Number(row.quantity ?? 0) * Number(m.usagePerUnit || 0) : 1);
  };

  // 卡片挂载（或换单据）时预填每行耗材草稿：
  // 1) 已登记过（decided && enabled）：该耗材仅被一行映射命中时，直接回显实际登记数量；
  // 2) 未登记：按 SKU→耗材映射预填默认（数量 = usagePerUnit × 数量）；无映射留给手工选。
  // 用户编辑会写入 rowCons 不被覆盖。
  useEffect(() => {
    const seeded: Record<string, { consumableId: number; quantity: string } | null> = {};
    const registered = doc.consumableUsageDecided && doc.consumableUsageEnabled ? (doc.consumableUsageItems ?? []) : [];
    const hitCount = new Map<number, number>();
    allocations.forEach((row) => {
      const m = usageMappings.find((mm) => mm.skuId === row.skuId);
      if (m) hitCount.set(m.consumableId, (hitCount.get(m.consumableId) ?? 0) + 1);
    });
    allocations.forEach((row, i) => {
      const k = rowKey(row, i);
      const m = usageMappings.find((mm) => mm.skuId === row.skuId);
      let draft = defaultFor(row, m);
      if (m && hitCount.get(m.consumableId) === 1) {
        const item = registered.find((it) => it.consumableId === m.consumableId);
        if (item) draft = { consumableId: m.consumableId, quantity: round3(Number(item.quantity)) };
      }
      seeded[k] = draft;
    });
    setRowCons(seeded);
    setUsageEnabled(decided ? Boolean(doc.consumableUsageEnabled) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.documentId]);

  useEffect(() => {
    if (!pendingConsumableUpdate) return;
    const index = allocations.findIndex((row) => row.id === pendingConsumableUpdate.allocationId);
    if (index < 0) return;
    const key = rowKey(allocations[index], index);
    setRowCons((previous) => ({ ...previous, [key]: pendingConsumableUpdate.draft }));
    onPendingConsumableUpdateApplied?.();
    // The parent clears this one-shot update after the allocation is present in the refreshed detail.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocations, pendingConsumableUpdate]);

  async function saveConsumables() {
    if (!linkId) return;
    if (usageEnabled === null) { onNotify("请先选择「有耗材使用」或「本次不使用耗材」"); return; }
    setSaving(true);
    try {
      if (usageEnabled) {
        // 把每行耗材按 consumableId 聚合（一个货品对应一个耗材，多行可合并）
        const agg = new Map<number, number>();
        allocations.forEach((row, i) => {
          const k = rowKey(row, i);
          const c = rowCons[k] ?? defaultFor(row, usageMappings.find((m) => m.skuId === row.skuId));
          if (c && c.consumableId) {
            const q = Number(c.quantity);
            if (Number.isFinite(q) && q > 0) agg.set(c.consumableId, (agg.get(c.consumableId) ?? 0) + q);
          }
        });
        const payload = Array.from(agg, ([consumable_id, quantity]) => ({ consumable_id, quantity: round3(quantity) }));
        if (payload.length === 0) { onNotify("请为至少一行选择耗材并填写数量"); setSaving(false); return; }
        await procurementChainApi.setInboundConsumableUsage(linkId, true, payload, "采购工作台登记耗材使用");
      } else {
        await procurementChainApi.setInboundConsumableUsage(linkId, false, [], "采购工作台登记耗材使用");
      }
      await onChanged();
      onNotify(usageEnabled ? "耗材使用已登记" : "已标记本次入库不使用耗材");
    } catch (caught) {
      onNotify(caught instanceof Error ? caught.message : "保存耗材失败");
    } finally {
      setSaving(false);
    }
  }

  const usageTag = !decided
    ? { text: "耗材：待登记", className: "font-medium text-amber-600" }
    : items.length > 0
      ? { text: "耗材：有", className: "text-emerald-600" }
      : { text: "耗材：无", className: "text-slate-400" };

  return (
    <div className={cx("rounded-lg border bg-white px-2.5 py-2", decided ? "border-slate-200" : "border-amber-300")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[12px] font-semibold text-slate-800">{doc.goodsdocNo || "已关联入库单"}</div>
          <div className="mt-0.5 truncate text-[11px] text-slate-400">{fmtDateTime(doc.date)} · {doc.warehouseName || "未注仓库"} · <span className={usageTag.className} title="由明细中登记的耗材使用自动标识">{usageTag.text}</span></div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {amountEditing ? (
              <>
                <input autoFocus onFocus={(e) => e.target.select()} value={amountValue}
                  onChange={(e) => setAmountValue(e.target.value)} placeholder={String(doc.amount ?? "")}
                  className="h-7 w-24 rounded border border-indigo-300 px-1.5 text-right text-[12px] tabular-nums outline-none" />
                <button disabled={busy || saving || !amountValue}
                  onClick={() => { onSaveAmount(doc.documentId!, amountValue); setAmountEditing(false); setAmountValue(""); }}
                  className="rounded bg-indigo-600 px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40">保存</button>
                <button onClick={() => { setAmountEditing(false); setAmountValue(""); }}
                  className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:text-slate-600">取消</button>
              </>
            ) : (
              <>
                <span className="text-[12px] font-semibold tabular-nums text-slate-700">{fmtMoney(doc.amount)}</span>
                {doc.documentId ? (
                  <button disabled={busy || closed}
                    onClick={() => { setAmountEditing(true); setAmountValue(String(doc.amount ?? "")); }}
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500 hover:bg-indigo-50 hover:text-indigo-600 disabled:opacity-40"
                    title="更正录错的入库单金额">✎ 改金额</button>
                ) : null}
                {mismatch && (
                  <>
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700 ring-1 ring-amber-200">
                      单据头 {fmtMoney(doc.amount)} ≠ 明细合计 {fmtMoney(doc.itemAmount)}（{doc.itemCount} 行）
                    </span>
                    <button disabled={busy || closed} onClick={() => doc.documentId && onCorrectAmount(doc.documentId)}
                      className="rounded bg-amber-500 px-1.5 py-0.5 text-[11px] font-medium text-white hover:bg-amber-600 disabled:opacity-40"
                      title="以明细金额合计重算单据金额">按明细更正</button>
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {linkId ? (
            <div className="flex items-center gap-1">
              <button disabled={busy || closed} onClick={() => onRequestReplace(linkId)}
                className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-slate-50 hover:text-indigo-600 disabled:opacity-40">更换</button>
              <button disabled={busy || closed} onClick={() => onRemove(linkId)}
                className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-slate-50 hover:text-red-500 disabled:opacity-40">解除</button>
            </div>
          ) : <span className="text-[11px] text-slate-400">历史关联</span>}
        </div>
      </div>

      <div className="mt-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-medium text-slate-500">
            SKU 明细 {allocations.length > 0 ? `(${allocations.length} 行 · ${fmtMoney(skuTotal)})` : ""}
          </span>
          {editable && <button disabled={busy || closed} onClick={() => onAddSku(doc.documentId!)}
            className="rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100 disabled:opacity-40">＋ 新建</button>}
        </div>
        <div className="mt-1 overflow-x-auto">
        <table className="w-full table-fixed border-collapse text-[12px]">
          <colgroup>
            <col style={{ width: "38%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "10%" }} />
          </colgroup>
          <thead>
            <tr className="text-[11px] text-slate-400">
              <th className="border-b border-slate-100 py-1 pr-2 text-left font-medium">SKU 明细</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">数量</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">单价</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">总价</th>
              <th className="border-b border-slate-100 py-1 px-1 text-left font-medium">耗材</th>
              <th className="border-b border-slate-100 py-1 pl-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {allocations.length > 0 ? allocations.map((row, i) => {
              const k = rowKey(row, i);
              const mapping = usageMappings.find((m) => m.skuId === row.skuId);
              const current = rowCons[k] ?? defaultFor(row, mapping);
              const material = current ? usageMaterials.find((m) => m.id === current.consumableId) : null;
              const editableCell = editable && !closed && usageEnabled !== false;
              return (
                <tr key={k} className="whitespace-nowrap border-b border-slate-50 last:border-0">
                  <td className="py-1.5 pr-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="shrink-0 font-mono text-[11px] text-indigo-500">{row.skuCode || "未关联SKU"}</span>
                      <span className="min-w-0 truncate text-[12px] font-medium text-slate-700" title={`${row.skuCode || ""} ${row.goodsName || "未命名商品"}`}>{row.goodsName || "未命名商品"}</span>
                    </div>
                  </td>
                  <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{row.quantity ?? "—"}</td>
                  <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{fmtMoney(row.unitPrice)}</td>
                  <td className="py-1.5 px-1 text-right tabular-nums font-medium text-slate-700">{fmtMoney(row.amount)}</td>
                  <td className="py-1.5 px-1">
                    {usageEnabled === false ? (
                      <span className="text-[11px] text-slate-400">本单不使用</span>
                    ) : (
                      <div className="flex items-center gap-1">
                        <SearchableSelect
                          compact
                          className="flex-1"
                          disabled={!editableCell}
                          ariaLabel="选择耗材"
                          placeholder={`＋ 选择耗材${mapping ? "" : "（手工）"}`}
                          value={current?.consumableId ? String(current.consumableId) : ""}
                          onChange={(next) => {
                            const id = Number(next);
                            if (!id) { setRowCons((p) => ({ ...p, [k]: null })); return; }
                            setRowCons((p) => ({ ...p, [k]: { consumableId: id, quantity: defaultQtyFor(row, id) } }));
                          }}
                          options={usageMaterials.map((m) => ({
                            value: String(m.id), label: `${m.code} · ${m.name}`, keywords: `${m.code} ${m.name}`,
                          }))}
                        />
                        <input value={current?.quantity ?? ""} disabled={!editableCell || !current?.consumableId}
                          onChange={(e) => setRowCons((p) => ({ ...p, [k]: { consumableId: current?.consumableId ?? mapping?.consumableId ?? 0, quantity: e.target.value } }))}
                          placeholder="数量" className="h-6 w-11 shrink-0 rounded border border-slate-200 px-1 text-center text-[11px] tabular-nums outline-none focus:border-amber-400 disabled:bg-slate-50" />
                        <span className="w-6 shrink-0 text-[11px] text-slate-400">{material?.unit}</span>
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pl-2 text-right">
                    {editable && row.id ? (
                      <div className="flex justify-end gap-1.5">
                        <button disabled={busy} onClick={() => onEditAllocation(row, current)} className="text-[11px] text-indigo-600 disabled:opacity-40">修改</button>
                        <button disabled={busy} onClick={() => onRemoveAllocation(row.id as number)} className="text-[11px] text-red-400 hover:text-red-600 disabled:opacity-40">删除</button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              );
            }            ) : (
              <tr><td colSpan={6} className="py-2 text-center text-[11px] text-slate-400">本张单据没有反填 SKU 明细</td></tr>
            )}
            {editor}
          </tbody>
        </table>
        </div>
      </div>

      {/* 未登记时才显示登记表单；已登记的状态见右上角徽标 */}
      {!decided && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded-md bg-amber-50/70 px-2 py-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-600">耗材登记</span>
            <div className="flex gap-1">
              <button type="button" disabled={busy || saving} onClick={() => setUsageEnabled(true)}
                className={cx("rounded border px-1.5 py-0.5 text-[11px]",
                  usageEnabled === true ? "border-amber-400 bg-amber-100 font-semibold text-amber-800" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>有耗材</button>
              <button type="button" disabled={busy || saving} onClick={() => setUsageEnabled(false)}
                className={cx("rounded border px-1.5 py-0.5 text-[11px]",
                  usageEnabled === false ? "border-slate-500 bg-slate-200 font-semibold text-slate-700" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>不使用</button>
            </div>
          </div>
          {linkId && !closed ? (
            <button disabled={saving || busy || usageEnabled === null} onClick={() => void saveConsumables()}
              className="rounded bg-amber-500 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-amber-600 disabled:opacity-40">
              {saving ? "保存中…" : "保存耗材"}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function DetailSection({ title, badge, children }: { title: string; badge?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200/80 bg-white">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[12px] font-semibold text-slate-700">{title}</span>
        <span className="text-[11px] text-slate-400">{badge}</span>
      </div>
      <div className="border-t border-slate-100 px-3 py-2.5">{children}</div>
    </section>
  );
}

function DetailField({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return <div className="min-w-0"><div className="text-[8px] text-slate-400">{label}</div><div className={cx("mt-0.5 truncate text-[12px] text-slate-600", strong && "font-semibold tabular-nums text-slate-800")} title={value}>{value}</div></div>;
}

function StatusSection({ title, status, records, href }: { title: string; status: string; records: string[]; href: string }) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
      <div className="flex items-center justify-between"><span className="text-[12px] font-semibold text-slate-700">{title}</span><Link href={href} className={cx("text-[9.5px]", records.length ? "text-emerald-600" : "text-slate-400")}>{status}</Link></div>
      {records.length > 0 && <div className="mt-1.5 truncate font-mono text-[11px] text-slate-400">{records.slice(0, 2).join("　")}</div>}
    </div>
  );
}

function SupplierList({ suppliers, loading, query, selectedName, onQueryChange, onSelect }: {
  suppliers: WorkbenchSupplierSummary[]; loading: boolean; query: string; selectedName: string | null;
  onQueryChange: (value: string) => void; onSelect: (name: string) => void;
}) {
  return (
    <section className="min-w-0 overflow-x-auto rounded-xl border border-slate-200/90 bg-white shadow-[0_3px_14px_rgba(40,53,85,0.035)]">
      <div className="flex min-w-[700px] items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div><h2 className="text-[13px] font-semibold text-slate-800">供应商管理</h2><p className="mt-0.5 text-[12px] text-slate-400">采购频次、金额、入库与发票状态汇总</p></div>
        <div className="flex w-56 items-center gap-2 rounded-lg bg-slate-50 px-3"><Icon name="search" size={14} /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索供应商" className="h-8 min-w-0 flex-1 bg-transparent text-[11px] outline-none placeholder:text-slate-400" /></div>
      </div>
      <div className="grid min-w-[700px] grid-cols-[minmax(220px,1fr)_90px_130px_110px_110px] gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-2 text-[12px] font-medium text-slate-400">
        <span>供应商</span><span className="text-right">采购次数</span><span className="text-right">累计采购</span><span className="text-right">未入库</span><span className="text-right">最近采购</span>
      </div>
      {loading ? <Loading text="正在加载供应商…" /> : suppliers.length === 0 ? <Empty text="暂无供应商数据" /> : <div className="min-w-[700px] divide-y divide-slate-100">{suppliers.map((supplier) => (
        <button key={supplier.supplierName} onClick={() => onSelect(supplier.supplierName)} className={cx(
          "grid w-full grid-cols-[minmax(220px,1fr)_90px_130px_110px_110px] items-center gap-3 px-4 py-3 text-left transition-colors",
          selectedName === supplier.supplierName ? "bg-indigo-50/60 ring-1 ring-inset ring-indigo-400" : "hover:bg-slate-50"
        )}>
          <span className="truncate text-[12px] font-medium text-slate-700">{supplier.supplierName}</span>
          <span className="text-right text-[11px] tabular-nums text-slate-500">{supplier.orderCount}次</span>
          <span className="text-right text-[11px] font-semibold tabular-nums text-slate-700">{fmtMoney(supplier.totalPurchase)}</span>
          <span className="text-right text-[11px] tabular-nums text-orange-500">{fmtMoney(supplier.uninbound)}</span>
          <span className="text-right text-[12px] text-slate-400">{fmtDate(supplier.lastOrderDate)}</span>
        </button>
      ))}</div>}
    </section>
  );
}

/** 发票状态标签：与发票对账视图（view=tax）口径一致 */
const INVOICE_MATCH_BADGE: Record<string, { text: string; cls: string }> = {
  matched: { text: "已配平", cls: "bg-emerald-50 text-emerald-600" },
  short: { text: "订单不足", cls: "bg-amber-50 text-amber-600" },
};

/** 订单匹配状态：remaining = 该单未被发票覆盖的余量（后端 FIFO 配平结果） */
function orderMatchStatus(order: { orderAmount: number; remaining: number }): { text: string; cls: string; sub: string } {
  if (order.orderAmount <= 0.005) return { text: "金额未同步", cls: "bg-slate-100 text-slate-400", sub: "金额为 0 未参与配平" };
  if (order.remaining <= 0.05) return { text: "已配平", cls: "bg-emerald-50 text-emerald-600", sub: "发票已足额覆盖" };
  const matched = order.orderAmount - order.remaining;
  if (matched > 0.05) return { text: "部分匹配", cls: "bg-sky-50 text-sky-600", sub: "已配 " + fmtMoney(matched) + " · 余 " + fmtMoney(order.remaining) };
  return { text: "待开票", cls: "bg-amber-50 text-amber-600", sub: "无发票覆盖" };
}

function SupplierDetailPanel({ detail, loading, onRenamed }: {
  detail: WorkbenchSupplierDetail | null;
  loading: boolean;
  onRenamed?: (newName: string) => void | Promise<void>;
}) {
  const supplierName = detail?.supplierName ?? null;
  const supplierPartnerId = detail?.partnerId ?? null;
  const [invoiceData, setInvoiceData] = useState<InvoiceReconciliation | null>(null);
  const [invoiceLoading, setInvoiceLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");

  // 供应商改名/归一：统一该供应商全部订单的写法，同名供应商自动合并
  async function saveRename() {
    if (!detail) return;
    const newName = nameDraft.trim();
    if (!newName || newName === detail.supplierName) {
      setEditingName(false);
      setRenameError("");
      return;
    }
    setRenaming(true);
    setRenameError("");
    try {
      await procurementWorkbenchApi.renameSupplier(detail.supplierName, newName);
      setEditingName(false);
      await onRenamed?.(newName);
    } catch (caught) {
      setRenameError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRenaming(false);
    }
  }

  // 发票匹配清单：按当前供应商拉取配平结果（手工关联优先 + FIFO 自动）；手工微调后 onReload 刷新
  const loadInvoiceMatch = useCallback(() => {
    if (!supplierName) {
      setInvoiceData(null);
      return;
    }
    let cancelled = false;
    setInvoiceLoading(true);
    procurementWorkbenchApi.invoiceReconciliation({
      partnerId: supplierPartnerId ?? undefined,
      supplier: supplierName,
    })
      .then((data) => { if (!cancelled) setInvoiceData(data); })
      .catch(() => { if (!cancelled) setInvoiceData(null); })
      .finally(() => { if (!cancelled) setInvoiceLoading(false); });
    return () => { cancelled = true; };
  }, [supplierName, supplierPartnerId]);

  useEffect(() => loadInvoiceMatch(), [loadInvoiceMatch]);

  const invoiceEntry = useMemo(() => {
    const list = invoiceData?.suppliers ?? [];
    if (list.length === 0) return null;
    return list.find((s) => s.supplier === supplierName) ?? list[0];
  }, [invoiceData, supplierName]);

  if (loading && !detail) return <aside className="rounded-xl border border-slate-200 bg-white"><Loading text="正在加载供应商画像…" /></aside>;
  if (!detail) return <aside className="flex min-h-[480px] items-center justify-center rounded-xl border border-slate-200 bg-white text-[12px] text-slate-400">选择供应商查看详情</aside>;
  return (
    <aside className="xl:sticky xl:top-[calc(var(--wb-header-h,162px)+8px)] xl:self-start">
      <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-[0_4px_18px_rgba(40,53,85,0.04)]">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] text-slate-400">供应商画像</div>
            {editingName ? (
              <input
                autoFocus
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveRename();
                  if (e.key === "Escape") { setEditingName(false); setRenameError(""); }
                }}
                className="mt-1 w-full rounded-md border border-indigo-200 px-2 py-1 text-[14px] font-semibold text-slate-800 outline-none focus:border-indigo-400"
              />
            ) : (
              <h2 className="mt-1 text-[15px] font-semibold text-slate-800">{detail.supplierName}</h2>
            )}
          </div>
          {editingName ? (
            <span className="flex shrink-0 gap-1.5 pt-4">
              <button disabled={renaming} onClick={() => void saveRename()} className="rounded-md bg-indigo-500 px-2.5 py-1 text-[11px] text-white hover:bg-indigo-600 disabled:opacity-50">{renaming ? "保存中…" : "保存"}</button>
              <button onClick={() => { setEditingName(false); setRenameError(""); }} className="rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-50">取消</button>
            </span>
          ) : (
            <button onClick={() => { setNameDraft(detail.supplierName); setEditingName(true); }} className="mt-3 shrink-0 text-[11px] text-indigo-500 hover:underline">改名</button>
          )}
        </div>
        {editingName && (
          <p className="mt-1 text-[10px] leading-4 text-slate-400">
            该供应商全部订单将统一改为新名称；若与现有供应商同名会自动合并（发票卖方名是税务事实不受影响）。回车保存，Esc 取消。
          </p>
        )}
        {renameError && <p className="mt-1 text-[11px] text-red-500">{renameError}</p>}
        <div className="mt-4 grid grid-cols-2 gap-2">
          <SupplierMetric label="采购次数" value={detail.orderCount + "次"} tone="indigo" />
          <SupplierMetric label="累计采购" value={fmtMoney(detail.totalPurchase)} tone="indigo" />
          <SupplierMetric label="未开票金额" value={fmtMoney(detail.uninvoiced)} tone="amber" />
          <SupplierMetric label="未入库金额" value={fmtMoney(detail.uninbound)} tone="amber" />
        </div>
        <div className="mt-3 rounded-lg border border-slate-100 p-3">
          <div className="text-[12px] font-semibold text-slate-700">常购SKU</div>
          <div className="mt-2 space-y-1.5">{detail.oftenSkus.length > 0 ? detail.oftenSkus.slice(0, 6).map((sku) => (
        <div key={sku.skuCode} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-2 text-[12px]"><span className="truncate text-slate-600">{sku.goodsName || "未命名商品"}</span><span className="shrink-0 font-mono text-indigo-500">{sku.skuCode} · {sku.count}次</span></div>
          )) : <div className="py-4 text-center text-[12px] text-slate-400">暂无常购SKU</div>}</div>
        </div>
        <div className="mt-3 rounded-lg border border-slate-100 p-3">
          <div className="text-[12px] font-semibold text-slate-700">最近采购订单</div>
          <div className="mt-2 space-y-1.5">{detail.recentOrders.length > 0 ? detail.recentOrders.slice(0, 6).map((order) => (
        <div key={order.orderId} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-2 text-[12px]"><span className="truncate font-mono text-indigo-500">{order.orderNo}</span><span className="shrink-0 text-slate-400">{fmtDate(order.orderDate)}　{fmtMoney(order.amount)}</span></div>
          )) : <div className="py-4 text-center text-[12px] text-slate-400">暂无历史订单</div>}</div>
        </div>
        <SupplierInvoiceMatchSection
          entry={invoiceEntry}
          loading={invoiceLoading && !invoiceEntry}
          hasData={invoiceData != null}
          onReload={loadInvoiceMatch}
        />
      </div>
    </aside>
  );
}

function SupplierInvoiceMatchSection({ entry, loading, hasData, onReload }: {
  entry: InvoiceReconciliation["suppliers"][number] | null;
  loading: boolean;
  hasData: boolean;
  onReload: () => void;
}) {
  const [adjustingId, setAdjustingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  // 手工微调：把订单挂到发票（manual 关联落库）后整票以手工为准，不再自动 FIFO
  async function addMatch(invoiceId: number, poId: number) {
    setBusy(true);
    try {
      await procurementWorkbenchApi.createInvoiceMatch(invoiceId, poId);
      setAdjustingId(null);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  async function removeLink(linkId: number) {
    setBusy(true);
    try {
      await procurementWorkbenchApi.deleteInvoiceMatch(linkId);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  const matchCandidates = (entry?.orders ?? []).filter((o) => o.remaining > 0.05);

  return (
    <div className="mt-3 rounded-lg border border-slate-100 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold text-slate-700">发票匹配清单</div>
        {entry && (
          <NextLink href={`/purchase/workbench?view=tax`} className="text-[11px] text-indigo-500 hover:underline">全量对账 →</NextLink>
        )}
      </div>
      <p className="mt-1 text-[10px] leading-4 text-slate-400">按开票日期配平：每张发票只自动匹配开票日当天及之前的采购订单；符合日期条件的订单再按下单时间从早到晚累计，一张发票可覆盖多张订单。手工调整优先并落库。</p>
      {loading ? (
        <div className="flex items-center gap-2 py-4 text-[12px] text-slate-400"><span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" />正在匹配发票…</div>
      ) : !entry ? (
        <div className="py-4 text-center text-[12px] text-slate-400">
          {hasData ? "该供应商暂无已开票的进项发票" : "暂无发票数据"}
          <div className="mt-1 text-[11px] text-slate-300">可到「税务发票」页导入发票清单</div>
        </div>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-3 gap-1.5 text-center">
            <div className="rounded-md bg-slate-50 px-1 py-1.5"><div className="text-[10px] text-slate-400">发票合计</div><div className="text-[12px] font-semibold tabular-nums text-slate-700">{entry.invoiceCount}张 · {fmtMoney(entry.invoiceTotal)}</div></div>
            <div className="rounded-md bg-emerald-50/70 px-1 py-1.5"><div className="text-[10px] text-slate-400">已配平</div><div className="text-[12px] font-semibold tabular-nums text-emerald-600">{fmtMoney(entry.matchedTotal)}</div></div>
            <div className="rounded-md bg-amber-50/70 px-1 py-1.5"><div className="text-[10px] text-slate-400">待开票订单</div><div className="text-[12px] font-semibold tabular-nums text-amber-600">{entry.remainingOrders}单 · {fmtMoney(entry.remainingOrderTotal)}</div></div>
          </div>
          {entry.months.length === 0 ? (
            <div className="py-3 text-center text-[12px] text-slate-400">该供应商暂无进项发票</div>
          ) : entry.months.map((month) => (
            <div key={month.month} className="mt-2">
              <div className="text-[11px] font-medium text-slate-500">{month.month === "未知月份" ? month.month : month.month + " 开票"}</div>
              <div className="mt-1 space-y-1.5">
                {month.invoices.map((inv) => {
                  const badge = INVOICE_MATCH_BADGE[inv.status] ?? { text: inv.status, cls: "bg-slate-100 text-slate-500" };
                  return (
                    <div key={inv.invoiceId} className="rounded-md bg-slate-50 px-2.5 py-2 text-[12px]">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-indigo-600">{inv.invoiceNo || "无发票号"}</span>
                        <span className="flex shrink-0 items-center gap-1">
                          {inv.manualLinked ? (
                            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-500">手动</span>
                          ) : inv.explicitLinked ? (
                            <span className="rounded bg-sky-50 px-1.5 py-0.5 text-[10px] text-sky-600">清单关联</span>
                          ) : null}
                          <span className={"rounded px-1.5 py-0.5 text-[10px] " + badge.cls}>{inv.shortReason === "explicit_link_issue" ? "待核对" : badge.text}</span>
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        开票 {fmtDate(inv.issueDate)} · 票面 {fmtMoney(inv.amount)}
                        {inv.coveredTotal > 0 && <> · 已配订单 {fmtMoney(inv.coveredTotal)}</>}
                        {inv.status === "short" && <> · 差额 {fmtMoney(Math.abs(inv.diff))}</>}
                      </div>
                      {inv.status === "short" && inv.shortReason === "date_cutoff" && (
                        <div className="mt-1 rounded bg-amber-50 px-2 py-1 text-[10px] leading-4 text-amber-700">
                          截至 {fmtDate(inv.issueDate)} 的订单金额不足；后续订单不参与本票自动匹配
                          {inv.futureOrderCount ? `（后续/日期待确认订单 ${inv.futureOrderCount} 单）` : ""}。
                        </div>
                      )}
                      {inv.status === "short" && inv.shortReason === "insufficient_orders" && (
                        <div className="mt-1 rounded bg-amber-50 px-2 py-1 text-[10px] leading-4 text-amber-700">
                          截至开票日可用采购订单金额不足，请核对是否存在漏单、金额未同步或供应商名称不一致。
                        </div>
                      )}
                      {inv.status === "short" && inv.shortReason === "explicit_link_issue" && (
                        <div className="mt-1 rounded bg-rose-50 px-2 py-1 text-[10px] leading-4 text-rose-700">
                          已有明确关联，但分摊金额或采购单映射不完整，请先核对关联关系；系统不会回退到 FIFO 猜单。
                        </div>
                      )}
                      {inv.covered.length === 0 ? (
                        <div className="mt-1 text-[11px] text-amber-600">
                          {inv.shortReason === "date_cutoff"
                            ? "截至开票日暂无可用采购订单；系统不会把之后下单的订单倒挂到此前发票"
                            : inv.shortReason === "explicit_link_issue"
                            ? "明确关联尚未形成可用分摊，请核对订单映射或分摊金额"
                            : "未匹配到可用采购订单（订单池为空或金额已耗尽）"}
                        </div>
                      ) : inv.covered.map((order) => (
                        <div key={(order.linkId ?? "a") + "-" + order.orderId} className="mt-1 flex items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px]">
                          <span className="flex min-w-0 items-center gap-1">
                            <span className={"shrink-0 rounded px-1 py-px text-[9px] " + (order.source === "manual" ? "bg-indigo-50 text-indigo-500" : order.source === "source_ref" ? "bg-sky-50 text-sky-600" : "bg-slate-100 text-slate-400")}>{order.source === "manual" ? "手动" : order.source === "source_ref" ? "清单" : "自动"}</span>
                            <span className="truncate font-mono text-slate-500">{order.orderNo || "无单号"}</span>
                          </span>
                          <span className="flex shrink-0 items-center gap-1.5 text-slate-400">
                            {fmtDate(order.date)} · 消耗 {fmtMoney(order.consumed)}{order.allocatedAmount != null && order.allocatedAmount !== order.consumed ? <span className="text-rose-500">（关联 {fmtMoney(order.allocatedAmount)}）</span> : null}{order.partial && <span className="text-amber-500">（部分）</span>}
                            {order.source === "manual" && order.linkId != null && (
                              <button disabled={busy} onClick={() => removeLink(order.linkId!)} className="text-red-400 hover:text-red-500">移除</button>
                            )}
                          </span>
                        </div>
                      ))}
                      <div className="mt-1 flex items-center justify-between gap-2">
                        <span className="text-[10px] text-slate-300">{inv.manualLinked ? "已按手工关联配平；全部移除后恢复清单/FIFO规则" : inv.explicitLinked ? "已按税务清单明确关联；如需纠正可手动指定订单覆盖" : "自动匹配结果不对？可手动指定订单"}</span>
                        <button disabled={busy} onClick={() => setAdjustingId(adjustingId === inv.invoiceId ? null : inv.invoiceId)} className="shrink-0 text-[11px] text-indigo-500 hover:underline">
                          {adjustingId === inv.invoiceId ? "收起" : "调整匹配"}
                        </button>
                      </div>
                      {adjustingId === inv.invoiceId && (
                        <div className="mt-1 rounded-md border border-indigo-100 bg-indigo-50/40 p-2">
                          <div className="text-[10px] text-slate-500">手工调整可覆盖自动日期规则：点选有余量的订单，可连续添加多单，直到本票金额配平。</div>
                          <div className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                            {matchCandidates.length === 0 ? (
                              <div className="py-2 text-center text-[11px] text-slate-400">该供应商没有可关联的订单余量</div>
                            ) : matchCandidates.map((o) => (
                              <button key={o.orderId} disabled={busy} onClick={() => addMatch(inv.invoiceId, o.orderId)} className="flex w-full items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px] hover:bg-indigo-50 disabled:opacity-50">
                                <span className="truncate font-mono text-indigo-600">{o.orderNo || "无单号"}</span>
                                <span className="shrink-0 text-slate-400">{fmtDate(o.date)} · 余 {fmtMoney(o.remaining)}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {entry.orders.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] font-medium text-slate-500">全部采购订单匹配状态</div>
                <div className="text-[10px] text-slate-400">共 {entry.orders.length} 单 · 按下单时间排序</div>
              </div>
              <div className="mt-1 max-h-[280px] space-y-1 overflow-y-auto pr-0.5">
                {entry.orders.map((order) => {
                  const st = orderMatchStatus(order);
                  return (
                    <div key={order.orderId} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-[12px]">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-indigo-500">{order.orderNo || "无单号"}</div>
                        <div className="text-[10px] text-slate-400">{fmtDate(order.date)}{order.platform ? " · " + order.platform : ""}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <div className="text-right">
                          <div className="text-[12px] font-semibold tabular-nums text-slate-700">{fmtMoney(order.orderAmount)}</div>
                          <div className="text-[10px] text-slate-400">{st.sub}</div>
                        </div>
                        <span className={"shrink-0 rounded px-1.5 py-0.5 text-[10px] " + st.cls}>{st.text}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SupplierMetric({ label, value, tone }: { label: string; value: string; tone: "indigo" | "amber" }) {
  return <div className={cx("rounded-lg px-3 py-2.5", tone === "indigo" ? "bg-indigo-50/60" : "bg-amber-50/60")}><div className="text-[11px] text-slate-400">{label}</div><div className={cx("mt-1 text-[12px] font-semibold tabular-nums", tone === "indigo" ? "text-indigo-700" : "text-amber-700")}>{value}</div></div>;
}

function Loading({ text }: { text: string }) {
  return <div className="flex min-h-[160px] items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-[11px] text-slate-400"><span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" />{text}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="flex min-h-[160px] items-center justify-center rounded-xl border border-slate-200 bg-white text-[11px] text-slate-400">{text}</div>;
}

function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, string> = {
    dashboard: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    sales: "M4 19V9m5 10V5m6 14v-7m5 7V3",
    box: "M4 7l8-4 8 4-8 4-8-4zm0 0v10l8 4 8-4V7M12 11v10",
    purchase: "M3 4h2l2 12h11l2-8H7m2 12h.01M18 20h.01",
    orders: "M6 3h12v18H6zM9 8h6M9 12h6M9 16h4",
    calendar: "M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2zm2-2v4m10-4v4M3 9h18",
    users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm9 4a4 4 0 0 1 4 4v2m-6-10a4 4 0 0 0 0-8",
    wallet: "M3 6h16a2 2 0 0 1 2 2v11H3zM3 6V4h14v2m0 7h4",
    reconcile: "M5 7h14M5 12h9M5 17h6m6-2 2 2 3-4",
    receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2V3zm3 5h6m-6 4h6m-6 4h4",
    cloud: "M7 18h11a4 4 0 0 0 .5-8A6 6 0 0 0 7.2 8.2 5 5 0 0 0 7 18zm5-8v7m-3-3 3 3 3-3",
    chart: "M4 20V10m5 10V4m6 16v-7m5 7H2",
    settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm0-13v2m0 15v2m9.5-9.5h-2m-15 0h-2m16.2-6.2-1.4 1.4M6.7 17.3l-1.4 1.4m13.4 0-1.4-1.4M6.7 6.7 5.3 5.3",
    import: "M12 3v12m-4-4 4 4 4-4M5 21h14",
    download: "M12 3v12m-4-4 4 4 4-4M5 21h14",
    plus: "M12 5v14M5 12h14",
    sync: "M20 7h-5V2M4 17h5v5M20 7a8 8 0 0 0-13-3M4 17a8 8 0 0 0 13 3",
    magic: "m5 19 14-14M15 5l4 4M5 4v3M3.5 5.5h3M18 16v4M16 18h4",
    refresh: "M20 6v5h-5M4 18v-5h5M19 11a7 7 0 0 0-12-4M5 13a7 7 0 0 0 12 4",
    filter: "M4 5h16l-6 7v6l-4 2v-8z",
    search: "m21 21-4.5-4.5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z",
    copy: "M9 9h11v11H9zM5 15V5h10",
    chevron: "m8 10 4 4 4-4",
    history: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm1-13v5l4 2 1-1.5-3.5-1.8V8H13z",
    "alert-circle": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-12v5m0 3v.01",
    edit: "M4 20h4l10.5-10.5a2.1 2.1 0 0 0-4-4L4 16v4zm9.5-13.5 4 4",
    x: "M18 6 6 18M6 6l12 12",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0"><path d={paths[name]} stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function ChainPanel({ overview, orders, total, pending, filter, loading, busy, prelinkBusy, xrefBusy, onFilterChange, onConfirm, onReject, onPrelink, onManualLink, onReload, onNotice, onStageView }: {
  overview: ChainOverview | null;
  orders: ChainOrderRow[];
  total: number;
  pending: PendingLink[];
  filter: ChainFilter;
  loading: boolean;
  busy: boolean;
  prelinkBusy: boolean;
  xrefBusy: boolean;
  onFilterChange: (value: ChainFilter) => void;
  onConfirm: (kind: string, linkId: number) => Promise<void>;
  onReject: (kind: string, linkId: number) => Promise<void>;
  onPrelink: (auto: boolean) => Promise<void>;
  onManualLink: (orderId: number, targetId: number) => Promise<void>;
  onReload?: () => Promise<void>;
  onNotice?: (text: string) => void;
  onStageView: (view: ViewMode) => void;
}) {
  const filtered = useMemo(() => {
    if (filter === "gap") return orders.filter((row) => row.doneCount < (row.stageTotal || 7));
    if (filter === "full") return orders.filter((row) => row.doneCount >= (row.stageTotal || 7));
    return orders;
  }, [filter, orders]);

  // 逐单候选展开：orderId -> 已加载候选
  const [candByOrder, setCandByOrder] = useState<Record<number, ChainLinkCandidate[]>>({});
  const [candLoadingId, setCandLoadingId] = useState<number | null>(null);
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({});
  async function toggleCandidates(row: ChainOrderRow) {
    const orderId = canonicalChainOrderId(row);
    if (candByOrder[orderId]) {
      setCandByOrder((prev) => { const next = { ...prev }; delete next[orderId]; return next; });
      return;
    }
    setCandLoadingId(orderId);
    try {
      const result = await procurementChainApi.candidates(orderId, "inbound", "", 8);
      setCandByOrder((prev) => ({ ...prev, [orderId]: result.items }));
    } catch {
      setCandByOrder((prev) => ({ ...prev, [orderId]: [] }));
    } finally {
      setCandLoadingId(null);
    }
  }
  async function linkCandidate(orderId: number, targetId: number) {
    await onManualLink(orderId, targetId);
    setCandByOrder((prev) => { const next = { ...prev }; delete next[orderId]; return next; });
  }

  // 对照表面板
  const [xrefOpen, setXrefOpen] = useState(false);
  const [xrefContent, setXrefContent] = useState("");
  const [xrefPreview, setXrefPreview] = useState<XrefPreview | null>(null);
  const [xrefApplyResult, setXrefApplyResult] = useState<XrefApplyResult | null>(null);
  const [xrefFileMeta, setXrefFileMeta] = useState<{ modifiedAt: number | null; size: number } | null>(null);
  async function openXrefPanel() {
    if (!xrefOpen) {
      try {
        const meta = await procurementChainApi.xrefFile();
        setXrefContent(meta.content || "");
        setXrefFileMeta({ modifiedAt: meta.modifiedAt, size: meta.size });
      } catch {
        setXrefContent("");
      }
    }
    setXrefOpen((v) => !v);
    setXrefPreview(null);
    setXrefApplyResult(null);
  }
  async function runXrefPreview() {
    if (!xrefContent.trim()) return;
    try {
      const result = await procurementChainApi.xrefPreview(xrefContent);
      setXrefPreview(result);
      setXrefApplyResult(null);
      onNotice?.(`预览完成：将创建 ${result.stats.to_create} 条，缺 ${result.stats.missing_order} 单 / ${result.stats.missing_rk} RK`);
    } catch (caught) {
      onNotice?.("对照表预览失败：" + String(caught));
    }
  }
  async function runXrefApply() {
    if (!xrefContent.trim()) return;
    try {
      const result = await procurementChainApi.xrefApply(xrefContent, true);
      setXrefApplyResult(result);
      setXrefPreview(null);
      onNotice?.(`对照表已应用：新建 ${result.created} 条链` + (result.missing_order.length || result.missing_rk.length
        ? `；缺 ${result.missing_order.length} 单 / ${result.missing_rk.length} RK` : ""));
      await onReload?.();
    } catch (caught) {
      onNotice?.("对照表应用失败：" + String(caught));
    }
  }

  return (
    <div className="mt-5 space-y-4">
      <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
        <div className="flex flex-wrap items-stretch gap-1.5">
          {(overview?.stages ?? []).map((stage) => {
        const done = stage.count > 0;
        const action = STAGE_ACTION[stage.key];
        const body = (
          <>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className={cx("inline-block h-1.5 w-1.5 shrink-0 rounded-full", DIM_DOT[stage.dimension] ?? "bg-slate-300")} />
                <span className={cx("text-[11px]", done ? "text-indigo-400" : "text-slate-400")}>
                  {CIRCLED[stage.no - 1] ?? stage.no} {stage.short}
                </span>
              </div>
              <div className={cx("mt-0.5 text-lg font-semibold leading-6", done ? "text-indigo-700" : "text-slate-400")}>
                {stage.count}
                <span className="ml-1 text-[11px] font-normal text-slate-400">{stage.pct}%</span>
              </div>
              <div className="truncate text-[12px] text-slate-400" title={stage.label}>{stage.label}</div>
            </div>
            <span className={cx("h-2 w-2 shrink-0 rounded-full", done ? "bg-indigo-500" : "bg-slate-200")} />
          </>
        );
        const shell = cx(
          "group flex min-w-[112px] flex-1 items-center justify-between rounded-lg px-3 py-2.5 transition-colors",
          done ? "bg-indigo-50/80 hover:bg-indigo-50" : "bg-slate-50 hover:bg-slate-100"
        );
        return action?.view ? (
          <button key={stage.key} type="button" onClick={() => onStageView(action.view as ViewMode)} className={shell}>{body}</button>
        ) : (
          <Link key={stage.key} href={action?.href ?? "#"} className={shell}>{body}</Link>
        );
          })}
          <div className="flex min-w-[104px] flex-1 items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
        <div>
          <div className="text-[11px] text-amber-500">待确认</div>
          <div className="mt-0.5 text-lg font-semibold leading-6 text-amber-600">{overview?.pending ?? 0}</div>
        </div>
        {Number(overview?.pending ?? 0) > 0 && (
          <span className="animate-pulse rounded-full bg-amber-400 px-1.5 py-0.5 text-[12px] font-medium text-white">需处理</span>
        )}
          </div>
        </div>
      </section>

      <QuickTriage
        orders={orders}
        pending={pending}
        busy={busy}
        onConfirm={onConfirm}
        onManualInbound={onManualLink}
        onReload={onReload}
        onNotice={onNotice}
      />

      <section className="rounded-xl border border-amber-200 bg-amber-50/40 p-3.5">
        <div className="mb-2.5 flex items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-amber-400" />
          <h2 className="text-[12px] font-semibold text-amber-800">关联待确认</h2>
          <span className="text-[11px] text-amber-500">{pending.length} 条建议</span>
        </div>
        {pending.length === 0 ? (
          <div className="rounded-lg border border-white bg-white/70 px-3 py-3 text-center text-[11px] text-amber-600/70">暂无待确认的关联建议；顶部「自动匹配并确认」会继续处理新数据</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
        {pending.map((item) => (
          <div key={item.kind + "-" + item.linkId} className="flex items-center justify-between gap-3 rounded-lg border border-amber-100 bg-white px-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="truncate font-mono text-[11px] font-medium text-slate-800">{item.orderNo}</span>
                <span className="text-slate-300">→</span>
                <span className="truncate font-mono text-[11px] font-medium text-indigo-600">{item.targetNo}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[12px] text-slate-400">
                <span className="rounded bg-slate-50 px-1 py-px text-slate-500">
                  {item.targetType === "inbound" ? "入库" : item.targetType === "settlement" ? "付款" : "发票"}
                </span>
                {item.confidence !== null && <span>置信度 {(item.confidence * 100).toFixed(0)}%</span>}
                {item.targetAmount !== null && <span>{fmtMoney(item.targetAmount)}</span>}
                {item.note && <span className="truncate">{item.note}</span>}
              </div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button disabled={busy} onClick={() => void onConfirm(item.kind, item.linkId)} className="rounded-md bg-indigo-600 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50">确认</button>
              <button disabled={busy} onClick={() => void onReject(item.kind, item.linkId)} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] text-slate-500 hover:bg-slate-50 disabled:opacity-50">拒绝</button>
            </div>
          </div>
        ))}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <div className="flex items-center gap-2">
        <span className="h-3.5 w-1 rounded-full bg-indigo-600" />
        <h2 className="text-[12px] font-semibold text-slate-800">订单链路</h2>
        <span className="text-[11px] text-slate-400">共 {total} 条 · 每单 7 环节完成度</span>
          </div>
          <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={prelinkBusy}
          onClick={() => void onPrelink(true)}
          title="多因子打分（SKU 重合+供应商+时间窗）：达到阈值的候选自动关联并确认；耗材按正品实际入库数量和映射自动扣减。一笔订单可拆批关联多张入库单。"
          className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors disabled:opacity-50",
            "bg-indigo-600 text-white hover:bg-indigo-700"
          )}
        >
          {prelinkBusy ? "自动处理中…" : "⚡ 智能预关联并确认"}
        </button>
        <button
          type="button"
          onClick={openXrefPanel}
          className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
            xrefOpen
              ? "bg-violet-600 text-white"
              : "border border-violet-200 bg-violet-50 text-violet-600 hover:bg-violet-100"
          )}
          title="维护 1688 ↔ 入库单 手动交叉对照表：粘贴/编辑后点「解析预览」查看将创建/缺失明细，确认无误后点「应用」批量建链（同时落盘到外部 DATA_DIR）"
        >
          {xrefOpen ? "收起对照表" : "📋 对照表"}
        </button>
        {([["all", "全部"], ["gap", "有缺口"], ["full", "已完成"]] as Array<[ChainFilter, string]>).map(([key, label]) => (
          <button key={key} onClick={() => onFilterChange(key)} className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
            filter === key ? "bg-indigo-600 text-white" : "bg-slate-50 text-slate-500 hover:bg-slate-100"
          )}>{label}</button>
        ))}
          </div>
        </div>
        {xrefOpen && (
          <section className="mt-3 rounded-xl border border-violet-200 bg-violet-50/30 p-3.5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-violet-500" />
          <h3 className="text-[12px] font-semibold text-violet-800">1688↔入库单 对照表</h3>
          <span className="text-[12px] text-violet-500">
            粘贴或编辑 TSV（19 列：年度 订货日期 订单号 条形码 … 备注）后点「解析预览」查看将创建/缺失明细，确认后点「应用」批量建链并落盘。
          </span>
          {xrefFileMeta?.modifiedAt != null && (
            <span className="text-[12px] text-slate-400">
              当前文件 {Math.round((xrefFileMeta.size || 0) / 1024)} KB · 修改 {fmtDateTime(new Date(xrefFileMeta.modifiedAt * 1000).toISOString())}
            </span>
          )}
        </div>
        <textarea
          value={xrefContent}
          onChange={(e) => setXrefContent(e.target.value)}
          placeholder={"年度\t订货日期\t订单号\t条形码\t…\n2025年\t2025/7/23\t4648732165543821020\t2020240528007\t…"}
          className="h-48 w-full resize-y rounded-md border border-violet-100 bg-white px-2 py-1.5 font-mono text-[12px] text-slate-800 focus:border-violet-400 focus:outline-none"
          spellCheck={false}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={xrefBusy || !xrefContent.trim()}
            onClick={() => void runXrefPreview()}
            className="rounded-md border border-violet-200 bg-white px-2.5 py-1 text-[12px] font-medium text-violet-700 hover:bg-violet-50 disabled:opacity-50"
          >解析预览</button>
          <button
            type="button"
            disabled={xrefBusy || !xrefContent.trim()}
            onClick={() => void runXrefApply()}
            className="rounded-md bg-violet-600 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-violet-700 disabled:opacity-50"
          >应用（写入并落盘）</button>
          <button
            type="button"
            onClick={async () => {
              const meta = await procurementChainApi.xrefFile();
              setXrefContent(meta.content || "");
              setXrefFileMeta({ modifiedAt: meta.modifiedAt, size: meta.size });
              setXrefPreview(null);
              setXrefApplyResult(null);
              onNotice?.("已重新加载文件内容");
            }}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] text-slate-500 hover:bg-slate-50"
          >从文件重新加载</button>
          <span className="text-[12px] text-slate-400">已写入字节：{xrefContent.length.toLocaleString()}</span>
        </div>
        {xrefPreview && (
          <div className="mt-2 rounded-md border border-slate-100 bg-white p-2 text-[12px] text-slate-700">
            <div className="flex flex-wrap gap-3">
              <span>总行 {xrefPreview.totalXrefRows} / 唯一对 {xrefPreview.totalPairs}</span>
              <span className="text-emerald-600">将创建 {xrefPreview.stats.to_create}</span>
              <span className="text-slate-500">已存在 {xrefPreview.stats.already_linked}</span>
              <span className="text-amber-600">缺订单 {xrefPreview.stats.missing_order} / 缺RK {xrefPreview.stats.missing_rk}</span>
              <span className="text-slate-500">组合装 {xrefPreview.stats.combo_skipped}</span>
              {xrefPreview.noiseLinkCount > 0 && <span className="text-rose-600">噪声链 {xrefPreview.noiseLinkCount}</span>}
            </div>
            {xrefPreview.missingOrder.length > 0 && (
              <div className="mt-1 text-rose-600">缺 1688 订单：{xrefPreview.missingOrder.join("、") || "—"}</div>
            )}
            {xrefPreview.missingRk.length > 0 && (
              <div className="mt-1 text-rose-600">缺 RK 入库单：{xrefPreview.missingRk.join("、") || "—"}</div>
            )}
            {xrefPreview.toCreate.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-slate-500">查看前 {xrefPreview.toCreate.length} 条将创建</summary>
                <div className="mt-1 max-h-32 overflow-y-auto font-mono text-[12px] text-slate-600">
                  {xrefPreview.toCreate.map((c, i) => (
                    <div key={i}>{c.order_no} → {c.rk_no}　SKUs {c.barcodes.join("+")}　qty盒={c.qty_boxes}　{c.note_extra && `备注：${c.note_extra.slice(0, 30)}`}</div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
        {xrefApplyResult && (
          <div className="mt-2 rounded-md border border-emerald-100 bg-emerald-50/50 p-2 text-[12px] text-emerald-800">
            <div className="flex flex-wrap gap-3">
              <span>本次新建 {xrefApplyResult.created} 条链</span>
              <span className="text-slate-500">对照表行 {xrefApplyResult.total_xref_rows} / 对 {xrefApplyResult.total_pairs}</span>
              <span className="text-slate-500">已存在 {xrefApplyResult.already_linked_count}</span>
              <span className="text-slate-500">组合装 {xrefApplyResult.combo_skipped_count}</span>
              {(xrefApplyResult.missing_order.length || xrefApplyResult.missing_rk.length) > 0 && (
                <span className="text-amber-700">缺 {xrefApplyResult.missing_order.length} 单 / {xrefApplyResult.missing_rk.length} RK（需先同步入库/订单）</span>
              )}
              {xrefApplyResult.noise_links.length > 0 && <span className="text-rose-700">噪声链 {xrefApplyResult.noise_links.length} 条（建议人工拒绝）</span>}
            </div>
            {xrefApplyResult.noise_links.length > 0 && (
              <div className="mt-1">id 列表：{xrefApplyResult.noise_links.map((n) => `${n.id}`).join("、")}</div>
            )}
          </div>
        )}
          </section>
        )}
        {loading ? <Loading text="正在加载采购链路…" /> : filtered.length === 0 ? <Empty text="没有符合条件的订单链路" /> : (
          <div className="divide-y divide-slate-100">
        {filtered.map((row) => {
          const chainOrderId = canonicalChainOrderId(row);
          const stageTotal = row.stageTotal || 7;
          const pct = Math.round((row.doneCount / stageTotal) * 100);
          return (
            <div key={row.orderId} className="px-4 py-3 hover:bg-slate-50/60">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-[11.5px] font-semibold text-slate-800">{row.orderNo}</span>
                    {row.pendingCount > 0 && (
                      <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[12px] font-medium text-amber-600">{row.pendingCount} 条待确认</span>
                    )}
                    {row.source === "workflow" && (
                      <span className="shrink-0 rounded bg-violet-50 px-1.5 py-0.5 text-[12px] font-medium text-violet-600">手工</span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-[12px] text-slate-400">
                    {row.supplier || "—"}　·　{fmtDate(row.orderDate)}　·　{row.title || "无标题"}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  <div className="text-right">
                    <div className="text-[13px] font-semibold tabular-nums text-slate-900">{fmtMoney(row.amount)}</div>
                    <div className="text-[12px] text-slate-400">完成度 {row.doneCount}/{stageTotal}</div>
                  </div>
                  <div className="w-24">
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div className={cx("h-full rounded-full", row.doneCount >= stageTotal ? "bg-emerald-500" : "bg-indigo-500")} style={{ width: pct + "%" }} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1">
                {row.stages.map((stage) => (
                  <span key={stage.key} title={stage.label} className={cx(
                    "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px]",
                    stage.done ? "bg-emerald-50 text-emerald-600" : "bg-slate-50 text-slate-400"
                  )}>
                    <span className={cx("inline-block h-1.5 w-1.5 rounded-full", stage.done ? "bg-emerald-500" : DIM_DOT[stage.dimension] ?? "bg-slate-300")} />
                    {CIRCLED[stage.no - 1] ?? stage.no} {stage.short}
                  </span>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy || candLoadingId === chainOrderId}
                  onClick={() => void toggleCandidates(row)}
                  className="rounded-md border border-indigo-100 bg-indigo-50/60 px-2 py-0.5 text-[12px] font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-50"
                >
                  {candLoadingId === chainOrderId ? "加载中…" : candByOrder[chainOrderId] ? "收起候选" : "候选入库单"}
                </button>
                {(row.inbound ?? []).length > 0 && (
                  <span className="text-[12px] text-slate-400">
                    已关联 {row.inbound.length} 张：{row.inbound.map((i) => i.goodsdocNo).join("、")}
                  </span>
                )}
              </div>
              {candByOrder[chainOrderId] && (
                <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/60 p-2">
                  {candByOrder[chainOrderId].length === 0 ? (
                    <div className="px-1 py-1 text-[12px] text-slate-400">没有可用的入库单候选（可能是全新单据，请先同步或文件导入入库单）</div>
                  ) : (
                    <div className="divide-y divide-slate-100">
                      {candByOrder[chainOrderId].map((c) => (
                        <div key={c.targetId} className="flex items-start justify-between gap-3 py-1.5">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="font-mono text-[12px] font-semibold text-slate-800">{c.targetNo}</span>
                              {c.currentlyLinked && <span className="rounded bg-emerald-50 px-1 text-[11px] text-emerald-600">已关联</span>}
                              {c.pendingSuggestion && <span className="rounded bg-amber-50 px-1 text-[11px] text-amber-600">待确认建议</span>}
                              {c.skuOverlapRatio !== null && c.skuOverlapRatio > 0 && (
                                <span className="rounded bg-indigo-50 px-1 text-[11px] text-indigo-600">SKU 重合 {c.matchedSkuCount}/{c.requiredSkuCount}</span>
                              )}
                              <span className={cx("rounded px-1 text-[11px]", c.score >= 0.5 ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400")}>
                                匹配 {(c.score * 100).toFixed(0)}%
                              </span>
                            </div>
                            <div className="mt-0.5 text-[12px] text-slate-400">
                              {c.targetSupplier || "无供应商"} · {fmtDate(c.targetDate)} · {c.reason}
                            </div>
                            {c.details.length > 0 && (() => {
                              const open = expandedDetails[`${chainOrderId}:${c.targetId}`];
                              return (
                                <div className="mt-1">
                                  <button
                                    type="button"
                                    onClick={() => setExpandedDetails((m) => ({ ...m, [`${chainOrderId}:${c.targetId}`]: !open }))}
                                    className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
                                  >
                                    📦 明细 ({c.details.length}) {open ? "▴" : "▾"}
                                  </button>
                                  {open && (
                                    <div className="mt-1 overflow-x-auto rounded border border-slate-100 bg-white">
                                      <table className="w-full text-[11px]">
                                        <thead className="bg-slate-50 text-slate-500">
                                          <tr>
                                            <th className="px-1.5 py-0.5 text-left font-medium">货品</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">规格</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">数量</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">含税单价</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">含税金额</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">条码</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">SKU匹配</th>
                                          </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-50">
                                          {c.details.map((d, i) => (
                                            <tr key={i} className="hover:bg-slate-50/50">
                                              <td className="px-1.5 py-0.5 text-slate-700">{d.goodsName || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-slate-500">{d.spec || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.applyQuantity ?? d.quantity ?? 0}{d.unitName || ""}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.unitPriceTax != null ? d.unitPriceTax.toFixed(2) : "—"}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.amountTax != null ? d.amountTax.toFixed(2) : "—"}</td>
                                              <td className="px-1.5 py-0.5 font-mono text-[11px] text-slate-500">{d.barcode || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-[11px]">
                                                {d.skuId ? <span className="rounded bg-indigo-50 px-1 text-indigo-600">#{d.skuId}·{d.matchStatus || "auto"}</span> : <span className="text-slate-300">未匹配</span>}
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                          <button
                            type="button"
                            disabled={busy || c.currentlyLinked}
                            onClick={() => void linkCandidate(chainOrderId, c.targetId)}
                            className="shrink-0 rounded-md bg-indigo-600 px-2 py-0.5 text-[12px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                          >
                            {c.currentlyLinked ? "已关联" : "关联"}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------- SKU 匹配工作台 ----------

const MATCH_STATUS_META: Record<string, { label: string; cls: string }> = {
  price_ok: { label: "金额校验通过", cls: "bg-emerald-50 text-emerald-600" },
  auto: { label: "自动命中", cls: "bg-blue-50 text-blue-600" },
  manual: { label: "人工指定", cls: "bg-indigo-50 text-indigo-600" },
  price_mismatch: { label: "金额异常", cls: "bg-red-50 text-red-600" },
  missing: { label: "档案缺失", cls: "bg-amber-50 text-amber-700" },
  unmatched: { label: "未匹配", cls: "bg-slate-100 text-slate-500" },
};

function MatchStatusChip({ status }: { status: string }) {
  const meta = MATCH_STATUS_META[status] ?? MATCH_STATUS_META.unmatched;
  return <span className={cx("rounded px-1.5 py-0.5 text-[12px] font-medium", meta.cls)}>{meta.label}</span>;
}

function MatchingView({ summary, pending, busy, loading, onRunAuto, onRefresh, onNotice, onOpenOrder }: {
  summary: InboundMatchSummary | null;
  pending: PendingAllocation[];
  busy: boolean;
  loading: boolean;
  onRunAuto: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
  onOpenOrder: (orderId: number) => void;
}) {
  const counts = summary?.counts ?? {};
  return (
    <div className="mt-5 space-y-4">
      <InboundMatchCard summary={summary} counts={counts} busy={busy} loading={loading} onRunAuto={onRunAuto} onRefresh={onRefresh} onNotice={onNotice} />
      <PendingAllocationCard pending={pending} loading={loading} onRefresh={onRefresh} onNotice={onNotice} onOpenOrder={onOpenOrder} />
    </div>
  );
}

function InboundMatchCard({ summary, counts, busy, loading, onRunAuto, onRefresh, onNotice }: {
  summary: InboundMatchSummary | null;
  counts: Record<string, number>;
  busy: boolean;
  loading: boolean;
  onRunAuto: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
}) {
  const [skus, setSkus] = useState<CatalogSkuRow[]>([]);
  const [savingItem, setSavingItem] = useState<number | null>(null);
  useEffect(() => {
    skuMatchingApi.skus().then(setSkus).catch(() => setSkus([]));
  }, []);
  const anomalies = summary?.anomalies ?? [];
  const manualMatches = summary?.manualMatches ?? [];

  async function assign(itemId: number, skuId: number) {
    if (!skuId) return;
    setSavingItem(itemId);
    try {
      await skuMatchingApi.manualInbound(itemId, skuId);
      onNotice("人工 SKU 匹配已保存，后续自动扫描不会覆盖");
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "人工指定失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function unassign(itemId: number) {
    if (!window.confirm("确认解除这条入库明细的 SKU 匹配？\n\n该明细会保留为人工待处理，自动扫描不会重新绑定；你可以随后选择正确的 SKU。")) return;
    setSavingItem(itemId);
    try {
      await skuMatchingApi.clearManualInbound(itemId);
      onNotice("SKU 匹配已解除，明细保留为人工待处理");
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "解除匹配失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function acceptCost(itemId: number) {
    if (!window.confirm("确认更新吉客云 SKU 主档成本？\n\n系统优先按已关联的 1688 实付金额比例分摊；未关联时才使用入库含税单价。该变更会影响后续利润计算，并记录审计日志。")) return;
    setSavingItem(itemId);
    try {
      const res = await skuMatchingApi.acceptCost(itemId);
      onNotice(`已确认成本：${res.oldCost ?? "无"} → ${res.newCost}`);
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "确认成本失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function batchAcceptCost(documentId: number, docNo: string, count: number) {
    if (!window.confirm(`确认按入库单 ${docNo} 批量确认成本？\n\n将处理该单 ${count} 条金额异常明细：已关联 1688 订单时按实付比例分摊，未关联时按入库含税单价更新 SKU 主档成本。该变更会影响后续利润计算。`)) return;
    setSavingItem(-documentId);
    try {
      const res = await skuMatchingApi.batchAcceptCost(documentId);
      onNotice(
        `按单批量确认完成：接受 ${res.accepted} 条` +
        (res.failed.length ? `，跳过 ${res.failed.length} 条（请核对异常说明）` : "")
      );
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "批量确认失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  return (
    <section className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-[14px] font-semibold text-slate-900">吉客云入库明细 ↔ SKU</h2>
          <p className="mt-0.5 text-[11px] text-slate-400">SKU 条码/货号命中主档后校验金额；系统识别异常，人工可随时改绑，且人工结果不被自动覆盖</p>
        </div>
        <div className="flex items-center gap-2">
          <HeaderButton icon="sync" busy={busy} onClick={() => void onRunAuto()}>扫描未人工锁定项</HeaderButton>
          <HeaderButton icon="refresh" onClick={() => void onRefresh()}>刷新</HeaderButton>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {[
          { label: "明细总数", value: summary?.total ?? 0, cls: "bg-slate-100 text-slate-600" },
          { label: "金额通过", value: counts.price_ok ?? 0, cls: "bg-emerald-50 text-emerald-600" },
          { label: "自动命中", value: counts.auto ?? 0, cls: "bg-blue-50 text-blue-600" },
          { label: "人工锁定", value: counts.manual ?? 0, cls: "bg-indigo-50 text-indigo-600" },
          { label: "金额异常", value: counts.price_mismatch ?? 0, cls: "bg-red-50 text-red-600" },
          { label: "档案缺失", value: counts.missing ?? 0, cls: "bg-amber-50 text-amber-700" },
        ].map((chip) => (
          <span key={chip.label} className={cx("rounded-lg px-2.5 py-1.5 text-[11px] font-medium", chip.cls)}>
        {chip.label} <span className="ml-1 text-[13px] font-semibold">{chip.value}</span>
          </span>
        ))}
      </div>
      {anomalies.length > 0 && (
        <div className="mt-3 rounded-lg border border-red-100 bg-red-50/50 p-3">
          <div className="mb-2 text-[11px] font-medium text-red-600">以下 {anomalies.length} 条明细需要人工确认</div>
          <div className="space-y-2.5">
        {Array.from(
          anomalies.reduce((map, item) => {
            const group = map.get(item.documentId);
            if (group) group.items.push(item);
            else map.set(item.documentId, { docNo: item.goodsdocNo, items: [item] });
            return map;
          }, new Map<number, { docNo: string; items: InboundMatchSummary["anomalies"] }>())
        ).map(([documentId, group]) => {
          const mismatchCount = group.items.filter((i) => i.status === "price_mismatch" && i.matchedSkuId != null).length;
          return (
            <div key={documentId} className="rounded-lg border border-slate-100 bg-white/70 p-2">
              <div className="mb-1.5 flex flex-wrap items-center gap-2 px-1">
                <span className="text-[11px] font-medium text-slate-700">{group.docNo}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[12px] text-slate-500">{group.items.length} 条异常</span>
                {mismatchCount > 1 && (
                  <button
                    onClick={() => void batchAcceptCost(documentId, group.docNo, mismatchCount)}
                    disabled={savingItem !== null}
                    className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[12px] font-medium text-emerald-600 hover:bg-emerald-100 disabled:opacity-50"
                    title={`一次确认该单 ${mismatchCount} 条异常明细的档案成本；历史价格偏差过大时会被拦截`}
                  >
                    按单批量确认成本（{mismatchCount}）
                  </button>
                )}
              </div>
              <InboundMatchRows items={group.items} skus={skus} savingItem={savingItem} onAssign={assign} onAcceptCost={acceptCost} />
            </div>
          );
        })}
          </div>
        </div>
      )}
      {!loading && anomalies.length === 0 && (
        <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] text-emerald-700">当前没有待处理的入库明细异常。</div>
      )}
      {manualMatches.length > 0 && (
        <details className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50/30">
          <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium text-indigo-700">人工已匹配 {manualMatches.length} 条（展开可再次更换）</summary>
          <div className="border-t border-indigo-100 p-3">
        <InboundMatchRows items={manualMatches} skus={skus} savingItem={savingItem} onAssign={assign} onUnassign={unassign} />
          </div>
        </details>
      )}
      {loading && <div className="mt-2 text-[11px] text-slate-400">加载中…</div>}
    </section>
  );
}

function InboundMatchRows({ items, skus, savingItem, onAssign, onUnassign, onAcceptCost }: {
  items: InboundMatchSummary["anomalies"];
  skus: CatalogSkuRow[];
  savingItem: number | null;
  onAssign: (itemId: number, skuId: number) => Promise<void>;
  onUnassign?: (itemId: number) => Promise<void>;
  onAcceptCost?: (itemId: number) => Promise<void>;
}) {
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.itemId} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-100 bg-white px-3 py-2 text-[11px]">
          <MatchStatusChip status={item.status} />
          <span className="font-medium text-slate-700">{item.goodsdocNo}</span>
          <span className="font-mono text-[12px] text-slate-400">{item.goodsNo || "无货号"}</span>
          <span className="text-slate-600">{item.goodsName || "未命名商品"}</span>
          <span className="text-slate-400">数量 {item.quantity ?? "-"}</span>
          <span className="text-slate-400">金额 {fmtMoney(item.amountTax)}</span>
          <span className="min-w-[160px] flex-1 truncate text-amber-600" title={item.note}>{item.note || "人工已确认"}</span>
          <SearchableSelect
        ariaLabel={`为 ${item.goodsdocNo} 选择 SKU`}
        disabled={savingItem !== null}
        className="max-w-[280px]"
        placeholder="选择吉客云 SKU…"
        value={item.status === "manual" && item.matchedSkuId ? String(item.matchedSkuId) : ""}
        onChange={(next) => void onAssign(item.itemId, Number(next))}
        options={skus.map((sku) => ({
          value: String(sku.id), label: `${sku.skuCode} · ${sku.skuName || sku.goodsName}`, keywords: `${sku.skuCode} ${sku.skuName ?? ""} ${sku.goodsName ?? ""}`,
        }))}
      />
          {onAcceptCost && item.status === "price_mismatch" && item.matchedSkuId != null && (
        <button
          onClick={() => void onAcceptCost(item.itemId)}
          disabled={savingItem !== null}
          className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[12px] font-medium text-emerald-600 hover:bg-emerald-100 disabled:opacity-50"
          title="确认更新档案成本：优先按关联 1688 实付比例分摊，否则使用入库含税单价"
        >
          确认成本
        </button>
          )}
          {onUnassign && item.status === "manual" && item.matchedSkuId != null && (
        <button
          onClick={() => void onUnassign(item.itemId)}
          disabled={savingItem !== null}
          className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[12px] font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          title="解除当前 SKU，保留为人工待处理且不参与自动扫描"
        >
          解除匹配
        </button>
          )}
        </div>
      ))}
    </div>
  );
}

function PendingAllocationCard({ pending, loading, onRefresh, onNotice, onOpenOrder }: {
  pending: PendingAllocation[];
  loading: boolean;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
  onOpenOrder: (orderId: number) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<Record<number, SkuCandidate[]>>({});
  const [draft, setDraft] = useState<{ skuId: number; quantity: string; unitPrice: string } | null>(null);
  const [savingPo, setSavingPo] = useState<number | null>(null);

  const toggle = async (poId: number) => {
    if (expanded === poId) {
      setExpanded(null);
      return;
    }
    setExpanded(poId);
    setDraft(null);
    if (!candidates[poId]) {
      try {
        const res = await skuMatchingApi.candidates(poId);
        setCandidates((c) => ({ ...c, [poId]: res.candidates }));
      } catch {
        setCandidates((c) => ({ ...c, [poId]: [] }));
      }
    }
  };

  const confirm = async (po: PendingAllocation) => {
    if (!draft) return;
    const quantity = Number(draft.quantity);
    const unitPrice = Number(draft.unitPrice);
    if (!po.editable) {
      onNotice("该订单已确认采购内容，如需改绑请先在订单详情核对状态");
      return;
    }
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(unitPrice) || unitPrice < 0) {
      onNotice("请填写有效的数量和单价");
      return;
    }
    setSavingPo(po.poId);
    try {
      await skuMatchingApi.addAllocation(po.poId, {
        sku_id: draft.skuId,
        quantity: draft.quantity,
        unit_price: draft.unitPrice,
        amount: String((Number(draft.quantity) || 0) * (Number(draft.unitPrice) || 0)),
      });
      onNotice(`订单 ${po.externalOrderId} SKU 分配已保存`);
      setDraft(null);
      setExpanded(null);
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "保存失败，请检查数量/单价后重试");
    } finally {
      setSavingPo(null);
    }
  };

  return (
    <section className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-[14px] font-semibold text-slate-900">1688 订单 SKU 配置</h2>
          <p className="mt-0.5 text-[11px] text-slate-400">1688 源数据无商品明细，需人工指定 SKU；候选按同供应商历史入库货品推荐，金额不平衡会提示</p>
        </div>
        <span className="rounded-lg bg-slate-100 px-2.5 py-1.5 text-[11px] font-medium text-slate-600">待配置 {pending.length}</span>
      </div>
      <div className="mt-3 space-y-2">
        {loading ? <div className="text-[11px] text-slate-400">加载中…</div> : pending.length === 0 ? (
          <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-600">全部订单已配置且金额平衡 ✓</div>
        ) : pending.map((po) => (
          <div key={po.poId} className="rounded-lg border border-slate-100">
        <div className="flex items-center gap-2 pr-3 hover:bg-slate-50">
          <button onClick={() => void toggle(po.poId)} className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left text-[11px]">
            <span className="font-medium text-slate-700">{po.supplierName || "(未知供应商)"}</span>
            <span className="font-mono text-slate-400">{po.externalOrderId}</span>
            <span className="text-slate-400">采购时间 {fmtDateTime(po.orderedAt)}</span>
            <span className="text-slate-400">实付 {fmtMoney(po.paidAmount)}</span>
            {po.allocationCount > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">已配 {po.allocationCount} 条</span>}
            {!po.balance.balanced && po.allocationCount > 0 && (
              <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600" title={po.balance.abnormalNote}>金额不平衡</span>
            )}
            {!po.editable && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">当前状态已锁定</span>}
            <span className="ml-auto text-slate-300">{expanded === po.poId ? "收起 ▲" : "配置 ▼"}</span>
          </button>
          <button onClick={() => onOpenOrder(po.fileOrderId ?? -po.poId)} className="shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-medium text-indigo-600 hover:bg-indigo-50">进入订单调整</button>
        </div>
        {expanded === po.poId && (
          <div className="border-t border-slate-100 px-3 py-2.5">
            {(candidates[po.poId] ?? []).length > 0 && (
              <div className="mb-2">
                <div className="mb-1 text-[10px] font-medium text-slate-400">候选（同供应商历史优先，其余来自吉客云 SKU 主档）</div>
                <div className="flex flex-wrap gap-1.5">
                  {(candidates[po.poId] ?? []).map((c) => (
                    <button
                      key={c.skuId}
                      onClick={() => setDraft({
                        skuId: c.skuId,
                        quantity: "1",
                        unitPrice: c.defaultCost != null && c.defaultCost > 0 ? String(c.defaultCost) : po.paidAmount != null && po.paidAmount > 0 ? String(po.paidAmount) : "0",
                      })}
                      className={cx(
                        "rounded-md border px-2 py-1 text-left text-[10.5px]",
                        draft?.skuId === c.skuId ? "border-indigo-300 bg-indigo-50 text-indigo-600" : "border-slate-200 text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/50"
                      )}
                      title={c.reason}
                    >
                      <span className="font-mono">{c.skuCode}</span> · {c.skuName || "未命名 SKU"}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {draft && (
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-slate-500">数量</span>
                <input value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: e.target.value })} className="w-16 rounded border border-slate-200 px-1.5 py-1" />
                <span className="text-slate-500">单价 ¥</span>
                <input value={draft.unitPrice} onChange={(e) => setDraft({ ...draft, unitPrice: e.target.value })} className="w-24 rounded border border-slate-200 px-1.5 py-1" />
                <span className="text-slate-400">新增 {fmtMoney(Number(draft.quantity || 0) * Number(draft.unitPrice || 0))}</span>
                {po.paidAmount != null && Math.abs(po.balance.allocated + Number(draft.quantity || 0) * Number(draft.unitPrice || 0) - po.paidAmount) > Math.max(po.paidAmount * 0.02, 0.5) && (
                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">新增后累计 {fmtMoney(po.balance.allocated + Number(draft.quantity || 0) * Number(draft.unitPrice || 0))}，与实付 {fmtMoney(po.paidAmount)} 仍不一致</span>
                )}
                {po.editable ? (
                  <HeaderButton icon="plus" busy={savingPo === po.poId} onClick={() => void confirm(po)}>保存分配</HeaderButton>
                ) : (
                  <span className="rounded-md bg-slate-100 px-2.5 py-1.5 text-[10px] text-slate-500">已确认状态不可直接改动</span>
                )}
              </div>
            )}
            {!draft && (candidates[po.poId] ?? []).length === 0 && (
              <div className="text-[11px] text-slate-400">暂无候选，可在采购订单详情中手动添加分配</div>
            )}
          </div>
        )}
          </div>
        ))}
      </div>
    </section>
  );
}
