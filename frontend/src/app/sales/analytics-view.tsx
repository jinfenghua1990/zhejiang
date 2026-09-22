"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { authenticatedFetch, dashboardApi, PlatformRow, profitApi, SkuRow, TrendPoint } from "@/lib/api";
import type { DetailInitial } from "./detail-view";

function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function numeric(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}

function percent(part: number, total: number, digits = 1): string {
  return total ? `${((part / total) * 100).toFixed(digits)}%` : "—";
}

function sumMetric(points: TrendPoint[], key: "salesAmount" | "costAmount" | "grossProfit"): number | null {
  if (!points.length) return null;
  return points.reduce<number>((sum, point) => sum + (numeric(point[key]) ?? 0), 0);
}

function localDate(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function shiftDate(date: string, days: number): string {
  const result = new Date(`${date}T00:00:00`);
  result.setDate(result.getDate() + days);
  return localDate(result);
}

function previousRange(start: string, end: string) {
  const days = Math.round((Date.parse(`${end}T00:00:00`) - Date.parse(`${start}T00:00:00`)) / 86400000) + 1;
  return { start: shiftDate(start, -days), end: shiftDate(start, -1), label: `前${days}天` };
}

function platformClass(platform: string): string {
  if (platform.startsWith("DY")) return "内容电商";
  if (platform.startsWith("得物")) return "垂直平台";
  if (/^(PDD|TM|JD|京喜)/.test(platform)) return "综合电商";
  return "其他平台";
}

function shortDate(value: string): string {
  const parts = value.split("-");
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : value;
}

function rollupTrend(points: TrendPoint[]): TrendPoint[] {
  if (points.length <= 31) return points;
  const grouped: Record<string, TrendPoint> = {};
  for (const point of points) {
    const month = point.date.slice(0, 7);
    const current = (grouped[month] ||= { date: month, orders: 0, salesAmount: "0", costAmount: "0", grossProfit: "0", costIncomplete: false });
    current.orders += point.orders;
    current.salesAmount = String(Number(current.salesAmount || 0) + Number(point.salesAmount || 0));
    // 个别订单缺成本时按已覆盖部分累计，并保留不完整标记，不再把整段置空。
    current.costIncomplete = current.costIncomplete || Boolean(point.costIncomplete);
    current.costAmount = String(Number(current.costAmount || 0) + Number(point.costAmount || 0));
    current.grossProfit = String(Number(current.grossProfit || 0) + Number(point.grossProfit || 0));
  }
  return Object.values(grouped).sort((a, b) => a.date.localeCompare(b.date));
}

const RANGE_BUTTONS: Array<["today" | "yesterday" | "d7" | "d30" | "pick_month" | "pick_year", string]> = [
  ["today", "今天"], ["yesterday", "昨天"], ["d7", "近7天"], ["d30", "近30天"], ["pick_month", "月份"], ["pick_year", "年份"],
];

const KPI_STYLE = {
  blue: ["border-blue-100 bg-gradient-to-br from-blue-50/90 to-white", "bg-[#5264f5]", "#6273f7"],
  cyan: ["border-sky-100 bg-gradient-to-br from-sky-50/90 to-white", "bg-[#19a9ef]", "#42b7ef"],
  amber: ["border-amber-100 bg-gradient-to-br from-amber-50/90 to-white", "bg-[#f6ad38]", "#f0b44c"],
  green: ["border-emerald-100 bg-gradient-to-br from-emerald-50/90 to-white", "bg-[#32c6a4]", "#43c9a9"],
  red: ["border-rose-100 bg-gradient-to-br from-rose-50/90 to-white", "bg-[#f45d64]", "#f2777d"],
} as const;

function Sparkline({ color }: { color: string }) {
  return <svg viewBox="0 0 86 34" className="h-9 w-20 shrink-0" aria-hidden="true"><path d="M2 28 C12 25, 13 17, 23 22 S35 27, 42 17 S54 23, 61 10 S73 15, 84 3 L84 34 L2 34 Z" fill={color} opacity=".1" /><path d="M2 28 C12 25, 13 17, 23 22 S35 27, 42 17 S54 23, 61 10 S73 15, 84 3" fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" /></svg>;
}

function KpiCard({ label, value, delta, hint, tone, icon }: { label: string; value: string; delta?: number | null; hint?: string; tone: keyof typeof KPI_STYLE; icon: string }) {
  const [card, iconBg, line] = KPI_STYLE[tone];
  return <article className={`relative overflow-hidden rounded-xl border px-3.5 py-3 shadow-[0_4px_14px_rgba(30,64,110,.04)] ${card}`}><div className="flex items-start justify-between gap-2"><div className="flex min-w-0 items-center gap-2.5"><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg font-semibold text-white shadow-sm ${iconBg}`}>{icon}</span><div className="min-w-0"><div className="truncate text-[11px] font-medium text-slate-500">{label}</div><div className="mt-1 text-[21px] font-bold leading-none tracking-tight text-slate-900 tabular-nums">{value}</div></div></div><Sparkline color={line} /></div><div className={`mt-2 text-[11px] font-medium tabular-nums ${delta === null || delta === undefined ? "text-slate-400" : delta >= 0 ? "text-emerald-600" : "text-rose-500"}`}>{delta === null || delta === undefined ? hint ?? "暂无对比数据" : `${delta >= 0 ? "↑" : "↓"} ${Math.abs(delta).toFixed(1)}%　较上期`}</div></article>;
}

function TrendChart({ points }: { points: TrendPoint[] }) {
  const width = 860;
  const height = 275;
  const left = 48;
  const right = 14;
  const top = 18;
  const bottom = 34;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const max = Math.max(1, ...points.flatMap((point) => [numeric(point.salesAmount), numeric(point.costAmount), numeric(point.grossProfit)]).filter((value): value is number => value !== null));
  const groupWidth = plotWidth / Math.max(points.length, 1);
  const barWidth = Math.max(3, Math.min(11, groupWidth * 0.18));
  const labelStep = Math.max(1, Math.ceil(points.length / 8));
  const metrics: Array<{ key: "salesAmount" | "costAmount" | "grossProfit"; label: string; color: string }> = [
    { key: "salesAmount", label: "销售金额", color: "#4c86ee" },
    { key: "costAmount", label: "货品成本", color: "#16c7a4" },
    { key: "grossProfit", label: "毛利润", color: "#5664f5" },
  ];
  if (!points.length) return <div className="flex h-[275px] items-center justify-center text-sm text-slate-400">该区间暂无销售数据</div>;
  return <div className="mt-3"><div className="mb-2 flex flex-wrap items-center justify-end gap-4 text-[11px] text-slate-500">{metrics.map((metric) => <span key={metric.key} className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full" style={{ backgroundColor: metric.color }} />{metric.label}</span>)}</div><svg viewBox={`0 0 ${width} ${height}`} className="h-[275px] w-full" role="img" aria-label="销售金额、货品成本和毛利润趋势"><title>销售金额、货品成本和毛利润趋势</title>{[0, 1, 2, 3, 4].map((tick) => { const y = top + (plotHeight * tick) / 4; const value = max * (1 - tick / 4); return <g key={tick}><line x1={left} x2={width - right} y1={y} y2={y} stroke="#e5ebf3" /><text x={left - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#94a3b8">¥{Math.round(value).toLocaleString("zh-CN")}</text></g>; })}{points.map((point, index) => { const center = left + groupWidth * index + groupWidth / 2; return <g key={point.date}>{metrics.map((metric, metricIndex) => { const value = numeric(point[metric.key]); if (value === null) return null; const barHeight = Math.max(2, (value / max) * plotHeight); const x = center + (metricIndex - 1) * (barWidth + 2) - barWidth / 2; const y = top + plotHeight - barHeight; return <rect key={metric.key} x={x} y={y} width={barWidth} height={barHeight} rx="2" fill={metric.color}><title>{`${point.date} · ${money(value)}`}</title></rect>; })}{(index % labelStep === 0 || index === points.length - 1) && <text x={center} y={height - 10} textAnchor="middle" fontSize="10" fill="#94a3b8">{shortDate(point.date)}</text>}</g>; })}</svg></div>;
}

function DonutChart({ rows, metric }: { rows: PlatformRow[]; metric: "salesAmount" | "grossProfit" }) {
  const values = rows.map((row) => numeric(row[metric]) ?? 0);
  const total = values.reduce((sum, value) => sum + value, 0);
  let cursor = 0;
  const stops = rows.map((_, index) => { const start = cursor; cursor += total ? (values[index] / total) * 100 : 0; return `${["#5664f5", "#16c7a4", "#b7bd2a", "#4d86f4", "#ff6b72", "#a878dc"][index % 6]} ${start}% ${cursor}%`; });
  return <div className="relative flex h-44 w-44 shrink-0 items-center justify-center rounded-full" style={{ background: total ? `conic-gradient(from -90deg, ${stops.join(", ")})` : "#e8eef7" }}><div className="flex h-28 w-28 flex-col items-center justify-center rounded-full bg-white text-center shadow-inner"><strong className="text-[17px] font-bold text-slate-800">{money(total)}</strong><span className="mt-0.5 text-[10px] text-slate-400">{metric === "salesAmount" ? "销售金额" : "毛利润"}</span></div></div>;
}

export default function SalesAnalyticsView({ onDrill }: { onDrill: (init: DetailInitial) => void }) {
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [platforms, setPlatforms] = useState<PlatformRow[]>([]);
  const [skus, setSkus] = useState<SkuRow[]>([]);
  const [previous, setPrevious] = useState<{ sales: number | null; orders: number; cost: number | null; gross: number | null; incomplete: boolean } | null>(null);
  const [costAlert, setCostAlert] = useState<{ error: string | null; warning: string | null } | null>(null);
  const [rangeKey, setRangeKey] = useState<"today" | "yesterday" | "d7" | "d30" | "pick_month" | "pick_year">("pick_month");
  const [pickMonth, setPickMonth] = useState(() => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`; });
  const [pickYear, setPickYear] = useState(() => String(new Date().getFullYear()));
  const [channelClass, setChannelClass] = useState("全部");
  const [donutMetric, setDonutMetric] = useState<"salesAmount" | "grossProfit">("salesAmount");
  const [filterNotes, setFilterNotes] = useState(false);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [importBad, setImportBad] = useState(false);
  const [importFileName, setImportFileName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const loadSeq = useRef(0);

  const range = useMemo(() => {
    const now = localDate(new Date());
    if (rangeKey === "today") return { start: now, end: now, label: "今天" };
    if (rangeKey === "yesterday") { const date = shiftDate(now, -1); return { start: date, end: date, label: "昨天" }; }
    if (rangeKey === "d7") return { start: shiftDate(now, -6), end: now, label: "近7天" };
    if (rangeKey === "d30") return { start: shiftDate(now, -29), end: now, label: "近30天" };
    if (rangeKey === "pick_month") { const [year, month] = pickMonth.split("-").map(Number); const lastDay = new Date(year, month, 0).getDate(); return { start: `${pickMonth}-01`, end: `${pickMonth}-${String(lastDay).padStart(2, "0")}`, label: `${pickMonth}月` }; }
    return { start: `${pickYear}-01-01`, end: `${pickYear}-12-31`, label: `${pickYear}年` };
  }, [pickMonth, pickYear, rangeKey]);

  function stepPeriod(direction: 1 | -1) {
    if (rangeKey === "pick_month") { const [year, month] = pickMonth.split("-").map(Number); const date = new Date(year, month - 1 + direction, 1); setPickMonth(`${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`); }
    if (rangeKey === "pick_year") setPickYear(String(Number(pickYear) + direction));
  }

  const load = useCallback(() => {
    const seq = ++loadSeq.current;
    setError("");
      // 月份模式下同时取月结/利润中心的计算结果：缺入库成本时把缺失 SKU 提示到页面上。
    const monthCompute = rangeKey === "pick_month"
      ? profitApi.compute(Number(pickMonth.slice(0, 4)), Number(pickMonth.slice(5, 7))).catch(() => null)
      : Promise.resolve(null);
    Promise.all([dashboardApi.salesTrend(365, range.start, range.end), dashboardApi.platformRanking(range.start, range.end), dashboardApi.skuRanking(10, range.start, range.end), dashboardApi.salesTrend(365, old.start, old.end), monthCompute])
      .then(([nextTrend, nextPlatforms, nextSkus, oldTrend, compute]) => {
        if (seq !== loadSeq.current) return;
        setTrend(nextTrend);
        setPlatforms(nextPlatforms);
        setSkus(nextSkus);
        setPrevious({ sales: sumMetric(oldTrend, "salesAmount"), orders: oldTrend.reduce((sum, point) => sum + point.orders, 0), cost: sumMetric(oldTrend, "costAmount"), gross: sumMetric(oldTrend, "grossProfit"), incomplete: oldTrend.some((point) => point.costIncomplete) });
        setCostAlert(compute ? { error: compute.error ?? null, warning: compute.warning ?? null } : null);
      })
      .catch((caught) => {
        if (seq === loadSeq.current) setError(String(caught));
      });
  }, [range, rangeKey, pickMonth]);

  useEffect(load, [load]);

  async function importFile(file: File) {
    setImporting(true); setImportMessage(""); setImportFileName(file.name);
    try {
      const form = new FormData(); form.append("file", file);
      const response = await authenticatedFetch("/api/v1/sales-file/import", { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) { setImportBad(true); setImportMessage(data?.detail || "文件格式无法解析，请检查文件后重试"); return; }
      if (Number(data?.itemsImported) <= 0) { setImportBad(true); setImportMessage("未读取到销售明细，系统未将本次导入视为成功"); return; }
      setImportBad(false); setImportMessage(`导入完成：有效订单 ${data.ordersImported} 单 · 明细 ${data.itemsImported} 行 · 跳过非成交状态 ${data.cancelledSkipped} 单${data.minDate ? ` · 覆盖 ${data.minDate} ~ ${data.maxDate}` : ""}`); load();
    } catch (caught) { setImportBad(true); setImportMessage(caught instanceof Error ? caught.message : "导入请求异常，请检查局域网连接后重试"); }
    finally { setImporting(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  const visiblePlatforms = useMemo(() => channelClass === "全部" ? platforms : platforms.filter((row) => platformClass(row.platform) === channelClass), [channelClass, platforms]);
  const trendPoints = useMemo(() => rollupTrend(trend), [trend]);
  const allSales = sumMetric(trend, "salesAmount") ?? 0;
  const allOrders = trend.reduce((sum, point) => sum + point.orders, 0);
  // 缺入库成本的订单按已覆盖部分计入，costIncomplete 标记用于提示补充数据。
  const costIncomplete = trend.some((point) => point.costIncomplete);
  const allCost = sumMetric(trend, "costAmount");
  const allGross = sumMetric(trend, "grossProfit");
  const allMargin = allGross !== null && allSales > 0 ? allGross / allSales * 100 : null;
  const filtered = channelClass !== "全部";
  const selectedSales = visiblePlatforms.reduce((sum, row) => sum + (numeric(row.salesAmount) ?? 0), 0);
  const selectedOrders = visiblePlatforms.reduce((sum, row) => sum + row.orders, 0);
  const selectedCost = visiblePlatforms.length ? visiblePlatforms.reduce((sum, row) => sum + (numeric(row.costAmount) ?? 0), 0) : null;
  const selectedGross = visiblePlatforms.length ? visiblePlatforms.reduce((sum, row) => sum + (numeric(row.grossProfit) ?? 0), 0) : null;
  const totalSales = filtered ? selectedSales : allSales;
  const totalOrders = filtered ? selectedOrders : allOrders;
  const totalCost = filtered ? selectedCost : allCost;
  const totalGross = filtered ? selectedGross : allGross;
  const margin = totalGross !== null && totalSales > 0 ? totalGross / totalSales * 100 : null;
  const channelTotal = visiblePlatforms.reduce((sum, row) => sum + (numeric(row.salesAmount) ?? 0), 0);
  const grossAvailable = visiblePlatforms.length > 0 && visiblePlatforms.every((row) => row.grossProfit != null);
  const old = previousRange(range.start, range.end);
  const salesDelta = !filtered && previous?.sales && previous.sales > 0 ? (totalSales - previous.sales) / previous.sales * 100 : null;
  const ordersDelta = !filtered && previous && previous.orders > 0 ? (totalOrders - previous.orders) / previous.orders * 100 : null;
  // 成本不完整时不做环比：部分成本与完整成本对比会失真。
  const costComparable = !costIncomplete && !previous?.incomplete;
  const costDelta = !filtered && costComparable && totalCost !== null && previous?.cost && previous.cost > 0 ? (totalCost - previous.cost) / previous.cost * 100 : null;
  const grossDelta = !filtered && costComparable && totalGross !== null && previous?.gross && previous.gross > 0 ? (totalGross - previous.gross) / previous.gross * 100 : null;
  const marginDelta = !filtered && costComparable && margin !== null && previous?.gross !== null && previous?.gross !== undefined && previous?.sales && previous.sales > 0 ? margin - previous.gross / previous.sales * 100 : null;
  const costNotice = costAlert?.error
    ? { tone: "error" as const, title: "毛利无法计算：入库成本全部缺失", text: costAlert.error }
    : costAlert?.warning
      ? { tone: "warn" as const, title: "部分 SKU 缺采购入库成本", text: costAlert.warning }
      : costIncomplete
        ? { tone: "warn" as const, title: "部分订单缺采购入库成本", text: "所选区间有订单没有采购入库成本，货品成本与毛利按已覆盖部分计算，实际毛利会更低，请补充入库成本。" }
        : null;

  return <>
    <section className="mt-4 rounded-xl border border-slate-200 bg-white px-3.5 py-3 shadow-sm"><div className="flex flex-wrap items-center gap-2"><div className="inline-flex overflow-hidden rounded-lg border border-slate-200 bg-white">{RANGE_BUTTONS.map(([key, label]) => <button key={key} onClick={() => setRangeKey(key)} className={`border-r border-slate-200 px-3 py-1.5 text-[11px] last:border-r-0 ${rangeKey === key ? "bg-[#5664f5] font-semibold text-white" : "text-slate-600 hover:bg-slate-50"}`}>{label}</button>)}</div>{rangeKey === "pick_month" && <><button onClick={() => stepPeriod(-1)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50">‹</button><input type="month" value={pickMonth} onChange={(event) => setPickMonth(event.target.value)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-400" /><button onClick={() => stepPeriod(1)} disabled={pickMonth >= localDate(new Date()).slice(0, 7)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-30">›</button></>}{rangeKey === "pick_year" && <><button onClick={() => stepPeriod(-1)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50">‹</button><input type="number" min={2000} max={2100} value={pickYear} onChange={(event) => setPickYear(event.target.value)} className="w-20 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700 outline-none focus:border-indigo-400" /><button onClick={() => stepPeriod(1)} disabled={Number(pickYear) >= new Date().getFullYear()} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-30">›</button></>}<span className="text-[11px] text-slate-400">{range.start} ~ {range.end}</span><select value={channelClass} onChange={(event) => setChannelClass(event.target.value)} className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] text-slate-600 outline-none focus:border-indigo-400"><option value="全部">渠道分类：全部</option><option value="综合电商">综合电商</option><option value="内容电商">内容电商</option><option value="垂直平台">垂直平台</option><option value="其他平台">其他平台</option></select><span className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[11px] text-slate-600">货币：人民币</span><button onClick={() => setFilterNotes((open) => !open)} aria-expanded={filterNotes} className="rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[11px] text-slate-600 hover:border-indigo-300 hover:text-indigo-600">⌯ 筛选条件</button><button onClick={() => fileRef.current?.click()} disabled={importing} title="上传吉客云导出的销售单文件，支持双 Sheet 或订单主表内含货品明细" className="ml-auto rounded-md bg-[#5664f5] px-3 py-1.5 text-[11px] font-semibold text-white shadow-[0_4px_12px_rgba(86,100,245,.25)] hover:bg-[#4654e8] disabled:opacity-50">{importing ? "导入中…" : "导入销售清单"}</button><input ref={fileRef} type="file" accept=".xlsx,.xls" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importFile(file); }} /></div>{filterNotes && <div className="mt-2 rounded-lg bg-indigo-50/70 px-3 py-2 text-[11px] text-indigo-700">统计仅计入成交状态订单；关闭、退货、取消、作废、待付款等不计入。货品成本和毛利按本系统采购入库单明细加权成本计算；个别订单缺成本时按已覆盖部分计入，并在页面上提示补充数据。</div>}</section>
    {importMessage && <div role={importBad ? "alert" : "status"} aria-live="polite" className={`mt-3 flex items-start gap-3 rounded-xl border px-3.5 py-3 shadow-sm ${importBad ? "border-rose-200 bg-rose-50 text-rose-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}><span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ${importBad ? "bg-rose-600 text-white" : "bg-emerald-600 text-white"}`}>{importBad ? "!" : "✓"}</span><div className="min-w-0 flex-1"><div className="text-xs font-semibold">{importBad ? "销售清单导入失败" : "销售清单导入完成"}</div><div className="mt-0.5 break-words text-[11px]">{importMessage}</div>{importBad && <div className="mt-1 text-[10px] text-rose-600">请上传 .xlsx 文件，并确认包含订单信息和销售明细。</div>}{importFileName && <div className="mt-1 truncate text-[10px] opacity-70">文件：{importFileName}</div>}</div>{importBad && <button type="button" onClick={() => fileRef.current?.click()} className="shrink-0 rounded-md border border-rose-200 bg-white px-2 py-1 text-[11px] text-rose-700">重新选择</button>}<button type="button" aria-label="关闭导入提示" onClick={() => { setImportMessage(""); setImportFileName(""); }} className="shrink-0 text-lg leading-4 opacity-50">×</button></div>}
    {costNotice && <div role="alert" className={`mt-3 flex items-start gap-3 rounded-xl border px-3.5 py-3 shadow-sm ${costNotice.tone === "error" ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}><span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${costNotice.tone === "error" ? "bg-rose-600" : "bg-amber-500"}`}>{costNotice.tone === "error" ? "!" : "▲"}</span><div className="min-w-0 flex-1"><div className="text-xs font-semibold">{costNotice.title}</div><div className="mt-0.5 break-words text-[11px]">{costNotice.text}</div></div><Link href="/data-center-import?tab=jackyun" className={`shrink-0 rounded-md border bg-white px-2 py-1 text-[11px] ${costNotice.tone === "error" ? "border-rose-200 text-rose-700" : "border-amber-200 text-amber-700"}`}>去补充入库成本</Link></div>}
    {error && <div className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">{error}</div>}
    <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5"><KpiCard label="销售金额（实收）" value={money(totalSales)} delta={salesDelta} hint={filtered ? "已按渠道分类筛选" : undefined} tone="blue" icon="¥" /><KpiCard label="订单数量" value={String(totalOrders)} delta={ordersDelta} hint={filtered ? "已按渠道分类筛选" : undefined} tone="cyan" icon="🛒" /><KpiCard label="货品成本" value={money(totalCost)} delta={costDelta} hint={costIncomplete ? "部分订单缺入库成本，按已覆盖部分计" : "按本系统采购入库加权成本"} tone="amber" icon="♟" /><KpiCard label="毛利润" value={money(totalGross)} delta={grossDelta} hint={costIncomplete ? "缺成本部分未计入，实际毛利更低" : "毛利 = 实付 − 入库成本"} tone="green" icon="↗" /><KpiCard label="毛利率" value={margin === null ? "—" : `${margin.toFixed(1)}%`} delta={marginDelta} hint={costIncomplete ? "缺成本部分未计入，实际毛利率更低" : "毛利 ÷ 实收金额"} tone="red" icon="%" /></div>
    <div className="mt-3 grid items-start gap-3 xl:grid-cols-[minmax(0,1.55fr)_minmax(390px,.95fr)]"><section className="rounded-xl border border-slate-200 bg-white p-4 shadow-[0_4px_14px_rgba(30,64,110,.035)]"><div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-800">销售趋势（按渠道）</h2><p className="mt-1 text-[11px] text-slate-400">{filtered ? "图表展示全部渠道趋势，指标和明细已按当前分类筛选" : `当前区间 ${range.label}`}</p></div><span className="text-[11px] text-slate-400">金额单位：人民币</span></div><TrendChart points={trendPoints} /></section><section className="rounded-xl border border-slate-200 bg-white p-4 shadow-[0_4px_14px_rgba(30,64,110,.035)]"><div className="flex items-center justify-between gap-3"><div><h2 className="text-sm font-semibold text-slate-800">渠道销售占比</h2><p className="mt-1 text-[11px] text-slate-400">销售渠道 · 点击明细查看订单</p></div><div className="inline-flex overflow-hidden rounded-md border border-slate-200 text-[11px]"><button onClick={() => setDonutMetric("salesAmount")} className={`px-2.5 py-1 ${donutMetric === "salesAmount" ? "bg-indigo-50 font-medium text-indigo-600" : "text-slate-500"}`}>销售金额</button><button disabled={!grossAvailable} onClick={() => setDonutMetric("grossProfit")} className={`border-l border-slate-200 px-2.5 py-1 ${donutMetric === "grossProfit" ? "bg-indigo-50 font-medium text-indigo-600" : "text-slate-500 disabled:opacity-40"}`}>毛利润</button></div></div><div className="mt-4 flex flex-wrap items-center justify-center gap-5 sm:flex-nowrap sm:justify-start"><DonutChart rows={visiblePlatforms} metric={donutMetric} /><div className="min-w-0 flex-1 space-y-2.5">{visiblePlatforms.map((row, index) => { const value = numeric(row[donutMetric]) ?? 0; return <button key={row.platform} onClick={() => onDrill({ platform: row.platform, start: range.start, end: range.end })} className="flex w-full items-center gap-2 text-left text-[11px] hover:text-indigo-600"><i className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: channelColor(index) }} /><span className="min-w-0 flex-1 truncate text-slate-600" title={row.platform}>{row.platform}</span><span className="w-10 text-right text-slate-400">{percent(value, visiblePlatforms.reduce((sum, item) => sum + (numeric(item[donutMetric]) ?? 0), 0))}</span><strong className="w-20 text-right font-medium tabular-nums text-slate-700">{money(value)}</strong></button>; })}</div></div>{donutMetric === "grossProfit" && !grossAvailable && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-700">部分渠道没有毛利字段，暂不切换毛利润占比。</div>}</section></div>
    <section className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_4px_14px_rgba(30,64,110,.035)]"><div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3"><div><h2 className="text-sm font-semibold text-slate-800">渠道明细（含利润成本）</h2><p className="mt-1 text-[11px] text-slate-400">按渠道汇总有效订单；点击“查看”进入销售明细</p></div><button onClick={() => onDrill({ start: range.start, end: range.end })} className="rounded-md border border-slate-200 px-3 py-1.5 text-[11px] text-indigo-600 hover:border-indigo-300 hover:bg-indigo-50">查看全部明细</button></div><div className="overflow-x-auto"><table className="w-full min-w-[980px] text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="w-12 px-4 py-2.5 text-left font-medium">#</th><th className="px-3 py-2.5 text-left font-medium">销售渠道</th><th className="px-3 py-2.5 text-left font-medium">渠道分类</th><th className="px-3 py-2.5 text-right font-medium">订单数量</th><th className="px-3 py-2.5 text-right font-medium">销售金额（实收）</th><th className="px-3 py-2.5 text-right font-medium">货品成本</th><th className="px-3 py-2.5 text-right font-medium">毛利润</th><th className="px-3 py-2.5 text-right font-medium">毛利率</th><th className="w-20 px-4 py-2.5 text-right font-medium">操作</th></tr></thead><tbody className="divide-y divide-slate-100">{visiblePlatforms.map((row, index) => { const sales = numeric(row.salesAmount) ?? 0; const cost = numeric(row.costAmount); const gross = numeric(row.grossProfit); return <tr key={row.platform} className="transition-colors hover:bg-indigo-50/40"><td className="px-4 py-2.5 text-slate-400">{index + 1}</td><td className="max-w-[260px] truncate px-3 py-2.5 font-medium text-slate-700" title={row.platform}>{row.platform}</td><td className="px-3 py-2.5 text-slate-500">{platformClass(row.platform)}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{row.orders}</td><td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-800">{money(sales)}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{money(cost)}</td><td className="px-3 py-2.5 text-right font-medium tabular-nums text-slate-700">{money(gross)}</td><td className="px-3 py-2.5 text-right tabular-nums text-slate-600">{gross === null || !sales ? "—" : `${(gross / sales * 100).toFixed(2)}%`}</td><td className="px-4 py-2.5 text-right"><button onClick={() => onDrill({ platform: row.platform, start: range.start, end: range.end })} className="font-medium text-indigo-600 hover:underline">查看</button></td></tr>; })}{!visiblePlatforms.length && <tr><td colSpan={9} className="px-4 py-10 text-center text-slate-400">暂无渠道数据</td></tr>}</tbody><tfoot className="bg-indigo-50/60 font-semibold text-slate-800"><tr><td colSpan={3} className="px-4 py-2.5 text-right">合计</td><td className="px-3 py-2.5 text-right tabular-nums">{totalOrders}</td><td className="px-3 py-2.5 text-right tabular-nums">{money(channelTotal)}</td><td className="px-3 py-2.5 text-right tabular-nums">{money(totalCost)}</td><td className="px-3 py-2.5 text-right tabular-nums">{money(totalGross)}</td><td className="px-3 py-2.5 text-right tabular-nums">{margin === null ? "—" : `${margin.toFixed(2)}%`}</td><td /></tr></tfoot></table></div></section>
    <section className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_4px_14px_rgba(30,64,110,.035)]"><div className="flex items-center justify-between px-4 py-3"><div><h2 className="text-sm font-semibold text-slate-800">货品销售排行</h2><p className="mt-1 text-[11px] text-slate-400">Top {skus.length} · 点击查看对应销售明细</p></div><span className="text-[11px] text-slate-400">区间销售额 {money(allSales)}</span></div><div className="overflow-x-auto"><table className="w-full min-w-[720px] text-xs"><thead className="bg-slate-50 text-slate-500"><tr><th className="w-12 px-4 py-2 text-left font-medium">#</th><th className="px-3 py-2 text-left font-medium">SKU</th><th className="px-3 py-2 text-left font-medium">商品</th><th className="px-3 py-2 text-right font-medium">订单数</th><th className="px-3 py-2 text-right font-medium">销售额</th><th className="px-4 py-2 text-right font-medium">占比</th></tr></thead><tbody className="divide-y divide-slate-100">{skus.map((sku, index) => <tr key={sku.skuCode} onClick={() => onDrill({ sku: sku.skuCode, start: range.start, end: range.end })} className="cursor-pointer hover:bg-indigo-50/40"><td className="px-4 py-2 text-slate-400">{index + 1}</td><td className="px-3 py-2 font-mono text-slate-500">{sku.skuCode}</td><td className="max-w-[420px] truncate px-3 py-2 text-slate-700">{sku.goodsName}</td><td className="px-3 py-2 text-right tabular-nums text-slate-600">{sku.orders}</td><td className="px-3 py-2 text-right font-medium tabular-nums">{money(sku.salesAmount)}</td><td className="px-4 py-2 text-right tabular-nums text-slate-400">{percent(numeric(sku.salesAmount) ?? 0, allSales)}</td></tr>)}</tbody></table></div></section>
    <div className="mt-3 flex items-center justify-between rounded-lg border border-indigo-100 bg-indigo-50/50 px-4 py-2.5 text-[11px] text-slate-500"><span><strong className="mr-2 text-indigo-600">数据洞察</strong>{costIncomplete ? "本期部分订单缺采购入库成本，货品成本与毛利按已覆盖部分计算，实际毛利会更低，请补充入库成本后重算。" : margin === null ? "当前区间尚无成本 / 毛利数据。" : `本期整体毛利率 ${margin.toFixed(2)}%，成本与毛利按本系统采购入库单明细汇总。`}</span><button onClick={() => onDrill({ start: range.start, end: range.end })} className="font-medium text-indigo-600 hover:underline">查看详细分析 ›</button></div>
  </>;
}

function channelColor(index: number): string {
  return ["#5664f5", "#16c7a4", "#b7bd2a", "#4d86f4", "#ff6b72", "#a878dc"][index % 6];
}
