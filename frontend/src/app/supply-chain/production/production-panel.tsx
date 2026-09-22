"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { authenticatedFetch } from "@/lib/api";

type SkuOption = {
  skuId: number;
  skuCode: string;
  skuName: string;
  goodsName: string;
  unit: string;
  suggestedReplenishment: string | null;
};

type ProductionItem = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  unit: string;
  quantity: string;
  completedQty: string;
};

type ProductionMaterial = {
  id: number;
  consumableId: number;
  code: string;
  name: string;
  unit: string;
  requiredQty: string;
  reservedQty: string;
  dispatchedQty: string;
  factoryReceivedQty: string;
  consumedQty: string;
  shortageQty: string;
  state: "shortage" | "reserved" | "transit" | "factory" | "consumed";
};

type ProductionOrder = {
  id: number;
  orderNo: string;
  factoryName: string;
  status: string;
  plannedStartDate: string | null;
  expectedDeliveryDate: string | null;
  sourceType: string;
  note: string;
  createdBy: string;
  createdAt: string | null;
  itemCount: number;
  materialCount: number;
  materialShortageCount: number;
  items: ProductionItem[];
  materials: ProductionMaterial[];
};

type FormItem = { key: number; skuId: string; quantity: string };

type ProductionConfig = {
  defaultFactory: string;
  salesWindowDays: number;
  leadDays: number;
  safetyDays: number;
};

const CONFIG_KEY = "supply-chain-production-config-v1";
const DEFAULT_CONFIG: ProductionConfig = {
  defaultFactory: "",
  salesWindowDays: 30,
  leadDays: 14,
  safetyDays: 7,
};

const STATUS: Record<string, { label: string; cls: string }> = {
  planned: { label: "计划中", cls: "bg-slate-100 text-slate-700" },
  confirmed: { label: "已确认", cls: "bg-blue-50 text-blue-700" },
  producing: { label: "生产中", cls: "bg-indigo-50 text-indigo-700" },
  produced: { label: "已生产", cls: "bg-violet-50 text-violet-700" },
  shipped: { label: "已发货", cls: "bg-amber-50 text-amber-700" },
  arrived: { label: "已到货", cls: "bg-cyan-50 text-cyan-700" },
  inbound: { label: "部分入库", cls: "bg-teal-50 text-teal-700" },
  completed: { label: "已完成", cls: "bg-emerald-50 text-emerald-700" },
  cancelled: { label: "已取消", cls: "bg-slate-100 text-slate-400" },
};

const MATERIAL_STATE: Record<ProductionMaterial["state"], string> = {
  shortage: "缺料",
  reserved: "已预占",
  transit: "发往工厂",
  factory: "工厂库存",
  consumed: "已消耗",
};

function qty(value: string | null, digits = 1) {
  if (value === null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  if (typeof payload?.detail === "string") return payload.detail;
  return `${fallback}（${response.status}）`;
}

export default function ProductionPanel() {
  const searchParams = useSearchParams();
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [skuOptions, setSkuOptions] = useState<SkuOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [factoryName, setFactoryName] = useState("");
  const [plannedStartDate, setPlannedStartDate] = useState("");
  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState("");
  const [note, setNote] = useState("");
  const [items, setItems] = useState<FormItem[]>([{ key: 1, skuId: "", quantity: "" }]);
  const [config, setConfig] = useState<ProductionConfig>(DEFAULT_CONFIG);
  const [configDraft, setConfigDraft] = useState<ProductionConfig>(DEFAULT_CONFIG);
  const [importing, setImporting] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(CONFIG_KEY);
      if (raw) {
        const saved = JSON.parse(raw) as Partial<ProductionConfig>;
        const next: ProductionConfig = {
          defaultFactory: typeof saved.defaultFactory === "string" ? saved.defaultFactory : "",
          salesWindowDays: Number(saved.salesWindowDays) || 30,
          leadDays: Number(saved.leadDays) || 14,
          safetyDays: Number(saved.safetyDays) >= 0 ? Number(saved.safetyDays) : 7,
        };
        setConfig(next);
        setConfigDraft(next);
        if (next.defaultFactory) setFactoryName(next.defaultFactory);
      }
    } catch {
      // 配置损坏时回退默认值，不阻断生产页面。
    } finally {
      setConfigLoaded(true);
    }
  }, []);

  const load = useCallback(async () => {
    if (!configLoaded) return;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        days: String(config.salesWindowDays),
        lead_days: String(config.leadDays),
        safety_days: String(config.safetyDays),
        limit: "2000",
      });
      const [orderResponse, skuResponse] = await Promise.all([
        authenticatedFetch("/api/v1/supply-chain/production-orders?limit=300", { cache: "no-store" }),
        authenticatedFetch(`/api/v1/supply-chain/replenishment?${params}`, { cache: "no-store" }),
      ]);
      if (!orderResponse.ok) throw new Error(await responseError(orderResponse, "生产单加载失败"));
      if (!skuResponse.ok) throw new Error(await responseError(skuResponse, "SKU 加载失败"));
      const orderPayload = await orderResponse.json();
      const skuPayload = await skuResponse.json();
      setOrders(orderPayload.rows ?? []);
      setSkuOptions(skuPayload.rows ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [config, configLoaded]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // 工作区下必须读本 Tab 冻结的 searchParams：隐藏 Tab 读 window.location.search 会命中别的 Tab
    const skuId = searchParams.get("skuId") ?? "";
    const quantity = searchParams.get("qty") ?? "";
    if (skuId) setItems([{ key: Date.now(), skuId, quantity }]);
  }, [searchParams]);

  const summary = useMemo(() => ({
    active: orders.filter((order) => !["completed", "cancelled"].includes(order.status)).length,
    producing: orders.filter((order) => ["producing", "produced", "shipped", "arrived", "inbound"].includes(order.status)).length,
    shortage: orders.filter((order) => order.materialShortageCount > 0 && order.status !== "cancelled").length,
  }), [orders]);

  function updateItem(key: number, field: "skuId" | "quantity", value: string) {
    setItems((current) => current.map((item) => item.key === key ? { ...item, [field]: value } : item));
  }

  function addItem() {
    setItems((current) => [...current, { key: Date.now() + current.length, skuId: "", quantity: "" }]);
  }

  function removeItem(key: number) {
    setItems((current) => current.length === 1 ? current : current.filter((item) => item.key !== key));
  }

  function saveConfig() {
    const next: ProductionConfig = {
      defaultFactory: configDraft.defaultFactory.trim(),
      salesWindowDays: Math.min(Math.max(Number(configDraft.salesWindowDays) || 30, 7), 180),
      leadDays: Math.min(Math.max(Number(configDraft.leadDays) || 14, 1), 120),
      safetyDays: Math.min(Math.max(Number(configDraft.safetyDays) || 0, 0), 90),
    };
    window.localStorage.setItem(CONFIG_KEY, JSON.stringify(next));
    setConfig(next);
    if (!factoryName.trim() && next.defaultFactory) setFactoryName(next.defaultFactory);
    setConfigDraft(next);
    setMessage("生产参数已保存并应用到补货与生产建议。");
  }

  async function import1688(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setImporting(true);
    setError("");
    setMessage("");
    try {
      const form = new FormData();
      form.append("file", file);
      const response = await authenticatedFetch("/api/v1/alibaba1688-imports/upload?auto_confirm=true", {
        method: "POST",
        body: form,
      });
      if (!response.ok) throw new Error(await responseError(response, "1688订单导入失败"));
      const result = await response.json();
      setMessage(`1688订单已导入${result.order_count ? `，识别 ${result.order_count} 单` : ""}。后续生产/采购链路继续在本系统跟进。`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setImporting(false);
      event.target.value = "";
    }
  }

  async function createOrder() {
    setError("");
    const normalized = items
      .filter((item) => item.skuId && Number(item.quantity) > 0)
      .map((item) => ({ sku_id: Number(item.skuId), quantity: item.quantity }));
    if (!factoryName.trim()) {
      setError("请先填写工厂名称");
      return;
    }
    if (!normalized.length) {
      setError("至少填写一个 SKU 和生产数量");
      return;
    }
    setSaving(true);
    try {
      const response = await authenticatedFetch("/api/v1/supply-chain/production-orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          factory_name: factoryName.trim(),
          planned_start_date: plannedStartDate || null,
          expected_delivery_date: expectedDeliveryDate || null,
          source_type: "manual",
          note,
          items: normalized,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "创建生产单失败"));
      setFactoryName(config.defaultFactory || "");
      setPlannedStartDate("");
      setExpectedDeliveryDate("");
      setNote("");
      setItems([{ key: Date.now(), skuId: "", quantity: "" }]);
      setMessage("生产单已创建。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function recalculate(orderId: number) {
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${orderId}/recalculate-materials`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "重算耗材失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function cancel(orderId: number) {
    if (!window.confirm("确认取消这张生产单？未发出的耗材预占会自动释放。")) return;
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${orderId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "取消生产单失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <div className="mx-auto max-w-[1580px] space-y-4">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / PRODUCTION</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">生产订单</h1>
            <p className="mt-1 text-sm text-slate-500">生产参数、新建订单、耗材需求和执行入口全部在本页直接显示。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <label className={`cursor-pointer rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 ${importing ? "pointer-events-none opacity-50" : ""}`}>
              {importing ? "导入中…" : "导入1688订单"}
              <input type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={import1688} />
            </label>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
          <span>进行中 <b className="ml-1 text-slate-900">{summary.active}</b></span>
          <span>执行中 <b className="ml-1 text-indigo-700">{summary.producing}</b></span>
          <span>缺料单 <b className={`ml-1 ${summary.shortage > 0 ? "text-red-700" : "text-slate-900"}`}>{summary.shortage}</b></span>
          <span className="ml-auto text-[11px] text-slate-400">全部生产单与耗材需求默认展开</span>
        </div>
      </header>

      <section className="rounded-xl border border-indigo-100 bg-indigo-50/30 px-4 py-3">
        <div className="flex flex-wrap items-end gap-2.5">
          <div className="mr-auto min-w-[180px]">
            <h2 className="text-sm font-semibold text-slate-800">生产参数</h2>
            <p className="mt-1 text-[11px] text-slate-500">常驻显示，不再折叠。</p>
          </div>
          <label className="text-[10px] text-slate-500">默认工厂
            <input value={configDraft.defaultFactory} onChange={(event) => setConfigDraft({ ...configDraft, defaultFactory: event.target.value })} placeholder="例如：美啡源" className="mt-1 block h-8 w-52 rounded-md border border-slate-200 bg-white px-2.5 text-xs" />
          </label>
          <label className="text-[10px] text-slate-500">销量观察
            <div className="mt-1 flex items-center gap-1"><input type="number" min="7" max="180" value={configDraft.salesWindowDays} onChange={(event) => setConfigDraft({ ...configDraft, salesWindowDays: Number(event.target.value) })} className="h-8 w-20 rounded-md border border-slate-200 bg-white px-2 text-xs" /><span>天</span></div>
          </label>
          <label className="text-[10px] text-slate-500">生产交期
            <div className="mt-1 flex items-center gap-1"><input type="number" min="1" max="120" value={configDraft.leadDays} onChange={(event) => setConfigDraft({ ...configDraft, leadDays: Number(event.target.value) })} className="h-8 w-20 rounded-md border border-slate-200 bg-white px-2 text-xs" /><span>天</span></div>
          </label>
          <label className="text-[10px] text-slate-500">安全库存
            <div className="mt-1 flex items-center gap-1"><input type="number" min="0" max="90" value={configDraft.safetyDays} onChange={(event) => setConfigDraft({ ...configDraft, safetyDays: Number(event.target.value) })} className="h-8 w-20 rounded-md border border-slate-200 bg-white px-2 text-xs" /><span>天</span></div>
          </label>
          <button onClick={saveConfig} className="h-8 rounded-md bg-indigo-600 px-3 text-xs font-medium text-white hover:bg-indigo-700">保存参数</button>
        </div>
      </section>

      {message && <div className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">新建生产单</h2>
            <p className="mt-1 text-[11px] text-slate-500">创建后按货品档案的正品-耗材关联自动计算需求；当前参数：近 {config.salesWindowDays} 天 / 交期 {config.leadDays} 天 / 安全 {config.safetyDays} 天。</p>
          </div>
        </div>

        <div className="grid gap-2.5 xl:grid-cols-[1.1fr_145px_145px_1.6fr_auto]">
          <label className="text-[10px] text-slate-500">工厂名称
            <input value={factoryName} onChange={(event) => setFactoryName(event.target.value)} placeholder="例如：深圳市美啡源实业有限公司" className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm text-slate-700 outline-none focus:border-indigo-400" />
          </label>
          <label className="text-[10px] text-slate-500">计划开始<input type="date" value={plannedStartDate} onChange={(event) => setPlannedStartDate(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-xs text-slate-700" /></label>
          <label className="text-[10px] text-slate-500">预计交货<input type="date" value={expectedDeliveryDate} onChange={(event) => setExpectedDeliveryDate(event.target.value)} className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2 text-xs text-slate-700" /></label>
          <label className="text-[10px] text-slate-500">备注<input value={note} onChange={(event) => setNote(event.target.value)} placeholder="生产要求、批次、包装说明等" className="mt-1 h-9 w-full rounded-md border border-slate-200 px-3 text-sm text-slate-700 outline-none focus:border-indigo-400" /></label>
          <button onClick={createOrder} disabled={saving} className="h-9 self-end rounded-md bg-indigo-600 px-4 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{saving ? "创建中…" : "创建生产单"}</button>
        </div>

        <div className="mt-3 space-y-1.5">
          {items.map((item, index) => (
            <div key={item.key} className="grid items-center gap-2 md:grid-cols-[30px_1fr_150px_58px]">
              <div className="text-center text-[11px] font-semibold text-slate-400">{index + 1}</div>
              <select value={item.skuId} onChange={(event) => updateItem(item.key, "skuId", event.target.value)} className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700">
                <option value="">选择正品 SKU</option>
                {skuOptions.map((sku) => <option key={sku.skuId} value={sku.skuId}>{sku.goodsName || sku.skuName || sku.skuCode} · {sku.skuCode}</option>)}
              </select>
              <input type="number" min="0.0001" step="1" value={item.quantity} onChange={(event) => updateItem(item.key, "quantity", event.target.value)} placeholder="生产数量" className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700" />
              <button type="button" onClick={() => removeItem(item.key)} disabled={items.length === 1} className="h-9 rounded-md border border-slate-200 bg-white px-2 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-30">删除</button>
            </div>
          ))}
          <button type="button" onClick={addItem} className="ml-[38px] rounded-md border border-dashed border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:border-indigo-300 hover:text-indigo-700">+ 添加 SKU</button>
        </div>
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div><h2 className="text-sm font-semibold text-slate-900">生产单列表</h2><p className="mt-1 text-[11px] text-slate-500">内容全部展开；缺料直接标红。</p></div>
          <button onClick={load} disabled={loading} className="rounded-md border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">{loading ? "刷新中…" : "刷新"}</button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1380px] text-sm">
            <thead className="bg-slate-50 text-left text-[11px] font-medium text-slate-500">
              <tr><th className="px-4 py-2.5">生产单</th><th className="px-4 py-2.5">工厂</th><th className="px-4 py-2.5">状态</th><th className="px-4 py-2.5">生产内容</th><th className="px-4 py-2.5">耗材需求 / 预占</th><th className="px-4 py-2.5">计划 / 交货</th><th className="px-4 py-2.5">操作</th></tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {orders.map((order) => {
                const status = STATUS[order.status] ?? { label: order.status, cls: "bg-slate-100 text-slate-600" };
                return (
                  <tr key={order.id} className={order.status === "cancelled" ? "bg-slate-50/60" : "bg-white hover:bg-slate-50/50"}>
                    <td className="px-4 py-3 align-top"><div className="font-mono text-xs font-semibold text-slate-800">{order.orderNo}</div><div className="mt-1 text-[10px] text-slate-400">{order.createdAt ? new Date(order.createdAt).toLocaleString("zh-CN") : ""}</div></td>
                    <td className="max-w-[200px] px-4 py-3 align-top"><div className="font-medium text-slate-700">{order.factoryName}</div>{order.note && <div className="mt-1 line-clamp-2 text-xs text-slate-400">{order.note}</div>}</td>
                    <td className="px-4 py-3 align-top"><span className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${status.cls}`}>{status.label}</span>{order.materialShortageCount > 0 && order.status !== "cancelled" && <div className="mt-2 text-[10px] font-medium text-red-600">{order.materialShortageCount} 项缺料</div>}</td>
                    <td className="max-w-[300px] px-4 py-3 align-top"><div className="space-y-1.5">{order.items.map((item) => <div key={item.id} className="flex items-center justify-between gap-3 text-xs"><span className="truncate text-slate-600">{item.skuName || item.skuCode}</span><span className="shrink-0 font-medium tabular-nums text-slate-800">{qty(item.quantity)} {item.unit}</span></div>)}</div></td>
                    <td className="max-w-[400px] px-4 py-3 align-top">
                      {order.materials.length === 0 ? <div className="text-xs text-amber-600">该生产内容尚未关联耗材</div> : <div className="space-y-1.5">{order.materials.map((material) => <div key={material.id} className={`grid grid-cols-[1fr_auto_auto] items-center gap-3 rounded-md px-2 py-1.5 text-xs ${material.state === "shortage" ? "bg-red-50" : "bg-slate-50"}`}><span className="truncate text-slate-600">{material.name} <span className="text-[10px] text-slate-400">{material.code}</span></span><span className="tabular-nums text-slate-700">需 {qty(material.requiredQty)} / 占 {qty(material.reservedQty)}</span><span className={material.state === "shortage" ? "font-medium text-red-700" : "text-emerald-700"}>{MATERIAL_STATE[material.state]}{Number(material.shortageQty) > 0 ? ` ${qty(material.shortageQty)}` : ""}</span></div>)}</div>}
                    </td>
                    <td className="px-4 py-3 align-top text-xs text-slate-600"><div>开始：{order.plannedStartDate || "—"}</div><div className="mt-1">交货：{order.expectedDeliveryDate || "—"}</div></td>
                    <td className="px-4 py-3 align-top">
                      <div className="flex flex-wrap gap-1.5">
                        {["planned", "confirmed"].includes(order.status) && <button onClick={() => recalculate(order.id)} className="rounded-md border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50">重算耗材</button>}
                        {!["completed", "cancelled"].includes(order.status) && <button onClick={() => cancel(order.id)} className="rounded-md border border-red-100 px-2 py-1 text-[11px] text-red-600 hover:bg-red-50">取消</button>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!loading && orders.length === 0 && <div className="p-10 text-center text-sm text-slate-400">还没有生产单，可以从上方直接创建。</div>}
        </div>
      </section>
    </div>
  );
}
