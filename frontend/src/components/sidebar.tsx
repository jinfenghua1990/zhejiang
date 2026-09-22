"use client";

import Link from "@/components/workspace/workspace-link";
import { usePathname, useSearchParams } from "next/navigation";
import { isSecondaryActive, resolveModule, type IconName, type SecondaryItem } from "@/lib/navigation";

/**
 * 左侧二级导航：跟随顶部一级模块动态变化（配置见 lib/navigation.ts）。
 * 品牌与账号入口已上移到全局顶栏（top-bar.tsx），这里只承担当前模块内的功能切换。
 */
export default function Sidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const module = resolveModule(pathname, searchParams);
  const workspaceLabel = module.workspace === "foreign" ? "外贸工作台" : "内销工作台";
  const groups = module.groups ?? [{ label: "", items: module.items ?? [] }];

  return (
    <aside className="app-sidebar flex h-full w-[208px] shrink-0 flex-col overflow-hidden text-white shadow-[8px_0_28px_rgba(15,39,70,0.08)]">
      <div className="border-b border-white/10 px-4 pb-4 pt-5">
        <div className="truncate text-[17px] font-semibold tracking-wide text-white">{module.title}</div>
        <div className="mt-1 text-[10px] font-medium tracking-wide text-slate-400">电商经营数据平台 · {workspaceLabel}</div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,.18)_transparent]">
        <div className="space-y-5">
          {groups.map((group) => (
            <div key={group.label || "default"}>
              {group.label && <div className="mb-2 px-3 text-[11px] font-medium tracking-wide text-slate-400">{group.label}</div>}
              <div className="space-y-1">
                {group.items.map((item) => <SidebarItem key={item.href} item={item} pathname={pathname} searchParams={searchParams} />)}
              </div>
            </div>
          ))}
        </div>
      </nav>
    </aside>
  );
}

function SidebarItem({ item, pathname, searchParams }: { item: SecondaryItem; pathname: string; searchParams: URLSearchParams }) {
  const active = isSecondaryActive(item, pathname, searchParams);
  return (
    <Link
      href={item.href}
      prefetch={false}
      aria-current={active ? "page" : undefined}
      className={`group flex items-center gap-3 rounded-lg px-3 py-2.5 text-[13px] transition-all ${
        active
          ? "app-sidebar-active font-medium text-white shadow-[0_6px_18px_rgba(37,116,232,.28)]"
          : "text-slate-200 hover:bg-white/8 hover:text-white"
      }`}
    >
      <span className={active ? "text-white" : "text-slate-400 group-hover:text-slate-200"}>
        <NavIcon name={item.icon} />
      </span>
      <span className="min-w-0 flex-1 truncate">{item.label}</span>
    </Link>
  );
}

function NavIcon({ name }: { name: IconName }) {
  const common = "h-[18px] w-[18px] shrink-0";

  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3.5 10.5 12 3.7l8.5 6.8v9.2a1 1 0 0 1-1 1h-5v-6h-5v6h-5a1 1 0 0 1-1-1v-9.2Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "sales" || name === "profit") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M4 19V9m5 10V5m5 14v-7m5 7V3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "box" || name === "inventory") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="m4.5 7.2 7.5-4 7.5 4v9.6l-7.5 4-7.5-4V7.2Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="m4.8 7.4 7.2 4 7.2-4M12 11.4v9" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "truck") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3 6h11v10H3V6Zm11 4h4l3 3v3h-7v-6Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <circle cx="7" cy="18" r="1.8" stroke="currentColor" strokeWidth="1.7" /><circle cx="18" cy="18" r="1.8" stroke="currentColor" strokeWidth="1.7" />
      </svg>
    );
  }

  if (name === "warehouse" || name === "factory") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3.5 20V8l8.5-4 8.5 4v12M7 20v-7h10v7M9 9h.01M12 9h.01M15 9h.01" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "cart") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3 5h2l2 10h10.5l2-7H6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="9" cy="19" r="1.5" fill="currentColor" /><circle cx="17" cy="19" r="1.5" fill="currentColor" />
      </svg>
    );
  }

  if (name === "wallet") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H18a2 2 0 0 1 2 2v12H6a2 2 0 0 1-2-2V6.5Zm11 4.5h6v4h-6a2 2 0 1 1 0-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "finance" || name === "tax") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M6 3.5h8l4 4V20H6V3.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M14 3.5V8h4M9 12h6M9 15.5h6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "mail") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M4 14V6a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v8M4 14a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2M4 14v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="m4 7 7 4 7-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "alert") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M12 3.8 21 20H3L12 3.8Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M12 9v5m0 3h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "settings") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
        <path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "automation" || name === "flow") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M19 7a8 8 0 0 0-13-1L4 8m1-1H4V4M5 17a8 8 0 0 0 13 1l2-2m-1 1h1v3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "receive" || name === "import") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M12 3v11m0 0 4-4m-4 4-4-4M4 17v3h16v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
      <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="m4.5 7.2 7.5 4 7.5-4M12 11.2V21" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}
