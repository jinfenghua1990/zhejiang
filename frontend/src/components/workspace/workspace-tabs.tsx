"use client";

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useWorkspace, type WorkspaceTab } from "@/lib/workspace/tab-store";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

/** 工作区 Tabs：主菜单管业务模块，这里管「同时打开的业务对象」。 */

function hrefOf(tab: WorkspaceTab): string {
  return tab.search ? `${tab.pathname}?${tab.search}` : tab.pathname;
}

/**
 * 读「此刻」地址栏的地址。
 * 不能拿 usePathname/useSearchParams 的当前值来比：history 改动后 React 要下一帧才重渲染，
 * 快速连点两个页签时第二次比对会拿旧值判定「没变」而漏同步地址。
 */
function liveHref(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}

export default function WorkspaceTabBar() {
  const ws = useWorkspace();

  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [allOpen, setAllOpen] = useState(false);
  const [allKeyword, setAllKeyword] = useState("");
  const [confirmClose, setConfirmClose] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 内销 / 外贸各自保留独立标签集；切换工作台只切可见组，不销毁另一边现场。
  const tabList = ws.tabsForWorkspace(ws.activeWorkspace);
  const ordered = useMemo(
    () => [...tabList.filter((tab) => tab.pinned), ...tabList.filter((tab) => !tab.pinned)],
    [tabList, ws.version],
  );

  const activeTab = ws.activeTab;
  const sourceTab = activeTab?.sourceTabId ? tabList.find((tab) => tab.id === activeTab.sourceTabId) ?? null : null;
  const confirmTab = confirmClose ? tabList.find((tab) => tab.id === confirmClose) ?? null : null;
  const menuTab = menu ? tabList.find((tab) => tab.id === menu.id) ?? null : null;

  // 点击外部关闭右键菜单 / 全部页面面板
  useEffect(() => {
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setMenu(null);
        setAllOpen(false);
      }
    }
    window.addEventListener("mousedown", onPointerDown);
    return () => window.removeEventListener("mousedown", onPointerDown);
  }, []);

  // 激活的 Tab 始终滚动到可见区域
  useEffect(() => {
    if (!ws.activeId) return;
    const node = barRef.current?.querySelector(`[data-tab-id="${ws.activeId}"]`);
    node?.scrollIntoView({ inline: "nearest", block: "nearest" });
  }, [ws.activeId]);

  // 上限等提示自动消失
  const notice = ws.notice;
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => ws.setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice, ws]);

  /** 地址栏始终等于激活 Tab 的 URL（这样刷新、复制链接、新窗口打开都对得上） */
  function syncUrlToActive() {
    const tab = ws.activeTab;
    const href = tab ? hrefOf(tab) : "/";
    if (href !== liveHref()) syncWorkspaceUrl(href);
  }

  function activate(tab: WorkspaceTab) {
    setAllOpen(false);
    setMenu(null);
    if (tab.id !== ws.activeId) ws.activate(tab.id);
    const href = hrefOf(tab);
    if (href !== liveHref()) syncWorkspaceUrl(href);
  }

  function closeNow(id: string) {
    ws.close(id);
    setConfirmClose(null);
    setMenu(null);
    syncUrlToActive();
  }

  function requestClose(tab: WorkspaceTab) {
    if (!tab.closable || tab.pinned) return;
    setMenu(null);
    if (ws.isDirty(tab.id)) {
      setConfirmClose(tab.id);
      return;
    }
    closeNow(tab.id);
  }

  function closeOthers(id: string) {
    ws.closeOthers(id);
    setMenu(null);
    syncUrlToActive();
  }

  function closeRight(id: string) {
    ws.closeRight(id);
    setMenu(null);
    syncUrlToActive();
  }

  const filterKeyword = allKeyword.trim().toLowerCase();
  const allTabs = ordered.filter((tab) => !filterKeyword || ws.displayTitle(tab).toLowerCase().includes(filterKeyword));

  return (
    <div ref={rootRef} data-workspace-tabbar className="relative z-20 shrink-0 border-b border-slate-200 bg-slate-50">
      <div className="flex h-[38px] items-center gap-1.5 px-2">
        <button
          type="button"
          disabled={!sourceTab}
          onClick={() => sourceTab && activate(sourceTab)}
          title={sourceTab ? `返回 ${ws.displayTitle(sourceTab)}` : "当前页面没有来源页面"}
          className="flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[12px] font-medium text-slate-600 transition hover:bg-white disabled:cursor-default disabled:text-slate-300 disabled:hover:bg-transparent"
        >
          <span aria-hidden="true">←</span>返回
        </button>

        <div
          ref={barRef}
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {ordered.map((tab) => {
            const active = tab.id === ws.activeId;
            return (
              <div
                key={tab.id}
                data-tab-id={tab.id}
                role="tab"
                aria-selected={active}
                onClick={() => activate(tab)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  setMenu({ id: tab.id, x: event.clientX, y: event.clientY });
                }}
                title={`${ws.displayTitle(tab)} · ${hrefOf(tab)}`}
                className={`group flex h-[28px] max-w-[220px] shrink-0 cursor-pointer items-center gap-1.5 rounded-md border px-2 text-[12px] transition ${
                  active
                    ? "border-blue-200 bg-white font-medium text-blue-700 shadow-[0_1px_3px_rgba(15,39,70,0.06)]"
                    : "border-transparent bg-white/60 text-slate-600 hover:border-slate-200 hover:bg-white"
                }`}
              >
                {tab.pinned && (
                  <svg viewBox="0 0 12 12" className="h-3 w-3 shrink-0 text-amber-500" aria-hidden="true">
                    <path d="M7.5 1.5 10.5 4.5 8.5 5 6.5 7l.5 2.5-4-4L5.5 4l2-2.5Z" fill="currentColor" />
                  </svg>
                )}
                <span className="min-w-0 truncate">{ws.displayTitle(tab)}</span>
                {tab.closable && !tab.pinned && (
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      requestClose(tab);
                    }}
                    aria-label="关闭标签"
                    className="shrink-0 rounded p-0.5 text-slate-400 opacity-60 transition hover:bg-slate-100 hover:text-slate-700 group-hover:opacity-100"
                  >
                    <svg viewBox="0 0 12 12" className="h-2.5 w-2.5" aria-hidden="true">
                      <path d="m3 3 6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                    </svg>
                  </button>
                )}
              </div>
            );
          })}
        </div>

        <button
          type="button"
          onClick={() => {
            setAllOpen((open) => !open);
            setMenu(null);
          }}
          className="flex shrink-0 items-center gap-1 rounded-md border border-slate-200 bg-white px-2 py-1 text-[12px] font-medium text-slate-600 transition hover:bg-slate-50"
        >
          {ws.activeWorkspace === "foreign" ? "外贸页面" : "内销页面"}
          <span className="tabular-nums text-slate-400">{tabList.length}</span>
          <svg viewBox="0 0 16 16" className="h-3 w-3 text-slate-400" aria-hidden="true">
            <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
        </button>
      </div>

      {notice && <div className="border-t border-amber-100 bg-amber-50 px-3 py-1.5 text-[12px] text-amber-700">{notice}</div>}

      {menu && menuTab && (
        <TabContextMenu
          tab={menuTab}
          x={menu.x}
          y={menu.y}
          canCloseOthers={ordered.some((tab) => tab.id !== menuTab.id && tab.closable && !tab.pinned)}
          canCloseRight={ordered.indexOf(menuTab) < ordered.length - 1}
          onClose={() => requestClose(menuTab)}
          onCloseOthers={() => closeOthers(menuTab.id)}
          onCloseRight={() => closeRight(menuTab.id)}
          onTogglePin={() => {
            ws.togglePin(menuTab.id);
            setMenu(null);
          }}
          onDuplicate={() => {
            ws.duplicate(menuTab.id);
            setMenu(null);
            syncUrlToActive();
          }}
        />
      )}

      {allOpen && (
        <div className="absolute right-2 top-full z-dropdown mt-1 w-[320px] rounded-xl border border-slate-200 bg-white p-2 shadow-lg">
          <input
            autoFocus
            value={allKeyword}
            onChange={(event) => setAllKeyword(event.target.value)}
            placeholder={ws.activeWorkspace === "foreign" ? "搜索外贸已打开页面…" : "搜索内销已打开页面…"}
            className="mb-1.5 w-full rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[12px] text-slate-700 outline-none focus:border-blue-400 focus:bg-white"
          />
          <div className="max-h-[320px] overflow-y-auto">
            {allTabs.length === 0 && <div className="px-2 py-4 text-center text-[12px] text-slate-400">没有匹配的页面</div>}
            {allTabs.map((tab) => (
              <div
                key={tab.id}
                className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] ${
                  tab.id === ws.activeId ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                <button type="button" onClick={() => activate(tab)} className="min-w-0 flex-1 truncate text-left">
                  {tab.pinned && <span className="mr-1 text-amber-500">●</span>}
                  {ws.displayTitle(tab)}
                </button>
                {tab.closable && !tab.pinned && (
                  <button
                    type="button"
                    onClick={() => requestClose(tab)}
                    aria-label="关闭"
                    className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {confirmTab && (
        <UnsavedDialog tab={confirmTab} onCancel={() => setConfirmClose(null)} onDiscard={() => closeNow(confirmTab.id)} />
      )}
    </div>
  );
}

function TabContextMenu({
  tab,
  x,
  y,
  canCloseOthers,
  canCloseRight,
  onClose,
  onCloseOthers,
  onCloseRight,
  onTogglePin,
  onDuplicate,
}: {
  tab: WorkspaceTab;
  x: number;
  y: number;
  canCloseOthers: boolean;
  canCloseRight: boolean;
  onClose: () => void;
  onCloseOthers: () => void;
  onCloseRight: () => void;
  onTogglePin: () => void;
  onDuplicate: () => void;
}) {
  const href = hrefOf(tab);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ left: x, top: y });
  const items: { label: string; onSelect: () => void; disabled?: boolean; separatorBefore?: boolean }[] = [
    { label: "关闭当前", onSelect: onClose, disabled: !tab.closable || tab.pinned },
    { label: "关闭其他", onSelect: onCloseOthers, disabled: !canCloseOthers },
    { label: "关闭右侧", onSelect: onCloseRight, disabled: !canCloseRight },
    { label: tab.pinned ? "取消固定" : "固定标签", onSelect: onTogglePin, disabled: !tab.closable, separatorBefore: true },
    { label: "复制标签页", onSelect: onDuplicate },
    { label: "在新窗口打开", onSelect: () => window.open(href, "_blank", "noopener") },
  ];
  const visibleItems = items.filter((item) => !item.disabled);

  useLayoutEffect(() => {
    const node = menuRef.current;
    if (!node) return;
    const gutter = 8;
    const rect = node.getBoundingClientRect();
    setPosition({
      left: Math.max(gutter, Math.min(x, window.innerWidth - rect.width - gutter)),
      top: Math.max(gutter, Math.min(y, window.innerHeight - rect.height - gutter)),
    });
  }, [x, y]);

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label={`${tab.title ?? tab.baseTitle} 标签页操作`}
      className="fixed z-dropdown max-h-[calc(100vh-16px)] w-[180px] overflow-y-auto rounded-xl border border-slate-200 bg-white p-1.5 shadow-lg"
      style={{ left: position.left, top: position.top }}
    >
      {visibleItems.map((item, index) => (
        <Fragment key={item.label}>
          {item.separatorBefore && index > 0 && <div className="my-1 border-t border-slate-100" aria-hidden="true" />}
          <button
            type="button"
            disabled={item.disabled}
            onClick={item.onSelect}
            role="menuitem"
            className="block w-full rounded-lg px-2.5 py-1.5 text-left text-[12px] text-slate-600 transition hover:bg-slate-50"
          >
            {item.label}
          </button>
        </Fragment>
      ))}
    </div>
  );
}

function UnsavedDialog({ tab, onCancel, onDiscard }: { tab: WorkspaceTab; onCancel: () => void; onDiscard: () => void }) {
  const ws = useWorkspace();
  return (
    <div className="fixed inset-0 z-modal flex items-center justify-center bg-slate-900/30 p-4">
      <div className="w-[360px] rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
        <div className="text-sm font-semibold text-slate-800">当前页面存在未保存内容</div>
        <p className="mt-2 text-[12px] leading-5 text-slate-500">
          「{ws.displayTitle(tab)}」里还有没提交的内容，关闭后会丢失。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-medium text-slate-600 hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onDiscard}
            className="rounded-lg bg-rose-600 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-rose-700"
          >
            放弃修改并关闭
          </button>
        </div>
      </div>
    </div>
  );
}
