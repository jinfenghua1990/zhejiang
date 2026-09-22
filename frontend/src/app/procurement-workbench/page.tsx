"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 采购域整合：原 /procurement-workbench 与 /purchase/workbench 重复，已合并。 */
export default function ProcurementWorkbenchLegacyRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/purchase/workbench?view=orders"); }, [router]);
  return null;
}