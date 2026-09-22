"use client";

import { useMemo, type ReactNode } from "react";
import {
  NavigationPromisesContext,
  PathnameContext,
  ReadonlyURLSearchParams,
  SearchParamsContext,
  createDevToolsInstrumentedPromise,
} from "next/dist/shared/lib/hooks-client-context.shared-runtime";

/**
 * 工作区 Tab 的 URL 冻结层（全站唯一 import next/dist 私有模块的地方）。
 *
 * 背景：Next App Router 的 usePathname() / useSearchParams() 读的是全局 context，
 * 隐藏的 Tab 会跟着地址栏一起变（采购工作台这类 query 驱动页面就会错乱）。
 * 这里给每个 Tab 包一层属于它自己的 pathname / search，
 * 让页面永远读到自己打开时的那份 URL（导航变化由工作区显式推进来）。
 *
 * 升级 Next 时只需要检查这一个文件；私有 context 不可用时会在 typecheck/构建阶段暴露。
 */
export function TabUrlProvider({
  pathname,
  search,
  children,
}: {
  /** 该 Tab 自己的路径（不含 query） */
  pathname: string;
  /** 该 Tab 自己的 query（不含 "?"） */
  search: string;
  children: ReactNode;
}) {
  const searchParams = useMemo(() => new URLSearchParams(search), [search]);

  // dev 模式下 Next 会优先读 NavigationPromisesContext（见 next/dist/client/components/navigation.js
  // 里 usePathname/useSearchParams 的 dev-only 分支），这里一并覆盖，保证开发模式与生产行为一致。
  const navigationPromises = useMemo(
    () => ({
      pathname: createDevToolsInstrumentedPromise("pathname", pathname),
      searchParams: createDevToolsInstrumentedPromise("searchParams", new ReadonlyURLSearchParams(searchParams)),
      params: createDevToolsInstrumentedPromise("params", {} as Record<string, string | string[]>),
    }),
    [pathname, searchParams],
  );

  return (
    <SearchParamsContext.Provider value={searchParams}>
      <PathnameContext.Provider value={pathname}>
        <NavigationPromisesContext.Provider value={navigationPromises}>{children}</NavigationPromisesContext.Provider>
      </PathnameContext.Provider>
    </SearchParamsContext.Provider>
  );
}