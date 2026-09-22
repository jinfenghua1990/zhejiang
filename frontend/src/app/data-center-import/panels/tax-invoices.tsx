"use client";

import { useCallback, useEffect, useState } from "react";
import { TaxInvoiceRow, TaxInvoiceImportRow, TaxInvoiceRecordPreview, taxInvoiceApi } from "@/lib/api";
import { LifecyclePanel } from "./LifecyclePanel";

export function TaxInvoicesPanel() {
  return (
    <div className="space-y-6">
      <LifecyclePanel<TaxInvoiceImportRow, TaxInvoiceRecordPreview>
        title="税务发票清单导入"
        description="从税务系统导出的发票清单（XLSX / CSV）上传后，系统自动识别发票号码、金额、购销方并进入发票台账，同时执行可验证的采购订单关联。需要逐行核对时可关闭自动确认；税务认证仍以实际勾选结果为准。"
        accept=".xlsx,.csv"
        fileHint="支持税务系统导出的 XLSX / CSV，自动识别发票号码、金额、购销方与进销项方向。"
        api={{
          imports: taxInvoiceApi.imports,
          upload: (file, autoConfirm) => taxInvoiceApi.upload(file, undefined, autoConfirm),
          confirm: taxInvoiceApi.confirm,
          softDelete: taxInvoiceApi.softDelete,
          restore: taxInvoiceApi.restore,
          records: taxInvoiceApi.records,
          deleteRow: taxInvoiceApi.deleteRow,
          restoreRow: taxInvoiceApi.restoreRow,
        }}
        renderSummary={(row) => (
          <div className="min-w-0 text-sm">
            <div className="truncate font-medium text-gray-700">{row.originalName}</div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-400">
              <span>{formatSize(row.size)}</span>
              <span>{row.recognizedRowCount} 行已识别</span>
              {row.needsReviewCount > 0 && <span className="text-amber-600">{row.needsReviewCount} 行待核对</span>}
              <span>{row.period || "未指定账期"}</span>
              <span>{row.uploader}</span>
              <span>{row.createdAt ? new Date(row.createdAt).toLocaleString("zh-CN") : "—"}</span>
            </div>
          </div>
        )}
        renderPreview={(_row, rows, _loading, error, actions) => {
          if (error) return <div className="text-sm text-red-600">{error}</div>;
          if (!rows || rows.length === 0)
            return <div className="py-4 text-center text-sm text-gray-400">本次导入暂无可预览的发票行。</div>;
          const columns = Object.keys(rows[0].payload ?? {});
          const deletedCount = rows.filter((r) => r.rowStatus === "deleted").length;
          const needsReviewCount = rows.filter(
            (r) => r.recognitionStatus === "needs_review" && r.rowStatus !== "deleted"
          ).length;
          return (
            <div>
              <div className="mb-2 text-xs text-gray-500">
                按导入表格原样展示，共 {rows.length} 行
                {deletedCount > 0 && (
                  <span className="ml-1 text-gray-400">（{deletedCount} 行已删除，置灰展示，可恢复）</span>
                )}
                {needsReviewCount > 0 && (
                  <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700">
                    {needsReviewCount} 行待核对（黄色底）
                  </span>
                )}
              </div>
              <div className="max-h-[480px] overflow-auto rounded-md border border-gray-200 bg-white">
                <table className="min-w-full text-xs">
                  <thead className="sticky top-0 z-10 bg-gray-50 text-left text-gray-500">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">#</th>
                      {columns.map((c) => (
                        <th key={c} className="whitespace-nowrap px-2 py-1.5 font-medium">{c}</th>
                      ))}
                      <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {rows.map((r) => {
                      const deleted = r.rowStatus === "deleted";
                      const busy = actions?.busyRowKey === r.rowIndex;
                      const needsReview = r.recognitionStatus === "needs_review";
                      const cellText = deleted ? "text-gray-300" : "text-gray-700";
                      return (
                        <tr
                          key={r.rowIndex}
                          className={
                            deleted ? "bg-gray-50/80" : needsReview ? "bg-amber-50/60" : "hover:bg-indigo-50/30"
                          }
                        >
                          <td className="whitespace-nowrap px-2 py-1.5 text-gray-400" title={r.errorSummary || undefined}>
                            {r.rowIndex}
                            {needsReview && !deleted && <span className="ml-1 text-amber-500">!</span>}
                          </td>
                          {columns.map((c) => (
                            <td
                              key={c}
                              className={`max-w-48 truncate whitespace-nowrap px-2 py-1.5 ${cellText}`}
                              title={r.payload[c]}
                            >
                              {r.payload[c] ?? ""}
                            </td>
                          ))}
                          <td className="whitespace-nowrap px-2 py-1.5 text-right">
                            {!actions?.editable ? null : deleted ? (
                              <button
                                onClick={() => actions.onRestoreRow(r.rowIndex)}
                                disabled={busy}
                                className="rounded px-1.5 py-0.5 text-xs text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                              >
                                恢复
                              </button>
                            ) : (
                              <button
                                onClick={() => actions.onDeleteRow(r.rowIndex)}
                                disabled={busy}
                                className="rounded px-1.5 py-0.5 text-xs text-red-500 hover:bg-red-50 disabled:opacity-40"
                              >
                                {busy ? "处理中…" : "删除"}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="mt-2 text-xs text-gray-400">
                不需要的发票行直接删除，删除后该发票同步从台账隐藏；确认生效前可随时恢复。
              </div>
            </div>
          );
        }}
      />
      <TaxInvoiceVerifyCard />
    </div>
  );
}

type VerifyFilter = "unverified" | "verified" | "all";

function TaxInvoiceVerifyCard() {
  const [filter, setFilter] = useState<VerifyFilter>("unverified");
  const [rows, setRows] = useState<TaxInvoiceRow[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string>("");

  const load = useCallback(() => {
    const verified = filter === "unverified" ? false : filter === "verified" ? true : undefined;
    taxInvoiceApi
      .invoices({ direction: "input", verified })
      .then(setRows)
      .catch(() => setRows([]));
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = (id: number) =>
    setSelected((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const toggleAll = () =>
    setSelected((prev) =>
      prev.size === rows.length ? new Set() : new Set(rows.map((r) => r.id))
    );

  const apply = async (verified: boolean) => {
    const ids = [...selected];
    if (!ids.length) {
      setMsg("请先勾选要操作的发票");
      return;
    }
    if (verified && !/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) {
      setMsg("认证月份格式应为 YYYY-MM，如 2026-08");
      return;
    }
    setBusy(true);
    setMsg("");
    try {
      const res = await taxInvoiceApi.bulkVerify(ids, verified, verified ? month : "");
      setMsg(`已更新 ${res.processed} 张发票`);
      setSelected(new Set());
      load();
    } catch {
      setMsg("操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const totalAmt = rows.reduce((s, r) => s + (Number(r.totalAmount) || 0), 0);

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">进项发票认证</h3>
          <p className="mt-0.5 text-xs text-gray-500">
            进项票在税务系统勾选认证（抵扣）后，回到此处标记所属月份。系统仅作跟踪，
            不影响实际申报；采购工作台据此判断「待认证」是否走完。
          </p>
        </div>
        <div className="flex items-center gap-1 text-xs">
          {(["unverified", "verified", "all"] as VerifyFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={
                filter === f
                  ? "rounded bg-indigo-600 px-2.5 py-1 text-white"
                  : "rounded border border-gray-200 px-2.5 py-1 text-gray-600 hover:bg-gray-50"
              }
            >
              {f === "unverified" ? "待认证" : f === "verified" ? "已认证" : "全部"}
            </button>
          ))}
        </div>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
        <input
          type="month"
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          className="rounded border border-gray-300 px-2 py-1"
        />
        <button
          onClick={() => apply(true)}
          disabled={busy}
          className="rounded bg-emerald-600 px-3 py-1 text-white hover:bg-emerald-700 disabled:opacity-40"
        >
          标记为已认证
        </button>
        <button
          onClick={() => apply(false)}
          disabled={busy}
          className="rounded border border-gray-300 px-3 py-1 text-gray-600 hover:bg-gray-50 disabled:opacity-40"
        >
          取消认证
        </button>
        <span className="text-gray-400">
          已选 {selected.size} 张 · 列表 {rows.length} 张（合计 ¥{totalAmt.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}）
        </span>
        {msg && <span className="ml-1 text-indigo-600">{msg}</span>}
      </div>

      <div className="max-h-[460px] overflow-auto rounded border border-gray-200">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 z-10 bg-gray-50 text-left text-gray-500">
            <tr>
              <th className="w-8 px-2 py-1.5">
                <input type="checkbox" checked={selected.size === rows.length && rows.length > 0} onChange={toggleAll} />
              </th>
              <th className="px-2 py-1.5 font-medium">发票号码</th>
              <th className="px-2 py-1.5 font-medium">开票方</th>
              <th className="px-2 py-1.5 text-right font-medium">金额</th>
              <th className="px-2 py-1.5 font-medium">开票日期</th>
              <th className="px-2 py-1.5 font-medium">认证状态</th>
              <th className="px-2 py-1.5 font-medium">认证月份</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-gray-400">
                  暂无进项发票
                </td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={r.id} className={selected.has(r.id) ? "bg-indigo-50/40" : "hover:bg-gray-50"}>
                  <td className="px-2 py-1.5">
                    <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} />
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-gray-700">{r.invoiceCode}{r.invoiceNumber}</td>
                  <td className="max-w-48 truncate px-2 py-1.5 text-gray-700" title={r.sellerName}>{r.sellerName}</td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums text-gray-700">
                    ¥{Number(r.totalAmount || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-gray-500">
                    {r.issueDate ? r.issueDate.slice(0, 10) : "—"}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.verified ? (
                      <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-600">已认证</span>
                    ) : (
                      <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">待认证</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-gray-500">{r.verifiedMonth || "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
