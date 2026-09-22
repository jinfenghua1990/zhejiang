"use client";

import Link from "@/components/workspace/workspace-link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { authenticatedFetch, changePassword, logout } from "@/lib/api";
import { MODULES, moduleWorkspace, resolveModule } from "@/lib/navigation";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";
import ThemeToggle from "@/components/theme-toggle";

type SearchItem = { label: string; sub: string };
type SearchGroups = {
  products: SearchItem[];
  suppliers: SearchItem[];
  purchases: SearchItem[];
  sales: SearchItem[];
};
type GlobalStatus = {
  pendingExceptions: number;
  lastSyncAt: string | null;
  state: "ok" | "running" | "failed" | "empty";
  sources: { provider: string; label: string; status: string; lastAt: string | null }[];
};

const EMPTY_GROUPS: SearchGroups = { products: [], suppliers: [], purchases: [], sales: [] };
const GROUP_META: { key: keyof SearchGroups; title: string }[] = [
  { key: "products", title: "货品" },
  { key: "suppliers", title: "供应商" },
  { key: "purchases", title: "采购单" },
  { key: "sales", title: "销售单" },
];

function clockOf(iso: string | null) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function ChevronDown() {
  return (
    <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5 shrink-0 text-slate-400" aria-hidden="true">
      <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true">
      <circle cx="9" cy="9" r="5.5" stroke="currentColor" strokeWidth="1.6" />
      <path d="m13.5 13.5 3 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

export default function TopBar() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const active = resolveModule(pathname, searchParams);
  const activeWorkspace = moduleWorkspace(active);
  // 财务共享同一页面，但 scope=foreign_trade 的工作台归属由统一路由规则决定。
  const isForeignWorkspace = activeWorkspace === "foreign";
  const workspaceLabel = isForeignWorkspace ? "外贸工作台" : "内销工作台";
  const workspaceHome = isForeignWorkspace ? "/foreign-trade" : "/";
  const topModules = MODULES.filter(
    (module) => module.showInTop !== false && moduleWorkspace(module) === (isForeignWorkspace ? "foreign" : "domestic"),
  );
  const [openMenu, setOpenMenu] = useState<"workspace" | "sync" | "account" | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [pwError, setPwError] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [groups, setGroups] = useState<SearchGroups>(EMPTY_GROUPS);
  const [searchOpen, setSearchOpen] = useState(false);
  const [status, setStatus] = useState<GlobalStatus | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const seqRef = useRef(0);
  const statusRequestInFlight = useRef(false);

  const loadStatus = useCallback(() => {
    if (statusRequestInFlight.current) return;
    statusRequestInFlight.current = true;
    authenticatedFetch("/api/v1/system/global-status")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: GlobalStatus | null) => data && setStatus(data))
      .catch(() => {})
      .finally(() => { statusRequestInFlight.current = false; });
  }, []);

  useEffect(() => {
    const runWhenVisible = () => {
      if (document.visibilityState === "visible") loadStatus();
    };
    runWhenVisible();
    const timer = window.setInterval(runWhenVisible, 60_000);
    document.addEventListener("visibilitychange", runWhenVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", runWhenVisible);
    };
  }, [loadStatus]);

  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpenMenu(null);
        setSearchOpen(false);
      }
    }
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, []);

  useEffect(() => {
    if (!passwordOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pwBusy) setPasswordOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [passwordOpen, pwBusy]);

  useEffect(() => {
    const trimmed = keyword.trim();
    if (!trimmed) {
      setGroups(EMPTY_GROUPS);
      setSearchOpen(false);
      return;
    }
    const seq = ++seqRef.current;
    const timer = window.setTimeout(() => {
      authenticatedFetch(`/api/v1/search/global?q=${encodeURIComponent(trimmed)}`)
        .then((res) => (res.ok ? res.json() : EMPTY_GROUPS))
        .then((data: SearchGroups) => {
          if (seqRef.current === seq) {
            setGroups(data);
            setSearchOpen(true);
          }
        })
        .catch(() => {});
    }, 250);
    return () => window.clearTimeout(timer);
  }, [keyword]);

  const hasHits = GROUP_META.some(({ key }) => groups[key].length > 0);

  function go(href: string) {
    setOpenMenu(null);
    setSearchOpen(false);
    // 走工作区地址同步，避开静态导出下 router.push 每次 1 秒多的 RSC 往返
    syncWorkspaceUrl(href, "push");
  }

  function submitSearch() {
    const trimmed = keyword.trim();
    if (!trimmed) return;
    go(groups.products.length > 0 ? `/products?q=${encodeURIComponent(trimmed)}` : `/sales?q=${encodeURIComponent(trimmed)}`);
  }

  async function submitPasswordChange() {
    setPwError("");
    if (pwNew.length < 8) {
      setPwError("新密码至少 8 位。");
      return;
    }
    if (pwNew !== pwConfirm) {
      setPwError("两次输入的新密码不一致。");
      return;
    }
    setPwBusy(true);
    try {
      await changePassword(pwOld, pwNew);
    } catch (error) {
      setPwError(error instanceof Error ? error.message : String(error));
      setPwBusy(false);
    }
  }

  function closePasswordModal() {
    if (pwBusy) return;
    setPasswordOpen(false);
    setPwOld("");
    setPwNew("");
    setPwConfirm("");
    setPwError("");
  }

  const syncDot =
    status?.state === "failed"
      ? "bg-rose-400"
      : status?.state === "running"
        ? "bg-amber-400 animate-pulse"
        : status?.state === "ok"
          ? "bg-emerald-400"
          : "bg-slate-300";
  const syncText =
    status?.state === "running"
      ? "同步中…"
      : status?.lastSyncAt
        ? `${clockOf(status.lastSyncAt)} 已同步`
        : "暂无同步";

  return (
    <header className="app-topbar relative z-40 flex h-14 shrink-0 items-center gap-3 border-b px-4 shadow-[0_1px_4px_rgba(15,39,70,0.04)]" ref={rootRef}>
      {/* 品牌 + 工作台切换 */}
      <Link href={workspaceHome} prefetch={false} className="flex shrink-0 items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-full app-brand-mark">
          <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" aria-hidden="true">
            <path d="M5 8h12v7a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5V8Z" fill="currentColor" />
            <path d="M17 10h1.4a2.6 2.6 0 1 1 0 5.2H17" stroke="currentColor" strokeWidth="1.8" />
          </svg>
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[15px] font-semibold tracking-[0.06em] text-slate-900">MEI FEI</span>
          <span className="block truncate text-[9px] text-slate-400">Good Coffee Better Life</span>
        </span>
      </Link>

      <div className="relative hidden shrink-0 xl:block">
        <button
          type="button"
          onClick={() => setOpenMenu((menu) => (menu === "workspace" ? null : "workspace"))}
          className="flex items-center gap-1 rounded-lg border border-slate-200 px-2 py-1.5 text-[12px] font-medium text-slate-700 hover:bg-slate-50"
        >
          {workspaceLabel}
          <ChevronDown />
        </button>
        {openMenu === "workspace" && (
          <div className="absolute left-0 top-full z-dropdown mt-1.5 w-52 app-popover rounded-xl border p-1.5 shadow-lg">
            <button
              type="button"
              onClick={() => go("/")}
              className={"flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[13px] font-medium " + (!isForeignWorkspace ? "bg-slate-50 text-slate-800" : "text-slate-600 hover:bg-slate-50")}
            >
              内销工作台
              {!isForeignWorkspace && <span className="text-[10px] font-normal text-blue-600">当前</span>}
            </button>
            <button
              type="button"
              onClick={() => go("/foreign-trade")}
              className={"flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-[13px] font-medium " + (isForeignWorkspace ? "bg-slate-50 text-slate-800" : "text-slate-600 hover:bg-slate-50")}
            >
              外贸工作台
              {isForeignWorkspace && <span className="text-[10px] font-normal text-blue-600">当前</span>}
            </button>
          </div>
        )}
      </div>

      {/* 一级业务导航 */}
      <nav className="top-primary-nav ml-2 flex min-w-0 max-w-[660px] flex-1 items-center gap-1 overflow-x-auto" aria-label="一级业务模块">
        {topModules.map((module) => {
          const isActive = module.key === active.key;
          return (
            <Link
              key={module.key}
              href={module.href}
              prefetch={false}
              aria-current={isActive ? "page" : undefined}
              className={`shrink-0 rounded-lg px-3.5 py-2 text-[13px] font-medium transition-colors ${
                isActive ? "app-nav-active" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              }`}
            >
              {module.label}
            </Link>
          );
        })}
      </nav>

      {/* 全局搜索 */}
      <div className="relative mx-2 hidden min-w-[180px] flex-1 max-w-[280px] 2xl:max-w-[420px] xl:block">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submitSearch();
          }}
          className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 focus-within:border-blue-400 focus-within:bg-white"
        >
          <SearchIcon />
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onFocus={() => hasHits && setSearchOpen(true)}
            placeholder="搜索货品、SKU、订单、供应商…"
            className="min-w-0 flex-1 bg-transparent text-[13px] text-slate-700 outline-none placeholder:text-slate-400"
          />
        </form>
        {searchOpen && hasHits && (
          <div className="absolute left-0 top-full z-dropdown mt-1.5 max-h-[420px] w-full overflow-y-auto app-popover rounded-xl border p-2 shadow-lg">
            {GROUP_META.map(({ key, title }) => {
              const items = groups[key];
              if (items.length === 0) return null;
              return (
                <div key={key} className="mb-1 last:mb-0">
                  <div className="px-2.5 py-1 text-[10px] font-medium tracking-wide text-slate-400">{title}</div>
                  {items.map((item) => (
                    <button
                      key={`${key}-${item.label}`}
                      type="button"
                      onClick={() => {
                        if (key === "products") go(`/products?q=${encodeURIComponent(item.label)}`);
                        else if (key === "sales") go(`/sales?q=${encodeURIComponent(item.label)}`);
                        else go(`/purchase/workbench?q=${encodeURIComponent(item.label)}`);
                      }}
                      className="flex w-full items-center justify-between gap-3 rounded-lg px-2.5 py-1.5 text-left hover:bg-slate-50"
                    >
                      <span className="min-w-0 truncate text-[13px] text-slate-700">{item.label}</span>
                      <span className="max-w-[150px] shrink-0 truncate text-[11px] text-slate-400">{item.sub}</span>
                    </button>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 右侧：同步 / 异常 / 设置 / 账号 */}
      <div className="ml-auto flex shrink-0 items-center gap-1">
        <div className="relative">
          <button
            type="button"
            onClick={() => setOpenMenu((menu) => (menu === "sync" ? null : "sync"))}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] text-slate-600 hover:bg-slate-100"
          >
            <span className={`h-2 w-2 rounded-full ${syncDot}`} />
            <span className="hidden 2xl:inline">{syncText}</span>
          </button>
          {openMenu === "sync" && (
            <div className="absolute right-0 top-full z-dropdown mt-1.5 w-64 app-popover rounded-xl border p-2 shadow-lg">
              <div className="px-2 py-1 text-[10px] font-medium tracking-wide text-slate-400">数据源同步状态</div>
              {(status?.sources ?? []).map((source) => (
                <div key={source.provider} className="flex items-center justify-between rounded-lg px-2.5 py-1.5 text-[12px]">
                  <span className="flex items-center gap-2 text-slate-700">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        source.status === "failed" ? "bg-rose-400" : source.status === "running" ? "bg-amber-400" : "bg-emerald-400"
                      }`}
                    />
                    {source.label}
                  </span>
                  <span className="text-[11px] text-slate-400">{clockOf(source.lastAt)}</span>
                </div>
              ))}
              {(!status || status.sources.length === 0) && (
                <div className="px-2.5 py-2 text-[12px] text-slate-400">还没有同步记录</div>
              )}
              <Link
                href="/data-center-import?tab=alibaba1688"
                prefetch={false}
                onClick={() => setOpenMenu(null)}
                className="mt-1 block rounded-lg px-2.5 py-1.5 text-[12px] text-blue-600 hover:bg-slate-50"
              >
                前往数据接入 →
              </Link>
            </div>
          )}
        </div>

        <Link
          href="/exceptions"
          prefetch={false}
          className="relative flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100"
          aria-label="异常中心"
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-[18px] w-[18px]" aria-hidden="true">
            <path d="M10 3.2a4.6 4.6 0 0 1 4.6 4.6c0 3 .8 4.3 1.5 5.1H3.9c.7-.8 1.5-2.1 1.5-5.1A4.6 4.6 0 0 1 10 3.2Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
            <path d="M8.5 15.6a1.6 1.6 0 0 0 3 0" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          {(status?.pendingExceptions ?? 0) > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-rose-500 px-1 text-[9px] font-semibold text-white">
              {status!.pendingExceptions}
            </span>
          )}
        </Link>

        <Link
          href="/settings/backup"
          prefetch={false}
          className={`flex h-8 items-center gap-1.5 rounded-lg px-2 text-[12px] font-medium transition-colors ${
            pathname.startsWith("/settings") || pathname.startsWith("/automation")
              ? "bg-slate-100 text-slate-800"
              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          }`}
          aria-label="系统设置"
          title="系统设置"
        >
          <svg viewBox="0 0 20 20" fill="none" className="h-[17px] w-[17px] shrink-0" aria-hidden="true">
            <circle cx="10" cy="10" r="2.4" stroke="currentColor" strokeWidth="1.5" />
            <path d="M10 2.8v2m0 10.4v2M2.8 10h2m10.4 0h2M4.9 4.9l1.4 1.4m7.4 7.4 1.4 1.4m0-10.2-1.4 1.4M6.3 13.7l-1.4 1.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <span>系统设置</span>
        </Link>

        <ThemeToggle />

        <div className="relative">
          <button
            type="button"
            onClick={() => setOpenMenu((menu) => (menu === "account" ? null : "account"))}
            className="flex items-center gap-1.5 rounded-lg py-1.5 pl-1.5 pr-2 hover:bg-slate-100"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[#112a49] text-[12px] font-semibold text-white">管</span>
            <span className="hidden text-[13px] font-medium text-slate-700 2xl:inline">管理员</span>
            <ChevronDown />
          </button>
          {openMenu === "account" && (
            <div className="absolute right-0 top-full z-dropdown mt-1.5 w-44 app-popover rounded-xl border p-1.5 shadow-lg">
              <button
                type="button"
                onClick={() => {
                  setOpenMenu(null);
                  setPwError("");
                  setPasswordOpen(true);
                }}
                className="block w-full rounded-lg px-3 py-2 text-left text-[13px] text-slate-700 hover:bg-slate-50"
              >
                修改管理员密码
              </button>
              {process.env.NEXT_PUBLIC_ACCESS_MODE !== "open" && (
                <button
                  type="button"
                  onClick={async () => {
                    setOpenMenu(null);
                    await logout();
                    router.replace("/login");
                  }}
                  className="block w-full rounded-lg px-3 py-2 text-left text-[13px] text-rose-600 hover:bg-rose-50"
                >
                  退出登录
                </button>
              )}
            </div>
          )}
        </div>
      </div>

      {passwordOpen && (
        <div
          className="fixed inset-0 z-modal flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[1px]"
          role="dialog"
          aria-modal="true"
          aria-label="修改管理员密码"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closePasswordModal();
          }}
        >
          <div className="w-full max-w-md overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900">
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
              <div>
                <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">修改管理员密码</h2>
                <p className="mt-1 text-[11px] leading-5 text-slate-500 dark:text-slate-300">修改成功后会退出当前登录，需要使用新密码重新登录。</p>
              </div>
              <button
                type="button"
                onClick={closePasswordModal}
                disabled={pwBusy}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-slate-200 text-lg leading-none text-slate-500 hover:bg-slate-50 disabled:opacity-40 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                aria-label="关闭"
              >
                ×
              </button>
            </div>

            <div className="space-y-4 p-5">
              <label className="block">
                <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">当前密码</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={pwOld}
                  onChange={(event) => setPwOld(event.target.value)}
                  className="app-input-control mt-1.5 block h-10 w-full rounded-lg px-3 text-sm"
                  placeholder="请输入当前管理员密码"
                />
              </label>
              <label className="block">
                <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">新密码</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={pwNew}
                  onChange={(event) => setPwNew(event.target.value)}
                  className="app-input-control mt-1.5 block h-10 w-full rounded-lg px-3 text-sm"
                  placeholder="至少 8 位"
                />
              </label>
              <label className="block">
                <span className="text-[11px] font-medium text-slate-600 dark:text-slate-300">确认新密码</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  value={pwConfirm}
                  onChange={(event) => setPwConfirm(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !pwBusy && pwOld && pwNew && pwConfirm) void submitPasswordChange();
                  }}
                  className="app-input-control mt-1.5 block h-10 w-full rounded-lg px-3 text-sm"
                  placeholder="再次输入新密码"
                />
              </label>
              {pwError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200">
                  {pwError}
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50/70 px-5 py-3 dark:border-slate-700 dark:bg-slate-800/50">
              <button type="button" onClick={closePasswordModal} disabled={pwBusy} className="app-button-secondary h-9 rounded-lg px-4 text-[12px] font-medium disabled:opacity-40">取消</button>
              <button
                type="button"
                onClick={() => void submitPasswordChange()}
                disabled={pwBusy || !pwOld || pwNew.length < 8 || !pwConfirm}
                className="app-button-primary h-9 rounded-lg px-4 text-[12px] font-medium disabled:cursor-not-allowed disabled:opacity-40"
              >
                {pwBusy ? "修改中…" : "确认修改"}
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
