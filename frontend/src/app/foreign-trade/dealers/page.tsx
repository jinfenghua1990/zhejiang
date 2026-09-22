"use client";

import { useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Dealer = {
  id: number;
  code: string;
  companyName: string;
  country: string;
  region: string;
  contactName: string;
  email: string;
  phone: string;
  dealerLevel: string;
  status: string;
  currency: string;
  shopifyCompanyId: string;
  shopifyCompanyLocationId: string;
  address: string;
  note: string;
};

type InventoryRow = {
  skuId: number;
  skuCode: string;
  skuName: string;
  actualStock: string;
  publicAvailable: string;
  dealerReserved: string;
  availableToDealer: string;
};

type Reservation = {
  id: number;
  dealerId: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  quantity: string;
  reservationKind: string;
  referenceNo: string;
  source: string;
  shopifyDraftOrderId: string;
  startsAt: string | null;
  expiresAt: string | null;
  status: string;
  effectiveStatus: string;
  note: string;
};

const inputClass = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100";
const EMPTY_DEALER = {
  code: "",
  company_name: "",
  country: "",
  region: "",
  contact_name: "",
  email: "",
  phone: "",
  dealer_level: "standard",
  status: "active",
  currency: "EUR",
  shopify_company_id: "",
  shopify_company_location_id: "",
  address: "",
  note: "",
};

function defaultExpiry() {
  const value = new Date(Date.now() + 48 * 60 * 60 * 1000);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || `请求失败（${res.status}）`);
  }
  return res.json();
}

export default function B2BDealersPage() {
  const [dealers, setDealers] = useState<Dealer[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [inventory, setInventory] = useState<InventoryRow[]>([]);
  const [reservations, setReservations] = useState<Reservation[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Dealer | null>(null);
  const [dealerForm, setDealerForm] = useState({ ...EMPTY_DEALER });
  const [reservationForm, setReservationForm] = useState({
    sku_code: "",
    quantity: "",
    expires_at: defaultExpiry(),
    reservation_kind: "quote",
    reference_no: "",
    source: "manual",
    shopify_draft_order_id: "",
    note: "",
  });

  const selected = useMemo(
    () => dealers.find((row) => row.id === selectedId) || null,
    [dealers, selectedId]
  );

  async function loadDealers() {
    setError("");
    try {
      const data = await request<{ items: Dealer[] }>(`/api/v1/foreign-trade/b2b/dealers?q=${encodeURIComponent(q)}`);
      setDealers(data.items);
      if (data.items.length && (selectedId === null || !data.items.some((row) => row.id === selectedId))) {
        setSelectedId(data.items[0].id);
      }
      if (!data.items.length) setSelectedId(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "加载经销商失败");
    }
  }

  async function loadDealerData(dealerId: number) {
    setError("");
    try {
      const [inv, resv] = await Promise.all([
        request<{ items: InventoryRow[] }>(`/api/v1/foreign-trade/b2b/dealers/${dealerId}/inventory`),
        request<{ items: Reservation[] }>(`/api/v1/foreign-trade/b2b/dealers/${dealerId}/reservations`),
      ]);
      setInventory(inv.items);
      setReservations(resv.items);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "加载客户库存失败");
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(loadDealers, 120);
    return () => window.clearTimeout(timer);
  }, [q]);

  useEffect(() => {
    if (selectedId !== null) loadDealerData(selectedId);
    else {
      setInventory([]);
      setReservations([]);
    }
  }, [selectedId]);

  function editDealer(row?: Dealer) {
    if (!row) {
      setEditing(null);
      setDealerForm({ ...EMPTY_DEALER });
      return;
    }
    setEditing(row);
    setDealerForm({
      code: row.code,
      company_name: row.companyName,
      country: row.country,
      region: row.region,
      contact_name: row.contactName,
      email: row.email,
      phone: row.phone,
      dealer_level: row.dealerLevel,
      status: row.status,
      currency: row.currency,
      shopify_company_id: row.shopifyCompanyId,
      shopify_company_location_id: row.shopifyCompanyLocationId,
      address: row.address,
      note: row.note,
    });
  }

  async function saveDealer() {
    if (!dealerForm.code.trim() || !dealerForm.company_name.trim()) {
      setError("经销商编码和公司名称必须填写");
      return;
    }
    setError("");
    try {
      const saved = await request<Dealer>(
        editing ? `/api/v1/foreign-trade/b2b/dealers/${editing.id}` : "/api/v1/foreign-trade/b2b/dealers",
        {
          method: editing ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(dealerForm),
        }
      );
      setEditing(null);
      setDealerForm({ ...EMPTY_DEALER });
      await loadDealers();
      setSelectedId(saved.id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存经销商失败");
    }
  }

  async function createReservation() {
    if (selectedId === null) return;
    if (!reservationForm.sku_code.trim() || !reservationForm.quantity.trim()) {
      setError("SKU 和预留数量必须填写");
      return;
    }
    setError("");
    try {
      await request("/api/v1/foreign-trade/b2b/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dealer_id: selectedId,
          ...reservationForm,
          expires_at: reservationForm.expires_at ? new Date(reservationForm.expires_at).toISOString() : null,
        }),
      });
      setReservationForm({
        sku_code: "",
        quantity: "",
        expires_at: defaultExpiry(),
        reservation_kind: "quote",
        reference_no: "",
        source: "manual",
        shopify_draft_order_id: "",
        note: "",
      });
      await loadDealerData(selectedId);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "库存预留失败");
    }
  }

  async function releaseReservation(id: number) {
    if (selectedId === null) return;
    await request(`/api/v1/foreign-trade/b2b/reservations/${id}/release`, { method: "POST" });
    await loadDealerData(selectedId);
  }

  async function extendReservation(id: number) {
    if (selectedId === null) return;
    const expiresAt = window.prompt("新的锁定到期时间（例如 2026-09-25T18:00）", defaultExpiry());
    if (!expiresAt) return;
    try {
      await request(`/api/v1/foreign-trade/b2b/reservations/${id}/extend`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expires_at: new Date(expiresAt).toISOString() }),
      });
      await loadDealerData(selectedId);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "延长库存失败");
    }
  }

  const reservedTotal = reservations
    .filter((row) => row.effectiveStatus === "active")
    .reduce((sum, row) => sum + Number(row.quantity || 0), 0);

  return (
    <div className="min-h-full bg-slate-50/70 px-5 py-5">
      <div className="mx-auto max-w-[1580px]">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-blue-500">FOREIGN TRADE · B2B</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">B2B 客户</h1>
            <p className="mt-1 text-sm text-slate-500">客户是主对象；报价、专属库存、订单和 Shopify Company 都围绕经销商管理。</p>
          </div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索公司 / 联系人 / 邮箱" className="w-72 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-blue-400" />
        </div>

        {error && <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

        <div className="mt-5 grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="space-y-4">
            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-800">{editing ? "编辑经销商" : "新增经销商"}</h2>
                {editing && <button onClick={() => editDealer()} className="text-xs text-slate-400">取消编辑</button>}
              </div>
              <div className="mt-3 space-y-2.5">
                <Field label="客户编码"><input className={inputClass} value={dealerForm.code} onChange={(e) => setDealerForm({ ...dealerForm, code: e.target.value })} placeholder="DE-MUELLER-001" /></Field>
                <Field label="公司名称"><input className={inputClass} value={dealerForm.company_name} onChange={(e) => setDealerForm({ ...dealerForm, company_name: e.target.value })} /></Field>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="国家"><input className={inputClass} value={dealerForm.country} onChange={(e) => setDealerForm({ ...dealerForm, country: e.target.value })} placeholder="DE" /></Field>
                  <Field label="区域"><input className={inputClass} value={dealerForm.region} onChange={(e) => setDealerForm({ ...dealerForm, region: e.target.value })} placeholder="Berlin" /></Field>
                </div>
                <Field label="联系人"><input className={inputClass} value={dealerForm.contact_name} onChange={(e) => setDealerForm({ ...dealerForm, contact_name: e.target.value })} /></Field>
                <Field label="邮箱"><input className={inputClass} value={dealerForm.email} onChange={(e) => setDealerForm({ ...dealerForm, email: e.target.value })} /></Field>
                <Field label="Shopify Company ID"><input className={inputClass} value={dealerForm.shopify_company_id} onChange={(e) => setDealerForm({ ...dealerForm, shopify_company_id: e.target.value })} /></Field>
                <Field label="Shopify Company Location ID"><input className={inputClass} value={dealerForm.shopify_company_location_id} onChange={(e) => setDealerForm({ ...dealerForm, shopify_company_location_id: e.target.value })} /></Field>
                <button onClick={saveDealer} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white">保存客户</button>
              </div>
            </section>

            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-slate-800">经销商客户</div>
              <div className="max-h-[520px] overflow-y-auto">
                {dealers.length === 0 ? <div className="p-8 text-center text-xs text-slate-400">还没有 B2B 客户</div> : dealers.map((row) => (
                  <button key={row.id} onClick={() => setSelectedId(row.id)} className={`block w-full border-b border-slate-100 px-4 py-3 text-left transition ${selectedId === row.id ? "bg-blue-50" : "hover:bg-slate-50"}`}>
                    <div className="flex items-start justify-between gap-2">
                      <div><div className="text-sm font-semibold text-slate-800">{row.companyName}</div><div className="mt-0.5 text-[11px] text-slate-400">{row.code} · {row.country || "未设置国家"}</div></div>
                      <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[10px] text-slate-500">{row.dealerLevel}</span>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">{row.contactName || "未填写联系人"} {row.email ? `· ${row.email}` : ""}</div>
                  </button>
                ))}
              </div>
            </section>
          </aside>

          <main className="min-w-0 space-y-4">
            {!selected ? (
              <section className="rounded-xl border border-dashed border-slate-300 bg-white py-24 text-center text-sm text-slate-400">选择一个经销商查看客户工作台</section>
            ) : (
              <>
                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2"><h2 className="text-lg font-semibold text-slate-900">{selected.companyName}</h2><span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">{selected.status}</span></div>
                      <div className="mt-1 text-xs text-slate-500">{selected.country || "—"} {selected.region ? `· ${selected.region}` : ""} · {selected.contactName || "未设置联系人"}</div>
                    </div>
                    <button onClick={() => editDealer(selected)} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">编辑客户</button>
                  </div>
                  <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                    <Metric label="有效专属预留" value={reservedTotal.toLocaleString("zh-CN")} hint="仅当前客户" />
                    <Metric label="Shopify Company" value={selected.shopifyCompanyId ? "已映射" : "未映射"} hint={selected.shopifyCompanyId || "后续可自动同步"} />
                    <Metric label="Company Location" value={selected.shopifyCompanyLocationId ? "已映射" : "未映射"} hint={selected.shopifyCompanyLocationId || "控制客户身份与库存视图"} />
                    <Metric label="币种" value={selected.currency} hint="经销报价默认币种" />
                  </div>
                </section>

                <section className="rounded-xl border border-slate-200 bg-white p-4">
                  <div className="flex items-center justify-between"><div><h2 className="text-sm font-semibold text-slate-800">新增专属库存预留</h2><p className="mt-1 text-xs text-slate-400">默认 48 小时；到期后自动退出可用预留计算，不修改真实库存。</p></div></div>
                  <div className="mt-3 grid gap-2 lg:grid-cols-6">
                    <Field label="SKU"><input className={inputClass} value={reservationForm.sku_code} onChange={(e) => setReservationForm({ ...reservationForm, sku_code: e.target.value })} placeholder="SKU 编码" /></Field>
                    <Field label="数量"><input className={inputClass} inputMode="decimal" value={reservationForm.quantity} onChange={(e) => setReservationForm({ ...reservationForm, quantity: e.target.value })} /></Field>
                    <Field label="来源"><select className={inputClass} value={reservationForm.reservation_kind} onChange={(e) => setReservationForm({ ...reservationForm, reservation_kind: e.target.value })}><option value="quote">报价单</option><option value="order">B2B订单</option><option value="manual">手工预留</option></select></Field>
                    <Field label="报价/订单号"><input className={inputClass} value={reservationForm.reference_no} onChange={(e) => setReservationForm({ ...reservationForm, reference_no: e.target.value })} /></Field>
                    <Field label="锁定到期"><input type="datetime-local" className={inputClass} value={reservationForm.expires_at} onChange={(e) => setReservationForm({ ...reservationForm, expires_at: e.target.value })} /></Field>
                    <div className="flex items-end"><button onClick={createReservation} className="w-full rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white">锁定库存</button></div>
                  </div>
                </section>

                <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                  <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><div><h2 className="text-sm font-semibold text-slate-800">该客户可见库存</h2><p className="mt-0.5 text-[11px] text-slate-400">不会展示其他经销商的预留数量。</p></div><button onClick={() => loadDealerData(selected.id)} className="text-xs text-blue-600">刷新</button></div>
                  {inventory.length === 0 ? <div className="p-10 text-center text-sm text-slate-400">当前没有可展示库存</div> : (
                    <div className="overflow-x-auto"><table className="min-w-[780px] w-full text-left text-xs">
                      <thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">SKU</th><th>真实库存</th><th>公共可售</th><th>给他的专属预留</th><th>该客户可下单</th></tr></thead>
                      <tbody>{inventory.map((row) => <tr key={row.skuId} className="border-t border-slate-100"><td className="px-4 py-3"><div className="font-semibold text-slate-800">{row.skuCode}</div><div className="text-slate-400">{row.skuName || "—"}</div></td><td>{Number(row.actualStock).toLocaleString()}</td><td>{Number(row.publicAvailable).toLocaleString()}</td><td className="font-semibold text-blue-600">{Number(row.dealerReserved).toLocaleString()}</td><td className="text-base font-semibold text-slate-900">{Number(row.availableToDealer).toLocaleString()}</td></tr>)}</tbody>
                    </table></div>
                  )}
                </section>

                <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
                  <div className="border-b border-slate-100 px-4 py-3"><h2 className="text-sm font-semibold text-slate-800">库存预留记录</h2></div>
                  {reservations.length === 0 ? <div className="p-10 text-center text-sm text-slate-400">该客户还没有库存预留</div> : (
                    <div className="overflow-x-auto"><table className="min-w-[980px] w-full text-left text-xs">
                      <thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">SKU</th><th>数量</th><th>来源</th><th>关联单号</th><th>到期时间</th><th>状态</th><th className="pr-4 text-right">操作</th></tr></thead>
                      <tbody>{reservations.map((row) => <tr key={row.id} className="border-t border-slate-100"><td className="px-4 py-3"><div className="font-medium text-slate-800">{row.skuCode}</div><div className="text-slate-400">{row.skuName || "—"}</div></td><td>{Number(row.quantity).toLocaleString()}</td><td>{row.reservationKind}</td><td>{row.referenceNo || "—"}</td><td>{row.expiresAt ? new Date(row.expiresAt).toLocaleString("zh-CN", { hour12: false }) : "长期"}</td><td><ReservationStatus value={row.effectiveStatus} /></td><td className="pr-4 text-right">{row.effectiveStatus === "active" && <><button onClick={() => extendReservation(row.id)} className="text-blue-600">延长</button><button onClick={() => releaseReservation(row.id)} className="ml-3 text-rose-500">释放</button></>}{row.effectiveStatus === "expired" && <button onClick={() => extendReservation(row.id)} className="text-blue-600">重新锁定</button>}</td></tr>)}</tbody>
                    </table></div>
                  )}
                </section>
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">{label}</span>{children}</label>;
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <div className="rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-3"><div className="text-[11px] text-slate-500">{label}</div><div className="mt-1 truncate text-lg font-semibold text-slate-900">{value}</div><div className="mt-0.5 truncate text-[10px] text-slate-400">{hint}</div></div>;
}

function ReservationStatus({ value }: { value: string }) {
  const active = value === "active";
  const expired = value === "expired";
  return <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${active ? "border-emerald-200 bg-emerald-50 text-emerald-700" : expired ? "border-amber-200 bg-amber-50 text-amber-700" : "border-slate-200 bg-slate-50 text-slate-500"}`}>{active ? "锁定中" : expired ? "已到期" : value === "released" ? "已释放" : value}</span>;
}
