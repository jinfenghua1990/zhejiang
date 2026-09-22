"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

export type ForeignTradeMode = "orders" | "channels" | "sku" | "fulfillment" | "alsvid";

type Channel = {
  id: number;
  code: string;
  name: string;
  channelType: string;
  brand: string;
  currency: string;
  countries: string[];
  enabled: boolean;
  connected: boolean;
  note: string;
};

type DealerOption = {
  id: number;
  code: string;
  companyName: string;
  status: string;
};

type SkuMapping = {
  id: number;
  channelCode: string;
  externalSku: string;
  internalSku: string;
  productName: string;
  status: string;
  note: string;
};

type Order = {
  id: number;
  channelCode: string;
  externalOrderNo: string;
  businessMode: "b2c" | "b2b";
  dealerId: number | null;
  dealerName: string;
  brand: string;
  country: string;
  currency: string;
  grossAmount: string;
  discountAmount: string;
  shippingIncome: string;
  paidAmount: string;
  refundAmount: string;
  paymentFee: string;
  purchaseCost: string;
  logisticsCost: string;
  exchangeRateToCny: string;
  profit: string;
  profitCny: string;
  status: string;
  procurementStatus: string;
  fulfillmentStatus: string;
  paymentStatus: string;
  customerName: string;
  customerEmail: string;
  shipTo: string;
  carrier: string;
  trackingNo: string;
  orderedAt: string | null;
  paidAt: string | null;
  shippedAt: string | null;
  items: Record<string, unknown>[];
  note: string;
};

const STATUS_LABEL: Record<string, string> = {
  pending: "待处理",
  confirmed: "已确认",
  procuring: "采购中",
  fulfilling: "履约中",
  shipped: "已发货",
  completed: "已完成",
  cancelled: "已取消",
  refunded: "已退款",
  done: "已完成",
  in_progress: "处理中",
  unpaid: "未收款",
  paid: "已收款",
  matched: "已匹配",
};

function money(value: string | number, currency = "EUR") {
  const number = Number(value || 0);
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 2 }).format(Number.isFinite(number) ? number : 0);
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || `请求失败（${res.status}）`);
  }
  return res.json();
}

const EMPTY_ORDER = {
  channel_code: "",
  external_order_no: "",
  business_mode: "b2c" as "b2c" | "b2b",
  dealer_id: null as number | null,
  brand: "",
  country: "",
  currency: "EUR",
  gross_amount: "0",
  discount_amount: "0",
  shipping_income: "0",
  paid_amount: "0",
  refund_amount: "0",
  payment_fee: "0",
  purchase_cost: "0",
  logistics_cost: "0",
  exchange_rate_to_cny: "1",
  status: "pending",
  procurement_status: "pending",
  fulfillment_status: "pending",
  payment_status: "unpaid",
  customer_name: "",
  customer_email: "",
  ship_to: "",
  carrier: "",
  tracking_no: "",
  ordered_at: null as string | null,
  paid_at: null as string | null,
  shipped_at: null as string | null,
  items: [] as Record<string, unknown>[],
  note: "",
};

type OrderDraft = typeof EMPTY_ORDER;

function orderToDraft(row: Order): OrderDraft {
  return {
    channel_code: row.channelCode,
    external_order_no: row.externalOrderNo,
    business_mode: row.businessMode,
    dealer_id: row.dealerId,
    brand: row.brand,
    country: row.country,
    currency: row.currency,
    gross_amount: row.grossAmount,
    discount_amount: row.discountAmount,
    shipping_income: row.shippingIncome,
    paid_amount: row.paidAmount,
    refund_amount: row.refundAmount,
    payment_fee: row.paymentFee,
    purchase_cost: row.purchaseCost,
    logistics_cost: row.logisticsCost,
    exchange_rate_to_cny: row.exchangeRateToCny,
    status: row.status,
    procurement_status: row.procurementStatus,
    fulfillment_status: row.fulfillmentStatus,
    payment_status: row.paymentStatus,
    customer_name: row.customerName,
    customer_email: row.customerEmail,
    ship_to: row.shipTo,
    carrier: row.carrier,
    tracking_no: row.trackingNo,
    ordered_at: row.orderedAt,
    paid_at: row.paidAt,
    shipped_at: row.shippedAt,
    items: row.items || [],
    note: row.note,
  };
}

function Pill({ value }: { value: string }) {
  const warm = value === "pending" || value === "unpaid";
  const good = value === "done" || value === "completed" || value === "shipped" || value === "paid" || value === "matched";
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium ${
      good ? "border-emerald-200 bg-emerald-50 text-emerald-700" :
      warm ? "border-amber-200 bg-amber-50 text-amber-700" :
      "border-blue-200 bg-blue-50 text-blue-700"
    }`}>
      {STATUS_LABEL[value] || value || "—"}
    </span>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="py-14 text-center text-sm text-slate-400">{text}</div>;
}

export default function ForeignTradeWorkbench({ mode }: { mode: ForeignTradeMode }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [dealers, setDealers] = useState<DealerOption[]>([]);
  const [mappings, setMappings] = useState<SkuMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [businessFilter, setBusinessFilter] = useState<"all" | "b2c" | "b2b">("all");
  const [editingOrder, setEditingOrder] = useState<Order | null>(null);
  const [orderDraft, setOrderDraft] = useState<OrderDraft>({ ...EMPTY_ORDER });
  const [orderFormOpen, setOrderFormOpen] = useState(false);
  const [channelForm, setChannelForm] = useState({ code: "", name: "", channel_type: "manual", brand: "", currency: "EUR", countries: "", enabled: true, connected: false, note: "" });
  const [editingChannel, setEditingChannel] = useState<Channel | null>(null);
  const [skuForm, setSkuForm] = useState({ channel_code: "", external_sku: "", internal_sku: "", product_name: "", status: "pending", note: "" });
  const [editingSku, setEditingSku] = useState<SkuMapping | null>(null);

  const title = {
    orders: "外贸订单",
    channels: "渠道管理",
    sku: "海外 SKU 映射",
    fulfillment: "履约中心",
    alsvid: "ALSVID",
  }[mode];

  const subtitle = {
    orders: "所有海外订单统一进入这里，再向采购、履约和财务流转。",
    channels: "管理 Shopify、B2B、代发和手工渠道；后续 API 接入也挂在渠道上。",
    sku: "把海外渠道 SKU 映射到中台 SKU，避免采购和成本核算认错货。",
    fulfillment: "处理待采购、待发货、物流单号与完成状态。",
    alsvid: "ALSVID 德国 / 奥地利业务视图，订单仍使用同一外贸订单底座。",
  }[mode];

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      if (mode === "channels") {
        const data = await api<{ items: Channel[] }>("/api/v1/foreign-trade/channels");
        setChannels(data.items);
      } else if (mode === "sku") {
        const data = await api<{ items: SkuMapping[] }>(`/api/v1/foreign-trade/sku-mappings?q=${encodeURIComponent(q)}`);
        setMappings(data.items);
      } else {
        const brand = mode === "alsvid" ? "&brand=ALSVID" : "";
        const business = businessFilter === "all" ? "" : "&business_mode=" + businessFilter;
        const [orderData, dealerData] = await Promise.all([
          api<{ items: Order[] }>(`/api/v1/foreign-trade/orders?q=${encodeURIComponent(q)}${brand}${business}`),
          api<{ items: DealerOption[] }>("/api/v1/foreign-trade/b2b/dealers"),
        ]);
        setOrders(orderData.items);
        setDealers(dealerData.items.filter((row) => row.status === "active"));
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [businessFilter, mode, q]);

  useEffect(() => {
    const timer = window.setTimeout(load, 120);
    return () => window.clearTimeout(timer);
  }, [load]);

  const visibleOrders = useMemo(() => {
    if (mode === "fulfillment") return orders.filter((row) => !["completed", "cancelled", "refunded"].includes(row.status));
    return orders;
  }, [mode, orders]);

  async function saveOrder() {
    if (!orderDraft.channel_code.trim() || !orderDraft.external_order_no.trim()) {
      setError("渠道编码和订单号必须填写");
      return;
    }
    if (orderDraft.business_mode === "b2b" && orderDraft.dealer_id === null) {
      setError("B2B 订单必须选择经销商");
      return;
    }
    setError("");
    const url = editingOrder ? `/api/v1/foreign-trade/orders/${editingOrder.id}` : "/api/v1/foreign-trade/orders";
    try {
      await api(url, {
        method: editingOrder ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(orderDraft),
      });
      setOrderFormOpen(false);
      setEditingOrder(null);
      setOrderDraft({ ...EMPTY_ORDER });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    }
  }

  async function patchOrder(row: Order, changes: Partial<OrderDraft>) {
    try {
      await api(`/api/v1/foreign-trade/orders/${row.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...orderToDraft(row), ...changes }),
      });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "更新失败");
    }
  }

  async function removeOrder(row: Order) {
    if (!window.confirm(`删除外贸订单 ${row.externalOrderNo}？`)) return;
    await api(`/api/v1/foreign-trade/orders/${row.id}`, { method: "DELETE" });
    await load();
  }

  function openOrder(row?: Order) {
    if (row) {
      setEditingOrder(row);
      setOrderDraft(orderToDraft(row));
    } else {
      setEditingOrder(null);
      setOrderDraft({ ...EMPTY_ORDER, brand: mode === "alsvid" ? "ALSVID" : "" });
    }
    setOrderFormOpen(true);
  }

  async function saveChannel() {
    if (!channelForm.code.trim() || !channelForm.name.trim()) return setError("渠道编码和名称必须填写");
    const body = {
      ...channelForm,
      countries: channelForm.countries.split(/[,，\s]+/).map((v) => v.trim()).filter(Boolean),
    };
    try {
      await api(editingChannel ? `/api/v1/foreign-trade/channels/${editingChannel.id}` : "/api/v1/foreign-trade/channels", {
        method: editingChannel ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setEditingChannel(null);
      setChannelForm({ code: "", name: "", channel_type: "manual", brand: "", currency: "EUR", countries: "", enabled: true, connected: false, note: "" });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    }
  }

  async function saveSku() {
    if (!skuForm.channel_code.trim() || !skuForm.external_sku.trim()) return setError("渠道编码和海外 SKU 必须填写");
    try {
      await api(editingSku ? `/api/v1/foreign-trade/sku-mappings/${editingSku.id}` : "/api/v1/foreign-trade/sku-mappings", {
        method: editingSku ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(skuForm),
      });
      setEditingSku(null);
      setSkuForm({ channel_code: "", external_sku: "", internal_sku: "", product_name: "", status: "pending", note: "" });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    }
  }

  if (mode === "channels") {
    return (
      <PageShell title={title} subtitle={subtitle} error={error}>
        <div className="grid gap-4 xl:grid-cols-[360px_1fr]">
          <section className="rounded-xl border border-slate-200 bg-white p-4">
            <h2 className="text-sm font-semibold text-slate-800">{editingChannel ? "编辑渠道" : "新增渠道"}</h2>
            <div className="mt-3 space-y-3">
              <Field label="渠道编码"><input value={channelForm.code} onChange={(e) => setChannelForm({ ...channelForm, code: e.target.value })} placeholder="shopify-de" className="input" /></Field>
              <Field label="渠道名称"><input value={channelForm.name} onChange={(e) => setChannelForm({ ...channelForm, name: e.target.value })} placeholder="Alsvid Shopify 德国站" className="input" /></Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="类型"><select value={channelForm.channel_type} onChange={(e) => setChannelForm({ ...channelForm, channel_type: e.target.value })} className="input"><option value="manual">手工</option><option value="shopify">Shopify</option><option value="b2b">B2B</option><option value="dropship">一件代发</option></select></Field>
                <Field label="币种"><input value={channelForm.currency} onChange={(e) => setChannelForm({ ...channelForm, currency: e.target.value.toUpperCase() })} className="input" /></Field>
              </div>
              <Field label="品牌"><input value={channelForm.brand} onChange={(e) => setChannelForm({ ...channelForm, brand: e.target.value })} placeholder="Alsvid" className="input" /></Field>
              <Field label="国家"><input value={channelForm.countries} onChange={(e) => setChannelForm({ ...channelForm, countries: e.target.value })} placeholder="DE, AT" className="input" /></Field>
              <div className="flex gap-4 text-xs text-slate-600">
                <label className="flex items-center gap-2"><input type="checkbox" checked={channelForm.enabled} onChange={(e) => setChannelForm({ ...channelForm, enabled: e.target.checked })} />启用</label>
                <label className="flex items-center gap-2"><input type="checkbox" checked={channelForm.connected} onChange={(e) => setChannelForm({ ...channelForm, connected: e.target.checked })} />已连接</label>
              </div>
              <button onClick={saveChannel} className="w-full rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white">保存渠道</button>
            </div>
          </section>
          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            {loading ? <Empty text="正在加载渠道…" /> : channels.length === 0 ? <Empty text="还没有渠道，先在左侧创建第一个渠道。" /> : (
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">渠道</th><th>类型</th><th>品牌</th><th>国家/币种</th><th>连接</th><th className="pr-4 text-right">操作</th></tr></thead>
                <tbody>{channels.map((row) => <tr key={row.id} className="border-t border-slate-100">
                  <td className="px-4 py-3"><div className="font-medium text-slate-800">{row.name}</div><div className="text-slate-400">{row.code}</div></td>
                  <td>{row.channelType}</td><td>{row.brand || "—"}</td><td>{row.countries.join(" / ") || "—"} · {row.currency}</td>
                  <td><Pill value={row.connected ? "done" : "pending"} /></td>
                  <td className="pr-4 text-right"><button className="text-blue-600" onClick={() => { setEditingChannel(row); setChannelForm({ code: row.code, name: row.name, channel_type: row.channelType, brand: row.brand, currency: row.currency, countries: row.countries.join(", "), enabled: row.enabled, connected: row.connected, note: row.note }); }}>编辑</button><button className="ml-3 text-rose-500" onClick={async () => { if (window.confirm("删除该渠道？")) { await api(`/api/v1/foreign-trade/channels/${row.id}`, { method: "DELETE" }); await load(); } }}>删除</button></td>
                </tr>)}</tbody>
              </table>
            )}
          </section>
        </div>
      </PageShell>
    );
  }

  if (mode === "sku") {
    return (
      <PageShell title={title} subtitle={subtitle} error={error}>
        <section className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="grid gap-2 md:grid-cols-6">
            <input value={skuForm.channel_code} onChange={(e) => setSkuForm({ ...skuForm, channel_code: e.target.value })} placeholder="渠道编码" className="input" />
            <input value={skuForm.external_sku} onChange={(e) => setSkuForm({ ...skuForm, external_sku: e.target.value })} placeholder="海外 SKU" className="input" />
            <input value={skuForm.internal_sku} onChange={(e) => setSkuForm({ ...skuForm, internal_sku: e.target.value })} placeholder="中台 SKU" className="input" />
            <input value={skuForm.product_name} onChange={(e) => setSkuForm({ ...skuForm, product_name: e.target.value })} placeholder="商品名称" className="input" />
            <select value={skuForm.status} onChange={(e) => setSkuForm({ ...skuForm, status: e.target.value })} className="input"><option value="pending">待匹配</option><option value="matched">已匹配</option></select>
            <button onClick={saveSku} className="rounded-lg bg-slate-900 px-3 py-2 text-sm font-medium text-white">{editingSku ? "保存修改" : "新增映射"}</button>
          </div>
        </section>
        <div className="mt-3 flex justify-end"><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索 SKU / 商品名称" className="input w-64" /></div>
        <section className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {loading ? <Empty text="正在加载 SKU 映射…" /> : mappings.length === 0 ? <Empty text="暂无 SKU 映射。" /> : (
            <table className="w-full text-left text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">渠道</th><th>海外 SKU</th><th>中台 SKU</th><th>商品</th><th>状态</th><th className="pr-4 text-right">操作</th></tr></thead>
              <tbody>{mappings.map((row) => <tr key={row.id} className="border-t border-slate-100"><td className="px-4 py-3">{row.channelCode}</td><td className="font-medium">{row.externalSku}</td><td>{row.internalSku || "—"}</td><td>{row.productName || "—"}</td><td><Pill value={row.status} /></td><td className="pr-4 text-right"><button className="text-blue-600" onClick={() => { setEditingSku(row); setSkuForm({ channel_code: row.channelCode, external_sku: row.externalSku, internal_sku: row.internalSku, product_name: row.productName, status: row.status, note: row.note }); }}>编辑</button><button className="ml-3 text-rose-500" onClick={async () => { if (window.confirm("删除该 SKU 映射？")) { await api(`/api/v1/foreign-trade/sku-mappings/${row.id}`, { method: "DELETE" }); await load(); } }}>删除</button></td></tr>)}</tbody>
            </table>
          )}
        </section>
      </PageShell>
    );
  }

  return (
    <PageShell title={title} subtitle={subtitle} error={error}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索订单号 / 客户 / 运单号" className="input w-72" />
          <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5">
            {(["all", "b2c", "b2b"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setBusinessFilter(value)}
                className={"rounded-md px-2.5 py-1.5 text-[11px] font-medium " + (businessFilter === value ? "bg-blue-50 text-blue-700" : "text-slate-500 hover:bg-slate-50")}
              >
                {value === "all" ? "全部" : value.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <button onClick={() => openOrder()} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white">+ 新增外贸订单</button>
      </div>

      {orderFormOpen && (
        <section className="mt-4 rounded-xl border border-blue-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between"><h2 className="text-sm font-semibold text-slate-800">{editingOrder ? `编辑订单 · ${editingOrder.externalOrderNo}` : "新增外贸订单"}</h2><button onClick={() => setOrderFormOpen(false)} className="text-xs text-slate-400">关闭</button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <Field label="业务类型">
              <select
                className="input"
                value={orderDraft.business_mode}
                onChange={(e) => {
                  const next = e.target.value as "b2c" | "b2b";
                  setOrderDraft({ ...orderDraft, business_mode: next, dealer_id: next === "b2c" ? null : orderDraft.dealer_id });
                }}
              >
                <option value="b2c">B2C · 零售客户</option>
                <option value="b2b">B2B · 经销商</option>
              </select>
            </Field>
            {orderDraft.business_mode === "b2b" ? (
              <Field label="经销商">
                <select
                  className="input"
                  value={orderDraft.dealer_id ?? ""}
                  onChange={(e) => {
                    const dealerId = e.target.value ? Number(e.target.value) : null;
                    const dealer = dealers.find((row) => row.id === dealerId);
                    setOrderDraft({
                      ...orderDraft,
                      dealer_id: dealerId,
                      customer_name: dealer?.companyName || orderDraft.customer_name,
                    });
                  }}
                >
                  <option value="">选择经销商</option>
                  {dealers.map((row) => <option key={row.id} value={row.id}>{row.companyName} · {row.code}</option>)}
                </select>
              </Field>
            ) : (
              <Field label="客户"><input className="input" value={orderDraft.customer_name} onChange={(e) => setOrderDraft({ ...orderDraft, customer_name: e.target.value })} /></Field>
            )}
            <Field label="渠道编码"><input className="input" value={orderDraft.channel_code} onChange={(e) => setOrderDraft({ ...orderDraft, channel_code: e.target.value })} /></Field>
            <Field label="渠道订单号"><input className="input" value={orderDraft.external_order_no} onChange={(e) => setOrderDraft({ ...orderDraft, external_order_no: e.target.value })} /></Field>
            <Field label="品牌"><input className="input" value={orderDraft.brand} onChange={(e) => setOrderDraft({ ...orderDraft, brand: e.target.value })} /></Field>
            <Field label="国家"><input className="input" value={orderDraft.country} onChange={(e) => setOrderDraft({ ...orderDraft, country: e.target.value })} /></Field>
            <Field label="币种"><input className="input" value={orderDraft.currency} onChange={(e) => setOrderDraft({ ...orderDraft, currency: e.target.value.toUpperCase() })} /></Field>
            <Field label="实收金额"><input className="input" inputMode="decimal" value={orderDraft.paid_amount} onChange={(e) => setOrderDraft({ ...orderDraft, paid_amount: e.target.value })} /></Field>
            <Field label="采购成本"><input className="input" inputMode="decimal" value={orderDraft.purchase_cost} onChange={(e) => setOrderDraft({ ...orderDraft, purchase_cost: e.target.value })} /></Field>
            <Field label="物流成本"><input className="input" inputMode="decimal" value={orderDraft.logistics_cost} onChange={(e) => setOrderDraft({ ...orderDraft, logistics_cost: e.target.value })} /></Field>
            <Field label="支付手续费"><input className="input" inputMode="decimal" value={orderDraft.payment_fee} onChange={(e) => setOrderDraft({ ...orderDraft, payment_fee: e.target.value })} /></Field>
            <Field label="退款金额"><input className="input" inputMode="decimal" value={orderDraft.refund_amount} onChange={(e) => setOrderDraft({ ...orderDraft, refund_amount: e.target.value })} /></Field>
            <Field label="兑人民币汇率"><input className="input" inputMode="decimal" value={orderDraft.exchange_rate_to_cny} onChange={(e) => setOrderDraft({ ...orderDraft, exchange_rate_to_cny: e.target.value })} /></Field>
            <Field label="物流公司"><input className="input" value={orderDraft.carrier} onChange={(e) => setOrderDraft({ ...orderDraft, carrier: e.target.value })} /></Field>
            <Field label="运单号"><input className="input" value={orderDraft.tracking_no} onChange={(e) => setOrderDraft({ ...orderDraft, tracking_no: e.target.value })} /></Field>
            <Field label="收款状态"><select className="input" value={orderDraft.payment_status} onChange={(e) => setOrderDraft({ ...orderDraft, payment_status: e.target.value })}><option value="unpaid">未收款</option><option value="paid">已收款</option></select></Field>
            <Field label="订单状态"><select className="input" value={orderDraft.status} onChange={(e) => setOrderDraft({ ...orderDraft, status: e.target.value })}><option value="pending">待处理</option><option value="confirmed">已确认</option><option value="procuring">采购中</option><option value="fulfilling">履约中</option><option value="shipped">已发货</option><option value="completed">已完成</option><option value="cancelled">已取消</option><option value="refunded">已退款</option></select></Field>
          </div>
          <div className="mt-4 flex justify-end gap-2"><button onClick={() => setOrderFormOpen(false)} className="rounded-lg border px-4 py-2 text-sm text-slate-600">取消</button><button onClick={saveOrder} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white">保存订单</button></div>
        </section>
      )}

      <section className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
        {loading ? <Empty text="正在加载外贸订单…" /> : visibleOrders.length === 0 ? <Empty text="暂无外贸订单，可以先手工新增；后续 Shopify 会自动写入这里。" /> : (
          <table className="min-w-[1180px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">订单</th><th>渠道 / 品牌</th><th>国家</th><th>实收</th><th>采购</th><th>履约</th><th>收款</th><th>利润</th><th>物流</th><th className="pr-4 text-right">操作</th></tr></thead>
            <tbody>{visibleOrders.map((row) => <tr key={row.id} className="border-t border-slate-100 align-top">
              <td className="px-4 py-3">
                <div className="flex items-center gap-2">
                  <div className="font-semibold text-slate-800">{row.externalOrderNo}</div>
                  <span className={"rounded px-1.5 py-0.5 text-[9px] font-semibold " + (row.businessMode === "b2b" ? "bg-violet-50 text-violet-700" : "bg-sky-50 text-sky-700")}>{row.businessMode.toUpperCase()}</span>
                </div>
                <div className="mt-1 flex items-center gap-2"><Pill value={row.status} />{row.businessMode === "b2b" && row.dealerName && <span className="max-w-36 truncate text-[10px] text-slate-400" title={row.dealerName}>{row.dealerName}</span>}</div>
              </td>
              <td className="py-3"><div>{row.channelCode}</div><div className="text-slate-400">{row.brand || "—"}</div></td>
              <td>{row.country || "—"}</td><td>{money(row.paidAmount, row.currency)}</td><td><Pill value={row.procurementStatus} /></td><td><Pill value={row.fulfillmentStatus} /></td><td><Pill value={row.paymentStatus} /></td>
              <td><div className={Number(row.profit) >= 0 ? "font-semibold text-emerald-600" : "font-semibold text-rose-600"}>{money(row.profit, row.currency)}</div><div className="text-slate-400">≈ ¥{Number(row.profitCny || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}</div></td>
              <td><div>{row.carrier || "—"}</div><div className="text-slate-400">{row.trackingNo || "—"}</div></td>
              <td className="pr-4 py-3 text-right whitespace-nowrap">
                {row.procurementStatus === "pending" && <button className="text-blue-600" onClick={() => patchOrder(row, { procurement_status: "in_progress", status: "procuring" })}>进入采购</button>}
                {row.procurementStatus !== "done" && row.procurementStatus !== "pending" && <button className="text-blue-600" onClick={() => patchOrder(row, { procurement_status: "done", fulfillment_status: "in_progress", status: "fulfilling" })}>采购完成</button>}
                {row.fulfillmentStatus !== "shipped" && row.procurementStatus === "done" && <button className="ml-3 text-blue-600" onClick={() => patchOrder(row, { fulfillment_status: "shipped", status: "shipped", shipped_at: new Date().toISOString() })}>标记发货</button>}
                {row.fulfillmentStatus === "shipped" && row.status !== "completed" && <button className="ml-3 text-emerald-600" onClick={() => patchOrder(row, { status: "completed" })}>完成</button>}
                <button className="ml-3 text-slate-600" onClick={() => openOrder(row)}>编辑</button>
                <button className="ml-3 text-rose-500" onClick={() => removeOrder(row)}>删除</button>
              </td>
            </tr>)}</tbody>
          </table>
        )}
      </section>
    </PageShell>
  );
}

function PageShell({ title, subtitle, error, children }: { title: string; subtitle: string; error: string; children: React.ReactNode }) {
  return (
    <div className="min-h-full bg-slate-50/70 px-5 py-5">
      <div className="mx-auto max-w-[1560px]">
        <div><div className="text-[11px] font-medium uppercase tracking-[0.14em] text-blue-500">FOREIGN TRADE</div><h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">{title}</h1><p className="mt-1 text-sm text-slate-500">{subtitle}</p></div>
        {error && <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}
        <div className="mt-5">{children}</div>
      </div>
      <style jsx global>{`.input{width:100%;border:1px solid rgb(226 232 240);border-radius:.5rem;background:white;padding:.5rem .65rem;font-size:.8rem;line-height:1.25rem;color:rgb(51 65 85);outline:none}.input:focus{border-color:rgb(96 165 250);box-shadow:0 0 0 2px rgb(219 234 254)}`}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">{label}</span>{children}</label>;
}
