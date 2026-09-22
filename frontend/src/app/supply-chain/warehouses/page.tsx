"use client";

import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Warehouse = {
  id: number;
  code: string;
  name: string;
  warehouseType: "factory" | "b2c" | "other";
  purpose: "goods" | "consumable" | "both";
  isSellable: boolean;
  status: "active" | "inactive";
  note: string;
  jackyunWarehouseId: string | null;
  source: "jackyun" | "local";
};

type Draft = {
  code: string;
  name: string;
  warehouse_type: "factory" | "b2c" | "other";
  purpose: "goods" | "consumable" | "both";
  is_sellable: boolean;
  status: "active" | "inactive";
  note: string;
  jackyun_warehouse_id: string;
};

const emptyDraft = (): Draft => ({
  code: "",
  name: "",
  warehouse_type: "other",
  purpose: "both",
  is_sellable: false,
  status: "active",
  note: "",
  jackyun_warehouse_id: "",
});

const input = "h-9 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 text-sm text-slate-700 outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-2 focus:ring-indigo-100";
const cell = "px-3 py-2.5 align-middle";

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function toDraft(row: Warehouse): Draft {
  return {
    code: row.code,
    name: row.name,
    warehouse_type: row.warehouseType,
    purpose: row.purpose,
    is_sellable: row.isSellable,
    status: row.status,
    note: row.note,
    jackyun_warehouse_id: row.jackyunWarehouseId ?? "",
  };
}

function draftChanged(row: Warehouse, draft: Draft) {
  return row.code !== draft.code
    || row.name !== draft.name
    || row.warehouseType !== draft.warehouse_type
    || row.purpose !== draft.purpose
    || row.isSellable !== draft.is_sellable
    || row.status !== draft.status
    || row.note !== draft.note
    || (row.jackyunWarehouseId ?? "") !== draft.jackyun_warehouse_id;
}

export default function WarehousesPage() {
  const [rows, setRows] = useState<Warehouse[]>([]);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [newRow, setNewRow] = useState<Draft>(emptyDraft());
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<Warehouse[]>("/api/v1/warehouses?include_inactive=true");
      setRows(data);
      setDrafts(Object.fromEntries(data.map((row) => [row.id, toDraft(row)])));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load().catch((e) => setError(String(e))); }, [load]);

  async function save(id: number) {
    const draft = drafts[id];
    if (!draft) return;
    setBusyId(id); setError(""); setMessage("");
    try {
      await api(`/api/v1/warehouses/${id}`, { method: "PATCH", body: JSON.stringify(draft) });
      setMessage("仓库已保存。历史采购、收货和库存继续引用同一个仓库 ID。");
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusyId(null); }
  }

  async function create() {
    setBusyId("new"); setError(""); setMessage("");
    try {
      await api("/api/v1/warehouses", { method: "POST", body: JSON.stringify(newRow) });
      setAdding(false); setNewRow(emptyDraft()); setMessage("新仓库已添加。");
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusyId(null); }
  }

  async function remove(id: number) {
    const row = rows.find((item) => item.id === id);
    if (!row || !window.confirm(`确定物理删除仓库“${row.name}”吗？\n\n已被库存或历史单据使用的仓库无法删除。`)) return;
    setBusyId(id); setError(""); setMessage("");
    try {
      await api(`/api/v1/warehouses/${id}`, { method: "DELETE" });
      setMessage(`仓库“${row.name}”已删除。`);
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusyId(null); }
  }

  function patch(id: number, values: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...values } }));
  }

  const activeCount = rows.filter((row) => row.status === "active").length;
  const sellableCount = rows.filter((row) => row.status === "active" && row.isSellable).length;
  const factoryCount = rows.filter((row) => row.warehouseType === "factory").length;
  const boundCount = rows.filter((row) => Boolean(row.jackyunWarehouseId)).length;

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 pb-8">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[11px] font-medium text-indigo-600">
              <span>库存中心</span><span className="text-slate-300">/</span><span>仓库档案</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2.5">
              <h1 className="text-2xl font-semibold tracking-tight text-slate-900">仓库档案</h1>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600">统一仓库档案</span>
            </div>
            <p className="mt-1 text-xs text-slate-500">维护仓库类型、用途、吉客云绑定与可售口径，采购入库和库存计算都引用这里的仓库 ID。</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button type="button" onClick={() => void load()} disabled={loading || busyId !== null} className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-600 transition hover:border-indigo-200 hover:text-indigo-600 disabled:cursor-wait disabled:opacity-50">{loading ? "刷新中…" : "刷新"}</button>
            <button type="button" onClick={() => { setAdding(true); setMessage(""); setError(""); }} disabled={adding} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm shadow-indigo-200 transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50">＋ 新增仓库</button>
          </div>
        </div>
      </header>

      {message && <div role="status" className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3.5 py-2.5 text-sm text-emerald-700"><span className="h-2 w-2 shrink-0 rounded-full bg-emerald-500" />{message}</div>}
      {error && <div role="alert" className="flex items-center gap-2 rounded-xl border border-rose-100 bg-rose-50 px-3.5 py-2.5 text-sm text-rose-700"><span className="h-2 w-2 shrink-0 rounded-full bg-rose-500" />{error}</div>}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="仓库概览">
        <SummaryCard mark="总" title="仓库总数" value={rows.length} hint="包含停用仓库" tone="indigo" />
        <SummaryCard mark="启" title="已启用" value={activeCount} hint={`${rows.length - activeCount} 个已停用`} tone="emerald" />
        <SummaryCard mark="售" title="可售仓" value={sellableCount} hint={`${boundCount} 个已绑定吉客云`} tone="blue" />
        <SummaryCard mark="厂" title="工厂仓" value={factoryCount} hint="默认不参与 B2C 可售" tone="amber" />
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3.5">
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-base font-semibold text-slate-900">仓库档案</h2>
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">{rows.length} 个仓库</span>
            </div>
            <p className="mt-1 text-xs text-slate-400">直接在表格内修改，点击该行“保存”后才会写入系统。</p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-700"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />{activeCount} 个启用</span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-slate-600"><span className="h-1.5 w-1.5 rounded-full bg-slate-400" />{boundCount} 个已绑定吉客云</span>
          </div>
        </div>

        <div className="border-b border-slate-100 bg-slate-50/70 px-4 py-2 text-[11px] text-slate-500">
          <span className="font-medium text-slate-700">维护字段</span><span className="mx-2 text-slate-300">·</span>编码、名称、类型、用途、吉客云仓库 ID、可售状态、启停状态和备注
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[1280px] text-left text-[13px]">
            <thead className="bg-slate-50/95 text-[11px] font-medium text-slate-500">
              <tr>
                <th className={`${cell} w-[150px]`}>仓库编码</th>
                <th className={`${cell} w-[210px]`}>仓库名称</th>
                <th className={`${cell} w-[150px]`}>仓库类型</th>
                <th className={`${cell} w-[170px]`}>库存用途</th>
                <th className={`${cell} w-[235px]`}>吉客云仓库 ID</th>
                <th className={`${cell} w-[120px]`}>参与可售</th>
                <th className={`${cell} w-[125px]`}>状态</th>
                <th className={`${cell} min-w-[235px]`}>备注</th>
                <th className={`${cell} w-[150px] text-right`}>操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {adding && <EditableRow key="new-row" draft={newRow} setDraft={setNewRow} busy={busyId === "new"} dirty isNew source="local" onSave={() => void create()} onCancel={() => { setAdding(false); setNewRow(emptyDraft()); }} />}
              {rows.map((row) => {
                const draft = drafts[row.id] ?? toDraft(row);
                return <EditableRow key={row.id} draft={draft} setDraft={(next) => patch(row.id, next)} busy={busyId === row.id} dirty={draftChanged(row, draft)} source={row.source} onSave={() => void save(row.id)} onDelete={() => void remove(row.id)} />;
              })}
            </tbody>
          </table>
          {!rows.length && !adding && <div className="p-12 text-center"><div className="text-sm font-medium text-slate-500">暂无仓库档案</div><p className="mt-1 text-xs text-slate-400">点击右上角“新增仓库”开始维护。</p></div>}
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-[1.25fr_.75fr]">
        <div className="rounded-2xl border border-indigo-100 bg-indigo-50/60 px-4 py-3.5 text-xs leading-6 text-slate-600">
          <div className="flex gap-2.5"><span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-indigo-500" /><div><div className="font-medium text-slate-800">仓库使用口径</div><p className="mt-0.5">工厂仓存放工厂耗材或工厂侧货品，默认不参与 B2C 可售；B2C 仓承接最终电商发货库存，并可绑定吉客云真实仓库。</p></div></div>
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 text-xs leading-6 text-slate-500">
          <div className="font-medium text-slate-800">维护提醒</div><p className="mt-0.5">同一个吉客云仓库 ID 只绑定一个本地仓库，避免入库和库存被拆到不同仓。</p>
        </div>
      </section>
    </div>
  );
}

function EditableRow({ draft, setDraft, busy, dirty, isNew, source, onSave, onDelete, onCancel }: {
  draft: Draft;
  setDraft: (next: Draft) => void;
  busy: boolean;
  dirty?: boolean;
  isNew?: boolean;
  source: "jackyun" | "local";
  onSave: () => void;
  onDelete?: () => void;
  onCancel?: () => void;
}) {
  const change = (next: Partial<Draft>) => setDraft({ ...draft, ...next });
  return <tr className={`${isNew ? "bg-indigo-50/40" : draft.status === "inactive" ? "bg-slate-50/70 text-slate-400" : "bg-white"} transition-colors hover:bg-slate-50`}>
    <td className={cell}><input value={draft.code} onChange={(e) => change({ code: e.target.value.toUpperCase() })} className={`${input} w-32 font-mono`} placeholder="FACTORY" /></td>
    <td className={cell}><input value={draft.name} onChange={(e) => change({ name: e.target.value })} className={`${input} w-48`} placeholder="仓库名称" /></td>
    <td className={cell}><select value={draft.warehouse_type} onChange={(e) => change({ warehouse_type: e.target.value as Draft["warehouse_type"] })} className={`${input} w-32`}><option value="factory">工厂仓</option><option value="b2c">B2C仓</option><option value="other">其他</option></select></td>
    <td className={cell}><select value={draft.purpose} onChange={(e) => change({ purpose: e.target.value as Draft["purpose"] })} className={`${input} w-36`}><option value="both">正品 + 耗材</option><option value="goods">仅正品</option><option value="consumable">仅耗材</option></select></td>
    <td className={cell}><div className="flex items-center gap-1.5"><input value={draft.jackyun_warehouse_id} onChange={(e) => change({ jackyun_warehouse_id: e.target.value })} className={`${input} w-40 font-mono`} placeholder={draft.warehouse_type === "factory" ? "工厂仓留空" : "可选"} /><span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${source === "jackyun" ? "bg-teal-50 text-teal-600" : "bg-slate-100 text-slate-500"}`}>{source === "jackyun" ? "吉客云" : "本地"}</span></div></td>
    <td className={cell}><label className="inline-flex items-center gap-2"><input type="checkbox" checked={draft.is_sellable} onChange={(e) => change({ is_sellable: e.target.checked })} className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-200" /><span className={`text-xs font-medium ${draft.is_sellable ? "text-emerald-700" : "text-slate-400"}`}>{draft.is_sellable ? "是" : "否"}</span></label></td>
    <td className={cell}><select value={draft.status} onChange={(e) => change({ status: e.target.value as Draft["status"] })} className={`${input} w-24`}><option value="active">启用</option><option value="inactive">停用</option></select></td>
    <td className={cell}><input value={draft.note} onChange={(e) => change({ note: e.target.value })} className={`${input} w-full min-w-[220px]`} placeholder="补充仓库说明（可选）" /></td>
    <td className={`${cell} whitespace-nowrap text-right`}><div className="flex items-center justify-end gap-2">{dirty ? <span className="text-[11px] text-amber-600">未保存</span> : <span className="text-[11px] text-emerald-600">已保存</span>}<button type="button" disabled={busy || !draft.code.trim() || !draft.name.trim()} onClick={onSave} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white shadow-sm shadow-indigo-100 transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "保存中…" : "保存"}</button>{onDelete && <button type="button" disabled={busy} onClick={onDelete} title="物理删除未被业务数据引用的仓库" className="rounded-lg border border-rose-200 bg-white px-2.5 py-1.5 text-xs text-rose-600 transition hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-40">删除</button>}{onCancel && <button type="button" disabled={busy} onClick={onCancel} className="text-xs text-slate-500 hover:text-slate-800">取消</button>}</div></td>
  </tr>;
}

function SummaryCard({ mark, title, value, hint, tone }: { mark: string; title: string; value: number; hint: string; tone: "indigo" | "emerald" | "blue" | "amber" }) {
  const toneClass = tone === "emerald" ? "bg-emerald-50 text-emerald-600" : tone === "blue" ? "bg-blue-50 text-blue-600" : tone === "amber" ? "bg-amber-50 text-amber-600" : "bg-indigo-50 text-indigo-600";
  return <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white px-4 py-3.5 shadow-sm">
    <div><div className="text-xs text-slate-500">{title}</div><div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900 tabular-nums">{value}</div><div className="mt-1 text-[11px] text-slate-400">{hint}</div></div>
    <span className={`flex h-10 w-10 items-center justify-center rounded-xl text-sm font-semibold ${toneClass}`}>{mark}</span>
  </div>;
}
