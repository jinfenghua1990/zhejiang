"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 旧地址兼容：回款与对账已从前台移除，财务工作统一进入月度资料。 */
export default function PaymentsPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/finance/monthly-send");
  }, [router]);

  return <main className="p-6 text-sm text-slate-500">正在打开财务资料…</main>;
}
