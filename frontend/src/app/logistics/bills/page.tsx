"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { logisticsApi, type LogisticsBill, type LogisticsBillImportPreview } from "@/lib/api";

function fmtMoney(v: string | null | undefined): string {
  if (v == null || v === "" || v === "0" || v === "0.00") return "—";
  try {
    return "¥" + parseFloat(v).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  } catch {
    return "—";
  }
}
function fmtCount(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("zh-CN");
}

const STATUS_STYLE: Record<string, string> = {
  settled: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  pending: "bg-amber-50 text-amber-700 ring-amber-200",
  abnormal: "bg-red-50 text-red-700 ring-red-200",
  partial: "bg-sky-50 text-sky-700 ring-sky-200",
};

function StatusTag({ status, label }: { status: string; label: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${STATUS_STYLE[status] ?? "bg-gray-100 text-gray-500 ring-gray-200"}`}>
      {label}
    </span>
  );
}

const INVOICE_LABEL: Record<string, string> = { none: "无", uninvoiced: "未开票", invoiced: "已开票" };

export default function LogisticsBillsPage() {
  const [items, setItems] = useState<LogisticsBill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [detail, setDetail] = useState<LogisticsBill | null>(null);
  const [saving, setSaving] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<LogisticsBillImportPreview | null>(null);
  const [importing, setImporting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await logisticsApi.bills();
      setItems(d.items ?? []);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const previewImport = async (file: File | undefined) => {
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      const result = await logisticsApi.importBillXlsx(file, false);
      setImportFile(file);
      setImportPreview(result.preview);
    } catch (e) {
      setError(e instanceof Error ? e.message : "物流账单识别失败");
    } finally {
      setImporting(false);
    }
  };

  const confirmImport = async () => {
    if (!importFile || !importPreview) return;
    setImporting(true);
    setError(null);
    try {
      await logisticsApi.importBillXlsx(importFile, true);
      setImportFile(null);
      setImportPreview(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "物流账单导入失败");
    } finally {
      setImporting(false);
    }
  };

  const [form, setForm] = useState({
    periodLabel: "",
    periodStart: "",
    periodEnd: "",
    carrier: "",
    waybillCount: "",
    actualAmount: "",
    invoiceStatus: "none",
    note: "",
  });

  const openCreate = () => {
    setForm({ periodLabel: "", periodStart: "", periodEnd: "", carrier: "", waybillCount: "", actualAmount: "", invoiceStatus: "none", note: "" });
    setShowCreate(true);
  };

  const submitCreate = async () => {
    setSaving(true);
    setError(null);
    try {
      await logisticsApi.createBill({
        period_label: form.periodLabel,
        period_start: form.periodStart || null,
        period_end: form.periodEnd || null,
        carrier: form.carrier,
        waybill_count: form.waybillCount ? parseInt(form.waybillCount, 10) : null,
        actual_amount: form.actualAmount,
        invoice_status: form.invoiceStatus,
        note: form.note,
      });
      setShowCreate(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const settle = async (id: number) => {
    if (!window.confirm("确认核销该账单？核销后对应账期的物流成本将改为实际账单金额。")) return;
    setSaving(true);
    try {
      await logisticsApi.settleBill(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "核销失败");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm("确认删除该账单？")) return;
    try {
      await logisticsApi.deleteBill(id);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  };

  const openDetail = async (id: number) => {
    try {
      const b = await logisticsApi.bill(id);
      setDetail(b);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载账单失败");
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[10px] font-medium text-blue-600">财务中心 / 物流对账</div>\n          <h1 className="mt-1 text-lg font-semibold text-gray-800">物流对账</h1>
          <p className="mt-0.5 text-xs text-gray-400">导入物流公司实际账单，按运单/订单核对费用；确认后的实际物流成本用于利润核算</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xlsm"
            className="hidden"
            onChange={(event) => {
              void previewImport(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
            className="rounded-lg bg-[#2574e8] px-3 py-2 text-sm font-medium text-white hover:bg-[#1f63c9] disabled:opacity-50"
          >
            {importing ? "识别中…" : "＋ 导入 Excel 账单"}
          </button>
          <button onClick={openCreate} className="rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-600 hover:bg-gray-50">
            手工新增
          </button>
        </div>
      </div>

      {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}

      {loading && !items.length ? (
        <div className="py-16 text-center text-sm text-gray-400">加载中…</div>
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 text-left text-xs text-gray-500">
                  <th className="px-4 py-2.5 font-medium">账单周期</th>
                  <th className="px-4 py-2.5 font-medium">物流公司</th>
                  <th className="px-4 py-2.5 font-medium">运单数</th>
                  <th className="px-4 py-2.5 font-medium">系统预估</th>
                  <th className="px-4 py-2.5 font-medium">实际账单</th>
                  <th className="px-4 py-2.5 font-medium">实际单均</th>
                  <th className="px-4 py-2.5 font-medium">差额</th>
                  <th className="px-4 py-2.5 font-medium">核销情况</th>
                  <th className="px-4 py-2.5 font-medium">发票状态</th>
                  <th className="px-4 py-2.5 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.length === 0 && (
                  <tr><td colSpan={10} className="px-4 py-10 text-center text-gray-400">暂无账单，点击右上角「导入物流账单」添加</td></tr>
                )}
                {items.map((b, i) => (
                  <tr key={b.id} className={i % 2 ? "bg-gray-50/40" : ""}>
                    <td className="px-4 py-2.5 text-gray-800">{b.periodLabel}</td>
                    <td className="px-4 py-2.5 text-gray-700">{b.carrier || "—"}</td>
                    <td className="px-4 py-2.5 tabular-nums text-gray-700">{fmtCount(b.waybillCount)}</td>
                    <td className="px-4 py-2.5 tabular-nums text-gray-600">{fmtMoney(b.estimatedAmount)}</td>
                    <td className="px-4 py-2.5 tabular-nums text-gray-800">{fmtMoney(b.actualAmount)}</td>
                    <td className="px-4 py-2.5 tabular-nums text-gray-600">{b.actualUnitPrice ? fmtMoney(b.actualUnitPrice) + "/单" : "—"}</td>
                    <td className={`px-4 py-2.5 tabular-nums ${b.difference && parseFloat(b.difference) < 0 ? "text-emerald-600" : "text-red-500"}`}>{fmtMoney(b.difference)}</td>
                    <td className="px-4 py-2.5"><StatusTag status={b.status} label={b.statusLabel} /></td>
                    <td className="px-4 py-2.5 text-gray-600">{INVOICE_LABEL[b.invoiceStatus] ?? b.invoiceStatus}</td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center justify-end gap-3">
                        <button onClick={() => openDetail(b.id)} className="text-xs text-[#2574e8] hover:underline">查看</button>
                        {b.status === "pending" && (
                          <button onClick={() => settle(b.id)} className="text-xs text-emerald-600 hover:underline">核销</button>
                        )}
                        <button onClick={() => remove(b.id)} className="text-xs text-red-500 hover:underline">删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {importPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={() => !importing && setImportPreview(null)}>
          <div className="max-h-[88vh] w-[900px] overflow-y-auto rounded-2xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-base font-semibold text-gray-900">物流账单识别预览</h3>
                <p className="mt-1 text-xs text-gray-400">{importPreview.fileName} · 确认后才会写入物流账单</p>
              </div>
              <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-700">
                {importPreview.periodLabel}
              </span>
            </div>

            {importPreview.warnings.length > 0 && (
              <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-5 text-amber-800">
                {importPreview.warnings.map((warning) => <div key={warning}>⚠ {warning}</div>)}
              </div>
            )}

            {importPreview.duplicateBillId && (
              <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700">
                该文件已经导入过，账单编号 #{importPreview.duplicateBillId}，请不要重复入账。
              </div>
            )}

            <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                ["快递明细", importPreview.shipmentCount.toLocaleString("zh-CN") + " 条"],
                ["自提/客户账号", importPreview.pickupCount.toLocaleString("zh-CN") + " 条"],
                ["收费记录", importPreview.waybillCount.toLocaleString("zh-CN") + " 条"],
                ["系统匹配", importPreview.matchedCount.toLocaleString("zh-CN") + " 条"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                  <div className="text-[11px] text-slate-400">{label}</div>
                  <div className="mt-1 text-lg font-semibold text-slate-800">{value}</div>
                </div>
              ))}
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-5">
              {[
                ["快递费", importPreview.shippingAmount],
                ["自提费用", importPreview.pickupAmount],
                ["增值服务", importPreview.valueAddedAmount],
                ["账单调整", importPreview.adjustmentAmount],
                ["最终实际应付", importPreview.actualAmount],
              ].map(([label, value], index) => (
                <div key={label} className={`rounded-xl border p-3 ${index === 4 ? "border-emerald-200 bg-emerald-50" : "border-slate-200 bg-white"}`}>
                  <div className="text-[11px] text-slate-400">{label}</div>
                  <div className={`mt-1 text-base font-semibold tabular-nums ${index === 4 ? "text-emerald-700" : "text-slate-800"}`}>{fmtMoney(value)}</div>
                </div>
              ))}
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div className="rounded-xl border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-800">账单识别</div>
                <div className="mt-3 space-y-2 text-xs">
                  <Info k="汇总仓库" v={importPreview.summaryWarehouse || "—"} />
                  <Info k="明细主要仓库" v={importPreview.detailWarehouse || "—"} />
                  <Info k="物流渠道" v={importPreview.carrier || "—"} />
                  <Info k="账单毛额" v={fmtMoney(importPreview.grossAmount)} />
                  <Info k="未匹配运单" v={String(importPreview.unmatchedCount)} />
                  <Info k="重复运单" v={String(importPreview.duplicateCount)} />
                </div>
              </div>
              <div className="rounded-xl border border-slate-200 p-4">
                <div className="text-sm font-medium text-slate-800">历史学习样本</div>
                <p className="mt-1 text-[11px] leading-5 text-slate-400">系统会把实际费用按月份、地区、物流公司、重量段沉淀为下一期预估模型。</p>
                <div className="mt-3 max-h-36 overflow-y-auto">
                  {(importPreview.regionalModels ?? []).slice(0, 8).map((row) => (
                    <div key={`${row.month}-${row.province}`} className="flex items-center justify-between border-b border-slate-100 py-1.5 text-xs last:border-b-0">
                      <span className="text-slate-600">{row.month} · {row.province}</span>
                      <span className="tabular-nums text-slate-800">{row.sampleCount} 单 · 均 {fmtMoney(row.avgFee)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button disabled={importing} onClick={() => setImportPreview(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">取消</button>
              <button
                disabled={importing || Boolean(importPreview.duplicateBillId)}
                onClick={() => void confirmImport()}
                className="rounded-lg bg-[#2574e8] px-4 py-2 text-sm font-medium text-white hover:bg-[#1f63c9] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {importing ? "导入中…" : "确认导入 · 待核销"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setShowCreate(false)}>
          <div className="w-[460px] rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-gray-800">导入物流账单</h3>
            <div className="mt-4 space-y-3">
              <div>
                <label className="text-xs text-gray-500">账单周期（如 2026 H1）</label>
                <input value={form.periodLabel} onChange={(e) => setForm({ ...form, periodLabel: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" placeholder="2026 H1" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-500">周期开始（年-月-日）</label>
                  <input type="date" value={form.periodStart} onChange={(e) => setForm({ ...form, periodStart: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" />
                </div>
                <div>
                  <label className="text-xs text-gray-500">周期结束</label>
                  <input type="date" value={form.periodEnd} onChange={(e) => setForm({ ...form, periodEnd: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-gray-500">物流公司</label>
                  <input value={form.carrier} onChange={(e) => setForm({ ...form, carrier: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" placeholder="圆通" />
                </div>
                <div>
                  <label className="text-xs text-gray-500">运单数</label>
                  <input type="number" value={form.waybillCount} onChange={(e) => setForm({ ...form, waybillCount: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" placeholder="52130" />
                </div>
              </div>
              <div>
                <label className="text-xs text-gray-500">实际账单金额（元）</label>
                <input type="number" value={form.actualAmount} onChange={(e) => setForm({ ...form, actualAmount: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" placeholder="251280.00" />
              </div>
              <div>
                <label className="text-xs text-gray-500">发票状态</label>
                <select value={form.invoiceStatus} onChange={(e) => setForm({ ...form, invoiceStatus: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]">
                  <option value="none">无</option>
                  <option value="uninvoiced">未开票</option>
                  <option value="invoiced">已开票</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-gray-500">备注</label>
                <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-[#2574e8]" />
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-600 hover:bg-gray-50">取消</button>
              <button onClick={submitCreate} disabled={saving || !form.actualAmount} className="rounded-lg bg-[#2574e8] px-3 py-2 text-sm font-medium text-white hover:bg-[#1f63c9] disabled:opacity-50">
                {saving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={() => setDetail(null)}>
          <div className="w-[520px] rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-gray-800">账单明细</h3>
            <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50/50 p-4">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <Info k="物流公司" v={detail.carrier || "—"} />
                <Info k="账单周期" v={detail.periodLabel} />
                <Info k="账单金额" v={fmtMoney(detail.actualAmount)} />
                <Info k="系统预估" v={fmtMoney(detail.estimatedAmount)} />
                <Info k="差额" v={fmtMoney(detail.difference)} />
                <Info k="实际单均" v={detail.actualUnitPrice ? fmtMoney(detail.actualUnitPrice) + "/单" : "—"} />
                <Info k="核销情况" v={detail.statusLabel} />
                <Info k="发票状态" v={INVOICE_LABEL[detail.invoiceStatus] ?? detail.invoiceStatus} />
              </div>
              <div className="mt-3 border-t border-gray-200 pt-3 text-xs text-gray-500">
                <div className="flex items-center justify-between gap-3">
                  <span>来源文件名</span>
                  <span className="max-w-[320px] truncate font-medium text-slate-700" title={detail.attachmentName || ""}>{detail.attachmentName || "手工录入"}</span>
                </div>
              </div>
            </div>
            {detail.importSummary && (
              <div className="mt-3 rounded-lg border border-gray-200 p-4">
                <div className="text-sm font-medium text-gray-700">账单费用拆分</div>
                <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-2 text-sm">
                  <Info k="快递费" v={fmtMoney(detail.importSummary.shippingAmount)} />
                  <Info k="自提/客户账号" v={fmtMoney(detail.importSummary.pickupAmount)} />
                  <Info k="增值服务" v={fmtMoney(detail.importSummary.valueAddedAmount)} />
                  <Info k="账单毛额" v={fmtMoney(detail.importSummary.grossAmount)} />
                  <Info k="赔付/盘亏调整" v={fmtMoney(detail.importSummary.adjustmentAmount)} />
                  <Info k="最终实际应付" v={fmtMoney(detail.importSummary.actualAmount)} />
                </div>
              </div>
            )}

            <div className="mt-3 rounded-lg border border-gray-200 p-4">
              <div className="text-sm font-medium text-gray-700">账单匹配结果</div>
              <div className="mt-2 grid grid-cols-4 gap-3 text-center">
                <Match k="匹配运单" v={fmtCount(detail.matchedCount)} />
                <Match k="未匹配" v={fmtCount(detail.unmatchedCount)} />
                <Match k="重复运单" v={fmtCount(detail.duplicateCount)} />
                <Match k="异常金额" v={fmtCount(detail.abnormalCount)} />
              </div>
              <p className="mt-2 text-xs text-gray-400">Excel 账单会按发货单号 → 物流单号 → 原始订单号依次匹配本地吉客云数据；未匹配项保留，便于后续补齐数据源后重新核对。</p>
            </div>
            <div className="mt-5 flex justify-end">
              <button onClick={() => setDetail(null)} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-600 hover:bg-gray-50">关闭</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Info({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-400">{k}</span>
      <span className="tabular-nums text-gray-700">{v}</span>
    </div>
  );
}

function Match({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-lg bg-white px-2 py-3 ring-1 ring-gray-200">
      <div className="text-sm font-semibold tabular-nums text-gray-800">{v}</div>
      <div className="mt-0.5 text-[11px] text-gray-400">{k}</div>
    </div>
  );
}
