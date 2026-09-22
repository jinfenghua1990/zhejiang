"use client";

import { useState } from "react";
import { Alibaba1688ImportRow, Alibaba1688OrderPreview, alibaba1688Api } from "@/lib/api";
import { Alibaba1688BrowserCard } from "./alibaba1688-browser-card";
import { LifecyclePanel } from "./LifecyclePanel";

export function Alibaba1688Panel() {
  // 同步完成后 remount 列表以刷新最近导入批次。
  const [reloadKey, setReloadKey] = useState(0);
  return (
    <div className="space-y-4">
      <Alibaba1688BrowserCard onSynced={() => setReloadKey((k) => k + 1)} />
      <LifecyclePanel<Alibaba1688ImportRow, Alibaba1688OrderPreview>
      title="1688 订单导入 · Excel 补充通道"
      description="上方「浏览器直采」为在线主通道；此面板用于补传 1688 导出的订单 Excel（如历史订单、直采缺失的数据）。系统自动解析订单编号、买卖家信息、金额、状态等字段，默认确认生效并继续执行采购链路自动化。相同订单号只保留一个订单主档，但不会删除入库文件的原始明细行。"
      accept=".xlsx"
      fileHint="支持 .xlsx 格式，系统会自动识别表头并提取订单数据。相同文件会自动去重。"
      api={{
        imports: alibaba1688Api.imports,
        upload: (file, autoConfirm) => alibaba1688Api.upload(file, autoConfirm),
        confirm: alibaba1688Api.confirm,
        softDelete: alibaba1688Api.softDelete,
        restore: alibaba1688Api.restore,
        records: alibaba1688Api.orders,
        deleteRow: alibaba1688Api.deleteRow,
        restoreRow: alibaba1688Api.restoreRow,
      }}
      renderSummary={(row) => (
        <div className="min-w-0 text-sm">
          <div className="truncate font-medium text-gray-700">{row.fileName}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-400">
            <span>{formatSize(row.fileSize)}</span>
            <span>{row.orderCount} 条订单</span>
            <span>{statusLabel(row.status)}</span>
            <span>{row.uploader}</span>
            <span>{row.createdAt ? new Date(row.createdAt).toLocaleString("zh-CN") : "—"}</span>
          </div>
        </div>
      )}
      renderPreview={(_row, rows, _loading, error, actions) => {
        if (error) return <div className="text-sm text-red-600">{error}</div>;
        if (!rows || rows.length === 0)
          return <div className="py-4 text-center text-sm text-gray-400">本次导入暂无可预览的订单明细。</div>;
        const deletedCount = rows.filter((o) => o.rowStatus === "deleted").length;
        const fmtTime = (t: string | null) => (t ? new Date(t).toLocaleString("zh-CN") : "—");
        return (
          <div>
            <div className="mb-2 text-xs text-gray-500">
              按导入订单明细原样展示，共 {rows.length} 条
              {deletedCount > 0 && (
                <span className="ml-1 text-gray-400">（{deletedCount} 条已删除，置灰展示，可恢复）</span>
              )}
            </div>
            <div className="max-h-[480px] overflow-auto rounded-md border border-gray-200 bg-white">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 z-10 bg-gray-50 text-left text-gray-500">
                  <tr>
                    <th className="px-2 py-1.5 font-medium">订单号</th>
                    <th className="px-2 py-1.5 font-medium">卖家公司</th>
                    <th className="px-2 py-1.5 font-medium">卖家会员</th>
                    <th className="px-2 py-1.5 font-medium">买家公司</th>
                    <th className="px-2 py-1.5 font-medium">买家会员</th>
                    <th className="px-2 py-1.5 text-right font-medium">商品总额</th>
                    <th className="px-2 py-1.5 text-right font-medium">运费</th>
                    <th className="px-2 py-1.5 text-right font-medium">优惠</th>
                    <th className="px-2 py-1.5 text-right font-medium">实付款</th>
                    <th className="px-2 py-1.5 font-medium">状态</th>
                    <th className="px-2 py-1.5 font-medium">下单时间</th>
                    <th className="px-2 py-1.5 font-medium">付款时间</th>
                    <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((o) => {
                    const deleted = o.rowStatus === "deleted";
                    const busy = actions?.busyRowKey === o.id;
                    const cellText = deleted ? "text-gray-300" : "text-gray-700";
                    return (
                      <tr key={o.id} className={deleted ? "bg-gray-50/80" : "hover:bg-indigo-50/30"}>
                        <td className={`whitespace-nowrap px-2 py-1.5 font-medium ${cellText}`}>{o.externalOrderId}</td>
                        <td className={`max-w-40 truncate whitespace-nowrap px-2 py-1.5 ${cellText}`} title={o.sellerCompanyName}>
                          {o.sellerCompanyName || "—"}
                        </td>
                        <td className={`max-w-32 truncate whitespace-nowrap px-2 py-1.5 ${cellText}`} title={o.sellerMemberName}>
                          {o.sellerMemberName || "—"}
                        </td>
                        <td className={`max-w-40 truncate whitespace-nowrap px-2 py-1.5 ${cellText}`} title={o.buyerCompanyName}>
                          {o.buyerCompanyName || "—"}
                        </td>
                        <td className={`max-w-32 truncate whitespace-nowrap px-2 py-1.5 ${cellText}`} title={o.buyerMemberName}>
                          {o.buyerMemberName || "—"}
                        </td>
                        <td className={`whitespace-nowrap px-2 py-1.5 text-right ${cellText}`}>{o.goodsTotal}</td>
                        <td className={`whitespace-nowrap px-2 py-1.5 text-right ${cellText}`}>{o.freight}</td>
                        <td className={`whitespace-nowrap px-2 py-1.5 text-right ${cellText}`}>{o.discount}</td>
                        <td className={`whitespace-nowrap px-2 py-1.5 text-right font-medium ${cellText}`}>{o.actualPayment}</td>
                        <td className={`whitespace-nowrap px-2 py-1.5 ${cellText}`}>{o.orderStatus}</td>
                        <td className={`whitespace-nowrap px-2 py-1.5 ${deleted ? "text-gray-300" : "text-gray-500"}`}>
                          {fmtTime(o.orderTime)}
                        </td>
                        <td className={`whitespace-nowrap px-2 py-1.5 ${deleted ? "text-gray-300" : "text-gray-500"}`}>
                          {fmtTime(o.payTime)}
                        </td>
                        <td className="whitespace-nowrap px-2 py-1.5 text-right">
                          {!actions?.editable ? null : deleted ? (
                            <button
                              onClick={() => actions.onRestoreRow(o.id)}
                              disabled={busy}
                              className="rounded px-1.5 py-0.5 text-xs text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                            >
                              恢复
                            </button>
                          ) : (
                            <button
                              onClick={() => actions.onDeleteRow(o.id)}
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
              不要的订单直接删除，删除后不进入采购链路/工作台；确认生效前可随时恢复。
            </div>
          </div>
        );
      }}
      />
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string): string {
  if (status === "completed") return "已完成";
  if (status === "failed") return "失败";
  return status;
}
