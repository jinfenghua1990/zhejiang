"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { authenticatedFetch, TaxCategoryRule } from "@/lib/api";

type FormState = {
  pattern: string;
  taxCode: string;
  matchKeyword: string;
  matchMode: TaxCategoryRule["matchMode"];
  priority: number;
  enabled: boolean;
  note: string;
};

const emptyForm: FormState = {
  pattern: "*软饮料*咖啡",
  taxCode: "",
  matchKeyword: "咖啡",
  matchMode: "contains",
  priority: 100,
  enabled: true,
  note: "",
};

export default function TaxCategoryRulesPanel({ onChanged, canEdit = true }: { onChanged?: () => void; canEdit?: boolean }) {
  const [items, setItems] = useState<TaxCategoryRule[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await authenticatedFetch("/api/v1/tax-accounting/category-rules", { cache: "no-store" });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `加载失败（${res.status}）`);
      setItems(payload.items || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  function startEdit(row: TaxCategoryRule) {
    setEditingId(row.id);
    setForm({
      pattern: row.pattern,
      taxCode: row.taxCode || "",
      matchKeyword: row.matchKeyword,
      matchMode: row.matchMode,
      priority: row.priority,
      enabled: row.enabled,
      note: row.note,
    });
    setError("");
    setMessage("");
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyForm);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const url = editingId
        ? `/api/v1/tax-accounting/category-rules/${editingId}`
        : "/api/v1/tax-accounting/category-rules";
      const res = await authenticatedFetch(url, {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pattern: form.pattern,
          tax_code: form.taxCode,
          match_keyword: form.matchKeyword,
          match_mode: form.matchMode,
          priority: form.priority,
          enabled: form.enabled,
          note: form.note,
        }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `保存失败（${res.status}）`);
      setMessage(editingId ? "财务分类已修改，已关联货品同步更新" : "财务分类已新增");
      resetForm();
      await load();
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function toggle(row: TaxCategoryRule) {
    setError("");
    try {
      const res = await authenticatedFetch(`/api/v1/tax-accounting/category-rules/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !row.enabled }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `更新失败（${res.status}）`);
      await load();
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <section className="mt-4 space-y-4">
      <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-4">
        <div className="text-sm font-semibold text-indigo-900">财务分类与税务代码</div>
        <p className="mt-1 text-xs leading-5 text-indigo-700">
          这里维护货品可共用的财务分类规则；在“货品档案”编辑货品时选择规则，税务代码会自动带入。修改规则代码后，已关联的正品和耗材会同步更新。
        </p>
        <div className="mt-2 grid gap-2 text-xs text-indigo-800 md:grid-cols-3">
          <div><span className="font-medium">格式：</span>*软饮料*咖啡</div>
          <div><span className="font-medium">关键字：</span>咖啡</div>
          <div><span className="font-medium">代码：</span>19 位税收分类编码</div>
        </div>
      </div>

      {canEdit ? <form onSubmit={save} className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold text-slate-800">{editingId ? "修改财务分类" : "新增财务分类"}</h2>
          {editingId && <button type="button" onClick={resetForm} className="text-xs text-slate-500 hover:text-slate-800">取消修改</button>}
        </div>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
          <label className="text-xs text-slate-500 xl:col-span-2">开票分类格式
            <input required value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })} placeholder="*其他*咖啡" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-900" />
          </label>
          <label className="text-xs text-slate-500">税务代码
            <input value={form.taxCode} onChange={(e) => setForm({ ...form, taxCode: e.target.value.replace(/\D/g, "") })} placeholder="19 位编码" maxLength={21} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 font-mono text-sm text-slate-900" />
          </label>
          <label className="text-xs text-slate-500">匹配关键字
            <input required value={form.matchKeyword} onChange={(e) => setForm({ ...form, matchKeyword: e.target.value })} placeholder="咖啡" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" />
          </label>
          <label className="text-xs text-slate-500">匹配方式
            <select value={form.matchMode} onChange={(e) => setForm({ ...form, matchMode: e.target.value as FormState["matchMode"] })} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm">
              <option value="contains">包含</option>
              <option value="exact">完全等于</option>
              <option value="prefix">开头是</option>
            </select>
          </label>
          <label className="text-xs text-slate-500">优先级
            <input type="number" min={0} max={9999} value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) || 0 })} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" />
          </label>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />启用</label>
          <label className="min-w-[240px] flex-1 text-xs text-slate-500">备注
            <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="可选" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm" />
          </label>
          <button disabled={saving} className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{saving ? "保存中…" : editingId ? "保存修改" : "添加规则"}</button>
        </div>
      </form> : <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-500">当前账号仅可查看和导出，新增、修改、启停财务分类需要管理员或操作员权限。</div>}

      {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
      {message && <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{message}</div>}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <div><h2 className="text-sm font-semibold text-slate-800">已维护分类</h2><p className="mt-1 text-[11px] text-slate-500">停用只影响后续分类计算，不删除历史发票明细。</p></div>
          <span className="text-xs text-slate-400">{loading ? "加载中…" : `${items.length} 条`}</span>
        </div>
        <table className="w-full min-w-[960px] text-sm">
          <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
            <tr><th className="px-4 py-2.5">开票分类格式</th><th className="px-4 py-2.5">财务大类</th><th className="px-4 py-2.5">税务代码</th><th className="px-4 py-2.5">匹配关键字</th><th className="px-4 py-2.5">匹配方式</th><th className="px-4 py-2.5 text-right">优先级</th><th className="px-4 py-2.5">状态</th><th className="px-4 py-2.5 text-right">操作</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {items.map((row) => (
              <tr key={row.id} className={!row.enabled ? "bg-slate-50/60 text-slate-400" : ""}>
                <td className="px-4 py-3 font-medium text-slate-900">{row.pattern}</td>
                <td className="px-4 py-3">{row.categoryName}</td>
                <td className="px-4 py-3 font-mono text-xs">{row.taxCode || <span className="text-amber-600">未配置</span>}</td>
                <td className="px-4 py-3">{row.matchKeyword}</td>
                <td className="px-4 py-3">{row.matchMode === "contains" ? "包含" : row.matchMode === "exact" ? "完全等于" : "开头是"}</td>
                <td className="px-4 py-3 text-right tabular-nums">{row.priority}</td>
                <td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-medium ${row.enabled ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{row.enabled ? "启用" : "停用"}</span></td>
                <td className="px-4 py-3 text-right">{canEdit ? <div className="flex justify-end gap-2"><button type="button" onClick={() => startEdit(row)} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700">修改</button><button type="button" onClick={() => void toggle(row)} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600">{row.enabled ? "停用" : "启用"}</button></div> : <span className="text-xs text-slate-400">只读</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && !items.length && <div className="p-8 text-center text-xs text-slate-400">还没有财务分类规则</div>}
      </div>
    </section>
  );
}
