"use client";

import Link from "next/link";
import ProductionPurchaseBoard from "./production-purchase-board";

export default function ProductionOrdersPage() {
  return (
    <div className="space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-gray-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div>
          <div className="text-xs font-medium text-indigo-600">采购中心 / 生产订单</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">生产订单</h1>
          <p className="mt-1 text-sm text-slate-500">以采购主单为入口自动归档：正品进入生产链路，耗材采购不会混入。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link href="/purchase/workbench?view=orders&kind=goods" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">采购主单</Link>
          <Link href="/supply-chain/production/manual" className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 hover:bg-indigo-100">特殊 / 内部生产</Link>
        </div>
      </header>

      <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-xs leading-5 text-blue-700">
        这里不再要求你重复创建一张生产主单。1688、淘宝等渠道的正品采购会按现有采购状态自动显示在待生产、生产中、在途、到货和完成；历史手工生产执行仍保留在“特殊 / 内部生产”。
      </div>

      <ProductionPurchaseBoard />
    </div>
  );
}
