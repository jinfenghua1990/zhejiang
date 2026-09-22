"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";
import TaxCategoryRulesPanel from "@/components/tax-category-rules-panel";
import {
  AuthUser,
  CatalogBulkDeleteItem,
  CatalogBulkDeleteResult,
  CatalogSkuRow,
  consumablesApi,
  dashboardApi,
  fetchMe,
  LinkedSkuRef,
  TaxCategoryRule,
  taxAccountingApi,
  UnifiedCatalogRow,
  authenticatedFetch,
  masterDataApi,
} from "@/lib/api";
import { useTabScopedState, useTabTitle } from "@/lib/workspace/tab-store";

/** 类型中文：single=单品 / bundle=套装 / virtual_bundle=虚拟组合套装（不同商品不同数量组合）。 */
const TYPE_LABEL: Record<string, string> = { single: "单品", bundle: "套装", virtual_bundle: "虚拟组合套装" };

type ProductForm = {
  jackyun_sku_id: string;
  sku_code: string;
  product_type: string;
  sku_name: string;
  barcode: string;
  unit: string;
  sale_price: string;
  default_cost: string;
  cost_mode: string;
  cost_tolerance_pct: string;
  tax_code: string;
  tax_category_rule_id: string;
  goods_category: string;
  status: string;
};

const emptyProduct: ProductForm = { jackyun_sku_id: "", sku_code: "", product_type: "single", sku_name: "", barcode: "", unit: "盒", sale_price: "", default_cost: "", cost_mode: "fixed", cost_tolerance_pct: "0.0200", tax_code: "", tax_category_rule_id: "", goods_category: "", status: "active" };
const catalogDeleteRefText = (ref: { label: string; count: number; numbers?: string[] }) => {
  const numbers = ref.numbers ?? [];
  if (!numbers.length) return `${ref.label}${ref.count}条`;
  const more = numbers.includes("等") ? "等" : "";
  const nos = numbers.filter((no) => no !== "等");
  if (ref.label === "销售订单") {
    return `被销售订单 ${nos.join("、")}${more} 占用·销售订单${ref.count}条`;
  }
  // 采购单号为纯数字（1688/外部订单号），其余按入库单号展示
  const orderNos = nos.filter((no) => /^\d+$/.test(no));
  const docNos = nos.filter((no) => !/^\d+$/.test(no));
  const parts = [
    orderNos.length ? `被采购单 ${orderNos.join("、")}${more} 占用` : "",
    docNos.length ? `被入库单 ${docNos.join("、")}${more} 占用` : "",
  ].filter(Boolean);
  return `${parts.join("、")}·${ref.label}${ref.count}条`;
};
const catalogDeleteIssueText = (result: CatalogBulkDeleteResult) => [
  result.blocked.length
    ? `${[...new Set(result.blocked.map((item) => item.reason || "已有历史业务引用，不能删除"))].join("；")}：${result.blocked.map((item) => `${item.code}（${item.references.map(catalogDeleteRefText).join("、")}）`).join("；")}`
    : "",
  result.invalid.length ? `类型不符：${result.invalid.map((item) => item.code).join("、")}` : "",
  result.notFound.length ? `不存在：${result.notFound.map((item) => `${item.kind}-${item.id}`).join("、")}` : "",
].filter(Boolean).join("；");

type KindFilter = "all" | "goods" | "consumable";
type ProductTab = "catalog" | "bundles" | "taxRules";
type MasterDataset = "catalog" | "bundles" | "tax_rules";

type ConsumableDraft = {
  id: number | null;
  code: string;
  name: string;
  barcode: string;
  category: string;
  unit: string;
  purchase_unit_cost: string;
  min_stock_qty: string;
  tax_code: string;
  tax_category_rule_id: string;
  sku_ids: number[];
};

const emptyConsumable: ConsumableDraft = {
  id: null, code: "", name: "", barcode: "", category: "", unit: "个",
  purchase_unit_cost: "", min_stock_qty: "", tax_code: "", tax_category_rule_id: "", sku_ids: [],
};

function MasterDataActions({
  dataset,
  label,
  exporting,
  importing,
  canEdit,
  onExport,
  onImport,
}: {
  dataset: MasterDataset;
  label: string;
  exporting: MasterDataset | null;
  importing: MasterDataset | null;
  canEdit: boolean;
  onExport: (dataset: MasterDataset, label: string, template?: boolean) => void;
  onImport: (dataset: MasterDataset, label: string, file: File | undefined) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {canEdit ? <label className="cursor-pointer rounded-lg border border-emerald-200 bg-white px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50">
          {importing === dataset ? "导入中…" : `导入${label}`}
          <input
            type="file"
            accept=".xlsx,.xlsm"
            className="hidden"
            disabled={importing !== null}
            onChange={(event) => {
              onImport(dataset, label, event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </label>
        : <span title="仅管理员或操作员可导入" className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-400">导入{label}（无权限）</span>}
      <button
        type="button"
        onClick={() => onExport(dataset, label)}
        disabled={exporting !== null}
        className="rounded-lg border border-blue-200 bg-white px-3 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:cursor-wait disabled:opacity-60"
      >
        {exporting === dataset ? "导出中…" : `导出${label}`}
      </button>
      <button
        type="button"
        onClick={() => onExport(dataset, label, true)}
        disabled={exporting !== null}
        className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:cursor-wait disabled:opacity-60"
      >
        下载模板
      </button>
    </div>
  );
}

function KindBadge({ kind }: { kind: "goods" | "consumable" }) {
  return kind === "consumable"
    ? <span className="mr-1.5 inline-block shrink-0 rounded bg-amber-100 px-1 py-px align-[1px] text-[10px] font-semibold leading-4 text-amber-700">耗</span>
    : <span className="mr-1.5 inline-block shrink-0 rounded bg-emerald-100 px-1 py-px align-[1px] text-[10px] font-semibold leading-4 text-emerald-700">品</span>;
}

function CatalogMetric({ label, value, hint, tone }: { label: string; value: string; hint: string; tone: "blue" | "amber" | "indigo" | "slate" }) {
  const toneClass = tone === "amber" ? "bg-amber-50 text-amber-600" : tone === "indigo" ? "bg-indigo-50 text-indigo-600" : tone === "slate" ? "bg-slate-100 text-slate-500" : "bg-blue-50 text-blue-600";
  return <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
    <div><div className="text-xs text-slate-500">{label}</div><div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div><div className="mt-1 text-[11px] text-slate-400">{hint}</div></div>
    <span className={`flex h-10 w-10 items-center justify-center rounded-xl text-sm font-semibold ${toneClass}`}>{label === "最近变更" ? "时" : label.slice(0, 1)}</span>
  </div>;
}

/** 品类单元格：点铅笔进入编辑，回车保存 / Esc 取消。 */
function EditableCategory({ row, onSave, canEdit }: { row: UnifiedCatalogRow; onSave: (row: UnifiedCatalogRow, value: string) => Promise<void>; canEdit: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(row.goodsCategory || "");
  const [saving, setSaving] = useState(false);
  if (!canEdit) {
    return <span className="inline-block min-w-[72px] px-1 text-[11px] text-gray-600">{row.goodsCategory || <span className="text-gray-300">未设置</span>}</span>;
  }
  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => { setValue(row.goodsCategory || ""); setEditing(true); }}
        title="修改品类（如 咖啡豆 / 饼干）"
        aria-label={`修改 ${row.code} 的品类，当前为 ${row.goodsCategory || "未设置"}`}
        className="group inline-flex min-w-[72px] items-center gap-1 rounded px-1 py-0.5 text-left hover:bg-blue-50"
      >
        <span className="text-[11px] text-gray-600">{row.goodsCategory || <span className="text-gray-300">未设置</span>}</span>
        <span className="rounded p-0.5 text-gray-300 transition-colors group-hover:text-blue-600" aria-hidden="true">
          <svg viewBox="0 0 16 16" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M11.5 2.5l2 2L6 12l-2.8.8L4 10l7.5-7.5z" strokeLinejoin="round" /></svg>
        </span>
      </button>
    );
  }
  const commit = async () => {
    if (saving) return;
    if (value.trim() === (row.goodsCategory || "").trim()) { setEditing(false); return; }
    setSaving(true);
    try { await onSave(row, value.trim()); setEditing(false); } finally { setSaving(false); }
  };
  return (
    <input
      autoFocus
      value={value}
      disabled={saving}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); void commit(); } if (e.key === "Escape") setEditing(false); }}
      onBlur={() => void commit()}
      placeholder="如 咖啡 / 饼干"
      className="h-6 w-24 rounded border border-blue-300 px-1.5 text-[11px] outline-none disabled:opacity-50"
    />
  );
}

function ProductEditor({
  form,
  setForm,
  onSubmit,
  onCancel,
  taxRules,
  inline = false,
}: {
  form: ProductForm;
  setForm: React.Dispatch<React.SetStateAction<ProductForm>>;
  onSubmit: (event: React.FormEvent) => void;
  onCancel: () => void;
  taxRules: TaxCategoryRule[];
  inline?: boolean;
}) {
  return (
    <form onSubmit={onSubmit} className={`grid grid-cols-6 gap-2 rounded-lg bg-blue-50 p-3 text-xs ${inline ? "border border-blue-100" : "mt-4"}`}>
      {inline && <div className="col-span-full flex items-center justify-between gap-3 border-b border-blue-100 pb-2"><span className="font-medium text-blue-700">编辑当前货品</span><span className="text-[11px] text-blue-500">保存后仍停留在当前货品位置</span></div>}
      <input aria-label="吉客云 SKU ID" placeholder="吉客云 SKU ID（可空）" value={form.jackyun_sku_id} onChange={(e) => setForm({ ...form, jackyun_sku_id: e.target.value })} className="rounded border px-2 py-1.5" />
      <input aria-label="SKU 编码" required placeholder="SKU 编码" value={form.sku_code} onChange={(e) => setForm({ ...form, sku_code: e.target.value })} className="rounded border px-2 py-1.5" />
      <select aria-label="货品类型" value={form.product_type} onChange={(e) => setForm({ ...form, product_type: e.target.value })} className="rounded border px-2 py-1.5"><option value="single">单品</option><option value="bundle">套装</option><option value="virtual_bundle">虚拟组合套装</option></select>
      <input aria-label="货品名称或规格" required placeholder="货品名称/规格" value={form.sku_name} onChange={(e) => setForm({ ...form, sku_name: e.target.value })} className="rounded border px-2 py-1.5" />
      <input aria-label="条码" placeholder="条码" value={form.barcode} onChange={(e) => setForm({ ...form, barcode: e.target.value })} className="rounded border px-2 py-1.5" />
      <input aria-label="单位" placeholder="单位" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} className="rounded border px-2 py-1.5" />
      <input aria-label="售价" placeholder="售价" value={form.sale_price} onChange={(e) => setForm({ ...form, sale_price: e.target.value })} className="rounded border px-2 py-1.5" />
      <input aria-label="默认成本" placeholder="默认成本" value={form.default_cost} onChange={(e) => setForm({ ...form, default_cost: e.target.value })} className="rounded border px-2 py-1.5" />
      <select aria-label="成本方式" value={form.cost_mode} onChange={(e) => setForm({ ...form, cost_mode: e.target.value })} className="rounded border px-2 py-1.5"><option value="fixed">固定成本</option><option value="dynamic">动态成本</option></select>
      <input aria-label="成本容差" placeholder="容差(如0.02)" value={form.cost_tolerance_pct} onChange={(e) => setForm({ ...form, cost_tolerance_pct: e.target.value })} className="rounded border px-2 py-1.5" />
      <select aria-label="财务分类规则" value={form.tax_category_rule_id} onChange={(e) => { const id = e.target.value; const rule = taxRules.find((item) => String(item.id) === id); setForm({ ...form, tax_category_rule_id: id, tax_code: rule?.taxCode || form.tax_code }); }} className="rounded border px-2 py-1.5">
        <option value="">不关联财务分类</option>
        {taxRules.map((rule) => <option key={rule.id} value={rule.id}>{rule.categoryName} · {rule.itemName}{rule.taxCode ? ` · ${rule.taxCode}` : " · 未配置代码"}</option>)}
      </select>
      <input aria-label="税务代码" placeholder="税务代码（税收分类编码）" title="开票用商品和服务税收分类编码（19 位，兼容旧 10 位简称）" value={form.tax_code} onChange={(e) => setForm({ ...form, tax_code: e.target.value.replace(/\D/g, ""), tax_category_rule_id: "" })} className="rounded border px-2 py-1.5 font-mono" />
      <input aria-label="品类" placeholder="品类（如 咖啡豆/饼干）" title="货品品类，来自吉客云同步，可本地修改" value={form.goods_category} onChange={(e) => setForm({ ...form, goods_category: e.target.value })} className="rounded border px-2 py-1.5" />
      <select aria-label="档案状态" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="rounded border px-2 py-1.5"><option value="active">启用</option><option value="inactive">停用</option></select>
      <div className="flex gap-2"><button type="submit" className="rounded bg-blue-600 px-3 py-1.5 text-white">保存</button><button type="button" onClick={onCancel} className="rounded border px-3 py-1.5">取消</button></div>
    </form>
  );
}

/** 统一档案表（货品档案 / 套装档案两个页签共用）。selectionKey 形如 kind-id。 */
function CatalogTable({ rows, onEditConsumable, onEditProduct, onToggleCostMode, onSaveCategory, onDeleteRow, selected, onToggleRow, onToggleAll, editingProductId, productForm, setProductForm, onSaveProduct, onCancelProduct, taxRules, canEdit, canDelete }: {
  rows: UnifiedCatalogRow[];
  onEditConsumable: (row: UnifiedCatalogRow) => void;
  onEditProduct: (row: UnifiedCatalogRow) => void;
  onToggleCostMode: (row: UnifiedCatalogRow) => void;
  onSaveCategory: (row: UnifiedCatalogRow, value: string) => Promise<void>;
  onDeleteRow: (row: UnifiedCatalogRow) => void;
  selected: Set<string>;
  onToggleRow: (row: UnifiedCatalogRow) => void;
  onToggleAll: () => void;
  editingProductId: number | null;
  productForm: ProductForm;
  setProductForm: React.Dispatch<React.SetStateAction<ProductForm>>;
  onSaveProduct: (event: React.FormEvent) => void;
  onCancelProduct: () => void;
  taxRules: TaxCategoryRule[];
  canEdit: boolean;
  canDelete: boolean;
}) {
  const allChecked = rows.length > 0 && rows.every((r) => selected.has(`${r.kind}-${r.id}`));
  return (
    <div className="products-catalog-table mt-4">
      <table className="w-full min-w-[1060px] text-sm">
        <thead className="products-catalog-table-head text-left text-xs text-gray-500">
          <tr className="border-b border-gray-100">
            <th className="w-8 py-2">
              <input type="checkbox" checked={allChecked} onChange={onToggleAll} title="全选/取消本页" className="h-3.5 w-3.5 accent-blue-600" />
            </th>
            <th className="py-2">货品名称</th>
            <th className="py-2">类型</th>
            <th className="py-2">品类</th>
            <th className="py-2">编码</th>
            <th className="py-2">条码</th>
            <th className="py-2">财务分类</th>
            <th className="py-2">税务代码</th>
            <th className="py-2">关联耗材 / 正品</th>
            <th className="py-2">状态</th>
            <th className="py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row) => (
            <Fragment key={`${row.kind}-${row.id}`}>
            <tr className={row.lowStock ? "bg-amber-50/60" : ""}>
              <td className="py-2.5">
                <input type="checkbox" checked={selected.has(`${row.kind}-${row.id}`)} onChange={() => onToggleRow(row)} className="h-3.5 w-3.5 accent-blue-600" />
              </td>
              <td className="max-w-[240px] py-2.5">
                <div className="truncate">
                  <KindBadge kind={row.kind} />
                  <span className={row.status !== "active" ? "text-gray-400" : "text-gray-800"}>{row.name}</span>
                </div>
                {row.kind === "goods" && row.goodsName && row.goodsName !== row.name && <div className="mt-0.5 truncate pl-6 text-[10px] text-gray-400">{row.goodsName}</div>}
              </td>
              <td className="py-2.5 text-[11px] text-gray-500">{row.kind === "goods" ? (row.category === "single" ? "正品" : TYPE_LABEL[row.category] ?? row.category) : "耗材"}</td>
              <td className="py-2.5"><EditableCategory row={row} onSave={onSaveCategory} canEdit={canEdit} /></td>
              <td className="py-2.5 font-mono text-xs text-gray-600">{row.code}</td>
              <td className="py-2.5 font-mono text-[11px] text-gray-500">{row.barcode || "—"}</td>
              <td className="max-w-[150px] py-2.5 text-[11px] text-gray-500">{row.taxCategoryRuleName || (row.taxCode ? <span className="text-slate-400" title="已设置直接税务代码，未关联共用财务分类规则">已填代码（未关联分类）</span> : <span className="text-gray-300">未设置</span>)}</td>
              <td className="py-2.5 font-mono text-[11px] text-gray-500" title={row.taxCode ? "税收分类编码（开票用）" : "未设置税务代码，点「编辑」补充"}>{row.taxCode || "—"}</td>
              <td className="max-w-[190px] py-2.5">
                {row.kind === "consumable"
                  ? (row.linkedSkus.length
                    ? <span className="text-[11px] text-gray-500" title={row.linkedSkus.map((s) => s.skuCode).join(", ")}>{row.linkedSkus.slice(0, 2).map((s) => s.skuCode).join(", ")}{row.linkedSkus.length > 2 ? ` +${row.linkedSkus.length - 2}` : ""}</span>
                    : <span className="text-[11px] text-gray-300">未关联</span>)
                  : <span className="text-[11px] text-gray-300">—</span>}
              </td>
              <td className="py-2.5">
                {row.status !== "active"
                    ? <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-400">停用</span>
                    : <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700">正常</span>}
              </td>
              <td className="py-2.5 text-right">
                {row.kind === "consumable"
                  ? canEdit && <div className="flex justify-end gap-1"><button onClick={() => onEditConsumable(row)} className="rounded-md px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50">编辑</button>{canDelete && <button onClick={() => onDeleteRow(row)} className="rounded-md px-2 py-1 text-xs text-rose-600 hover:bg-rose-50">删除</button>}</div>
                  : <div className="flex justify-end gap-1">
                      {canEdit && <><button onClick={() => editingProductId === row.id ? onCancelProduct() : onEditProduct(row)} className="rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100">{editingProductId === row.id ? "收起" : "编辑"}</button>
                      <button onClick={() => onToggleCostMode(row)} className="rounded-md px-2 py-1 text-xs text-blue-600 hover:bg-blue-50">
                          切{row.costMode === "dynamic" ? "固定" : "动态"}
                        </button>{canDelete && <button onClick={() => onDeleteRow(row)} className="rounded-md px-2 py-1 text-xs text-rose-600 hover:bg-rose-50">删除</button>}</>}
                    </div>}
              </td>
            </tr>
            {row.kind === "goods" && editingProductId === row.id && (
              <tr className="bg-blue-50/40">
                <td colSpan={11} className="p-2">
                  <ProductEditor form={productForm} setForm={setProductForm} onSubmit={onSaveProduct} onCancel={onCancelProduct} taxRules={taxRules} inline />
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {!rows.length && <p className="p-8 text-center text-sm text-gray-400">没有匹配的货品</p>}
    </div>
  );
}

export default function ProductsPage() {
  // 工作区下每个 Tab 都有自己的 URL，必须读注入的 searchParams 而不是 window.location.search
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tab, setTab] = useTabScopedState<ProductTab>("products.tab", () => {
    const requested = searchParams.get("tab") || searchParams.get("productTab");
    return requested === "bundles" || requested === "taxRules" || requested === "tax-rules" ? (requested === "tax-rules" ? "taxRules" : requested) : "catalog";
  });
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [catalog, setCatalog] = useState<UnifiedCatalogRow[]>([]);
  const [products, setProducts] = useState<CatalogSkuRow[]>([]);
  const [taxRules, setTaxRules] = useState<TaxCategoryRule[]>([]);
  const [kind, setKind] = useTabScopedState<KindFilter>("products.kind", () => {
    const initial = searchParams.get("kind");
    return initial === "consumable" || initial === "goods" ? initial : "all";
  });
  const [search, setSearch] = useTabScopedState("products.search", () => searchParams.get("q") ?? "");
  const [bundleSearch, setBundleSearch] = useTabScopedState("products.bundleSearch", "");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [productForm, setProductForm] = useState(emptyProduct);
  const [consumableEditor, setConsumableEditor] = useState<"new" | number | null>(null);
  const [consumableDraft, setConsumableDraft] = useState<ConsumableDraft>(emptyConsumable);
  const [savingConsumable, setSavingConsumable] = useState(false);
  const [skuSearch, setSkuSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkTaxCode, setBulkTaxCode] = useState("");
  const [bulkOverwrite, setBulkOverwrite] = useState(false);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [catalogDeleting, setCatalogDeleting] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);
  const [blockedForInactive, setBlockedForInactive] = useState<CatalogBulkDeleteItem[]>([]);
  const [bundleDeleting, setBundleDeleting] = useState(false);
  const [exportingMaster, setExportingMaster] = useState<MasterDataset | null>(null);
  const [importingMaster, setImportingMaster] = useState<MasterDataset | null>(null);
  const [categoryFilter, setCategoryFilter] = useTabScopedState("products.category", "all");
  const [statusFilter, setStatusFilter] = useTabScopedState("products.status", "all");

  // 货品、套装、财务分类已经提升为独立导航入口；工作区标题与当前入口保持一致。
  useTabTitle(tab === "bundles" ? "套装档案" : tab === "taxRules" ? "财务分类" : "货品档案");

  const canEdit = currentUser?.roles.some((role) => role === "admin" || role === "operator") ?? false;
  const canDeleteCatalog = currentUser?.roles.includes("admin") ?? false;
  const canDeleteBundles = currentUser?.roles.includes("admin") ?? false;

  useEffect(() => {
    fetchMe().then(setCurrentUser).catch(() => setCurrentUser(null));
  }, []);

  useEffect(() => {
    // 注意：这里必须用工作区注入的 searchParams（每个 Tab 自己那一份），
    // 且跳转要用 router.replace —— window.location.replace 会整页重载并清空所有工作区 Tab。
    const productTab = searchParams.get("productTab");
    if (productTab === "inventory") router.replace("/inventory?tab=goods");
    else if (productTab === "consumables") router.replace("/products?kind=consumable");
    else if (productTab === "bundles") setTab("bundles");
    else if (productTab === "tax-rules") setTab("taxRules");
    else if (searchParams.get("tab") === "bundles") setTab("bundles");
    else if (searchParams.get("tab") === "taxRules") setTab("taxRules");
    const requestedKind = searchParams.get("kind");
    if (requestedKind === "goods" || requestedKind === "consumable") setKind(requestedKind);
  }, [router, searchParams]);

  const load = useCallback(() => {
    Promise.all([
      dashboardApi.catalogUnified("all", search),
      dashboardApi.products(""),
      taxAccountingApi.categoryRules(),
    ])
      .then(([unified, skuRows, rulePayload]) => {
        setCatalog(unified); setProducts(skuRows); setTaxRules(rulePayload.items || []);
      })
      .catch((e) => setErr(String(e)));
  }, [search]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  const counts = useMemo(() => ({
    bundles: catalog.filter((r) => r.kind === "goods" && (r.category === "bundle" || r.category === "virtual_bundle")).length,
    baseGoods: catalog.filter((r) => r.kind === "goods" && r.category === "single").length,
    consumable: catalog.filter((r) => r.kind === "consumable").length,
  }), [catalog]);

  const bundleRows = useMemo(() => {
    const term = bundleSearch.trim().toLowerCase();
    return catalog.filter((r) => r.kind === "goods" && (r.category === "bundle" || r.category === "virtual_bundle"))
      .filter((r) => !term || `${r.code} ${r.name} ${r.barcode}`.toLowerCase().includes(term));
  }, [catalog, bundleSearch]);
  const selectedBundleIds = useMemo(
    () => bundleRows.filter((row) => selected.has(`${row.kind}-${row.id}`)).map((row) => row.id),
    [bundleRows, selected],
  );
  const catalogBaseRows = useMemo(
    () => catalog.filter((r) => !(r.kind === "goods" && (r.category === "bundle" || r.category === "virtual_bundle"))),
    [catalog],
  );
  const categoryOptions = useMemo(
    () => [...new Set(catalogBaseRows.map((row) => row.goodsCategory).filter(Boolean))].sort(),
    [catalogBaseRows],
  );
  const catalogRows = useMemo(() => {
    const filtered = catalogBaseRows
      .filter((row) => kind === "all" || row.kind === kind)
      .filter((row) => categoryFilter === "all" || row.goodsCategory === categoryFilter)
      .filter((row) => statusFilter === "all" || row.status === statusFilter);
    return [...filtered].sort((a, b) => Number(a.kind !== "goods") - Number(b.kind !== "goods"));
  }, [catalogBaseRows, categoryFilter, kind, statusFilter]);
  const selectedCatalogItems = useMemo<CatalogBulkDeleteItem[]>(
    () => catalogRows
      .filter((row) => selected.has(`${row.kind}-${row.id}`))
      .map((row) => ({ kind: row.kind, id: row.id })),
    [catalogRows, selected],
  );

  useEffect(() => {
    const visibleKeys = new Set(
      tab === "bundles"
        ? bundleRows.map((row) => `goods-${row.id}`)
        : tab === "catalog"
          ? catalogRows.map((row) => `${row.kind}-${row.id}`)
          : [],
    );
    setSelected((prev) => {
      if (!prev.size) return prev;
      const next = new Set([...prev].filter((key) => visibleKeys.has(key)));
      return next.size === prev.size ? prev : next;
    });
  }, [tab, bundleRows, catalogRows]);

  const saveCostMode = async (row: UnifiedCatalogRow) => {
    const mode = (row.costMode || "fixed") === "fixed" ? "dynamic" : "fixed";
    try { await dashboardApi.updateCostPolicy(row.id, mode, row.costTolerancePct || "0.0200"); setMsg(`${row.code} 已切换为${mode === "fixed" ? "固定成本" : "动态成本"}`); load(); } catch (e) { setErr(String(e)); }
  };

  const toggleRow = (row: UnifiedCatalogRow) => {
    const key = `${row.kind}-${row.id}`;
    setSelected((prev) => { const next = new Set(prev); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  };
  const toggleAll = (rows: UnifiedCatalogRow[]) => {
    setSelected((prev) => {
      const all = rows.every((r) => prev.has(`${r.kind}-${r.id}`));
      const next = new Set(prev);
      rows.forEach((r) => { const key = `${r.kind}-${r.id}`; if (all) next.delete(key); else next.add(key); });
      return next;
    });
  };
  const saveCategory = async (row: UnifiedCatalogRow, value: string) => {
    try {
      await dashboardApi.updateCategory(row.kind, row.id, value);
      setMsg(`${row.code} 品类已更新为「${value || "空"}」`);
      load();
    } catch (e) { setErr(String(e)); throw e; }
  };
  const applyBulkTaxCode = async () => {
    const items = [...selected].map((key) => { const [kind, id] = key.split("-"); return { kind: kind as "goods" | "consumable", id: Number(id) }; });
    if (!items.length || !bulkTaxCode.trim()) return;
    setBulkSaving(true);
    try {
      const r = await dashboardApi.bulkSetTaxCode(items, bulkTaxCode.trim(), bulkOverwrite);
      setMsg(`税务代码已设置：更新 ${r.updated} 项${r.skipped ? `，跳过已有值 ${r.skipped} 项` : ""}${r.missing ? `，未找到 ${r.missing} 项` : ""}`);
      setSelected(new Set());
      load();
    } catch (e) { setErr(String(e)); } finally { setBulkSaving(false); }
  };
  const setCatalogStatus = async (items: CatalogBulkDeleteItem[], status: "active" | "inactive") => {
    if (!canDeleteCatalog || !items.length || statusSaving) return;
    if (status === "inactive") {
      const confirmed = window.confirm(`停用后不再参与新增业务，但保留全部历史记录。确认停用 ${items.length} 项？`);
      if (!confirmed) return;
    }
    setStatusSaving(true);
    setErr("");
    try {
      const result = await dashboardApi.bulkCatalogStatus(items, status);
      const label = status === "inactive" ? "停用" : "启用";
      setMsg(`已${label} ${result.updated} 项${result.notFound.length ? `，${result.notFound.length} 项未找到` : ""}`);
      const doneKeys = new Set(items.map((item) => `${item.kind}-${item.id}`));
      setSelected((prev) => new Set([...prev].filter((key) => !doneKeys.has(key))));
      setBlockedForInactive([]);
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setStatusSaving(false);
    }
  };
  const deleteCatalogItems = async (items: CatalogBulkDeleteItem[]) => {
    if (!canDeleteCatalog || !items.length || catalogDeleting) return;
    setCatalogDeleting(true);
    setErr("");
    setBlockedForInactive([]);
    try {
      const preview = await dashboardApi.bulkDeleteCatalog(items, true);
      const previewIssues = catalogDeleteIssueText(preview);
      if (!preview.deletedItems.length) {
        setBlockedForInactive(preview.blocked.map((item) => ({ kind: item.kind, id: item.id })));
        setErr(`没有可删除的档案${previewIssues ? `：${previewIssues}` : ""}。请改为停用，或处理历史引用后删除。`);
        return;
      }
      const confirmed = window.confirm(
        `本次选中 ${items.length} 项，预计删除 ${preview.deletedItems.length} 项${previewIssues ? `，${previewIssues}` : ""}。\n\n确认继续删除？`,
      );
      if (!confirmed) return;
      const result = await dashboardApi.bulkDeleteCatalog(items);
      const deletedKeys = new Set(result.deletedItems.map((item) => `${item.kind}-${item.id}`));
      setSelected((prev) => new Set([...prev].filter((key) => !deletedKeys.has(key))));
      setMsg(`货品档案删除完成：已删除 ${result.deleted} 项`);
      const resultIssues = catalogDeleteIssueText(result);
      if (resultIssues) {
        setErr(`未删除明细：${resultIssues}。请保留或改为停用。`);
        if (result.blocked.length) setBlockedForInactive(result.blocked.map((item) => ({ kind: item.kind, id: item.id })));
      }
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setCatalogDeleting(false);
    }
  };
  const deleteBundles = async (ids: number[]) => {
    if (!canDeleteBundles || !ids.length || bundleDeleting) return;
    const uniqueIds = [...new Set(ids)];
    setBundleDeleting(true);
    setErr("");
    try {
      const preview = await dashboardApi.bulkDeleteBundles(uniqueIds, true);
      const previewRefs = preview.blocked.slice(0, 6).map((item) => {
        const refs = item.references.map((ref) => `${ref.label}${ref.count}条`).join("、");
        return `${item.skuCode}${refs ? `（${refs}）` : ""}`;
      }).join("；");
      const previewMore = preview.blocked.length > 6 ? `；另有 ${preview.blocked.length - 6} 个档案因引用保留` : "";
      const confirmed = window.confirm(
        `本次选中 ${uniqueIds.length} 个套装：预计删除 ${preview.deletedIds.length} 个，保留 ${preview.blocked.length} 个，${preview.invalid.length + preview.notFound.length} 个无效。${previewRefs ? `\n\n保留原因：${previewRefs}${previewMore}` : ""}\n\n确认继续删除？`,
      );
      if (!confirmed) return;
      const result = await dashboardApi.bulkDeleteBundles(uniqueIds);
      setSelected((prev) => {
        const next = new Set(prev);
        result.deletedIds.forEach((id) => next.delete(`goods-${id}`));
        return next;
      });
      const blockedDetails = result.blocked.map((item) => {
        const refs = item.references.map((ref) => `${ref.label}${ref.count}条`).join("、");
        return `${item.skuCode}${refs ? `（${refs}）` : ""}`;
      }).join("；");
      const invalidDetails = result.invalid.map((item) => item.skuCode).join("、");
      const notFoundDetails = result.notFound.join("、");
      const notDeleted = [blockedDetails, invalidDetails && `无效档案：${invalidDetails}`, notFoundDetails && `不存在ID：${notFoundDetails}`].filter(Boolean).join("；");
      if (notDeleted) {
        setMsg(`套装批量删除完成：已删除 ${result.deleted} 个，${result.blocked.length} 个因历史业务引用保留`);
        setErr(`未删除明细：${notDeleted}。请保留或改为停用。`);
      } else {
        setMsg(`套装批量删除完成：已删除 ${result.deleted} 个`);
      }
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBundleDeleting(false);
    }
  };
  const deleteSelectedBundles = async () => {
    if (!selectedBundleIds.length) return;
    await deleteBundles(selectedBundleIds);
  };
  const saveProduct = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await dashboardApi.saveProduct({
        ...productForm,
        sku_id: editing || undefined,
        tax_category_rule_id: productForm.tax_category_rule_id ? Number(productForm.tax_category_rule_id) : undefined,
      });
      setMsg(editing ? "货品档案已修改" : "货品档案已新建"); setEditing(null); load();
    } catch (e) { setErr(String(e)); }
  };
  const startEditProduct = (row: UnifiedCatalogRow) => {
    setEditing(row.id);
    setProductForm({ jackyun_sku_id: row.jackyunSkuId || "", sku_code: row.code, product_type: row.category in TYPE_LABEL ? row.category : "single", sku_name: row.name, barcode: row.barcode, unit: row.unit, sale_price: row.salePrice || "", default_cost: row.defaultCost || "", cost_mode: row.costMode || "fixed", cost_tolerance_pct: row.costTolerancePct || "0.0200", tax_code: row.taxCode || "", tax_category_rule_id: row.taxCategoryRuleId ? String(row.taxCategoryRuleId) : "", goods_category: row.goodsCategory || "", status: row.status });
  };
  const selectTab = (next: ProductTab) => {
    setTab(next);
    setSelected(new Set());
    // 用工作区注入的 searchParams（本 Tab 自己那一份）拼参数，跳转仍走 router，
    // 这样工作区才会把新 query 同步到本 Tab 的 URL 快照上。
    const params = new URLSearchParams(searchParams.toString());
    params.delete("productTab");
    if (next === "catalog") params.delete("tab");
    else params.set("tab", next);
    syncWorkspaceUrl(`/products${params.toString() ? `?${params}` : ""}`);
  };
  const startNewProduct = () => { if (!canEdit) return; setEditing(0); setProductForm(emptyProduct); selectTab("catalog"); };
  const startNewBundle = () => { if (!canEdit) return; setEditing(0); setProductForm({ ...emptyProduct, product_type: "bundle", unit: "套" }); selectTab("bundles"); };
  const exportMasterData = async (dataset: MasterDataset, label: string, template = false) => {
    setExportingMaster(dataset);
    setErr("");
    try {
      const params = new URLSearchParams();
      if (dataset === "catalog") {
        if (kind !== "all") params.set("kind", kind);
        if (search.trim()) params.set("q", search.trim());
      } else if (dataset === "bundles" && bundleSearch.trim()) {
        params.set("q", bundleSearch.trim());
      }
      if (template) params.set("template", "true");
      const query = params.toString();
      const endpoint = dataset === "catalog" ? "catalog" : dataset;
      const result = await authenticatedFetch(`/api/v1/data/export/${endpoint}${query ? `?${query}` : ""}`, { cache: "no-store" });
      if (!result.ok) {
        const body = await result.json().catch(() => ({})) as { detail?: unknown };
        throw new Error(typeof body.detail === "string" ? body.detail : `导出失败（${result.status}）`);
      }
      const blob = await result.blob();
      const disposition = result.headers.get("content-disposition") || "";
      const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const filename = encodedName ? decodeURIComponent(encodedName) : `${label}-${template ? "模板" : "核验"}.xlsx`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      const count = result.headers.get("x-export-row-count");
      setMsg(`${label}${template ? "模板" : "已导出"}${!template && count ? `（${count} 条）` : ""}`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setExportingMaster(null);
    }
  };
  const importMasterData = async (dataset: MasterDataset, label: string, file: File | undefined) => {
    if (!file) return;
    setImportingMaster(dataset);
    setErr("");
    try {
      const result = await masterDataApi.importXlsx(dataset, file);
      const mappings = result.mappingsCreated || result.mappingsRemoved
        ? `，映射新增 ${result.mappingsCreated ?? 0}、移除 ${result.mappingsRemoved ?? 0}`
        : "";
      const linked = result.linkedProducts || result.linkedConsumables
        ? `，已同步关联货品 ${result.linkedProducts ?? 0}、耗材 ${result.linkedConsumables ?? 0}`
        : "";
      setMsg(`${label}已导入：新增 ${result.created}、更新 ${result.updated}${mappings}${linked}`);
      load();
    } catch (e) {
      setErr(String(e));
    } finally {
      setImportingMaster(null);
    }
  };

  const openConsumableEditor = (row?: UnifiedCatalogRow) => {
    if (row) {
      setConsumableDraft({
        id: row.id, code: row.code, name: row.name, barcode: row.barcode,
        category: row.category, unit: row.unit,
        purchase_unit_cost: "", min_stock_qty: row.minStock || "", tax_code: row.taxCode || "", tax_category_rule_id: row.taxCategoryRuleId ? String(row.taxCategoryRuleId) : "",
        sku_ids: row.linkedSkus.map((s) => s.skuId),
      });
      setConsumableEditor(row.id);
    } else {
      setConsumableDraft(emptyConsumable);
      setConsumableEditor("new");
    }
    selectTab("catalog");
    setSkuSearch("");
  };
  const toggleLinkedSku = (skuId: number) => {
    setConsumableDraft((d) => ({ ...d, sku_ids: d.sku_ids.includes(skuId) ? d.sku_ids.filter((x) => x !== skuId) : [...d.sku_ids, skuId] }));
  };
  const saveConsumable = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingConsumable(true);
    try {
      await consumablesApi.save({
        consumable_id: consumableEditor === "new" ? undefined : (consumableDraft.id ?? consumableEditor),
        code: consumableDraft.code,
        name: consumableDraft.name,
        barcode: consumableDraft.barcode,
        category: consumableDraft.category,
        unit: consumableDraft.unit,
        purchase_unit_cost: consumableDraft.purchase_unit_cost || null,
        min_stock_qty: consumableDraft.min_stock_qty || "0",
        tax_code: consumableDraft.tax_code,
        tax_category_rule_id: consumableDraft.tax_category_rule_id ? Number(consumableDraft.tax_category_rule_id) : undefined,
        sku_ids: consumableDraft.sku_ids,
      });
      setMsg(consumableEditor === "new" ? "耗材档案已建立（库存请通过耗材采购单收货登记；商品入库耗用由绑定关系确认）" : "耗材档案已更新");
      setConsumableEditor(null);
      load();
    } catch (e) { setErr(String(e)); } finally { setSavingConsumable(false); }
  };

  const skuCandidates = useMemo(() => {
    const term = skuSearch.trim().toLowerCase();
    return products.filter((p) => !term || `${p.skuCode} ${p.skuName} ${p.goodsName}`.toLowerCase().includes(term)).slice(0, 30);
  }, [products, skuSearch]);
  const linkedRefs = useMemo(() => {
    const map = new Map<number, LinkedSkuRef>();
    products.forEach((p) => map.set(p.id, { skuId: p.id, skuCode: p.skuCode, skuName: p.skuName || p.goodsName }));
    return consumableDraft.sku_ids.map((id) => map.get(id)).filter(Boolean) as LinkedSkuRef[];
  }, [consumableDraft.sku_ids, products]);

  const bulkBar = selected.size > 0 && <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs">
    <span className="font-medium text-blue-700">已选 {selected.size} 项</span>
    <input placeholder="税务代码（19 位税收分类编码）" value={bulkTaxCode} onChange={(e) => setBulkTaxCode(e.target.value.replace(/\D/g, ""))} maxLength={21} className="w-56 rounded border bg-white px-2 py-1.5 font-mono" />
    <label className="flex items-center gap-1 text-gray-600"><input type="checkbox" checked={bulkOverwrite} onChange={(e) => setBulkOverwrite(e.target.checked)} className="h-3.5 w-3.5 accent-blue-600" />覆盖已有值（默认仅填空缺）</label>
    <button onClick={applyBulkTaxCode} disabled={bulkSaving || !bulkTaxCode.trim()} className="rounded bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50">{bulkSaving ? "保存中…" : "批量设置税务代码"}</button>
    {canDeleteCatalog
      ? <>
        <button onClick={() => void deleteCatalogItems(selectedCatalogItems)} disabled={catalogDeleting} className="rounded bg-rose-600 px-3 py-1.5 font-medium text-white hover:bg-rose-700 disabled:cursor-wait disabled:opacity-50">{catalogDeleting ? "删除中…" : "删除选中"}</button>
        <button onClick={() => void setCatalogStatus(selectedCatalogItems, "inactive")} disabled={statusSaving} className="rounded bg-slate-600 px-3 py-1.5 font-medium text-white hover:bg-slate-700 disabled:cursor-wait disabled:opacity-50">{statusSaving ? "处理中…" : "停用选中"}</button>
        <button onClick={() => void setCatalogStatus(selectedCatalogItems, "active")} disabled={statusSaving} className="rounded border border-emerald-300 bg-white px-3 py-1.5 font-medium text-emerald-700 hover:bg-emerald-50 disabled:cursor-wait disabled:opacity-50">启用选中</button>
      </>
      : <><span title="仅管理员可删除货品档案" className="rounded border border-rose-200 bg-white px-3 py-1.5 text-rose-400">删除选中（仅管理员）</span>
        <span title="仅管理员可停用/启用货品档案" className="rounded border border-slate-200 bg-white px-3 py-1.5 text-slate-400">停用/启用（仅管理员）</span></>}
    <button onClick={() => setSelected(new Set())} className="rounded border bg-white px-3 py-1.5 text-gray-600 hover:bg-gray-50">取消选择</button>
  </div>;
  const pageMeta = tab === "bundles"
    ? {
        breadcrumb: "基础货品 / 套装档案",
        title: "套装档案",
        description: "独立维护套装与虚拟组合套装 SKU；货品档案不再通过页内 TAB 混合切换。",
        badge: "套装与虚拟组合",
        tone: "indigo",
      }
    : tab === "taxRules"
      ? {
          breadcrumb: "财务中心 / 财务分类",
          title: "财务分类",
          description: "维护货品与耗材共用的财务分类规则、匹配方式与税务代码；归属财务中心统一管理。",
          badge: "财务规则",
          tone: "indigo",
        }
      : {
          breadcrumb: "基础货品 / 货品档案",
          title: "货品档案",
          description: "统一维护正品与耗材基础资料；库存、仓库与流水统一收口到库存中心。",
          badge: "正品与耗材统一档案",
          tone: "blue",
        };

  const bundleBulkBar = selectedBundleIds.length > 0 && <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs">
    <span className="font-medium text-rose-700">已选 {selectedBundleIds.length} 个套装</span>
    <span className="text-rose-600">仅删除没有历史业务引用的档案</span>
    {canDeleteBundles
      ? <button onClick={() => void deleteSelectedBundles()} disabled={bundleDeleting} className="rounded bg-rose-600 px-3 py-1.5 font-medium text-white hover:bg-rose-700 disabled:cursor-wait disabled:opacity-50">{bundleDeleting ? "删除中…" : "批量删除"}</button>
      : <span title="仅管理员可批量删除套装档案" className="rounded border border-rose-200 bg-white px-3 py-1.5 text-rose-400">批量删除（仅管理员）</span>}
    <button onClick={() => setSelected(new Set())} className="rounded border bg-white px-3 py-1.5 text-gray-600 hover:bg-gray-50">取消选择</button>
  </div>;
  return <div className="mx-auto max-w-[1600px]">

    <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className={`text-[11px] font-medium tracking-wide ${pageMeta.tone === "indigo" ? "text-indigo-600" : "text-blue-600"}`}>{pageMeta.breadcrumb}</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{pageMeta.title}</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">{pageMeta.description}</p>
        </div>
        <span className={`rounded-full px-3 py-1.5 text-xs font-medium ${pageMeta.tone === "indigo" ? "bg-indigo-50 text-indigo-700" : "bg-blue-50 text-blue-700"}`}>{pageMeta.badge}</span>
      </div>
    </header>

    {tab === "catalog" && <section className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="货品档案指标">
      <CatalogMetric label="正品数量" value={String(counts.baseGoods)} hint="单品档案" tone="blue" />
      <CatalogMetric label="耗材数量" value={String(counts.consumable)} hint="耗材档案" tone="amber" />
      <CatalogMetric label="套装数量" value={String(counts.bundles)} hint="套装档案已独立到左侧菜单" tone="indigo" />
      <CatalogMetric label="最近变更" value="—" hint="当前接口未返回档案更新时间" tone="slate" />
    </section>}

    {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}{canDeleteCatalog && blockedForInactive.length > 0 && <button onClick={() => void setCatalogStatus(blockedForInactive, "inactive")} disabled={statusSaving} className="ml-3 rounded bg-slate-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:cursor-wait disabled:opacity-60">改为停用（{blockedForInactive.length} 项）</button>}<button className="ml-3" onClick={() => { setErr(""); setBlockedForInactive([]); }}>关闭</button></div>}
    {msg && <div className="mt-4 rounded-lg bg-green-50 p-3 text-sm text-green-700">{msg}</div>}

    {tab === "catalog" && <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-sm font-semibold text-slate-900">正品与耗材</h2><p className="mt-1 text-xs text-slate-500">正品、耗材统一维护基础资料；库存数量与库存状态请前往库存总览。</p></div>
        <div className="flex flex-wrap items-center gap-2">
          {canEdit && <><button onClick={startNewProduct} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700">新增正品</button>
          <button onClick={() => openConsumableEditor()} className="rounded-lg bg-amber-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-600">新增耗材</button></>}
          <MasterDataActions dataset="catalog" label="货品档案" canEdit={canEdit} exporting={exportingMaster} importing={importingMaster} onExport={exportMasterData} onImport={importMasterData} />
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="货品名称、SKU、条码" className="h-9 w-64 rounded-lg border border-slate-200 bg-slate-50/60 px-3 text-sm outline-none focus:border-blue-400 focus:bg-white" />
        <select aria-label="类型筛选" value={kind} onChange={(e) => setKind(e.target.value as KindFilter)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">类型：全部</option><option value="goods">类型：正品</option><option value="consumable">类型：耗材</option></select>
        <select aria-label="品类筛选" value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)} className="h-9 max-w-48 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">品类：全部</option>{categoryOptions.map((category) => <option key={category} value={category}>{category}</option>)}</select>
        <select aria-label="状态筛选" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-600 outline-none focus:border-blue-400"><option value="all">状态：全部</option><option value="active">状态：启用</option><option value="inactive">状态：停用</option></select>
        <span className="ml-auto text-xs text-slate-400">共 {catalogRows.length} 条</span>
      </div>

      {editing === 0 && <ProductEditor form={productForm} setForm={setProductForm} onSubmit={saveProduct} onCancel={() => setEditing(null)} taxRules={taxRules} />}

      {bulkBar}
      <CatalogTable rows={catalogRows} onEditConsumable={openConsumableEditor} onEditProduct={startEditProduct} onToggleCostMode={saveCostMode} onSaveCategory={saveCategory} onDeleteRow={(row) => void deleteCatalogItems([{ kind: row.kind, id: row.id }])} selected={selected} onToggleRow={toggleRow} onToggleAll={() => toggleAll(catalogRows)} editingProductId={editing} productForm={productForm} setProductForm={setProductForm} onSaveProduct={saveProduct} onCancelProduct={() => setEditing(null)} taxRules={taxRules} canEdit={canEdit} canDelete={canDeleteCatalog} />
    </section>}

    {tab === "bundles" && <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><h2 className="text-sm font-medium text-gray-700">套装与虚拟组合</h2><span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-600">{counts.bundles} 个 SKU</span></div><p className="mt-1 text-xs text-gray-400">单独维护套装与虚拟组合套装 SKU 主档；当前系统没有套装组成明细表，因此模板只维护主档字段，不虚构组成关系。</p></div><div className="flex flex-wrap items-center gap-2"><MasterDataActions dataset="bundles" label="套装" canEdit={canEdit} exporting={exportingMaster} importing={importingMaster} onExport={exportMasterData} onImport={importMasterData} /><input value={bundleSearch} onChange={(e) => setBundleSearch(e.target.value)} placeholder="搜索编码、条码或名称" className="w-56 rounded-lg border px-3 py-1.5 text-sm" /></div></div>
      {canEdit && editing !== 0 && <div className="mt-3 flex justify-end"><button type="button" onClick={startNewBundle} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700">+ 新建套装</button></div>}
      {editing === 0 && <ProductEditor form={productForm} setForm={setProductForm} onSubmit={saveProduct} onCancel={() => setEditing(null)} taxRules={taxRules} />}
      {bundleBulkBar}
      <CatalogTable rows={bundleRows} onEditConsumable={openConsumableEditor} onEditProduct={startEditProduct} onToggleCostMode={saveCostMode} onSaveCategory={saveCategory} onDeleteRow={(row) => void deleteBundles([row.id])} selected={selected} onToggleRow={toggleRow} onToggleAll={() => toggleAll(bundleRows)} editingProductId={editing} productForm={productForm} setProductForm={setProductForm} onSaveProduct={saveProduct} onCancelProduct={() => setEditing(null)} taxRules={taxRules} canEdit={canEdit} canDelete={canDeleteBundles} />
    </section>}

    {tab === "taxRules" && <>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-100 bg-indigo-50/60 px-4 py-3"><div><h2 className="text-sm font-semibold text-indigo-900">分类规则</h2><p className="mt-1 text-xs text-indigo-700">分类规则、匹配方式和税务代码独立导入导出；修改已关联规则后，货品税务代码会同步更新。</p></div><MasterDataActions dataset="tax_rules" label="财务分类" canEdit={canEdit} exporting={exportingMaster} importing={importingMaster} onExport={exportMasterData} onImport={importMasterData} /></div>
      <TaxCategoryRulesPanel onChanged={load} canEdit={canEdit} />
    </>}

    {consumableEditor !== null && <div className="fixed inset-0 z-modal flex items-center justify-center bg-slate-900/30 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) setConsumableEditor(null); }}>
      <form onSubmit={saveConsumable} className="max-h-[92vh] w-full max-w-2xl overflow-auto rounded-2xl bg-white p-5 shadow-2xl">
          <div className="flex items-start justify-between"><div><h3 className="text-base font-semibold text-slate-800">{consumableEditor === "new" ? "新建耗材档案" : "编辑耗材档案"}</h3><p className="mt-1 text-xs text-slate-400">编码首次手填（建议 HC-CH- + 条形码）；库存不在此修改，入库走耗材采购单收货，出库走绑定商品的商品入库单确认耗用。</p></div><button type="button" onClick={() => setConsumableEditor(null)} className="text-xl leading-none text-slate-300 hover:text-slate-500">×</button></div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-xs">
          <label className="col-span-2">耗材编码<span className="text-red-500">*</span><div className="mt-1.5 flex gap-2"><input required value={consumableDraft.code} onChange={(e) => setConsumableDraft({ ...consumableDraft, code: e.target.value })} placeholder="如 HC-CH-2020240528003（多耗材加 -BX/-LB/-CT 后缀）" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /><button type="button" disabled={!consumableDraft.barcode.trim()} onClick={() => setConsumableDraft((d) => ({ ...d, code: `HC-CH-${d.barcode.trim()}` }))} className="h-9 shrink-0 rounded-lg border border-slate-200 px-3 text-slate-600 hover:border-amber-300 hover:text-amber-600 disabled:opacity-40">HC-CH-+条码</button></div></label>
          <label className="col-span-2">耗材名称<span className="text-red-500">*</span><input required value={consumableDraft.name} onChange={(e) => setConsumableDraft({ ...consumableDraft, name: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>条形码<input value={consumableDraft.barcode} onChange={(e) => setConsumableDraft({ ...consumableDraft, barcode: e.target.value })} placeholder="可与正品条码相同" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>单位<input required value={consumableDraft.unit} onChange={(e) => setConsumableDraft({ ...consumableDraft, unit: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>分类<input value={consumableDraft.category} onChange={(e) => setConsumableDraft({ ...consumableDraft, category: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>财务分类规则<select value={consumableDraft.tax_category_rule_id} onChange={(e) => { const id = e.target.value; const rule = taxRules.find((item) => String(item.id) === id); setConsumableDraft({ ...consumableDraft, tax_category_rule_id: id, tax_code: rule?.taxCode || consumableDraft.tax_code }); }} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 outline-none focus:border-amber-400"><option value="">不关联财务分类</option>{taxRules.map((rule) => <option key={rule.id} value={rule.id}>{rule.categoryName} · {rule.itemName}{rule.taxCode ? ` · ${rule.taxCode}` : " · 未配置代码"}</option>)}</select></label>
          <label>税务代码<input value={consumableDraft.tax_code} onChange={(e) => setConsumableDraft({ ...consumableDraft, tax_code: e.target.value.replace(/\D/g, ""), tax_category_rule_id: "" })} placeholder="税收分类编码（开票用）" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 font-mono outline-none focus:border-amber-400" /></label>
          <label>安全库存<input value={consumableDraft.min_stock_qty} onChange={(e) => setConsumableDraft({ ...consumableDraft, min_stock_qty: e.target.value })} placeholder="0 = 不预警" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label className="col-span-2">参考采购单价<input value={consumableDraft.purchase_unit_cost} onChange={(e) => setConsumableDraft({ ...consumableDraft, purchase_unit_cost: e.target.value })} placeholder="可空，收货时按实付更新" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
        </div>
        <div className="mt-4 rounded-xl border border-slate-200 p-3">
          <div className="flex items-center justify-between"><div><h4 className="text-xs font-semibold text-slate-700">关联正品</h4><p className="mt-0.5 text-[10px] text-slate-400">一个耗材可关联多个正品 SKU（多对多），保存后永久生效。</p></div><span className="text-[10px] text-slate-400">已选 {consumableDraft.sku_ids.length}</span></div>
          {linkedRefs.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{linkedRefs.map((ref) => <span key={ref.skuId} className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"><span className="font-mono">{ref.skuCode}</span> {ref.skuName}<button type="button" onClick={() => toggleLinkedSku(ref.skuId)} className="text-amber-400 hover:text-red-500">×</button></span>)}</div>}
          <input value={skuSearch} onChange={(e) => setSkuSearch(e.target.value)} placeholder="搜索正品 SKU 编码或名称" className="mt-2 h-8 w-full rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-amber-400" />
          <div className="mt-2 max-h-40 divide-y divide-slate-100 overflow-auto rounded-lg border border-slate-100">{skuCandidates.map((p) => { const checked = consumableDraft.sku_ids.includes(p.id); return <button type="button" key={p.id} onClick={() => toggleLinkedSku(p.id)} className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-[11px] ${checked ? "bg-amber-50" : "hover:bg-slate-50"}`}><span className="min-w-0 truncate text-slate-600"><span className="font-mono text-indigo-500">{p.skuCode}</span> · {p.skuName || p.goodsName}</span><span className={`ml-2 shrink-0 ${checked ? "text-amber-600" : "text-slate-300"}`}>{checked ? "已选 ✓" : "选择"}</span></button>; })}{!skuCandidates.length && <div className="px-3 py-3 text-[11px] text-slate-400">没有匹配的正品 SKU</div>}</div>
        </div>
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setConsumableEditor(null)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button><button type="submit" disabled={savingConsumable} className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50">{savingConsumable ? "保存中…" : "保存档案"}</button></div>
      </form>
    </div>}
  </div>;
}
