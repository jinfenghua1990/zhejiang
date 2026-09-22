"use client";

import { useEffect, useState, type FormEvent } from "react";
import { externalPurchaseOrderApi, type ExternalPurchaseOrderRow } from "@/lib/api";

type FormState = {
  platform: "pdd" | "taobao" | "other";
  orderNo: string;
  supplier: string;
  orderedAt: string;
  amount: string;
  paidAmount: string;
  title: string;
};

const CHANNELS = {
  pdd: "拼多多",
  taobao: "淘宝 / 天猫",
  other: "其他渠道",
} as const;

const EMPTY_FORM: FormState = {
  platform: "other",
  orderNo: "",
  supplier: "",
  orderedAt: "",
  amount: "",
  paidAmount: "",
  title: "",
};

export function OtherChannelOrdersPanel() {
  const [rows, setRows] = useState<ExternalPurchaseOrderRow[]>([]);
  const [query, setQuery] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = async (value = query) => {
    setLoading(true);
    setError("");
    try {
      setRows(await externalPurchaseOrderApi.list(value));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "订单主档加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load("");
  }, []);

  const updateForm = (key: keyof FormState, value: string) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const startEdit = (row: ExternalPurchaseOrderRow) => {
    setEditingId(row.id);
    setForm({
      platform: row.platform === "pdd" || row.platform === "taobao" ? row.platform : "other",
      orderNo: row.externalOrderId,
      supplier: row.supplierName,
      orderedAt: row.orderedAt ? row.orderedAt.slice(0, 16) : "",
      amount: row.orderAmount ?? "",
      paidAmount: row.paidAmount ?? "",
      title: row.title,
    });
    setMessage("");
  };

  const resetForm = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
  };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.orderNo.trim()) {
      setError("请填写订单号");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const common = {
        platform: form.platform,
        supplier_name: form.supplier.trim(),
        title: form.title.trim(),
        ordered_at: form.orderedAt || null,
        ...(form.amount.trim() ? { order_amount: form.amount.trim() } : {}),
        ...(form.paidAmount.trim() ? { paid_amount: form.paidAmount.trim() } : {}),
      };
      if (editingId !== null) {
        await externalPurchaseOrderApi.update(editingId, common);
        setMessage(`订单 ${form.orderNo.trim()} 已更新`);
      } else {
        await externalPurchaseOrderApi.create({
          external_order_id: form.orderNo.trim(),
          ...common,
        });
        setMessage(`订单 ${form.orderNo.trim()} 已加入订单号库`);
      }
      resetForm();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "订单保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="text-xl font-semibold">其他渠道采购订单</h1>
      <p className="mt-1 max-w-4xl text-sm leading-6 text-gray-500">
        维护不在 1688 原始订单中的采购订单号。它们仍进入同一采购工作台和入库链路，但不会被伪装成 1688 订单。
      </p>

      <div className="mt-5 rounded-xl border border-indigo-100 bg-indigo-50/50 p-4 text-sm text-indigo-900">
        <div className="font-medium">入库文件自动识别</div>
        <div className="mt-1 text-xs leading-5 text-indigo-700">
          确认「入库申请单货品」文件时，系统会保留每一条原始行；其中找不到 1688 原始订单的订单号，会按往来单位推断渠道后写入本页，并继续关联对应入库单。
        </div>
      </div>

      <form onSubmit={submit} className="mt-4 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-gray-800">{editingId === null ? "新建订单号" : "编辑订单主档"}</h2>
            <p className="mt-1 text-xs text-gray-400">订单号保存后不允许修改，避免破坏已有入库、发票和付款关联。</p>
          </div>
          {editingId !== null && <button type="button" onClick={resetForm} className="text-xs text-gray-500 hover:text-gray-800">取消编辑</button>}
        </div>
        <div className="mt-3 grid gap-2 md:grid-cols-3 xl:grid-cols-6">
          <label className="text-xs text-gray-500">渠道<select value={form.platform} onChange={(e) => updateForm("platform", e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-sm text-gray-700"><option value="pdd">{CHANNELS.pdd}</option><option value="taobao">{CHANNELS.taobao}</option><option value="other">{CHANNELS.other}</option></select></label>
          <label className="text-xs text-gray-500 md:col-span-2">订单号<input required value={form.orderNo} onChange={(e) => updateForm("orderNo", e.target.value)} disabled={editingId !== null} placeholder="例如 20260501002" className="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-2 font-mono text-sm text-gray-700 disabled:bg-gray-50" /></label>
          <label className="text-xs text-gray-500 md:col-span-2">供应商 / 往来单位<input value={form.supplier} onChange={(e) => updateForm("supplier", e.target.value)} placeholder="例如 淘宝临时采购" className="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-2 text-sm text-gray-700" /></label>
          <label className="text-xs text-gray-500">采购时间<input type="datetime-local" value={form.orderedAt} onChange={(e) => updateForm("orderedAt", e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-2 text-xs text-gray-700" /></label>
          <label className="text-xs text-gray-500">订单金额<input type="number" min="0" step="0.01" value={form.amount} onChange={(e) => updateForm("amount", e.target.value)} placeholder="可后补" className="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-2 text-sm text-gray-700" /></label>
          <label className="text-xs text-gray-500">实付金额<input type="number" min="0" step="0.01" value={form.paidAmount} onChange={(e) => updateForm("paidAmount", e.target.value)} placeholder="可后补" className="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-2 text-sm text-gray-700" /></label>
          <label className="text-xs text-gray-500 md:col-span-3 xl:col-span-4">备注<input value={form.title} onChange={(e) => updateForm("title", e.target.value)} placeholder="可记录来源、支付说明等" className="mt-1 w-full rounded-lg border border-gray-200 px-2.5 py-2 text-sm text-gray-700" /></label>
          <div className="flex items-end"><button disabled={busy} className="w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50">{busy ? "保存中…" : editingId === null ? "加入订单号库" : "保存修改"}</button></div>
        </div>
      </form>

      {error && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {message && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}

      <section className="mt-6 rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 p-4">
          <div><h2 className="text-sm font-semibold text-gray-800">订单号库</h2><p className="mt-1 text-xs text-gray-400">共 {rows.length} 条其他渠道订单；来源文件识别的记录可继续补充金额。</p></div>
          <form onSubmit={(e) => { e.preventDefault(); setQuery(searchDraft.trim()); void load(searchDraft.trim()); }} className="flex gap-2"><input value={searchDraft} onChange={(e) => setSearchDraft(e.target.value)} placeholder="搜索订单号 / 供应商" className="w-56 rounded-lg border border-gray-200 px-3 py-1.5 text-xs" /><button className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50">搜索</button></form>
        </div>
        {loading ? <div className="py-12 text-center text-sm text-gray-400">加载中…</div> : rows.length === 0 ? <div className="py-12 text-center text-sm text-gray-400">暂无其他渠道订单。可从上方新建，或确认入库申请单文件后自动识别。</div> : (
          <div className="overflow-x-auto">
            <table className="min-w-[900px] w-full text-xs">
              <thead className="bg-gray-50 text-left text-gray-500"><tr><th className="px-4 py-2.5 font-medium">渠道 / 订单号</th><th className="px-3 py-2.5 font-medium">供应商</th><th className="px-3 py-2.5 font-medium">采购时间</th><th className="px-3 py-2.5 text-right font-medium">订单金额</th><th className="px-3 py-2.5 text-right font-medium">实付金额</th><th className="px-3 py-2.5 font-medium">来源</th><th className="px-4 py-2.5 text-right font-medium">操作</th></tr></thead>
              <tbody className="divide-y divide-gray-100">{rows.map((row) => <tr key={row.id} className="hover:bg-indigo-50/30"><td className="px-4 py-3"><div className="flex items-center gap-2"><span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-600">{channelLabel(row.platform)}</span><span className="font-mono font-medium text-gray-800">{row.externalOrderId}</span></div><div className="mt-1 max-w-[360px] truncate text-gray-400">{row.title || "—"}</div></td><td className="px-3 py-3 text-gray-700">{row.supplierName || "待补录"}</td><td className="px-3 py-3 text-gray-500">{formatDate(row.orderedAt)}</td><td className="px-3 py-3 text-right tabular-nums text-gray-700">{row.orderAmount ?? "待补录"}</td><td className="px-3 py-3 text-right tabular-nums text-gray-700">{row.paidAmount ?? "待补录"}</td><td className="px-3 py-3 text-gray-500">{sourceLabel(row.source)}</td><td className="px-4 py-3 text-right"><button type="button" onClick={() => startEdit(row)} className="text-indigo-600 hover:text-indigo-800">编辑</button></td></tr>)}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function channelLabel(value: string): string {
  if (value === "pdd") return CHANNELS.pdd;
  if (value === "taobao") return CHANNELS.taobao;
  return CHANNELS.other;
}

function sourceLabel(value: string): string {
  return value === "jackyun_inbound_apply" ? "入库文件识别" : "手工维护";
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value.replace("T", " ").slice(0, 16) : parsed.toLocaleString("zh-CN");
}
