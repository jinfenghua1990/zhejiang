"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import WorkspaceTabBar from "@/components/workspace/workspace-tabs";
import { TabRuntimeProvider, WorkspaceProvider, useWorkspace, type WorkspaceTab } from "@/lib/workspace/tab-store";
import { normalizeRoute, resolveRoute, routeComponent, tabIdentity } from "@/lib/workspace/route-table";
import { TabUrlProvider } from "@/lib/workspace/tab-url-context";

/**
 * 工作区宿主：全站页面的渲染入口（auth-shell 不再直接渲染 Next 的 children）。
 *
 * - 打开过的页面一直挂载，非激活 Tab 只做可见性隐藏 ⇒ 筛选、滚动、展开态、勾选、未提交输入全部保留；
 * - 每个 Tab 用 TabUrlProvider 冻结自己的 URL，页面内部的 usePathname/useSearchParams 永远读到自己那一份；
 * - 地址栏 = 激活 Tab 的 URL：切换/关闭 Tab 时同步，刷新后按 sessionStorage 恢复工作区。
 */
export default function WorkspaceHost() {
  return (
    <WorkspaceProvider>
      <WorkspaceLayout />
    </WorkspaceProvider>
  );
}

function WorkspaceLayout() {
  const ws = useWorkspace();
  const { unresolved } = useWorkspaceRouting();
  const wsRef = useRef(ws);
  wsRef.current = ws;

  // 只绑定一次浏览器关闭监听；工作区 context 每次状态变化都会换引用，
  // 不能因此反复 remove/add 全局监听。
  useEffect(() => {
    function onBeforeUnload(event: BeforeUnloadEvent) {
      if (!wsRef.current.anyDirty()) return;
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, []);

  return (
    <>
      <WorkspaceTabBar />
      <div className="relative min-h-0 w-full flex-1">
        {ws.tabs.map((tab) => (
          <WorkspacePanel key={tab.id} tab={tab} active={tab.id === ws.activeId} />
        ))}
        {ws.tabs.length === 0 && !unresolved && (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">正在打开工作区…</div>
        )}
        {unresolved && <UnknownRouteView />}
      </div>
    </>
  );
}

/** 路由 ↔ Tab 对齐：地址栏变化时激活已有 Tab 或新建 Tab；同一个 Tab 内部改参数只更新它自己。 */
function useWorkspaceRouting(): { unresolved: boolean } {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const ws = useWorkspace();
  const [unresolved, setUnresolved] = useState(false);
  const handledRef = useRef<string | null>(null);

  const search = searchParams.toString();
  const href = search ? `${pathname}?${search}` : pathname;

  useEffect(() => {
    if (!ws.ready) return;
    if (handledRef.current === href) return;
    handledRef.current = href;

    // 旧地址（/purchase、/procurement-chain/detail、/payments…）先归一化到正式地址
    const normalized = normalizeRoute(href);
    if (normalized !== href) {
      router.replace(normalized, { scroll: false });
      return;
    }

    const resolved = resolveRoute(href);
    if (!resolved) {
      setUnresolved(true);
      return;
    }
    setUnresolved(false);

    const identity = tabIdentity(resolved.pathname, resolved.search, resolved.entry);
    const active = ws.activeTab;
    if (active && active.workspace === resolved.workspace && active.identity === identity) {
      if (active.search !== resolved.search) ws.updateSearch(active.id, resolved.search);
      return;
    }

    // 打开失败（如已达到 Tab 上限）：把地址栏退回当前激活 Tab，避免地址与工作区脱节
    if (!ws.openRoute(href) && active) {
      const fallback = active.search ? `${active.pathname}?${active.search}` : active.pathname;
      if (fallback !== href) router.replace(fallback, { scroll: false });
    }
  }, [href, router, ws]);

  return { unresolved };
}

function WorkspacePanel({ tab, active }: { tab: WorkspaceTab; active: boolean }) {
  const ws = useWorkspace();
  const wsRef = useRef(ws);
  wsRef.current = ws;
  const scrollRef = useRef<HTMLDivElement>(null);
  const Component = routeComponent(tab.pathname);

  // 页面元素只创建一次：宿主重渲染不会连带重渲染这个 Tab 里的页面
  const element = useMemo(() => (Component ? <Component /> : null), [Component]);
  const bare = tab.pathname === "/purchase/workbench";

  // 滚动监听按 Tab 只绑定一次；不能因为其它 Tab 状态变化而给所有保活页重复解绑/重绑。
  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const onScroll = () => wsRef.current.setScrollTop(tab.id, node.scrollTop);
    node.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      node.removeEventListener("scroll", onScroll);
      wsRef.current.setScrollTop(tab.id, node.scrollTop);
    };
  }, [tab.id]);

  useEffect(() => {
    if (!active) return;
    const node = scrollRef.current;
    if (!node) return;
    const saved = wsRef.current.getScrollTop(tab.id);
    if (saved > 0 && Math.abs(node.scrollTop - saved) > 1) node.scrollTop = saved;
  }, [active, tab.id]);

  return (
    <div
      data-workspace-panel={tab.id}
      data-purchase-workbench={bare ? "true" : undefined}
      aria-hidden={!active}
      className="absolute inset-0"
      style={{
        visibility: active ? "visible" : "hidden",
        pointerEvents: active ? "auto" : "none",
        // 保留页签里的输入/滚动现场，但让浏览器跳过隐藏页的布局和绘制。
        contentVisibility: active ? "visible" : "hidden",
      }}
    >
      <TabUrlProvider pathname={tab.pathname} search={tab.search}>
        <TabRuntimeProvider tabId={tab.id} active={active}>
          <div ref={scrollRef} data-app-scroll className="h-full min-h-0 w-full min-w-0 overflow-x-hidden overflow-y-auto">
            <div className={`app-route-content w-full min-w-0 ${bare ? "" : "px-6 py-5 xl:px-8 xl:py-6"}`}>
              <Suspense fallback={<div className="px-1 py-10 text-sm text-slate-400">正在加载页面…</div>}>{element}</Suspense>
            </div>
          </div>
        </TabRuntimeProvider>
      </TabUrlProvider>
    </div>
  );
}

function UnknownRouteView() {
  const router = useRouter();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-sm text-slate-500">
      <div className="text-base font-medium text-slate-700">这个地址不在工作区里</div>
      <div className="text-xs text-slate-400">可能已改版或被移除，回到经营总览继续操作。</div>
      <button
        type="button"
        onClick={() => router.replace("/")}
        className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
      >
        回到经营总览
      </button>
    </div>
  );
}
