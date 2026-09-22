"use client";

import Link from "@/components/workspace/workspace-link";
import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";
import { useTabTitle } from "@/lib/workspace/tab-store";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

type Scope = "all" | "domestic" | "foreign_trade";

type LegalEntity = {
  id: number;
  code: string;
  name: string;
  countryCode: string;
  baseCurrency: string;
  taxId: string;
  status: string;
  isDefault: boolean;
  businessScopes: string[];
  note: string;
};

type Entry = {
  id: number;
  legalEntityId: number;
  legalEntityName: string;
  businessScope: string;
  sourceType: string;
  sourceId: string;
  sourceNo: string;
  category: string;
  categoryLabel: string;
  direction: string;
  currency: string;
  amount: string;
  taxAmount: string;
  valueType: string;
  settlementStatus: string;
  invoiceStatus: string;
  accountingYear: number;
  accountingMonth: number;
  occurredAt: string | null;
  note: string;
};

type CurrencyTotal = {
  currency: string;
  actualIncome: string;
  actualExpense: string;
  estimatedIncome: string;
  estimatedExpense: string;
  actualCashInflow: string;
  actualCashOutflow: string;
  forecastCashInflow: string;
  forecastCashOutflow: string;
  actualProfit: string;
  estimatedProfit: string;
  actualNetCash: string;
  forecastNetCash: string;
};

type CenterData = {
  entity: LegalEntity;
  businessScope: Scope;
  year: number;
  month: number;
  entryCount: number;
  todo: {
    pendingConfirmation: number;
    pendingSettlement: number;
    pendingInvoice: number;
    estimated: number;
    anomalies: number;
  };
  totalsByCurrency: CurrencyTotal[];
  scopeSummary: Record<string, { count: number; estimated: number; pending: number }>;
  recentEntries: Entry[];
  principle: string;
};

const SCOPE_COPY: Record<Scope, string> = {
  all: "全部",
  domestic: "内销",
  foreign_trade: "外贸",
};

const CATEGORY_OPTIONS = [
  ["sales_income", "销售收入"],
  ["purchase_cost", "采购成本"],
  ["platform_fee", "平台费用"],
  ["domestic_logistics", "国内物流"],
  ["international_freight", "国际物流"],
  ["export_fee", "出口费用"],
  ["export_tax_refund", "出口退税"],
  ["customs_duty", "进口关税"],
  ["anti_dumping_duty", "反倾销税"],
  ["countervailing_duty", "反补贴税"],
  ["import_vat", "进口 VAT"],
  ["clearance_fee", "清关费用"],
  ["last_mile_fee", "海外末端配送"],
  ["refund", "退款"],
  ["exchange_gain_loss", "汇兑损益"],
  ["other", "其他"],
] as const;

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function parseMonth(value: string) {
  const [year, month] = value.split("-").map(Number);
  return { year, month };
}

function money(value: string | number, currency = "CNY") {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return "—";
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${currency} ${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
  }
}

function scopeLabel(scope: string) {
  return scope === "foreign_trade" ? "外贸" : "内销";
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    pending_confirmation: "待确认",
    partial: "部分结算",
    settled: "已结算",
    closed: "已完成",
    anomaly: "异常",
  };
  return labels[status] || status || "—";
}

export default function FinanceCenterPage() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const scopeParam = searchParams.get("scope");
  const initialScope: Scope =
    scopeParam === "domestic" || scopeParam === "foreign_trade" || scopeParam === "all"
      ? scopeParam
      : "all";

  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [entityId, setEntityId] = useState<number | null>(null);
  const [scope, setScope] = useState<Scope>(initialScope);
  useTabTitle(scope === "foreign_trade" ? "外贸财务" : "财务中心");
  const [period, setPeriod] = useState(currentMonth());
  const [data, setData] = useState<CenterData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [entryOpen, setEntryOpen] = useState(false);
  const [entityOpen, setEntityOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");

  const periodParts = useMemo(() => parseMonth(period), [period]);

  const [entryForm, setEntryForm] = useState({
    business_scope: "domestic",
    source_type: "manual",
    source_id: "",
    source_no: "",
    category: "other",
    direction: "expense",
    currency: "CNY",
    amount: "0",
    tax_amount: "0",
    value_type: "actual",
    settlement_status: "pending",
    invoice_status: "unknown",
    note: "",
  });

  const [entityForm, setEntityForm] = useState({
    code: "",
    name: "",
    country_code: "CN",
    base_currency: "CNY",
    tax_id: "",
    status: "active",
    is_default: false,
    business_scopes: ["foreign_trade"] as string[],
    note: "",
  });

  useEffect(() => {
    const next: Scope =
      scopeParam === "domestic" || scopeParam === "foreign_trade" || scopeParam === "all"
        ? scopeParam
        : "all";
    setScope((current) => current === next ? current : next);
    if (next !== "all") {
      setEntryForm((current) => (
        current.business_scope === next ? current : { ...current, business_scope: next }
      ));
    }
  }, [scopeParam]);

  const loadEntities = useCallback(async () => {
    const res = await authenticatedFetch("/api/v1/finance/entities", { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "公司主体加载失败");
    const rows = body.items as LegalEntity[];
    setEntities(rows);
    setEntityId((current) => current ?? rows.find((row) => row.isDefault)?.id ?? rows[0]?.id ?? null);
    return rows;
  }, []);

  const loadCenter = useCallback(async () => {
    if (!entityId || !periodParts.year || !periodParts.month) return;
    setLoading(true);
    try {
      const params = new URLSearchParams({
        legal_entity_id: String(entityId),
        business_scope: scope,
        year: String(periodParts.year),
        month: String(periodParts.month),
      });
      const res = await authenticatedFetch("/api/v1/finance/center?" + params.toString(), { cache: "no-store" });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "财务中心加载失败");
      setData(body);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "财务中心加载失败");
    } finally {
      setLoading(false);
    }
  }, [entityId, periodParts.month, periodParts.year, scope]);

  useEffect(() => {
    void loadEntities().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [loadEntities]);

  useEffect(() => {
    void loadCenter();
  }, [loadCenter]);

  function chooseScope(next: Scope) {
    setScope(next);
    setEntryForm((current) => ({
      ...current,
      business_scope: next === "all" ? current.business_scope : next,
    }));
    const params = new URLSearchParams(searchParams.toString());
    if (next === "all") params.delete("scope");
    else params.set("scope", next);
    const query = params.toString();
    syncWorkspaceUrl(query ? `${pathname}?${query}` : pathname, "replace");
  }

  async function syncBusiness() {
    setSyncing(true);
    setSyncMessage("");
    setError("");
    try {
      const params = new URLSearchParams({
        year: String(periodParts.year),
        month: String(periodParts.month),
        business_scope: scope,
      });
      const res = await authenticatedFetch("/api/v1/finance/sync-business?" + params.toString(), {
        method: "POST",
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "业务数据同步失败");
      setSyncMessage(
        `已同步：新增 ${body.created ?? 0} · 更新 ${body.updated ?? 0} · 清理 ${body.deleted ?? 0}`
      );
      await loadCenter();
    } catch (e) {
      setError(e instanceof Error ? e.message : "业务数据同步失败");
    } finally {
      setSyncing(false);
    }
  }

  async function saveEntry() {
    if (!entityId) return;
    setSaving(true);
    setError("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/entries", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          legal_entity_id: entityId,
          ...entryForm,
          accounting_year: periodParts.year,
          accounting_month: periodParts.month,
          occurred_at: new Date().toISOString(),
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "保存失败");
      setEntryOpen(false);
      setEntryForm((current) => ({
        ...current,
        source_id: "",
        source_no: "",
        amount: "0",
        tax_amount: "0",
        note: "",
      }));
      await loadCenter();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function saveEntity() {
    setSaving(true);
    setError("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/entities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entityForm),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "主体保存失败");
      setEntityOpen(false);
      setEntityForm({
        code: "",
        name: "",
        country_code: "CN",
        base_currency: "CNY",
        tax_id: "",
        status: "active",
        is_default: false,
        business_scopes: ["foreign_trade"],
        note: "",
      });
      const rows = await loadEntities();
      setEntityId(rows.find((row) => row.code === body.code)?.id ?? body.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "主体保存失败");
    } finally {
      setSaving(false);
    }
  }

  const selectedEntity = entities.find((row) => row.id === entityId) ?? null;
  const totals = data?.totalsByCurrency ?? [];

  return (
    <div className="pb-10">
      <header className="app-page-header -mx-1 pb-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-[10px] font-medium uppercase tracking-[0.14em] text-blue-500">FINANCE CENTER</div>
            <h1 className="mt-1 text-xl font-semibold text-slate-900">财务中心</h1>
            <p className="mt-1.5 text-sm text-slate-500">
              公司主体是第一维度，内销 / 外贸是第二维度。业务事实先进入财务事项池，再进入收支、税务、利润和月结。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => void syncBusiness()}
              disabled={syncing}
              className="app-button-secondary rounded-lg px-3 py-2 text-[11px] font-medium disabled:opacity-50"
            >
              {syncing ? "同步中…" : "同步业务数据"}
            </button>
            <button onClick={() => setEntityOpen((v) => !v)} className="app-button-secondary rounded-lg px-3 py-2 text-[11px] font-medium">
              + 公司主体
            </button>
            <button onClick={() => setEntryOpen((v) => !v)} className="app-button-primary rounded-lg px-3.5 py-2 text-[11px] font-medium">
              + 财务事项
            </button>
          </div>
        </div>
      </header>

      {error && <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12px] text-rose-700">{error}</div>}
      {syncMessage && <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-2.5 text-[11px] text-emerald-700">{syncMessage}</div>}

      <section className="mt-4 app-card rounded-xl p-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-[280px]">
            <div className="mb-1 text-[10px] font-medium text-slate-400">公司主体</div>
            <select
              className="app-input-control h-9 w-full rounded-lg px-3 text-[12px]"
              value={entityId ?? ""}
              onChange={(e) => setEntityId(Number(e.target.value) || null)}
            >
              {entities.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.name}{row.isDefault ? " · 默认" : ""}
                </option>
              ))}
            </select>
          </div>

          <div>
            <div className="mb-1 text-[10px] font-medium text-slate-400">业务范围</div>
            <div className="flex h-9 rounded-lg border border-slate-200 bg-slate-50 p-0.5">
              {(Object.keys(SCOPE_COPY) as Scope[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => chooseScope(key)}
                  className={"rounded-md px-4 text-[11px] font-medium transition " + (
                    scope === key ? "bg-white text-blue-700 shadow-sm" : "text-slate-500 hover:text-slate-800"
                  )}
                >
                  {SCOPE_COPY[key]}
                </button>
              ))}
            </div>
          </div>

          <label className="ml-auto min-w-[150px]">
            <div className="mb-1 text-[10px] font-medium text-slate-400">账期</div>
            <input
              type="month"
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
              className="app-input-control h-9 w-full rounded-lg px-3 text-[12px]"
            />
          </label>
        </div>
        {selectedEntity && (
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-slate-100 pt-2 text-[10px] text-slate-400">
            <span>主体编码：{selectedEntity.code}</span>
            <span>国家：{selectedEntity.countryCode}</span>
            <span>本位币：{selectedEntity.baseCurrency}</span>
            <span>业务：{selectedEntity.businessScopes.map((item) => scopeLabel(item)).join(" / ") || "—"}</span>
          </div>
        )}
      </section>

      {entityOpen && (
        <section className="mt-3 app-card rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-[13px] font-semibold text-slate-900">新增公司主体</h2>
              <p className="mt-0.5 text-[10px] text-slate-400">后续奥地利、德国、香港主体直接在这里增加，不需要重做财务模块。</p>
            </div>
            <button onClick={() => setEntityOpen(false)} className="text-[11px] text-slate-400">关闭</button>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-3 xl:grid-cols-6">
            <Field label="主体编码"><input className="finance-input" value={entityForm.code} onChange={(e) => setEntityForm({ ...entityForm, code: e.target.value })} placeholder="AT01" /></Field>
            <Field label="公司名称"><input className="finance-input" value={entityForm.name} onChange={(e) => setEntityForm({ ...entityForm, name: e.target.value })} /></Field>
            <Field label="国家"><input className="finance-input" value={entityForm.country_code} onChange={(e) => setEntityForm({ ...entityForm, country_code: e.target.value })} placeholder="AT" /></Field>
            <Field label="本位币"><input className="finance-input" value={entityForm.base_currency} onChange={(e) => setEntityForm({ ...entityForm, base_currency: e.target.value })} placeholder="EUR" /></Field>
            <Field label="税号 / VAT No."><input className="finance-input" value={entityForm.tax_id} onChange={(e) => setEntityForm({ ...entityForm, tax_id: e.target.value })} /></Field>
            <label className="flex h-[55px] items-end pb-2 text-[11px] text-slate-600"><input type="checkbox" checked={entityForm.is_default} onChange={(e) => setEntityForm({ ...entityForm, is_default: e.target.checked })} className="mr-2" />设为默认主体</label>
          </div>
          <div className="mt-3 flex justify-end">
            <button onClick={() => void saveEntity()} disabled={saving || !entityForm.code.trim() || !entityForm.name.trim()} className="app-button-primary rounded-lg px-4 py-2 text-[11px] font-medium disabled:opacity-40">
              保存主体
            </button>
          </div>
        </section>
      )}

      {entryOpen && (
        <section className="mt-3 app-card rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-[13px] font-semibold text-slate-900">新增财务事项</h2>
              <p className="mt-0.5 text-[10px] text-slate-400">目前用于人工补录/调整；后续销售、采购、Shipment 会自动写入同一事项池。</p>
            </div>
            <button onClick={() => setEntryOpen(false)} className="text-[11px] text-slate-400">关闭</button>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-4 xl:grid-cols-7">
            <Field label="业务">
              <select className="finance-input" value={entryForm.business_scope} onChange={(e) => setEntryForm({ ...entryForm, business_scope: e.target.value })}>
                <option value="domestic">内销</option>
                <option value="foreign_trade">外贸</option>
              </select>
            </Field>
            <Field label="财务类别">
              <select className="finance-input" value={entryForm.category} onChange={(e) => setEntryForm({ ...entryForm, category: e.target.value })}>
                {CATEGORY_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
              </select>
            </Field>
            <Field label="方向">
              <select className="finance-input" value={entryForm.direction} onChange={(e) => setEntryForm({ ...entryForm, direction: e.target.value })}>
                <option value="income">收入</option>
                <option value="expense">支出</option>
              </select>
            </Field>
            <Field label="币种"><input className="finance-input" value={entryForm.currency} onChange={(e) => setEntryForm({ ...entryForm, currency: e.target.value.toUpperCase() })} /></Field>
            <Field label="金额"><input className="finance-input" inputMode="decimal" value={entryForm.amount} onChange={(e) => setEntryForm({ ...entryForm, amount: e.target.value })} /></Field>
            <Field label="税额"><input className="finance-input" inputMode="decimal" value={entryForm.tax_amount} onChange={(e) => setEntryForm({ ...entryForm, tax_amount: e.target.value })} /></Field>
            <Field label="金额口径">
              <select className="finance-input" value={entryForm.value_type} onChange={(e) => setEntryForm({ ...entryForm, value_type: e.target.value })}>
                <option value="actual">实际</option>
                <option value="estimated">预计</option>
              </select>
            </Field>
            <Field label="来源类型"><input className="finance-input" value={entryForm.source_type} onChange={(e) => setEntryForm({ ...entryForm, source_type: e.target.value })} placeholder="manual / shipment" /></Field>
            <Field label="来源单号"><input className="finance-input" value={entryForm.source_no} onChange={(e) => setEntryForm({ ...entryForm, source_no: e.target.value })} /></Field>
            <Field label="结算状态">
              <select className="finance-input" value={entryForm.settlement_status} onChange={(e) => setEntryForm({ ...entryForm, settlement_status: e.target.value })}>
                <option value="pending">待处理</option>
                <option value="pending_confirmation">待确认</option>
                <option value="partial">部分结算</option>
                <option value="settled">已结算</option>
                <option value="anomaly">异常</option>
              </select>
            </Field>
            <Field label="发票状态">
              <select className="finance-input" value={entryForm.invoice_status} onChange={(e) => setEntryForm({ ...entryForm, invoice_status: e.target.value })}>
                <option value="unknown">不确定</option>
                <option value="pending">待票</option>
                <option value="missing">缺票</option>
                <option value="received">已收票</option>
                <option value="not_required">无需发票</option>
              </select>
            </Field>
            <div className="md:col-span-2 xl:col-span-3">
              <Field label="备注"><input className="finance-input" value={entryForm.note} onChange={(e) => setEntryForm({ ...entryForm, note: e.target.value })} /></Field>
            </div>
          </div>
          <div className="mt-3 flex justify-end">
            <button onClick={() => void saveEntry()} disabled={saving || !entityId} className="app-button-primary rounded-lg px-4 py-2 text-[11px] font-medium disabled:opacity-40">
              保存财务事项
            </button>
          </div>
        </section>
      )}

      <section className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <TodoCard label="待确认" value={data?.todo.pendingConfirmation ?? 0} />
        <TodoCard label="待结算" value={data?.todo.pendingSettlement ?? 0} />
        <TodoCard label="待发票" value={data?.todo.pendingInvoice ?? 0} />
        <TodoCard label="预计事项" value={data?.todo.estimated ?? 0} />
        <TodoCard label="异常事项" value={data?.todo.anomalies ?? 0} danger={(data?.todo.anomalies ?? 0) > 0} />
      </section>

      <section className="mt-4 app-card rounded-xl">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-[13px] font-semibold text-slate-900">本月经营口径</h2>
            <p className="mt-0.5 text-[10px] text-slate-400">不同币种分别展示，不直接跨币种相加。预计利润 = 实际 + 预计收入 − 实际 + 预计支出。</p>
          </div>
          <span className="text-[10px] text-slate-400">{data?.entryCount ?? 0} 条财务事项</span>
        </div>
        {loading ? (
          <div className="py-10 text-center text-[12px] text-slate-400">正在加载财务数据…</div>
        ) : totals.length === 0 ? (
          <div className="px-4 py-8">
            <div className="text-[13px] font-medium text-slate-700">这个账期还没有进入统一事项池的数据。</div>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">原有银行、发票、月结功能没有丢失；V1 先把统一底座搭好，接下来再逐步让内销订单、采购、Shipment 自动生成事项。</p>
          </div>
        ) : (
          <div className="grid gap-px bg-slate-100 md:grid-cols-2 xl:grid-cols-4">
            {totals.map((row) => (
              <div key={row.currency} className="bg-white p-4">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-semibold text-slate-700">{row.currency}</span>
                  <span className="text-[9px] text-slate-400">实际 / 含预计</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-[10px]">
                  <Metric label="实际收入" value={money(row.actualIncome, row.currency)} />
                  <Metric label="实际支出" value={money(row.actualExpense, row.currency)} />
                  <Metric label="实际利润" value={money(row.actualProfit, row.currency)} strong />
                  <Metric label="预计利润" value={money(row.estimatedProfit, row.currency)} strong />
                  <Metric label="已结算净现金" value={money(row.actualNetCash, row.currency)} />
                  <Metric label="含待结算现金预测" value={money(row.forecastNetCash, row.currency)} />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(330px,.55fr)]">
        <section className="app-card overflow-hidden rounded-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <div>
              <h2 className="text-[13px] font-semibold text-slate-900">财务事项池</h2>
              <p className="mt-0.5 text-[10px] text-slate-400">订单、采购、物流、Shipment、退税等最终都在这里形成统一财务事实。</p>
            </div>
          </div>
          {(data?.recentEntries.length ?? 0) === 0 ? (
            <div className="py-12 text-center text-[11px] text-slate-400">暂无财务事项。</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-[900px] w-full text-left text-[11px]">
                <thead className="bg-slate-50 text-slate-400">
                  <tr><th className="px-4 py-2.5">业务</th><th>事项</th><th>来源</th><th>金额</th><th>预计/实际</th><th>结算</th><th>发票</th></tr>
                </thead>
                <tbody>
                  {data?.recentEntries.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-4 py-2.5"><span className={"rounded px-1.5 py-0.5 text-[9px] font-medium " + (row.businessScope === "foreign_trade" ? "bg-violet-50 text-violet-700" : "bg-sky-50 text-sky-700")}>{scopeLabel(row.businessScope)}</span></td>
                      <td><div className="font-medium text-slate-700">{row.categoryLabel}</div><div className="text-[9px] text-slate-400">{row.direction === "income" ? "收入" : "支出"}</div></td>
                      <td><div>{row.sourceNo || row.sourceType}</div><div className="text-[9px] text-slate-400">{row.sourceType}</div></td>
                      <td className={row.direction === "income" ? "font-medium text-emerald-700" : "font-medium text-slate-700"}>{money(row.amount, row.currency)}</td>
                      <td>{row.valueType === "estimated" ? "预计" : "实际"}</td>
                      <td>{statusLabel(row.settlementStatus)}</td>
                      <td>{row.invoiceStatus === "received" ? "已收票" : row.invoiceStatus === "not_required" ? "无需" : row.invoiceStatus === "missing" ? "缺票" : row.invoiceStatus === "pending" ? "待票" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <aside className="space-y-3">
          <section className="app-card rounded-xl p-4">
            <h2 className="text-[13px] font-semibold text-slate-900">业务构成</h2>
            <div className="mt-3 space-y-2">
              <ScopeRow label="内销" data={data?.scopeSummary.domestic} href="/finance?scope=domestic" />
              <ScopeRow label="外贸" data={data?.scopeSummary.foreign_trade} href="/finance?scope=foreign_trade" />
            </div>
          </section>

          <section className="app-card rounded-xl p-4">
            <h2 className="text-[13px] font-semibold text-slate-900">财务处理</h2>
            <div className="mt-2 grid gap-1.5">
              <QuickLink href="/finance/bank-transactions" title="收支 / 银行流水" desc="实际资金进出与匹配" />
              <QuickLink href="/finance/invoices" title="发票管理" desc="进销项票据与匹配" />
              <QuickLink href="/finance/vouchers" title="自动记账凭证" desc="按实际财务事项生成借贷凭证" />
              <QuickLink href="/finance/tax-accounting" title="发票税务" desc="国内税务归集与分类" />
              <QuickLink href="/finance/monthly-send" title="月结中心" desc="整理、打包并发送财务资料" />
            </div>
          </section>

          <section className="rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-[10px] leading-5 text-blue-700">
            V1 先建立统一财务底座。下一阶段内销销售/采购、外贸订单/Shipment 会按规则自动生成事项；人工补录只用于调整和无法自动识别的费用。
          </section>
        </aside>
      </div>

      <style jsx global>{`
        .finance-input{width:100%;height:34px;border:1px solid var(--app-border);border-radius:.5rem;background:var(--app-input);padding:.4rem .6rem;font-size:.75rem;line-height:1rem;color:var(--app-text);outline:none}
        .finance-input:focus{border-color:var(--app-accent);box-shadow:0 0 0 3px var(--app-focus)}
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[10px] font-medium text-slate-500">{label}</span>{children}</label>;
}

function TodoCard({ label, value, danger = false }: { label: string; value: number; danger?: boolean }) {
  return <div className="app-card rounded-xl px-4 py-3"><div className="text-[10px] text-slate-400">{label}</div><div className={"mt-1 text-xl font-semibold " + (danger ? "text-rose-600" : "text-slate-900")}>{value}</div></div>;
}

function Metric({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return <div><div className="text-slate-400">{label}</div><div className={"mt-0.5 " + (strong ? "text-[13px] font-semibold text-slate-900" : "font-medium text-slate-700")}>{value}</div></div>;
}

function ScopeRow({ label, data, href }: { label: string; data?: { count: number; estimated: number; pending: number }; href: string }) {
  return (
    <Link href={href} className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 hover:bg-slate-100">
      <div><div className="text-[11px] font-medium text-slate-700">{label}</div><div className="mt-0.5 text-[9px] text-slate-400">{data?.count ?? 0} 条 · {data?.pending ?? 0} 待处理</div></div>
      <span className="text-[10px] text-slate-400">{data?.estimated ?? 0} 预计</span>
    </Link>
  );
}

function QuickLink({ href, title, desc }: { href: string; title: string; desc: string }) {
  return (
    <Link href={href} className="block rounded-lg border border-slate-100 px-3 py-2 hover:bg-slate-50">
      <div className="text-[11px] font-medium text-slate-700">{title}</div>
      <div className="mt-0.5 text-[9px] text-slate-400">{desc}</div>
    </Link>
  );
}
