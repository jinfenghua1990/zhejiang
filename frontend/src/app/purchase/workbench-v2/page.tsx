"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * 历史采购工作台 V2 已停止独立维护。
 * 采购域统一以 /purchase/workbench 为唯一主入口，避免两套 UI 和业务逻辑继续分叉。
 */
export default function PurchaseWorkbenchV2LegacyRedirect() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/purchase/workbench?view=orders");
  }, [router]);

  return null;
}
