"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Group = "all" | "production" | "transit" | "receiving" | "archive";
type Stage = "pending" | "waiting" | "producing" | "shipped" | "arrived" | "inbound" | "done";

type PurchaseItem = {
  skuId: number | string | null;
  skuCode: string;
  name: string;
  spec: string;
  quantity: number | null;
  amount: number | null;
  source: string;
};

type PurchaseProductionRow = {
  orderId: number | null;
  externalPoId: number | null;
  orderNo: string;
  platform: string;
  supplier: string;
  title: string;
  orderDate: string | null;
  amount: number | null;
  purchaseStatus: string;
  stage: string;
  stageLabel: string;
  archiveGroup: Group;
  items: PurchaseItem[];
  itemCount: number;
  quantityTotal: number;
  jackyunPurchaseNos: string[];
  jackyunInboundNos: string[];
  inboundWarehouses: string[];
  shipStatus: string;
  logistics: Record<string, unknown>;
};

type Payload = {
  rows: PurchaseProductionRow[];
  summary: {
    count: number;
    productionCount: number;
    producingCount: number;
    transitCount: number;
    receivingCount: number;
    completedCount: number;
  };
};

const STAGE_LABEL: Record<Stage, string> = {
  pending: "待确认",
  waiting: "待生产",
  producing: "生产中",
  shipped: "已发货",
  arrived: "已到货",
  inbound: "已入库",
  done: "已完成",
};
const STAGES = Object.keys(STAGE_LABEL) as Stage[];

const GROUPS: Array<{ key: Group; label: string }> = [
  { key: "all", label: "全部正品采购" },
  { key: "production", label: "待确认/待生产" },
  { key: "transit", label: "已发货/在途" },
  { key: "receiving", label: "到货/入库" },
  { key: "archive", label: "已完成" },
];

const STAGE_STYLE: Record<string, string> = {
  pending: "border-amber-200 bg-amber-50 text-amber-700",
  waiting: "border-sky-200 bg-sky-50 text-sky-700",
  producing: "border-indigo-200 bg-indigo-50 text-indigo-700",
  shipped: "border-cyan-200 bg-cyan-50 text-cyan-700",
  arrived: "border-violet-200 bg-violet-50 text-violet-700",
  inbound: "border-emerald-200 bg-emerald-50 text-emerald-700",
  done: "border-slate-200 bg-slate-50 text-slate-600",
};

function fmtDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function fmtMoney(value: number | null) {
  if (value === null || value === undefined) return "—";
  return `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function fmtQty(value: number | null) {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function logisticsText(logistics: Record<string, unknown>) {
  const values = [
    logistics.logisticsCompany,
    logistics.logisticName,
    logistics.company,
    logistics.trackingNo,
    logistics.logisticNo,
    logistics.mailNo,
  ].filter((value) => typeof value === "string" && value.trim());
  return values.length ? values.join(" · ") : "—";
}

export default function ProductionPurchaseBoard({
  initialGroup = "all",
  showTabs = true,
  title = "生产采购主单",
  description = "直接读取现有采购数据：正品自动进入生产链路，耗材采购自动排除。",
  actionLabel = "查看采购主单",
  actionHref,
}: {
  initialGroup?: Group;
  showTabs?: boolean;
  title?: string;
  description?: string;
  actionLabel?: string;
  actionHref?: (row: PurchaseProductionRow) => string;
}) {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const [group, setGroup] = useState<Group>(initialGroup);
  const [stage, setStage] = useState<Stage | "">("");
  const [rows, setRows] = useState<PurchaseProductionRow[]>([]);
  const [summary, setSummary] = useState<Payload["summary"]>({
    count: 0,
    productionCount: 0,
    producingCount: 0,
    transitCount: 0,
    receivingCount: 0,
    completedCount: 0,
  });
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const loadRequest = useRef(0);

  useEffect(() => {
    const requested = searchParams.get("group");
    if (requested && GROUPS.some((item) => item.key === requested)) setGroup(requested as Group);
    const requestedStage = searchParams.get("stage");
    setStage(requestedStage && STAGES.includes(requestedStage as Stage) ? requestedStage as Stage : "");
  }, [searchParams]);

  const load = useCallback(async () => {
    const requestId = ++loadRequest.current;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ group, limit: "2000" });
      if (stage) params.set("stage", stage);
      if (search.trim()) params.set("q", search.trim());
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-purchase-view?${params}`, { cache: "no-store" });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(typeof payload?.detail === "string" ? payload.detail : `生产采购数据加载失败（${response.status}）`);
      }
      const payload = (await response.json()) as Payload;
      if (requestId !== loadRequest.current) return;
      setRows(payload.rows ?? []);
      setSummary(payload.summary ?? summary);
    } catch (caught) {
      if (requestId !== loadRequest.current) return;
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (requestId === loadRequest.current) setLoading(false);
    }
  }, [group, search, stage]);

  useEffect(() => {
    void load();
  }, [load]);

  // 工作区下必须用本 Tab 冻结的 searchParams/pathname，window.location 会读到激活 Tab 的地址
  function changeGroup(next: Group) {
    setGroup(next);
    setStage("");
    const params = new URLSearchParams(searchParams.toString());
    if (next === "all") params.delete("group");
    else params.set("group", next);
    params.delete("stage");
    syncWorkspaceUrl(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`);
  }

  function clearStage() {
    setStage("");
    const params = new URLSearchParams(searchParams.toString());
    params.delete("stage");
    syncWorkspaceUrl(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`);
  }

  const visibleItems = useMemo(() => rows.reduce((total, row) => total + row.itemCount, 0), [rows]);

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">{title}</h2>
            <p className="mt-1 text-xs text-slate-500">{description}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && void load()}
              placeholder="订单号 / 工厂 / SKU"
              className="h-9 w-52 rounded-lg border border-slate-200 px-3 text-sm outline-none focus:border-indigo-400"
            />
            <button onClick={() => void load()} disabled={loading} className="h-9 rounded-lg border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50">
              {loading ? "刷新中…" : "刷新"}
            </button>
          </div>
        </div>

        {showTabs && (
          <div className="mt-4 flex flex-wrap gap-2">
            {GROUPS.map((item) => (
              <button
                key={item.key}
                onClick={() => changeGroup(item.key)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium ${group === item.key ? "border-indigo-200 bg-indigo-50 text-indigo-700" : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50"}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        )}

        {stage && (
          <button type="button" onClick={clearStage} className="mt-3 rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100">
            阶段筛选：{STAGE_LABEL[stage]} ×
          </button>
        )}

        <div className="mt-4 grid gap-2 sm:grid-cols-3 xl:grid-cols-7">
          <Metric label="当前显示" value={summary.count} />
          <Metric label="待确认/待生产" value={summary.productionCount} />
          <Metric label="其中生产中" value={summary.producingCount} />
          <Metric label="在途" value={summary.transitCount} />
          <Metric label="到货/入库" value={summary.receivingCount} />
          <Metric label="已完成" value={summary.completedCount} />
          <Metric label="明细行" value={visibleItems} />
        </div>
      </div>

      {error && <div className="m-4 rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[1250px] text-sm">
          <thead className="bg-slate-50 text-left text-[11px] font-medium text-slate-500">
            <tr>
              <th className="px-4 py-3">来源订单</th>
              <th className="px-4 py-3">工厂 / 供应商</th>
              <th className="px-4 py-3">生产内容</th>
              <th className="px-4 py-3">数量</th>
              <th className="px-4 py-3">吉客云单号</th>
              <th className="px-4 py-3">当前阶段</th>
              <th className="px-4 py-3">物流</th>
              <th className="px-4 py-3">下单时间</th>
              <th className="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <tr key={`${row.platform}:${row.orderNo}`} className="align-top hover:bg-slate-50/60">
                <td className="px-4 py-3">
                  <div className="font-medium text-slate-900">{row.orderNo || "—"}</div>
                  <div className="mt-1 flex items-center gap-1.5 text-[10px] text-slate-400">
                    <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5">{row.platform === "taobao" ? "淘宝" : row.platform === "pdd" ? "拼多多" : row.platform}</span>
                    <span className="rounded border border-blue-200 bg-blue-50 px-1.5 py-0.5 text-blue-600">正品</span>
                  </div>
                </td>
                <td className="px-4 py-3">
                  <div className="max-w-[220px] font-medium text-slate-700">{row.supplier || "未填写"}</div>
                  {row.title && <div className="mt-1 max-w-[220px] truncate text-[11px] text-slate-400" title={row.title}>{row.title}</div>}
                </td>
                <td className="px-4 py-3">
                  <div className="space-y-1">
                    {row.items.length ? row.items.slice(0, 4).map((item, index) => (
                      <div key={`${item.skuCode}:${index}`} className="max-w-[310px] text-xs text-slate-700">
                        <span className="font-medium">{item.name}</span>
                        {item.skuCode && <span className="ml-1 text-slate-400">{item.skuCode}</span>}
                        {item.spec && <span className="ml-1 text-slate-400">· {item.spec}</span>}
                        {item.quantity !== null && <span className="ml-1 text-slate-500">× {fmtQty(item.quantity)}</span>}
                      </div>
                    )) : <span className="text-xs text-amber-600">待确认采购内容</span>}
                    {row.items.length > 4 && <div className="text-[10px] text-slate-400">另 {row.items.length - 4} 项</div>}
                  </div>
                </td>
                <td className="px-4 py-3 font-medium tabular-nums text-slate-700">{row.quantityTotal ? fmtQty(row.quantityTotal) : "—"}</td>
                <td className="px-4 py-3">
                  <div className="space-y-1 text-xs">
                    {row.jackyunInboundNos.map((no) => <div key={`in:${no}`} className="font-medium text-emerald-700">入库 {no}</div>)}
                    {row.jackyunPurchaseNos.map((no) => <div key={`po:${no}`} className="text-slate-600">采购 {no}</div>)}
                    {!row.jackyunInboundNos.length && !row.jackyunPurchaseNos.length && <span className="text-slate-400">待关联</span>}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${STAGE_STYLE[row.stage] ?? STAGE_STYLE.pending}`}>
                    {row.stageLabel}
                  </span>
                  {row.inboundWarehouses.length > 0 && <div className="mt-1 text-[10px] text-slate-400">{row.inboundWarehouses.join(" / ")}</div>}
                </td>
                <td className="px-4 py-3">
                  <div className="max-w-[220px] text-xs text-slate-600">{logisticsText(row.logistics)}</div>
                  {row.shipStatus && <div className="mt-1 text-[10px] text-slate-400">{row.shipStatus}</div>}
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">{fmtDate(row.orderDate)}</td>
                <td className="px-4 py-3 text-right">
                  {row.orderId !== null ? (
                    <Link href={actionHref ? actionHref(row) : `/purchase/workbench?order=${row.orderId}`} className="inline-flex rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700">
                      {actionLabel}
                    </Link>
                  ) : <span className="text-xs text-slate-400">—</span>}
                </td>
              </tr>
            ))}
            {!loading && !rows.length && (
              <tr><td colSpan={9} className="px-4 py-12 text-center text-sm text-slate-400">当前没有符合条件的正品采购单</td></tr>
            )}
            {loading && (
              <tr><td colSpan={9} className="px-4 py-12 text-center text-sm text-slate-400">正在按现有采购数据整理生产订单…</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="border-t border-slate-100 bg-slate-50/60 px-5 py-3 text-[11px] text-slate-500">
        金额参考：{fmtMoney(rows.reduce((sum, row) => sum + (row.amount ?? 0), 0))}。本视图不复制订单，状态仍以采购主单 / 吉客云真实入库为准。
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2">
      <div className="text-[10px] text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-slate-800">{value.toLocaleString("zh-CN")}</div>
    </div>
  );
}
