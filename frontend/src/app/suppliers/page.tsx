"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import {
  businessPartnerApi,
  procurementWorkbenchApi,
  supplierApi,
  PartnerReferenceCoverage,
  SupplierInput,
  SupplierRecord,
  WorkbenchOrder,
  WorkbenchSupplierDetail,
  WorkbenchSupplierSummary,
  WorkbenchSummary,
} from "@/lib/api";
import { useTabScopedState, useTabTitle } from "@/lib/workspace/tab-store";

type StatusFilter = "all" | "regular" | "temporary";
type SortKey = "totalPurchase" | "invoiced" | "uninvoiced" | "pendingCount" | "lastOrderDate";
type SortDir = "desc" | "asc";
type DetailTab = "orders" | "uninvoiced" | "profile";

type FormState = {
  id: number | null;
  name: string;
  platform: string;
  externalShopId: string;
  contact: string;
  taxNo: string;
  phone: string;
  address: string;
  notes: string;
};

const PLATFORM_OPTIONS = ["1688", "拼多多", "淘宝", "线下", "其他"];

const MONEY_OPTS = { minimumFractionDigits: 2, maximumFractionDigits: 2 } as const;

function fmtMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("zh-CN", MONEY_OPTS);
}

function fmtDate(v: string | null | undefined): string {
  const s = (v ?? "").trim();
  return s ? s.slice(0, 10) : "—";
}

function invoiceStatusLabel(s: WorkbenchOrder["invoiceStatus"]): string {
  if (s === "pending") return "待开票";
  if (s === "partial") return "部分开票";
  if (s === "done") return "已开票";
  return "—";
}

function invoiceStatusClass(s: WorkbenchOrder["invoiceStatus"]): string {
  if (s === "done") return "text-emerald-600";
  if (s === "pending") return "text-amber-600";
  if (s === "partial") return "text-violet-600";
  return "text-slate-400";
}

function todayStr(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const picked = [...new Set([1, 2, current - 1, current, current + 1, total - 1, total])]
    .filter((p) => p >= 1 && p <= total)
    .sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const p of picked) {
    if (p - prev > 1) out.push("…");
    out.push(p);
    prev = p;
  }
  return out;
}

type DisplayRow = {
  record: SupplierRecord;
  totalPurchase: number | null;
  invoiced: number | null;
  uninvoiced: number | null;
  wbOrderCount: number | null;
  lastOrderDate: string | null;
  pendingCount: number | null;
};

function supplierIdentityKey(partnerId: number | null | undefined, name: string | null | undefined): string {
  return partnerId != null ? `partner:${partnerId}` : `name:${(name ?? "").trim()}`;
}

function purchaseTypeLabel(type: SupplierRecord["purchaseType"]): string {
  return type === "regular" ? "常购供应商" : "临时供应商";
}

function PurchaseTypeBadge({ type }: { type: SupplierRecord["purchaseType"] }) {
  const regular = type === "regular";
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${regular ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
      {purchaseTypeLabel(type)}
    </span>
  );
}

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  if (!active) return <span className="text-[10px] leading-none text-slate-300">↕</span>;
  return <span className="text-[10px] leading-none text-violet-500">{dir === "desc" ? "↓" : "↑"}</span>;
}

function SortTh({
  label,
  colKey,
  sortKey,
  sortDir,
  onSort,
  right,
}: {
  label: string;
  colKey: SortKey;
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  right?: boolean;
}) {
  const active = sortKey === colKey;
  return (
    <th className={`whitespace-nowrap px-3 py-2 font-medium ${right ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => onSort(colKey)}
        className={`inline-flex items-center gap-0.5 ${active ? "text-violet-600" : "text-slate-500 hover:text-slate-700"}`}
      >
        {label}
        <SortIcon active={active} dir={sortDir} />
      </button>
    </th>
  );
}

function StrokeIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-4 w-4"
    >
      {children}
    </svg>
  );
}

export default function SuppliersPage() {
  const [rows, setRows] = useState<SupplierRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [keywordInput, setKeywordInput] = useTabScopedState("suppliers.searchInput", "");
  const [keyword, setKeyword] = useTabScopedState("suppliers.search", "");
  const [status, setStatus] = useTabScopedState<StatusFilter>("suppliers.status", "all");
  const [form, setForm] = useState<FormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "err"; text: string } | null>(null);

  const [wbMap, setWbMap] = useState<Record<string, WorkbenchSupplierSummary>>({});
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null);
  const [invCounts, setInvCounts] = useState<Record<string, number>>({});
  const [coverage, setCoverage] = useState<PartnerReferenceCoverage | null>(null);
  const [coverageLoading, setCoverageLoading] = useState(true);
  const [coverageSyncing, setCoverageSyncing] = useState(false);

  const [sortKey, setSortKey] = useTabScopedState<SortKey>("suppliers.sortKey", "totalPurchase");
  const [sortDir, setSortDir] = useTabScopedState<SortDir>("suppliers.sortDir", "desc");
  const [page, setPage] = useTabScopedState("suppliers.page", 1);
  const [pageSize, setPageSize] = useTabScopedState("suppliers.pageSize", 10);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selectionTouched = useRef(false);
  const [tab, setTab] = useState<DetailTab>("orders");
  const [detail, setDetail] = useState<WorkbenchSupplierDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await supplierApi.list(keyword.trim(), status);
      setRows(data);
    } catch (error) {
      setNotice({ tone: "err", text: error instanceof Error ? error.message : "加载供应商失败" });
    } finally {
      setLoading(false);
    }
  }, [keyword, status]);

  useEffect(() => { void load(); }, [load]);

  const loadCoverage = useCallback(async () => {
    try {
      const data = await businessPartnerApi.coverage();
      setCoverage(data);
    } catch {
      setCoverage(null);
    } finally {
      setCoverageLoading(false);
    }
  }, []);

  useEffect(() => { void loadCoverage(); }, [loadCoverage]);

  async function refreshMasterData() {
    setCoverageSyncing(true);
    try {
      const result = await businessPartnerApi.sync();
      await Promise.all([load(), loadCoverage()]);
      setNotice({
        tone: "ok",
        text: `主数据已重建：新增主体 ${result.createdPartners} 个，归集来源 ${result.createdLinks + result.updatedLinks} 条。`,
      });
    } catch (error) {
      setNotice({ tone: "err", text: error instanceof Error ? error.message : "主数据重建失败" });
    } finally {
      setCoverageSyncing(false);
    }
  }

  // 搜索 300ms 防抖
  useEffect(() => {
    const t = setTimeout(() => setKeyword(keywordInput), 300);
    return () => clearTimeout(t);
  }, [keywordInput]);

  // 切换关键词 / 筛选回到第 1 页；切换筛选同时重置排序
  useEffect(() => { setPage(1); }, [keyword, status]);

  // 工作台聚合数据（一次性加载）：财务汇总、KPI 待开票订单数、每供应商未开票订单数
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [wb, sum, inv] = await Promise.all([
          procurementWorkbenchApi.suppliers(200),
          procurementWorkbenchApi.summary(),
          procurementWorkbenchApi.orders({ status: "invoice", page: 1, pageSize: 200 }),
        ]);
        if (!alive) return;
        const map: Record<string, WorkbenchSupplierSummary> = {};
        for (const it of wb.items) {
          map[supplierIdentityKey(it.partnerId, it.supplierName)] = it;
        }
        setWbMap(map);
        setSummary(sum);
        const counts: Record<string, number> = {};
        for (const g of inv.groups) {
          for (const o of g.items) {
            if (!o.supplier && o.supplierPartnerId == null) continue;
            const key = supplierIdentityKey(o.supplierPartnerId, o.supplier);
            counts[key] = (counts[key] ?? 0) + 1;
          }
        }
        setInvCounts(counts);
      } catch {
        // 财务聚合数据加载失败不阻塞档案展示，财务列显示 —
      }
    })();
    return () => { alive = false; };
  }, []);

  const displayRows = useMemo<DisplayRow[]>(
    () =>
      rows.map((r) => {
        const identityKey = supplierIdentityKey(r.partnerId, r.name);
        const wb = wbMap[identityKey];
        return {
          record: r,
          totalPurchase: wb ? wb.totalPurchase : null,
          invoiced: wb ? wb.totalPurchase - wb.uninvoiced : null,
          uninvoiced: wb ? wb.uninvoiced : null,
          wbOrderCount: wb ? wb.orderCount : null,
          lastOrderDate: wb?.lastOrderDate || null,
          pendingCount: invCounts[identityKey] ?? (wb ? 0 : null),
        };
      }),
    [rows, wbMap, invCounts],
  );

  function onSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(sortDir === "desc" ? "asc" : "desc");
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sorted = useMemo(() => {
    const arr = [...displayRows];
    const dir = sortDir === "desc" ? -1 : 1;
    arr.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; // 缺失财务数据的恒排最后
      if (bv == null) return -1;
      if (sortKey === "lastOrderDate") return String(av).localeCompare(String(bv)) * dir;
      return ((av as number) - (bv as number)) * dir;
    });
    return arr;
  }, [displayRows, sortKey, sortDir]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);
  const pageRows = useMemo(
    () => sorted.slice((page - 1) * pageSize, page * pageSize),
    [sorted, page, pageSize],
  );

  const kpi = useMemo(() => {
    let total = 0;
    let invoiced = 0;
    let uninvoiced = 0;
    for (const r of displayRows) {
      if (r.totalPurchase != null) total += r.totalPurchase;
      if (r.invoiced != null) invoiced += r.invoiced;
      if (r.uninvoiced != null) uninvoiced += r.uninvoiced;
    }
    return { count: rows.length, total, invoiced, uninvoiced, pendingInvoice: summary?.pendingInvoice ?? null };
  }, [displayRows, rows.length, summary]);

  // 进入页面默认选中第一行（用户主动关闭/选中后不再自动选）
  useEffect(() => {
    if (selectionTouched.current || sorted.length === 0) return;
    setSelectedId((cur) =>
      cur != null && sorted.some((r) => r.record.id === cur) ? cur : sorted[0].record.id,
    );
  }, [sorted]);

  const selected = useMemo(
    () => displayRows.find((r) => r.record.id === selectedId) ?? null,
    [displayRows, selectedId],
  );
  const selectedName = selected?.record.name ?? null;
  useTabTitle(selectedName ? `供应商 · ${selectedName}` : null);

  function selectRow(id: number) {
    selectionTouched.current = true;
    setSelectedId(id);
  }

  function closeDetail() {
    selectionTouched.current = true;
    setSelectedId(null);
  }

  // 选中供应商后按 canonical partnerId 拉取工作台明细；旧数据才回退名称路径。
  useEffect(() => {
    setDetail(null);
    if (!selected) return;
    let alive = true;
    setDetailLoading(true);
    const request = selected.record.partnerId != null
      ? procurementWorkbenchApi.supplierDetailByPartner(selected.record.partnerId)
      : procurementWorkbenchApi.supplierDetail(selected.record.name);
    request
      .then((d) => { if (alive) setDetail(d); })
      .catch(() => { /* 明细拉取失败时展示空态 */ })
      .finally(() => { if (alive) setDetailLoading(false); });
    return () => { alive = false; };
  }, [selected]);

  useEffect(() => { setTab("orders"); }, [selectedName]);

  const recentOrders = detail?.recentOrders ?? [];
  const uninvoicedOrders = useMemo(
    () => recentOrders.filter((o) => o.invoiceStatus !== "done"),
    [recentOrders],
  );

  function downloadUninvoicedCsv() {
    if (!selected) return;
    const lines = uninvoicedOrders.map((o) => {
      const cells = [
        (o.orderDate ?? "").trim().slice(0, 10),
        o.orderNo ?? "",
        o.amount == null ? "" : o.amount.toFixed(2),
        o.paidAmount == null ? "" : o.paidAmount.toFixed(2),
      ];
      return cells.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",");
    });
    const csv = "\uFEFF" + ["采购日期,采购单号,采购金额,实付金额", ...lines].join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `未开票明细_${selected.record.name}_${todayStr()}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function openEdit(row: SupplierRecord) {
    setForm({
      id: row.id,
      name: row.name,
      platform: row.platform || "1688",
      externalShopId: row.externalShopId,
      contact: row.contact,
      taxNo: row.taxNo,
      phone: row.phone,
      address: row.address,
      notes: row.notes,
    });
    setNotice(null);
  }

  async function save() {
    if (!form) return;
    if (!form.name.trim()) {
      setNotice({ tone: "err", text: "供应商名称必填" });
      return;
    }
    setSaving(true);
    try {
      const payload: SupplierInput = {
        name: form.name.trim(),
        platform: form.platform.trim(),
        externalShopId: form.externalShopId.trim(),
        contact: form.contact.trim(),
        taxNo: form.taxNo.trim(),
        phone: form.phone.trim(),
        address: form.address.trim(),
        notes: form.notes.trim(),
      };
      if (form.id == null) {
        await supplierApi.create(payload);
        setNotice({ tone: "ok", text: `已新增供应商「${payload.name}」` });
      } else {
        await supplierApi.update(form.id, payload);
        setNotice({ tone: "ok", text: `已保存供应商「${payload.name}」` });
      }
      setForm(null);
      await load();
    } catch (error) {
      setNotice({ tone: "err", text: error instanceof Error ? error.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  function changeStatus(next: StatusFilter) {
    setStatus(next);
    setSortKey("totalPurchase");
    setSortDir("desc");
  }

  const kpiCards: { label: string; value: string; unit?: string; money?: boolean; accent?: boolean; chip: string; icon: ReactNode }[] = [
    {
      label: "供应商数",
      value: String(kpi.count),
      unit: "家",
      chip: "bg-blue-50 text-blue-600",
      icon: (
        <StrokeIcon>
          <path d="M4 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16" />
          <path d="M16 8h2a2 2 0 0 1 2 2v11" />
          <path d="M2 21h20" />
          <path d="M8 7h.01M12 7h.01M8 11h.01M12 11h.01M8 15h.01M12 15h.01" />
        </StrokeIcon>
      ),
    },
    {
      label: "累计采购金额",
      money: true,
      value: fmtMoney(kpi.total),
      chip: "bg-violet-50 text-violet-600",
      icon: (
        <StrokeIcon>
          <path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2H5" />
          <circle cx="16" cy="14" r="1" fill="currentColor" stroke="none" />
        </StrokeIcon>
      ),
    },
    {
      label: "已开票金额",
      money: true,
      value: fmtMoney(kpi.invoiced),
      chip: "bg-emerald-50 text-emerald-600",
      icon: (
        <StrokeIcon>
          <circle cx="12" cy="12" r="9" />
          <path d="m8.5 12.5 2.5 2.5 4.5-5" />
        </StrokeIcon>
      ),
    },
    {
      label: "未开票金额",
      money: true,
      value: fmtMoney(kpi.uninvoiced),
      accent: true,
      chip: "bg-red-100 text-red-600",
      icon: (
        <StrokeIcon>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 8v4M12 16h.01" />
        </StrokeIcon>
      ),
    },
    {
      label: "未开票订单数",
      value: kpi.pendingInvoice == null ? "—" : String(kpi.pendingInvoice),
      unit: "单",
      chip: "bg-amber-50 text-amber-600",
      icon: (
        <StrokeIcon>
          <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
          <path d="M14 3v5h5" />
          <path d="M9 13h6M9 17h4" />
        </StrokeIcon>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <nav className="flex items-center gap-1.5 text-xs text-slate-400">
          <span>供应商</span>
          <span>/</span>
          <span className="text-slate-600">供应商档案</span>
        </nav>
        <h1 className="mt-1 text-xl font-semibold tracking-tight text-slate-900">供应商档案</h1>
        <p className="mt-0.5 text-xs text-slate-500">管理生产及商品采购供应商，查看采购、入库、开票及订单往来</p>
      </header>

      {notice && (
        <div className={`rounded-lg px-3 py-2 text-sm ${notice.tone === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>
          {notice.text}
        </div>
      )}

      <section className="rounded-xl border border-violet-200 bg-violet-50/50 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">往来主体覆盖率</h2>
            <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-500">
              采购、发票、付款流水等有身份事实的记录统一归集到 BusinessPartner；这里的付款流水状态不代表采购、入库或业务闭环。
            </p>
          </div>
          <button
            type="button"
            onClick={() => void refreshMasterData()}
            disabled={coverageSyncing}
            className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {coverageSyncing ? "主数据重建中…" : "重建并刷新"}
          </button>
        </div>
        {coverageLoading ? (
          <div className="mt-3 text-xs text-slate-400">正在读取主体覆盖率…</div>
        ) : coverage ? (
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ["总体覆盖率", `${Math.round(Math.max(0, Math.min(1, coverage.coverage)) * 100)}%`],
              ["应归集事实", String(coverage.totalFacts)],
              ["已归集", String(coverage.linkedFacts)],
              ["待处理", String(coverage.unlinkedFacts)],
            ].map(([label, value]) => (
              <div key={label} className="rounded-lg border border-white bg-white/80 px-3 py-2">
                <div className="text-[11px] text-slate-400">{label}</div>
                <div className="mt-1 text-lg font-semibold tabular-nums text-slate-800">{value}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="mt-3 text-xs text-amber-700">暂时无法读取主体覆盖率，档案列表仍可正常使用。</div>
        )}
      </section>

      {/* KPI 卡片行 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {kpiCards.map((c) => (
          <div key={c.label} className={`rounded-xl border p-4 ${c.accent ? "border-red-200 bg-red-50/70" : "border-slate-200 bg-white"}`}>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-xs text-slate-500">{c.label}</div>
                <div className={`mt-1.5 truncate text-xl font-semibold tabular-nums ${c.accent ? "text-red-600" : "text-slate-900"}`}>
                  {c.money && <span className="mr-0.5 text-sm font-medium text-slate-400">¥</span>}
                  {c.value}
                  {c.unit && <span className="ml-1 text-xs font-normal text-slate-400">{c.unit}</span>}
                </div>
              </div>
              <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${c.chip}`}>{c.icon}</span>
            </div>
          </div>
        ))}
      </div>

      {/* 左：供应商列表 / 右：详情面板 */}
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[62fr_38fr]">
        <section className="rounded-xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3">
            <input
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              placeholder="搜索供应商名称 / 税号 / 联系人 / 电话"
              className="h-9 w-64 rounded-lg border border-slate-300 px-3 text-sm outline-none placeholder:text-slate-400 focus:border-violet-400"
            />
            <div className="flex overflow-hidden rounded-lg border border-slate-300 text-sm">
              {([["all", "全部供应商"], ["regular", "常购供应商"], ["temporary", "临时供应商"]] as [StatusFilter, string][]).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => changeStatus(key)}
                  className={status === key ? "bg-violet-600 px-3 py-1.5 font-medium text-white" : "bg-white px-3 py-1.5 text-slate-600 hover:bg-slate-50"}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="ml-auto text-xs text-slate-400">类型按有效采购次数自动判定：1 次为临时，2 次及以上为常购</span>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50/60 text-xs">
                  <th className="px-3 py-2 text-left font-medium text-slate-500">供应商名称</th>
                  <th className="px-3 py-2 text-left font-medium text-slate-500">税号</th>
                  <SortTh label="累计采购货款" colKey="totalPurchase" sortKey={sortKey} sortDir={sortDir} onSort={onSort} right />
                  <SortTh label="已开票" colKey="invoiced" sortKey={sortKey} sortDir={sortDir} onSort={onSort} right />
                  <SortTh label="未开票" colKey="uninvoiced" sortKey={sortKey} sortDir={sortDir} onSort={onSort} right />
                  <SortTh label="未开票订单" colKey="pendingCount" sortKey={sortKey} sortDir={sortDir} onSort={onSort} right />
                  <SortTh label="最近采购时间" colKey="lastOrderDate" sortKey={sortKey} sortDir={sortDir} onSort={onSort} />
                  <th className="px-3 py-2 text-left font-medium text-slate-500">供应商类型</th>
                  <th className="px-3 py-2 text-right font-medium text-slate-500">操作</th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400">加载中…</td></tr>
                ) : pageRows.length === 0 ? (
                  <tr><td colSpan={9} className="px-3 py-8 text-center text-slate-400">暂无采购供应商；产生有效采购订单后会自动进入供应商档案</td></tr>
                ) : (
                  pageRows.map((row) => (
                    <tr
                      key={row.record.id}
                      onClick={() => selectRow(row.record.id)}
                      className={`cursor-pointer border-b border-slate-50 last:border-0 ${selectedId === row.record.id ? "bg-violet-50/70" : "hover:bg-slate-50/60"}`}
                    >
                      <td className="max-w-[180px] truncate px-3 py-2 font-medium text-slate-800">
                        {row.record.name}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-slate-600">{row.record.taxNo || "—"}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-700">{fmtMoney(row.totalPurchase)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-700">{fmtMoney(row.invoiced)}</td>
                      <td className={`px-3 py-2 text-right tabular-nums ${row.uninvoiced == null ? "text-slate-400" : "text-red-600"}`}>
                        {fmtMoney(row.uninvoiced)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-700">
                        {row.pendingCount == null ? "—" : row.pendingCount}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-slate-600">{fmtDate(row.lastOrderDate)}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <PurchaseTypeBadge type={row.record.purchaseType} />
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); selectRow(row.record.id); }}
                          className="rounded px-1.5 py-1 text-xs font-medium text-violet-600 hover:bg-violet-50"
                        >
                          查看
                        </button>
                        <button
                          type="button"
                          onClick={(e) => { e.stopPropagation(); openEdit(row.record); }}
                          className="rounded px-1.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100"
                        >
                          编辑
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
            <div className="flex items-center gap-2">
              <span>共 {sorted.length} 条记录</span>
              <select
                value={pageSize}
                onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }}
                className="rounded-md border border-slate-200 px-1.5 py-1 text-xs outline-none focus:border-violet-400"
              >
                {[10, 20, 50].map((n) => (
                  <option key={n} value={n}>{n} 条/页</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              >
                上一页
              </button>
              {pageList(page, totalPages).map((p, idx) =>
                p === "…" ? (
                  <span key={`ellipsis-${idx}`} className="px-1 text-slate-400">…</span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPage(p)}
                    className={p === page ? "min-w-[26px] rounded-md bg-violet-600 px-1.5 py-1 font-medium text-white" : "min-w-[26px] rounded-md px-1.5 py-1 text-slate-600 hover:bg-slate-100"}
                  >
                    {p}
                  </button>
                ),
              )}
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="rounded-md border border-slate-200 px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              >
                下一页
              </button>
            </div>
          </div>
        </section>

        {/* 右：详情面板 */}
        <aside className="rounded-xl border border-slate-200 bg-white xl:sticky xl:top-[104px] xl:self-start">
          {selected ? (
            <>
              <div className="border-b border-slate-100 px-4 py-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <h3 className="truncate text-sm font-semibold text-slate-900">{selected.record.name}</h3>
                    <PurchaseTypeBadge type={selected.record.purchaseType} />
                  </div>
                  <button
                    type="button"
                    onClick={closeDetail}
                    className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                    aria-label="关闭"
                  >
                    ✕
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-slate-600">{selected.record.taxNo || "—"}</span>
                  {selected.record.taxNo && (
                    <span className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-medium text-violet-600">发票自动识别</span>
                  )}
                  <Link
                    href={`/finance/partners?role=supplier&keyword=${encodeURIComponent(selected.record.name)}`}
                    className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600 hover:bg-blue-100"
                  >
                    查看财务往来档案 →
                  </Link>
                  {selected.record.formerNames.length > 0 && (
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">
                      曾用名：{selected.record.formerNames.join("、")}
                    </span>
                  )}
                </div>
                <p className="mt-1.5 truncate text-xs text-slate-400">
                  联系人 {selected.record.contact || "—"} / 电话 {selected.record.phone || "—"} / 地址 {selected.record.address || "—"}
                </p>
              </div>

              <div className="max-h-[calc(100vh-16rem)] overflow-y-auto">
                <div className="grid grid-cols-2 gap-px border-b border-slate-100 bg-slate-100 xl:grid-cols-4">
                  <div className="bg-white px-3 py-2.5">
                    <div className="text-[10px] text-slate-400">累计采购货款</div>
                    <div className="mt-0.5 truncate text-sm font-semibold tabular-nums text-slate-800">{fmtMoney(selected.totalPurchase)}</div>
                  </div>
                  <div className="bg-white px-3 py-2.5">
                    <div className="text-[10px] text-slate-400">已开票</div>
                    <div className="mt-0.5 truncate text-sm font-semibold tabular-nums text-slate-800">{fmtMoney(selected.invoiced)}</div>
                  </div>
                  <div className="bg-white px-3 py-2.5">
                    <div className="text-[10px] text-slate-400">未开票</div>
                    <div className={`mt-0.5 truncate text-sm font-semibold tabular-nums ${selected.uninvoiced == null ? "text-slate-400" : "text-red-600"}`}>
                      {fmtMoney(selected.uninvoiced)}
                    </div>
                  </div>
                  <div className="bg-white px-3 py-2.5">
                    <div className="text-[10px] text-slate-400">采购订单数</div>
                    <div className="mt-0.5 truncate text-sm font-semibold tabular-nums text-slate-800">
                      {selected.wbOrderCount == null ? "—" : selected.wbOrderCount}
                    </div>
                  </div>
                </div>

                <div className="flex items-center justify-between border-b border-slate-100 px-3">
                  <div className="flex">
                    {([["orders", "采购记录"], ["uninvoiced", "未开票明细"], ["profile", "基础资料"]] as [DetailTab, string][]).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        onClick={() => setTab(key)}
                        className={
                          tab === key
                            ? "relative px-2.5 py-2 text-xs font-medium text-violet-600 after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 after:rounded-full after:bg-violet-600"
                            : "px-2.5 py-2 text-xs text-slate-500 hover:text-slate-700"
                        }
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  {tab === "uninvoiced" && (
                    <button
                      type="button"
                      onClick={downloadUninvoicedCsv}
                      disabled={uninvoicedOrders.length === 0}
                      className="rounded-lg border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-600 hover:bg-violet-100 disabled:opacity-40"
                    >
                      下载未开票明细
                    </button>
                  )}
                </div>

                <div className="px-3 py-3">
                  {tab === "orders" && (
                    detailLoading ? (
                      <div className="py-8 text-center text-xs text-slate-400">加载中…</div>
                    ) : recentOrders.length === 0 ? (
                      <div className="py-8 text-center text-xs text-slate-400">暂无采购记录</div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="border-b border-slate-100 text-left text-[11px] text-slate-400">
                            <th className="px-2 py-1.5 font-medium">采购日期</th>
                            <th className="px-2 py-1.5 font-medium">采购单号</th>
                            <th className="px-2 py-1.5 font-medium">渠道</th>
                            <th className="px-2 py-1.5 text-right font-medium">采购金额</th>
                            <th className="px-2 py-1.5 text-right font-medium">实付金额</th>
                            <th className="px-2 py-1.5 text-right font-medium">发票状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {recentOrders.map((o) => (
                            <tr key={o.orderId} className="border-b border-slate-50 last:border-0">
                              <td className="whitespace-nowrap px-2 py-1.5 text-slate-600">{fmtDate(o.orderDate)}</td>
                              <td className="max-w-[140px] truncate px-2 py-1.5 font-mono text-slate-600" title={o.orderNo}>{o.orderNo}</td>
                              <td className="px-2 py-1.5 text-slate-600">{o.platform || "—"}</td>
                              <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fmtMoney(o.amount)}</td>
                              <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fmtMoney(o.paidAmount)}</td>
                              <td className={`px-2 py-1.5 text-right ${invoiceStatusClass(o.invoiceStatus)}`}>
                                {invoiceStatusLabel(o.invoiceStatus)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )
                  )}

                  {tab === "uninvoiced" && (
                    uninvoicedOrders.length === 0 ? (
                      <div className="py-8 text-center text-xs text-slate-400">暂无未开票明细</div>
                    ) : (
                      <>
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-slate-100 text-left text-[11px] text-slate-400">
                              <th className="px-2 py-1.5 font-medium">采购日期</th>
                              <th className="px-2 py-1.5 font-medium">采购单号</th>
                              <th className="px-2 py-1.5 text-right font-medium">采购金额</th>
                              <th className="px-2 py-1.5 text-right font-medium">实付金额</th>
                            </tr>
                          </thead>
                          <tbody>
                            {uninvoicedOrders.map((o) => (
                              <tr key={o.orderId} className="border-b border-slate-50 last:border-0">
                                <td className="whitespace-nowrap px-2 py-1.5 text-slate-600">{fmtDate(o.orderDate)}</td>
                                <td className="max-w-[160px] truncate px-2 py-1.5 font-mono text-slate-600" title={o.orderNo}>{o.orderNo}</td>
                                <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fmtMoney(o.amount)}</td>
                                <td className="px-2 py-1.5 text-right tabular-nums text-slate-700">{fmtMoney(o.paidAmount)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <div className="mt-2 text-right text-[11px] text-slate-400">共 {uninvoicedOrders.length} 条记录</div>
                      </>
                    )
                  )}

                  {tab === "profile" && (
                    <>
                      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                        {(
                          [
                            ["供应商类型", purchaseTypeLabel(selected.record.purchaseType)],
                            ["平台", selected.record.platform],
                            ["店铺 / 编码", selected.record.externalShopId],
                            ["联系人", selected.record.contact],
                            ["电话", selected.record.phone],
                            ["税号", selected.record.taxNo],
                            ["地址", selected.record.address],
                            ["备注", selected.record.notes],
                            ["建档时间", fmtDate(selected.record.createdAt)],
                          ] as [string, string][]
                        ).map(([k, v]) => (
                          <div key={k} className="min-w-0">
                            <dt className="text-slate-400">{k}</dt>
                            <dd className={`mt-0.5 break-words text-slate-700 ${k === "税号" ? "font-mono" : ""}`}>{v || "—"}</dd>
                          </div>
                        ))}
                      </dl>
                      <div className="mt-4 flex justify-end gap-2">
                        {selected.record.partnerId != null && (
                          <Link
                            href="/finance/partners"
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                          >
                            编辑往来主档
                          </Link>
                        )}
                        <button
                          type="button"
                          onClick={() => openEdit(selected.record)}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                        >
                          编辑采购画像
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex h-64 flex-col items-center justify-center gap-1 px-6 text-center">
              <div className="text-sm text-slate-400">未选择供应商</div>
              <div className="text-xs text-slate-400">点击左侧列表中的供应商查看采购、开票与订单明细</div>
            </div>
          )}
        </aside>
      </div>

      {form && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4" onClick={() => !saving && setForm(null)}>
          <div className="max-h-[88vh] w-full max-w-xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="h-4 w-1.5 rounded bg-violet-600" />
                <div>
                  <h3 className="text-base font-semibold text-slate-900">编辑采购画像</h3>
                  <div className="mt-0.5 text-[11px] text-slate-400">主体名称、税号、联系人、地址及银行账户统一在往来单位主档维护。</div>
                </div>
              </div>
              <button type="button" onClick={() => setForm(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600" aria-label="关闭">✕</button>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="col-span-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5">
                <div className="text-[10px] text-slate-400">统一往来主体</div>
                <div className="mt-1 text-sm font-medium text-slate-800">{form.name || "—"}</div>
                <div className="mt-1 font-mono text-[11px] text-slate-500">{form.taxNo || "税号未维护"}</div>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">采购平台 / 渠道</span>
                <select
                  value={form.platform}
                  onChange={(e) => setForm({ ...form, platform: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-violet-400"
                >
                  {PLATFORM_OPTIONS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-600">店铺 / 供应商编码</span>
                <input
                  value={form.externalShopId}
                  onChange={(e) => setForm({ ...form, externalShopId: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-violet-400"
                />
              </label>
              <label className="col-span-2 block">
                <span className="text-xs font-medium text-slate-600">采购画像备注</span>
                <input
                  value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-1.5 text-sm outline-none focus:border-violet-400"
                  placeholder="例如：1688店铺、MOQ、结算习惯、采购侧备注"
                />
              </label>
              <div className="col-span-2 flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
                <span>供应商类型由真实采购次数自动计算；身份资料只维护一份。</span>
                <Link href="/finance/partners" className="font-medium text-violet-600 hover:text-violet-700">
                  去往来单位主档维护
                </Link>
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setForm(null)} disabled={saving} className="rounded-lg border border-slate-300 px-4 py-1.5 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50">取消</button>
              <button type="button" onClick={() => void save()} disabled={saving} className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50">
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}


    </div>
  );
}
