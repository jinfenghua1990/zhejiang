"use client";

import Link from "next/link";
import ProductionPanel from "../production-panel";

export default function ManualProductionPage() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        <span>这里仅用于没有采购主单来源的特殊 / 内部生产，以及历史手工生产执行记录。</span>
        <Link href="/purchase/workbench?kind=goods" className="rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium hover:bg-amber-100">返回采购工作台</Link>
      </div>
      <ProductionPanel />
    </div>
  );
}
