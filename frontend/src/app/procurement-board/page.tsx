"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 采购域整合：看板独有数据（付款率环 / 付款分层）已并入 /purchase/workbench。 */
export default function ProcurementBoardLegacyRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/purchase/workbench?view=orders"); }, [router]);
  return null;
}