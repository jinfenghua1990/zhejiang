"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { taxInvoiceApi, type TaxInvoiceCategoryOutput, type TaxInvoiceCategoryV2, type TaxInvoiceImportRow, type TaxInvoiceLinesResponse, type TaxInvoiceProcessingStatus, type TaxInvoicePurchaseCandidate, type TaxInvoiceRow, type TaxInvoiceSummary } from "@/lib/api";
import { useTabActive, useTabScopedState } from "@/lib/workspace/tab-store";

type FilterValue = "input" | "output";
type MatchFilter = "all" | "matched" | "partial" | "unmatched" | "needs_review" | "pending";
type InvoiceStatusFilter = "all" | "blue_active" | "partially_red_offset" | "fully_red_offset" | "red_invoice" | "exception" | "void" | "unknown";
/** v2 分类筛选：6 个分类 + ""=待判断 + 派生组伪值（计入运营成本） */
type TypeFilter = "all" | TaxInvoiceCategoryV2 | TaxInvoiceCategoryOutput | "" | "group:operating";

function money(value: string | number | null | undefined) {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isFinite(amount)
    ? `¥${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : String(value);
}

function dateText(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString("zh-CN");
}

function invoiceNo(row: TaxInvoiceRow) {
  return `${row.invoiceCode || ""}${row.invoiceNumber || ""}` || "未记录号码";
}

function directionLabel(value: string) {
  return value === "input" ? "进项" : value === "output" ? "销项" : "待确认";
}

function directionClass(value: string) {
  return value === "input"
    ? "border-blue-200 bg-blue-50 text-blue-700"
    : value === "output"
      ? "border-violet-200 bg-violet-50 text-violet-700"
      : "border-slate-200 bg-slate-50 text-slate-500";
}

function matchLabel(value: string) {
  return value === "matched"
    ? "已匹配"
    : value === "partial"
      ? "部分匹配"
      : value === "needs_review"
        ? "待核对"
        : "未匹配";
}

function businessMatchStatus(row: TaxInvoiceRow) {
  // 发票自己的业务匹配只认采购/销售域的显式状态；禁止回退到旧 matchStatus。
  return row.businessMatchStatus;
}

function bankPaymentLabel(row: TaxInvoiceRow) {
  if (row.direction !== "input" || row.bankPaymentStatus === "not_applicable") return "—";
  if (row.bankPaymentStatus === "overpaid_after_red") return "红冲后超额付款";
  if (row.bankPaymentStatus === "red_overpayment_settled") return "红冲超额已处理";
  if (row.bankPaymentStatus === "matched") return "已付清核对";
  if (row.bankPaymentStatus === "partial") return "部分付款核对";
  return "待付款核对";
}

function bankPaymentClass(row: TaxInvoiceRow) {
  if (row.direction !== "input" || row.bankPaymentStatus === "not_applicable") return "text-slate-300";
  if (row.bankPaymentStatus === "overpaid_after_red") return "bg-rose-100 text-rose-800";
  if (row.bankPaymentStatus === "red_overpayment_settled") return "bg-emerald-100 text-emerald-800";
  if (row.bankPaymentStatus === "matched") return "bg-emerald-50 text-emerald-700";
  if (row.bankPaymentStatus === "partial") return "bg-blue-50 text-blue-700";
  return "bg-amber-50 text-amber-700";
}

function isCorporatePaymentVerified(row: TaxInvoiceRow) {
  if (row.direction !== "input" || row.bankPaymentStatus !== "matched" || row.paymentMethod !== "corporate") return false;
  const paidAmount = Number(row.bankPaidAmount);
  const effectiveAmount = Number(row.bankEffectiveInvoiceAmount || row.totalAmount);
  const remainingAmount = Number(row.bankRemainingAmount);
  return Number.isFinite(paidAmount)
    && Number.isFinite(effectiveAmount)
    && Number.isFinite(remainingAmount)
    && Math.abs(paidAmount - effectiveAmount) <= 0.005
    && remainingAmount <= 0.005;
}

function matchesInvoiceLifecycle(row: TaxInvoiceRow, filter: InvoiceStatusFilter) {
  if (filter === "all") return true;
  if (filter === "blue_active") return row.invoiceColor === "blue" && row.redStatus === "none";
  if (filter === "partially_red_offset") return row.redStatus === "partially_red_offset";
  if (filter === "fully_red_offset") return row.redStatus === "fully_red_offset";
  if (filter === "red_invoice") return row.invoiceColor === "red";
  if (filter === "exception") return Boolean(row.accountingException);
  if (filter === "void") return row.redStatus === "void" || row.status === "void";
  return row.redStatus === "unknown" || row.status === "unknown";
}

function invoiceLifecycleClass(row: TaxInvoiceRow) {
  if (row.accountingException) return "bg-rose-100 text-rose-800";
  if (row.invoiceColor === "red") return "bg-rose-50 text-rose-700";
  if (row.redStatus === "fully_red_offset") return "bg-orange-100 text-orange-800";
  if (row.redStatus === "partially_red_offset") return "bg-amber-50 text-amber-700";
  if (row.redStatus === "none" && row.invoiceColor === "blue") return "bg-emerald-50 text-emerald-700";
  return "bg-slate-100 text-slate-500";
}

function matchClass(value: string) {
  return value === "matched"
    ? "bg-emerald-50 text-emerald-700"
    : value === "partial"
      ? "bg-blue-50 text-blue-700"
      : value === "needs_review"
        ? "bg-amber-50 text-amber-700"
        : "bg-slate-100 text-slate-500";
}

/** 派生态（由 v2 分类派生，仅作展示与统计）：计入运营成本 / 计入报销成本 / 不计入 / 待判断 */
type DerivedGroup = "operating" | "reimburse" | "excluded" | "pending";

function derivedGroup(value: string): DerivedGroup {
  if (value === "goods" || value === "platform_fee" || value === "operating_other") return "operating";
  if (value === "reimburse_advance" || value === "reimburse_operating") return "reimburse";
  if (value === "excluded") return "excluded";
  return "pending";
}

function derivedLabel(value: DerivedGroup): string {
  return value === "operating"
    ? "计入运营成本"
    : value === "reimburse"
      ? "计入报销成本"
      : value === "excluded"
        ? "不计入"
        : "待判断";
}

function derivedClass(value: DerivedGroup): string {
  return value === "operating"
    ? "border-blue-200 bg-blue-50 text-blue-700"
    : value === "reimburse"
      ? "border-amber-200 bg-amber-50 text-amber-700"
      : value === "excluded"
        ? "border-slate-200 bg-slate-100 text-slate-500"
        : "border-orange-200 bg-orange-50 text-orange-700";
}

function lineSummary(row: TaxInvoiceRow) {
  if (!row.lineItemCount) return "未解析到开票明细";
  const items = row.lineItems || [];
  const names = items.slice(0, 2).map((item) => item.goodsName ? `${item.goodsName}${item.spec ? `（${item.spec}）` : ""}` : "已识别明细");
  return `${names.join("、") || "已识别明细"}${row.lineItemCount > names.length ? ` 等 ${row.lineItemCount} 项` : ""}`;
}

/** 进项发票 v2 分类（6+1）：运营成本三分类、报销两分类、不计入；空=待判断（未分类）。
 *  后端已存分类为权威，缺失时按开票明细兜底识别。 */
const CATEGORY_META: Record<string, { label: string; cls: string }> = {
  goods: { label: "运营成本：货款发票", cls: "border-blue-200 bg-blue-50 text-blue-700" },
  platform_fee: { label: "运营成本：平台服务费", cls: "border-violet-200 bg-violet-50 text-violet-700" },
  operating_other: { label: "运营成本其他", cls: "border-cyan-200 bg-cyan-50 text-cyan-700" },
  reimburse_advance: { label: "报销：代付", cls: "border-amber-200 bg-amber-50 text-amber-700" },
  reimburse_operating: { label: "报销：运营成本", cls: "border-amber-200 bg-amber-50 text-amber-700" },
  excluded: { label: "不计入任何报销运营", cls: "border-slate-200 bg-slate-100 text-slate-500" },
  "": { label: "待判断（未分类）", cls: "border-orange-200 bg-orange-50 text-orange-700" },
};
const CATEGORY_V2_OPTIONS: TaxInvoiceCategoryV2[] = [
  "goods", "platform_fee", "operating_other", "reimburse_advance", "reimburse_operating", "excluded",
];
function categoryLabel(value: string): string {
  return CATEGORY_META[value]?.label ?? (value || "待判断（未分类）");
}
function categoryClass(value: string): string {
  return CATEGORY_META[value]?.cls ?? CATEGORY_META[""].cls;
}
function invoiceCategory(row: TaxInvoiceRow): TaxInvoiceCategoryV2 | "" {
  // 后端存值为权威：未分类一律如实显示「待判断」，不做明细推断兜底
  return (row.category || "") as TaxInvoiceCategoryV2 | "";
}
/** 销项发票分类（独立枚举）：给买家开发票 / 给平台开服务费；空=待判断。后端存值为权威，不做明细推断。 */
const OUTPUT_CATEGORY_META: Record<string, { label: string; cls: string }> = {
  buyer_sales: { label: "给买家开发票", cls: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  platform_service: { label: "给平台开服务费", cls: "border-violet-200 bg-violet-50 text-violet-700" },
  "": { label: "待判断", cls: "border-orange-200 bg-orange-50 text-orange-700" },
};
const OUTPUT_CATEGORY_OPTIONS: TaxInvoiceCategoryOutput[] = ["buyer_sales", "platform_service"];
/** 行内/批量下拉可选项：进项 6+1、销项 3 项（含空=待判断）。 */
const INPUT_CATEGORY_SELECT_OPTIONS: (TaxInvoiceCategoryV2 | "")[] = [...CATEGORY_V2_OPTIONS, ""];
const OUTPUT_CATEGORY_SELECT_OPTIONS: (TaxInvoiceCategoryOutput | "")[] = [...OUTPUT_CATEGORY_OPTIONS, ""];
/** 行分类取值：销项直接用存量（无明细推断），进项保留兜底识别。 */
function rowCategoryValue(row: TaxInvoiceRow): string {
  return row.direction === "output" ? row.category || "" : invoiceCategory(row);
}
function outputCategoryLabel(value: string): string {
  return OUTPUT_CATEGORY_META[value]?.label ?? (value || "待判断");
}

/** 进项发票支付方式：已确认银行付款可推导为对公；无银行证据时可人工标记个人垫付或平台自动扣款货款。 */
const PAYMENT_METHOD_META: Record<string, { label: string; cls: string }> = {
  corporate: { label: "对公账户支出", cls: "border-blue-200 bg-blue-50 text-blue-700" },
  personal: { label: "个人垫付", cls: "border-amber-200 bg-amber-50 text-amber-700" },
  platform_auto_debit: { label: "平台自动扣款货款", cls: "border-cyan-200 bg-cyan-50 text-cyan-700" },
  mixed: { label: "对公 + 个人垫付", cls: "border-violet-200 bg-violet-50 text-violet-700" },
  "": { label: "未设置", cls: "border-slate-200 bg-slate-100 text-slate-500" },
};
function paymentMethodLabel(value: string): string {
  return PAYMENT_METHOD_META[value]?.label ?? (value || "未设置");
}
function paymentMethodClass(value: string): string {
  return PAYMENT_METHOD_META[value]?.cls ?? PAYMENT_METHOD_META[""].cls;
}
/** 按行方向取分类文案与徽章样式。 */
function rowCategoryLabel(row: TaxInvoiceRow, value: string): string {
  return row.direction === "output" ? outputCategoryLabel(value) : categoryLabel(value);
}
function rowCategoryClass(row: TaxInvoiceRow, value: string): string {
  return row.direction === "output"
    ? (OUTPUT_CATEGORY_META[value]?.cls ?? OUTPUT_CATEGORY_META[""].cls)
    : categoryClass(value);
}
function rowCategoryOptions(row: TaxInvoiceRow): readonly string[] {
  return row.direction === "output" ? OUTPUT_CATEGORY_SELECT_OPTIONS : INPUT_CATEGORY_SELECT_OPTIONS;
}
/** 分类筛选谓词：支持精确 6+1 与派生组伪值。 */
function matchCategoryFilter(category: string, filter: TypeFilter): boolean {
  if (filter === "all") return true;
  if (filter === "group:operating") return derivedGroup(category) === "operating";
  return category === filter;
}
/** processing_status 兼容缓存：由分类派生（运营成本→required；报销/不计入→not_required；空→pending）。 */
function processingFromCategory(value: string): TaxInvoiceProcessingStatus {
  const group = derivedGroup(value);
  return group === "operating" ? "required" : group === "pending" ? "pending" : "not_required";
}
function invoiceKindLabel(row: TaxInvoiceRow): string {
  const type = row.invoiceType || "";
  if (type.includes("专用")) return "专票";
  if (type.includes("普通")) return "普票";
  if (type.includes("客票")) return "客票";
  return type || "—";
}
function invoiceKindClass(row: TaxInvoiceRow): string {
  const type = row.invoiceType || "";
  if (type.includes("专用")) return "border-blue-200 bg-blue-50 text-blue-700";
  if (type.includes("普通")) return "border-slate-200 bg-slate-100 text-slate-600";
  if (type.includes("客票")) return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-slate-200 bg-white text-slate-400";
}

export default function InvoiceManagementPage() {
  const [rows, setRows] = useState<TaxInvoiceRow[]>([]);
  const [summary, setSummary] = useState<TaxInvoiceSummary | null>(null);
  const [imports, setImports] = useState<TaxInvoiceImportRow[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [query, setQuery] = useTabScopedState("invoices.search", "");
  const [direction, setDirection] = useTabScopedState<FilterValue>("invoices.direction", "input");
  const [matchStatus, setMatchStatus] = useTabScopedState<MatchFilter>("invoices.match", "all");
  const [invoiceStatus, setInvoiceStatus] = useTabScopedState<InvoiceStatusFilter>("invoices.status", "all");
  const [typeFilter, setTypeFilter] = useTabScopedState<TypeFilter>("invoices.type", "all");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  // 批量分类选择：__placeholder__ = 未选择（与「待判断」的空值区分开）
  const [batchCategory, setBatchCategory] = useState("__placeholder__");
  const [batchBusy, setBatchBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [period, setPeriod] = useTabScopedState("invoices.period", "");
  const fileRef = useRef<HTMLInputElement>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [invoiceRows, invoiceSummary, activeImports] = await Promise.all([
        taxInvoiceApi.invoices({ limit: 500 }),
        taxInvoiceApi.summary(),
        taxInvoiceApi.imports("active"),
      ]);
      setRows(invoiceRows);
      setSummary(invoiceSummary);
      setImports(activeImports);
      // 首次加载不自动打开详情弹窗；只有用户主动点“查看详情”时才打开。
      setSelectedId((current) => (current && invoiceRows.some((row) => row.id === current) ? current : null));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (row.direction !== direction) return false;
      if (!matchesInvoiceLifecycle(row, invoiceStatus)) return false;
      if (matchStatus === "pending") {
        if (!["unmatched", "partial", "needs_review"].includes(businessMatchStatus(row))) return false;
      } else if (matchStatus !== "all" && businessMatchStatus(row) !== matchStatus) return false;
      if (typeFilter !== "all" && !matchCategoryFilter(rowCategoryValue(row), typeFilter)) return false;
      if (!needle) return true;
      // 多关键词空格分隔 = 全部命中才显示（AND），如「河北 咖啡」只留两者兼有的行
      const keywords = needle.split(/\s+/).filter(Boolean);
      const searchable = [
        invoiceNo(row), row.sellerName, row.sellerTaxId, row.buyerName, row.buyerTaxId,
        row.purchaseOrderNos.join(" "), row.inboundNos.join(" "), row.matchNote,
        row.lineItems.map((line) => [line.goodsName, line.spec, line.remark].filter(Boolean).join(" ")).join(" "),
      ].join(" ").toLowerCase();
      return keywords.every((keyword) => searchable.includes(keyword));
    });
  }, [direction, invoiceStatus, matchStatus, query, rows, typeFilter]);

  const selected = rows.find((row) => row.id === selectedId) ?? null;
  const visibleTotalAmount = useMemo(
    () => visibleRows.reduce((sum, row) => sum + (Number(row.accountingNetAmount) || 0), 0),
    [visibleRows],
  );
  const categoryCounts = useMemo(() => {
    const inputCounts: Record<TaxInvoiceCategoryV2 | "", number> = {
      goods: 0, platform_fee: 0, operating_other: 0,
      reimburse_advance: 0, reimburse_operating: 0, excluded: 0, "": 0,
    };
    const outputCounts: Record<TaxInvoiceCategoryOutput | "", number> = {
      buyer_sales: 0, platform_service: 0, "": 0,
    };
    for (const row of rows) {
      if (row.direction === "input") inputCounts[invoiceCategory(row)] += 1;
      else if (row.direction === "output") outputCounts[(row.category || "") as TaxInvoiceCategoryOutput | ""] += 1;
    }
    return { input: inputCounts, output: outputCounts };
  }, [rows]);
  const batchCategoryReady = batchCategory !== "__placeholder__";

  // 筛选条件或方向变化时清空已选行，避免对已不可见的行批量操作
  useEffect(() => {
    setSelectedIds(new Set());
  }, [direction, query, matchStatus, invoiceStatus, typeFilter]);

  const allVisibleSelected = visibleRows.length > 0 && visibleRows.every((row) => selectedIds.has(row.id));
  // 部分选中时表头复选框显示半选态
  useEffect(() => {
    if (selectAllRef.current) {
      selectAllRef.current.indeterminate = visibleRows.length > 0 && !allVisibleSelected && visibleRows.some((row) => selectedIds.has(row.id));
    }
  }, [allVisibleSelected, selectedIds, visibleRows]);
  function toggleRowSelected(id: number) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  function toggleSelectAll() {
    setSelectedIds(allVisibleSelected ? new Set() : new Set(visibleRows.map((row) => row.id)));
  }
  async function changeCategory(id: number, category: string) {
    setError("");
    setMessage("");
    try {
      await taxInvoiceApi.bulkSetCategory([id], category);
      setRows((current) => current.map((row) => (row.id === id ? { ...row, category, processingStatus: processingFromCategory(category) } : row)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  async function changePaymentMethod(id: number, paymentMethod: "personal" | "platform_auto_debit" | "") {
    setError("");
    setMessage("");
    try {
      await taxInvoiceApi.bulkSetPaymentMethod([id], paymentMethod);
      await load();
      setMessage(paymentMethod === "personal" ? "已标记个人垫付。" : paymentMethod === "platform_auto_debit" ? "已标记平台自动扣款货款。" : "已清除人工付款方式标记。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  async function moveSelectedToCategory() {
    if (!batchCategoryReady || !selectedIds.size) return;
    setBatchBusy(true);
    setError("");
    setMessage("");
    try {
      const ids = [...selectedIds];
      await taxInvoiceApi.bulkSetCategory(ids, batchCategory);
      setRows((current) => current.map((row) => (ids.includes(row.id) ? { ...row, category: batchCategory, processingStatus: processingFromCategory(batchCategory) } : row)));
      setSelectedIds(new Set());
      setMessage(`已将 ${ids.length} 张发票移动到「${direction === "output" ? outputCategoryLabel(batchCategory) : categoryLabel(batchCategory)}」。`);
      setBatchCategory("__placeholder__");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBatchBusy(false);
    }
  }

  async function handleUpload(fileList: FileList | undefined) {
    const files = fileList ? Array.from(fileList) : [];
    if (!files.length) return;
    setUploading(true);
    setError("");
    setMessage("");
    try {
      const completed: string[] = [];
      const failed: string[] = [];
      for (const [index, file] of files.entries()) {
        setMessage(`正在处理第 ${index + 1} / ${files.length} 份：${file.name}`);
        try {
          const result = await taxInvoiceApi.upload(file, period || undefined, true);
          const imported = result.import;
          completed.push(
            result.duplicate
              ? `${imported.originalName}（重复，已跳过）`
              : `${imported.originalName}（识别 ${imported.recognizedRowCount} 行，匹配 ${imported.matchedRowCount} 行${imported.needsReviewCount ? `，待核对 ${imported.needsReviewCount} 行` : ""}）`,
          );
        } catch (caught) {
          failed.push(`${file.name}：${caught instanceof Error ? caught.message : String(caught)}`);
        }
      }
      setMessage(`批量处理完成：成功 ${completed.length} 份${failed.length ? `，失败 ${failed.length} 份（${failed.join("、")}）` : ""}。${completed.join("；")}`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="mx-auto max-w-[1600px]">
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-2 backdrop-blur">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">发票管理</h1>
          <p className="text-xs text-slate-500">发票业务匹配、银行付款核对分开管理；对公付款以银行证据为准，个人垫付或平台自动扣款货款需人工明确</p>
          <Link href="/data-center-import?tab=tax" className="text-xs text-indigo-600 hover:underline">查看原始导入批次与明细</Link>
        </div>
      </header>

      {message && <div className="mt-4 rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-2.5 text-xs text-emerald-700">{message}</div>}
      {error && <div className="mt-4 rounded-lg border border-rose-100 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">{error}</div>}

      {/* 吸顶单行工具条：tab + 筛选 + 操作合并一行，滚动时固定在顶部 */}
      <div className="sticky top-0 z-30 -mx-1 flex flex-wrap items-center gap-2 border-b border-slate-200 bg-white/95 px-4 py-2 shadow-sm backdrop-blur">
          <div className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5">
            {([
              ["input", "进项发票", summary?.byDirection?.input ?? 0],
              ["output", "销项发票", summary?.byDirection?.output ?? 0],
            ] as const).map(([value, label, count]) => (
              <button
                key={value}
                type="button"
                onClick={() => { setDirection(value); setTypeFilter("all"); setMatchStatus("all"); setInvoiceStatus("all"); setBatchCategory("__placeholder__"); }}
                className={`rounded-lg px-4 py-1.5 text-xs transition ${direction === value ? "bg-indigo-600 font-medium text-white shadow-sm" : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"}`}
              >
                {label} <span className={direction === value ? "text-indigo-100" : "text-slate-400"}>{count}</span>
              </button>
            ))}
          </div>
          <div className="relative min-w-[220px] flex-1">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400">⌕</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="关键词即时筛选：发票号 / 开票方 / 税号 / 货物明细 / 采购单 / 入库单，可空格分隔多词" className="w-full rounded-lg border border-slate-200 bg-white py-1.5 pl-8 pr-8 text-xs outline-none focus:border-indigo-400" />
            {query && <button type="button" onClick={() => setQuery("")} title="清空关键词" className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-1 text-slate-400 transition hover:text-slate-700">✕</button>}
          </div>
          <select value={matchStatus} onChange={(event) => setMatchStatus(event.target.value as MatchFilter)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none"><option value="all">全部业务匹配</option><option value="pending">业务待匹配（未匹配+部分匹配+待核对）</option><option value="matched">已匹配</option><option value="partial">部分匹配</option><option value="unmatched">未匹配</option><option value="needs_review">待核对</option></select>
          <select value={invoiceStatus} onChange={(event) => setInvoiceStatus(event.target.value as InvoiceStatusFilter)} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none">
            <option value="all">全部票据状态</option>
            <option value="blue_active">蓝字发票（有效）</option>
            <option value="partially_red_offset">蓝字发票（部分红冲）</option>
            <option value="fully_red_offset">蓝字发票（已全额红冲）</option>
            <option value="red_invoice">红字发票（冲销）</option>
            <option value="exception">红冲关联异常</option>
            <option value="void">作废发票</option>
            <option value="unknown">待确认发票</option>
          </select>
          {direction === "input" && ((summary?.redInvoiceCount || 0) + (summary?.partiallyRedOffsetBlueCount || 0) + (summary?.fullyRedOffsetBlueCount || 0) > 0) && (
            <button type="button" onClick={() => setInvoiceStatus(invoiceStatus === "red_invoice" ? "all" : "red_invoice")} title="蓝字原票与红字冲销票分别留存；财务统计按正负金额净额抵消，部分红冲保留蓝票剩余有效金额。" className={`rounded-full border px-2.5 py-1 text-[11px] transition ${invoiceStatus === "red_invoice" ? "border-rose-300 bg-rose-50 font-medium text-rose-700 ring-1 ring-rose-200" : "border-rose-200 bg-white text-rose-600 hover:bg-rose-50"}`}>红字发票 <span className="tabular-nums">{summary?.redInvoiceCount || 0}</span></button>
          )}
          {!!summary?.redPairExceptionCount && (
            <button type="button" onClick={() => setInvoiceStatus(invoiceStatus === "exception" ? "all" : "exception")} className={`rounded-full border px-2.5 py-1 text-[11px] transition ${invoiceStatus === "exception" ? "border-rose-400 bg-rose-100 font-medium text-rose-800" : "border-rose-200 bg-white text-rose-600 hover:bg-rose-50"}`}>红冲异常 <span className="tabular-nums">{summary.redPairExceptionCount}</span></button>
          )}
          <span className="text-[11px] font-medium text-slate-400">分类</span>
          <span className="text-[11px] font-medium text-slate-400">分类</span>
          <button type="button" onClick={() => setTypeFilter("all")} className={`rounded-full border px-2.5 py-1 text-[11px] transition ${typeFilter === "all" ? "border-indigo-300 bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"}`}>全部 <span className="tabular-nums">{rows.filter((row) => row.direction === direction).length}</span></button>
          {(direction === "output" ? (["buyer_sales", "platform_service", ""] as const) : (["goods", "platform_fee", "operating_other", "reimburse_advance", "reimburse_operating", "excluded", ""] as const)).map((value) => (
            <button
              key={value || "pending"}
              type="button"
              onClick={() => setTypeFilter(typeFilter === value ? "all" : value)}
              className={`rounded-full border px-2.5 py-1 text-[11px] transition ${typeFilter === value ? "border-indigo-300 bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200" : `border-slate-200 bg-white text-slate-600 hover:bg-slate-50 ${value === "" ? "border-dashed" : ""}`}`}
            >
              {direction === "output" ? outputCategoryLabel(value) : categoryLabel(value)} <span className="tabular-nums">{(categoryCounts[direction] as Record<string, number>)[value]}</span>
            </button>
          ))}
        <div className="ml-auto flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".xlsx,.csv" multiple className="hidden" onChange={(event) => void handleUpload(event.target.files ?? undefined)} />
          <input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} aria-label="发票所属账期" className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none focus:border-indigo-400" />
          <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} className="rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-50">{uploading ? "识别匹配中…" : "上传发票清单"}</button>
          <button type="button" onClick={() => void load()} disabled={loading} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">刷新</button>
        </div>
      </div>

      <section className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 bg-slate-50/40 px-4 py-1.5 text-[11px] text-slate-400">
          <span>共 <span className="tabular-nums text-slate-600">{visibleRows.length}</span> 张 · 财务净额 <span className="tabular-nums text-slate-600">{money(String(visibleTotalAmount))}</span></span>
          <div className="flex flex-wrap items-center gap-2">
            {direction === "input" && <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />绿色高亮 = 对公付款金额已核对</span>}
            <span>{typeFilter !== "all" ? `分类：${direction === "output" ? outputCategoryLabel(typeFilter as string) : typeFilter === "group:operating" ? "计入运营成本（货款/平台服务费/其他）" : categoryLabel(typeFilter)}` : ""}</span>
          </div>
        </div>

        {selectedIds.size > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-indigo-100 bg-indigo-50/70 px-4 py-2.5">
            <span className="text-xs font-medium text-indigo-700">已选择 <span className="tabular-nums">{selectedIds.size}</span> 张<span className="font-normal text-indigo-400">（当前筛选可见 {visibleRows.length} 张）</span></span>
            <span className="flex items-center gap-1.5"><span className="text-[11px] text-indigo-400">移动到：</span>
              <select value={batchCategory} onChange={(event) => setBatchCategory(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-300">
                <option value="__placeholder__" disabled>请选择分类</option>
                {(direction === "output" ? OUTPUT_CATEGORY_SELECT_OPTIONS : INPUT_CATEGORY_SELECT_OPTIONS).map((value) => (
                  <option key={value || "pending"} value={value}>{direction === "output" ? outputCategoryLabel(value) : categoryLabel(value)}</option>
                ))}
              </select>
              <button type="button" onClick={() => void moveSelectedToCategory()} disabled={batchBusy || !batchCategoryReady} className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40">{batchBusy ? "移动中…" : "批量移动"}</button>
            </span>
            <span className="hidden text-[10px] text-indigo-300 xl:inline">{direction === "output" ? "销项分类：给买家开发票 / 给平台开服务费 · 空=待判断" : "分类决定统计口径：运营成本三分类=计入运营成本 · 报销两分类=计入报销成本 · 不计入任何报销运营 · 空=待判断"}</span>
                        <button type="button" onClick={() => { setSelectedIds(new Set()); setBatchCategory("__placeholder__"); }} className="ml-auto rounded-md border border-slate-200 bg-white px-3 py-1 text-xs text-slate-600 hover:bg-slate-50">取消选择</button>
          </div>
        )}

        <div className="max-h-[calc(100vh-200px)] overflow-auto">
          <table className="w-full min-w-[1600px] border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-[11px] text-slate-500 shadow-[0_1px_0_0_#e2e8f0]">
              <tr>
                <th className="w-10 px-4 py-2.5"><input ref={selectAllRef} type="checkbox" checked={allVisibleSelected} onChange={toggleSelectAll} title="全选当前筛选结果" className="h-3.5 w-3.5 accent-indigo-600" /></th>
                <th className="px-3 py-2.5">发票号码</th>
                <th className="px-3 py-2.5">开票方</th>
                <th className="px-3 py-2.5">开票日期</th>
                <th className="px-3 py-2.5">方向</th>
                <th className="px-3 py-2.5">开票明细</th>
                <th className="px-3 py-2.5">发票类型</th>
                <th className="px-3 py-2.5 text-right">价税合计</th>
                <th className="px-3 py-2.5">票据状态</th>
                <th className="px-3 py-2.5">类别</th>
                <th className="px-3 py-2.5">付款方式</th>
                <th className="px-3 py-2.5">采购关联</th>
                <th className="px-3 py-2.5">入库关联</th>
                <th className="px-3 py-2.5">业务匹配</th>
                <th className="px-3 py-2.5">付款核对</th>
                <th className="px-4 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {visibleRows.map((row) => {
                const category = rowCategoryValue(row);
                const corporatePaymentVerified = isCorporatePaymentVerified(row);
                return (
                <tr key={row.id} className={`transition ${selectedId === row.id ? "bg-indigo-50/60" : corporatePaymentVerified ? "bg-emerald-100/95 hover:bg-emerald-200/95" : "hover:bg-indigo-50/40"}`}>
                  <td className={`px-4 py-2.5 ${corporatePaymentVerified ? "border-l-4 border-emerald-600 bg-emerald-200/80" : ""}`}><input type="checkbox" checked={selectedIds.has(row.id)} disabled={row.invoiceColor === "red"} onChange={() => toggleRowSelected(row.id)} onClick={(event) => event.stopPropagation()} title={row.invoiceColor === "red" ? "红字发票分类继承蓝字票，不单独批量分类" : undefined} className="h-3.5 w-3.5 accent-indigo-600 disabled:opacity-30" /></td>
                  <td className="px-3 py-2.5"><div className="flex flex-col items-start gap-1"><div className="font-mono font-medium text-slate-800">{invoiceNo(row)}</div>{corporatePaymentVerified && <span className="inline-flex items-center gap-1 rounded-md bg-emerald-700 px-1.5 py-0.5 text-[10px] font-bold text-white shadow-sm"><span aria-hidden="true">✓</span>对公已核对</span>}</div></td>
                  <td className="max-w-[210px] truncate px-3 py-2.5 text-slate-700" title={row.direction === "output" ? `销项抬头：${row.buyerName || "未记录购买方"} · 本单位：${row.sellerName || "—"}` : row.sellerName}>{row.direction === "output" ? <><span className="mr-1 inline-block rounded border border-violet-200 bg-violet-50 px-1 align-[-1px] text-[10px] leading-4 text-violet-700">购方</span><span className="font-medium">{row.buyerName || "未记录购买方"}</span><div className="mt-0.5 truncate text-[10px] text-slate-400">本单位：{row.sellerName || "—"}</div></> : (row.sellerName || "未记录开票方")}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-slate-500">{dateText(row.issueDate)}</td>
                  <td className="px-3 py-2.5"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${directionClass(row.direction)}`}>{directionLabel(row.direction)}</span></td>
                  <td className="max-w-[320px] truncate px-3 py-2.5 text-slate-700" title={lineSummary(row)}>{lineSummary(row)}</td>
                  <td className="px-3 py-2.5"><span className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium ${invoiceKindClass(row)}`}>{invoiceKindLabel(row)}</span></td>
                  <td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-700">{money(row.totalAmount)}</td>
                  <td className="px-3 py-2.5"><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${invoiceLifecycleClass(row)}`}>{row.invoiceStatusLabel}</span>{row.redRelatedInvoiceNo && <div className="mt-1 max-w-[180px] truncate text-[10px] text-rose-600" title={row.redRelatedInvoiceNo}>对应 {row.redRelatedInvoiceNo}</div>}{row.accountingException && <div className="mt-1 max-w-[220px] text-[10px] text-rose-600" title={row.accountingException}>{row.accountingException}</div>}</td>
                  <td className="px-3 py-2.5">
                    <div className="relative inline-flex items-center" onClick={(event) => event.stopPropagation()}>
                      <select
                        value={category}
                        onChange={(event) => void changeCategory(row.id, event.target.value)}
                        disabled={Boolean(row.categoryInherited) || row.invoiceColor === "red"}
                        title={row.categoryInherited ? "红字发票类别继承对应蓝字发票，请修改蓝字原票" : row.invoiceColor === "red" ? "红字发票需先关联对应蓝字票，不能独立分类" : "点击修改分类"}
                        className={`cursor-pointer appearance-none whitespace-nowrap rounded-full border py-0.5 pl-2 pr-5 text-[10px] font-medium outline-none transition hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-70 ${rowCategoryClass(row, category)} ${category === "" ? "border-dashed" : ""}`}
                      >
                        {rowCategoryOptions(row).map((value) => (
                          <option key={value || "pending"} value={value}>{rowCategoryLabel(row, value)}</option>
                        ))}
                      </select>
                      <span className="pointer-events-none absolute right-1.5 text-[8px] text-current opacity-50">▾</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    {row.direction !== "input" || row.bankPaymentStatus === "not_applicable" ? (
                      <span className="text-slate-300">—</span>
                    ) : row.bankPaymentStatus === "matched" ? (
                      corporatePaymentVerified ? (
                        <span title="银行流水已全额核对，付款方式为对公账户支出" className="whitespace-nowrap rounded-full border border-emerald-300 bg-emerald-600 px-2 py-0.5 text-[10px] font-semibold text-white shadow-sm">✓ 对公已核对</span>
                      ) : <span className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium ${paymentMethodClass("corporate")}`}>对公账户支出</span>
                    ) : (
                      <select
                        value={row.manualPaymentMethod || ""}
                        onChange={(event) => void changePaymentMethod(row.id, event.target.value as "personal" | "platform_auto_debit" | "")}
                        onClick={(event) => event.stopPropagation()}
                        title={row.bankPaymentStatus === "partial" ? "银行已部分付款；可标记剩余部分是否个人垫付" : "无银行付款证据时可标记个人垫付或平台自动扣款货款"}
                        className={`max-w-[150px] rounded-full border px-2 py-0.5 text-[10px] font-medium outline-none ${paymentMethodClass(row.paymentMethod || "")}`}
                      >
                        <option value="">{row.bankPaymentStatus === "partial" ? "对公部分付款" : "未设置"}</option>
                        <option value="personal">{row.bankPaymentStatus === "partial" ? "对公 + 个人垫付" : "个人垫付"}</option>
                        {(row.bankPaymentStatus === "unmatched" || row.manualPaymentMethod === "platform_auto_debit") && <option value="platform_auto_debit">平台自动扣款货款</option>}
                      </select>
                    )}
                  </td>
                  <td className="max-w-[210px] px-3 py-2.5 text-slate-600">{row.purchaseOrderNos.length ? <span title={row.purchaseOrderNos.join("、")}>{row.purchaseOrderNos.slice(0, 2).join("、")}{row.purchaseOrderNos.length > 2 ? ` 等 ${row.purchaseOrderNos.length} 单` : ""}</span> : <span className="text-slate-400">未匹配</span>}</td>
                  <td className="max-w-[190px] px-3 py-2.5 text-slate-600">{row.inboundNos.length ? <span title={row.inboundNos.join("、")}>{row.inboundNos.slice(0, 2).join("、")}{row.inboundNos.length > 2 ? ` 等 ${row.inboundNos.length} 单` : ""}</span> : <span className="text-slate-400">—</span>}</td>
                  <td className="px-3 py-2.5"><span title={`业务已匹配 ${money(row.businessMatchedAmount)} / 剩余 ${money(row.businessRemainingAmount)}`} className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${matchClass(businessMatchStatus(row))}`}>{matchLabel(businessMatchStatus(row))}</span></td>
                  <td className="px-3 py-2.5">
                    {row.direction === "input" ? (
                      <span
                        title={row.bankPaymentStatus === "partial" || row.bankPaymentStatus === "matched" ? `已核对 ${money(row.bankPaidAmount)} / 剩余 ${money(row.bankRemainingAmount)}${corporatePaymentVerified ? " · 对公金额一致" : ""}` : undefined}
                        className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium ${bankPaymentClass(row)} ${corporatePaymentVerified ? "ring-1 ring-emerald-300" : ""}`}
                      >
                        {corporatePaymentVerified ? "✓ 金额一致" : bankPaymentLabel(row)}
                      </span>
                    ) : <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right"><button type="button" onClick={(event) => { event.stopPropagation(); setSelectedId(row.id); }} className="text-indigo-600 hover:text-indigo-800">查看详情</button></td>
                </tr>
                );
              })}
              {!loading && !visibleRows.length && <tr><td colSpan={16} className="px-4 py-16 text-center text-sm text-slate-400">{rows.length ? "没有符合当前筛选条件的发票" : "暂时没有发票，请从右上角上传税务发票清单"}</td></tr>}
              {loading && <tr><td colSpan={16} className="px-4 py-16 text-center text-sm text-slate-400">正在加载发票池…</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-4 py-2.5 text-[11px] text-slate-400">
          <span>当前显示 {visibleRows.length} 张 · 原始发票清单和导入批次永久保留</span>
          <Link href="/data-center-import?tab=tax" className="text-indigo-600 hover:underline">进入原始清单核对</Link>
        </div>
      </section>

      {selected && <InvoiceDetailModal row={selected} onRefresh={load} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

function purchaseTypeLabel(targetType: string) {
  return targetType === "jackyun_purchase_order" ? "吉客云采购单" : "1688 订单";
}

function purchaseTypeClass(targetType: string) {
  return targetType === "jackyun_purchase_order"
    ? "border-violet-200 bg-violet-50 text-violet-700"
    : "border-sky-200 bg-sky-50 text-sky-700";
}

function candidateTypeLabel(targetType: string) {
  return targetType === "sales_order" ? "销售订单" : purchaseTypeLabel(targetType);
}

function candidateTypeClass(targetType: string) {
  return targetType === "sales_order"
    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
    : purchaseTypeClass(targetType);
}

function amountDiffLabel(candidate: TaxInvoicePurchaseCandidate) {
  if (!candidate.amountDiff) return <span className="text-slate-300">—</span>;
  const diff = Number(candidate.amountDiff);
  if (!Number.isFinite(diff)) return <span className="text-slate-300">—</span>;
  if (Math.abs(diff) < 1) return <span className="font-medium text-emerald-600">金额一致</span>;
  return <span className={diff > 0 ? "text-amber-600" : "text-rose-600"}>{diff > 0 ? "+" : ""}{diff.toFixed(2)}</span>;
}

function InvoiceDetailModal({ row, onRefresh, onClose }: { row: TaxInvoiceRow; onRefresh: () => Promise<void>; onClose: () => void }) {
  const isOutput = row.direction === "output";
  const businessPurchaseTypes = new Set([
    "alibaba1688_order",
    "external_purchase_order",
    "jackyun_purchase_order",
  ]);
  const purchaseLinks = isOutput
    ? row.links.filter((link) => link.targetType === "sales_order")
    : row.links.filter((link) => businessPurchaseTypes.has(link.targetType));
  const [pickerOpen, setPickerOpen] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [candidates, setCandidates] = useState<TaxInvoicePurchaseCandidate[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [submitting, setSubmitting] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState(false);
  const [actionError, setActionError] = useState("");
  const [lines, setLines] = useState<TaxInvoiceLinesResponse | null>(null);
  const [linesLoading, setLinesLoading] = useState(false);
  const [linesError, setLinesError] = useState("");
  // 工作区下所有 Tab 都常驻挂载：隐藏 Tab 不能响应全局 Esc，否则在别的 Tab 按 Esc 会关掉这里的弹窗
  const tabActive = useTabActive();

  useEffect(() => {
    let cancelled = false;
    setLines(null);
    setLinesError("");
    setLinesLoading(true);
    taxInvoiceApi.lines(row.id)
      .then((data) => { if (!cancelled) setLines(data); })
      .catch((caught) => { if (!cancelled) setLinesError(caught instanceof Error ? caught.message : String(caught)); })
      .finally(() => { if (!cancelled) setLinesLoading(false); });
    return () => { cancelled = true; };
  }, [row.id]);

  useEffect(() => {
    if (!tabActive) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (pickerOpen) setPickerOpen(false);
      else onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickerOpen, onClose, tabActive]);

  async function searchCandidates(value: string) {
    setSearching(true);
    setActionError("");
    try {
      const found = await taxInvoiceApi.purchaseCandidates(row.id, value);
      setCandidates(found);
      setSearched(true);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSearching(false);
    }
  }

  async function handleCategoryChange(category: string) {
    setActionError("");
    try {
      await taxInvoiceApi.bulkSetCategory([row.id], category);
      await onRefresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  async function handlePaymentMethodChange(paymentMethod: string) {
    setActionError("");
    try {
      await taxInvoiceApi.bulkSetPaymentMethod([row.id], paymentMethod);
      await onRefresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  function openPicker() {
    const initial = isOutput ? row.buyerName || "" : row.sellerName || "";
    setKeyword(initial);
    setCandidates([]);
    setSearched(false);
    setActionError("");
    setPickerOpen(true);
    void searchCandidates(initial);
  }

  async function handleLink(candidate: TaxInvoicePurchaseCandidate) {
    setSubmitting(`${candidate.targetType}-${candidate.targetId}`);
    setActionError("");
    try {
      await taxInvoiceApi.linkPurchase(row.id, { targetType: candidate.targetType, targetId: candidate.targetId });
      setPickerOpen(false);
      await onRefresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSubmitting(null);
    }
  }

  async function handleUnlink(link: TaxInvoiceRow["links"][number]) {
    const targetDesc = link.targetType === "sales_order" ? `订单 ${link.targetNo}` : link.targetLabel;
    if (!window.confirm(`确认解除与「${targetDesc}」的关联？解除会保留审计记录，之后可重新关联。`)) return;
    setUnlinking(true);
    setActionError("");
    try {
      await taxInvoiceApi.unlinkPurchase(row.id, link.id);
      await onRefresh();
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setUnlinking(false);
    }
  }

  async function handleResolveRedBlue() {
    setActionError("");
    try {
      const found = await taxInvoiceApi.redBlueCandidates(row.id, row.sellerName || "");
      if (!found.length) { setActionError("没有找到可选蓝字发票，请先确认蓝票已经导入。"); return; }
      const preview = found.slice(0, 8).map((item) => `#${item.invoiceId} ${item.invoiceNumber} ${money(item.totalAmount)} ${item.taxPartyMatched ? "税号一致" : "税号待核"}`).join("\n");
      const input = window.prompt(`输入要关联的蓝字发票 ID：\n${preview}`, String(found[0].invoiceId));
      if (!input) return;
      const blueId = Number(input);
      if (!Number.isInteger(blueId) || blueId <= 0) { setActionError("蓝字发票 ID 无效"); return; }
      await taxInvoiceApi.setRedBlueLink(row.id, blueId, "财务人工确认红蓝关系");
      await onRefresh();
    } catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function handleClearRedBlue() {
    if (!window.confirm("确认解除人工红蓝关联？解除后系统会重新按官方发票号码自动识别。")) return;
    setActionError("");
    try { await taxInvoiceApi.clearRedBlueLink(row.id); await onRefresh(); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function handleAddRedSettlement(type: "bank_refund_transaction" | "future_invoice_offset" | "personal_refund" | "other_red_settlement") {
    setActionError("");
    try {
      const defaultAmount = row.redSettlementRemainingAmount || row.redSettlementTargetAmount || String(Math.abs(Number(row.totalAmount || 0)));
      let targetId: number | null = null;
      if (type === "bank_refund_transaction") {
        const found = await taxInvoiceApi.refundCandidates(row.id, row.sellerName || "");
        if (!found.length) { setActionError("没有找到银行收入流水，请先导入银行流水或改用其他处理方式。"); return; }
        const preview = found.slice(0, 8).map((item) => `#${item.txnId} ${item.txnDate} ${money(item.amount)} ${item.counterpartyName || "未记录对方"}${item.amountMatched ? " · 金额一致" : ""}`).join("\n");
        const input = window.prompt(`输入供应商退款银行流水 ID：\n${preview}`, String(found[0].txnId));
        if (!input) return; targetId = Number(input);
      } else if (type === "future_invoice_offset") {
        const found = await taxInvoiceApi.redBlueCandidates(row.id, row.sellerName || "");
        const preview = found.slice(0, 8).map((item) => `#${item.invoiceId} ${item.invoiceNumber} ${money(item.totalAmount)}`).join("\n");
        const input = window.prompt(`输入用于冲抵的后续蓝字进项发票 ID：\n${preview}`, found[0] ? String(found[0].invoiceId) : "");
        if (!input) return; targetId = Number(input);
      }
      if (targetId !== null && (!Number.isInteger(targetId) || targetId <= 0)) { setActionError("目标 ID 无效"); return; }
      const amountText = window.prompt("输入本次处理金额：", defaultAmount);
      if (!amountText) return;
      const amount = Number(amountText);
      if (!Number.isFinite(amount) || amount <= 0) { setActionError("处理金额必须大于 0"); return; }
      const note = window.prompt("备注（可选）：", "") || "";
      await taxInvoiceApi.addRedSettlement(row.id, { settlementType: type, amount, targetId, note });
      await onRefresh();
    } catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function handleRemoveRedSettlement(linkId: number) {
    if (!window.confirm("确认解除这条红冲结算记录？审计历史会保留。")) return;
    setActionError("");
    try { await taxInvoiceApi.removeRedSettlement(row.id, linkId); await onRefresh(); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function handleVatDeductible(status: "pending" | "deductible" | "non_deductible") {
    setActionError("");
    try {
      await taxInvoiceApi.setVatReview(row.id, { vatDeductibleStatus: status });
      await onRefresh();
    } catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  async function handleVatTransfer(status: "required_confirmation" | "completed") {
    setActionError("");
    try {
      await taxInvoiceApi.setVatReview(row.id, {
        inputVatTransferStatus: status,
        inputVatTransferAmount: row.inputVatTransferAmount || "0",
      });
      await onRefresh();
    } catch (caught) { setActionError(caught instanceof Error ? caught.message : String(caught)); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/45 p-4 sm:p-8" onClick={onClose}>
      <div className="w-full max-w-4xl overflow-hidden rounded-xl bg-white shadow-xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div>
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-mono text-sm font-semibold text-slate-900">{invoiceNo(row)}</h2><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${directionClass(row.direction)}`}>{directionLabel(row.direction)}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${matchClass(businessMatchStatus(row))}`}>{matchLabel(businessMatchStatus(row))}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${invoiceLifecycleClass(row)}`}>{row.invoiceStatusLabel}</span>
            {row.direction === "input" && (
              <>
                <span className="relative inline-flex items-center" onClick={(event) => event.stopPropagation()}>
                  <select value={invoiceCategory(row)} onChange={(event) => void handleCategoryChange(event.target.value)} disabled={Boolean(row.categoryInherited) || row.invoiceColor === "red"} title={row.categoryInherited ? "红字发票分类继承蓝字原票" : "点击修改分类（分类决定统计口径）"} className={`cursor-pointer appearance-none whitespace-nowrap rounded-full border py-0.5 pl-2 pr-5 text-[10px] font-medium outline-none transition hover:opacity-80 ${categoryClass(invoiceCategory(row))} ${invoiceCategory(row) === "" ? "border-dashed" : ""}`}>
                    {(["goods", "platform_fee", "operating_other", "reimburse_advance", "reimburse_operating", "excluded", ""] as const).map((value) => (
                      <option key={value || "pending"} value={value}>{categoryLabel(value)}</option>
                    ))}
                  </select>
                  <span className="pointer-events-none absolute right-1.5 text-[8px] text-current opacity-50">▾</span>
                </span>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${derivedClass(derivedGroup(invoiceCategory(row)))}`}>{derivedLabel(derivedGroup(invoiceCategory(row)))}</span>
              </>
            )}
            {row.direction === "output" && (
              <span className="relative inline-flex items-center" onClick={(event) => event.stopPropagation()}>
                <select value={row.category || ""} onChange={(event) => void handleCategoryChange(event.target.value)} disabled={Boolean(row.categoryInherited) || row.invoiceColor === "red"} title={row.categoryInherited ? "红字发票分类继承蓝字原票" : "点击修改销项分类"} className={`cursor-pointer appearance-none whitespace-nowrap rounded-full border py-0.5 pl-2 pr-5 text-[10px] font-medium outline-none transition hover:opacity-80 ${rowCategoryClass(row, row.category || "")} ${!row.category ? "border-dashed" : ""}`}>
                  {(["buyer_sales", "platform_service", ""] as const).map((value) => (
                    <option key={value || "pending"} value={value}>{outputCategoryLabel(value)}</option>
                  ))}
                </select>
                <span className="pointer-events-none absolute right-1.5 text-[8px] text-current opacity-50">▾</span>
              </span>
            )}
            {row.direction === "input" && row.bankPaymentStatus !== "not_applicable" && (
              row.bankPaymentStatus === "matched" ? (
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${paymentMethodClass("corporate")}`}>
                  对公账户支出
                </span>
              ) : (
                <span className="relative inline-flex items-center" onClick={(event) => event.stopPropagation()}>
                  <select
                    value={row.manualPaymentMethod || ""}
                    onChange={(event) => void handlePaymentMethodChange(event.target.value)}
                    title={row.bankPaymentStatus === "partial" ? "银行已部分付款；可标记剩余部分个人垫付" : "可人工标记个人垫付或平台自动扣款货款；对公付款必须由银行核对产生"}
                    className={`cursor-pointer appearance-none whitespace-nowrap rounded-full border py-0.5 pl-2 pr-5 text-[10px] font-medium outline-none ${paymentMethodClass(row.paymentMethod || "")}`}
                  >
                    <option value="">{row.bankPaymentStatus === "partial" ? "对公部分付款" : "付款方式未设置"}</option>
                    <option value="personal">{row.bankPaymentStatus === "partial" ? "对公 + 个人垫付" : "个人垫付"}</option>
                    {(row.bankPaymentStatus === "unmatched" || row.manualPaymentMethod === "platform_auto_debit") && <option value="platform_auto_debit">平台自动扣款货款</option>}
                  </select>
                  <span className="pointer-events-none absolute right-1.5 text-[8px] text-current opacity-50">▾</span>
                </span>
              )
            )}</div>
            <div className="mt-1 text-xs text-slate-500">{row.sellerName || "未记录开票方"} · 开票日期 {dateText(row.issueDate)} · 价税合计 {money(row.totalAmount)}</div>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭详情" className="rounded-md px-2 py-1 text-base leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-600">✕</button>
        </div>
        <div className="border-b border-slate-100 px-5 py-4">
          <div className="mb-2 text-xs font-semibold text-slate-700">货物明细{lines ? `（${lines.total} 行）` : ""}</div>
          {linesError && <div className="mb-2 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] text-rose-700">明细加载失败：{linesError}</div>}
          {linesLoading ? (
            <div className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-xs text-slate-400">正在加载明细…</div>
          ) : lines ? (
            lines.items.length ? (
              <div className="max-h-[40vh] overflow-auto rounded-lg border border-slate-200">
                <table className="w-full min-w-[720px] border-collapse text-xs">
                  <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                    <tr>
                      <th className="px-3 py-2.5">货物名称</th>
                      <th className="px-3 py-2.5">规格型号</th>
                      <th className="px-3 py-2.5">单位</th>
                      <th className="px-3 py-2.5 text-right">数量</th>
                      <th className="px-3 py-2.5 text-right">单价</th>
                      <th className="px-3 py-2.5 text-right">金额</th>
                      <th className="px-3 py-2.5">税率</th>
                      <th className="px-3 py-2.5 text-right">税额</th>
                      <th className="px-3 py-2.5 text-right">价税合计</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {lines.items.map((line, index) => (
                      <tr key={index} className="hover:bg-indigo-50/30">
                        <td className="px-3 py-2 text-slate-700">{line.goodsName ?? "—"}</td>
                        <td className="px-3 py-2 text-slate-500">{line.spec ?? "—"}</td>
                        <td className="px-3 py-2 text-slate-500">{line.unit ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{line.quantity ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{line.unitPrice ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{money(line.amount)}</td>
                        <td className="px-3 py-2 text-slate-500">{line.taxRate ?? "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{money(line.taxAmount)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-slate-700">{money(line.totalAmount)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="bg-slate-50/70 font-medium text-slate-700">
                      <td className="border-t border-slate-200 px-3 py-2" colSpan={5}>合计</td>
                      <td className="border-t border-slate-200 px-3 py-2 text-right tabular-nums">{money(lines.sumAmount)}</td>
                      <td className="border-t border-slate-200 px-3 py-2" />
                      <td className="border-t border-slate-200 px-3 py-2 text-right tabular-nums">{money(lines.sumTax)}</td>
                      <td className="border-t border-slate-200 px-3 py-2 text-right tabular-nums">{money(lines.sumTotal)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-slate-200 px-3 py-6 text-center text-xs text-slate-400">这张发票的清单里没有货物明细行</div>
            )
          ) : null}
        </div>
      {row.redStatus !== "none" && (
        <div className={`border-b px-4 py-3 text-xs ${row.accountingException ? "border-rose-200 bg-rose-100 text-rose-900" : "border-rose-100 bg-rose-50/70 text-rose-800"}`}>
          <div><b>红冲状态：</b>{row.invoiceStatusLabel}。{row.redStatus === "partially_red_offset" ? ` 原票 ${money(row.totalAmount)}，累计红冲 ${money(row.redOffsetAmount)}，剩余有效 ${money(row.remainingAfterRedAmount)}。` : row.redStatus === "fully_red_offset" ? ` 原票已被全额冲销，剩余有效金额 ${money(row.remainingAfterRedAmount)}。` : row.invoiceColor === "red" ? " 该红字发票以负数冲减对应蓝字原票。" : ""}{row.redRelatedInvoiceNo ? ` 对应发票：${row.redRelatedInvoiceNo}。` : ""}{row.redRelatedInvoicePeriod ? ` 原蓝票账期：${row.redRelatedInvoicePeriod}。` : ""}{row.redCrossPeriod ? "（跨期红冲）" : ""}{row.redPairMethod === "manual" ? "（人工确认关联）" : ""}{row.redNoticeNo ? ` 确认单：${row.redNoticeNo}。` : ""}{row.accountingException ? ` 异常：${row.accountingException}` : ""}</div>
          {row.invoiceColor === "red" && (
            <div className="mt-2 flex flex-wrap gap-2">
              {(row.redPairStatus === "unpaired" || row.redPairStatus === "ambiguous" || !row.redRelatedInvoiceId) && <button type="button" onClick={() => void handleResolveRedBlue()} className="rounded-md border border-rose-300 bg-white px-2.5 py-1 text-[11px] font-medium text-rose-700 hover:bg-rose-50">人工指定蓝字原票</button>}
              {row.redPairMethod === "manual" && <button type="button" onClick={() => void handleClearRedBlue()} className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-[11px] text-slate-600 hover:bg-slate-50">解除人工关联</button>}
            </div>
          )}
        </div>
      )}
      {(Number(row.bankOverpaidAmount || 0) > 0 || Number(row.businessOvermatchedAmount || 0) > 0) && (
        <div className="border-b border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800">
          {Number(row.bankOverpaidAmount || 0) > 0 && <div><b>红冲后超额付款：</b>有效金额 {money(row.bankEffectiveInvoiceAmount)}，历史对公已付 {money(row.bankPaidAmount)}，历史超额 {money(row.bankOverpaidAmount)}，已处理 {money(row.bankOverpaidSettledAmount || "0")}，当前待退款/冲抵 {money(row.bankOverpaidUnsettledAmount || row.bankOverpaidAmount)}。</div>}
          {Number(row.businessOvermatchedAmount || 0) > 0 && <div className="mt-1"><b>红冲后业务超额关联：</b>{row.businessMatchException || `超额 ${money(row.businessOvermatchedAmount)}`}。系统不会自动篡改历史人工关联，请核对处理。</div>}
        </div>
      )}
      {row.direction === "input" && (
        <div className="border-b border-slate-100 bg-white px-4 py-3 text-xs text-slate-600">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <b>增值税状态：</b>
              进项抵扣 {row.vatDeductibleStatus === "non_deductible" ? "不可抵扣" : row.vatDeductibleStatus === "deductible" ? "可抵扣（人工确认）" : row.vatDeductibleStatus === "verified_pending_tax_filing" ? "已认证，待申报事实确认" : row.vatDeductibleStatus === "pending" ? "待确认" : "不适用"}
              {row.vatDeductibleManual && <span className="ml-1 text-indigo-500">· 人工确认</span>}
              {row.inputVatTransferStatus === "required_confirmation" && <span className="ml-3 font-medium text-rose-700">红冲涉及进项税转出待确认 {money(row.inputVatTransferAmount)}</span>}
              {row.inputVatTransferStatus === "completed" && <span className="ml-3 font-medium text-emerald-700">进项税转出已确认完成 {money(row.inputVatTransferAmount)}</span>}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {row.invoiceColor !== "red" && <>
                <button type="button" onClick={() => void handleVatDeductible("deductible")} className="rounded border border-emerald-200 px-2 py-1 text-[11px] text-emerald-700 hover:bg-emerald-50">确认可抵扣</button>
                <button type="button" onClick={() => void handleVatDeductible("non_deductible")} className="rounded border border-amber-200 px-2 py-1 text-[11px] text-amber-700 hover:bg-amber-50">确认不可抵扣</button>
                <button type="button" onClick={() => void handleVatDeductible("pending")} className="rounded border border-slate-200 px-2 py-1 text-[11px] text-slate-500 hover:bg-slate-50">改回待确认</button>
              </>}
              {row.invoiceColor === "red" && row.inputVatTransferStatus === "required_confirmation" && <button type="button" onClick={() => void handleVatTransfer("completed")} className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] text-emerald-700">确认已转出</button>}
              {row.invoiceColor === "red" && row.inputVatTransferStatus === "completed" && <button type="button" onClick={() => void handleVatTransfer("required_confirmation")} className="rounded border border-slate-200 px-2 py-1 text-[11px] text-slate-500">撤回为待确认</button>}
            </div>
          </div>
        </div>
      )}
      {row.invoiceColor === "red" && row.direction === "input" && (
        <div className="border-b border-slate-100 bg-slate-50/70 px-4 py-3 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><b className="text-slate-700">红冲资金结算：</b><span className="text-slate-500">已处理 {money(row.redSettledAmount)} / 红字金额 {money(row.redSettlementTargetAmount)} · 待处理 {money(row.redSettlementRemainingAmount)}</span></div>
            <div className="flex flex-wrap gap-1.5">
              <button type="button" onClick={() => void handleAddRedSettlement("bank_refund_transaction")} className="rounded-md border border-emerald-200 bg-white px-2 py-1 text-[11px] text-emerald-700 hover:bg-emerald-50">匹配供应商退款</button>
              <button type="button" onClick={() => void handleAddRedSettlement("future_invoice_offset")} className="rounded-md border border-blue-200 bg-white px-2 py-1 text-[11px] text-blue-700 hover:bg-blue-50">抵扣后续货款</button>
              <button type="button" onClick={() => void handleAddRedSettlement("personal_refund")} className="rounded-md border border-amber-200 bg-white px-2 py-1 text-[11px] text-amber-700 hover:bg-amber-50">退回个人垫付</button>
              <button type="button" onClick={() => void handleAddRedSettlement("other_red_settlement")} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50">其他处理</button>
            </div>
          </div>
          {!!row.redSettlements?.length && <div className="mt-2 space-y-1">{row.redSettlements.map((item) => <div key={item.linkId} className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2 py-1"><span>{item.targetLabel} · {money(item.amount)}{item.note ? ` · ${item.note}` : ""}</span><button type="button" onClick={() => void handleRemoveRedSettlement(item.linkId)} className="text-rose-500 hover:text-rose-700">解除</button></div>)}</div>}
        </div>
      )}
      <div className="grid gap-3 border-b border-slate-100 bg-slate-50/50 px-4 py-3 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <Info label="销方税号" value={row.sellerTaxId || "—"} />
        <Info label="购方" value={row.buyerName || "—"} />
        <Info label="购方税号" value={row.buyerTaxId || "—"} />
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-slate-700">{isOutput ? "销售订单关联" : "采购关联"}</span>
            <button type="button" onClick={openPicker} disabled={row.invoiceColor === "red"} title={row.invoiceColor === "red" ? "红字发票业务关系继承对应蓝字票，不单独关联订单" : undefined} className="rounded-md border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-40">{isOutput ? "+ 关联销售订单" : "+ 关联采购单"}</button>
          </div>
          {actionError && <div className="mb-2 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] text-rose-700">{actionError}</div>}
          {purchaseLinks.length ? (
            <div className="space-y-2">
              {purchaseLinks.map((link) => (
                <div key={`${link.targetType}-${link.targetId}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2">
                  <span className="text-slate-700">{link.targetLabel}</span>
                  <span className="flex items-center gap-3 text-[11px] text-indigo-600">
                    {link.allocatedAmount === null ? "未分配金额" : money(String(link.allocatedAmount))} · {link.confirmed ? "已确认" : "待确认"}
                    <button type="button" onClick={() => void handleUnlink(link)} disabled={unlinking} className="text-rose-500 hover:text-rose-700 disabled:cursor-wait disabled:opacity-50">解除</button>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-slate-200 px-3 py-5 text-center text-xs text-slate-400">{isOutput ? "尚未关联销售订单" : "尚未匹配采购单"}</div>
          )}
        </div>
        <div>
          <div className="mb-2 text-xs font-semibold text-slate-700">入库追溯</div>
          {row.inboundNos.length ? <div className="flex flex-wrap gap-2">{row.inboundNos.map((no) => <Link key={no} href="/supply-chain/receiving" className="rounded-lg border border-blue-100 bg-blue-50/60 px-3 py-2 font-mono text-xs text-blue-700 hover:bg-blue-100">{no}</Link>)}</div> : <div className="rounded-lg border border-dashed border-slate-200 px-3 py-5 text-center text-xs text-slate-400">{isOutput ? "销项发票不涉及入库追溯" : "已匹配采购单，但还没有关联入库单"}</div>}
        </div>
      </div>
      {row.matchNote && <div className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500">匹配说明：{row.matchNote}</div>}
      {pickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={(event) => { event.stopPropagation(); setPickerOpen(false); }}>
          <div className="flex max-h-[82vh] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-slate-900">{isOutput ? "关联销售订单" : "关联采购单"}</div>
                <div className="mt-0.5 text-[11px] text-slate-400">发票 {invoiceNo(row)} · 价税合计 {money(row.totalAmount)}</div>
              </div>
              <button type="button" onClick={() => setPickerOpen(false)} className="rounded-md px-2 py-1 text-xs text-slate-400 hover:bg-slate-100 hover:text-slate-600">关闭</button>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-3">
              <input
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") void searchCandidates(keyword); }}
                placeholder={isOutput ? "按订单号或买家/客户搜索，留空则推荐开票当月或最近订单" : "按单号或供应商搜索，留空则按发票销方推荐最近单据"}
                className="min-w-[240px] flex-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs outline-none focus:border-indigo-400"
              />
              <button type="button" onClick={() => void searchCandidates(keyword)} disabled={searching} className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-indigo-700 disabled:cursor-wait disabled:opacity-50">{searching ? "搜索中…" : "搜索"}</button>
            </div>
            {actionError && <div className="border-b border-rose-100 bg-rose-50 px-4 py-2 text-[11px] text-rose-700">{actionError}</div>}
            <div className="overflow-auto">
              <table className="w-full min-w-[760px] border-collapse text-xs">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr>
                    <th className="px-3 py-2.5">类型</th>
                    <th className="px-3 py-2.5">单号</th>
                    <th className="px-3 py-2.5">{isOutput ? "买家" : "供应商"}</th>
                    <th className="px-3 py-2.5">日期</th>
                    <th className="px-3 py-2.5 text-right">金额</th>
                    <th className="px-3 py-2.5">金额差</th>
                    <th className="px-3 py-2.5">占用</th>
                    <th className="px-4 py-2.5 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {candidates.map((candidate) => {
                    const key = `${candidate.targetType}-${candidate.targetId}`;
                    const occupied = candidate.linkedInvoiceId !== null && candidate.linkedInvoiceId !== row.id;
                    return (
                      <tr key={key} className="hover:bg-indigo-50/30">
                        <td className="px-3 py-2.5"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${candidateTypeClass(candidate.targetType)}`}>{candidateTypeLabel(candidate.targetType)}</span></td>
                        <td className="px-3 py-2.5 font-mono text-slate-800">{candidate.orderNo || `#${candidate.targetId}`}</td>
                        <td className="max-w-[180px] truncate px-3 py-2.5 text-slate-600" title={candidate.targetType === "sales_order" ? candidate.buyer || "" : candidate.supplier}>{candidate.targetType === "sales_order" ? (candidate.buyer || "—") : (candidate.supplier || "—")}</td>
                        <td className="whitespace-nowrap px-3 py-2.5 text-slate-500">{candidate.orderDate ? dateText(candidate.orderDate) : "—"}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{money(candidate.amount)}</td>
                        <td className="px-3 py-2.5">{amountDiffLabel(candidate)}</td>
                        <td className="px-3 py-2.5">{occupied ? <span className="rounded-full bg-rose-50 px-2 py-0.5 text-[10px] text-rose-600">已关联 {candidate.linkedInvoiceNo}</span> : <span className="text-slate-300">—</span>}</td>
                        <td className="px-4 py-2.5 text-right">
                          <button type="button" onClick={() => void handleLink(candidate)} disabled={submitting !== null} className="rounded-md border border-indigo-200 bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 disabled:cursor-wait disabled:opacity-50">{submitting === key ? "关联中…" : occupied ? "仍要关联" : "关联"}</button>
                        </td>
                      </tr>
                    );
                  })}
                  {!searching && searched && !candidates.length && <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-400">{isOutput ? "没有找到匹配的销售订单，换个关键词试试" : "没有找到匹配的采购单，换个关键词试试"}</td></tr>}
                  {searching && <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-400">{isOutput ? "正在搜索候选销售订单…" : "正在搜索候选采购单…"}</td></tr>}
                </tbody>
              </table>
            </div>
            <div className="border-t border-slate-100 px-4 py-2 text-[11px] text-slate-400">关联后发票标记为已匹配；被其他发票占用的{isOutput ? "销售订单" : "采购单"}也可强制关联，请先核对金额。</div>
          </div>
        </div>
      )}
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><div className="text-[10px] text-slate-400">{label}</div><div className="mt-1 truncate text-slate-700" title={value}>{value}</div></div>;
}
