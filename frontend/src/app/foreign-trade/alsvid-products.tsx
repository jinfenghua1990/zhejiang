"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";
import ForeignTradeWorkbench from "./workbench";

const BRAND = "ALSVID";

type Platform = {
  id: number;
  brand: string;
  code: string;
  name: string;
  nameEn: string;
  description: string;
  displayOrder: number;
  enabled: boolean;
};

type ForeignProduct = {
  id: number;
  brand: string;
  platformCode: string;
  platformName: string;
  platformNameEn: string;
  modelCode: string;
  name: string;
  nameEn: string;
  skuId: number | null;
  skuCode: string;
  skuName: string;
  externalSku: string;
  status: "draft" | "planned" | "active" | "archived" | string;
  countries: string[];
  currency: string;
  note: string;
};

type CatalogSku = {
  id: number;
  skuCode: string;
  skuName: string;
  goodsName: string;
  status: string;
};

type ProductDraft = {
  platform_code: string;
  model_code: string;
  name: string;
  name_en: string;
  sku_id: number | null;
  external_sku: string;
  status: "draft" | "planned" | "active" | "archived";
  countries: string;
  currency: string;
  note: string;
};

const DEFAULT_PLATFORMS: Platform[] = [
  { id: 0, brand: BRAND, code: "FC1", name: "折叠旗舰", nameEn: "Folding Flagship", description: "折叠旗舰平台", displayOrder: 10, enabled: true },
  { id: 0, brand: BRAND, code: "FT1", name: "胖胎", nameEn: "Fat Tire", description: "Fat Tire 胖胎平台", displayOrder: 20, enabled: true },
  { id: 0, brand: BRAND, code: "CT1", name: "都市", nameEn: "City", description: "City 都市平台", displayOrder: 30, enabled: true },
  { id: 0, brand: BRAND, code: "GT1", name: "长途", nameEn: "Gravel / Touring", description: "Gravel / Touring 长途平台", displayOrder: 40, enabled: true },
];

const EMPTY_DRAFT: ProductDraft = {
  platform_code: "FC1",
  model_code: "",
  name: "",
  name_en: "",
  sku_id: null,
  external_sku: "",
  status: "draft",
  countries: "DE, AT",
  currency: "EUR",
  note: "",
};

const STATUS_LABEL: Record<string, string> = {
  draft: "草稿",
  planned: "规划中",
  active: "已启用",
  archived: "已归档",
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await authenticatedFetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "请求失败");
  }
  return payload as T;
}

function splitList(value: string) {
  return [...new Set(value.split(/[,，\s]+/).map((item) => item.trim().toUpperCase()).filter(Boolean))];
}

function statusClass(status: string) {
  if (status === "active") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "archived") return "border-slate-200 bg-slate-100 text-slate-500";
  return status === "planned"
    ? "border-amber-200 bg-amber-50 text-amber-700"
    : "border-blue-200 bg-blue-50 text-blue-700";
}

export default function AlsvidProductsWorkbench() {
  const [view, setView] = useState<"products" | "orders">("products");
  const [platforms, setPlatforms] = useState<Platform[]>(DEFAULT_PLATFORMS);
  const [products, setProducts] = useState<ForeignProduct[]>([]);
  const [catalogSkus, setCatalogSkus] = useState<CatalogSku[]>([]);
  const [selectedPlatform, setSelectedPlatform] = useState("all");
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<ProductDraft>(EMPTY_DRAFT);
  const [editingProduct, setEditingProduct] = useState<ForeignProduct | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const loadProducts = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ brand: BRAND });
      if (search.trim()) params.set("q", search.trim());
      const payload = await request<{ items: ForeignProduct[] }>(
        "/api/v1/foreign-trade/products?" + params.toString(),
      );
      setProducts(payload.items);
      const platformPayload = await request<{ items: Platform[] }>(
        "/api/v1/foreign-trade/products/platforms?brand=" + BRAND,
      );
      setPlatforms(platformPayload.items.length ? platformPayload.items : DEFAULT_PLATFORMS);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "加载 ALSVID 产品失败");
    } finally {
      setLoading(false);
    }
  }, [search]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadProducts(), 120);
    return () => window.clearTimeout(timer);
  }, [loadProducts]);

  useEffect(() => {
    void request<{ items: CatalogSku[] }>("/api/v1/dashboard/products?limit=500")
      .then((payload) => setCatalogSkus(payload.items))
      .catch(() => setCatalogSkus([]));
  }, []);

  const platformMap = useMemo(
    () => new Map(platforms.map((platform) => [platform.code, platform])),
    [platforms],
  );

  function openCreate(code?: string) {
    setEditingProduct(null);
    setDraft({
      ...EMPTY_DRAFT,
      platform_code: code || (selectedPlatform === "all" ? "FC1" : selectedPlatform),
    });
    setFormOpen(true);
    setError("");
  }

  function openEdit(row: ForeignProduct) {
    setEditingProduct(row);
    setDraft({
      platform_code: row.platformCode,
      model_code: row.modelCode,
      name: row.name,
      name_en: row.nameEn,
      sku_id: row.skuId,
      external_sku: row.externalSku,
      status: row.status as ProductDraft["status"],
      countries: row.countries.join(", "),
      currency: row.currency,
      note: row.note,
    });
    setFormOpen(true);
    setError("");
  }

  async function saveProduct() {
    if (!draft.platform_code.trim() || !draft.model_code.trim() || !draft.name.trim()) {
      setError("平台、型号编码和产品名称必须填写");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const body = {
        brand: BRAND,
        platform_code: draft.platform_code.trim().toUpperCase(),
        model_code: draft.model_code.trim().toUpperCase(),
        name: draft.name.trim(),
        name_en: draft.name_en.trim(),
        sku_id: draft.sku_id,
        external_sku: draft.external_sku.trim(),
        status: draft.status,
        countries: splitList(draft.countries),
        currency: draft.currency.trim().toUpperCase() || "EUR",
        note: draft.note.trim(),
      };
      const url = editingProduct
        ? "/api/v1/foreign-trade/products/" + editingProduct.id
        : "/api/v1/foreign-trade/products";
      await request(url, {
        method: editingProduct ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setFormOpen(false);
      setEditingProduct(null);
      setDraft(EMPTY_DRAFT);
      await loadProducts();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存产品失败");
    } finally {
      setSaving(false);
    }
  }

  async function removeProduct(row: ForeignProduct) {
    if (!window.confirm("删除产品 " + row.modelCode + "？")) return;
    try {
      await request("/api/v1/foreign-trade/products/" + row.id, { method: "DELETE" });
      await loadProducts();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "删除产品失败");
    }
  }

  const visibleProducts = products.filter(
    (row) => selectedPlatform === "all" || row.platformCode === selectedPlatform,
  );
  const summary = platforms.map((platform) => ({
    ...platform,
    count: products.filter((row) => row.platformCode === platform.code).length,
  }));

  return (
    <div className="min-h-full bg-slate-50/70 px-5 py-5">
      <div className="mx-auto max-w-[1560px]">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-blue-600">ALSVID · EXTERNAL PRODUCT SYSTEM</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">ALSVID 产品中心</h1>
            <p className="mt-1 text-sm text-slate-500">按技术平台管理外贸产品；产品主档可继续绑定共用 SKU，后续复用库存、采购、物流和财务底座。</p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/foreign-trade" className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600 hover:border-blue-300 hover:text-blue-600">返回外贸总览</Link>
            <button type="button" onClick={() => openCreate()} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white">+ 新建产品</button>
          </div>
        </header>

        <div className="mt-5 flex items-center gap-2 border-b border-slate-200">
          <button type="button" onClick={() => setView("products")} className={"border-b-2 px-3 py-2 text-sm font-medium " + (view === "products" ? "border-blue-600 text-blue-700" : "border-transparent text-slate-400")}>产品平台</button>
          <button type="button" onClick={() => setView("orders")} className={"border-b-2 px-3 py-2 text-sm font-medium " + (view === "orders" ? "border-blue-600 text-blue-700" : "border-transparent text-slate-400")}>ALSVID 订单</button>
        </div>

        {view === "products" ? (
          <>
            <section className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {summary.map((platform) => (
                <button
                  type="button"
                  key={platform.code}
                  onClick={() => setSelectedPlatform(selectedPlatform === platform.code ? "all" : platform.code)}
                  className={"rounded-2xl border bg-white p-4 text-left shadow-[0_8px_24px_rgba(15,23,42,0.03)] transition " + (selectedPlatform === platform.code ? "border-blue-400 ring-2 ring-blue-100" : "border-slate-200 hover:border-blue-200")}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-mono text-lg font-semibold tracking-tight text-slate-900">{platform.code}</div>
                      <div className="mt-1 text-sm font-semibold text-slate-800">{platform.name}</div>
                      <div className="mt-0.5 text-[11px] text-slate-400">{platform.nameEn}</div>
                    </div>
                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">{platform.count} 个产品</span>
                  </div>
                  <p className="mt-3 min-h-10 text-xs leading-5 text-slate-500">{platform.description}</p>
                  <div className="mt-3 text-[11px] font-medium text-blue-600">查看该平台产品 →</div>
                </button>
              ))}
            </section>

            <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-900">ALSVID 技术平台产品</h2>
                  <p className="mt-1 text-xs text-slate-400">FC1 / FT1 / CT1 / GT1 是产品平台编码；具体车型可以在平台下继续扩展，例如 FC1-01。</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索型号、产品名称、外部 SKU" className="h-9 w-64 rounded-lg border border-slate-200 bg-slate-50/60 px-3 text-sm outline-none focus:border-blue-400 focus:bg-white" />
                  <button type="button" onClick={() => openCreate()} className="h-9 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700">+ 新增平台产品</button>
                </div>
              </div>

              {formOpen && (
                <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50/40 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-800">{editingProduct ? "编辑 ALSVID 产品" : "新建 ALSVID 产品"}</h3>
                      <p className="mt-1 text-xs text-slate-500">先建立外贸产品主档；若暂未有库存 SKU，可以保存为规划中，后续再绑定。</p>
                    </div>
                    <button type="button" onClick={() => setFormOpen(false)} className="text-xs text-slate-400 hover:text-slate-600">关闭</button>
                  </div>
                  <div className="mt-4 grid gap-3 md:grid-cols-4">
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">技术平台</span><select className="input" value={draft.platform_code} onChange={(event) => setDraft({ ...draft, platform_code: event.target.value })}>{platforms.map((platform) => <option key={platform.code} value={platform.code}>{platform.code} · {platform.name}</option>)}</select></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">型号编码</span><input className="input" value={draft.model_code} onChange={(event) => setDraft({ ...draft, model_code: event.target.value })} placeholder="如 FC1-01" /></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">中文名称</span><input className="input" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="如 折叠旗舰" /></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">英文名称</span><input className="input" value={draft.name_en} onChange={(event) => setDraft({ ...draft, name_en: event.target.value })} placeholder="如 Folding Flagship" /></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">外部 SKU</span><input className="input" value={draft.external_sku} onChange={(event) => setDraft({ ...draft, external_sku: event.target.value })} placeholder="Shopify / Dealer SKU，可后补" /></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">共用中台 SKU</span><select className="input" value={draft.sku_id ?? ""} onChange={(event) => setDraft({ ...draft, sku_id: event.target.value ? Number(event.target.value) : null })}><option value="">暂不绑定</option>{catalogSkus.map((sku) => <option key={sku.id} value={sku.id}>{sku.skuCode} · {sku.skuName || sku.goodsName}</option>)}</select></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">状态</span><select className="input" value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as ProductDraft["status"] })}><option value="draft">草稿</option><option value="planned">规划中</option><option value="active">已启用</option><option value="archived">已归档</option></select></label>
                    <label className="block"><span className="mb-1 block text-[11px] font-medium text-slate-500">币种</span><input className="input" value={draft.currency} onChange={(event) => setDraft({ ...draft, currency: event.target.value })} /></label>
                    <label className="block md:col-span-2"><span className="mb-1 block text-[11px] font-medium text-slate-500">销售国家</span><input className="input" value={draft.countries} onChange={(event) => setDraft({ ...draft, countries: event.target.value })} placeholder="DE, AT" /></label>
                    <label className="block md:col-span-2"><span className="mb-1 block text-[11px] font-medium text-slate-500">备注</span><input className="input" value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} placeholder="配置、定位或上市备注" /></label>
                  </div>
                  <div className="mt-4 flex items-center justify-between gap-3">
                    <p className="text-[11px] text-slate-400">绑定中台 SKU 后，外贸产品可以沿用现有库存、采购和成本数据。</p>
                    <div className="flex gap-2"><button type="button" onClick={() => setFormOpen(false)} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs text-slate-600">取消</button><button type="button" onClick={() => void saveProduct()} disabled={saving} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:opacity-50">{saving ? "保存中…" : "保存产品"}</button></div>
                  </div>
                </div>
              )}

              {error && <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

              <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200">
                {loading ? <div className="py-14 text-center text-sm text-slate-400">正在加载 ALSVID 产品…</div> : visibleProducts.length === 0 ? <div className="py-14 text-center text-sm text-slate-400">暂无匹配产品，可点击“新建平台产品”。</div> : (
                  <table className="min-w-[980px] w-full text-left text-xs">
                    <thead className="bg-slate-50 text-slate-500"><tr><th className="px-4 py-3">产品</th><th>技术平台</th><th>中台 SKU</th><th>外部 SKU</th><th>销售国家 / 币种</th><th>状态</th><th className="pr-4 text-right">操作</th></tr></thead>
                    <tbody>{visibleProducts.map((row) => {
                      const platform = platformMap.get(row.platformCode);
                      return <tr key={row.id} className="border-t border-slate-100 align-top">
                        <td className="px-4 py-3"><div className="font-semibold text-slate-800">{row.modelCode} · {row.name}</div><div className="mt-1 text-slate-400">{row.nameEn || "—"}</div></td>
                        <td><span className="rounded bg-blue-50 px-2 py-1 font-mono text-[11px] text-blue-700">{row.platformCode}</span><div className="mt-1 text-slate-400">{platform?.name || row.platformName}</div></td>
                        <td>{row.skuCode ? <><div className="font-mono text-slate-700">{row.skuCode}</div><div className="text-slate-400">{row.skuName || "—"}</div></> : <span className="text-slate-300">未绑定</span>}</td>
                        <td className="font-mono text-slate-600">{row.externalSku || "—"}</td>
                        <td>{row.countries.join(" / ") || "—"} · {row.currency}</td>
                        <td><span className={"inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium " + statusClass(row.status)}>{STATUS_LABEL[row.status] || row.status}</span></td>
                        <td className="pr-4 text-right whitespace-nowrap"><button type="button" onClick={() => openEdit(row)} className="text-blue-600">编辑</button><button type="button" onClick={() => void removeProduct(row)} className="ml-3 text-rose-500">删除</button></td>
                      </tr>;
                    })}</tbody>
                  </table>
                )}
              </div>
            </section>
          </>
        ) : (
          <div className="mt-5"><ForeignTradeWorkbench mode="alsvid" /></div>
        )}
      </div>
      <style jsx global>{".input{width:100%;border:1px solid rgb(226 232 240);border-radius:.5rem;background:white;padding:.5rem .65rem;font-size:.8rem;line-height:1.25rem;color:rgb(51 65 85);outline:none}.input:focus{border-color:rgb(96 165 250);box-shadow:0 0 0 2px rgb(219 234 254)}"}</style>
    </div>
  );
}
