"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 采购域整合：采购台账已并入 /purchase/workbench 的「链路建链」视图。 */
export default function ProcurementLedgerLegacyRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/purchase/workbench?view=chain"); }, [router]);
  return null;
}