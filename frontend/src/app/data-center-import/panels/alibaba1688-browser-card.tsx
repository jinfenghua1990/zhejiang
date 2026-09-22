"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Alibaba1688BrowserJob,
  Alibaba1688BrowserStatus,
  Alibaba1688SyncMode,
  Alibaba1688TimeField,
  alibaba1688BrowserApi,
  authenticatedFetch,
} from "@/lib/api";

const STATUS_LABELS: Record<string, string> = {
  connected: "已登录",
  needs_login: "待扫码登录",
  error: "异常",
  partial: "部分完成",
  untested: "未验证",
  unconfigured: "未启用",
};

function fmtTime(t: string | null): string {
  if (!t) return "—";
  try {
    return new Date(t).toLocaleString("zh-CN");
  } catch {
    return t;
  }
}

type RunningKind = "sync" | "login" | null;
type PullDimension = "all" | "supplier" | "keyword" | "single";
type PullDatePreset = "7d" | "30d" | "custom";

function localDateString(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function presetDateRange(days: number) {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - days + 1);
  return { start: localDateString(start), end: localDateString(end) };
}

type PullIconName = "account" | "grid" | "supplier" | "search" | "order" | "calendar" | "info" | "download" | "help" | "refresh";

function PullIcon({ name, size = 20 }: { name: PullIconName; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", "aria-hidden": true } as const;
  if (name === "grid") return <svg {...common}><rect x="4" y="4" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.8" /><rect x="14" y="4" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.8" /><rect x="4" y="14" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.8" /><rect x="14" y="14" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.8" /></svg>;
  if (name === "supplier") return <svg {...common}><path d="M4 10.5 12 4l8 6.5v8.5H4v-8.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="M8 19v-4h8v4M8 10h.01M12 10h.01M16 10h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "search") return <svg {...common}><circle cx="10.5" cy="10.5" r="5.8" stroke="currentColor" strokeWidth="1.8" /><path d="m15 15 4.5 4.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "order") return <svg {...common}><path d="M6 3.5h9l3 3V20H6V3.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /><path d="M9 11h6m-6 3.5h6m-6 3.5h3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "calendar") return <svg {...common}><rect x="4" y="5.5" width="16" height="14" rx="2" stroke="currentColor" strokeWidth="1.8" /><path d="M8 3.5v4M16 3.5v4M4 9.5h16" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "info") return <svg {...common}><circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.8" /><path d="M12 10.5v5m0-8h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "download") return <svg {...common}><path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19.5h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (name === "help") return <svg {...common}><circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.8" /><path d="M9.7 9.5a2.4 2.4 0 1 1 3.8 1.9c-.9.6-1.5 1-1.5 2.1m0 2.8h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
  if (name === "refresh") return <svg {...common}><path d="M19 8V4m0 0h-4m4 0a8 8 0 1 0 1 10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  return <svg {...common}><circle cx="12" cy="8" r="3" stroke="currentColor" strokeWidth="1.8" /><path d="M5 20c.7-3.4 3.2-5 7-5s6.3 1.6 7 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
}

function PullStep({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="border-b border-slate-200 px-3 py-3 last:border-b-0 sm:px-5 sm:py-3.5">
    <div className="flex items-baseline justify-between gap-3">
      <h3 className="text-[14px] font-semibold text-slate-900">{title}</h3>
      {hint && <p className="text-right text-[10px] text-slate-400">{hint}</p>}
    </div>
    <div className="mt-2">{children}</div>
  </section>;
}

function PullDimensionCard({ selected, icon, title, subtitle, onClick }: { selected: boolean; icon: PullIconName; title: string; subtitle: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`group relative flex min-h-[68px] items-center gap-2.5 rounded-xl border-2 px-3 py-2 text-left transition-colors sm:px-3.5 ${selected ? "border-indigo-500 bg-indigo-50/70" : "border-slate-100 bg-white hover:border-indigo-200 hover:bg-indigo-50/35"}`}>
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${selected ? "bg-indigo-100 text-indigo-600" : "bg-slate-50 text-slate-500 group-hover:text-indigo-600"}`}><PullIcon name={icon} size={20} /></span>
    <span className="min-w-0">
      <span className="block text-[13px] font-semibold text-slate-800">{title}</span>
      <span className="mt-0.5 block truncate text-[10px] text-slate-400">{subtitle}</span>
    </span>
    <span className={`ml-auto flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${selected ? "border-indigo-500 bg-indigo-600 text-white" : "border-slate-300 text-transparent"}`}>✓</span>
  </button>;
}

export function Alibaba1688BrowserCard({ onSynced, onClose }: { onSynced?: () => void; onClose?: () => void }) {
  const [status, setStatus] = useState<Alibaba1688BrowserStatus | null>(null);
  const [running, setRunning] = useState<RunningKind>(null);
  const initialRange = presetDateRange(7);
  const [dimension, setDimension] = useState<PullDimension>("all");
  const [datePreset, setDatePreset] = useState<PullDatePreset>("7d");
  const [startDate, setStartDate] = useState(initialRange.start);
  const [endDate, setEndDate] = useState(initialRange.end);
  const [orderNo, setOrderNo] = useState("");
  const [supplier, setSupplier] = useState("");
  const [keyword, setKeyword] = useState("");
  const [timeField, setTimeField] = useState<Alibaba1688TimeField>("order_time");
  const [onlyUnfinished, setOnlyUnfinished] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [message, setMessage] = useState<{ text: string; tone: "info" | "ok" | "warn" | "error" } | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await alibaba1688BrowserApi.status());
    } catch {
      // 状态读取失败不打断页面
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refreshStatus();
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refreshStatus]);

  // 轮询最近的 SyncJob，直到本次触发的任务结束（成功/失败/待登录）。
  const pollJob = useCallback(
    (kind: Exclude<RunningKind, null>, baselineId: number) => {
      const jobType = kind === "sync" ? "browser_orders" : "browser_login";
      const deadline = Date.now() + (kind === "sync" ? 6 : 6.5) * 60 * 1000;
      timerRef.current && clearInterval(timerRef.current);
      timerRef.current = setInterval(async () => {
        if (Date.now() > deadline) {
          if (timerRef.current) clearInterval(timerRef.current);
          if (mountedRef.current) {
            setRunning(null);
            setMessage({ text: "等待任务结果超时，请稍后刷新查看同步日志", tone: "warn" });
          }
          return;
        }
        try {
          const jobs = await alibaba1688BrowserApi.jobs(8);
          const job = jobs.find((j) => j.jobType === jobType && j.id > baselineId);
          if (!job) return; // 任务还在队列中
          if (job.status === "running") return;
          if (timerRef.current) clearInterval(timerRef.current);
          if (!mountedRef.current) return;
          setRunning(null);
          refreshStatus();
          if (kind === "sync") {
            onSynced?.();
            if (job.status === "success" || job.status === "partial") {
              const s = job.stats || {};
              const match = (s.remarkMatch || {}) as Record<string, unknown>;
              const linked = Number(match.linked || 0);
              const unverified = Number(match.unverified || 0);
              const scopeText = s.mode === "range"
                ? `${s.timeField === "pay_time" ? "付款时间" : "下单时间"} ${s.startDate || ""} 至 ${s.endDate || ""}`
                : s.mode === "single"
                  ? `订单号 ${s.orderNo || ""}`
                  : `默认增量（${s.timeField === "pay_time" ? "付款时间" : "下单时间"}）`;
              const supplierText = typeof s.supplier === "string" && s.supplier.trim()
                ? `，供应商 ${s.supplier}`
                : "";
              const keywordText = typeof s.keyword === "string" && s.keyword.trim()
                ? `，关键词 ${s.keyword}`
                : "";
              const unfinishedText = s.onlyUnfinished ? "，仅未完成" : "";
              const matchText = linked > 0
                ? `，备注已关联 ${linked} 单`
                : unverified > 0
                  ? `，备注待核验 ${unverified} 个`
                  : "";
              const closedText = Number(s.skippedClosed || 0) > 0
                ? `，跳过关闭 ${s.skippedClosed} 单`
                : "";
              const supplierSkipText = Number(s.skippedSupplier || 0) > 0
                ? `，跳过供应商不匹配 ${s.skippedSupplier} 单`
                : "";
              setMessage({
                text: `${job.status === "partial" ? "同步部分完成" : "同步完成"}（${scopeText}${supplierText}${keywordText}${unfinishedText}）：新增 ${s.created ?? 0} 单，恢复 ${s.restored ?? 0} 单，识别重复 ${s.duplicates ?? 0} 单，状态更新 ${s.merged ?? 0} 单${supplierSkipText}${closedText}，翻 ${s.pagesVisited ?? 0} 页${matchText}`,
                tone: job.status === "partial" ? "warn" : "ok",
              });
            } else if (job.status === "needs_login" || job.status === "login_required") {
              setMessage({ text: "1688 登录态已失效，请点击「先扫码登录」重新授权", tone: "warn" });
            } else if (job.status === "not_found") {
              setMessage({ text: job.errorSummary || "没有找到符合条件的 1688 采购订单，未写入订单", tone: "warn" });
            } else if (job.status === "closed_skipped") {
              setMessage({ text: job.errorSummary || "该订单已关闭，未写入采购订单", tone: "warn" });
            } else {
              setMessage({ text: `同步结束（${job.status}）：${job.errorSummary || "详见同步日志"}`, tone: "error" });
            }
          } else if (job.status === "success") {
            setMessage({ text: "扫码登录成功，登录态已保存到服务器", tone: "ok" });
          } else {
            setMessage({ text: `登录未完成（${job.status}）：${job.errorSummary || "请重试"}`, tone: "warn" });
          }
        } catch {
          // 单次轮询失败忽略
        }
      }, 3000);
    },
    [refreshStatus, onSynced]
  );

  const startLogin = useCallback(async () => {
    setMessage(null);
    setRunning("login");
    try {
      let baselineId = 0;
      try {
        const jobs: Alibaba1688BrowserJob[] = await alibaba1688BrowserApi.jobs(1);
        baselineId = jobs[0]?.id ?? 0;
      } catch {
        // 拿不到基线就从 0 开始
      }
      const res = await alibaba1688BrowserApi.login();
      setMessage({ text: res.message || "请在 Mac mini 屏幕前完成扫码（超时 5 分钟）", tone: "info" });
      pollJob("login", baselineId);
    } catch (e) {
      setRunning(null);
      setMessage({ text: "发起登录失败：" + String(e), tone: "error" });
    }
  }, [pollJob]);


  const connectOpenPlatform = useCallback(async () => {
    setMessage(null);
    try {
      const res = await authenticatedFetch("/api/v1/integrations/alibaba1688/auth-url", { cache: "no-store" });
      const data = await res.json();
      if (res.ok && data.authorizationUrl) {
        window.location.href = data.authorizationUrl;
        return;
      }
      setMessage({ text: "1688 开放平台授权：" + (data.detail ?? "当前未配置"), tone: "warn" });
    } catch (error) {
      setMessage({ text: "1688 开放平台授权失败：" + String(error), tone: "error" });
    }
  }, []);

  const startSync = useCallback(async () => {
    if (status?.status === "needs_login" || status?.status === "unconfigured") {
      await startLogin();
      return;
    }
    const effectiveMode: Alibaba1688SyncMode = dimension === "single" ? "single" : "range";
    if (effectiveMode === "range" && (!startDate || !endDate || startDate > endDate)) {
      setMessage({ text: !startDate || !endDate ? "请选择开始日期和结束日期" : "开始日期不能晚于结束日期", tone: "warn" });
      return;
    }
    if (dimension === "single" && !orderNo.trim()) {
      setMessage({ text: "请输入需要补拉的 1688 采购订单号", tone: "warn" });
      return;
    }
    if (dimension === "supplier" && !supplier.trim()) {
      setMessage({ text: "请输入供应商公司名或账号", tone: "warn" });
      return;
    }
    if (dimension === "keyword" && !keyword.trim()) {
      setMessage({ text: "请输入商品名称、SKU 等关键词", tone: "warn" });
      return;
    }
    setMessage(null);
    setRunning("sync");
    try {
      let baselineId = 0;
      try {
        const jobs: Alibaba1688BrowserJob[] = await alibaba1688BrowserApi.jobs(1);
        baselineId = jobs[0]?.id ?? 0;
      } catch {
        // 拿不到基线就从 0 开始
      }
      await alibaba1688BrowserApi.sync({
        mode: effectiveMode,
        ...(effectiveMode === "range" ? { startDate, endDate } : {}),
        ...(effectiveMode === "single" ? { orderNo: orderNo.trim() } : {}),
        ...(dimension === "supplier" && supplier.trim() ? { supplier: supplier.trim() } : {}),
        ...(dimension === "keyword" && keyword.trim() ? { keyword: keyword.trim() } : {}),
        onlyUnfinished: effectiveMode !== "single" && onlyUnfinished,
        timeField,
      });
      setMessage({ text: "已发起浏览器同步：正在服务器上打开 1688 订单页捕获数据…", tone: "info" });
      pollJob("sync", baselineId);
    } catch (e) {
      setRunning(null);
      setMessage({ text: "发起同步失败：" + String(e), tone: "error" });
    }
  }, [dimension, endDate, keyword, onlyUnfinished, orderNo, pollJob, startDate, startLogin, status?.status, supplier, timeField]);

  const badgeStatus = status?.status ?? "unconfigured";
  const effectiveMode: Alibaba1688SyncMode = dimension === "single" ? "single" : "range";
  const activeOnlyUnfinished = effectiveMode !== "single" && onlyUnfinished;
  const scopeInvalid = (effectiveMode === "range" && (!startDate || !endDate || startDate > endDate))
    || (dimension === "single" && !orderNo.trim())
    || (dimension === "supplier" && !supplier.trim())
    || (dimension === "keyword" && !keyword.trim());
  const isWaitingForLogin = status?.status === "needs_login" || status?.status === "unconfigured";

  const isModal = Boolean(onClose);
  const dimensionLabel = dimension === "supplier" ? `供应商：${supplier || "未填写"}` : dimension === "keyword" ? `关键词：${keyword || "未填写"}` : dimension === "single" ? `订单号：${orderNo || "未填写"}` : "全部订单";
  const rangeLabel = dimension === "single" ? "单笔订单" : `${startDate || "—"} 至 ${endDate || "—"}`;
  const connectionLabel = status?.status === "connected" ? "已连接" : STATUS_LABELS[badgeStatus] || "待检查";
  const dateRangeChanged = (preset: PullDatePreset) => {
    setDatePreset(preset);
    if (preset !== "custom") {
      const next = presetDateRange(preset === "30d" ? 30 : 7);
      setStartDate(next.start);
      setEndDate(next.end);
    }
  };

  return <div className={`overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[0_10px_36px_rgba(47,72,120,.08)] ${isModal ? "max-h-[calc(100vh-120px)] overflow-y-auto" : ""}`}>
    <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur sm:px-5">
      <div className="flex items-start gap-3">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-orange-400 to-orange-600 text-[17px] font-bold text-white shadow-[0_5px_12px_rgba(234,88,12,.2)]">1688</span>
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight text-slate-900 sm:text-[22px]">拉取 1688 采购订单</h2>
          <p className="mt-0.5 text-[11px] text-slate-500 sm:text-[12px]">从 1688 获取采购订单并同步到系统，系统将自动去重，避免重复导入。</p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-indigo-600">
          <button type="button" onClick={() => setHelpOpen((open) => !open)} className="hidden items-center gap-1 text-[11px] font-medium hover:text-indigo-700 sm:inline-flex"><PullIcon name="help" size={16} />使用说明</button>
          <span className="hidden h-5 w-px bg-slate-200 sm:block" />
          {onClose && <button type="button" aria-label="关闭拉取 1688 采购订单弹窗" onClick={onClose} className="rounded-lg px-1.5 py-0.5 text-[24px] leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>}
        </div>
      </div>
      {helpOpen && <div className="mt-2 rounded-lg bg-indigo-50 px-3 py-1.5 text-[10px] leading-5 text-indigo-700"><PullIcon name="info" size={14} /> <span className="ml-1">选择账号、拉取维度和时间范围后开始拉取。默认按订单号自动去重；已删除订单不会被常规拉取恢复，关闭/取消订单会跳过。</span></div>}
    </header>

    <div>
      <PullStep title="选择 1688 账号（数据来源）">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <label className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-slate-200 bg-white px-3 py-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-orange-50 text-orange-600"><PullIcon name="account" size={19} /></span>
            <select aria-label="1688 数据来源账号" value={status?.account || ""} disabled className="min-w-0 flex-1 appearance-none bg-transparent text-[14px] font-semibold text-slate-800 outline-none disabled:opacity-100">
              <option value="">当前浏览器账号（未登录）</option>
              {status?.account && <option value={status.account}>{status.account}</option>}
            </select>
            <span className="text-slate-400">⌄</span>
          </label>
          <span className={`inline-flex items-center gap-1.5 self-start rounded-full px-2.5 py-1 text-[11px] font-medium lg:self-auto ${status?.status === "connected" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}><span className={`h-2 w-2 rounded-full ${status?.status === "connected" ? "bg-emerald-500" : "bg-amber-500"}`} />{connectionLabel}</span>
          <span className="text-[11px] text-slate-500">最近同步：{fmtTime(status?.lastSyncAt ?? null)}</span>
          <button type="button" onClick={startLogin} disabled={running !== null || !status?.enabled} className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-700 hover:border-indigo-300 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-50"><PullIcon name="refresh" size={14} />重新登录</button><button type="button" onClick={connectOpenPlatform} disabled={running !== null} className="inline-flex h-8 items-center justify-center rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-700 hover:border-indigo-300 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-50">开放平台授权</button>
        </div>
        {!status?.enabled && <p className="mt-2 text-[11px] text-slate-400">浏览器通道未启用（配置 ALIBABA_1688_BROWSER_ENABLED）</p>}
      </PullStep>

      <PullStep title="选择拉取维度（按什么找订单）" hint="选择不同维度后，再填写对应的筛选条件">
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
          <PullDimensionCard selected={dimension === "all"} icon="grid" title="全部订单" subtitle="按时间范围拉取所有订单" onClick={() => setDimension("all")} />
          <PullDimensionCard selected={dimension === "supplier"} icon="supplier" title="供应商" subtitle="按供应商名称或账号" onClick={() => setDimension("supplier")} />
          <PullDimensionCard selected={dimension === "keyword"} icon="search" title="关键词" subtitle="按商品名称、SKU 等" onClick={() => setDimension("keyword")} />
          <PullDimensionCard selected={dimension === "single"} icon="order" title="订单号" subtitle="按 1688 订单号精准拉取" onClick={() => setDimension("single")} />
        </div>
        {dimension === "supplier" && <label className="mt-3 flex items-center gap-2 text-[12px] text-slate-600" htmlFor="alibaba1688-supplier"><PullIcon name="supplier" size={16} /><span className="shrink-0">供应商</span><input id="alibaba1688-supplier" value={supplier} onChange={(event) => setSupplier(event.target.value)} disabled={running !== null} placeholder="输入供应商公司名或账号" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-[12px] text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400" /></label>}
        {dimension === "keyword" && <label className="mt-3 flex items-center gap-2 text-[12px] text-slate-600" htmlFor="alibaba1688-keyword"><PullIcon name="search" size={16} /><span className="shrink-0">关键词</span><input id="alibaba1688-keyword" value={keyword} onChange={(event) => setKeyword(event.target.value)} disabled={running !== null} placeholder="输入商品名称、SKU、货品编码等" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 text-[12px] text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400" /></label>}
        {dimension === "single" && <label className="mt-3 flex items-center gap-2 text-[12px] text-slate-600" htmlFor="alibaba1688-order-no"><PullIcon name="order" size={16} /><span className="shrink-0">1688 采购订单号</span><input id="alibaba1688-order-no" value={orderNo} onChange={(event) => setOrderNo(event.target.value)} disabled={running !== null} placeholder="输入订单号，补拉单笔订单" className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-2 font-mono text-[12px] text-slate-700 outline-none placeholder:text-slate-400 focus:border-indigo-400" /></label>}
      </PullStep>

      <PullStep title="设置拉取范围（在什么范围里找）">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[minmax(190px,.7fr)_minmax(190px,.7fr)_minmax(300px,1.2fr)]">
          <label className="text-[12px] font-medium text-slate-600">时间字段<select aria-label="时间字段" value={timeField} onChange={(event) => setTimeField(event.target.value as Alibaba1688TimeField)} disabled={running !== null || dimension === "single"} className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-normal text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50"><option value="order_time">下单时间</option><option value="pay_time">付款时间</option></select></label>
          <label className="text-[12px] font-medium text-slate-600">时间范围<select aria-label="时间范围" value={datePreset} onChange={(event) => dateRangeChanged(event.target.value as PullDatePreset)} disabled={running !== null || dimension === "single"} className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-normal text-slate-700 outline-none focus:border-indigo-400 disabled:bg-slate-50"><option value="7d">最近 7 天</option><option value="30d">最近 30 天</option><option value="custom">自定义时间</option></select></label>
          <div className="text-[12px] font-medium text-slate-600">具体日期<div className="mt-1.5 flex h-10 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3"><PullIcon name="calendar" size={17} /><input aria-label="开始日期" type="date" value={startDate} onChange={(event) => { setDatePreset("custom"); setStartDate(event.target.value); }} disabled={running !== null || dimension === "single"} className="min-w-0 flex-1 bg-transparent text-[13px] font-normal text-slate-700 outline-none disabled:bg-slate-50" /><span className="text-slate-300">至</span><input aria-label="结束日期" type="date" value={endDate} onChange={(event) => { setDatePreset("custom"); setEndDate(event.target.value); }} disabled={running !== null || dimension === "single"} className="min-w-0 flex-1 bg-transparent text-[13px] font-normal text-slate-700 outline-none disabled:bg-slate-50" /></div></div>
        </div>
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-indigo-50 px-3 py-1.5 text-[10px] leading-5 text-indigo-700"><PullIcon name="info" size={15} /><span>将根据所选时间字段，在指定时间范围内拉取订单；重复订单按 1688 采购订单号自动合并，已删除和已关闭订单不会被常规拉取恢复。</span></div>
      </PullStep>

      <PullStep title="其他选项">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <label className="flex items-start gap-3 rounded-lg px-1 py-1"><input type="checkbox" checked readOnly aria-label="自动去重已拉取订单" className="mt-1 h-4 w-4 accent-indigo-600" /><span><span className="block text-[13px] font-medium text-slate-800">自动去重已拉取订单</span><span className="mt-1 block text-[11px] text-slate-400">系统会按 1688 订单号自动识别重复，避免重复导入。</span></span></label>
          <label className="flex items-start gap-3 rounded-lg px-1 py-1"><input type="checkbox" checked={onlyUnfinished} onChange={(event) => setOnlyUnfinished(event.target.checked)} disabled={running !== null || dimension === "single"} aria-label="仅拉取未完成订单" className="mt-1 h-4 w-4 accent-indigo-600" /><span><span className="block text-[13px] font-medium text-slate-800">仅拉取未完成订单</span><span className="mt-1 block text-[11px] text-slate-400">仅拉取 1688 上状态为未完成的订单（可选）。</span></span></label>
        </div>
      </PullStep>
    </div>

    <footer className="sticky bottom-0 z-20 flex flex-col gap-2.5 border-t border-slate-200 bg-white/95 px-4 py-2.5 backdrop-blur sm:flex-row sm:items-center sm:justify-between sm:px-5">
      <div className="flex min-w-0 items-start gap-2 text-[11px] text-slate-500"><PullIcon name="order" size={20} /><div className="min-w-0"><div className="font-medium text-slate-700">本次拉取</div><div className="mt-0.5 truncate">{dimensionLabel} · {rangeLabel} · {activeOnlyUnfinished ? "仅未完成" : "全部状态"}</div></div></div>
      <div className="flex shrink-0 items-center justify-end gap-2">{onClose && <button type="button" onClick={onClose} className="h-9 rounded-lg border border-slate-200 bg-white px-4 text-[12px] font-medium text-slate-700 hover:bg-slate-50">取消</button>}<button type="button" onClick={startSync} disabled={running !== null || !status?.enabled || (!isWaitingForLogin && scopeInvalid)} className="inline-flex h-9 items-center justify-center gap-2 rounded-lg bg-indigo-600 px-5 text-[12px] font-semibold text-white shadow-[0_5px_13px_rgba(79,70,229,.2)] hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"><PullIcon name="download" size={16} />{running === "sync" ? "拉取进行中…" : running === "login" ? "等待扫码…" : isWaitingForLogin ? "先扫码登录" : "开始拉取"}</button></div>
    </footer>
    {message && <div className={`mx-3 mb-3 rounded-lg border px-3 py-2 text-[11px] sm:mx-5 ${message.tone === "error" ? "border-rose-100 bg-rose-50 text-rose-700" : message.tone === "warn" ? "border-amber-100 bg-amber-50 text-amber-700" : message.tone === "ok" ? "border-emerald-100 bg-emerald-50 text-emerald-700" : "border-blue-100 bg-blue-50 text-blue-700"}`}>{message.text}</div>}
  </div>;
}
