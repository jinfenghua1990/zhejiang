"use client";

import dynamic from "next/dynamic";
import type { WorkbenchView } from "@/lib/workbench-navigation";

const loading = () => <div className="p-8 text-sm text-slate-400">正在加载功能…</div>;
const modules = {
  dashboard: dynamic(() => import("@/app/page"), { loading }),
  sales: dynamic(() => import("@/app/sales/page"), { loading }),
  products: dynamic(() => import("@/app/products/page"), { loading }),
  inventory_goods: dynamic(() => import("@/app/products/inventory-goods/page"), { loading }),
  inventory_consumables: dynamic(() => import("@/app/products/inventory-consumables/page"), { loading }),
  finance: dynamic(() => import("@/app/finance/page"), { loading }),
  exceptions: dynamic(() => import("@/app/exceptions/page"), { loading }),
  automation: dynamic(() => import("@/app/automation/page"), { loading }),
  settings: dynamic(() => import("@/app/settings/page"), { loading }),
  imports: dynamic(() => import("@/app/data-center-import/page"), { loading }),
  tax: dynamic(() => import("./invoice-reconciliation-view"), { loading }),
};

export function WorkspaceModule({ view }: { view: WorkbenchView }) {
  const Panel = modules[view as keyof typeof modules];
  return Panel ? <div className="mt-5 min-w-0 overflow-x-auto"><Panel /></div> : null;
}
