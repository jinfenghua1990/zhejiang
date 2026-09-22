/**
 * 工作区地址同步：把纯前端状态写进地址栏，但不触发 Next 的 RSC 往返。
 *
 * 背景：静态导出（output: "export"）下 router.push/replace 即便只改查询参数或切回已存在的
 * 页签，也会去拉一次当前页的 RSC payload（浏览器实测单次约 1.4s，而 curl 同一文件只要 0.2s）。
 * 采购工作台点一下筛选、点一张单、点一个页签都会卡一下，就是这里来的。
 *
 * 为什么直接用 history 也安全：Next 自己 patch 了 window.history.pushState / replaceState
 * （见 next/dist/client/components/app-router.js ── "ensure external changes to the history
 * are reflected in the Next.js Router"）。传 data = null 时它会：
 *   1. 用 copyNextJsInternalHistoryState 把内部的 __NA / __PRIVATE_NEXTJS_INTERNALS_TREE 补回去，
 *      所以浏览器前进/后退（popstate 分支要求 event.state.__NA 存在）不会被判成外部站点而整页刷新；
 *   2. dispatch 一次 ACTION_RESTORE，让 usePathname / useSearchParams 拿到新地址，
 *      工作区宿主据此更新/激活页签 —— 但不发任何网络请求。
 *
 * 必须传 null：传 window.history.state 会命中 Next 的「内部导航」分支（data.__NA 为真），
 * 反而完全跳过地址同步，页面读到的还是旧 query。
 */
export function syncWorkspaceUrl(href: string, mode: "push" | "replace" = "replace") {
  if (typeof window === "undefined") return;
  if (mode === "push") window.history.pushState(null, "", href);
  else window.history.replaceState(null, "", href);
}