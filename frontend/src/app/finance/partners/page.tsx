"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import Link from "@/components/workspace/workspace-link";
import {
  authenticatedFetch,
  businessPartnerApi,
  downloadAuthenticatedFile,
  type BusinessPartnerDetail,
  type BusinessPartnerBankAccount,
  type BusinessPartnerInput,
  type BusinessPartnerListItem,
  type BusinessPartnerRole,
  type PartnerReferenceCoverage,
} from "@/lib/api";
import { useTabScopedState, useTabTitle } from "@/lib/workspace/tab-store";

type RoleFilter = "all" | BusinessPartnerRole;
type DetailTab = "overview" | "purchases" | "inbounds" | "invoices" | "payments" | "sales" | "review" | "profile";

type BankRawDetail = {
  id: number;
  txnDate: string;
  transactionTime: string | null;
  accountNo: string;
  accountCode?: string;
  serialNo: string;
  voucherNo: string;
  sourceRowNumber: number | null;
  raw: { fields?: Record<string, unknown>; rowNumber?: number; sourceHistory?: unknown[]; [key: string]: unknown };
  sourceFile: { fileName: string; sha256: string; downloadUrl?: string } | null;
};

const roleLabels: Record<BusinessPartnerRole, string> = {
  supplier: "供应商",
  customer: "客户",
  counterparty: "其他往来",
};

const emptyForm: BusinessPartnerInput = {
  name: "",
  roles: ["counterparty"],
  taxNo: "",
  contact: "",
  phone: "",
  address: "",
  bankName: "",
  bankAccountNo: "",
  bankAccountName: "",
  formerNames: [],
  bankAccounts: [{ bankName: "", accountNo: "", accountName: "", isPrimary: true }],
  notes: "",
};

function money(value: number | null | undefined) {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount)
    ? `¥${amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : "—";
}

function dateText(value: string | null | undefined) {
  return value ? value.slice(0, 10) : "—";
}

function invoicePaymentStatus(row: BusinessPartnerDetail["invoices"][number]) {
  const effectiveAmount = Math.max(0, Number(row.effectiveAmount ?? row.amount ?? 0));
  const paidAmount = Math.max(0, Number(row.bankPaidAmount ?? 0));
  const remainingAmount = Math.max(0, Number(row.bankRemainingAmount ?? 0));

  if (row.invoiceColor === "red") {
    return paidAmount > 0.005
      ? {
          label: "红冲资金已关联",
          className: "bg-emerald-50 px-2 py-1 font-medium text-emerald-700",
          title: `红字发票不参与原蓝字付款核对，已关联资金 ${money(paidAmount)}`,
        }
      : {
          label: "不适用",
          className: "bg-slate-100 px-2 py-1 font-medium text-slate-500",
          title: "红字发票通过红蓝关系冲销，不作为原蓝字付款金额单独核对",
        };
  }

  if (row.redStatus === "fully_red_offset" && effectiveAmount <= 0.005) {
    return paidAmount > 0.005
      ? {
          label: "红冲后超额待处理",
          className: "bg-rose-50 px-2 py-1 font-medium text-rose-700",
          title: `原蓝字发票已全额红冲，但历史已关联付款 ${money(paidAmount)}，需处理退款或后续抵扣`,
        }
      : {
          label: "不适用",
          className: "bg-slate-100 px-2 py-1 font-medium text-slate-500",
          title: "蓝字发票已全额红冲，红冲后没有待核对的有效应付金额",
        };
  }

  const settled = remainingAmount <= 0.005 && effectiveAmount > 0.005;
  const partial = paidAmount > 0.005 && !settled;
  return {
    label: settled ? "已匹配" : partial ? "部分匹配" : "待匹配",
    className: settled
      ? "bg-emerald-50 px-2 py-1 font-medium text-emerald-700"
      : partial
        ? "bg-blue-50 px-2 py-1 font-medium text-blue-700"
        : "bg-amber-50 px-2 py-1 font-medium text-amber-700",
    title: settled || partial ? `已关联 ${money(paidAmount)} / 有效金额 ${money(effectiveAmount)}` : undefined,
  };
}

function rawValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

function RoleBadges({ roles }: { roles: BusinessPartnerRole[] }) {
  if (!roles.length) return <span className="text-xs text-slate-400">未分类</span>;
  return <span className="flex flex-wrap gap-1">{roles.map((role) => (
    <span key={role} className={role === "supplier" ? "rounded-full bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-700" : role === "customer" ? "rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700" : "rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600"}>
      {roleLabels[role]}
    </span>
  ))}</span>;
}

function Metric({ label, value, hint, tone = "slate" }: { label: string; value: string; hint?: string; tone?: "slate" | "blue" | "amber" | "emerald" }) {
  const tones = {
    slate: "border-slate-200 bg-white",
    blue: "border-blue-100 bg-blue-50/50",
    amber: "border-amber-100 bg-amber-50/50",
    emerald: "border-emerald-100 bg-emerald-50/50",
  };
  return <div className={`rounded-xl border px-3 py-2.5 ${tones[tone]}`}>
    <div className="text-[11px] text-slate-500">{label}</div>
    <div className="mt-1 text-base font-semibold tabular-nums text-slate-900">{value}</div>
    {hint && <div className="mt-1 text-[10px] leading-4 text-slate-400">{hint}</div>}
  </div>;
}

function partnerToForm(detail: BusinessPartnerDetail): BusinessPartnerInput {
  return {
    name: detail.name,
    roles: detail.roles.length ? detail.roles : ["counterparty"],
    taxNo: detail.taxNo,
    contact: detail.contact,
    phone: detail.phone,
    address: detail.address,
    bankName: detail.bankName,
    bankAccountNo: detail.bankAccountNo,
    bankAccountName: detail.bankAccountName,
    formerNames: [...detail.formerNames],
    bankAccounts: detail.bankAccounts.length
      ? detail.bankAccounts.map((row) => ({ ...row }))
      : [{ bankName: detail.bankName, accountNo: detail.bankAccountNo, accountName: detail.bankAccountName, isPrimary: true }],
    notes: detail.notes,
  };
}

export default function BusinessPartnersPage() {
  const [items, setItems] = useState<BusinessPartnerListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [keywordInput, setKeywordInput] = useTabScopedState("finance.partners.keywordInput", "");
  const [keyword, setKeyword] = useTabScopedState("finance.partners.keyword", "");
  const [role, setRole] = useTabScopedState<RoleFilter>("finance.partners.role", "all");
  const [selectedId, setSelectedId] = useTabScopedState<number | null>("finance.partners.selectedId", null);
  const [tab, setTab] = useTabScopedState<DetailTab>("finance.partners.tab", "overview");
  const [detail, setDetail] = useState<BusinessPartnerDetail | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [form, setForm] = useState<BusinessPartnerInput | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [alias, setAlias] = useState("");
  const [rawDetail, setRawDetail] = useState<BankRawDetail | null>(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [duplicatePrompt, setDuplicatePrompt] = useState<{ mine: BusinessPartnerDetail; other: BusinessPartnerDetail } | null>(null);
  const [savingDuplicate, setSavingDuplicate] = useState(false);
  const [coverage, setCoverage] = useState<PartnerReferenceCoverage | null>(null);
  // 「稍后处理」只关弹窗，本次会话内对同一组合不再弹。
  const [deferredDuplicates, setDeferredDuplicates] = useState<string[]>([]);
  const initialSelection = useRef(false);
  const searchParams = useSearchParams();
  const entryApplied = useRef(false);

  // 供应商档案等入口带 role / keyword / partnerId 跳进来时，直接定位到对应档案。
  useEffect(() => {
    if (entryApplied.current) return;
    entryApplied.current = true;
    const roleParam = searchParams.get("role");
    if (roleParam === "supplier" || roleParam === "customer" || roleParam === "counterparty") {
      setRole(roleParam);
    }
    const keywordParam = searchParams.get("keyword");
    if (keywordParam) {
      setKeywordInput(keywordParam);
      setKeyword(keywordParam);
    }
    const partnerParam = Number(searchParams.get("partnerId") || "");
    if (Number.isInteger(partnerParam) && partnerParam > 0) {
      initialSelection.current = true;
      setSelectedId(partnerParam);
    }
  }, [searchParams, setKeyword, setKeywordInput, setRole, setSelectedId]);

  const loadList = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await businessPartnerApi.list(keyword.trim(), role);
      setItems(response.items);
      try {
        setCoverage(await businessPartnerApi.coverage());
      } catch {
        // 覆盖率是运维提示；读取失败不阻塞主档使用。
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载往来单位失败");
    } finally {
      setLoading(false);
    }
  }, [keyword, role]);

  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => {
    const timer = window.setTimeout(() => setKeyword(keywordInput), 280);
    return () => window.clearTimeout(timer);
  }, [keywordInput, setKeyword]);

  useEffect(() => {
    if (initialSelection.current && selectedId != null && items.some((row) => row.id === selectedId)) return;
    initialSelection.current = true;
    setSelectedId(items[0]?.id ?? null);
  }, [items, selectedId, setSelectedId]);

  const loadDetail = useCallback(async (id: number | null) => {
    setDetail(null);
    if (id == null) return;
    setDetailLoading(true);
    try {
      setDetail(await businessPartnerApi.detail(id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "加载往来明细失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadDetail(selectedId); }, [loadDetail, selectedId]);
  useTabTitle(detail ? `往来单位 · ${detail.name}` : "往来单位档案");

  // 详情里还有未确认的「疑似同一主体」时弹窗，请人工判断；已判断过的组合后端不会再返回。
  useEffect(() => {
    const pending = detail?.possibleDuplicates.find(
      (item) => !deferredDuplicates.includes(`${detail.id}-${item.id}`),
    );
    if (!detail || !pending) {
      setDuplicatePrompt(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const other = await businessPartnerApi.detail(pending.id);
        if (!cancelled) setDuplicatePrompt({ mine: detail, other });
      } catch {
        if (!cancelled) setDuplicatePrompt(null);
      }
    })();
    return () => { cancelled = true; };
  }, [detail, deferredDuplicates]);

  const totalReview = useMemo(
    () => items.reduce((total, item) => total + item.summary.needsReviewCount, 0),
    [items],
  );

  async function syncAll() {
    setSyncing(true);
    setMessage("");
    setError("");
    try {
      const result = await businessPartnerApi.sync();
      setMessage(
        `已核对全部来源：新增 ${result.createdPartners} 个档案，新增 ${result.createdLinks} 条来源关联；` +
        `银行↔发票自动匹配 ${result.bankInvoiceMatchesCreated} 条` +
        (result.bankInvoiceRepaired ? `，纠正历史错配 ${result.bankInvoiceRepaired} 条` : "") +
        (result.bankInvoiceAmbiguous ? `，另有 ${result.bankInvoiceAmbiguous} 组歧义待人工确认` : "") +
        `；往来来源待确认 ${result.needsReview} 条。`,
      );
      await loadList();
      await loadDetail(selectedId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }

  async function recheckSelected() {
    if (!detail) return;
    setRechecking(true);
    setMessage("");
    setError("");
    try {
      const result = await businessPartnerApi.recheck(detail.id);
      setDetail(result.detail);
      const delta = result.partnerInvoicePaidAfter - result.partnerInvoicePaidBefore;
      setMessage(
        `已重新核对「${result.detail.name}」：本档案新增匹配关系 ${result.partnerMatchesAdded} 条` +
        (Math.abs(delta) > 0.005 ? `，发票银行关联金额变化 ${money(delta)}` : "") +
        (result.bankInvoiceRepaired ? `；全局匹配器纠正历史自动错配 ${result.bankInvoiceRepaired} 条` : "") +
        (result.bankInvoiceAmbiguous ? `；${result.bankInvoiceAmbiguous} 组歧义保留人工确认` : "") +
        `；当前待确认 ${result.needsReview} 条。`,
      );
      await loadList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "重新核对失败");
    } finally {
      setRechecking(false);
    }
  }

  async function saveForm() {
    if (!form) return;
    setSaving(true);
    setError("");
    try {
      const next = editingId == null
        ? await businessPartnerApi.create(form)
        : await businessPartnerApi.update(editingId, form);
      setForm(null);
      setEditingId(null);
      setSelectedId(next.id);
      setDetail(next);
      setMessage(editingId == null ? `已新建往来单位「${next.name}」。` : `已保存「${next.name}」，历史来源已重新核对。`);
      await loadList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function addAlias() {
    if (!detail || !alias.trim()) return;
    setSaving(true);
    try {
      const next = await businessPartnerApi.addIdentifier(detail.id, "alias", alias.trim());
      setDetail(next);
      setAlias("");
      setMessage("已补充别名并重新核对历史来源。匹配到的记录已归入当前档案；仍有歧义的保留待确认。");
      await loadList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存别名失败");
    } finally {
      setSaving(false);
    }
  }

  async function claimReview(linkId: number, rawName: string) {
    if (!detail) return;
    if (!window.confirm(`确认将「${rawName || "该来源记录"}」归入「${detail.name}」？\n系统会保留原始名称，并把它作为已确认别名。`)) return;
    setSaving(true);
    try {
      const next = await businessPartnerApi.claimReview(detail.id, linkId, "财务中心人工确认往来单位归属");
      setDetail(next);
      setMessage("已人工确认归属，并保留来源名称作为别名和审计记录。");
      await loadList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "确认关联失败");
    } finally {
      setSaving(false);
    }
  }

  async function decideDuplicate(same: boolean) {
    if (!detail || !duplicatePrompt) return;
    const other = duplicatePrompt.other;
    setSavingDuplicate(true);
    setError("");
    try {
      const next = await businessPartnerApi.decideDuplicate(detail.id, other.id, {
        same,
        note: same ? "财务中心人工确认同一主体，合并到当前 canonical 主档" : "财务中心人工确认不是同一主体",
      });
      setDetail(next);
      setDuplicatePrompt(null);
      setMessage(same ? `已合并主体「${other.name}」：采购、发票、银行及其他业务事实已统一归到「${next.name}」。` : `已记录「${detail.name}」与「${other.name}」不是同一主体。`);
      await loadList();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存判断失败");
    } finally {
      setSavingDuplicate(false);
    }
  }

  function deferDuplicate() {
    if (!detail || !duplicatePrompt) return;
    setDeferredDuplicates((prev) => [...prev, `${detail.id}-${duplicatePrompt.other.id}`]);
    setDuplicatePrompt(null);
  }

  async function openRaw(url: string) {
    setRawLoading(true);
    try {
      const response = await authenticatedFetch(url, { cache: "no-store" });
      const payload = (await response.json().catch(() => ({}))) as BankRawDetail & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || `读取原始流水失败（${response.status}）`);
      setRawDetail(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "读取原始流水失败");
    } finally {
      setRawLoading(false);
    }
  }

  async function downloadRaw(url: string, filename: string) {
    try {
      await downloadAuthenticatedFile(url, filename);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "下载原始文件失败");
    }
  }

  const tabs: Array<[DetailTab, string, number?]> = detail ? [
    ["overview", "往来概览"],
    ["purchases", `采购 ${detail.purchases.length}`],
    ["inbounds", `入库 ${detail.inbounds.length}`],
    ["invoices", `发票 ${detail.invoices.length}`],
    ["payments", `银行 ${detail.payments.length}`],
    ["sales", `销售 ${detail.sales.length}`],
    ["review", "待确认", detail.reviewItems.length],
    ["profile", "档案资料"],
  ] : [];

  return (
    <div className="mx-auto max-w-[1720px] space-y-3">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[10px] font-semibold tracking-wide text-blue-600">财务中心 / 往来单位</div>
            <div className="mt-0.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h1 className="text-xl font-semibold tracking-tight text-slate-900">往来单位档案</h1>
              <p className="text-xs text-slate-500">统一查看采购、入库、发票、银行与销售来源；原始记录不改写。</p>
              {coverage && <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px]">
                <span className={coverage.unlinkedFacts > 0 ? "rounded bg-amber-50 px-2 py-0.5 font-medium text-amber-700" : "rounded bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700"}>
                  主数据覆盖 {(coverage.coverage * 100).toFixed(1)}%
                </span>
                <span className="text-slate-400">已归档 {coverage.linkedFacts}/{coverage.totalFacts} 条业务事实</span>
                {coverage.unlinkedFacts > 0 && <span className="font-medium text-amber-600">未归档 {coverage.unlinkedFacts} 条</span>}
              </div>}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => void syncAll()} disabled={syncing || rechecking} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-60">{syncing ? "全量核对中…" : "全量核对"}</button>
            <button type="button" onClick={() => { setForm({ ...emptyForm }); setEditingId(null); }} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700">新增往来单位</button>
          </div>
        </div>
      </header>

      {(message || error) && <div className={`rounded-lg border px-3 py-2 text-xs ${error ? "border-red-200 bg-red-50 text-red-700" : "border-emerald-200 bg-emerald-50 text-emerald-700"}`}>{error || message}</div>}

      <div className="grid gap-3 xl:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-100 p-3">
            <div className="flex items-center justify-between gap-2"><div className="text-sm font-semibold text-slate-800">全部往来单位</div><span className={totalReview ? "rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700" : "rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-500"}>{totalReview ? `${totalReview} 条待确认` : `${items.length} 个档案`}</span></div>
            <input value={keywordInput} onChange={(event) => setKeywordInput(event.target.value)} placeholder="搜索名称 / 税号 / 账号 / 别名" className="mt-2.5 w-full rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-1.5 text-xs outline-none placeholder:text-slate-400 focus:border-blue-400 focus:bg-white" />
            <div className="mt-2 flex flex-wrap gap-1">
              {(["all", "supplier", "customer", "counterparty"] as RoleFilter[]).map((key) => <button key={key} type="button" onClick={() => setRole(key)} className={`rounded-md px-2 py-1 text-[11px] ${role === key ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>{key === "all" ? "全部" : roleLabels[key]}</button>)}
            </div>
          </div>
          <div className="max-h-[calc(100vh-214px)] overflow-y-auto p-1.5">
            {loading && <div className="px-3 py-8 text-center text-sm text-slate-400">正在建立来源关联…</div>}
            {!loading && items.length === 0 && <div className="px-3 py-8 text-center text-sm text-slate-400">暂无匹配的往来单位</div>}
            {items.map((item) => <button key={item.id} type="button" onClick={() => { setSelectedId(item.id); setTab("overview"); }} className={`mb-1 w-full rounded-lg border px-2.5 py-2 text-left transition ${selectedId === item.id ? "border-blue-200 bg-blue-50/80" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`}>
              <div className="flex gap-2"><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium text-slate-800">{item.name}</div><div className="mt-1"><RoleBadges roles={item.roles} /></div></div>{item.summary.needsReviewCount > 0 && <span className="mt-0.5 h-fit rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">待确认 {item.summary.needsReviewCount}</span>}{item.possibleDuplicateCount > 0 && <span className="mt-0.5 h-fit rounded-full bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-600">疑似重复 {item.possibleDuplicateCount}</span>}</div>
              <div className="mt-1.5 flex items-center gap-2 overflow-hidden text-[10px] tabular-nums text-slate-500"><span className="truncate">采购 {money(item.summary.purchaseAmount)}</span><span className="text-slate-300">·</span><span className="truncate">发票 {money(item.summary.invoiceAmount)}</span><span className="text-slate-300">·</span><span className="truncate">付款 {money(item.summary.bankPaidAmount)}</span></div>
            </button>)}
          </div>
        </aside>

        <main className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {!selectedId && <div className="flex min-h-[560px] flex-col items-center justify-center text-slate-400"><div className="text-base">选择一个往来单位</div><div className="mt-1 text-sm">查看它的采购、发票、银行支付和销售往来</div></div>}
          {selectedId && detailLoading && <div className="flex min-h-[560px] items-center justify-center text-sm text-slate-400">正在汇总往来数据…</div>}
          {detail && !detailLoading && <>
            <div className="border-b border-slate-100 px-4 py-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><h2 className="max-w-[720px] truncate text-lg font-semibold text-slate-900">{detail.name}</h2><RoleBadges roles={detail.roles} /></div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
                    <span>税号 <span className="font-mono text-slate-700">{detail.taxNo || "待补充"}</span></span>
                    <span className="text-slate-300">|</span>
                    <span>主账号 <span className="font-mono text-slate-700">{detail.bankAccountNo || "待补充"}</span></span>
                    {detail.formerNames.length > 0 && <><span className="text-slate-300">|</span><span>曾用名 {detail.formerNames.join("、")}</span></>}
                    {detail.legacySupplierId && <Link href="/suppliers" className="font-medium text-blue-600 hover:text-blue-700">查看供应商档案 →</Link>}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px]">
                    <span className={detail.taxNo ? "rounded bg-emerald-50 px-2 py-1 font-medium text-emerald-700" : "rounded bg-slate-100 px-2 py-1 text-slate-400"}>税号{detail.taxNo ? "已识别" : "待补"}</span>
                    <span className={detail.bankAccountNo ? "rounded bg-emerald-50 px-2 py-1 font-medium text-emerald-700" : "rounded bg-slate-100 px-2 py-1 text-slate-400"}>银行账号{detail.bankAccountNo ? "已识别" : "待补"}</span>
                    <span className={detail.identifiers.some((row) => row.kind === "alias" || row.kind === "former_name") ? "rounded bg-blue-50 px-2 py-1 font-medium text-blue-700" : "rounded bg-slate-100 px-2 py-1 text-slate-400"}>别名/曾用名 {detail.identifiers.filter((row) => row.kind === "alias" || row.kind === "former_name").length}</span>
                    {detail.summary.needsReviewCount > 0 && <button type="button" onClick={() => setTab("review")} className="rounded bg-amber-50 px-2 py-1 font-medium text-amber-700 hover:bg-amber-100">待确认 {detail.summary.needsReviewCount}</button>}
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <button type="button" onClick={() => void recheckSelected()} disabled={rechecking || syncing} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60">{rechecking ? "重新核对中…" : "重新核对匹配关系"}</button>
                  <button type="button" onClick={() => { setForm(partnerToForm(detail)); setEditingId(detail.id); }} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50">编辑档案</button>
                  <Link href="/finance/bank-transactions?view=transactions" className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">银行流水</Link>
                </div>
              </div>
              {detail.possibleDuplicates.length > 0 && (
                <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
                  <span className="font-medium">疑似同一主体：</span>
                  {detail.possibleDuplicates.map((item) => (
                    <Link key={item.id} href={`/finance/partners?partnerId=${item.id}`} className="mr-2 font-medium text-amber-800 underline hover:text-amber-900">
                      {item.name}{item.taxNo ? `（税号 ${item.taxNo}）` : "（无税号）"}
                    </Link>
                  ))}
                  <span className="text-amber-600">名称去括号与后缀后相同，按规则不自动合并，请人工判断。</span>
                </div>
              )}
              <div className="mt-3 flex gap-1 overflow-x-auto border-b border-slate-100 -mb-3"><div className="flex min-w-max gap-0.5">{tabs.map(([key, label, count]) => <button key={key} type="button" onClick={() => setTab(key)} className={`relative px-2.5 py-2 text-[11px] font-medium ${tab === key ? "text-blue-600" : "text-slate-500 hover:text-slate-700"}`}>{label}{count ? <span className="ml-1 rounded-full bg-amber-100 px-1.5 py-0.5 text-[9px] text-amber-700">{count}</span> : null}{tab === key && <span className="absolute inset-x-2 bottom-0 h-0.5 rounded bg-blue-600" />}</button>)}</div></div>
            </div>

            <div className="p-4">
              {tab === "overview" && <Overview detail={detail} onTab={setTab} />}
              {tab === "purchases" && <Purchases rows={detail.purchases} />}
              {tab === "inbounds" && <Inbounds rows={detail.inbounds} />}
              {tab === "invoices" && <Invoices rows={detail.invoices} />}
              {tab === "payments" && <Payments rows={detail.payments} rawLoading={rawLoading} onRaw={openRaw} />}
              {tab === "sales" && <Sales rows={detail.sales} />}
              {tab === "review" && <Review rows={detail.reviewItems} saving={saving} onClaim={claimReview} />}
              {tab === "profile" && <Profile detail={detail} alias={alias} setAlias={setAlias} saving={saving} onAddAlias={addAlias} />}
            </div>
          </>}
        </main>
      </div>

      {form && <PartnerForm form={form} setForm={setForm} editing={editingId != null} saving={saving} onClose={() => { setForm(null); setEditingId(null); }} onSave={() => void saveForm()} />}
      {duplicatePrompt && <DuplicateDialog mine={duplicatePrompt.mine} other={duplicatePrompt.other} saving={savingDuplicate} onDecide={(same) => void decideDuplicate(same)} onDefer={deferDuplicate} />}
      {rawDetail && <RawDialog detail={rawDetail} onClose={() => setRawDetail(null)} onDownload={(url, filename) => void downloadRaw(url, filename)} />}
    </div>
  );
}

function Overview({ detail, onTab }: { detail: BusinessPartnerDetail; onTab: (tab: DetailTab) => void }) {
  const s = detail.summary;
  return <div className="space-y-5">
    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
      <Metric label="采购实付" value={money(s.purchaseAmount)} hint={`${s.purchaseOrderCount} 张采购单`} tone="blue" />
      <Metric label="实际入库金额" value={money(s.inboundAmount)} hint={`${s.inboundCount} 张入库单`} />
      <Metric label="已收/关联发票" value={money(s.invoiceAmount)} hint={`${s.invoiceCount} 张发票`} tone="amber" />
      <Metric label="银行对公付款" value={money(s.bankPaidAmount)} hint={`${s.bankTransactionCount} 笔银行流水`} tone="emerald" />
      <Metric label="采购与付款差额" value={money(s.purchasePaymentDifference)} hint="用于核对，不替代会计应付余额" />
      <Metric label="发票与付款差额" value={money(s.invoicePaymentDifference)} hint="按当前已关联发票和付款计算" />
      <Metric label="销售实收" value={money(s.salesReceivedAmount)} hint={`${s.salesOrderCount} 张销售订单`} tone="emerald" />
      <Metric label="待人工确认" value={`${s.needsReviewCount} 条`} hint="名称相近、税号或账号证据不足时保留待确认" tone={s.needsReviewCount ? "amber" : "slate"} />
    </div>
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4"><div className="text-sm font-semibold text-slate-800">本档案的关联原则</div><p className="mt-1 text-xs leading-5 text-slate-600">优先按税号、银行账号、精确名称和已确认别名关联；相似名称不会自动合并。采购、发票、银行流水的原始记录仍保留在各自来源中，可随时回查。</p><div className="mt-3 flex flex-wrap gap-2"><button type="button" onClick={() => onTab("review")} className="rounded-lg border border-amber-200 bg-white px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-50">处理待确认记录</button><button type="button" onClick={() => onTab("profile")} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100">补充税号、账号或别名</button></div></div>
  </div>;
}

function Empty({ text }: { text: string }) { return <div className="rounded-xl border border-dashed border-slate-200 py-10 text-center text-sm text-slate-400">{text}</div>; }

function Purchases({ rows }: { rows: BusinessPartnerDetail["purchases"] }) {
  if (!rows.length) return <Empty text="暂无已关联采购记录" />;
  return <div className="overflow-x-auto rounded-xl border border-slate-200"><table className="min-w-[880px] w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2.5">采购单号</th><th className="px-3 py-2.5">渠道 / 摘要</th><th className="px-3 py-2.5">日期</th><th className="px-3 py-2.5 text-right">订单金额</th><th className="px-3 py-2.5 text-right">实付金额</th><th className="px-3 py-2.5">状态</th></tr></thead><tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={`${row.sourceType}-${row.id}`}><td className="px-3 py-2.5 font-mono text-slate-700">{row.no || "—"}</td><td className="max-w-[300px] px-3 py-2.5"><div className="text-slate-700">{row.platform || "—"}</div><div className="mt-0.5 truncate text-[10px] text-slate-400">{row.title || "—"}</div></td><td className="px-3 py-2.5 text-slate-600">{dateText(row.date)}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{money(row.amount)}</td><td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-800">{money(row.paidAmount)}</td><td className="px-3 py-2.5 text-slate-500">{row.status || "—"}</td></tr>)}</tbody></table></div>;
}

function Inbounds({ rows }: { rows: BusinessPartnerDetail["inbounds"] }) {
  if (!rows.length) return <Empty text="暂无已关联入库记录" />;
  return <div className="overflow-x-auto rounded-xl border border-slate-200"><table className="min-w-[760px] w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2.5">入库单号</th><th className="px-3 py-2.5">日期</th><th className="px-3 py-2.5">仓库</th><th className="px-3 py-2.5 text-right">入库数量</th><th className="px-3 py-2.5 text-right">入库金额</th></tr></thead><tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={`${row.sourceType}-${row.id}`}><td className="px-3 py-2.5 font-mono text-slate-700">{row.no || "—"}</td><td className="px-3 py-2.5 text-slate-600">{dateText(row.date)}</td><td className="px-3 py-2.5 text-slate-600">{row.warehouse || "—"}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{row.quantity.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}</td><td className="px-3 py-2.5 text-right tabular-nums font-medium text-slate-800">{money(row.amount)}</td></tr>)}</tbody></table></div>;
}

function Invoices({ rows }: { rows: BusinessPartnerDetail["invoices"] }) {
  if (!rows.length) return <Empty text="暂无已关联发票" />;
  return <div className="overflow-x-auto rounded-xl border border-slate-200">
    <table className="min-w-[980px] w-full text-left text-[11px]">
      <thead className="bg-slate-50 text-slate-500">
        <tr>
          <th className="px-3 py-2">日期 / 发票号</th>
          <th className="px-3 py-2">发票状态</th>
          <th className="px-3 py-2">开票双方</th>
          <th className="px-3 py-2 text-right">价税合计</th>
          <th className="px-3 py-2 text-right">红冲后有效额</th>
          <th className="px-3 py-2 text-right">银行已关联</th>
          <th className="px-3 py-2 text-right">待匹配</th>
          <th className="px-3 py-2">匹配状态</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {rows.map((row) => {
          const remaining = Math.max(0, Number(row.bankRemainingAmount || 0));
          const paymentStatus = invoicePaymentStatus(row);
          return <tr key={row.id} className="hover:bg-slate-50/60">
            <td className="px-3 py-2.5">
              <div className="text-slate-600">{dateText(row.date)}</div>
              <div className="mt-0.5 font-mono text-[10px] text-slate-500">{row.no || "—"}</div>
            </td>
            <td className="px-3 py-2.5">
              <span className={`rounded-md px-2 py-1 font-medium ${row.invoiceColor === "red" ? "bg-rose-50 text-rose-700" : row.redStatus === "fully_red_offset" ? "bg-orange-100 text-orange-800" : row.redStatus === "partially_red_offset" ? "bg-amber-50 text-amber-700" : row.redStatus === "blue_red_pending" || row.redStatus === "over_red_offset" ? "bg-red-50 text-red-700" : "bg-emerald-50 text-emerald-700"}`}>
                {row.invoiceStatusLabel || (row.invoiceColor === "red" ? "红字发票" : "蓝字发票")}
              </span>
              {row.redRelatedInvoiceNo && <div className="mt-1 max-w-[180px] truncate text-[10px] text-rose-600" title={row.redRelatedInvoiceNo}>对应 {row.redRelatedInvoiceNo}</div>}
              {row.accountingException && <div className="mt-1 max-w-[220px] text-[10px] text-rose-600" title={row.accountingException}>{row.accountingException}</div>}
            </td>
            <td className="max-w-[360px] px-3 py-2.5">
              <div className="truncate font-medium text-slate-700">销方：{row.sellerName || "—"}</div>
              <div className="mt-0.5 truncate text-[10px] text-slate-400">购方：{row.buyerName || "—"}</div>
            </td>
            <td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-800">{money(row.amount)}</td>
            <td className={`px-3 py-2.5 text-right font-medium tabular-nums ${Number(row.effectiveAmount ?? row.amount) + 0.005 < Number(row.amount) ? "text-amber-700" : "text-slate-800"}`}>{money(row.effectiveAmount ?? row.amount)}</td>
            <td className="px-3 py-2.5 text-right tabular-nums text-emerald-700">{money(row.bankPaidAmount)}</td>
            <td className={`px-3 py-2.5 text-right tabular-nums ${remaining > 0.005 ? "font-medium text-amber-700" : "text-slate-400"}`}>{money(remaining)}</td>
            <td className="px-3 py-2.5">
              <span className={`rounded-md ${paymentStatus.className}`} title={paymentStatus.title}>
                {paymentStatus.label}
              </span>
              {row.verified && <span className="ml-1.5 text-[10px] text-slate-400">已认证</span>}
            </td>
          </tr>;
        })}
      </tbody>
    </table>
  </div>;
}

function Payments({ rows, rawLoading, onRaw }: { rows: BusinessPartnerDetail["payments"]; rawLoading: boolean; onRaw: (url: string) => void }) {
  if (!rows.length) return <Empty text="暂无已关联银行流水" />;
  return <div className="overflow-x-auto rounded-xl border border-slate-200"><table className="min-w-[1080px] w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2.5">交易日期</th><th className="px-3 py-2.5">收支</th><th className="px-3 py-2.5">对方信息</th><th className="px-3 py-2.5 text-right">金额</th><th className="px-3 py-2.5">已关联发票</th><th className="px-3 py-2.5">原始流水</th></tr></thead><tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={row.id}><td className="px-3 py-2.5"><div className="text-slate-700">{dateText(row.transactionTime || row.date)}</div><div className="mt-0.5 font-mono text-[10px] text-slate-400">{row.serialNo || row.voucherNo || "—"}</div></td><td className="px-3 py-2.5"><span className={row.direction === "out" ? "rounded bg-rose-50 px-1.5 py-0.5 text-rose-700" : "rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700"}>{row.direction === "out" ? "支付" : "收款"}</span></td><td className="max-w-[280px] px-3 py-2.5"><div className="truncate text-slate-700">{row.counterpartyName || "—"}</div><div className="mt-0.5 truncate text-[10px] text-slate-400">{row.counterpartyAccount || row.summary || "—"}</div></td><td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-800">{money(row.amount)}</td><td className="max-w-[250px] px-3 py-2.5">{row.invoices.length ? <div className="space-y-1">{row.invoices.map((invoice) => <div key={invoice.invoiceId} className="truncate text-[10px] text-blue-700">{invoice.invoiceNo} · {money(invoice.allocatedAmount)}</div>)}</div> : <span className="text-slate-400">未关联发票</span>}</td><td className="px-3 py-2.5">{row.rawAvailable ? <button type="button" disabled={rawLoading} onClick={() => onRaw(row.rawUrl)} className="rounded border border-blue-200 px-2 py-1 text-[10px] font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50">查看原始记录</button> : <span className="text-[10px] text-slate-400">历史无原始行</span>}</td></tr>)}</tbody></table></div>;
}

function Sales({ rows }: { rows: BusinessPartnerDetail["sales"] }) {
  if (!rows.length) return <Empty text="暂无已关联销售记录" />;
  return <div className="overflow-x-auto rounded-xl border border-slate-200"><table className="min-w-[800px] w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-2.5">销售单号</th><th className="px-3 py-2.5">渠道</th><th className="px-3 py-2.5">日期</th><th className="px-3 py-2.5 text-right">应收</th><th className="px-3 py-2.5 text-right">实收</th><th className="px-3 py-2.5">状态</th></tr></thead><tbody className="divide-y divide-slate-100">{rows.map((row) => <tr key={row.id}><td className="px-3 py-2.5 font-mono text-slate-700">{row.no || "—"}<div className="mt-0.5 text-[10px] text-slate-400">{row.sourceNo || row.customerCode || ""}</div></td><td className="px-3 py-2.5 text-slate-600">{row.platform || "—"}</td><td className="px-3 py-2.5 text-slate-600">{dateText(row.date)}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{money(row.amount)}</td><td className="px-3 py-2.5 text-right tabular-nums font-medium text-emerald-700">{money(row.paidAmount)}</td><td className="px-3 py-2.5 text-slate-500">{row.status || "—"}</td></tr>)}</tbody></table></div>;
}

function Review({ rows, saving, onClaim }: { rows: BusinessPartnerDetail["reviewItems"]; saving: boolean; onClaim: (id: number, name: string) => void }) {
  if (!rows.length) return <Empty text="没有待确认记录。系统没有把相似名称自动串账。" />;
  return <div className="space-y-2">{rows.map((row) => <div key={row.linkId} className="rounded-xl border border-amber-200 bg-amber-50/40 p-3"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="text-sm font-medium text-slate-800">{row.sourceLabel} · {row.no || "未编号"}</div><div className="mt-1 text-xs text-slate-600">来源名称：{row.rawName || "—"}　税号：{row.rawTaxNo || "—"}　账号：{row.rawAccountNo || "—"}</div><div className="mt-1 text-[11px] text-slate-500">日期 {dateText(row.date)} · 金额 {money(row.amount)} · 系统发现当前档案是候选，但没有足够证据自动确认。</div></div><button type="button" disabled={saving} onClick={() => onClaim(row.linkId, row.rawName)} className="shrink-0 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-700 disabled:opacity-50">确认归入此档案</button></div></div>)}</div>;
}

function Profile({ detail, alias, setAlias, saving, onAddAlias }: { detail: BusinessPartnerDetail; alias: string; setAlias: (value: string) => void; saving: boolean; onAddAlias: () => void }) {
  const grouped = detail.identifiers.reduce<Record<string, string[]>>((result, row) => { (result[row.kind] ||= []).push(row.value); return result; }, {});
  const labels: Record<string, string> = { name: "主名称", alias: "已确认别名", former_name: "曾用名", tax_no: "税号", bank_account: "银行账号", customer_code: "客户编码" };
  return <div className="space-y-5">
    <div className="grid gap-3 md:grid-cols-2"><Info label="联系人" value={detail.contact} /><Info label="电话" value={detail.phone} /><Info label="地址" value={detail.address} /><Info label="税号" value={detail.taxNo} /></div>
    <div className="rounded-xl border border-slate-200 p-4">
      <div className="flex items-center justify-between"><div className="text-sm font-semibold text-slate-800">银行账户</div><span className="text-[11px] text-slate-400">{detail.bankAccounts.length} 个账号</span></div>
      <div className="mt-3 space-y-2">{detail.bankAccounts.length ? detail.bankAccounts.map((row) => <div key={row.accountNo} className="grid gap-2 rounded-lg bg-slate-50 px-3 py-2.5 sm:grid-cols-[1fr_1.35fr_1fr_auto]"><div><div className="text-[10px] text-slate-400">开户行</div><div className="mt-0.5 text-xs text-slate-700">{row.bankName || "—"}</div></div><div><div className="text-[10px] text-slate-400">银行账号</div><div className="mt-0.5 font-mono text-xs text-slate-800">{row.accountNo}</div></div><div><div className="text-[10px] text-slate-400">开户名称</div><div className="mt-0.5 text-xs text-slate-700">{row.accountName || "—"}</div></div><div className="flex items-center">{row.isPrimary && <span className="rounded bg-blue-50 px-2 py-1 text-[10px] font-medium text-blue-600">主账户</span>}</div></div>) : <div className="text-xs text-slate-400">暂无银行账户</div>}</div>
    </div>
    <div className="rounded-xl border border-slate-200 p-4"><div className="text-sm font-semibold text-slate-800">名称、税号和账号证据</div><div className="mt-3 space-y-3">{Object.entries(grouped).map(([kind, values]) => <div key={kind}><div className="text-[11px] text-slate-500">{labels[kind] || kind}</div><div className="mt-1 flex flex-wrap gap-1.5">{values.map((value) => <span key={`${kind}-${value}`} className="rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700">{value}</span>)}</div></div>)}</div><div className="mt-4 border-t border-slate-100 pt-4"><div className="text-xs font-medium text-slate-700">快速补充别名</div><p className="mt-1 text-[11px] leading-4 text-slate-500">完整维护曾用名和多个银行账户请使用“编辑档案”。这里保留快速补充入口。</p><div className="mt-2 flex gap-2"><input value={alias} onChange={(event) => setAlias(event.target.value)} placeholder="输入已确认的完整名称" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400" /><button type="button" disabled={saving || !alias.trim()} onClick={onAddAlias} className="rounded-lg border border-blue-200 px-3 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-50">保存别名</button></div></div></div>
    <div className="rounded-xl border border-slate-200 p-4"><div className="text-sm font-semibold text-slate-800">备注</div><div className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-600">{detail.notes || "暂无备注"}</div></div>
  </div>;
}

function Info({ label, value }: { label: string; value: string }) { return <div className="rounded-lg bg-slate-50 px-3 py-2.5"><div className="text-[11px] text-slate-400">{label}</div><div className="mt-1 break-all text-sm text-slate-700">{value || "—"}</div></div>; }

function PartnerForm({ form, setForm, editing, saving, onClose, onSave }: { form: BusinessPartnerInput; setForm: (next: BusinessPartnerInput) => void; editing: boolean; saving: boolean; onClose: () => void; onSave: () => void }) {
  const formInput = "w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-400";
  const bankAccounts = form.bankAccounts?.length ? form.bankAccounts : [{ bankName: "", accountNo: "", accountName: "", isPrimary: true }];
  const formerNames = form.formerNames || [];

  function set<K extends keyof BusinessPartnerInput>(key: K, value: BusinessPartnerInput[K]) { setForm({ ...form, [key]: value }); }
  function toggleRole(role: BusinessPartnerRole) { const roles = form.roles.includes(role) ? form.roles.filter((item) => item !== role) : [...form.roles, role]; set("roles", roles.length ? roles : ["counterparty"]); }
  function setFormerName(index: number, value: string) { set("formerNames", formerNames.map((item, i) => i === index ? value : item)); }
  function addFormerName() { set("formerNames", [...formerNames, ""]); }
  function removeFormerName(index: number) { set("formerNames", formerNames.filter((_, i) => i !== index)); }
  function setBank(index: number, patch: Partial<BusinessPartnerBankAccount>) {
    set("bankAccounts", bankAccounts.map((item, i) => i === index ? { ...item, ...patch } : item));
  }
  function addBank() { set("bankAccounts", [...bankAccounts, { bankName: "", accountNo: "", accountName: "", isPrimary: false }]); }
  function removeBank(index: number) {
    const next = bankAccounts.filter((_, i) => i !== index);
    if (next.length && !next.some((row) => row.isPrimary)) next[0] = { ...next[0], isPrimary: true };
    set("bankAccounts", next.length ? next : [{ bankName: "", accountNo: "", accountName: "", isPrimary: true }]);
  }
  function makePrimary(index: number) { set("bankAccounts", bankAccounts.map((item, i) => ({ ...item, isPrimary: i === index }))); }

  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-4"><div className="max-h-[calc(100vh-40px)] w-full max-w-4xl overflow-y-auto rounded-2xl bg-white shadow-2xl">
    <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-100 bg-white px-5 py-4"><div><h3 className="text-base font-semibold text-slate-900">{editing ? "编辑往来单位" : "新增往来单位"}</h3><p className="mt-1 text-xs text-slate-500">主档资料只维护一次；曾用名和全部银行账户都会参与采购、发票与银行流水识别。</p></div><button type="button" onClick={onClose} className="text-xl text-slate-400 hover:text-slate-700">×</button></div>
    <div className="space-y-5 p-5">
      <section className="grid gap-3 sm:grid-cols-2">
        <Field label="单位名称 *"><input value={form.name} onChange={(event) => set("name", event.target.value)} className={formInput} /></Field>
        <Field label="税号"><input value={form.taxNo || ""} onChange={(event) => set("taxNo", event.target.value)} className={formInput} /></Field>
        <div className="sm:col-span-2"><div className="mb-1 text-xs font-medium text-slate-600">角色</div><div className="flex flex-wrap gap-2">{(["supplier", "customer", "counterparty"] as BusinessPartnerRole[]).map((role) => <label key={role} className="flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-700"><input type="checkbox" checked={form.roles.includes(role)} onChange={() => toggleRole(role)} />{roleLabels[role]}</label>)}</div></div>
      </section>

      <section className="rounded-xl border border-slate-200 p-4">
        <div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold text-slate-800">曾用名 / 历史名称</div><div className="mt-1 text-[11px] text-slate-500">例如旧营业执照名称、银行户名、带“个体工商户”的完整名称。系统按完整值识别，不做危险的模糊合并。</div></div><button type="button" onClick={addFormerName} className="shrink-0 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50">+ 添加曾用名</button></div>
        <div className="mt-3 space-y-2">{formerNames.length ? formerNames.map((value, index) => <div key={index} className="flex gap-2"><input value={value} onChange={(event) => setFormerName(index, event.target.value)} placeholder="输入完整曾用名" className={formInput} /><button type="button" onClick={() => removeFormerName(index)} className="shrink-0 rounded-lg px-3 text-xs text-slate-400 hover:bg-rose-50 hover:text-rose-600">删除</button></div>) : <div className="rounded-lg bg-slate-50 px-3 py-3 text-xs text-slate-400">暂无曾用名，需要时点击右上角添加。</div>}</div>
      </section>

      <section className="rounded-xl border border-slate-200 p-4">
        <div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold text-slate-800">银行账户</div><div className="mt-1 text-[11px] text-slate-500">支持多个结算账号。主账户用于页面摘要；所有账号都会参与银行流水身份识别。</div></div><button type="button" onClick={addBank} className="shrink-0 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50">+ 添加账户</button></div>
        <div className="mt-3 space-y-3">{bankAccounts.map((row, index) => <div key={index} className={`rounded-xl border p-3 ${row.isPrimary ? "border-blue-200 bg-blue-50/30" : "border-slate-200 bg-slate-50/40"}`}>
          <div className="mb-2 flex items-center justify-between"><div className="flex items-center gap-2"><span className="text-xs font-medium text-slate-700">账户 {index + 1}</span>{row.isPrimary && <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-700">主账户</span>}</div><div className="flex gap-1">{!row.isPrimary && <button type="button" onClick={() => makePrimary(index)} className="rounded px-2 py-1 text-[10px] font-medium text-blue-600 hover:bg-blue-50">设为主账户</button>}{bankAccounts.length > 1 && <button type="button" onClick={() => removeBank(index)} className="rounded px-2 py-1 text-[10px] text-slate-400 hover:bg-rose-50 hover:text-rose-600">删除</button>}</div></div>
          <div className="grid gap-2 sm:grid-cols-3"><Field label="开户行"><input value={row.bankName} onChange={(event) => setBank(index, { bankName: event.target.value })} className={formInput} /></Field><Field label="银行账号"><input value={row.accountNo} onChange={(event) => setBank(index, { accountNo: event.target.value })} className={formInput} /></Field><Field label="开户名称"><input value={row.accountName} onChange={(event) => setBank(index, { accountName: event.target.value })} className={formInput} /></Field></div>
        </div>)}</div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2"><Field label="联系人"><input value={form.contact || ""} onChange={(event) => set("contact", event.target.value)} className={formInput} /></Field><Field label="电话"><input value={form.phone || ""} onChange={(event) => set("phone", event.target.value)} className={formInput} /></Field><Field label="地址" wide><input value={form.address || ""} onChange={(event) => set("address", event.target.value)} className={formInput} /></Field><Field label="备注" wide><textarea value={form.notes || ""} onChange={(event) => set("notes", event.target.value)} rows={3} className={`${formInput} resize-y`} /></Field></section>
    </div>
    <div className="sticky bottom-0 flex justify-end gap-2 border-t border-slate-100 bg-white px-5 py-4"><button type="button" onClick={onClose} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100">取消</button><button type="button" disabled={saving || !form.name.trim()} onClick={onSave} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">{saving ? "保存中…" : "保存并重新核对"}</button></div>
  </div></div>;
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) { return <label className={wide ? "sm:col-span-2" : ""}><span className="mb-1 block text-xs font-medium text-slate-600">{label}</span>{children}</label>; }

function DuplicateDialog({ mine, other, saving, onDecide, onDefer }: { mine: BusinessPartnerDetail; other: BusinessPartnerDetail; saving: boolean; onDecide: (same: boolean) => void; onDefer: () => void }) {
  const rows: Array<[string, BusinessPartnerDetail, string]> = [
    ["当前档案", mine, "text-slate-700"],
    ["疑似同一主体", other, "text-amber-700"],
  ];
  return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/35 p-4">
    <div className="max-h-[calc(100vh-40px)] w-full max-w-xl overflow-y-auto rounded-2xl bg-white shadow-2xl">
      <div className="border-b border-slate-100 px-5 py-4">
        <h3 className="text-base font-semibold text-slate-900">疑似同一主体，请确认</h3>
        <p className="mt-1 text-xs text-slate-500">名称去掉括号内容和公司后缀后相同。系统不会自动合并，请人工判断；确认后会保留一个唯一主体 ID。</p>
      </div>
      <div className="space-y-3 p-5">
        <div className="overflow-hidden rounded-xl border border-amber-200">
          <table className="w-full text-left text-[11px]">
            <thead className="bg-amber-50/60 text-amber-700">
              <tr>
                <th className="px-3 py-2 font-medium">档案</th>
                <th className="px-3 py-2 text-right font-medium">采购单</th>
                <th className="px-3 py-2 text-right font-medium">发票</th>
                <th className="px-3 py-2 text-right font-medium">银行流水</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-amber-100">
              {rows.map(([label, row, tone]) => <tr key={row.id}>
                <td className="px-3 py-2">
                  <div className={`truncate ${tone}`}>{row.name}</div>
                  <div className="mt-0.5 text-[10px] text-slate-400">{label} · 税号 {row.taxNo || "无税号"}</div>
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700">{row.summary.purchaseOrderCount}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700">{row.summary.invoiceCount}</td>
                <td className="px-3 py-2 text-right tabular-nums text-slate-700">{row.summary.bankTransactionCount}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
        <p className="text-[11px] leading-4 text-slate-500">确认为同一主体后，对方名称会登记为曾用名；采购、发票、银行、入库等记录只迁移 canonical partner ID，原始名称、税号和账号不改写，对方档案归档保留审计。</p>
      </div>
      <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 px-5 py-4">
        <button type="button" onClick={onDefer} disabled={saving} className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 disabled:opacity-50">稍后处理</button>
        <button type="button" onClick={() => onDecide(false)} disabled={saving} className="rounded-lg border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">不是同一主体</button>
        <button type="button" onClick={() => onDecide(true)} disabled={saving} className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50">{saving ? "合并中…" : "确认为同一主体并合并"}</button>
      </div>
    </div>
  </div>;
}

function RawDialog({ detail, onClose, onDownload }: { detail: BankRawDetail; onClose: () => void; onDownload: (url: string, filename: string) => void }) { return <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-900/35 p-4"><div className="max-h-[calc(100vh-40px)] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white shadow-2xl"><div className="sticky top-0 flex items-start justify-between border-b border-slate-100 bg-white px-5 py-4"><div><h3 className="text-base font-semibold text-slate-900">银行原始流水记录</h3><p className="mt-1 text-xs text-slate-500">第 {detail.sourceRowNumber ?? detail.raw.rowNumber ?? "—"} 行 · 流水号 {detail.serialNo || "—"}</p></div><button type="button" onClick={onClose} className="text-xl text-slate-400 hover:text-slate-700">×</button></div><div className="grid gap-2 p-5 sm:grid-cols-4"><Info label="交易日期" value={detail.txnDate} /><Info label="交易时间" value={detail.transactionTime || ""} /><Info label="凭证号码" value={detail.voucherNo} /><Info label="我方账号" value={detail.accountNo} /></div><div className="px-5 pb-5"><div className="rounded-xl border border-slate-200"><div className="border-b border-slate-100 px-3 py-2 text-xs font-medium text-slate-700">完整原始字段</div><div className="grid gap-px bg-slate-100 sm:grid-cols-2">{Object.entries(detail.raw.fields || {}).map(([key, value]) => <div key={key} className="bg-white px-3 py-2"><div className="text-[10px] text-slate-400">{key}</div><div className="mt-1 break-all text-xs text-slate-700">{rawValue(value)}</div></div>)}{!Object.keys(detail.raw.fields || {}).length && <pre className="overflow-x-auto bg-white p-3 text-xs text-slate-600">{JSON.stringify(detail.raw, null, 2)}</pre>}</div></div>{detail.sourceFile && <div className="mt-3 text-xs text-slate-500">来源文件：{detail.sourceFile.fileName} · SHA256：{detail.sourceFile.sha256}{detail.sourceFile.downloadUrl && <button type="button" className="ml-2 font-medium text-blue-600 hover:text-blue-700" onClick={() => onDownload(detail.sourceFile!.downloadUrl!, detail.sourceFile!.fileName)}>下载原文件</button>}</div>}</div></div></div>; }
