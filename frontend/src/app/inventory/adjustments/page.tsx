"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "@/components/workspace/workspace-link";
import {
  inventoryStocktakeApi,
  type InventoryStocktakeCandidate,
  type InventoryStocktakeItem,
  type InventoryStocktakeTask,
  type WarehouseRow,
  warehousesApi,
} from "@/lib/api";

type StocktakeKind = "goods" | "consumable";
type TaskStatus = InventoryStocktakeTask["status"];
type DraftCount = { actual: string; reason: string };

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: "bg-slate-100 text-slate-600 ring-slate-200",
  counting: "bg-blue-50 text-blue-700 ring-blue-200",
  review: "bg-amber-50 text-amber-700 ring-amber-200",
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  cancelled: "bg-rose-50 text-rose-600 ring-rose-200",
};

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "待盘点",
  counting: "盘点中",
  review: "待确认",
  completed: "已完成",
  cancelled: "已取消",
};

function numberText(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("zh-CN", { maximumFractionDigits: 4 })
    : value || "0";
}

function fmtDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function difference(actual: string, book: string) {
  if (actual.trim() === "") return null;
  const a = Number(actual);
  const b = Number(book);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return a - b;
}

function kindLabel(kind: StocktakeKind) {
  return kind === "goods" ? "正品" : "耗材";
}

export default function InventoryStocktakePage() {
  const [warehouses, setWarehouses] = useState<WarehouseRow[]>([]);
  const [tasks, setTasks] = useState<InventoryStocktakeTask[]>([]);
  const [selectedTask, setSelectedTask] = useState<InventoryStocktakeTask | null>(null);
  const [draft, setDraft] = useState<Record<number, DraftCount>>({});
  const [statusFilter, setStatusFilter] = useState<"all" | TaskStatus>("all");
  const [createOpen, setCreateOpen] = useState(false);
  const [scope, setScope] = useState<"all" | "partial">("partial");
  const [warehouseId, setWarehouseId] = useState("");
  const [kinds, setKinds] = useState<StocktakeKind[]>(["goods"]);
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [stockCondition, setStockCondition] = useState<"all" | "nonzero" | "zero" | "negative">("all");
  const [note, setNote] = useState("");
  const [candidates, setCandidates] = useState<InventoryStocktakeCandidate[]>([]);
  const [selectedRefs, setSelectedRefs] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [candidateLoading, setCandidateLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [createError, setCreateError] = useState("");
  const [message, setMessage] = useState("");
  const [dirty, setDirty] = useState(false);

  const loadTasks = useCallback(async () => {
    const next = await inventoryStocktakeApi.list();
    setTasks(next);
    return next;
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([warehousesApi.list(false), inventoryStocktakeApi.list()])
      .then(([warehouseRows, taskRows]) => {
        setWarehouses(warehouseRows);
        setTasks(taskRows);
        const firstWarehouse = warehouseRows.find((row) => row.status === "active");
        if (firstWarehouse) setWarehouseId(String(firstWarehouse.id));
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setLoading(false));
  }, []);

  const selectedWarehouse = warehouses.find((row) => String(row.id) === warehouseId) ?? null;
  const allowedKinds = useMemo<StocktakeKind[]>(() => {
    if (!selectedWarehouse) return [];
    if (selectedWarehouse.purpose === "goods") return ["goods"];
    if (selectedWarehouse.purpose === "consumable") return ["consumable"];
    return ["goods", "consumable"];
  }, [selectedWarehouse]);

  useEffect(() => {
    if (!selectedWarehouse) return;
    setKinds((current) => {
      const kept = current.filter((kind) => allowedKinds.includes(kind));
      return kept.length ? kept : allowedKinds.slice(0, 1);
    });
  }, [allowedKinds, selectedWarehouse]);

  const visibleCandidates = useMemo(() => {
    if (stockCondition === "all") return candidates;
    return candidates.filter((item) => {
      const qty = Number(item.bookQty || 0);
      if (stockCondition === "nonzero") return qty !== 0;
      if (stockCondition === "zero") return qty === 0;
      return qty < 0;
    });
  }, [candidates, stockCondition]);

  const categories = useMemo(
    () => Array.from(new Set(candidates.map((item) => item.category).filter(Boolean))).sort(),
    [candidates],
  );

  const filteredTasks = useMemo(
    () => statusFilter === "all" ? tasks : tasks.filter((task) => task.status === statusFilter),
    [statusFilter, tasks],
  );

  const stats = useMemo(() => ({
    pending: tasks.filter((task) => task.status === "pending").length,
    counting: tasks.filter((task) => task.status === "counting").length,
    review: tasks.filter((task) => task.status === "review").length,
    completed: tasks.filter((task) => task.status === "completed").length,
  }), [tasks]);

  async function loadCandidates() {
    if (!selectedWarehouse || kinds.length === 0) return;
    setCandidateLoading(true);
    setCreateError("");
    try {
      const rows = await inventoryStocktakeApi.candidates(
        selectedWarehouse.id,
        kinds,
        scope === "partial" ? search : "",
        scope === "partial" ? category : "",
      );
      setCandidates(rows);
      if (scope === "all") {
        setSelectedRefs(new Set(rows.map((row) => `${row.kind}:${row.id}`)));
      } else {
        setSelectedRefs((current) => new Set([...current].filter((key) => rows.some((row) => `${row.kind}:${row.id}` === key))));
      }
    } catch (caught) {
      setCandidates([]);
      setCreateError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCandidateLoading(false);
    }
  }

  useEffect(() => {
    if (!createOpen || !selectedWarehouse || kinds.length === 0) return;
    const timer = window.setTimeout(() => void loadCandidates(), 180);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createOpen, scope, warehouseId, kinds.join(","), search, category]);

  async function openTask(taskId: number) {
    setBusy("detail");
    setError("");
    try {
      const detail = await inventoryStocktakeApi.detail(taskId);
      setSelectedTask(detail);
      const nextDraft: Record<number, DraftCount> = {};
      for (const item of detail.items ?? []) {
        nextDraft[item.id] = { actual: item.actualQty ?? "", reason: item.reason ?? "" };
      }
      setDraft(nextDraft);
      setDirty(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  function toggleKind(kind: StocktakeKind) {
    if (!allowedKinds.includes(kind)) return;
    setKinds((current) => {
      if (current.includes(kind)) {
        return current.length === 1 ? current : current.filter((value) => value !== kind);
      }
      return [...current, kind];
    });
    setSelectedRefs(new Set());
  }

  function toggleCandidate(item: InventoryStocktakeCandidate) {
    const key = `${item.kind}:${item.id}`;
    setSelectedRefs((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  async function createTask() {
    if (!selectedWarehouse || kinds.length === 0) return;
    if (scope === "partial" && selectedRefs.size === 0) {
      setCreateError("部分盘点至少选择一个正品或耗材。");
      return;
    }
    setBusy("create");
    setCreateError("");
    setMessage("");
    try {
      const selectedItems = scope === "partial"
        ? candidates
            .filter((item) => selectedRefs.has(`${item.kind}:${item.id}`))
            .map((item) => ({ kind: item.kind, id: item.id }))
        : [];
      const created = await inventoryStocktakeApi.create({
        scope,
        warehouse_id: selectedWarehouse.id,
        item_kinds: kinds,
        selected_items: selectedItems,
        search: scope === "partial" ? search : "",
        category: scope === "partial" ? category : "",
        note,
      });
      setCreateOpen(false);
      setMessage(`已创建盘点任务 ${created.number}，共 ${created.itemCount} 项。`);
      setSearch("");
      setCategory("");
      setStockCondition("all");
      setNote("");
      setSelectedRefs(new Set());
      setCandidates([]);
      await loadTasks();
      await openTask(created.id);
    } catch (caught) {
      setCreateError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  function updateDraft(itemId: number, patch: Partial<DraftCount>) {
    setDraft((current) => ({
      ...current,
      [itemId]: { ...(current[itemId] ?? { actual: "", reason: "" }), ...patch },
    }));
    setDirty(true);
  }

  function fillBookQty() {
    if (!selectedTask?.items) return;
    setDraft((current) => {
      const next = { ...current };
      for (const item of selectedTask.items ?? []) {
        if ((next[item.id]?.actual ?? "").trim() === "") {
          next[item.id] = { actual: item.bookQty, reason: next[item.id]?.reason ?? "" };
        }
      }
      return next;
    });
    setDirty(true);
  }

  async function saveCounts() {
    if (!selectedTask?.items?.length) return;
    setBusy("save");
    setError("");
    setMessage("");
    try {
      const updated = await inventoryStocktakeApi.saveCounts(
        selectedTask.id,
        selectedTask.items.map((item) => ({
          id: item.id,
          actual_qty: (draft[item.id]?.actual ?? "").trim() || null,
          reason: draft[item.id]?.reason ?? "",
        })),
      );
      setSelectedTask(updated);
      const nextDraft: Record<number, DraftCount> = {};
      for (const item of updated.items ?? []) nextDraft[item.id] = { actual: item.actualQty ?? "", reason: item.reason ?? "" };
      setDraft(nextDraft);
      setDirty(false);
      setMessage(updated.status === "review" ? "实盘数量已保存，任务已进入待确认。" : "盘点进度已保存。");
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  async function confirmTask() {
    if (!selectedTask || dirty) return;
    if (!window.confirm(`确认盘点任务 ${selectedTask.number} 的差异并调整库存？\n\n确认后将写入正式库存流水，已完成任务不能取消。`)) return;
    setBusy("confirm");
    setError("");
    setMessage("");
    try {
      const updated = await inventoryStocktakeApi.confirm(selectedTask.id);
      setSelectedTask(updated);
      setMessage(`盘点任务 ${updated.number} 已完成，库存差异已经写入库存事实。`);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  async function cancelTask() {
    if (!selectedTask || selectedTask.status === "completed") return;
    if (!window.confirm(`取消盘点任务 ${selectedTask.number}？已填写的盘点草稿会保留在任务记录中，但不会调整库存。`)) return;
    setBusy("cancel");
    setError("");
    try {
      const updated = await inventoryStocktakeApi.cancel(selectedTask.id);
      setSelectedTask(updated);
      setDirty(false);
      setMessage(`盘点任务 ${updated.number} 已取消，没有产生库存调整。`);
      await loadTasks();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  const detailItems = selectedTask?.items ?? [];
  const localCounted = detailItems.filter((item) => (draft[item.id]?.actual ?? "").trim() !== "").length;
  const localDiffCount = detailItems.filter((item) => {
    const diff = difference(draft[item.id]?.actual ?? "", item.bookQty);
    return diff !== null && Math.abs(diff) > 0.00005;
  }).length;
  const reasonsComplete = detailItems.every((item) => {
    const diff = difference(draft[item.id]?.actual ?? "", item.bookQty);
    return diff === null || Math.abs(diff) <= 0.00005 || Boolean((draft[item.id]?.reason ?? "").trim());
  });

  return (
    <div className="mx-auto max-w-[1440px] space-y-4 pb-8">
      <header className="rounded-2xl border border-slate-200 bg-white px-5 py-4 shadow-sm dark:border-slate-700 dark:bg-slate-900">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium tracking-wide text-blue-600 dark:text-blue-300">库存中心 / 库存盘点</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 dark:text-slate-100">库存盘点</h1>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500 dark:text-slate-400">
              先建立盘点任务，再录入实盘数量；系统自动计算盘盈盘亏。差异确认后才调整正式库存，避免直接手工填写“加减数量”。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/inventory" className="rounded-lg border border-slate-200 px-3.5 py-2 text-xs font-medium text-slate-600 hover:border-blue-200 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300">
              返回库存总览
            </Link>
            <button type="button" onClick={() => { setCreateOpen(true); setCreateError(""); }} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-blue-700">
              ＋ 新建盘点
            </button>
          </div>
        </div>
      </header>

      {message && <div role="status" className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-200">{message}</div>}
      {error && <div role="alert" className="rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">{error}</div>}

      <section className="grid gap-3 sm:grid-cols-4">
        {([
          ["pending", "待盘点", stats.pending],
          ["counting", "盘点中", stats.counting],
          ["review", "待确认", stats.review],
          ["completed", "已完成", stats.completed],
        ] as const).map(([key, label, value]) => (
          <button key={key} type="button" onClick={() => setStatusFilter(statusFilter === key ? "all" : key)} className={`rounded-xl border bg-white px-4 py-3 text-left shadow-sm transition hover:border-blue-200 dark:bg-slate-900 ${statusFilter === key ? "border-blue-300 ring-2 ring-blue-100 dark:ring-blue-500/20" : "border-slate-200 dark:border-slate-700"}`}>
            <div className="text-[11px] text-slate-400">{label}</div>
            <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900 dark:text-slate-100">{value}</div>
          </button>
        ))}
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5 dark:border-slate-800">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">盘点任务</h2>
            <p className="mt-0.5 text-[11px] text-slate-400">任务状态：待盘点 → 盘点中 → 待确认 → 已完成</p>
          </div>
          {statusFilter !== "all" && <button type="button" onClick={() => setStatusFilter("all")} className="text-xs font-medium text-blue-600 hover:underline">查看全部任务</button>}
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-[980px] w-full text-left text-xs">
            <thead className="bg-slate-50 text-[11px] font-medium text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              <tr>
                <th className="px-4 py-2.5">盘点单号</th>
                <th className="px-4 py-2.5">仓库</th>
                <th className="px-4 py-2.5">范围</th>
                <th className="px-4 py-2.5">类型</th>
                <th className="px-4 py-2.5 text-right">进度</th>
                <th className="px-4 py-2.5 text-right">差异项</th>
                <th className="px-4 py-2.5">状态</th>
                <th className="px-4 py-2.5">创建时间</th>
                <th className="px-4 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {filteredTasks.map((task) => (
                <tr key={task.id} className="hover:bg-slate-50/70 dark:hover:bg-slate-800/30">
                  <td className="px-4 py-3 font-mono font-medium text-slate-800 dark:text-slate-100">{task.number}</td>
                  <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{task.warehouseName}</td>
                  <td className="px-4 py-3 text-slate-600 dark:text-slate-300">{task.scopeLabel}</td>
                  <td className="px-4 py-3 text-slate-500">{task.itemKinds.map(kindLabel).join(" + ")}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-600 dark:text-slate-300">{task.countedCount}/{task.itemCount}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    <span className={task.differenceCount ? "font-semibold text-amber-600" : "text-slate-400"}>{task.differenceCount}</span>
                  </td>
                  <td className="px-4 py-3"><span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${STATUS_TONE[task.status]}`}>{task.statusLabel}</span></td>
                  <td className="px-4 py-3 text-slate-400">{fmtDate(task.createdAt)}</td>
                  <td className="px-4 py-3 text-right"><button type="button" onClick={() => void openTask(task.id)} className="font-medium text-blue-600 hover:underline">{task.status === "completed" ? "查看" : "继续盘点"}</button></td>
                </tr>
              ))}
              {!loading && filteredTasks.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-12 text-center text-sm text-slate-400">当前没有盘点任务，点击右上角“新建盘点”开始。</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {selectedTask && (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-mono text-sm font-semibold text-slate-900 dark:text-slate-100">{selectedTask.number}</h2>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${STATUS_TONE[selectedTask.status]}`}>{STATUS_LABEL[selectedTask.status]}</span>
                {dirty && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700 ring-1 ring-amber-200">有未保存修改</span>}
              </div>
              <p className="mt-1 text-[11px] text-slate-400">
                {selectedTask.warehouseName} · {selectedTask.scopeLabel} · {selectedTask.itemKinds.map(kindLabel).join(" + ")} · 创建人 {selectedTask.createdBy || "—"}
              </p>
            </div>
            <button type="button" onClick={() => setSelectedTask(null)} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-500 dark:border-slate-700">收起</button>
          </div>

          <div className="grid gap-2 border-b border-slate-100 bg-slate-50/60 px-5 py-3 sm:grid-cols-4 dark:border-slate-800 dark:bg-slate-800/30">
            <div><div className="text-[10px] text-slate-400">盘点明细</div><div className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-100">{detailItems.length} 项</div></div>
            <div><div className="text-[10px] text-slate-400">已实盘</div><div className="mt-0.5 text-sm font-semibold text-blue-700 dark:text-blue-300">{localCounted}/{detailItems.length}</div></div>
            <div><div className="text-[10px] text-slate-400">当前差异</div><div className={`mt-0.5 text-sm font-semibold ${localDiffCount ? "text-amber-600" : "text-emerald-600"}`}>{localDiffCount} 项</div></div>
            <div><div className="text-[10px] text-slate-400">库存调整</div><div className="mt-0.5 text-sm font-semibold text-slate-800 dark:text-slate-100">{selectedTask.status === "completed" ? "已写入" : "尚未写入"}</div></div>
          </div>

          {selectedTask.status !== "completed" && selectedTask.status !== "cancelled" && (
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-5 py-3 dark:border-slate-800">
              <p className="text-[11px] text-slate-400">实盘数 - 账面数 = 盘点差异；有差异的行必须填写原因。</p>
              <div className="flex gap-2">
                <button type="button" onClick={fillBookQty} className="rounded-lg border border-slate-200 px-3 py-1.5 text-[11px] font-medium text-slate-600 hover:border-blue-200 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300">未填写项 = 账面数</button>
                <button type="button" onClick={() => void saveCounts()} disabled={Boolean(busy) || !dirty} className="rounded-lg bg-blue-600 px-3.5 py-1.5 text-[11px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">{busy === "save" ? "保存中…" : "保存盘点进度"}</button>
              </div>
            </div>
          )}

          <div className="max-h-[560px] overflow-auto">
            <table className="min-w-[1080px] w-full text-left text-xs">
              <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-medium text-slate-500 shadow-[0_1px_0_rgba(226,232,240,1)] dark:bg-slate-800 dark:text-slate-400">
                <tr>
                  <th className="px-4 py-2.5">类型</th>
                  <th className="px-4 py-2.5">编码 / 名称</th>
                  <th className="px-4 py-2.5">分类</th>
                  <th className="px-4 py-2.5 text-right">账面数</th>
                  <th className="px-4 py-2.5 w-[150px]">实盘数</th>
                  <th className="px-4 py-2.5 text-right">差异</th>
                  <th className="px-4 py-2.5 w-[280px]">差异原因</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                {detailItems.map((item) => {
                  const rowDraft = draft[item.id] ?? { actual: item.actualQty ?? "", reason: item.reason ?? "" };
                  const diff = difference(rowDraft.actual, item.bookQty);
                  const hasDiff = diff !== null && Math.abs(diff) > 0.00005;
                  const readOnly = selectedTask.status === "completed" || selectedTask.status === "cancelled";
                  return (
                    <tr key={item.id} className={hasDiff ? "bg-amber-50/35 dark:bg-amber-500/5" : ""}>
                      <td className="px-4 py-2.5"><span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${item.kind === "goods" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{kindLabel(item.kind)}</span></td>
                      <td className="px-4 py-2.5"><div className="font-mono font-medium text-slate-800 dark:text-slate-100">{item.code}</div><div className="mt-0.5 max-w-[320px] truncate text-[11px] text-slate-400" title={item.name}>{item.name}</div></td>
                      <td className="px-4 py-2.5 text-slate-500">{item.category || "—"}</td>
                      <td className="px-4 py-2.5 text-right font-mono tabular-nums text-slate-600 dark:text-slate-300">{numberText(item.bookQty)} {item.unit}</td>
                      <td className="px-4 py-2.5">
                        {readOnly ? <span className="font-mono tabular-nums text-slate-700 dark:text-slate-200">{numberText(rowDraft.actual || null)}</span> : (
                          <input
                            value={rowDraft.actual}
                            onChange={(event) => updateDraft(item.id, { actual: event.target.value })}
                            inputMode="decimal"
                            placeholder="输入实盘数"
                            className="h-8 w-full rounded-lg border border-slate-200 bg-white px-2.5 font-mono text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950"
                          />
                        )}
                      </td>
                      <td className={`px-4 py-2.5 text-right font-mono font-semibold tabular-nums ${hasDiff ? diff! > 0 ? "text-emerald-600" : "text-rose-600" : "text-slate-400"}`}>
                        {diff === null ? "—" : `${diff > 0 ? "+" : ""}${numberText(String(diff))}`}
                      </td>
                      <td className="px-4 py-2.5">
                        {readOnly ? <span className="text-slate-600 dark:text-slate-300">{rowDraft.reason || "—"}</span> : (
                          <input
                            value={rowDraft.reason}
                            onChange={(event) => updateDraft(item.id, { reason: event.target.value })}
                            placeholder={hasDiff ? "必填：如盘盈、盘亏、破损、计量差异" : "无差异可不填"}
                            className={`h-8 w-full rounded-lg border bg-white px-2.5 text-xs outline-none dark:bg-slate-950 ${hasDiff && !rowDraft.reason.trim() ? "border-amber-300 focus:border-amber-500" : "border-slate-200 focus:border-blue-400 dark:border-slate-700"}`}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selectedTask.status !== "completed" && selectedTask.status !== "cancelled" && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-4 dark:border-slate-800">
              <button type="button" onClick={() => void cancelTask()} disabled={Boolean(busy)} className="rounded-lg border border-rose-200 px-3.5 py-2 text-xs font-medium text-rose-600 hover:bg-rose-50 disabled:opacity-40">取消任务</button>
              <div className="flex items-center gap-3">
                <span className="text-[11px] text-slate-400">
                  {localCounted < detailItems.length
                    ? `还有 ${detailItems.length - localCounted} 项未盘点`
                    : !reasonsComplete
                      ? "差异项还需要填写原因"
                      : dirty
                        ? "请先保存盘点进度"
                        : selectedTask.status === "review"
                          ? "可以确认并调整库存"
                          : "请保存后确认"}
                </span>
                <button
                  type="button"
                  onClick={() => void confirmTask()}
                  disabled={Boolean(busy) || dirty || selectedTask.status !== "review" || localCounted !== detailItems.length || !reasonsComplete}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy === "confirm" ? "确认中…" : "确认并调整库存"}
                </button>
              </div>
            </div>
          )}
        </section>
      )}

      {createOpen && (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[1px]">
          <div className="flex max-h-[92vh] w-full max-w-[1100px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
              <div><h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">新建盘点任务</h2><p className="mt-1 text-xs text-slate-400">先确定仓库和范围。全部盘点会锁定当前仓库内所选类型的全部有效档案；部分盘点只生成已勾选项目。</p></div>
              <button type="button" onClick={() => setCreateOpen(false)} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-500 dark:border-slate-700">关闭</button>
            </div>

            <div className="overflow-y-auto p-5">
              <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
                <button type="button" onClick={() => { setScope("all"); setSearch(""); setCategory(""); }} className={`rounded-xl border p-4 text-left transition ${scope === "all" ? "border-blue-400 bg-blue-50 ring-2 ring-blue-100 dark:bg-blue-500/10 dark:ring-blue-500/20" : "border-slate-200 hover:border-blue-200 dark:border-slate-700"}`}>
                  <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">全部盘点</div>
                  <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">盘点选定仓库内全部有效正品 / 耗材，适合月末、季末或完整盘库。</div>
                </button>
                <button type="button" onClick={() => setScope("partial")} className={`rounded-xl border p-4 text-left transition ${scope === "partial" ? "border-blue-400 bg-blue-50 ring-2 ring-blue-100 dark:bg-blue-500/10 dark:ring-blue-500/20" : "border-slate-200 hover:border-blue-200 dark:border-slate-700"}`}>
                  <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">部分盘点</div>
                  <div className="mt-1 text-xs leading-5 text-slate-500 dark:text-slate-400">按商品 / SKU、分类和库存条件筛选后勾选，适合异常复核和日常抽盘。</div>
                </button>
              </div>

              <div className="mt-4 grid gap-3 md:grid-cols-[1.2fr_1fr]">
                <label className="text-xs text-slate-600 dark:text-slate-300">盘点仓库
                  <select value={warehouseId} onChange={(event) => { setWarehouseId(event.target.value); setSelectedRefs(new Set()); }} className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950">
                    <option value="">请选择仓库</option>
                    {warehouses.filter((row) => row.status === "active").map((row) => <option key={row.id} value={row.id}>{row.code} · {row.name}</option>)}
                  </select>
                </label>
                <div>
                  <div className="text-xs text-slate-600 dark:text-slate-300">盘点类型</div>
                  <div className="mt-1.5 flex h-10 items-center gap-2">
                    {(["goods", "consumable"] as StocktakeKind[]).map((kind) => {
                      const enabled = allowedKinds.includes(kind);
                      const checked = kinds.includes(kind);
                      return <button key={kind} type="button" disabled={!enabled} onClick={() => toggleKind(kind)} className={`h-9 rounded-lg border px-4 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-35 ${checked ? "border-blue-300 bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300" : "border-slate-200 text-slate-500 dark:border-slate-700"}`}>{kindLabel(kind)}</button>;
                    })}
                  </div>
                </div>
              </div>

              {scope === "partial" && (
                <div className="mt-4 grid gap-3 md:grid-cols-3">
                  <label className="text-xs text-slate-600 dark:text-slate-300">商品 / SKU
                    <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="编码、名称、条码" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950" />
                  </label>
                  <label className="text-xs text-slate-600 dark:text-slate-300">分类
                    <input list="stocktake-categories" value={category} onChange={(event) => setCategory(event.target.value)} placeholder="全部分类" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950" />
                    <datalist id="stocktake-categories">{categories.map((value) => <option key={value} value={value} />)}</datalist>
                  </label>
                  <label className="text-xs text-slate-600 dark:text-slate-300">库存条件
                    <select value={stockCondition} onChange={(event) => setStockCondition(event.target.value as typeof stockCondition)} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950">
                      <option value="all">全部</option><option value="nonzero">账面库存 ≠ 0</option><option value="zero">账面库存 = 0</option><option value="negative">账面库存 &lt; 0</option>
                    </select>
                  </label>
                </div>
              )}

              <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-2.5 dark:border-slate-800 dark:bg-slate-800/40">
                  <div className="text-xs font-medium text-slate-700 dark:text-slate-200">{scope === "all" ? "预计盘点范围" : "选择盘点项目"}</div>
                  <div className="flex items-center gap-3 text-[11px] text-slate-400">
                    <span>{candidateLoading ? "读取中…" : `匹配 ${visibleCandidates.length} 项`}</span>
                    {scope === "partial" && <span>已选 <strong className="text-blue-600">{selectedRefs.size}</strong> 项</span>}
                    {scope === "partial" && visibleCandidates.length > 0 && (
                      <button type="button" onClick={() => setSelectedRefs(new Set(visibleCandidates.map((row) => `${row.kind}:${row.id}`)))} className="font-medium text-blue-600 hover:underline">全选当前结果</button>
                    )}
                  </div>
                </div>
                <div className="max-h-[300px] overflow-auto">
                  <table className="min-w-[760px] w-full text-left text-xs">
                    <thead className="sticky top-0 bg-white text-[10px] text-slate-400 shadow-[0_1px_0_rgba(226,232,240,1)] dark:bg-slate-900">
                      <tr><th className="w-12 px-3 py-2"></th><th className="px-3 py-2">类型</th><th className="px-3 py-2">编码 / 名称</th><th className="px-3 py-2">分类</th><th className="px-3 py-2 text-right">当前账面数</th></tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                      {visibleCandidates.map((item) => {
                        const key = `${item.kind}:${item.id}`;
                        const checked = scope === "all" || selectedRefs.has(key);
                        return (
                          <tr key={key} onClick={() => scope === "partial" && toggleCandidate(item)} className={scope === "partial" ? "cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/30" : ""}>
                            <td className="px-3 py-2"><input type="checkbox" readOnly checked={checked} disabled={scope === "all"} className="h-3.5 w-3.5 rounded border-slate-300" /></td>
                            <td className="px-3 py-2"><span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${item.kind === "goods" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{kindLabel(item.kind)}</span></td>
                            <td className="px-3 py-2"><div className="font-mono font-medium text-slate-700 dark:text-slate-200">{item.code}</div><div className="mt-0.5 max-w-[360px] truncate text-[11px] text-slate-400">{item.name}</div></td>
                            <td className="px-3 py-2 text-slate-500">{item.category || "—"}</td>
                            <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-700 dark:text-slate-200">{numberText(item.bookQty)} {item.unit}</td>
                          </tr>
                        );
                      })}
                      {!candidateLoading && visibleCandidates.length === 0 && <tr><td colSpan={5} className="px-4 py-8 text-center text-xs text-slate-400">当前条件下没有可盘点项目。</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>

              <label className="mt-4 block text-xs text-slate-600 dark:text-slate-300">盘点备注
                <textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="可选，例如：9月月末仓库盘点" rows={2} className="mt-1.5 w-full rounded-lg border border-slate-200 px-3 py-2 text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-950" />
              </label>

              {createError && <div className="mt-3 rounded-lg border border-rose-100 bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-700 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">{createError}</div>}
              {selectedWarehouse && kinds.includes("consumable") && (
                <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
                  耗材当前库存底层仍按“自有仓 / 工厂仓”两类汇总。系统只会在该口径能唯一对应当前仓库时创建盘点，无法安全拆分时会直接阻止，不会猜库存。
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-4 dark:border-slate-800">
              <span className="text-[11px] text-slate-400">
                {scope === "all" ? `将创建 ${candidates.length} 项全部盘点任务` : `已选择 ${selectedRefs.size} 项`}
              </span>
              <div className="flex gap-2">
                <button type="button" onClick={() => setCreateOpen(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs font-medium text-slate-600 dark:border-slate-700 dark:text-slate-300">取消</button>
                <button type="button" onClick={() => void createTask()} disabled={Boolean(busy) || candidateLoading || !selectedWarehouse || !kinds.length || !candidates.length || (scope === "partial" && !selectedRefs.size)} className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">{busy === "create" ? "创建中…" : "创建盘点任务"}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
