"use client";

import { useEffect } from "react";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

/** 旧地址兼容：外贸财务已统一使用共享财务底座，通过 scope 保留外贸工作台归属。 */
export default function LegacyForeignTradeFinancePage() {
  useEffect(() => {
    syncWorkspaceUrl("/finance?scope=foreign_trade", "replace");
  }, []);
  return null;
}
