"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/**
 * 历史采购链详情地址兼容入口。
 *
 * 采购链详情已经并入统一采购工作台，旧页面不再渲染第二套侧栏、页头和
 * 流程卡片；保留这个路由只负责把旧深链带到统一订单详情中。
 */
export default function ProcurementChainDetailRedirect() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const params = new URLSearchParams({ view: "orders" });
    const orderId = searchParams.get("id") || searchParams.get("order");
    if (orderId) params.set("order", orderId);
    router.replace(`/purchase/workbench?${params.toString()}`);
  }, [router, searchParams]);

  return <div className="flex min-h-[240px] items-center justify-center text-sm text-slate-400">正在打开统一采购工作台…</div>;
}
