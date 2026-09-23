"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { authenticatedFetch } from "@/lib/api";
import { useTabRuntime, useTabScopedState, useTabTitle, useWorkspace } from "@/lib/workspace/tab-store";

type Pkg = { id: number; version: number; status: string; sha256: string; createdAt: string | null };
type LegalEntity = {
  id: number;
  code: string;
  name: string;
  countryCode: string;
  baseCurrency: string;
  isDefault: boolean;
  businessScopes: string[];
};
type Period = {
  company: string;
  year: number;
  month: number;
  status: string;
  missing: Record<string, number>;
  fileCount: number;
  packages: Pkg[];
};
type SalesField = { key: string; label: string; enabled: boolean };
type SalesTemplate = {
  id: number;
  company: string;
  name: string;
  enabled: boolean;
  fields: SalesField[];
  rules: Record<string, string>;
  toAddrs: string[];
  ccAddrs: string[];
  autoSend: boolean;
  sendDay: number;
  sendHour: number;
};
type SalesPreview = {
  year: number;
  month: number;
  rowCount: number;
  fields: SalesField[];
  summary: { orderCount: number; warehouseCount: number; totalQuantity: string; salesAmount: string; costAmount: string; costIncomplete?: boolean; costMissingDetail?: { skuCode: string; skuName: string; quantity: string }[] };
  rows: Array<Record<string, string>>;
};
type Unbilled = {
  period: string;
  version?: number | null;
  adjusted?: boolean;
  selectedKeys?: string[];
  sourceCount?: number;
  selectedCount?: number;
  updatedAt?: string | null;
  salesAmount: string;
  redSalesAdjustmentAmount?: string;
  adjustedSalesAmount?: string;
  invoicedAmount: string;
  unbilledAmount: string;
  rowInvoicedTotal?: string;
  unattributedInvoiced?: string;
  sourceDetails?: Array<{ period: string; taxCode: string; taxName: string; product: string; quantity: string; sales: string; redSalesAdjustment?: string; adjustedSales?: string; invoiced?: string; unbilled?: string; cost: string }>;
  details: Array<{ period: string; taxCode: string; taxName: string; product: string; quantity: string; sales: string; redSalesAdjustment?: string; adjustedSales?: string; invoiced?: string; unbilled?: string; cost: string }>;
};
type FileRow = {
  id: number;
  category: string;
  originalName: string;
  size: number;
  sha256: string;
  version: number;
  uploader: string;
  uploadedAt: string | null;
};
type BusinessStatus = {
  ready: boolean;
  source: string;
  salesSource: string;
  costSource: string;
  salesOrderCount: number;
  warehouseCount: number;
  totalQuantity: string;
  salesAmount: string;
  costAmount: string;
  costIncomplete: boolean;
  costMissingCount: number;
  costMissingDetail: Array<{ skuCode: string; skuName: string; quantity: string }>;
  legalEntityId: number;
  company: string;
  businessScopes: string[];
  domesticSupported: boolean;
  foreignEntryCount: number;
  foreignTotalsByCurrency: Array<{
    currency: string;
    income: number;
    expense: number;
    profit: number;
    cashIn: number;
    cashOut: number;
    netCash: number;
  }>;
  sync: {
    created: number;
    updated: number;
    deleted: number;
    sources: Record<string, number>;
  };
};
type MailStatus = { configured: boolean; host: string; port: number; username: string; from: string };
type PaymentMatchInvoice = {
  linkId: number | null;
  invoiceId: number;
  invoiceNumber: string;
  sellerName: string;
  issueDate: string;
  totalAmount: string;
  allocatedAmount: string | null;
};
type PaymentMatchRow = {
  id: number;
  txnDate: string;
  counterpartyName: string;
  counterpartyAccount?: string;
  amount: string;
  voucherNo: string;
  summary: string;
  accountNo?: string;
  accountName?: string;
  invoices: PaymentMatchInvoice[];
  matchedAmount: string;
  remaining: string;
  status: "matched" | "partial" | "unmatched" | string;
  suggestedInvoiceIds: number[];
};
type PaymentMatchPoolInvoiceLink = PaymentMatchInvoice & {
  txnId: number | null;
  txnDate: string;
  txnAmount: string;
  counterpartyName: string;
  counterpartyAccount: string;
  voucherNo: string;
  summary: string;
  accountNo: string;
  accountName: string;
};
type PaymentMatchPoolInvoice = {
  id: number;
  invoiceNumber: string;
  sellerName: string;
  issueDate: string;
  totalAmount: string;
  bankLinkedAmount: string;
  remaining: string;
  bankMatchStatus: "matched" | "partial" | "unmatched" | "overpaid_after_red" | "red_overpayment_settled" | string;
  overpaidAfterRedAmount?: string;
  overpaidSettledAmount?: string;
  overpaidUnsettledAmount?: string;
  links: PaymentMatchPoolInvoiceLink[];
  suggested: boolean;
  suggestedPaymentIds: number[];
};
type PaymentMatchOverview = {
  year: number;
  month: number;
  payments: PaymentMatchRow[];
  invoicePool: PaymentMatchPoolInvoice[];
  summary: {
    paymentTotal: string;
    matchedTotal: string;
    unmatchedTotal: string;
    txnCount: number;
    matchedCount: number;
    partialCount: number;
    unmatchedCount: number;
    invoiceTotal: string;
    invoiceMatchedTotal: string;
    invoiceOutstandingTotal: string;
    invoiceCount: number;
    invoiceMatchedCount: number;
    invoicePartialCount: number;
    invoiceUnmatchedCount: number;
    invoiceOverpaidAfterRedCount?: number;
    invoiceOverpaidAfterRedTotal?: string;
  };
};

type CorporatePaymentRow = {
  linkId: number;
  paymentId: number;
  paymentDate: string;
  paymentAccount: string;
  paymentAccountName: string;
  supplierName: string;
  supplierTaxId: string;
  counterpartyAccount: string;
  voucherNo: string;
  summary: string;
  paymentAmount: string;
  paymentAllocatedAmount: string;
  paymentMatchedTotal: string;
  paymentStatus: string;
  invoiceId: number;
  invoiceNumber: string;
  invoiceDate: string;
  invoiceType: string;
  invoiceAmountExclTax: string;
  invoiceTaxAmount: string;
  invoiceTotalAmount: string;
  invoiceCorporatePaidTotal: string;
  invoiceOutstandingAmount: string;
  invoiceStatus: string;
  purchaseOrderNos: string[];
};
type CorporateInvoicePayment = {
  linkId: number;
  paymentId: number;
  paymentDate: string;
  paymentAccount: string;
  paymentAccountName: string;
  counterpartyAccount: string;
  voucherNo: string;
  summary: string;
  paymentAmount: string;
  allocatedAmount: string;
  paymentMatchedTotal: string;
  paymentStatus: string;
};
type CorporateInvoiceRow = {
  invoiceId: number;
  invoiceKey: string;
  invoiceNumber: string;
  invoiceDate: string;
  invoiceType: string;
  supplierName: string;
  supplierTaxId: string;
  invoiceAmountExclTax: string;
  invoiceTaxAmount: string;
  invoiceTotalAmount: string;
  invoiceCorporatePaidTotal: string;
  invoiceOutstandingAmount: string;
  invoiceOverpaidAmount?: string;
  invoiceOverpaidSettledAmount?: string;
  invoiceOverpaidUnsettledAmount?: string;
  invoiceEffectiveAmount?: string;
  invoiceStatus: "paid" | "partial" | "unpaid" | "not_applicable" | "overpaid_after_red" | "red_overpayment_settled" | string;
  bankReconciliationStatus?: "paid" | "partial" | "unpaid" | "not_applicable" | "overpaid_after_red" | "red_overpayment_settled" | string;
  bankReconciliationApplicable?: boolean;
  bankReconciliationReason?: string;
  paymentSource: "corporate" | "personal" | "platform_auto_debit" | "mixed" | "not_applicable" | string;
  paymentSourceLabel: string;
  expenseNature: string;
  expenseNatureLabel: string;
  expenseNatureBasis?: string;
  invoiceColor?: "blue" | "red" | "unknown" | string;
  invoiceStatusLabel?: string;
  redStatus?: string;
  redRelatedInvoiceNo?: string;
  redRelatedInvoicePeriod?: string;
  redCrossPeriod?: boolean;
  redSettlementStatus?: string;
  redSettlementRemainingAmount?: string;
  vatDeductibleStatus?: string;
  inputVatTransferStatus?: string;
  inputVatTransferAmount?: string;
  purchaseOrderNos: string[];
  payments: CorporateInvoicePayment[];
};
type CorporatePaymentReport = {
  year: number;
  month: number;
  company: string;
  summary: {
    paymentCount: number;
    invoiceCount: number;
    paymentTotal: string;
    allocatedTotal: string;
    invoiceTotal: string;
    outstandingTotal: string;
    overpaidAfterRedCount?: number;
    overpaidAfterRedAmount?: string;
    historicalOverpaidAfterRedAmount?: string;
    settledOverpaidAfterRedAmount?: string;
    resolvedRedOverpaymentCount?: number;
    redSettlementRemainingAmount?: string;
    companyMismatchInvoiceCount?: number;
    companyMismatchInvoiceAmount?: string;
    companyMismatchInvoiceNos?: string[];
    crossPeriodRedCount?: number;
    crossPeriodRedAmount?: string;
    paidInvoiceCount?: number;
    partialInvoiceCount?: number;
    unpaidInvoiceCount?: number;
    notApplicableInvoiceCount?: number;
    corporateInvoiceCount?: number;
    personalInvoiceCount?: number;
    platformAutoDebitInvoiceCount?: number;
    mixedInvoiceCount?: number;
    notApplicablePaymentCount?: number;
    personalInferredAmount?: string;
    expenseNatureCounts?: Record<string, number>;
  };
  invoiceRows: CorporateInvoiceRow[];
  rows: CorporatePaymentRow[];
  adjusted?: boolean;
  version?: number | null;
  selectedKeys?: string[];
  selectionError?: string;
  selectedCount?: number;
  sourceCount?: number;
  updatedAt?: string | null;
};
type PickerInvoice = { id: number; invoiceNumber: string; sellerName: string; issueDate: string; totalAmount: string; remaining: string; suggested: boolean };

function corporateBankStatus(row: CorporateInvoiceRow) {
  return row.bankReconciliationStatus || row.invoiceStatus;
}

function corporateBankStatusLabel(status: string) {
  if (status === "overpaid_after_red") return "红冲后超额付款待处理";
  if (status === "red_overpayment_settled") return "红冲超额已处理";
  if (status === "paid") return "银行付款已核对";
  if (status === "partial") return "银行付款部分核对";
  if (status === "not_applicable") return "无需核对银行付款";
  return "待核对银行付款";
}

function bankReconciliationBadgeClass(status: string) {
  if (status === "paid") return "bg-emerald-600 text-white shadow-sm";
  if (status === "partial") return "bg-amber-500 text-white";
  if (status === "overpaid_after_red") return "bg-rose-600 text-white";
  if (status === "red_overpayment_settled") return "bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200";
  if (status === "not_applicable") return "bg-slate-50 text-slate-400 ring-1 ring-inset ring-slate-200";
  return "bg-white text-slate-500 ring-1 ring-inset ring-slate-300";
}

function bankMatchStatusLabel(status: string) {
  if (status === "overpaid_after_red") return "红冲后超额付款";
  if (status === "red_overpayment_settled") return "红冲超额已处理";
  if (status === "matched") return "银行付款已核对";
  if (status === "partial") return "银行付款部分核对";
  return "待核对银行付款";
}

function invoiceColorLabel(row: CorporateInvoiceRow) {
  if (row.invoiceColor === "red") return "红字发票";
  if (row.invoiceColor === "blue") return "蓝字发票";
  return "票据待确认";
}

function invoiceLifecycleLabel(row: CorporateInvoiceRow) {
  if (row.redStatus === "fully_red_offset") return "已全额红冲";
  if (row.redStatus === "partially_red_offset") return "部分红冲";
  if (row.redStatus === "over_red_offset") return "红冲金额异常";
  if (row.redStatus === "blue_red_pending") return "已红冲 · 待关联红字票";
  if (row.redStatus === "red_invoice") return "红冲";
  if (row.redStatus === "red_invoice_unpaired") return "红冲 · 待关联蓝字票";
  if (row.redStatus === "red_invoice_ambiguous") return "红冲 · 蓝字票关联歧义";
  if (row.invoiceColor === "blue") return "有效";
  return row.invoiceStatusLabel || "";
}

function invoiceColorBadgeClass(row: CorporateInvoiceRow) {
  if (row.invoiceColor === "red") return "bg-rose-100 text-rose-700 ring-rose-200";
  if (row.invoiceColor === "blue") return "bg-sky-100 text-sky-700 ring-sky-200";
  return "bg-slate-100 text-slate-500 ring-slate-200";
}

function invoiceLifecycleBadgeClass(row: CorporateInvoiceRow) {
  if (row.invoiceColor === "red" || row.redStatus === "over_red_offset") return "bg-rose-50 text-rose-700 ring-rose-100";
  if (row.redStatus === "fully_red_offset") return "bg-violet-50 text-violet-700 ring-violet-100";
  if (row.redStatus === "partially_red_offset" || row.redStatus === "blue_red_pending") return "bg-amber-50 text-amber-700 ring-amber-100";
  return "bg-slate-50 text-slate-500 ring-slate-100";
}

function previousMonthValue() {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonthValue(value: string, offset: number) {
  const [year, month] = value.split("-").map(Number);
  if (!year || !month) return value;
  const d = new Date(year, month - 1 + offset, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—";
}

function formatBytes(value: number) {
  if (!value) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function unbilledDetailKey(detail: { taxCode: string; product: string }) {
  return `${detail.taxCode || ""}\u001f${detail.product || ""}`;
}

function emails(value: string) {
  return value.split(/[\s,;，；]+/).map((x) => x.trim()).filter(Boolean);
}

function money(value: string | number | undefined) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

/** 与交付 xlsx 一致的纯数字格式（无货币符号）。 */
function num(value: string | number | undefined) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
}

/** 行已开票金额；缺失返回 null（展示为 —）。 */
function invoicedValue(detail: { invoiced?: string }): number | null {
  if (detail.invoiced == null || detail.invoiced === "") return null;
  return Number(detail.invoiced);
}

/** 行无票收入：后端缺省但有已开票时，前端按调整后销售金额 − 已开票净额兜底。 */
function unbilledValue(detail: { sales: string; adjustedSales?: string; invoiced?: string; unbilled?: string }): number | null {
  const invoiced = invoicedValue(detail);
  if (invoiced === null) return null;
  if (detail.unbilled != null && detail.unbilled !== "") return Number(detail.unbilled);
  return Math.max(Number(detail.adjustedSales ?? detail.sales) - invoiced, 0);
}

/** 归档文件 → 交付表名（与后端打包映射一致）。 */
function deliveryName(month: number, name: string) {
  if (name.includes("交易明细")) return `${month}月-银行交易明细`;
  if (name.includes("回单详情")) return `${month}月-银行回单详情`;
  return name;
}

const CARD = "rounded-xl border border-slate-200 bg-white shadow-sm";

export default function MonthlySendPage() {
  const runtime = useTabRuntime();
  const workspace = useWorkspace();
  const ownTab = runtime?.tabId ? workspace.tabs.find((tab) => tab.id === runtime.tabId) : null;
  const ownSearch = ownTab?.search || "";

  // 全页唯一的账期选择器：选一次，下面所有卡片都跟着它走。
  const [sendMonth, setSendMonth] = useTabScopedState("monthly.month", previousMonthValue);
  const sel = useMemo(() => {
    const [year, mm] = sendMonth.split("-").map(Number);
    return year && mm ? { year, month: mm } : null;
  }, [sendMonth]);
  useTabTitle(sel ? `月度资料 · ${sendMonth}` : null);

  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [entityId, setEntityId] = useTabScopedState<number | null>("monthly.entity", () => null);
  const [template, setTemplate] = useState<SalesTemplate | null>(null);
  const [toText, setToText] = useState("");
  const [ccText, setCcText] = useState("");
  const [periods, setPeriods] = useState<Period[]>([]);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [businessStatus, setBusinessStatus] = useState<BusinessStatus | null>(null);
  const [unbilled, setUnbilled] = useState<Unbilled | null>(null);
  const [mailStatus, setMailStatus] = useState<MailStatus | null>(null);
  const [mailOpen, setMailOpen] = useState(false);
  const sendSettingsSnapshot = useRef<{ toText: string; ccText: string; autoSend: boolean; sendDay: number; sendHour: number } | null>(null);
  const [financeTab, setFinanceTab] = useTabScopedState<"monthly" | "records" | "archive" | "ledger" | "corporate" | "match">("monthly.tab", "monthly");
  const [corporateView, setCorporateView] = useState<"adjust" | "preview">("adjust");
  useEffect(() => {
    const params = new URLSearchParams(ownSearch);
    const requestedMonth = params.get("month") || "";
    const requestedTab = params.get("tab") || "";
    if (/^\d{4}-\d{2}$/.test(requestedMonth)) setSendMonth(requestedMonth);
    if (["monthly", "records", "archive", "ledger", "corporate", "match"].includes(requestedTab)) {
      setFinanceTab(requestedTab as "monthly" | "records" | "archive" | "ledger" | "corporate" | "match");
    }
  }, [ownSearch, setFinanceTab, setSendMonth]);

  const [matchData, setMatchData] = useState<PaymentMatchOverview | null>(null);
  const [corporatePayment, setCorporatePayment] = useState<CorporatePaymentReport | null>(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [pickerTxn, setPickerTxn] = useState<PaymentMatchRow | null>(null);
  const [pickerInvoice, setPickerInvoice] = useState<PaymentMatchPoolInvoice | null>(null);
  const [pickerQuery, setPickerQuery] = useState("");
  const [pickerScope, setPickerScope] = useState<"month" | "all">("month");
  const [allInvoices, setAllInvoices] = useState<PickerInvoice[]>([]);
  /** 发送内容勾选：手动打包只包含勾选项；自动发送始终重新生成当前全部月度交付表。 */
  const [includeSel, setIncludeSel] = useState<string[]>(["交易明细", "回单详情", "无票收入", "已收票对公付款明细"]);
  const [showUnbilledDetail, setShowUnbilledDetail] = useState(false);
  const [unbilledDetailMode, setUnbilledDetailMode] = useState<"adjust" | "preview">("adjust");
  const [showCorporateDetail, setShowCorporateDetail] = useState(false);
  const [unbilledSearch, setUnbilledSearch] = useState("");
  const [unbilledSelectedKeys, setUnbilledSelectedKeys] = useState<string[]>([]);
  const [corporateSelectedKeys, setCorporateSelectedKeys] = useState<string[]>([]);
  const [showFields, setShowFields] = useState(false);
  const [preview, setPreview] = useState<SalesPreview | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const templateRequestSeq = useRef(0);
  const periodsRequestSeq = useRef(0);
  const filesRequestSeq = useRef(0);
  const businessRequestSeq = useRef(0);
  const unbilledRequestSeq = useRef(0);
  const corporateRequestSeq = useRef(0);
  const matchRequestSeq = useRef(0);
  const pickerRequestSeq = useRef(0);
  const uploadKind = useRef<"交易明细" | "回单详情">("交易明细");

  const corporateInvoices = corporatePayment?.invoiceRows || [];
  const corporateDeliveryState = !corporatePayment
    ? { ok: false, text: "计算中" }
    : { ok: true, text: corporatePayment.adjusted ? "已调整" : "已生成" };

  const corporateReviewSummary = corporatePayment
    ? `${corporatePayment.adjusted ? `已选择 ${corporatePayment.selectedCount || 0}/${corporatePayment.sourceCount || 0} 张发票 · ` : ""}对公 ${corporatePayment.summary.corporateInvoiceCount || 0} 张 · 个人 ${corporatePayment.summary.personalInvoiceCount || 0} 张${(corporatePayment.summary.mixedInvoiceCount || 0) > 0 ? ` · 混合 ${corporatePayment.summary.mixedInvoiceCount} 张` : ""}${(corporatePayment.summary.overpaidAfterRedCount || 0) > 0 ? ` · 红冲后超额付款 ${corporatePayment.summary.overpaidAfterRedCount} 张` : ""}${(corporatePayment.summary.companyMismatchInvoiceCount || 0) > 0 ? ` · 主体不符 ${corporatePayment.summary.companyMismatchInvoiceCount} 张` : ""}`
    : "发票号 · 支付方式 · 费用性质";

  const selectedEntity = entities.find((row) => row.id === entityId) ?? null;
  const companyName = selectedEntity?.name ?? "";
  const domesticSupportedByEntity = Boolean(
    selectedEntity?.isDefault && selectedEntity.businessScopes.includes("domestic")
  );
  const period = periods.find((p) => sel && p.year === sel.year && p.month === sel.month) || null;
  const latest = useCallback(
    (keyword: string) =>
      files.filter((f) => f.originalName.includes(keyword)).sort((a, b) => b.version - a.version)[0] || null,
    [files],
  );
  const bankTx = latest("交易明细");
  const bankReceipt = latest("回单详情");
  const otherFiles = files.filter((f) => !f.originalName.includes("交易明细") && !f.originalName.includes("回单详情"));
  const salesFile = [...otherFiles].filter((f) => f.category === "sales_summary").sort((a, b) => b.version - a.version)[0] || null;

  const loadEntities = useCallback(async () => {
    const res = await authenticatedFetch("/api/v1/finance/entities", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "公司主体加载失败");
    const rows = (data.items || []) as LegalEntity[];
    setEntities(rows);
    setEntityId((current) => current && rows.some((row) => row.id === current)
      ? current
      : (rows.find((row) => row.isDefault)?.id ?? rows[0]?.id ?? null));
  }, [setEntityId]);

  const loadTemplate = useCallback(async () => {
    if (!companyName) {
      templateRequestSeq.current += 1;
      return;
    }
    const seq = ++templateRequestSeq.current;
    const res = await authenticatedFetch(`/api/v1/finance/sales-report/template?company=${encodeURIComponent(companyName)}`, { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "加载失败");
    if (seq !== templateRequestSeq.current) return;
    setTemplate(data);
    setToText((data.toAddrs || []).join(", "));
    setCcText((data.ccAddrs || []).join(", "));
  }, [companyName]);

  const loadPeriods = useCallback(() => {
    if (!companyName) {
      periodsRequestSeq.current += 1;
      setPeriods([]);
      return;
    }
    const seq = ++periodsRequestSeq.current;
    authenticatedFetch(`/api/v1/finance/periods?company=${encodeURIComponent(companyName)}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((rows) => { if (seq === periodsRequestSeq.current) setPeriods(rows); })
      .catch(() => { if (seq === periodsRequestSeq.current) setPeriods([]); });
  }, [companyName]);

  const loadFiles = useCallback(() => {
    if (!sel || !companyName) return;
    const seq = ++filesRequestSeq.current;
    authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/files?company=${encodeURIComponent(companyName)}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((rows) => { if (seq === filesRequestSeq.current) setFiles(rows); })
      .catch(() => { if (seq === filesRequestSeq.current) setMsg("报告档案加载失败，请刷新重试"); });
  }, [companyName, sel]);

  const loadBusiness = useCallback(() => {
    if (!sel || !companyName || !entityId) return;
    const seq = ++businessRequestSeq.current;
    authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/refresh-business?company=${encodeURIComponent(companyName)}&legal_entity_id=${entityId}`, {
      method: "POST",
      cache: "no-store",
    })
      .then(async (r) => {
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "业务数据刷新失败");
        return data;
      })
      .then((data) => { if (seq === businessRequestSeq.current) setBusinessStatus(data); })
      .catch(() => { if (seq === businessRequestSeq.current) setBusinessStatus(null); });
  }, [companyName, entityId, sel]);

  const loadUnbilled = useCallback(() => {
    if (!sel || !companyName || !domesticSupportedByEntity) {
      setUnbilled(null);
      return;
    }
    const seq = ++unbilledRequestSeq.current;
    authenticatedFetch(`/api/v1/finance/unbilled/preview?year=${sel.year}&month=${sel.month}&company=${encodeURIComponent(companyName)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (seq === unbilledRequestSeq.current) setUnbilled(data); })
      .catch(() => { if (seq === unbilledRequestSeq.current) setMsg("未结算调整预览加载失败，请刷新重试"); });
  }, [companyName, domesticSupportedByEntity, sel]);

  const loadCorporatePayment = useCallback(() => {
    if (!sel || !companyName || !domesticSupportedByEntity) {
      corporateRequestSeq.current += 1;
      setCorporatePayment(null);
      setCorporateSelectedKeys([]);
      return;
    }
    const seq = ++corporateRequestSeq.current;
    authenticatedFetch(`/api/v1/finance/corporate-payment-report?year=${sel.year}&month=${sel.month}&company=${encodeURIComponent(companyName)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (seq !== corporateRequestSeq.current) return;
        setCorporatePayment(data);
        if (data) {
          const available = (data.invoiceRows || []).map((row: CorporateInvoiceRow) => row.invoiceKey).filter(Boolean);
          // selectedKeys=[] 可能表示历史号码存在歧义；不能把空数组误当成“默认全选”。
          const selected = Array.isArray(data.selectedKeys) ? data.selectedKeys : available;
          setCorporateSelectedKeys(selected.filter((key: string) => available.includes(key)));
          if (data.selectionError) {
            setMsg(`历史发票选择需要重新确认：${data.selectionError}`);
          }
        } else {
          setCorporateSelectedKeys([]);
        }
      })
      .catch(() => {
        if (seq !== corporateRequestSeq.current) return;
        setCorporatePayment(null);
        setCorporateSelectedKeys([]);
      });
  }, [companyName, domesticSupportedByEntity, sel]);

  const loadMatch = useCallback(() => {
    if (!sel) return;
    const seq = ++matchRequestSeq.current;
    setMatchLoading(true);
    authenticatedFetch(`/api/v1/finance/payment-invoice-match/${sel.year}/${sel.month}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (seq === matchRequestSeq.current) setMatchData(data); })
      .catch(() => { if (seq === matchRequestSeq.current) setMatchData(null); })
      .finally(() => { if (seq === matchRequestSeq.current) setMatchLoading(false); });
  }, [sel]);

  const loadMailStatus = useCallback(() => {
    authenticatedFetch("/api/v1/finance/mail-status", { cache: "no-store" })
      .then((r) => r.json())
      .then(setMailStatus)
      .catch(() => {});
  }, []);

  useEffect(() => {
    void loadEntities().catch((e) => setMsg(String(e)));
    void loadMailStatus();
  }, [loadEntities, loadMailStatus]);
  useEffect(() => {
    if (!companyName) return;
    setTemplate(null);
    setPeriods([]);
    setFiles([]);
    setBusinessStatus(null);
    setUnbilled(null);
    setCorporatePayment(null);
    void loadTemplate().catch((e) => setMsg(String(e)));
    void loadPeriods();
  }, [companyName, loadPeriods, loadTemplate]);
  useEffect(() => {
    setIncludeSel(domesticSupportedByEntity
      ? ["交易明细", "回单详情", "无票收入", "已收票对公付款明细"]
      : ["交易明细", "回单详情"]);
    if (!domesticSupportedByEntity && (financeTab === "ledger" || financeTab === "match" || financeTab === "corporate")) {
      setFinanceTab("monthly");
    }
  }, [domesticSupportedByEntity, entityId, financeTab, setFinanceTab]);
  useEffect(() => { loadFiles(); loadBusiness(); loadUnbilled(); loadCorporatePayment(); }, [loadFiles, loadBusiness, loadUnbilled, loadCorporatePayment]);
  useEffect(() => { if (financeTab === "match" && domesticSupportedByEntity) loadMatch(); }, [domesticSupportedByEntity, financeTab, loadMatch]);
  // 弹层切“全部待核对”时，只读取银行付款维度的待核对发票。
  // 不能复用 tax_invoices.match_status：那个字段属于采购/销售业务关联状态。
  useEffect(() => {
    if (!pickerTxn || pickerScope !== "all") return;
    const seq = ++pickerRequestSeq.current;
    authenticatedFetch("/api/v1/finance/payment-invoice-match/invoices?limit=500", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: Array<Record<string, unknown>>) => {
        if (seq !== pickerRequestSeq.current) return;
        setAllInvoices(rows.map((row) => ({
          id: Number(row.id),
          invoiceNumber: String(row.invoiceNumber || ""),
          sellerName: String(row.sellerName || ""),
          issueDate: String(row.issueDate || "").slice(0, 10),
          totalAmount: String(row.totalAmount || "0"),
          remaining: String(row.remaining || "0"),
          suggested: false,
        })));
      })
      .catch(() => { if (seq === pickerRequestSeq.current) setAllInvoices([]); });
  }, [pickerTxn, pickerScope]);
  useEffect(() => {
    if (!unbilled) {
      setUnbilledSelectedKeys([]);
      return;
    }
    const sourceDetails = unbilled.sourceDetails?.length ? unbilled.sourceDetails : unbilled.details;
    const available = sourceDetails.map(unbilledDetailKey);
    const selected = unbilled.selectedKeys?.length ? unbilled.selectedKeys : available;
    setUnbilledSelectedKeys(selected.filter((key) => available.includes(key)));
  }, [unbilled]);

  function openSendSettings() {
    sendSettingsSnapshot.current = {
      toText,
      ccText,
      autoSend: Boolean(template?.autoSend),
      sendDay: template?.sendDay ?? 3,
      sendHour: template?.sendHour ?? 10,
    };
    setMailOpen(true);
    void loadMailStatus();
  }

  function closeSendSettings(revert = true) {
    const snapshot = sendSettingsSnapshot.current;
    if (revert && snapshot) {
      setToText(snapshot.toText);
      setCcText(snapshot.ccText);
      setTemplate((current) => current ? {
        ...current,
        autoSend: snapshot.autoSend,
        sendDay: snapshot.sendDay,
        sendHour: snapshot.sendHour,
      } : current);
    }
    sendSettingsSnapshot.current = null;
    setMailOpen(false);
  }

  async function saveTemplate(source: "send" | "fields" = "send") {
    if (!template) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/sales-report/template", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company: companyName || template.company, enabled: template.enabled, fields: template.fields, rules: template.rules,
          to_addrs: emails(toText), cc_addrs: emails(ccText),
          auto_send: template.autoSend, send_day: template.sendDay, send_hour: template.sendHour,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      setTemplate(data); setToText((data.toAddrs || []).join(", ")); setCcText((data.ccAddrs || []).join(", "));
      setMsg(source === "fields" ? "销售汇总字段配置已保存。" : "发送设置已保存，自动发送与手动发送共用。");
      return true;
    } catch (e) {
      setMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`);
      return false;
    } finally { setBusy(false); }
  }

  /** 上传/替换某张表：自动按标准表名归档，类别固定 bank，同名递增版本。 */
  async function uploadBank(file: File) {
    if (!sel) { setMsg("请选择账期月份"); return; }
    const ext = file.name.includes(".") ? `.${file.name.split(".").pop()}` : "";
    const fd = new FormData();
    fd.append("file", file);
    fd.append("period_year", String(sel.year));
    fd.append("period_month", String(sel.month));
    fd.append("category", "bank");
    fd.append("original_name", `银行${uploadKind.current}${ext}`);
    fd.append("company", companyName);
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/files", { method: "POST", body: fd });
      const d = await res.json();
      setMsg(res.ok
        ? `${uploadKind.current} 已归档 v${d.version}${d.bankImport ? ` · 流水入库：新增 ${d.bankImport.created}，重复 ${d.bankImport.duplicates}` : ""}${d.bankImportError ? ` · 流水解析失败：${d.bankImportError}` : ""}`
        : `上传失败：${d.detail}`);
      if (fileRef.current) fileRef.current.value = "";
    } finally { setBusy(false); loadFiles(); loadPeriods(); if (uploadKind.current === "交易明细") loadMatch(); }
  }

  function handleUploadSelection(file: File) {
    void uploadBank(file);
  }

  /** 标记已开票：分配金额 = min(发票价税合计, 付款剩余)，多张票分次标记即可拆分。 */
  async function markInvoiced(txn: PaymentMatchRow, inv: PickerInvoice) {
    const remain = Number(txn.remaining) || 0;
    const total = Number(inv.totalAmount) || 0;
    const alloc = Math.min(remain, total);
    if (alloc <= 0) { setMsg("该发票可分配金额为 0，无法标记"); return; }
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/payment-invoice-match/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ txn_id: txn.id, invoice_id: inv.id, allocated_amount: alloc.toFixed(2) }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "标记失败");
      setMsg(`已标记：发票 ${inv.invoiceNumber}（${money(alloc)}）配到 ${txn.txnDate} 付款（${txn.counterpartyName || "对方未名"}）`);
      setPickerTxn(null);
      setPickerQuery("");
      loadMatch();
    } catch (e) {
      setMsg(`标记失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function matchInvoiceToPayment(invoice: PaymentMatchPoolInvoice, txn: PaymentMatchRow) {
    const invoiceRemain = Number(invoice.remaining) || 0;
    const paymentRemain = Number(txn.remaining) || 0;
    const alloc = Math.min(invoiceRemain, paymentRemain);
    if (alloc <= 0) { setMsg("当前发票或付款已没有可核对金额"); return; }
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/payment-invoice-match/link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ txn_id: txn.id, invoice_id: invoice.id, allocated_amount: alloc.toFixed(2) }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || "核对失败");
      setMsg(`已核对：发票 ${invoice.invoiceNumber || "未记录号码"} ↔ ${txn.txnDate} 银行付款 ${money(alloc)}`);
      setPickerInvoice(null);
      setPickerQuery("");
      await loadMatch();
      await loadCorporatePayment();
    } catch (e) {
      setMsg(`核对失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function unlinkPayment(linkId: number) {
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/payment-invoice-match/link/${linkId}`, { method: "DELETE" });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.detail || "解除失败");
      }
      setMsg("已解除标记，付款回到未配票状态。");
      loadMatch();
    } catch (e) {
      setMsg(`解除失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  /** 手动发送 = 每次都用最新资料重新打包（新版本），再发给财务；保证收到的一定是最新数据。 */
  async function packageAndSend() {
    if (!sel) return;
    if (!selectedMaterialsReady) {
      setMsg("本次勾选的发送资料尚未就绪，请先补齐对应资料或取消勾选后再发送。");
      return;
    }
    if (!emails(toText).length) {
      setMsg("请先在“发送设置”中填写财务收件人。");
      return;
    }
    if (!mailReady) {
      setMsg("邮件通道尚未配置完成，请先检查发送设置。");
      return;
    }
    setBusy(true); setMsg("");
    try {
      const pkgRes = await authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/package?company=${encodeURIComponent(companyName)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include: includeSel }),
      });
      const pkgData = await pkgRes.json();
      if (!pkgRes.ok) throw new Error(pkgData.detail || "打包失败");
      const res = await authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          version: pkgData.version,
          company: companyName,
          to_addrs: emails(toText),
          cc_addrs: emails(ccText),
        }),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.detail || `发送失败（${res.status}）`);
      setMsg(`已打包 V${d.version} 并发送给财务（${d.kind === "resent" ? "重发" : "首次"}）`);
      loadPeriods();
    } catch (e) { setMsg(`失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  async function generateSales() {
    if (!sel) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/sales-report/generate?year=${sel.year}&month=${sel.month}&company=${encodeURIComponent(companyName)}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "生成失败");
      setMsg(`销售汇总已生成并归档 v${data.version}`);
      loadFiles(); loadPeriods();
    } catch (e) { setMsg(`生成失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  async function previewSales() {
    if (!sel) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/sales-report/preview?year=${sel.year}&month=${sel.month}&limit=30&company=${encodeURIComponent(companyName)}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "预览失败");
      setPreview(data);
    } catch (e) { setMsg(`预览失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  async function downloadFile(row: FileRow) {
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/files/${row.id}/download`);
      if (!res.ok) { const d = await res.json().catch(() => ({})); setMsg(`下载失败：${d.detail ?? res.status}`); return; }
      const blobUrl = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = blobUrl; link.download = row.originalName;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(blobUrl);
    } finally { setBusy(false); }
  }

  async function saveUnbilledAdjustment() {
    if (!sel || !unbilled) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/unbilled/adjustment?year=${sel.year}&month=${sel.month}&company=${encodeURIComponent(companyName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_keys: unbilledSelectedKeys }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      setShowUnbilledDetail(false);
      setMsg(`无票收入明细已保存并生成新版 v${data.version}`);
      loadUnbilled(); loadFiles(); loadPeriods();
    } catch (e) {
      setMsg(`保存调整失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function saveCorporateAdjustment() {
    if (!sel || !corporatePayment) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/corporate-payment/adjustment?year=${sel.year}&month=${sel.month}&company=${encodeURIComponent(companyName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_keys: corporateSelectedKeys }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      setShowCorporateDetail(false);
      setMsg(`已收票对公付款明细已保存并生成新版 v${data.version}`);
      loadCorporatePayment(); loadFiles(); loadPeriods();
    } catch (e) {
      setMsg(`保存调整失败：${e instanceof Error ? e.message : String(e)}`);
    } finally { setBusy(false); }
  }

  async function downloadPackage(pkg: Pkg) {
    if (!sel) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/packages/${pkg.id}/download`);
      if (!res.ok) { const d = await res.json().catch(() => ({})); setMsg(`下载失败：${d.detail ?? res.status}`); return; }
      const blobUrl = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = blobUrl; link.download = `${sel.year}年${sel.month}月财务资料_V${pkg.version}.zip`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(blobUrl);
    } finally { setBusy(false); }
  }

  /** 删除归档文件（记录 + 磁盘文件，不可恢复）。 */
  async function deleteFile(row: FileRow) {
    if (!sel || !window.confirm(`删除「${deliveryName(sel.month, row.originalName)} v${row.version}」？磁盘文件与记录一并删除，不可恢复。`)) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/files/${row.id}`, { method: "DELETE" });
      const d = await res.json();
      setMsg(res.ok ? `已删除 ${deliveryName(sel.month, row.originalName)} v${row.version}` : `删除失败：${d.detail ?? res.status}`);
    } finally { setBusy(false); loadFiles(); loadPeriods(); }
  }

  /** 删除交付包记录与 ZIP（不可恢复）。 */
  async function deletePackage(pkg: Pkg) {
    if (!window.confirm(`删除交付包 V${pkg.version}（${pkg.status === "SENT" ? "已发送" : "已打包"}）？记录与 ZIP 文件一并删除，不可恢复。`)) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/packages/${pkg.id}`, { method: "DELETE" });
      const d = await res.json();
      setMsg(res.ok ? `已删除 V${pkg.version}` : `删除失败：${d.detail ?? res.status}`);
    } finally { setBusy(false); loadPeriods(); }
  }

  function updateField(index: number, values: Partial<SalesField>) {
    setTemplate((current) => current ? { ...current, fields: current.fields.map((f, i) => i === index ? { ...f, ...values } : f) } : current);
  }
  function moveField(index: number, direction: -1 | 1) {
    setTemplate((current) => {
      if (!current) return current;
      const target = index + direction;
      if (target < 0 || target >= current.fields.length) return current;
      const next = [...current.fields];
      [next[index], next[target]] = [next[target], next[index]];
      return { ...current, fields: next };
    });
  }

  const businessReady = Boolean(businessStatus?.ready);
  const needsUnbilled = Boolean(businessStatus?.domesticSupported ?? domesticSupportedByEntity);
  const monthlyReady = Boolean(businessReady && bankTx && bankReceipt && (!needsUnbilled || (unbilled && corporatePayment)));
  const selectedMaterialsReady = Boolean(
    includeSel.length
    && (!includeSel.includes("交易明细") || bankTx)
    && (!includeSel.includes("回单详情") || bankReceipt)
    && (!includeSel.includes("无票收入") || (unbilled && businessReady))
    && (!includeSel.includes("已收票对公付款明细") || corporatePayment)
  );
  const mailReady = Boolean(mailStatus?.configured);
  const sendReady = Boolean(selectedMaterialsReady && emails(toText).length && mailReady);
  const readyCount = [bankTx, bankReceipt, ...(needsUnbilled ? [unbilled, corporatePayment] : [])].filter(Boolean).length;
  const requiredDeliveryCount = needsUnbilled ? 4 : 2;
  const hasForeignSummary = (businessStatus?.foreignEntryCount ?? 0) > 0;
  const latestPkg = period?.packages.length ? period.packages[period.packages.length - 1] : null;
  const latestSentPkg = [...(period?.packages || [])].reverse().find((pkg) => pkg.status === "SENT") || null;
  const attachmentSize = (bankTx?.size || 0) + (bankReceipt?.size || 0);
  const unbilledSourceDetails = useMemo(
    () => unbilled?.sourceDetails?.length ? unbilled.sourceDetails : (unbilled?.details || []),
    [unbilled],
  );
  const unbilledViewDetails = unbilledDetailMode === "adjust" ? unbilledSourceDetails : (unbilled?.details || []);
  const filteredUnbilledDetails = useMemo(() => {
    const query = unbilledSearch.trim().toLowerCase();
    if (!query) return unbilledViewDetails;
    return unbilledViewDetails.filter((row) => [row.taxCode, row.taxName, row.product].some((value) => value.toLowerCase().includes(query)));
  }, [unbilledViewDetails, unbilledSearch]);
  const visibleUnbilledKeys = filteredUnbilledDetails.map(unbilledDetailKey);
  const selectedVisibleCount = visibleUnbilledKeys.filter((key) => unbilledSelectedKeys.includes(key)).length;
  const allVisibleSelected = visibleUnbilledKeys.length > 0 && selectedVisibleCount === visibleUnbilledKeys.length;

  function toggleUnbilledKey(key: string) {
    setUnbilledSelectedKeys((keys) => keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key]);
  }

  function toggleVisibleUnbilled() {
    setUnbilledSelectedKeys((keys) => {
      if (allVisibleSelected) return keys.filter((key) => !visibleUnbilledKeys.includes(key));
      return [...new Set([...keys, ...visibleUnbilledKeys])];
    });
  }

  const corporateAllKeys = corporateInvoices.map((row) => row.invoiceKey).filter(Boolean);
  const corporateAllSelected = corporateAllKeys.length > 0 && corporateAllKeys.every((key) => corporateSelectedKeys.includes(key));

  function toggleCorporateKey(key: string) {
    setCorporateSelectedKeys((keys) => keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key]);
  }

  function toggleAllCorporate() {
    setCorporateSelectedKeys(corporateAllSelected ? [] : corporateAllKeys);
  }

  function closeUnbilledDetail() {
    if (unbilled) {
      const sourceDetails = unbilled.sourceDetails?.length ? unbilled.sourceDetails : unbilled.details;
      const available = sourceDetails.map(unbilledDetailKey);
      const selected = unbilled.selectedKeys?.length ? unbilled.selectedKeys : available;
      setUnbilledSelectedKeys(selected.filter((key) => available.includes(key)));
    } else {
      setUnbilledSelectedKeys([]);
    }
    setUnbilledSearch("");
    setShowUnbilledDetail(false);
  }

  /** 已收票对公付款调整正文：页签与弹窗共用；readOnly 时操作列只读，goMatch 负责跳到银行核对。
   *  传入 selection 时表格增加发票勾选列（弹窗模式），页签模式不传。 */
  function corporateAdjustBody(readOnly: boolean, goMatch: () => void, selection?: { selected: string[]; onToggle: (key: string) => void; onToggleAll: () => void }) {
    const savedSelection = readOnly && corporatePayment?.adjusted
      ? new Set((corporatePayment.selectedKeys || []).map((key) => key.trim()).filter(Boolean))
      : null;
    const viewInvoices = savedSelection
      ? corporateInvoices.filter((row) => savedSelection.has(row.invoiceKey))
      : corporateInvoices;
    const viewInvoiceIds = new Set(viewInvoices.map((row) => row.invoiceId));
    const viewSummary = corporatePayment
      ? savedSelection
        ? (() => {
            const paymentById = new Map<number, number>();
            for (const row of viewInvoices) {
              for (const payment of row.payments) {
                if (!paymentById.has(payment.paymentId)) paymentById.set(payment.paymentId, Number(payment.paymentAmount || 0));
              }
            }
            const status = (row: CorporateInvoiceRow) => corporateBankStatus(row);
            return {
              paymentCount: paymentById.size,
              invoiceCount: viewInvoices.length,
              paymentTotal: Array.from(paymentById.values()).reduce((sum, value) => sum + value, 0),
              allocatedTotal: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceCorporatePaidTotal || 0), 0),
              invoiceTotal: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceTotalAmount || 0), 0),
              outstandingTotal: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceOutstandingAmount || 0), 0),
              overpaidAfterRedCount: viewInvoices.filter((row) => status(row) === "overpaid_after_red").length,
              overpaidAfterRedAmount: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceOverpaidUnsettledAmount || 0), 0),
              historicalOverpaidAfterRedAmount: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceOverpaidAmount || 0), 0),
              settledOverpaidAfterRedAmount: viewInvoices.reduce((sum, row) => sum + Number(row.invoiceOverpaidSettledAmount || 0), 0),
              resolvedRedOverpaymentCount: viewInvoices.filter((row) => status(row) === "red_overpayment_settled").length,
              redSettlementRemainingAmount: viewInvoices.reduce((sum, row) => sum + Number(row.redSettlementRemainingAmount || 0), 0),
              companyMismatchInvoiceCount: corporatePayment.summary.companyMismatchInvoiceCount || 0,
              companyMismatchInvoiceAmount: corporatePayment.summary.companyMismatchInvoiceAmount || "0",
              crossPeriodRedCount: viewInvoices.filter((row) => row.redCrossPeriod).length,
              crossPeriodRedAmount: viewInvoices.filter((row) => row.redCrossPeriod).reduce((sum, row) => sum + Math.abs(Number(row.invoiceTotalAmount || 0)), 0),
              paidInvoiceCount: viewInvoices.filter((row) => status(row) === "paid").length,
              partialInvoiceCount: viewInvoices.filter((row) => status(row) === "partial").length,
              unpaidInvoiceCount: viewInvoices.filter((row) => status(row) === "unpaid").length,
              notApplicableInvoiceCount: viewInvoices.filter((row) => status(row) === "not_applicable").length,
              corporateInvoiceCount: viewInvoices.filter((row) => row.paymentSource === "corporate").length,
              personalInvoiceCount: viewInvoices.filter((row) => row.paymentSource === "personal").length,
              platformAutoDebitInvoiceCount: viewInvoices.filter((row) => row.paymentSource === "platform_auto_debit").length,
              mixedInvoiceCount: viewInvoices.filter((row) => row.paymentSource === "mixed").length,
              notApplicablePaymentCount: viewInvoices.filter((row) => row.paymentSource === "not_applicable").length,
              personalInferredAmount: viewInvoices
                .filter((row) => row.paymentSource === "personal" || row.paymentSource === "mixed")
                .reduce((sum, row) => sum + Number(row.invoiceOutstandingAmount || 0), 0),
              expenseNatureCounts: viewInvoices.reduce<Record<string, number>>((acc, row) => {
                const label = row.expenseNatureLabel || "待分类";
                acc[label] = (acc[label] || 0) + 1;
                return acc;
              }, {}),
            };
          })()
        : corporatePayment.summary
      : null;

    return (
      <>
        {corporatePayment && viewSummary && <div className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-7">
          {([
            ["发票价税合计", money(viewSummary.invoiceTotal), `${viewSummary.invoiceCount} 张进项票`],
            ["对公支付", money(viewSummary.allocatedTotal), `${viewSummary.corporateInvoiceCount || 0} 张对公 · ${viewSummary.paymentCount} 笔银行流水`],
            ["个人支付", money(viewSummary.personalInferredAmount ?? viewSummary.outstandingTotal), `${viewSummary.personalInvoiceCount || 0} 张个人支付`],
            ["平台扣款", String(viewSummary.platformAutoDebitInvoiceCount || 0), "张平台自动扣款货款"],
            ["红冲待处理", money(viewSummary.redSettlementRemainingAmount || viewSummary.overpaidAfterRedAmount || 0), `${viewSummary.crossPeriodRedCount || 0} 张跨期红字 · ${viewSummary.overpaidAfterRedCount || 0} 张超额付款`],
            ["主体不符", String(viewSummary.companyMismatchInvoiceCount || 0), money(viewSummary.companyMismatchInvoiceAmount || 0)],
            ["费用性质", String(Object.keys(viewSummary.expenseNatureCounts || {}).length), "类"],
          ] as const).map(([label, value, hint]) => <div key={label} className="bg-white px-4 py-3"><div className="text-[10px] text-slate-400">{label}</div><div className="mt-1 text-base font-semibold text-slate-800">{value}</div><div className="mt-0.5 text-[10px] text-slate-400">{hint}</div></div>)}
        </div>}
        {!corporatePayment ? <div className="px-4 py-12 text-center text-sm text-slate-400">正在生成发票核对清单…</div> : !viewInvoices.length ? <div className="px-4 py-12 text-center text-sm text-slate-400">{readOnly && corporatePayment.adjusted ? "当前保存版本没有可发送的进项发票，请返回调整明细。" : "当前账期没有收到进项发票。"}</div> : <>
          <div className="overflow-x-auto border-b border-slate-200">
            <table className="w-full min-w-[1640px] text-xs">
              <thead className="bg-slate-50 text-left text-slate-500"><tr>
                {selection && <th className="w-10 px-3 py-2.5 font-medium"><input type="checkbox" checked={corporateAllSelected} onChange={selection.onToggleAll} aria-label="全选发票" className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" /></th>}
                <th className="px-3 py-2.5 font-medium">发票日期</th><th className="px-3 py-2.5 font-medium">供应商</th><th className="px-3 py-2.5 font-medium">发票号码</th><th className="px-3 py-2.5 font-medium">发票属性</th><th className="px-3 py-2.5 font-medium">费用性质</th><th className="min-w-[130px] px-3 py-2.5 font-medium">银行核对状态</th><th className="px-3 py-2.5 text-right font-medium">价税合计</th><th className="px-3 py-2.5 text-right font-medium">对公支付金额</th><th className="px-3 py-2.5 font-medium">银行付款信息</th><th className="px-3 py-2.5 font-medium">采购订单</th><th className="px-3 py-2.5 text-right font-medium">操作</th>
              </tr></thead>
              <tbody className="divide-y divide-slate-100">{viewInvoices.map((row) => {
                const checked = selection ? selection.selected.includes(row.invoiceKey) : true;
                return <tr key={row.invoiceId} className={selection && !checked ? "bg-slate-50/70 text-slate-400" : row.invoiceColor === "red" ? "bg-rose-50/35" : row.redStatus === "fully_red_offset" ? "bg-violet-50/25" : row.paymentSource === "corporate" && corporateBankStatus(row) === "paid" ? "bg-emerald-50/40" : row.paymentSource === "personal" ? "bg-amber-50/25" : row.paymentSource === "platform_auto_debit" ? "bg-cyan-50/25" : ""}>
                {selection && <td className="px-3 py-2.5"><input type="checkbox" checked={checked} onChange={() => selection.onToggle(row.invoiceKey)} aria-label={`选择发票 ${row.invoiceNumber}`} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" /></td>}
                <td className="whitespace-nowrap px-3 py-2.5 text-slate-600">{row.invoiceDate || "—"}</td>
                <td className="max-w-[220px] px-3 py-2.5"><div className="truncate font-medium text-slate-800">{row.supplierName || "—"}</div><div className="mt-0.5 truncate font-mono text-[10px] text-slate-400">{row.supplierTaxId || ""}</div></td>
                <td className="px-3 py-2.5 font-mono text-[11px] text-slate-600">{row.invoiceNumber || "—"}</td>
                <td className="min-w-[150px] px-3 py-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`inline-flex rounded-full px-2 py-1 text-[10px] font-semibold ring-1 ${invoiceColorBadgeClass(row)}`}>{invoiceColorLabel(row)}</span>
                    {invoiceLifecycleLabel(row) && <span className={`inline-flex rounded-full px-2 py-1 text-[10px] font-medium ring-1 ${invoiceLifecycleBadgeClass(row)}`}>{invoiceLifecycleLabel(row)}</span>}
                  </div>
                  {row.redRelatedInvoiceNo && <div className="mt-1 text-[10px] text-slate-400">{row.invoiceColor === "red" ? "冲销蓝票" : "对应红票"} · <span className="font-mono">{row.redRelatedInvoiceNo}</span></div>}
                </td>
                <td className="px-3 py-2.5"><div className="font-medium text-slate-700">{row.expenseNatureLabel || "待分类"}</div><div className="mt-0.5 text-[10px] text-slate-400">{row.invoiceColor === "red" ? "红冲 · " : ""}{row.expenseNatureBasis || ""}</div></td>
                <td className="px-3 py-2.5"><span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold ${bankReconciliationBadgeClass(corporateBankStatus(row))}`}>{corporateBankStatus(row) === "paid" ? "✓ " : ""}{corporateBankStatusLabel(corporateBankStatus(row))}</span><div className="mt-1"><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${row.paymentSource === "corporate" ? "bg-emerald-50 text-emerald-700" : row.paymentSource === "personal" ? "bg-amber-50 text-amber-700" : row.paymentSource === "platform_auto_debit" ? "bg-cyan-50 text-cyan-700" : row.paymentSource === "mixed" ? "bg-blue-50 text-blue-700" : "bg-slate-100 text-slate-500"}`}>{row.paymentSourceLabel || "—"}</span></div></td>
                <td className={`px-3 py-2.5 text-right font-semibold tabular-nums ${row.invoiceColor === "red" ? "text-rose-700" : "text-slate-800"}`}>{money(row.invoiceTotalAmount)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-emerald-700">{row.paymentSource === "not_applicable" ? <span className="text-slate-300">—</span> : money(row.invoiceCorporatePaidTotal)}</td>
                <td className="px-3 py-2.5"><div className="space-y-1">{row.payments.length ? row.payments.map((payment) => <div key={payment.linkId} className="text-[11px] text-slate-600"><span>{payment.paymentDate}</span><span className="mx-1 text-slate-300">·</span><span className="font-semibold text-slate-800">{money(payment.allocatedAmount)}</span><div className="text-[10px] text-slate-400">{payment.paymentAccount || payment.paymentAccountName || "未记录账户"}{payment.voucherNo ? ` · ${payment.voucherNo}` : ""}</div></div>) : row.paymentSource === "personal" ? <span className="text-amber-600">无对公流水 · 按个人支付</span> : row.paymentSource === "platform_auto_debit" ? <span className="text-cyan-700">平台自动扣款货款 · 无银行流水</span> : row.paymentSource === "not_applicable" ? <span className="text-slate-400" title={row.bankReconciliationReason || "该发票不参与银行付款核对"}>不适用</span> : <span className="text-slate-300">未记录银行付款</span>}{corporateBankStatus(row) === "overpaid_after_red" && <div className="rounded bg-rose-50 px-2 py-1 text-[10px] font-medium text-rose-700">红冲后历史超额 {money(row.invoiceOverpaidAmount)} · 当前待处理 {money(row.invoiceOverpaidUnsettledAmount || row.invoiceOverpaidAmount)}</div>}{corporateBankStatus(row) === "red_overpayment_settled" && <div className="rounded bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700">红冲超额 {money(row.invoiceOverpaidAmount)} 已完成退款/冲抵</div>}{row.redCrossPeriod && <div className="text-[10px] text-violet-600">跨期红冲 · 原蓝票账期 {row.redRelatedInvoicePeriod || "待核"}</div>}</div></td>
                <td className="max-w-[260px] px-3 py-2.5 text-slate-600">{row.purchaseOrderNos.join("、") || "—"}</td>
                <td className="px-3 py-2.5 text-right">{readOnly ? <span className="text-[11px] text-slate-300">只读预览</span> : row.paymentSource === "not_applicable" ? <span className="text-[11px] text-slate-300">—</span> : row.paymentSource === "platform_auto_debit" ? <span className="text-[11px] font-medium text-cyan-700">平台扣款</span> : <button type="button" onClick={goMatch} className="rounded-md border border-blue-200 px-2.5 py-1.5 text-[11px] font-medium text-blue-600 hover:bg-blue-50">{row.paymentSource === "corporate" ? "查看银行流水" : "匹配银行流水"}</button>}</td>
              </tr>;
              })}</tbody>
            </table>
          </div>
        </>}
      </>
    );
  }

  function DataIcon({ kind }: { kind: "bank" | "receipt" | "sales" | "purchase_inbound" | "sales_query" }) {
    const tone = kind === "bank"
      ? "bg-blue-50 text-blue-600 ring-blue-100"
      : kind === "receipt"
        ? "bg-emerald-50 text-emerald-600 ring-emerald-100"
        : kind === "purchase_inbound"
          ? "bg-violet-50 text-violet-600 ring-violet-100"
          : kind === "sales_query"
            ? "bg-cyan-50 text-cyan-600 ring-cyan-100"
            : "bg-orange-50 text-orange-600 ring-orange-100";
    const path = kind === "sales" || kind === "sales_query"
      ? "M5 19V9m4 10V5m5 14v-7m5 7V3"
      : kind === "purchase_inbound"
        ? "M4 7.5 12 3l8 4.5v9L12 21l-8-4.5v-9Zm0 0 8 4.5 8-4.5M12 12v9M8 5.2l8 4.5"
        : kind === "receipt"
          ? "M6 3h9l3 3v15H6V3Zm9 0v3h3M9 11h6M9 15h6"
          : "M4 20V8l8-4 8 4v12M7 20v-6h10v6M8 9h.01M12 9h.01M16 9h.01";
    return <span className={`inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ${tone}`} aria-hidden="true"><svg viewBox="0 0 24 24" className="h-5 w-5" fill="none"><path d={path} stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span>;
  }

  /** 资料行统一用表格网格排版，桌面端对齐参考图，窄屏自动收拢。 */
  function ItemRow({ kind, title, state, summary, updatedAt, version, parameter, selected, onToggle, selectable, actions }: {
    kind: "bank" | "receipt" | "sales" | "purchase_inbound" | "sales_query";
    title: string;
    state: { ok: boolean; text: string };
    summary: React.ReactNode;
    updatedAt?: string | null;
    version?: number | null;
    parameter: React.ReactNode;
    selected: boolean;
    onToggle: () => void;
    selectable?: boolean;
    actions: React.ReactNode;
  }) {
    return (
      <div className="grid gap-3 border-t border-slate-100 px-4 py-3 md:grid-cols-[28px_minmax(180px,1.2fr)_minmax(150px,1fr)_58px_112px_88px_minmax(150px,1fr)_auto] md:items-center">
        <div className="flex items-center md:justify-center">
          <input type="checkbox" checked={selected} onChange={onToggle} disabled={selectable === false} aria-label={`选择 ${title}`} className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500 disabled:cursor-default disabled:opacity-70" />
        </div>
        <div className="flex min-w-0 items-center gap-2.5">
          <DataIcon kind={kind} />
          <div className="min-w-0">
            <div className="truncate text-[13px] font-semibold text-slate-800">{title}</div>
            <div className="mt-1 truncate text-[11px] text-slate-400">{kind === "sales" ? "系统生成 · 销售出库数据" : kind === "purchase_inbound" ? "每月业务源文件 · 入库与成本" : kind === "sales_query" ? "每月业务源文件 · 销售数量" : kind === "bank" ? "银行流水原始资料" : "银行回单原始资料"}</div>
          </div>
        </div>
        <div className="min-w-0 text-[11px] leading-5 text-slate-500">{summary}</div>
        <div className="text-xs font-medium text-slate-600">{version ? `v${version}` : "—"}</div>
        <div className="text-[11px] leading-4 text-slate-500">{formatDate(updatedAt)}</div>
        <div><span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium ring-1 ring-inset ${state.ok ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-amber-200"}`}><span className={`h-1.5 w-1.5 rounded-full ${state.ok ? "bg-emerald-500" : "bg-amber-500"}`} />{state.text}</span></div>
        <div className="min-w-0 truncate text-[11px] text-slate-500">{parameter}</div>
        <div className="flex flex-wrap items-center justify-start gap-1.5 md:justify-end">{actions}</div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-[1500px] space-y-4 pb-8">
      <nav aria-label="月结中心导航" className="sticky top-0 z-20 -mx-2 flex min-h-12 items-center gap-1 overflow-x-auto border-b border-slate-200 bg-white px-2 shadow-[0_1px_0_rgba(15,23,42,0.02)] sm:-mx-4 sm:px-4">
        <Link href="/finance/monthly-send" className="mr-2 inline-flex shrink-0 items-center gap-2 px-1 py-3 text-sm font-semibold text-slate-800"><span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-blue-600 text-white"><svg viewBox="0 0 24 24" className="h-4 w-4" fill="none"><path d="M7 4h10v16H7V4Zm3 3h4M10 11h4M10 15h3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg></span>月结中心</Link>
        <span className="shrink-0 px-1 text-slate-300">›</span>
        {([
          ["monthly", "本月月结与发送"],
          ["corporate", "已收票付款明细"],
          ["records", "发送记录"],
          ["archive", "资料归档"],
          ["ledger", "销售汇总台账"],
        ] as const).map(([key, label]) => (
          <button key={key} type="button" disabled={!domesticSupportedByEntity && (key === "ledger" || key === "corporate")} onClick={() => setFinanceTab(key)} className={`relative shrink-0 px-4 py-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:text-slate-300 ${financeTab === key || (key === "corporate" && financeTab === "match") ? "text-blue-600" : "text-slate-600 hover:text-blue-600"}`}>
            {label}{(financeTab === key || (key === "corporate" && financeTab === "match")) && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-blue-600" />}
          </button>
        ))}
        <button type="button" onClick={() => setMsg("每月发送会按选定账期汇总资料，确认发送前可在右侧检查附件与收件人。")} className="ml-auto inline-flex shrink-0 items-center gap-1.5 px-2 py-3 text-xs text-slate-500 hover:text-blue-600">ⓘ 使用帮助</button>
      </nav>

      <header className="flex flex-wrap items-end justify-between gap-3 px-1">
        <div><h1 className="text-2xl font-bold tracking-tight text-slate-900">月结中心</h1><p className="mt-1 text-sm text-slate-500">按公司主体读取业务结果，核对交付资料，生成月度财务包并发送。</p></div>
        <button type="button" onClick={openSendSettings} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm font-medium text-slate-700 shadow-sm hover:border-blue-300 hover:text-blue-600">
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2" stroke="currentColor" strokeWidth="1.8" /><path d="m3 7 9 6 9-6" stroke="currentColor" strokeWidth="1.8" /></svg>
          发送设置{mailStatus && !mailStatus.configured && <span title="SMTP 未配置" className="h-1.5 w-1.5 rounded-full bg-rose-500" />}
        </button>
      </header>

      <section className={`${CARD} flex flex-wrap items-center gap-3 px-3 py-3 sm:px-4`}>
        <label className="min-w-[260px]">
          <span className="sr-only">公司主体</span>
          <select
            value={entityId ?? ""}
            onChange={(e) => setEntityId(Number(e.target.value) || null)}
            className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-700 shadow-sm"
          >
            {entities.map((row) => (
              <option key={row.id} value={row.id}>{row.name}{row.isDefault ? " · 默认" : ""}</option>
            ))}
          </select>
        </label>
        <div className="flex items-center rounded-lg border border-slate-200 bg-white shadow-sm">
          <button type="button" aria-label="上一个账期" onClick={() => setSendMonth(shiftMonthValue(sendMonth, -1))} className="px-3 py-2 text-lg leading-none text-slate-500 hover:bg-slate-50 hover:text-blue-600">‹</button>
          <label className="border-x border-slate-200 px-3 text-sm font-semibold text-slate-800"><span className="sr-only">账期</span><input type="month" value={sendMonth} onChange={(e) => setSendMonth(e.target.value)} className="w-[118px] border-0 bg-transparent p-0 text-sm font-semibold outline-none" /></label>
          <button type="button" aria-label="下一个账期" onClick={() => setSendMonth(shiftMonthValue(sendMonth, 1))} className="px-3 py-2 text-lg leading-none text-slate-500 hover:bg-slate-50 hover:text-blue-600">›</button>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">月度状态：<span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ring-1 ring-inset ${monthlyReady ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-amber-200"}`}><span className={`h-1.5 w-1.5 rounded-full ${monthlyReady ? "bg-emerald-500" : "bg-amber-500"}`} />{monthlyReady ? "全量资料齐全" : businessStatus && !businessReady ? "待补业务成本" : "待补财务资料"}</span></div>
        <div className="flex items-center gap-2 text-xs text-slate-500">发送状态：<span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ring-1 ring-inset ${period?.status === "SENT" ? "bg-blue-50 text-blue-700 ring-blue-200" : "bg-slate-50 text-slate-600 ring-slate-200"}`}><span className={`h-1.5 w-1.5 rounded-full ${period?.status === "SENT" ? "bg-blue-500" : "bg-slate-400"}`} />{period?.status === "SENT" ? "已发送" : period?.status === "PACKAGED" ? "已打包" : "未发送"}</span></div>
        <div className="text-xs text-slate-500">最后发送：<span className="text-slate-700">{latestSentPkg ? formatDate(latestSentPkg.createdAt) : "—"}</span></div>
        <button type="button" onClick={() => latestPkg ? void downloadPackage(latestPkg) : setMsg("该账期暂无可导出的发送包")} disabled={busy || !latestPkg} className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-blue-600 hover:border-blue-300 disabled:cursor-not-allowed disabled:text-slate-300"><svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" /></svg>导出清单</button>
      </section>

      {msg && <div role="status" className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">{msg}</div>}

      {financeTab === "monthly" && <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className={`${CARD} min-w-0 overflow-hidden`}>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
            <div><h2 className="text-base font-semibold text-slate-900">本月处理清单 <span className="ml-1 text-slate-500">({readyCount}/{requiredDeliveryCount})</span></h2><p className="mt-1 text-xs text-slate-400">业务数据自动读取；这里按主体整理银行资料、系统生成资料并发送给财务</p></div>
            <div className="flex items-center gap-2"><button type="button" onClick={() => { uploadKind.current = "交易明细"; fileRef.current?.click(); }} disabled={busy} className="inline-flex items-center gap-1 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50">＋ 新增资料</button><button type="button" onClick={() => { loadFiles(); loadPeriods(); loadBusiness(); loadUnbilled(); loadCorporatePayment(); }} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">刷新</button></div>
          </div>
          <div className="hidden items-center gap-3 bg-slate-50 px-4 py-2 text-[11px] font-medium text-slate-500 md:grid md:grid-cols-[28px_minmax(180px,1.2fr)_minmax(150px,1fr)_58px_112px_88px_minmax(150px,1fr)_auto]">
            <span /><span>资料名称</span><span>资料摘要</span><span>当前版本</span><span>更新时间</span><span>资料状态</span><span>参数摘要</span><span className="text-right">操作</span>
          </div>
          <div className={`border-t border-slate-100 px-4 py-3 ${businessReady ? "bg-emerald-50/45" : "bg-amber-50/55"}`}>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px]">
              <span className={`font-medium ${businessReady ? "text-emerald-700" : "text-amber-700"}`}>
                {businessStatus ? (businessReady ? "✓ 业务数据已接通" : "⚠ 业务成本待补齐") : "正在读取业务数据…"}
              </span>
              {businessStatus?.domesticSupported ? (
                <>
                  <span className="text-slate-500">销售订单 {businessStatus.salesOrderCount} 笔</span>
                  <span className="text-slate-500">销售额 {money(businessStatus.salesAmount)}</span>
                  <span className="text-slate-500">销售成本 {money(businessStatus.costAmount)}</span>
                  {businessStatus.costIncomplete && (
                    <span className="text-amber-700">缺 {businessStatus.costMissingCount} 个 SKU 成本</span>
                  )}
                </>
              ) : (
                <span className="text-slate-500">当前主体无国内销售月结口径</span>
              )}
              {(businessStatus?.foreignEntryCount ?? 0) > 0 && (
                <span className="font-medium text-violet-700">外贸财务事项 {businessStatus?.foreignEntryCount} 条</span>
              )}
              {domesticSupportedByEntity && (
                <span className="ml-auto flex gap-3">
                  <Link href="/sales?tab=analytics" className="font-medium text-blue-600 hover:underline">销售中心</Link>
                  <Link href="/supply-chain/receiving" className="font-medium text-blue-600 hover:underline">采购入库</Link>
                </span>
              )}
            </div>
            {businessStatus?.costIncomplete && businessStatus.costMissingDetail.length > 0 && (
              <div className="mt-1 truncate text-[10px] text-amber-700">
                待补：{businessStatus.costMissingDetail.slice(0, 8).map((item) => item.skuCode || item.skuName).join("、")}
                {businessStatus.costMissingDetail.length > 8 ? "…" : ""}
              </div>
            )}
          </div>
          <ItemRow kind="bank" title={`${sel?.month || ""}月-银行交易明细`} state={{ ok: Boolean(bankTx), text: bankTx ? "已导入" : "待上传" }} summary={bankTx ? <>全部账户 · 已导入<br />{formatBytes(bankTx.size)}</> : <>等待上传原始银行流水<br /><span className="text-amber-600">上传后自动归档</span></>} updatedAt={bankTx?.uploadedAt} version={bankTx?.version} parameter={bankTx ? "账期内 · 全部账户" : "待补充资料"} selected={includeSel.includes("交易明细")} onToggle={() => setIncludeSel((s) => s.includes("交易明细") ? s.filter((x) => x !== "交易明细") : [...s, "交易明细"])} actions={<>{bankTx && <button type="button" onClick={() => void downloadFile(bankTx)} disabled={busy} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50">下载</button>}<button type="button" onClick={() => { uploadKind.current = "交易明细"; fileRef.current?.click(); }} disabled={busy} className="rounded-md bg-blue-600 px-2.5 py-1.5 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-50">{bankTx ? "替换" : "上传"}</button></>} />
          <ItemRow kind="receipt" title={`${sel?.month || ""}月-银行回单详情`} state={{ ok: Boolean(bankReceipt), text: bankReceipt ? "已导入" : "待上传" }} summary={bankReceipt ? <>全部账户 · 已导入<br />{formatBytes(bankReceipt.size)}</> : <>等待上传银行回单<br /><span className="text-amber-600">上传后自动归档</span></>} updatedAt={bankReceipt?.uploadedAt} version={bankReceipt?.version} parameter={bankReceipt ? "账期内 · 全部账户" : "待补充资料"} selected={includeSel.includes("回单详情")} onToggle={() => setIncludeSel((s) => s.includes("回单详情") ? s.filter((x) => x !== "回单详情") : [...s, "回单详情"])} actions={<>{bankReceipt && <button type="button" onClick={() => void downloadFile(bankReceipt)} disabled={busy} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50">下载</button>}<button type="button" onClick={() => { uploadKind.current = "回单详情"; fileRef.current?.click(); }} disabled={busy} className="rounded-md bg-blue-600 px-2.5 py-1.5 text-[11px] font-medium text-white hover:bg-blue-700 disabled:opacity-50">{bankReceipt ? "替换" : "上传"}</button></>} />
          {needsUnbilled && (<ItemRow kind="sales" title={`${sel?.month || ""}月-销售出库-无票收入`} state={{ ok: Boolean(unbilled), text: unbilled ? (unbilled.adjusted ? "已调整" : "已生成") : "计算中" }} summary={unbilled ? <>销售总额 {money(unbilled.salesAmount)} · 红字调整 {money(unbilled.redSalesAdjustmentAmount || 0)}<br />调整后销售 {money(unbilled.adjustedSalesAmount || unbilled.salesAmount)} · 无票收入 {money(unbilled.unbilledAmount)}</> : <>正在读取销售出库数据<br /><span className="text-slate-400">按当前账期自动计算</span></>} updatedAt={unbilled?.updatedAt || salesFile?.uploadedAt} version={unbilled?.version || salesFile?.version} parameter={unbilled?.adjusted ? `已选择 ${unbilled.selectedCount || 0}/${unbilled.sourceCount || 0} 条` : "出库时间 · 全部渠道"} selected={includeSel.includes("无票收入")} onToggle={() => setIncludeSel((s) => s.includes("无票收入") ? s.filter((x) => x !== "无票收入") : [...s, "无票收入"])} actions={<><button type="button" onClick={() => { setUnbilledDetailMode("adjust"); setShowUnbilledDetail(true); }} disabled={!unbilled} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">调整明细</button><button type="button" onClick={() => { setUnbilledDetailMode("preview"); setShowUnbilledDetail(true); }} disabled={!unbilled || busy} className="rounded-md border border-blue-200 px-2.5 py-1.5 text-[11px] text-blue-600 hover:bg-blue-50 disabled:opacity-50">预览</button></>} />)}
          {needsUnbilled && (<ItemRow kind="purchase_inbound" title={`${sel?.month || ""}月-已收票对公付款明细`} state={corporateDeliveryState} summary={corporatePayment ? <>进项发票 {corporatePayment.summary.invoiceCount} 张 · 对公付款 {corporatePayment.summary.paymentCount} 笔<br />已关联 {money(corporatePayment.summary.allocatedTotal)}</> : <>正在关联银行付款、发票与采购商品<br /><span className="text-slate-400">只统计已确认的付款↔发票关联</span></>} parameter={corporateReviewSummary} updatedAt={corporatePayment?.updatedAt} version={corporatePayment?.version} selected={includeSel.includes("已收票对公付款明细")} onToggle={() => setIncludeSel((s) => s.includes("已收票对公付款明细") ? s.filter((x) => x !== "已收票对公付款明细") : [...s, "已收票对公付款明细"])} actions={<><button type="button" onClick={() => { setShowCorporateDetail(true); void loadCorporatePayment(); }} disabled={!corporatePayment} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40">调整明细</button><button type="button" onClick={() => { setCorporateView("preview"); setFinanceTab("corporate"); void loadCorporatePayment(); }} disabled={!corporatePayment} className="rounded-md border border-violet-200 px-2.5 py-1.5 text-[11px] text-violet-600 hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-40">预览</button></>} />)}
          <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.pdf" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) handleUploadSelection(file); }} />
        </section>

        <aside className={`${CARD} h-fit overflow-hidden xl:sticky xl:top-4`}>
          <div className="border-b border-slate-200 px-4 py-4">
            <h2 className="text-base font-semibold text-slate-900">本次发送摘要</h2>
            <p className="mt-1 text-xs text-slate-400">资料选择只在左侧主清单修改；这里仅确认本次将发送什么、发给谁。</p>
          </div>
          <div className="space-y-2 bg-slate-50/70 p-3">
            {([
              ["交易明细", `${sel?.month || ""}月-银行交易明细`, bankTx, "bank"],
              ["回单详情", `${sel?.month || ""}月-银行回单详情`, bankReceipt, "receipt"],
              ["无票收入", `${sel?.month || ""}月-销售出库-无票收入`, unbilled, "sales"],
              ["已收票对公付款明细", `${sel?.month || ""}月-已收票对公付款明细`, corporatePayment, "purchase_inbound"],
            ] as const)
              .filter(([key]) => (key !== "无票收入" || needsUnbilled) && includeSel.includes(key))
              .map(([key, label, item, kind]) => (
                <div key={key} className="flex items-center gap-2 rounded-lg bg-white px-2.5 py-2 shadow-sm ring-1 ring-slate-100">
                  <DataIcon kind={kind} />
                  <span className="min-w-0 flex-1 truncate text-xs font-medium text-slate-700">{label}</span>
                  <span className={`h-2 w-2 shrink-0 rounded-full ${item ? "bg-emerald-500" : "bg-amber-400"}`} title={item ? "资料已就绪" : "资料未就绪"} />
                </div>
              ))}
            {!includeSel.length && !hasForeignSummary && <div className="rounded-lg border border-dashed border-slate-200 bg-white px-3 py-5 text-center text-xs text-slate-400">左侧主清单尚未选择发送资料</div>}
            {hasForeignSummary && (
              <div className="flex items-center gap-2 rounded-lg bg-violet-50 px-2.5 py-2 ring-1 ring-violet-100">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white text-violet-600">€</span>
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-violet-800">{sel?.month || ""}月-外贸财务汇总</span>
                <span className="text-[9px] text-violet-500">系统生成</span>
              </div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3 border-b border-slate-100 px-4 py-3 text-xs">
            <div><div className="text-slate-400">预计附件</div><div className="mt-1 font-semibold text-slate-800">{includeSel.length + (hasForeignSummary ? 1 : 0)} 个</div></div>
            <div><div className="text-slate-400">银行原件大小</div><div className="mt-1 font-semibold text-slate-800">{formatBytes(attachmentSize)}</div></div>
          </div>
          <div className="space-y-3 px-4 py-4">
            <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 text-xs">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-slate-400">发送至</div>
                  <div className="mt-1 break-all font-medium text-slate-700">{emails(toText).join("、") || "未设置"}</div>
                  {emails(ccText).length > 0 && <div className="mt-1 break-all text-[11px] text-slate-500">抄送：{emails(ccText).join("、")}</div>}
                  <div className="mt-2 text-[11px] text-slate-500">{template?.autoSend ? `自动发送：每月 ${template.sendDay} 日 ${String(template.sendHour).padStart(2, "0")}:00` : "发送方式：仅手动发送"}</div>
                </div>
                <button type="button" onClick={openSendSettings} className="shrink-0 rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-blue-600 hover:border-blue-300">修改设置</button>
              </div>
            </div>
            <div>
              <div className="mb-1.5 text-xs font-medium text-slate-600">本次发送条件</div>
              <div className="space-y-1.5 text-xs text-slate-500">
                {includeSel.includes("交易明细") && <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(bankTx)} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />银行交易明细已就绪</label>}
                {includeSel.includes("回单详情") && <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(bankReceipt)} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />银行回单详情已就绪</label>}
                {includeSel.includes("无票收入") && <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(unbilled && businessReady)} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />无票收入已生成且业务成本完整</label>}
                {includeSel.includes("已收票对公付款明细") && <label className="flex items-center gap-2"><input type="checkbox" checked={Boolean(corporatePayment)} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />已收票对公付款明细已生成</label>}
                <label className="flex items-center gap-2"><input type="checkbox" checked={emails(toText).length > 0} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />财务收件人已设置</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={mailReady} disabled className="h-4 w-4 rounded border-slate-300 text-blue-600" />邮件通道可用</label>
              </div>
            </div>
            <button type="button" onClick={packageAndSend} disabled={busy || !sel || !sendReady} className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40">➤ 确认并发送给财务</button>
            <div className="text-[11px] leading-4 text-slate-400">{latestPkg ? <>最新包 V{latestPkg.version} · {latestPkg.status === "SENT" ? "已发送" : "已打包"} · {formatDate(latestPkg.createdAt)}</> : "该账期还没有打包记录"}{mailStatus && !mailStatus.configured && <span className="ml-1.5 rounded bg-rose-50 px-1.5 py-0.5 font-medium text-rose-600 ring-1 ring-inset ring-rose-200">SMTP 未配置</span>}</div>
          </div>
        </aside>
      </div>}

      {financeTab === "records" && <section className={`${CARD} overflow-hidden`}><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4"><div><h2 className="text-base font-semibold text-slate-900">发送记录</h2><p className="mt-1 text-xs text-slate-400">每次打包与发送都会保留版本，便于回溯本次实际发送内容。</p></div><span className="text-xs text-slate-400">{period?.packages.length || 0} 条记录</span></div>{!period?.packages.length ? <div className="px-4 py-12 text-center text-sm text-slate-400">该账期暂无打包 / 发送记录</div> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-sm"><thead className="bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2.5 font-medium">版本</th><th className="px-4 py-2.5 font-medium">状态</th><th className="px-4 py-2.5 font-medium">SHA256</th><th className="px-4 py-2.5 font-medium">打包时间</th><th className="px-4 py-2.5 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-slate-100">{[...period.packages].reverse().map((pkg) => <tr key={pkg.id}><td className="px-4 py-3 font-medium">V{pkg.version}</td><td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-xs ring-1 ring-inset ${pkg.status === "SENT" ? "bg-emerald-50 text-emerald-700 ring-emerald-200" : "bg-blue-50 text-blue-700 ring-blue-200"}`}>{pkg.status === "SENT" ? "已发送" : "已打包"}</span></td><td className="px-4 py-3 font-mono text-xs text-slate-400">{pkg.sha256}…</td><td className="px-4 py-3 text-xs text-slate-500">{formatDate(pkg.createdAt)}</td><td className="px-4 py-3 text-right"><button type="button" onClick={() => void downloadPackage(pkg)} disabled={busy} className="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50">下载 ZIP</button><button type="button" onClick={() => void deletePackage(pkg)} disabled={busy} className="ml-4 text-xs font-medium text-rose-500 hover:underline disabled:opacity-50">删除</button></td></tr>)}</tbody></table></div>}</section>}

      {financeTab === "archive" && <section className={`${CARD} overflow-hidden`}><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4"><div><h2 className="text-base font-semibold text-slate-900">资料归档</h2><p className="mt-1 text-xs text-slate-400">业务源文件、银行资料、系统生成资料和历史版本都保留在当前账期。</p></div><button type="button" onClick={() => { uploadKind.current = "交易明细"; fileRef.current?.click(); }} disabled={busy} className="rounded-lg border border-blue-200 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50">＋ 新增资料</button></div>{!files.length ? <div className="px-4 py-12 text-center text-sm text-slate-400">该账期暂无归档文件</div> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-sm"><thead className="bg-slate-50 text-left text-xs text-slate-500"><tr><th className="px-4 py-2.5 font-medium">文件</th><th className="px-4 py-2.5 font-medium">类型</th><th className="px-4 py-2.5 font-medium">版本</th><th className="px-4 py-2.5 font-medium">归档时间</th><th className="px-4 py-2.5 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-slate-100">{[...files].sort((a, b) => a.originalName.localeCompare(b.originalName) || a.version - b.version).map((row) => <tr key={row.id}><td className="px-4 py-3">{deliveryName(sel?.month ?? 0, row.originalName)}<span className="ml-2 text-[10px] text-slate-300">{row.originalName}</span></td><td className="px-4 py-3 text-xs text-slate-500">{row.category === "sales_summary" ? "系统生成" : row.category === "bank" ? "银行资料" : row.category === "purchase_inbound" ? "采购入库源文件" : row.category === "sales_query" ? "销售数量源文件" : "外部数据导入"}</td><td className="px-4 py-3 text-xs text-slate-500">v{row.version}</td><td className="px-4 py-3 text-xs text-slate-400">{formatDate(row.uploadedAt)}</td><td className="px-4 py-3 text-right"><button type="button" onClick={() => void downloadFile(row)} disabled={busy} className="text-xs font-medium text-blue-600 hover:underline disabled:opacity-50">下载</button><button type="button" onClick={() => void deleteFile(row)} disabled={busy} className="ml-4 text-xs font-medium text-rose-500 hover:underline disabled:opacity-50">删除</button></td></tr>)}</tbody></table></div>}<div className="border-t border-slate-100 px-4 py-3 text-[11px] text-slate-400">每次上传 / 生成都会留版本；业务源文件用于本地入库计算，财务交付包只取所选交付资料。</div><input ref={fileRef} type="file" accept=".xlsx,.xls,.csv,.pdf" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) handleUploadSelection(file); }} /></section>}

      {financeTab === "ledger" && <section className={`${CARD} overflow-hidden`}><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4"><div><h2 className="text-base font-semibold text-slate-900">销售汇总台账</h2><p className="mt-1 text-xs text-slate-400">月度时间 / 仓库 / 税务编号 / 发货总数量 / 销售总金额 / 销售总成本；无票收入表自动引用销售总金额。</p></div><div className="flex gap-2"><button type="button" onClick={previewSales} disabled={busy} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">预览</button><button type="button" onClick={() => setShowFields((v) => !v)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">字段配置</button><button type="button" onClick={generateSales} disabled={busy} className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">生成并归档</button></div></div>{showFields && template && <div className="border-b border-slate-100 bg-slate-50/70 p-4"><div className="grid gap-2 lg:grid-cols-2">{template.fields.map((field, index) => <div key={field.key} className={`grid grid-cols-[28px_1fr_auto] items-center gap-2 rounded-lg border px-2 py-2 ${field.enabled ? "border-blue-100 bg-blue-50/30" : "border-slate-100 bg-white"}`}><input type="checkbox" checked={field.enabled} onChange={(e) => updateField(index, { enabled: e.target.checked })} /><div className="flex min-w-0 items-center gap-2"><span className="w-28 shrink-0 truncate font-mono text-[10px] text-slate-400">{field.key}</span><input value={field.label} onChange={(e) => updateField(index, { label: e.target.value })} className="min-w-0 flex-1 rounded border border-slate-200 bg-white px-2 py-1 text-xs" /></div><div className="flex gap-1"><button type="button" onClick={() => moveField(index, -1)} disabled={index === 0} className="rounded border border-slate-200 px-2 py-1 text-[10px] text-slate-500 disabled:opacity-30">↑</button><button type="button" onClick={() => moveField(index, 1)} disabled={index === template.fields.length - 1} className="rounded border border-slate-200 px-2 py-1 text-[10px] text-slate-500 disabled:opacity-30">↓</button></div></div>)}</div><div className="mt-3 flex justify-end"><button type="button" onClick={() => { void saveTemplate("fields"); }} disabled={busy} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:opacity-50">保存字段配置</button></div></div>}{preview && <div className="p-4"><div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 md:grid-cols-5">{[["订单数", preview.summary.orderCount], ["仓库数", preview.summary.warehouseCount], ["发货总数量", Number(preview.summary.totalQuantity).toLocaleString("zh-CN")], ["销售总金额", money(preview.summary.salesAmount)], ["销售总成本", money(preview.summary.costAmount)]] .map(([label, value]) => <div key={String(label)} className="bg-white p-3"><div className="text-[10px] text-slate-400">{label}</div><div className="mt-1 text-lg font-semibold text-slate-800">{value}</div></div>)}</div>{preview.summary.costIncomplete && <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700 ring-1 ring-inset ring-amber-200"><span>部分 SKU 没有采购入库成本（{(preview.summary.costMissingDetail || []).map((d) => d.skuCode).join("、") || "—"}），销售总成本只含已覆盖部分，实际成本会更高。请补充采购入库成本后重新生成。</span><Link href="/data-center-import?tab=jackyun" className="shrink-0 font-medium text-amber-800 underline">去补充入库成本</Link></div>}<div className="mt-3 overflow-x-auto rounded-xl border border-slate-200"><table className="w-full min-w-[900px] text-xs"><thead className="bg-slate-50 text-left text-slate-500"><tr>{preview.fields.map((field) => <th key={field.key} className="whitespace-nowrap px-3 py-2 font-medium">{field.label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{preview.rows.slice(0, 12).map((row, index) => <tr key={index}>{preview.fields.map((field) => <td key={field.key} className="max-w-[220px] truncate px-3 py-2 text-slate-600">{row[field.key] || "—"}</td>)}</tr>)}</tbody></table><div className="px-3 py-2 text-[10px] text-slate-400">预览前 {Math.min(12, preview.rows.length)} 行 · 本月共 {preview.rowCount} 个仓库</div></div></div>}{!preview && <div className="px-4 py-12 text-center text-sm text-slate-400">点击“预览”查看当前账期数据，字段配置可直接调整导出列。</div>}</section>}

      {financeTab === "corporate" && <section className={`${CARD} overflow-hidden`}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">{sel?.month || ""}月-已收票付款明细</h2>
            <p className="mt-1 text-xs text-slate-400">{corporateView === "adjust" ? "财务主视图按“支付方式 + 费用性质”归纳：银行流水匹配成功为对公支付，无对公流水的有效进项票默认按个人支付；人工标记的平台自动扣款货款单独列示；对公行同步展示付款时间、账号、凭证和金额。" : `发送前只读预览 · ${corporatePayment?.adjusted ? `当前保存版本 v${corporatePayment.version || "—"}，显示 ${corporatePayment.selectedCount || 0}/${corporatePayment.sourceCount || 0} 张发票` : "尚未人工调整，按当前全部发票发送"}；是否纳入本次发送以月结清单勾选状态为准。`}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex overflow-hidden rounded-lg border border-slate-200 bg-white text-xs">
              <button type="button" onClick={() => setCorporateView("adjust")} className={`px-3 py-2 font-medium ${corporateView === "adjust" ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-50"}`}>明细核对</button>
              <button type="button" onClick={() => setCorporateView("preview")} className={`px-3 py-2 font-medium ${corporateView === "preview" ? "bg-violet-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>发送预览</button>
            </div>
            {corporateView === "adjust" ? <>
              <button type="button" onClick={() => { setFinanceTab("match"); void loadMatch(); }} className="rounded-lg border border-blue-200 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50">核对银行付款</button>
              <button type="button" onClick={loadCorporatePayment} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">刷新</button>
            </> : <>
              <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${includeSel.includes("已收票对公付款明细") ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-1 ring-amber-200"}`}>{includeSel.includes("已收票对公付款明细") ? "已纳入本次发送" : "未纳入本次发送"}</span>
              <button type="button" onClick={packageAndSend} disabled={busy || !sel || !sendReady || !includeSel.includes("已收票对公付款明细")} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "发送中…" : "确认并发送给财务"}</button>
            </>}
          </div>
        </div>
        {corporateAdjustBody(corporateView === "preview", () => { setFinanceTab("match"); void loadMatch(); })}
      </section>}

      {financeTab === "match" && <section className={`${CARD} overflow-hidden`}>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-4">
          <div>
            <div className="mb-1 text-[11px] font-medium text-violet-600">{sel?.month || ""}月-已收票付款明细 / 银行核对</div>
            <h2 className="text-base font-semibold text-slate-900">发票 ↔ 银行付款核对</h2>
            <p className="mt-1 text-xs text-slate-400">这是“已收票付款明细”的内部核对步骤：按进项发票逐张核对银行付款，银行流水只作为核对依据。</p>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => { setCorporateView("adjust"); setFinanceTab("corporate"); void loadCorporatePayment(); }} className="rounded-lg border border-violet-200 px-3 py-2 text-xs font-medium text-violet-600 hover:bg-violet-50">返回付款明细</button>
            <button type="button" onClick={loadMatch} disabled={matchLoading} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">刷新</button>
          </div>
        </div>
        {matchData && (
          <div className="grid grid-cols-2 gap-px bg-slate-100 sm:grid-cols-5">
            {([
              ["进项发票", money(matchData.summary.invoiceTotal), `${matchData.summary.invoiceCount} 张`],
              ["银行付款已核对", money(matchData.summary.invoiceMatchedTotal), `${matchData.summary.invoiceMatchedCount} 张完成`],
              ["待核对银行付款", money(matchData.summary.invoiceOutstandingTotal), `${matchData.summary.invoiceUnmatchedCount} 张待核对`],
              ["银行付款部分核对", String(matchData.summary.invoicePartialCount), "张发票"],
              ["可用银行支出", money(matchData.summary.paymentTotal), `${matchData.summary.txnCount} 笔`],
            ] as const).map(([label, value, hint]) => (
              <div key={label} className="bg-white px-4 py-3">
                <div className="text-[10px] text-slate-400">{label}</div>
                <div className="mt-1 text-base font-semibold text-slate-800">{value}</div>
                <div className="mt-0.5 text-[10px] text-slate-400">{hint}</div>
              </div>
            ))}
          </div>
        )}
        {matchLoading && <div className="px-4 py-10 text-center text-sm text-slate-400">加载中…</div>}
        {matchData && !matchData.invoicePool.length && <div className="px-4 py-12 text-center text-sm text-slate-400">当前账期没有需要核对银行付款的进项发票；红冲、作废、待确认及非正数金额发票不会进入付款核对。</div>}
        {matchData && Boolean(matchData.invoicePool.length) && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1260px] text-sm">
              <thead className="bg-slate-50 text-left text-xs text-slate-500">
                <tr><th className="px-4 py-2.5 font-medium">发票日期</th><th className="px-4 py-2.5 font-medium">供应商</th><th className="px-4 py-2.5 font-medium">发票号码</th><th className="px-4 py-2.5 text-right font-medium">价税合计</th><th className="px-4 py-2.5 text-right font-medium">银行已核对金额</th><th className="px-4 py-2.5 text-right font-medium">待核对银行金额</th><th className="px-4 py-2.5 font-medium">已关联银行流水</th><th className="px-4 py-2.5 font-medium">银行核对状态</th><th className="px-4 py-2.5 text-right font-medium">操作</th></tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {matchData.invoicePool.map((row) => (
                  <tr key={row.id} className={row.bankMatchStatus === "unmatched" ? "bg-amber-50/25" : ""}>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-600">{row.issueDate || "—"}</td>
                    <td className="max-w-[220px] truncate px-4 py-3 text-slate-800">{row.sellerName || <span className="text-slate-300">销方未名</span>}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-600">{row.invoiceNumber || "—"}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-medium text-slate-800">{money(row.totalAmount)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right text-emerald-700">{money(row.bankLinkedAmount)}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right font-medium text-amber-700">{money(row.remaining)}</td>
                    <td className="px-4 py-3">
                      {row.links.length ? <div className="space-y-1">{row.links.map((link) => <div key={link.linkId || `${row.id}-${link.txnId}`} className="text-xs"><div className="flex flex-wrap items-center gap-2"><span className="text-slate-600">{link.txnDate}</span><span className="font-medium text-slate-700">{money(link.allocatedAmount || 0)}</span>{link.linkId && <button type="button" onClick={() => void unlinkPayment(link.linkId!)} disabled={busy} className="text-[10px] text-rose-500 hover:underline disabled:opacity-40">解除</button>}</div><div className="text-[10px] text-slate-400">{link.accountNo || link.accountName || "未记录账户"}{link.voucherNo ? ` · ${link.voucherNo}` : ""}</div></div>)}</div> : <span className="text-xs text-slate-300">尚未关联银行付款</span>}
                    </td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded-full px-2 py-1 text-[10px] font-medium ${row.bankMatchStatus === "matched" ? "bg-emerald-50 text-emerald-700" : row.bankMatchStatus === "partial" ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-500"}`}>{bankMatchStatusLabel(row.bankMatchStatus)}</span></td>
                    <td className="px-4 py-3 text-right"><button type="button" onClick={() => { setPickerQuery(""); setPickerInvoice(row); }} disabled={busy || Number(row.remaining) <= 0.01} className="rounded-md border border-blue-200 px-2.5 py-1.5 text-[11px] font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-40">{row.bankMatchStatus === "matched" ? "银行核对完成" : "选择银行流水"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!matchData && !matchLoading && <div className="px-4 py-12 text-center text-sm text-slate-400">请先选择账期；加载失败时可点「刷新」重试。</div>}
      </section>}

      {showUnbilledDetail && (
        <div
          className="fixed inset-0 z-modal flex items-end justify-center bg-slate-950/35 p-3 sm:p-6"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeUnbilledDetail();
          }}
          role="dialog"
          aria-modal="true"
          aria-label={unbilledDetailMode === "adjust" ? "无票收入明细调整" : "无票收入发送预览"}
        >
          <div className="w-full max-w-[1180px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 sm:px-5">
              <div className="flex min-w-0 items-center gap-3">
                <DataIcon kind="sales" />
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-slate-900">
                    {sel?.month}月-销售出库-无票收入{" "}
                    <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                      {unbilled?.version || salesFile?.version ? "v" + (unbilled?.version || salesFile?.version) : "预览"}
                    </span>
                  </h2>
                  <p className="mt-1 text-xs text-slate-400">
                    {unbilledDetailMode === "adjust"
                      ? "勾选要纳入本版本的明细；这里始终显示完整原始明细，之前取消的行也可以重新勾选。"
                      : `发送前只读预览 · ${unbilled?.adjusted ? `当前保存版本 v${unbilled.version || "—"}，显示 ${unbilled.selectedCount || 0}/${unbilled.sourceCount || 0} 条明细` : "尚未人工调整，按当前全部明细发送"}。`}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={closeUnbilledDetail}
                aria-label="关闭明细弹窗"
                className="rounded-lg p-2 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              >
                ×
              </button>
            </div>

            <div className="grid max-h-[72vh] min-h-[360px] md:grid-cols-[230px_minmax(0,1fr)]">
              <div className="border-b border-slate-100 bg-slate-50/60 p-4 md:border-b-0 md:border-r">
                <h3 className="text-sm font-semibold text-slate-800">筛选条件</h3>
                <label className="mt-4 block text-xs text-slate-500">
                  账期
                  <input
                    type="month"
                    value={sendMonth}
                    onChange={(e) => setSendMonth(e.target.value)}
                    className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs"
                  />
                </label>
                <label className="mt-3 block text-xs text-slate-500">
                  时间口径
                  <select
                    className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs"
                    defaultValue="month"
                  >
                    <option value="month">月度时间</option>
                  </select>
                </label>
                <label className="mt-3 block text-xs text-slate-500">
                  搜索订单 / 税务编号 / 产品
                  <input
                    value={unbilledSearch}
                    onChange={(e) => setUnbilledSearch(e.target.value)}
                    placeholder="搜索明细"
                    className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs"
                  />
                </label>
                <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50/60 p-3 text-[11px] leading-4 text-blue-800">
                  {unbilledDetailMode === "adjust"
                    ? "无票收入 = 调整后销售金额 − 已开票净额；销项红字调整同时冲减销售基数和已开票净额。当前明细按税务编号 + 产品聚合；取消勾选的明细不会进入本次版本。"
                    : "当前为只读发送预览，只展示这个版本实际会进入附件的明细。"} 
                </div>
              </div>

              <div className="min-w-0 overflow-auto p-4">
                <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  <div className="rounded-lg bg-slate-50 p-2.5">
                    <div className="text-[10px] text-slate-400">销售总金额</div>
                    <div className="mt-1 text-sm font-semibold">{money(unbilled?.salesAmount)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2.5">
                    <div className="text-[10px] text-slate-400">销项红字调整</div>
                    <div className="mt-1 text-sm font-semibold text-rose-700">{money(unbilled?.redSalesAdjustmentAmount || 0)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2.5">
                    <div className="text-[10px] text-slate-400">调整后销售金额</div>
                    <div className="mt-1 text-sm font-semibold">{money(unbilled?.adjustedSalesAmount || unbilled?.salesAmount)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2.5">
                    <div className="text-[10px] text-slate-400">已开票净额</div>
                    <div className="mt-1 text-sm font-semibold">{money(unbilled?.invoicedAmount)}</div>
                  </div>
                  <div className="rounded-lg bg-amber-50 p-2.5">
                    <div className="text-[10px] text-amber-600">无票收入</div>
                    <div className="mt-1 text-sm font-semibold text-amber-800">{money(unbilled?.unbilledAmount)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2.5">
                    <div className="text-[10px] text-slate-400">已选择</div>
                    <div className="mt-1 text-sm font-semibold">{unbilledSelectedKeys.length} 条</div>
                  </div>
                </div>

                {!unbilledViewDetails.length ? (
                  <div className="flex h-48 items-center justify-center text-sm text-slate-400">该账期暂无销售明细</div>
                ) : (
                  <table className="w-full min-w-[820px] border-collapse text-xs">
                    <thead className="bg-slate-50 text-left text-slate-500">
                      <tr>
                        {unbilledDetailMode === "adjust" && <th className="w-10 border-b border-slate-200 px-3 py-2 font-medium">
                          <input
                            type="checkbox"
                            checked={allVisibleSelected}
                            onChange={toggleVisibleUnbilled}
                            aria-label="全选当前筛选明细"
                            className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                          />
                        </th>}
                        <th className="border-b border-slate-200 px-3 py-2 font-medium">月度时间</th>
                        <th className="border-b border-slate-200 px-3 py-2 font-medium">税务编号</th>
                        <th className="border-b border-slate-200 px-3 py-2 font-medium">税收分类名称</th>
                        <th className="border-b border-slate-200 px-3 py-2 font-medium">产品</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">发货数量</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">销售金额</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">销项红字调整</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">调整后销售金额</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">已开票金额</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">无票收入</th>
                        <th className="border-b border-slate-200 px-3 py-2 text-right font-medium">销售成本</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {filteredUnbilledDetails.map((detail, index) => {
                        const key = unbilledDetailKey(detail);
                        const rowInvoiced = invoicedValue(detail);
                        const rowUnbilled = unbilledValue(detail);
                        return (
                          <tr key={key || index} className={unbilledDetailMode === "adjust" && !unbilledSelectedKeys.includes(key) ? "bg-slate-50/70 text-slate-400" : ""}>
                            {unbilledDetailMode === "adjust" && <td className="px-3 py-2">
                              <input
                                type="checkbox"
                                checked={unbilledSelectedKeys.includes(key)}
                                onChange={() => toggleUnbilledKey(key)}
                                aria-label={"选择 " + detail.product}
                                className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                              />
                            </td>}
                            <td className="px-3 py-2 text-slate-600">{detail.period}</td>
                            <td className="px-3 py-2 font-mono text-[11px] text-slate-500">{detail.taxCode || "—"}</td>
                            <td className="px-3 py-2">{detail.taxName || "—"}</td>
                            <td className="max-w-[260px] truncate px-3 py-2">{detail.product}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{Number(detail.quantity).toLocaleString("zh-CN")}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{num(detail.sales)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-rose-700">{num(detail.redSalesAdjustment || 0)}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{num(detail.adjustedSales ?? detail.sales)}</td>
                            <td className="px-3 py-2 text-right tabular-nums">{rowInvoiced === null ? "—" : num(rowInvoiced)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-amber-700">{rowUnbilled === null ? "—" : num(rowUnbilled)}</td>
                            <td className="px-3 py-2 text-right tabular-nums text-amber-700">{num(detail.cost)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                    <tfoot className="bg-slate-50 font-semibold text-slate-900">
                      <tr>
                        <td colSpan={unbilledDetailMode === "adjust" ? 5 : 4} className="px-3 py-2">合计</td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.quantity || 0), 0).toLocaleString("zh-CN")}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.sales || 0), 0))}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-rose-700">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.redSalesAdjustment || 0), 0))}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.adjustedSales ?? detail.sales ?? 0), 0))}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => {
                            const v = invoicedValue(detail);
                            return sum + (v === null ? 0 : v);
                          }, 0))}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-amber-700">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => {
                            const v = unbilledValue(detail);
                            return sum + (v === null ? 0 : v);
                          }, 0))}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-amber-700">
                          {num(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.cost || 0), 0))}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 bg-slate-50/70 px-4 py-3">
              <div className="mr-auto text-xs text-slate-500">
                {unbilledDetailMode === "adjust"
                  ? <>已选择 {unbilledSelectedKeys.length} / {unbilled?.sourceCount ?? unbilledSourceDetails.length} 条 · 当前筛选 {filteredUnbilledDetails.length} 条 · 当前筛选合计{" "}
                    {money(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.sales || 0), 0))}</>
                  : <>当前版本明细 {unbilled?.selectedCount ?? unbilled?.details.length ?? 0} / {unbilled?.sourceCount ?? unbilled?.details.length ?? 0} 条 · 预览合计{" "}
                    {money(filteredUnbilledDetails.reduce((sum, detail) => sum + Number(detail.sales || 0), 0))}</>}
                {Number(unbilled?.unattributedInvoiced || 0) > 0 ? (
                  <span className="ml-2 text-amber-700">
                    已开票净额 {money(unbilled?.invoicedAmount)} 中有 {money(unbilled?.unattributedInvoiced)} 未关联到具体销售订单，仅计入总额、不摊入明细行
                  </span>
                ) : null}
              </div>
              {unbilledDetailMode === "adjust" ? <>
                <button type="button" onClick={closeUnbilledDetail} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">取消</button>
                <button type="button" onClick={() => void saveUnbilledAdjustment()} disabled={busy || !unbilled || unbilledSelectedKeys.length === 0} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">保存并生成新版</button>
              </> : <>
                <button type="button" onClick={closeUnbilledDetail} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">关闭</button>
                <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${includeSel.includes("无票收入") ? "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200" : "bg-amber-50 text-amber-700 ring-1 ring-amber-200"}`}>{includeSel.includes("无票收入") ? "已纳入本次发送" : "未纳入本次发送"}</span>
                <button type="button" onClick={packageAndSend} disabled={busy || !sel || !sendReady || !includeSel.includes("无票收入")} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "发送中…" : "确认并发送给财务"}</button>
              </>}
            </div>
          </div>
        </div>
      )}

      {showCorporateDetail && (
        <div
          className="fixed inset-0 z-modal flex items-end justify-center bg-slate-950/35 p-3 sm:p-6"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setShowCorporateDetail(false);
          }}
          role="dialog"
          aria-modal="true"
          aria-label="已收票对公付款明细调整"
        >
          <div className="flex max-h-[80vh] w-full max-w-[1500px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-4 py-3 sm:px-5">
              <div className="flex min-w-0 items-center gap-3">
                <DataIcon kind="purchase_inbound" />
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-slate-900">
                    {sel?.month}月-已收票对公付款明细{" "}
                    <span className="ml-1 rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                      {corporatePayment?.version ? "v" + corporatePayment.version : "调整"}
                    </span>
                  </h2>
                  <p className="mt-1 text-xs text-slate-400">勾选要纳入本版本的发票；保存后生成新版财务资料，历史版本保留。</p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button type="button" onClick={loadCorporatePayment} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50">刷新</button>
                <button type="button" onClick={() => { setShowCorporateDetail(false); setFinanceTab("match"); void loadMatch(); }} className="rounded-lg border border-blue-200 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50">核对银行付款</button>
                <button type="button" onClick={() => setShowCorporateDetail(false)} aria-label="关闭对公付款明细弹窗" className="rounded-lg p-2 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {corporateAdjustBody(false, () => { setShowCorporateDetail(false); setFinanceTab("match"); void loadMatch(); }, { selected: corporateSelectedKeys, onToggle: toggleCorporateKey, onToggleAll: toggleAllCorporate })}
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-200 bg-slate-50/70 px-4 py-3">
              <div className="mr-auto text-xs text-slate-500">已选择 {corporateSelectedKeys.length} / {corporatePayment?.sourceCount ?? corporateInvoices.length} 张发票</div>
              <button type="button" onClick={() => setShowCorporateDetail(false)} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">取消</button>
              <button type="button" onClick={() => void saveCorporateAdjustment()} disabled={busy || !corporatePayment || corporateSelectedKeys.length === 0} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">保存并生成新版</button>
            </div>
          </div>
        </div>
      )}

      {pickerInvoice && (() => {
        const query = pickerQuery.trim().toLowerCase();
        const candidates = (matchData?.payments || [])
          .filter((row) => Number(row.remaining) > 0.01)
          .filter((row) => !query || [row.txnDate, row.counterpartyName, row.voucherNo, row.accountNo, row.accountName].join(" ").toLowerCase().includes(query))
          .sort((a, b) =>
            Number(pickerInvoice.suggestedPaymentIds.includes(b.id)) - Number(pickerInvoice.suggestedPaymentIds.includes(a.id))
            || a.txnDate.localeCompare(b.txnDate)
          );
        return (
          <div
            className="fixed inset-0 z-modal flex items-end justify-center bg-slate-950/35 p-3 sm:p-6"
            onMouseDown={(event) => { if (event.target === event.currentTarget) setPickerInvoice(null); }}
            role="dialog"
            aria-modal="true"
            aria-label="选择银行流水核对发票"
          >
            <div className="flex max-h-[80vh] w-full max-w-[920px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
              <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-slate-900">选择银行流水 · 核对发票</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    发票 {pickerInvoice.invoiceNumber || "未记录号码"} · {pickerInvoice.sellerName || "销方未名"} · 价税合计 {money(pickerInvoice.totalAmount)} · 待核对 {money(pickerInvoice.remaining)}
                  </p>
                </div>
                <button type="button" onClick={() => setPickerInvoice(null)} aria-label="关闭银行流水选择" className="rounded-lg p-2 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
              </div>
              <div className="border-b border-slate-100 bg-slate-50/60 px-5 py-3">
                <input value={pickerQuery} onChange={(e) => setPickerQuery(e.target.value)} placeholder="搜索付款日期 / 对方户名 / 账号 / 凭证号" className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400" />
              </div>
              <div className="min-h-[220px] flex-1 overflow-y-auto">
                {!candidates.length && <div className="px-5 py-10 text-center text-sm text-slate-400">当前账期没有可用于核对的银行支出流水。</div>}
                <div className="divide-y divide-slate-100">
                  {candidates.map((row) => {
                    const suggested = pickerInvoice.suggestedPaymentIds.includes(row.id);
                    return <div key={row.id} className={`grid gap-3 px-5 py-3 sm:grid-cols-[110px_minmax(0,1fr)_130px_110px] sm:items-center ${suggested ? "bg-blue-50/50" : ""}`}>
                      <div className="text-xs text-slate-600">{row.txnDate}</div>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 text-sm"><span className="truncate font-medium text-slate-800">{row.counterpartyName || "对方未名"}</span>{suggested && <span className="rounded bg-blue-600 px-1.5 py-0.5 text-[10px] font-medium text-white">同名同金额建议</span>}</div>
                        <div className="mt-0.5 truncate text-[11px] text-slate-400">{row.accountNo || row.accountName || "未记录账户"}{row.voucherNo ? ` · ${row.voucherNo}` : ""}{row.summary ? ` · ${row.summary}` : ""}</div>
                      </div>
                      <div className="text-right"><div className="text-sm font-semibold text-slate-800">{money(row.amount)}</div><div className="text-[10px] text-slate-400">可用 {money(row.remaining)}</div></div>
                      <div className="text-right"><button type="button" onClick={() => void matchInvoiceToPayment(pickerInvoice, row)} disabled={busy} className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-40">核对这笔</button></div>
                    </div>;
                  })}
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {pickerTxn && (() => {
        const query = pickerQuery.trim().toLowerCase();
        const monthRows: PickerInvoice[] = (matchData?.invoicePool || [])
          .filter((row) => Number(row.remaining) > 0.01 || row.suggested)
          .map((row) => ({ id: row.id, invoiceNumber: row.invoiceNumber, sellerName: row.sellerName, issueDate: row.issueDate, totalAmount: row.totalAmount, remaining: row.remaining, suggested: row.suggested }));
        const sourceRows = pickerScope === "month" ? monthRows : allInvoices;
        const rows = sourceRows
          .filter((row) => !query || row.invoiceNumber.toLowerCase().includes(query) || row.sellerName.toLowerCase().includes(query))
          .sort((a, b) => Number(b.suggested) - Number(a.suggested) || Number(b.remaining) - Number(a.remaining));
        return (
          <div
            className="fixed inset-0 z-modal flex items-end justify-center bg-slate-950/35 p-3 sm:p-6"
            onMouseDown={(event) => { if (event.target === event.currentTarget) setPickerTxn(null); }}
            role="dialog"
            aria-modal="true"
            aria-label="选择发票标记已开票"
          >
            <div className="flex max-h-[80vh] w-full max-w-[860px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
              <div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4">
                <div className="min-w-0">
                  <h2 className="text-base font-semibold text-slate-900">选择发票 · 标记已开票</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    付款 {pickerTxn.txnDate} · {pickerTxn.counterpartyName || "对方未名"} · {money(pickerTxn.amount)} · 剩余 {money(pickerTxn.remaining)}；分配金额按 min(发票金额, 付款剩余)。
                  </p>
                </div>
                <button type="button" onClick={() => setPickerTxn(null)} aria-label="关闭发票选择" className="rounded-lg p-2 text-xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
              </div>
              <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-slate-50/60 px-5 py-3">
                <div className="flex overflow-hidden rounded-lg border border-slate-200 text-xs">
                  <button type="button" onClick={() => setPickerScope("month")} className={`px-3 py-1.5 transition ${pickerScope === "month" ? "bg-blue-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>当月发票</button>
                  <button type="button" onClick={() => setPickerScope("all")} className={`px-3 py-1.5 transition ${pickerScope === "all" ? "bg-blue-600 text-white" : "bg-white text-slate-500 hover:text-slate-800"}`}>全部未配发票</button>
                </div>
                <input value={pickerQuery} onChange={(e) => setPickerQuery(e.target.value)} placeholder="搜索发票号 / 销方名称" className="min-w-[200px] flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-blue-400" />
              </div>
              <div className="min-h-[200px] flex-1 overflow-y-auto">
                {!rows.length && <div className="px-5 py-10 text-center text-sm text-slate-400">{pickerScope === "month" ? "当月没有可用的进项发票；可切「全部未配发票」搜索。" : "没有匹配的未配发票"}</div>}
                <div className="divide-y divide-slate-100">
                  {rows.map((row) => {
                    const suggestedSame = pickerScope === "month" && row.suggested;
                    return (
                      <div key={row.id} className={`flex flex-wrap items-center gap-3 px-5 py-3 ${suggestedSame ? "bg-blue-50/50" : ""}`}>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2 text-sm">
                            <span className="font-mono text-slate-700">{row.invoiceNumber}</span>
                            {suggestedSame && <span className="rounded bg-blue-600 px-1.5 py-0.5 text-[10px] font-medium text-white">同名同金额</span>}
                            <span className={`rounded px-1.5 py-0.5 text-[10px] ${Number(row.remaining) > 0.01 ? "bg-slate-100 text-slate-500" : "bg-slate-100 text-slate-400"}`}>可分摊 {money(row.remaining)}</span>
                          </div>
                          <div className="mt-0.5 text-xs text-slate-400">{row.sellerName || "销方未名"} · {row.issueDate || "开票日期未知"} · 价税合计 {money(row.totalAmount)}</div>
                        </div>
                        <button type="button" onClick={() => void markInvoiced(pickerTxn, row)} disabled={busy || Number(row.remaining) <= 0.01} className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40">选用</button>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {mailOpen && <div className="fixed inset-0 z-modal flex items-start justify-center overflow-y-auto bg-slate-950/40 p-4 sm:p-8" onMouseDown={(event) => { if (event.target === event.currentTarget) closeSendSettings(); }} role="dialog" aria-modal="true" aria-label="发送设置"><div className="my-auto w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"><div className="flex items-center justify-between gap-3 border-b border-slate-200 px-5 py-4"><div><h2 className="text-base font-semibold text-slate-900">发送设置</h2><p className="mt-0.5 text-[11px] text-slate-500">收件人、抄送和自动发送时间统一在这里维护，手动发送与自动发送共用。</p></div><button type="button" onClick={() => closeSendSettings()} className="rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-600 hover:border-slate-300">关闭</button></div><div className="space-y-4 p-5"><div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3"><div className="text-[11px] font-medium text-slate-500">发件邮箱（服务器 .env 配置，不支持在此修改）</div><div className="mt-1.5 text-sm text-slate-800">{mailStatus?.from || "未配置"}</div><div className="mt-1 text-[11px] text-slate-400">{mailStatus?.configured ? `SMTP：${mailStatus.host}:${mailStatus.port} · 账号 ${mailStatus.username}` : "SMTP 未配置，发送会失败"}</div></div><label className="block text-xs text-slate-500">财务收件人<input value={toText} onChange={(e) => setToText(e.target.value)} placeholder="finance@example.com" className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" /></label><label className="block text-xs text-slate-500">抄送（可选）<input value={ccText} onChange={(e) => setCcText(e.target.value)} placeholder="多个邮箱用逗号分隔" className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" /></label><div className="rounded-xl border border-slate-200 p-4"><label className="flex items-center gap-2 text-sm font-medium text-slate-700"><input type="checkbox" checked={Boolean(template?.autoSend)} onChange={(e) => setTemplate((t) => t ? { ...t, autoSend: e.target.checked } : t)} className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />启用每月自动发送</label><div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500"><span>每月</span><input type="number" min={1} max={28} value={template?.sendDay ?? 3} onChange={(e) => setTemplate((t) => t ? { ...t, sendDay: Number(e.target.value) } : t)} disabled={!template?.autoSend} className="w-16 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-50 disabled:text-slate-300" /><span>日</span><select value={template?.sendHour ?? 10} onChange={(e) => setTemplate((t) => t ? { ...t, sendHour: Number(e.target.value) } : t)} disabled={!template?.autoSend} className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs disabled:bg-slate-50 disabled:text-slate-300">{Array.from({ length: 24 }, (_, i) => <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>)}</select></div></div></div><div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50/60 px-5 py-3"><button type="button" onClick={() => closeSendSettings()} className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-600">取消</button><button type="button" onClick={() => { void saveTemplate("send").then((saved) => { if (saved) closeSendSettings(false); }); }} disabled={busy || !template} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40">保存发送设置</button></div></div></div>}
    </div>
  );
}
