"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CatalogSkuRow,
  consumablesApi,
  ConsumableMappingRow,
  ConsumableRow,
  ConsumableTransactionRow,
  warehousesApi,
  WarehouseRow,
} from "@/lib/api";

type Props = {
  rows: ConsumableRow[];
  products: CatalogSkuRow[];
  reload: () => void;
  notify: (message: string) => void;
  fail: (message: string) => void;
  onImport: (event: React.ChangeEvent<HTMLInputElement>) => void;
  /** 打开耗材档案编辑弹窗（由父页面统一档案提供）。 */
  onEdit: (id: number) => void;
  /** 打开耗材档案新建弹窗（由父页面统一档案提供）。 */
  onCreate: () => void;
};

type Filter = "all" | "low" | "unmapped" | "reconcile";

const EMPTY_TRANSACTION = {
  transaction_type: "purchase",
  quantity: "",
  unit_cost: "",
  location: "own",
  warehouse_id: "",
  note: "",
};

const TX_OPTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: "purchase", label: "采购入库", hint: "入库到已选实际仓库" },
  { value: "send_factory", label: "发往工厂", hint: "库存 → 在途（历史流转）" },
  { value: "factory_receive", label: "工厂收货", hint: "在途 → 工厂仓" },
  { value: "consume", label: "领用消耗", hint: "从已选实际仓库扣减" },
  { value: "stocktake", label: "盘点", hint: "按实际数量校正已选仓库" },
  { value: "loss", label: "报损", hint: "从已选实际仓库扣减" },
  { value: "manual", label: "手工调整", hint: "正数增加、负数扣减" },
  { value: "adjustment", label: "盘点调整", hint: "历史导入兼容" },
];

function txLabel(type: string) {
  return TX_OPTIONS.find((o) => o.value === type)?.label ?? "盘点调整";
}

function locationLabel(location: string | null) {
  if (location === "factory") return "工厂";
  if (location === "own") return "自有仓";
  return "";
}

function numberOf(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function qty(value: string | null | undefined) {
  return numberOf(value).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
}

function money(value: string | null | undefined) {
  if (value == null || value === "") return "—";
  return `¥${numberOf(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function hasHistoricalDifference(row: ConsumableRow) {
  return Math.abs(
    numberOf(row.purchasedQty)
      - numberOf(row.usedQty)
      - numberOf(row.stockQty)
      - numberOf(row.factoryQty),
  ) > 0.01;
}

/** 流水数量的显示符号。 */
function txSign(type: string) {
  if (type === "consume" || type === "loss") return "-";
  if (type === "purchase" || type === "factory_receive") return "+";
  if (type === "send_factory") return "→";
  return "±";
}

export default function ConsumableWorkbench({ rows, products, reload, notify, fail, onImport, onEdit, onCreate }: Props) {
  const [selectedId, setSelectedId] = useState<number | null>(rows[0]?.id ?? null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [category, setCategory] = useState("all");
  const [mappings, setMappings] = useState<ConsumableMappingRow[]>([]);
  const [transactions, setTransactions] = useState<ConsumableTransactionRow[]>([]);
  const [mapping, setMapping] = useState({ sku_id: "", usage_per_unit: "1" });
  const [transaction, setTransaction] = useState(EMPTY_TRANSACTION);
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [transactionSaving, setTransactionSaving] = useState(false);

  const consumableWarehouses = useMemo(
    () => warehouses.filter((row) => row.status === "active" && (row.purpose === "consumable" || row.purpose === "both")),
    [warehouses],
  );

  const transactionOptions = useMemo(() => {
    const directFactoryWarehouse = consumableWarehouses.length === 1 && consumableWarehouses[0].warehouseType === "factory";
    return directFactoryWarehouse
      ? TX_OPTIONS.filter((option) => option.value !== "send_factory" && option.value !== "factory_receive")
      : TX_OPTIONS;
  }, [consumableWarehouses]);

  const categories = useMemo(
    () => Array.from(new Set(rows.map((row) => row.category).filter(Boolean))).sort(),
    [rows],
  );

  const filteredRows = useMemo(() => {
    const term = query.trim().toLowerCase();
    return rows.filter((row) => {
      const textMatch = !term || `${row.code} ${row.name} ${row.barcode || ""} ${row.category}`.toLowerCase().includes(term);
      const categoryMatch = category === "all" || row.category === category;
      const filterMatch =
        filter === "all" ||
        (filter === "low" && row.lowStock) ||
        (filter === "unmapped" && row.mappingCount === 0) ||
        (filter === "reconcile" && hasHistoricalDifference(row));
      return textMatch && categoryMatch && filterMatch;
    });
  }, [category, filter, query, rows]);

  const selected = filteredRows.find((row) => row.id === selectedId) ?? null;
  const lowStockCount = rows.filter((row) => row.lowStock).length;
  const unmappedCount = rows.filter((row) => row.mappingCount === 0).length;
  const reconcileCount = rows.filter(hasHistoricalDifference).length;
  const inventoryValue = rows.reduce(
    (sum, row) => sum + Math.max(0, numberOf(row.availableQty)) * numberOf(row.purchaseUnitCost),
    0,
  );

  useEffect(() => {
    warehousesApi.list(false).then(setWarehouses).catch((error) => fail(String(error)));
  }, [fail]);

  useEffect(() => {
    if (transaction.warehouse_id || !consumableWarehouses.length) return;
    const warehouse = consumableWarehouses[0];
    setTransaction((current) => ({
      ...current,
      warehouse_id: String(warehouse.id),
      location: warehouse.warehouseType === "factory" ? "factory" : "own",
    }));
  }, [consumableWarehouses, transaction.warehouse_id]);

  useEffect(() => {
    if (transactionOptions.some((option) => option.value === transaction.transaction_type)) return;
    setTransaction((current) => ({ ...current, transaction_type: transactionOptions[0]?.value ?? "purchase" }));
  }, [transaction.transaction_type, transactionOptions]);

  useEffect(() => {
    if (filteredRows.length === 0) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (!filteredRows.some((row) => row.id === selectedId)) {
      setSelectedId(filteredRows[0].id);
    }
  }, [filteredRows, selectedId]);

  useEffect(() => {
    if (!selected) {
      setMappings([]);
      setTransactions([]);
      return;
    }
    let cancelled = false;
    Promise.all([consumablesApi.mappings(selected.id), consumablesApi.transactions(selected.id)])
      .then(([mappingRows, transactionRows]) => {
        if (cancelled) return;
        setMappings(mappingRows);
        setTransactions(transactionRows);
      })
      .catch((error) => {
        if (!cancelled) fail(String(error));
      });
    return () => {
      cancelled = true;
    };
  }, [fail, selected]);

  async function saveMapping(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || !mapping.sku_id) return;
    try {
      await consumablesApi.saveMapping({
        consumable_id: selected.id,
        sku_id: Number(mapping.sku_id),
        usage_per_unit: mapping.usage_per_unit,
      });
      setMapping({ sku_id: "", usage_per_unit: "1" });
      setMappings(await consumablesApi.mappings(selected.id));
      reload();
      notify("SKU 用量映射已保存");
    } catch (error) {
      fail(String(error));
    }
  }

  async function removeMapping(id: number) {
    if (!window.confirm("确认删除这条 SKU 用量映射？")) return;
    try {
      await consumablesApi.deleteMapping(id);
      if (selected) setMappings(await consumablesApi.mappings(selected.id));
      reload();
      notify("SKU 用量映射已删除");
    } catch (error) {
      fail(String(error));
    }
  }

  async function addTransaction(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    if (!transaction.warehouse_id && consumableWarehouses.length) {
      fail("请选择实际耗材仓库");
      return;
    }
    setTransactionSaving(true);
    try {
      await consumablesApi.addTransaction(selected.id, {
        ...transaction,
        warehouse_id: transaction.warehouse_id ? Number(transaction.warehouse_id) : null,
      });
      setTransaction({
        ...EMPTY_TRANSACTION,
        warehouse_id: consumableWarehouses[0] ? String(consumableWarehouses[0].id) : "",
        location: consumableWarehouses[0]?.warehouseType === "factory" ? "factory" : "own",
      });
      setTransactions(await consumablesApi.transactions(selected.id));
      reload();
      notify(`${txLabel(transaction.transaction_type)}已登记，库存已更新`);
    } catch (error) {
      fail(String(error));
    } finally {
      setTransactionSaving(false);
    }
  }

  return (
    <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_30px_rgba(35,47,78,0.04)]">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-slate-800">耗材库</h2>
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600">本平台台账</span>
          </div>
          <p className="mt-1 text-xs text-slate-400">耗材不写入吉客云；在本平台按实际配置仓维护采购入库、领用消耗、盘点报损和 SKU 用量映射。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="cursor-pointer rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:border-indigo-300 hover:text-indigo-600">
            导入历史清单
            <input type="file" accept=".xlsx,.xlsm" onChange={onImport} className="hidden" />
          </label>
          <button type="button" onClick={onCreate} className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-medium text-white hover:bg-indigo-700">
            + 新建耗材
          </button>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Summary label="耗材种类" value={String(rows.length)} hint="已启用档案" />
        <Summary label="低库存 / 负库存" value={String(lowStockCount)} hint="可用 ≤ 安全库存" tone={lowStockCount ? "amber" : "normal"} />
        <Summary label="待核对历史数据" value={String(reconcileCount)} hint="采购量 − 已使用 ≠ 库存" tone={reconcileCount ? "amber" : "normal"} />
        <Summary label="可用库存金额" value={`¥${inventoryValue.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} hint={`${unmappedCount} 条未建立 SKU 映射`} />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[240px] flex-1">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索代码、条码、名称或分类" className="h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100" />
        </div>
        <select value={category} onChange={(event) => setCategory(event.target.value)} className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-xs text-slate-600 outline-none focus:border-indigo-400">
          <option value="all">全部分类</option>
          {categories.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <div className="flex rounded-lg border border-slate-200 p-0.5 text-xs">
          {([
            ["all", "全部"],
            ["low", "低库存"],
            ["unmapped", "未映射"],
            ["reconcile", "待核对"],
          ] as const).map(([key, label]) => (
            <button key={key} type="button" onClick={() => setFilter(key)} className={`rounded-md px-2.5 py-1.5 ${filter === key ? "bg-indigo-50 font-medium text-indigo-600" : "text-slate-500 hover:bg-slate-50"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(390px,1fr)]">
        <div className="overflow-hidden rounded-xl border border-slate-200">
          <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50/70 px-4 py-3">
            <div className="text-xs font-semibold text-slate-700">耗材台账</div>
            <div className="text-[11px] text-slate-400">显示 {filteredRows.length} / {rows.length} 条</div>
          </div>
          <div className="max-h-[620px] overflow-auto">
            <table className="w-full min-w-[720px] text-left text-xs">
              <thead className="sticky top-0 z-10 border-b border-slate-100 bg-white text-[11px] text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">耗材</th>
                  <th className="px-3 py-3 font-medium">三仓库存</th>
                  <th className="px-3 py-3 font-medium">使用进度</th>
                  <th className="px-3 py-3 font-medium">映射</th>
                  <th className="px-3 py-3 text-right font-medium">单价</th>
                  <th className="px-4 py-3 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filteredRows.map((row) => {
                  const progress = Math.min(100, Math.max(0, numberOf(row.usageRate) * 100));
                  const active = selectedId === row.id;
                  return (
                    <tr key={row.id} onClick={() => setSelectedId(row.id)} className={`cursor-pointer transition-colors ${active ? "bg-indigo-50/70" : "hover:bg-slate-50"}`}>
                      <td className="max-w-[280px] px-4 py-3">
                        <div className="flex items-start gap-2">
                          <span className={`mt-0.5 h-2 w-2 shrink-0 rounded-full ${row.lowStock ? "bg-amber-500" : "bg-emerald-500"}`} />
                          <div className="min-w-0">
                            <div className="truncate font-mono text-[11px] font-medium text-indigo-600">{row.code}</div>
                            <div className="mt-1 truncate font-medium text-slate-700">{row.name}</div>
                            <div className="mt-1 truncate text-[10px] text-slate-400">{row.category || "未分类"} · 单位 {row.unit}{row.barcode ? ` · ${row.barcode}` : ""}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <div className={`font-semibold tabular-nums ${Number(row.stockQty) < 0 ? "text-red-600" : row.lowStock ? "text-amber-600" : "text-slate-700"}`}>{qty(row.stockQty)} <span className="font-normal text-slate-400">自有</span></div>
                        <div className="mt-1 text-[10px] tabular-nums text-slate-400">厂 {qty(row.factoryQty)} · 途 {qty(row.transitQty)}</div>
                      </td>
                      <td className="w-32 px-3 py-3">
                        <div className="flex items-center justify-between text-[10px] text-slate-400"><span>使用率</span><span>{(numberOf(row.usageRate) * 100).toFixed(1)}%</span></div>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full ${row.lowStock ? "bg-amber-500" : "bg-indigo-500"}`} style={{ width: `${progress}%` }} /></div>
                      </td>
                      <td className="px-3 py-3">
                        <span className={`rounded-full px-2 py-1 text-[10px] ${row.mappingCount ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{row.mappingCount ? `${row.mappingCount} 个 SKU` : "未映射"}</span>
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-600">{money(row.purchaseUnitCost)}</td>
                      <td className="px-4 py-3 text-right"><button type="button" onClick={(event) => { event.stopPropagation(); onEdit(row.id); }} className="rounded-md px-2 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100">编辑</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {filteredRows.length === 0 && <div className="px-4 py-16 text-center text-xs text-slate-400">没有符合条件的耗材</div>}
          </div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white">
          {!selected ? (
            <div className="flex min-h-[420px] items-center justify-center text-xs text-slate-400">请选择一条耗材查看台账详情</div>
          ) : (
            <div>
              <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-4">
                <div className="min-w-0">
                  <div className="font-mono text-[11px] font-medium text-indigo-600">{selected.code}</div>
                  <h3 className="mt-1 truncate text-base font-semibold text-slate-800">{selected.name}</h3>
                  <div className="mt-1 text-[11px] text-slate-400">
                    {selected.category || "未分类"} · {selected.unit} · {selected.mappingCount} 个 SKU 映射
                    {selected.barcode ? <> · 条码 <span className="font-mono">{selected.barcode}</span></> : null}
                  </div>
                </div>
                <button type="button" onClick={() => onEdit(selected.id)} className="shrink-0 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-medium text-slate-600 hover:border-indigo-300 hover:text-indigo-600">编辑档案</button>
              </div>

              <div className="grid grid-cols-4 gap-px border-b border-slate-100 bg-slate-100">
                <DetailMetric label={consumableWarehouses.length === 1 && consumableWarehouses[0].warehouseType === "factory" ? consumableWarehouses[0].name : "自有仓"} value={`${qty(consumableWarehouses.length === 1 && consumableWarehouses[0].warehouseType === "factory" ? selected.factoryQty : selected.stockQty)}`} tone={selected.lowStock ? "amber" : "normal"} />
                <DetailMetric label={consumableWarehouses.length === 1 && consumableWarehouses[0].warehouseType === "factory" ? "其他库存" : "工厂仓"} value={`${qty(consumableWarehouses.length === 1 && consumableWarehouses[0].warehouseType === "factory" ? selected.stockQty : selected.factoryQty)}`} />
                <DetailMetric label="在途" value={`${qty(selected.transitQty)}`} />
                <DetailMetric label="可用库存" value={`${qty(selected.availableQty)}`} />
              </div>

              {selected.linkedSkus?.length > 0 && (
                <div className="mx-4 mt-4 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
                  关联正品：{selected.linkedSkus.map((s) => <span key={s.skuId} className="mr-2 font-mono text-indigo-500">{s.skuCode}</span>)}
                </div>
              )}

              {hasHistoricalDifference(selected) && (
                <div className="mx-4 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
                  历史账面存在差异：采购量 − 已使用量不等于当前库存。请通过“盘点”流水校正，不要直接改库存数字。
                </div>
              )}

              <div className="space-y-5 p-4">
                <section>
                  <div className="flex items-center justify-between">
                    <div><h4 className="text-xs font-semibold text-slate-700">SKU 用量映射</h4><p className="mt-1 text-[10px] text-slate-400">只定义“每个 SKU 消耗多少”，不直接扣库存。</p></div>
                    <span className="text-[10px] text-slate-400">{mappings.length} 条</span>
                  </div>
                  <form onSubmit={saveMapping} className="mt-3 grid grid-cols-[minmax(0,1fr)_90px_52px] gap-2">
                    <select required value={mapping.sku_id} onChange={(event) => setMapping({ ...mapping, sku_id: event.target.value })} className="h-8 min-w-0 rounded-md border border-slate-200 px-2 text-[10px] text-slate-600 outline-none focus:border-indigo-400">
                      <option value="">添加货品 SKU</option>
                      {products.map((product) => <option key={product.id} value={product.id}>{product.skuCode} · {product.skuName || product.goodsName}</option>)}
                    </select>
                    <input required value={mapping.usage_per_unit} onChange={(event) => setMapping({ ...mapping, usage_per_unit: event.target.value })} placeholder="每单位用量" className="h-8 rounded-md border border-slate-200 px-2 text-[10px] text-slate-600 outline-none focus:border-indigo-400" />
                    <button type="submit" className="h-8 rounded-md bg-indigo-50 text-[10px] font-medium text-indigo-600 hover:bg-indigo-100">保存</button>
                  </form>
                  <div className="mt-2 divide-y divide-slate-100 rounded-lg border border-slate-100">
                    {mappings.map((item) => <div key={item.id} className="flex items-center justify-between gap-2 px-3 py-2 text-[10px]"><span className="min-w-0 truncate text-slate-600"><span className="font-mono text-indigo-500">{item.skuCode}</span> · {item.skuName || "未命名 SKU"}</span><span className="flex shrink-0 items-center gap-2 text-slate-400">{item.usagePerUnit} / 单位 <button type="button" onClick={() => void removeMapping(item.id)} className="text-red-400 hover:text-red-600">删除</button></span></div>)}
                    {mappings.length === 0 && <div className="px-3 py-3 text-[10px] text-slate-400">暂无映射</div>}
                  </div>
                </section>

                <section className="border-t border-slate-100 pt-5">
                  <div className="flex items-center justify-between"><div><h4 className="text-xs font-semibold text-slate-700">库存流水</h4><p className="mt-1 text-[10px] text-slate-400">库存只能由流水变化；每次手工登记都必须选择实际耗材仓。</p></div><span className="text-[10px] text-slate-400">最近 {Math.min(transactions.length, 8)} 条</span></div>
                  <form onSubmit={addTransaction} className="mt-3 space-y-2">
                    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
                      <select value={transaction.transaction_type} onChange={(event) => setTransaction({ ...transaction, transaction_type: event.target.value })} className="h-8 rounded-md border border-slate-200 px-1.5 text-[10px] text-slate-600 outline-none focus:border-indigo-400">
                        {transactionOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                      <div className="flex h-8 items-center px-2 text-[10px] text-slate-400">{transactionOptions.find((o) => o.value === transaction.transaction_type)?.hint}</div>
                    </div>
                    <div className="grid grid-cols-[150px_92px_92px_minmax(0,1fr)_48px] gap-2">
                      <select
                        required={consumableWarehouses.length > 0}
                        value={transaction.warehouse_id}
                        aria-label="实际耗材仓库"
                        onChange={(event) => {
                          const warehouse = consumableWarehouses.find((row) => String(row.id) === event.target.value);
                          setTransaction({
                            ...transaction,
                            warehouse_id: event.target.value,
                            location: warehouse?.warehouseType === "factory" ? "factory" : "own",
                          });
                        }}
                        className="h-8 rounded-md border border-slate-200 px-1 text-[10px] text-slate-600 outline-none focus:border-indigo-400"
                      >
                        <option value="">选择实际仓库</option>
                        {consumableWarehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
                      </select>
                      <input required value={transaction.quantity} onChange={(event) => setTransaction({ ...transaction, quantity: event.target.value })} placeholder="数量" className="h-8 rounded-md border border-slate-200 px-2 text-[10px] text-slate-600 outline-none focus:border-indigo-400" />
                      <input value={transaction.unit_cost} onChange={(event) => setTransaction({ ...transaction, unit_cost: event.target.value })} placeholder="单位成本" className="h-8 rounded-md border border-slate-200 px-2 text-[10px] text-slate-600 outline-none focus:border-indigo-400" />
                      <input value={transaction.note} onChange={(event) => setTransaction({ ...transaction, note: event.target.value })} placeholder="来源或备注" className="h-8 min-w-0 rounded-md border border-slate-200 px-2 text-[10px] text-slate-600 outline-none focus:border-indigo-400" />
                      <button type="submit" disabled={transactionSaving} className="h-8 rounded-md bg-indigo-600 text-[10px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{transactionSaving ? "…" : "登记"}</button>
                    </div>
                  </form>
                  <div className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-100">
                    {transactions.slice(0, 8).map((item) => {
                      const sign = txSign(item.transactionType);
                      const snapshot = item.warehouseName && item.factoryBefore != null
                        ? `${item.warehouseName} ${qty(item.factoryBefore)}→${qty(item.factoryAfter)}`
                        : item.warehouseName && item.stockBefore != null
                          ? `${item.warehouseName} ${qty(item.stockBefore)}→${qty(item.stockAfter)}`
                          : item.stockBefore != null
                            ? `自有 ${qty(item.stockBefore)}→${qty(item.stockAfter)}`
                            : item.factoryBefore != null
                              ? `工厂 ${qty(item.factoryBefore)}→${qty(item.factoryAfter)}`
                          : "";
                      return (
                        <div key={item.id} className="flex items-center justify-between gap-2 px-3 py-2 text-[10px]">
                          <span className="min-w-0 truncate text-slate-600">
                            {txLabel(item.transactionType)}
                            {item.warehouseName ? <span className="ml-1 rounded bg-slate-100 px-1 text-slate-500">{item.warehouseName}</span> : locationLabel(item.location) ? <span className="ml-1 rounded bg-slate-100 px-1 text-slate-500">{locationLabel(item.location)}</span> : null}
                            {item.note ? <span className="ml-1 text-slate-400">· {item.note}</span> : null}
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            {snapshot && <span className="tabular-nums text-slate-400">{snapshot}</span>}
                            <span className={`tabular-nums ${item.transactionType === "consume" || item.transactionType === "loss" ? "text-amber-600" : "text-emerald-600"}`}>
                              {sign}{qty(item.quantity)} {selected.unit}
                            </span>
                          </span>
                        </div>
                      );
                    })}
                    {transactions.length === 0 && <div className="px-3 py-3 text-[10px] text-slate-400">暂无流水。历史导入数据会标记为期初值，后续调整请从这里登记。</div>}
                  </div>
                </section>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Summary({ label, value, hint, tone = "normal" }: { label: string; value: string; hint: string; tone?: "normal" | "amber" }) {
  return <div className="rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3"><div className="text-[11px] text-slate-400">{label}</div><div className={`mt-1 text-xl font-semibold tabular-nums ${tone === "amber" ? "text-amber-600" : "text-slate-800"}`}>{value}</div><div className="mt-1 text-[10px] text-slate-400">{hint}</div></div>;
}

function DetailMetric({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "amber" }) {
  return <div className="bg-white px-3 py-3"><div className="text-[10px] text-slate-400">{label}</div><div className={`mt-1 text-sm font-semibold tabular-nums ${tone === "amber" ? "text-amber-600" : "text-slate-700"}`}>{value}</div></div>;
}
