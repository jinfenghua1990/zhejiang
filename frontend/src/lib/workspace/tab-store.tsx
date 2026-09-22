"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { normalizeRoute, resolveRoute, routeWorkspace, tabBusinessId, tabIdentity, tabTitle, type RouteEntry, type WorkspaceKey } from "./route-table";

/**
 * 工作区 Tab 状态：谁打开了、当前激活哪个、每个 Tab 的 URL / 标题 / 来源 / 固定状态。
 *
 * 现场（筛选、滚动、展开态、未提交输入）不在 URL 里，而是靠 Tab 组件一直挂载 + 这里的
 * 滚动位置与 page scope 快照兜底（刷新后恢复）。
 */
export const MAX_TABS = 12;

export type WorkspaceTab = {
  id: string;
  /** 所属独立工作台：内销 / 外贸。 */
  workspace: WorkspaceKey;
  /** 该 Tab 自己的路径（不含 query） */
  pathname: string;
  /** 该 Tab 自己的 query（不含 "?"） */
  search: string;
  /** 页面用 useTabTitle 上报的业务标题（如「采购单 · CG24091801」） */
  title: string | null;
  /** 路由表推导的默认标题 */
  baseTitle: string;
  businessType: string;
  businessId: string | null;
  /** 去重身份：pathname + 业务参数 */
  identity: string;
  /** 由哪个 Tab 打开（页面内「← 返回」回到来源 Tab） */
  sourceTabId: string | null;
  closable: boolean;
  pinned: boolean;
  createdAt: number;
  lastActiveAt: number;
};

export type TabDisplayTitle = string;

const STORAGE_KEY = "workspace.tabs.v1";

type PersistedTab = Omit<WorkspaceTab, "workspace"> & { workspace?: WorkspaceKey };

type PersistedState = {
  version: 1;
  activeId: string | null;
  tabs: PersistedTab[];
  scrollTops: Record<string, number>;
  scopes: Record<string, Record<string, unknown>>;
};

let tabSeq = 0;
function nextTabId(): string {
  tabSeq += 1;
  return `t${Date.now().toString(36)}${tabSeq}`;
}

function readPersisted(): PersistedState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedState;
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.tabs)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export type WorkspaceApi = {
  /** 变更计数：context 值随它变化，保证消费方（Tab 条 / 页面 hooks）能重渲染 */
  version: number;
  /** 是否已完成 sessionStorage 恢复（恢复前不做路由↔Tab 对齐，避免覆盖） */
  ready: boolean;
  tabs: WorkspaceTab[];
  activeId: string | null;
  activeTab: WorkspaceTab | null;
  activeWorkspace: WorkspaceKey;
  tabsForWorkspace: (workspace: WorkspaceKey) => WorkspaceTab[];
  maxTabs: number;
  notice: string | null;
  setNotice: (message: string | null) => void;
  /** 按地址打开/激活 Tab（命中已有身份则直接切过去）；返回 tabId */
  openRoute: (href: string, options?: { sourceTabId?: string | null; silent?: boolean; forceNew?: boolean }) => string | null;
  activate: (id: string) => void;
  /** 关闭 Tab；返回关闭后应该激活的 Tab id（调用方负责同步地址栏） */
  close: (id: string) => string | null;
  closeOthers: (id: string) => void;
  closeRight: (id: string) => void;
  togglePin: (id: string) => void;
  duplicate: (id: string) => string | null;
  /**
   * 当前 Tab 就地换成新地址（关闭详情、订单被删除等「对象消失」场景）。
   * 新地址若已被别的 Tab 代表，则本 Tab 退场并切回那个 Tab（等价于「返回来源」）。
   * 返回调用方应同步到地址栏的地址。
   */
  retarget: (href: string) => string;
  updateSearch: (id: string, search: string) => void;
  setTitle: (id: string, title: string | null) => void;
  setScrollTop: (id: string, top: number) => void;
  getScrollTop: (id: string) => number;
  setScope: (id: string, key: string, value: unknown) => void;
  getScope: <T>(id: string, key: string) => T | undefined;
  setDirty: (id: string, dirty: boolean) => void;
  isDirty: (id: string) => boolean;
  anyDirty: () => boolean;
  displayTitle: (tab: WorkspaceTab) => string;
};

const WorkspaceContext = createContext<WorkspaceApi | null>(null);

export function useWorkspace(): WorkspaceApi {
  const api = useContext(WorkspaceContext);
  if (!api) throw new Error("useWorkspace 必须在 WorkspaceProvider 内使用");
  return api;
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  const stateRef = useRef<{ tabs: WorkspaceTab[]; activeId: string | null }>({ tabs: [], activeId: null });
  const scrollRef = useRef<Record<string, number>>({});
  const scopeRef = useRef<Record<string, Record<string, unknown>>>({});
  const dirtyRef = useRef<Set<string>>(new Set());
  const saveTimerRef = useRef<number | null>(null);

  const bump = useCallback(() => setVersion((value) => value + 1), []);

  // 首次挂载后恢复上次的工作区（Tab 列表 / 滚动位置 / 列表页筛选快照）。
  // 必须放在 effect 里：静态导出的预渲染 HTML 没有 sessionStorage，首帧保持一致避免 hydration 不匹配。
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const persisted = readPersisted();
    if (persisted) {
      stateRef.current = {
        tabs: persisted.tabs.slice(0, MAX_TABS * 2).map((tab) => ({
          ...tab,
          // 路由归属规则会迭代；恢复历史 Tab 时按当前 pathname + query 重新计算，
          // 避免旧 sessionStorage 永久保留曾经错误的工作台归属。
          workspace: routeWorkspace(tab.pathname, tab.search),
        })),
        activeId: null,
      };
      scrollRef.current = { ...persisted.scrollTops };
      scopeRef.current = { ...persisted.scopes };
      bump();
    }
    setReady(true);
  }, [bump]);

  const persist = useCallback(() => {
    if (typeof window === "undefined") return;
    const payload: PersistedState = {
      version: 1,
      activeId: stateRef.current.activeId,
      tabs: stateRef.current.tabs,
      scrollTops: scrollRef.current,
      scopes: scopeRef.current,
    };
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // 隐私模式等场景写入失败不影响使用
    }
  }, []);

  const schedulePersist = useCallback(() => {
    if (typeof window === "undefined") return;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      persist();
    }, 400);
  }, [persist]);

  // 关闭/刷新页面前立即落盘，避免丢掉最后一次滚动位置或筛选
  useEffect(() => {
    function flush() {
      persist();
    }
    window.addEventListener("beforeunload", flush);
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("pagehide", flush);
    };
  }, [persist]);

  const api = useMemo<WorkspaceApi>(() => {
    function findById(id: string | null): WorkspaceTab | null {
      if (!id) return null;
      return stateRef.current.tabs.find((tab) => tab.id === id) ?? null;
    }

    function openRoute(href: string, options?: { sourceTabId?: string | null; silent?: boolean; forceNew?: boolean }): string | null {
      const resolved = resolveRoute(href);
      if (!resolved) return null;
      const { pathname, search, entry, workspace } = resolved;
      const identity = tabIdentity(pathname, search, entry);

      const existing = options?.forceNew ? undefined : stateRef.current.tabs.find((tab) => tab.workspace === workspace && tab.identity === identity);
      if (existing) {
        if (existing.pathname === pathname && existing.search !== search) {
          // 同一个业务对象：同步 query 的同时刷新标题/业务标识，避免“销售明细”仍显示旧 Tab 标题。
          stateRef.current.tabs = stateRef.current.tabs.map((tab) =>
            tab.id === existing.id
              ? {
                  ...tab,
                  search,
                  title: null,
                  baseTitle: tabTitle(entry, search),
                  businessId: tabBusinessId(entry, search),
                  identity,
                  lastActiveAt: Date.now(),
                }
              : tab,
          );
        }
        stateRef.current.activeId = existing.id;
        bump();
        schedulePersist();
        return existing.id;
      }

      const workspaceTabs = stateRef.current.tabs.filter((tab) => tab.workspace === workspace);
      if (workspaceTabs.length >= MAX_TABS) {
        if (!options?.silent) {
          setNotice(`${workspace === "foreign" ? "外贸" : "内销"}工作台最多同时打开 ${MAX_TABS} 个页面，请先关闭一些页面再打开新的。`);
        }
        return null;
      }

      const requestedSourceId = options?.sourceTabId === undefined ? stateRef.current.activeId : options.sourceTabId;
      const sourceTab = requestedSourceId ? stateRef.current.tabs.find((item) => item.id === requestedSourceId) : null;
      const sourceTabId = sourceTab?.workspace === workspace ? sourceTab.id : null;
      const tab: WorkspaceTab = {
        id: nextTabId(),
        workspace,
        pathname,
        search,
        title: null,
        baseTitle: tabTitle(entry, search),
        businessType: entry.businessType,
        businessId: tabBusinessId(entry, search),
        identity,
        sourceTabId,
        closable: entry.closable !== false,
        pinned: entry.pinned === true,
        createdAt: Date.now(),
        lastActiveAt: Date.now(),
      };
      stateRef.current.tabs = [...stateRef.current.tabs, tab];
      stateRef.current.activeId = tab.id;
      setNotice(null);
      bump();
      schedulePersist();
      return tab.id;
    }

    function activate(id: string) {
      if (stateRef.current.activeId === id) return;
      if (!findById(id)) return;
      stateRef.current.activeId = id;
      stateRef.current.tabs = stateRef.current.tabs.map((tab) => (tab.id === id ? { ...tab, lastActiveAt: Date.now() } : tab));
      bump();
      schedulePersist();
    }

    function close(id: string): string | null {
      const tabs = stateRef.current.tabs;
      const index = tabs.findIndex((tab) => tab.id === id);
      if (index < 0) return stateRef.current.activeId;
      const tab = tabs[index];
      if (!tab.closable || tab.pinned) return stateRef.current.activeId;

      const remaining = tabs.filter((item) => item.id !== id);
      let nextActive = stateRef.current.activeId;
      if (stateRef.current.activeId === id) {
        nextActive = remaining[index]?.id ?? remaining[index - 1]?.id ?? remaining[0]?.id ?? null;
      }
      stateRef.current.tabs = remaining.map((item) => (item.sourceTabId === id ? { ...item, sourceTabId: null } : item));
      stateRef.current.activeId = nextActive;
      delete scrollRef.current[id];
      delete scopeRef.current[id];
      dirtyRef.current.delete(id);
      bump();
      schedulePersist();
      return nextActive;
    }

    function closeOthers(id: string) {
      const keep = findById(id);
      if (!keep) return;
      stateRef.current.tabs = stateRef.current.tabs.filter(
        (tab) => tab.workspace !== keep.workspace || tab.id === id || tab.pinned || !tab.closable,
      );
      stateRef.current.activeId = id;
      const alive = new Set(stateRef.current.tabs.map((tab) => tab.id));
      for (const key of Object.keys(scrollRef.current)) if (!alive.has(key)) delete scrollRef.current[key];
      for (const key of Object.keys(scopeRef.current)) if (!alive.has(key)) delete scopeRef.current[key];
      for (const dirty of [...dirtyRef.current]) if (!alive.has(dirty)) dirtyRef.current.delete(dirty);
      bump();
      schedulePersist();
    }

    function closeRight(id: string) {
      const tabs = stateRef.current.tabs;
      const target = findById(id);
      if (!target) return;
      const sameWorkspace = tabs.filter((tab) => tab.workspace === target.workspace);
      const index = sameWorkspace.findIndex((tab) => tab.id === id);
      if (index < 0) return;
      const rightIds = new Set(
        sameWorkspace
          .slice(index + 1)
          .filter((tab) => tab.closable && !tab.pinned)
          .map((tab) => tab.id),
      );
      stateRef.current.tabs = tabs.filter((tab) => !rightIds.has(tab.id));
      if (!stateRef.current.tabs.some((tab) => tab.id === stateRef.current.activeId)) {
        stateRef.current.activeId = id;
      }
      const alive = new Set(stateRef.current.tabs.map((tab) => tab.id));
      for (const key of Object.keys(scrollRef.current)) if (!alive.has(key)) delete scrollRef.current[key];
      for (const key of Object.keys(scopeRef.current)) if (!alive.has(key)) delete scopeRef.current[key];
      for (const dirty of [...dirtyRef.current]) if (!alive.has(dirty)) dirtyRef.current.delete(dirty);
      bump();
      schedulePersist();
    }

    function togglePin(id: string) {
      stateRef.current.tabs = stateRef.current.tabs.map((tab) =>
        tab.id === id ? { ...tab, pinned: !tab.pinned } : tab,
      );
      bump();
      schedulePersist();
    }

    function duplicate(id: string): string | null {
      const tab = findById(id);
      if (!tab) return null;
      return openRoute(`${tab.pathname}${tab.search ? `?${tab.search}` : ""}`, { sourceTabId: tab.sourceTabId, forceNew: true });
    }

    function retarget(href: string): string {
      const normalized = normalizeRoute(href);
      const resolved = resolveRoute(normalized);
      const active = findById(stateRef.current.activeId);
      if (!resolved || !active) return resolved ? normalized : href;

      const { pathname, search, entry } = resolved;
      const workspace = entry.workspace ?? "domestic";
      const identity = tabIdentity(pathname, search, entry);

      // 新地址已被同一工作台的另一个 Tab 代表：本 Tab 退场并切回那个 Tab。
      const host = stateRef.current.tabs.find(
        (tab) => tab.id !== active.id && tab.workspace === workspace && tab.identity === identity,
      );
      if (host && active.closable && !active.pinned) {
        stateRef.current.tabs = stateRef.current.tabs
          .filter((tab) => tab.id !== active.id)
          .map((tab) => (tab.id === host.id
            ? { ...tab, lastActiveAt: Date.now(), sourceTabId: tab.sourceTabId === active.id ? null : tab.sourceTabId }
            : tab.sourceTabId === active.id ? { ...tab, sourceTabId: null } : tab));
        stateRef.current.activeId = host.id;
        delete scrollRef.current[active.id];
        delete scopeRef.current[active.id];
        dirtyRef.current.delete(active.id);
        bump();
        schedulePersist();
        return host.search ? `${host.pathname}?${host.search}` : host.pathname;
      }

      stateRef.current.tabs = stateRef.current.tabs.map((tab) =>
        tab.id === active.id
          ? {
              ...tab,
              workspace,
              pathname,
              search,
              title: null,
              baseTitle: tabTitle(entry, search),
              businessType: entry.businessType,
              businessId: tabBusinessId(entry, search),
              identity,
            }
          : tab,
      );
      bump();
      schedulePersist();
      return search ? `${pathname}?${search}` : pathname;
    }

    function updateSearch(id: string, search: string) {
      const tab = findById(id);
      if (!tab || tab.search === search) return;
      const entry = entryOf(tab.pathname);
      const identity = tabIdentity(tab.pathname, search, entry);
      stateRef.current.tabs = stateRef.current.tabs.map((item) =>
        item.id === id
          ? {
              ...item,
              search,
              baseTitle: tabTitle(entry, search),
              businessId: tabBusinessId(entry, search),
              identity,
            }
          : item,
      );
      bump();
      schedulePersist();
    }

    function setTitle(id: string, title: string | null) {
      const tab = findById(id);
      if (!tab) return;
      const next = title?.trim() || null;
      if (tab.title === next) return;
      stateRef.current.tabs = stateRef.current.tabs.map((item) => (item.id === id ? { ...item, title: next } : item));
      bump();
    }

    function setScrollTop(id: string, top: number) {
      scrollRef.current[id] = top;
      schedulePersist();
    }

    function getScrollTop(id: string): number {
      return scrollRef.current[id] ?? 0;
    }

    function setScope(id: string, key: string, value: unknown) {
      const scope = scopeRef.current[id] ?? {};
      scope[key] = value;
      scopeRef.current[id] = scope;
      schedulePersist();
    }

    function getScope<T>(id: string, key: string): T | undefined {
      return scopeRef.current[id]?.[key] as T | undefined;
    }

    return {
      version,
      ready,
      get tabs() {
        return stateRef.current.tabs;
      },
      get activeId() {
        return stateRef.current.activeId;
      },
      get activeTab() {
        return findById(stateRef.current.activeId);
      },
      get activeWorkspace() {
        return findById(stateRef.current.activeId)?.workspace ?? "domestic";
      },
      tabsForWorkspace(workspace: WorkspaceKey) {
        return stateRef.current.tabs.filter((tab) => tab.workspace === workspace);
      },
      maxTabs: MAX_TABS,
      notice,
      setNotice,
      openRoute,
      activate,
      close,
      closeOthers,
      closeRight,
      togglePin,
      duplicate,
      retarget,
      updateSearch,
      setTitle,
      setScrollTop,
      getScrollTop,
      setScope,
      getScope,
      setDirty(id: string, dirty: boolean) {
        if (dirty) dirtyRef.current.add(id);
        else dirtyRef.current.delete(id);
      },
      isDirty(id: string) {
        return dirtyRef.current.has(id);
      },
      anyDirty() {
        return dirtyRef.current.size > 0;
      },
      displayTitle(tab: WorkspaceTab) {
        return tab.title ?? tab.baseTitle;
      },
    };
  }, [bump, notice, ready, schedulePersist, version]);

  return <WorkspaceContext.Provider value={api}>{children}</WorkspaceContext.Provider>;
}

const FALLBACK_ENTRY: RouteEntry = {
  pathname: "",
  title: "未注册页面",
  businessType: "master",
  load: () => import("@/app/page"),
};

function entryOf(pathname: string): RouteEntry {
  return resolveRoute(pathname)?.entry ?? FALLBACK_ENTRY;
}

/* ------------------------------------------------------------------ */
/* 页面侧 hooks：由 Tab 面板提供运行时信息（tabId / 是否激活）          */
/* ------------------------------------------------------------------ */

export type TabRuntime = { tabId: string; active: boolean };

const TabRuntimeContext = createContext<TabRuntime | null>(null);

export function TabRuntimeProvider({ tabId, active, children }: { tabId: string; active: boolean; children: ReactNode }) {
  const value = useMemo(() => ({ tabId, active }), [tabId, active]);
  return <TabRuntimeContext.Provider value={value}>{children}</TabRuntimeContext.Provider>;
}

export function useTabRuntime(): TabRuntime | null {
  return useContext(TabRuntimeContext);
}

/** 页面是否处于激活 Tab（隐藏 Tab 可据此暂停轮询）。 */
export function useTabActive(): boolean {
  return useContext(TabRuntimeContext)?.active ?? true;
}

/** 详情页上报业务标题，如 useTabTitle(order ? `采购单 · ${order.orderNo}` : null)。 */
export function useTabTitle(title: string | null | undefined) {
  const runtime = useTabRuntime();
  const api = useWorkspace();
  const tabId = runtime?.tabId;
  // api 每次变更都会换引用，这里走 ref 让 effect 只在标题/激活 Tab 变化时才跑
  const apiRef = useRef(api);
  apiRef.current = api;
  useEffect(() => {
    if (!tabId) return;
    apiRef.current.setTitle(tabId, title ?? null);
  }, [tabId, title]);
}

/** 注册「当前页面存在未保存内容」，关闭 Tab / 刷新时会被拦截。 */
export function useTabDirty(dirty: boolean) {
  const runtime = useTabRuntime();
  const api = useWorkspace();
  const tabId = runtime?.tabId;
  const apiRef = useRef(api);
  apiRef.current = api;
  useEffect(() => {
    if (!tabId) return;
    apiRef.current.setDirty(tabId, dirty);
    return () => apiRef.current.setDirty(tabId, false);
  }, [tabId, dirty]);
}

/**
 * 与 Tab 绑定的状态：Tab 保活期间用组件 state，刷新后从工作区快照恢复。
 * 列表页的筛选/页码/排序用它，刷新后现场不丢。
 */
export function useTabScopedState<T>(key: string, initial: T | (() => T)) {
  const runtime = useTabRuntime();
  const api = useWorkspace();
  const tabId = runtime?.tabId;
  const apiRef = useRef(api);
  apiRef.current = api;

  const [value, setValue] = useState<T>(() => {
    const saved = tabId ? api.getScope<T>(tabId, key) : undefined;
    if (saved !== undefined) return saved;
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });

  // set 的引用保持稳定（否则放进 effect 依赖里会反复触发）
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const resolved = typeof next === "function" ? (next as (p: T) => T)(prev) : next;
        if (tabId) apiRef.current.setScope(tabId, key, resolved);
        return resolved;
      });
    },
    [key, tabId],
  );

  return [value, set] as const;
}