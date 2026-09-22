"use client";

import {
  type AnchorHTMLAttributes,
  type MouseEvent,
} from "react";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

export type WorkspaceLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & {
  href: string;
  /** 页内状态切换可用 replace；业务页面跳转默认 push。 */
  replace?: boolean;
  /** 兼容原 next/link 调用点；工作区链接本身不做 RSC prefetch。 */
  prefetch?: boolean;
};

function isInternalWorkspaceHref(href: string): boolean {
  return href.startsWith("/") && !href.startsWith("//");
}

/**
 * 工作区内部链接。
 *
 * 普通左键只同步地址栏，由 WorkspaceHost 打开/激活保活 Tab，避免 Next App Router
 * 为静态导出页面额外请求 RSC payload。修饰键、新窗口、下载与外部链接保持浏览器原生行为。
 */
export default function WorkspaceLink({
  href,
  replace = false,
  prefetch: _prefetch,
  target,
  download,
  onClick,
  ...props
}: WorkspaceLinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented) return;

    const modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
    const opensElsewhere = Boolean(target && target !== "_self");
    if (
      event.button !== 0 ||
      modified ||
      opensElsewhere ||
      download !== undefined ||
      !isInternalWorkspaceHref(href)
    ) {
      return;
    }

    event.preventDefault();
    syncWorkspaceUrl(href, replace ? "replace" : "push");
  }

  return (
    <a
      {...props}
      href={href}
      target={target}
      download={download}
      onClick={handleClick}
    />
  );
}
