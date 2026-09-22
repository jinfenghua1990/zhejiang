"use client";

import { useEffect } from "react";
import { syncWorkspaceUrl } from "@/lib/workspace/url-sync";

/** 旧地址兼容：吉客云入库导入已收进“到仓入库单”页面弹窗，不再维护第二套页面。 */
export default function LegacyJackyunReceivingPage() {
  useEffect(() => {
    syncWorkspaceUrl("/supply-chain/receiving?panel=jackyun", "replace");
  }, []);
  return null;
}
