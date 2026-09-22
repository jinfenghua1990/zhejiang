"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Alibaba1688Panel } from "./panels/alibaba1688";
import { OtherChannelOrdersPanel } from "./panels/external-orders";
import { JackyunPanel } from "./panels/jackyun";
import { TaxInvoicesPanel } from "./panels/tax-invoices";
import { authenticatedFetch, masterDataApi } from "@/lib/api";

const TABS = [
  { key: "alibaba1688", label: "1688 采购订单", desc: "在线拉取或上传导出文件" },
  { key: "external_orders", label: "其他渠道采购订单", desc: "拼多多、淘宝、线下等订单号主档" },
  { key: "jackyun", label: "吉客云采购/入库单", desc: "采购单、采购入库单、结算单等客户端导出文件" },
  { key: "tax", label: "税务发票清单", desc: "税务系统官方发票清单" },
] as const;

const VISIBLE_IMPORT_TABS = TABS.filter((item) => item.key !== "tax");

type TabKey = (typeof TABS)[number]["key"];
const TAB_KEYS = TABS.map((t) => t.key) as readonly string[];

type MasterImportKey = "catalog" | "bundles" | "tax_rules" | "warehouses" | "suppliers" | "external_orders";
type ExportOption = { key: string; label: string; hint: string; loop: boolean; importKey?: MasterImportKey };

const EXPORTS: ExportOption[] = [
  { key: "purchase_orders", label: "采购订单", hint: "含明细与入库关联 · 仅核验", loop: false },
  { key: "catalog", label: "货品档案", hint: "正品单品 + 耗材基础档案", loop: true, importKey: "catalog" },
  { key: "inventory", label: "当前库存", hint: "运算结果 · 仅核验不回导", loop: false },
  { key: "warehouses", label: "仓库档案", hint: "仓库主档与吉客云 ID", loop: true, importKey: "warehouses" },
  { key: "bundles", label: "套装档案", hint: "套装与虚拟组合套装主档", loop: true, importKey: "bundles" },
  { key: "tax_rules", label: "财务分类", hint: "财务大类规则与税务代码", loop: true, importKey: "tax_rules" },
  { key: "suppliers", label: "供应商档案", hint: "税号、联系人与渠道信息", loop: true, importKey: "suppliers" },
  { key: "external_orders", label: "其他渠道订单号库", hint: "淘宝、拼多多、线下采购订单", loop: true, importKey: "external_orders" },
  { key: "production_orders", label: "生产订单（内部）", hint: "仅核验不回导", loop: false },
  { key: "material_flow", label: "生产采购链路", hint: "仅核验不回导", loop: false },
  { key: "inbound_documents", label: "吉客云入库单（导出核验）", hint: "入库单 + 入库明细 · 仅核验", loop: false },
  { key: "sales_items", label: "销售明细", hint: "销售单查询货品行 · 仅核验", loop: false },
  { key: "tax_invoices", label: "发票台账", hint: "发票关联与认证状态 · 仅核验", loop: false },
] as const;

export default function DataCenterImportPage() {
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<TabKey>("alibaba1688");
  const [exporting, setExporting] = useState<string | null>(null);
  const [importing, setImporting] = useState<string | null>(null);
  const [toolMessage, setToolMessage] = useState("");
  const [toolError, setToolError] = useState("");
  const returnOrder = searchParams.get("order");

  useEffect(() => {
    const param = searchParams.get("tab");
    if (param && TAB_KEYS.includes(param)) {
      setTab(param as TabKey);
    }
  }, [searchParams]);

  async function exportData(dataset: string, label: string, template: boolean = false) {
    setExporting(dataset);
    setToolMessage("");
    setToolError("");
    try {
      const response = await authenticatedFetch(`/api/v1/data/export/${dataset}${template ? "?template=1" : ""}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `导出失败（${response.status}）`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
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
      const count = response.headers.get("x-export-row-count");
      setToolMessage(
        template
          ? `${label}空模板已下载，填写后可在上方「导入数据」上传，系统会按档案 ID 或业务编码增量更新。`
          : `${label}已导出${count ? `（${count} 条主记录）` : ""}，可直接修改后从「导入数据」重新导入。`,
      );
    } catch (caught) {
      setToolError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setExporting(null);
    }
  }

  async function importMasterData(dataset: MasterImportKey, label: string, file: File | undefined) {
    if (!file) return;
    setImporting(dataset);
    setToolMessage("");
    setToolError("");
    try {
      const result = await masterDataApi.importXlsx(dataset, file);
      const mappingText = result.mappingsCreated || result.mappingsRemoved
        ? `，映射新增 ${result.mappingsCreated ?? 0}、移除 ${result.mappingsRemoved ?? 0}`
        : "";
      setToolMessage(`${label}已回导：新增 ${result.created}、更新 ${result.updated}${result.skipped ? `、跳过 ${result.skipped}` : ""}${mappingText}。库存流水和采购状态未被修改。`);
    } catch (caught) {
      setToolError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setImporting(null);
    }
  }

  return (
    <div>
      <header className="app-page-header -mx-1 bg-[#f4f7fb]/95 pb-3 backdrop-blur">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">数据接入</h1>
            <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-500">
              所有外部文件先在这里导入、预览、确认；业务事实也可以按数据维度导出，方便你在 Excel 中手动核验订单、入库、库存和成本链路。
            </p>
          </div>
          {tab === "alibaba1688" && (
            <div className="flex shrink-0 flex-wrap gap-2">
              <Link href="#data-source-panel" className="rounded-lg bg-indigo-600 px-3.5 py-2 text-xs font-medium text-white shadow-sm hover:bg-indigo-700">开始拉取 1688 采购订单</Link>
              <Link href="/purchase/workbench?view=orders" className="rounded-lg border border-slate-200 bg-white px-3.5 py-2 text-xs font-medium text-slate-600 shadow-sm hover:bg-slate-50">查看采购订单</Link>
            </div>
          )}
        </div>
        {returnOrder && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-indigo-100 bg-indigo-50/60 px-4 py-3 text-sm text-indigo-800"><span>正在为采购订单 <b className="font-mono">{returnOrder}</b> 补充原始资料。确认导入后，回到订单完成关联。</span><Link href={`/purchase/workbench?view=orders&order=${encodeURIComponent(returnOrder)}`} className="rounded-md bg-white px-3 py-1.5 text-xs font-medium text-indigo-600 shadow-sm">返回当前订单</Link></div>}
      </header>

      {/* 数据接入不再占顶部一级菜单；从各业务区进入后，由隐藏的数据接入侧栏切换来源。 */}

      <section id="data-tools" className="mt-5 grid gap-4 xl:grid-cols-[minmax(300px,0.8fr)_minmax(560px,1.5fr)]">
        <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">导入数据</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">选择来源后上传文件。默认会保留原始行，可关闭自动确认后逐行检查。</p>
            </div>
            <span className="rounded-full bg-white px-2 py-1 text-[10px] font-medium text-indigo-600">预览 · 确认</span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {VISIBLE_IMPORT_TABS.map((item) => (
              <Link
                key={item.key}
                href={`/data-center-import?tab=${item.key}#data-source-panel`}
                className={`flex items-center justify-between rounded-lg border bg-white px-3 py-2.5 text-xs transition hover:border-indigo-300 hover:bg-indigo-50 ${tab === item.key ? "border-indigo-300 ring-1 ring-indigo-200" : "border-indigo-100"}`}
              >
                <span><span className="font-medium text-slate-800">{item.label}</span><span className="ml-2 text-[10px] text-slate-400">{item.desc}</span></span>
                <span className="shrink-0 text-indigo-600">{item.key === "alibaba1688" ? "拉取 / 上传 →" : "上传 →"}</span>
              </Link>
            ))}
            <Link
              href="/logistics/bills?import=1"
              className="flex items-center justify-between rounded-lg border border-indigo-100 bg-white px-3 py-2.5 text-xs transition hover:border-indigo-300 hover:bg-indigo-50"
            >
              <span>
                <span className="font-medium text-slate-800">快递物流账单</span>
                <span className="ml-2 text-[10px] text-slate-400">仓配账单 / 运单明细 / 增值服务 / 报价表</span>
              </span>
              <span className="shrink-0 text-indigo-600">前往物流导入 →</span>
            </Link>
          </div>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-slate-900">导出数据 · 手动核验</h2>
              <p className="mt-1 text-xs leading-5 text-slate-500">
                导出本地已落库事实，不触发外部同步。带「可回导」的数据集支持闭环：导出 → 在 Excel 里修改/添加 → 点击同卡片的「回导」；系统按档案 ID 或业务编码增量更新。标注「仅核验」的结果请勿回导。
              </p>
            </div>
            <span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700">Excel XLSX</span>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
            {EXPORTS.map((item) => (
              <div
                key={item.key}
                className="flex min-h-[52px] items-center justify-between rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2 transition hover:border-indigo-200 hover:bg-indigo-50"
              >
                <button
                  type="button"
                  disabled={exporting !== null}
                  onClick={() => void exportData(item.key, item.label)}
                  className="min-w-0 flex-1 text-left disabled:cursor-wait disabled:opacity-60"
                >
                  <span><span className="block text-xs font-medium text-slate-700">{exporting === item.key ? "导出中…" : item.label}</span><span className="mt-0.5 block text-[10px] text-slate-400">{item.hint}</span></span>
                </button>
                {item.importKey && (
                  <label className="ml-2 shrink-0 cursor-pointer rounded border border-emerald-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 hover:bg-emerald-50">
                    {importing === item.importKey ? "回导中…" : "回导"}
                    <input
                      type="file"
                      accept=".xlsx,.xlsm"
                      className="hidden"
                      disabled={importing !== null}
                      onChange={(event) => {
                        void importMasterData(item.importKey!, item.label, event.target.files?.[0]);
                        event.target.value = "";
                      }}
                    />
                  </label>
                )}
                {item.loop && (
                  <button
                    type="button"
                    disabled={exporting !== null}
                    onClick={() => void exportData(item.key, item.label, true)}
                    title="下载只含表头的空模板"
                    className="ml-2 shrink-0 rounded border border-indigo-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-indigo-600 hover:bg-indigo-50 disabled:cursor-wait disabled:opacity-60"
                  >
                    模板
                  </button>
                )}
                <span className="ml-1.5 shrink-0 text-indigo-600">↓</span>
              </div>
            ))}
          </div>
          {toolMessage && <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{toolMessage}</div>}
          {toolError && <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{toolError}</div>}
        </div>
      </section>

      <div id="data-source-panel" className="mt-6 scroll-mt-24">
        {tab === "alibaba1688" && <Alibaba1688Panel />}
        {tab === "external_orders" && <OtherChannelOrdersPanel />}
        {tab === "jackyun" && <JackyunPanel />}
        {tab === "tax" && <TaxInvoicesPanel />}
      </div>
    </div>
  );
}
