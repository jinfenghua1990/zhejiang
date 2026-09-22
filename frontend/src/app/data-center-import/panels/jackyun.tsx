"use client";

import { useState } from "react";
import { JackyunFileImportRow, JackyunRecordPreview, jackyunFileApi, testJackyun } from "@/lib/api";
import { LifecyclePanel } from "./LifecyclePanel";

/**
 * 吉客云业务单据：纯手动上传。
 * 开放平台在线同步已停用（JACKYUN_SYNC_MODE=manual），业务单据一律由客户端导出文件上传。
 */
export function JackyunPanel() {
  const [testing, setTesting] = useState(false);
  const [testMessage, setTestMessage] = useState("");
  const [testTone, setTestTone] = useState<"ok" | "warn" | "error">("warn");

  async function runConnectionTest() {
    setTesting(true);
    setTestMessage("");
    try {
      const result = await testJackyun();
      if (!result.ok) {
        setTestTone("error");
        setTestMessage("连接失败：" + (result.error ?? "未知错误"));
      } else if (result.businessReady === false) {
        setTestTone("warn");
        setTestMessage("连接通道可用，但吉客云业务 API 权限尚未开通；业务单据继续使用文件导入。");
      } else {
        setTestTone("ok");
        setTestMessage("连接与业务接口状态正常。");
      }
    } catch (error) {
      setTestTone("error");
      setTestMessage("连接测试异常：" + String(error));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div>
      <div className="mb-4 app-card rounded-xl p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[13px] font-semibold text-slate-900">吉客云连接与导入</div>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">连接测试和业务导入都留在这里；当前业务单据以客户端文件导入为主。</p>
          </div>
          <button
            type="button"
            onClick={runConnectionTest}
            disabled={testing}
            className="app-button-secondary rounded-lg px-3 py-1.5 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            {testing ? "测试中…" : "测试吉客云连接"}
          </button>
        </div>
        {testMessage && (
          <div className={"mt-3 rounded-lg px-3 py-2 text-[11px] leading-5 " + (testTone === "ok" ? "bg-emerald-50 text-emerald-700" : testTone === "error" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700")}>
            {testMessage}
          </div>
        )}
      </div>

      <div className="mb-4 rounded-lg border border-emerald-100 bg-emerald-50/60 px-4 py-3 text-xs text-emerald-800">
        采购入库单就在这里导入：请从吉客云客户端导出「采购入库申请单货品」或「采购入库单」的 XLSX / CSV 后上传，系统会自动识别入库单、回填明细并执行采购链路关联。当前不支持直接在线拉取吉客云单据。
      </div>
      <LifecyclePanel<JackyunFileImportRow, JackyunRecordPreview>
      title="吉客云客户端导出导入"
      description="从吉客云客户端导出的采购/库存/结算等报表，默认上传后自动生效，并继续执行字段映射、订单建档和采购链路关联。需要逐行核对时可关闭自动确认。"
      accept=".xlsx,.csv"
      fileHint="支持吉客云客户端导出的 XLSX / CSV；采购入库单建议包含「入库单号/申请单号」和「货品编号」，所有原始列均保留。下方“吉客云入库单（导出核验）”只是下载本地数据，不是上传入口。"
      api={{
        imports: jackyunFileApi.imports,
        upload: (file, autoConfirm) => jackyunFileApi.upload(file, autoConfirm),
        confirm: jackyunFileApi.confirm,
        softDelete: jackyunFileApi.softDelete,
        restore: jackyunFileApi.restore,
        records: jackyunFileApi.records,
        deleteRow: jackyunFileApi.deleteRow,
        restoreRow: jackyunFileApi.restoreRow,
      }}
      buildConfirmNotice={(row) => {
        const mr = row.mapResult;
        if (!mr) return "";
        if (!mr.ok) return `映射失败：${mr.error ?? "未知错误"}`;
        if (mr.skipped) return `报表已确认并存档：${mr.reason ?? "当前报表类型暂无自动映射"}。`;
        if (mr.mapper === "inbound_items") {
          const parts = [`保留 ${mr.sourceRows ?? 0} 条原始行`];
          parts.push(`回填 ${mr.matched ?? 0} 行入库明细`);
          parts.push(`覆盖 ${mr.documents ?? 0} 张入库单`);
          if (mr.filledFields) parts.push(`写入 ${mr.filledFields} 个字段（单价/数量/规格等）`);
          if (mr.createdExternalOrders) parts.push(`建立 ${mr.createdExternalOrders} 个其他渠道订单号`);
          if (mr.externalOrderNos?.length) parts.push(`订单号：${mr.externalOrderNos.join("、")}`);
          if (mr.createdLinks) parts.push(`新增并确认 ${mr.createdLinks} 条采购-入库关系`);
          if (mr.alreadyLinked) parts.push(`${mr.alreadyLinked} 条已有链路保留不变`);
          if (mr.missingRk?.length) parts.push(`缺少入库单：${mr.missingRk.join("、")}`);
          if (mr.rejectedLinks) parts.push(`${mr.rejectedLinks} 条已拒绝关系未覆盖`);
          if (mr.allocSeeded) parts.push(`反填 ${mr.allocSeeded} 条 SKU 分配`);
          return `入库申请单货品已处理：${parts.join("、")}。原始行不合并，只有同一订单与入库单关系不会重复建链。`;
        }
        if (mr.mapper === "purchase") {
          const parts = [`已生成采购单 ${mr.mapped ?? 0} 张`];
          if (mr.updated) parts.push(`更新 ${mr.updated} 张`);
          return `采购报表已确认并生效：${parts.join("、")}。可到采购工作台订单详情「吉客云采购单关联」匹配。`;
        }
        return "报表已确认并生效。";
      }}
      renderSummary={(row) => (
        <div className="min-w-0 text-sm">
          <div className="truncate font-medium text-gray-700">{row.originalName}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-400">
            <span>{formatSize(row.size)}</span>
            <span>{row.rowCount} 行</span>
            <span>类型：{reportTypeLabel(row.reportType)}</span>
            <span>{row.uploader}</span>
            <span>{row.createdAt ? new Date(row.createdAt).toLocaleString("zh-CN") : "—"}</span>
          </div>
        </div>
      )}
      renderPreview={(row, rows, _loading, error, actions) => {
        if (error) return <div className="text-sm text-red-600">{error}</div>;
        if (!rows || rows.length === 0)
          return <div className="py-4 text-center text-sm text-gray-400">本次导入暂无可预览的原始行。</div>;
        const columns = Object.keys(rows[0].payload ?? {});
        const deletedCount = rows.filter((r) => r.rowStatus === "deleted").length;
        return (
          <div>
            <div className="mb-2 text-xs text-gray-500">
              报表类型：{reportTypeLabel(row.reportType)} · 按导入表格原样展示，共 {rows.length} 行
              {deletedCount > 0 && (
                <span className="ml-1 text-gray-400">（{deletedCount} 行已删除，置灰展示，可恢复）</span>
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
                    return (
                      <tr key={r.rowIndex} className={deleted ? "bg-gray-50/80" : "hover:bg-indigo-50/30"}>
                        <td className="whitespace-nowrap px-2 py-1.5 text-gray-400">{r.rowIndex}</td>
                        {columns.map((c) => (
                          <td
                            key={c}
                            className={`max-w-48 truncate whitespace-nowrap px-2 py-1.5 ${
                              deleted ? "text-gray-300" : "text-gray-700"
                            }`}
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
              不需要的行直接删除，已删行不进入业务数据；确认生效前可随时恢复。
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

function reportTypeLabel(t: string): string {
  if (t === "purchase") return "采购";
  if (t === "inbound") return "入库";
  if (t === "settlement") return "结算";
  if (t === "inventory") return "库存";
  if (t === "sales") return "销售";
  return t || "未知";
}
