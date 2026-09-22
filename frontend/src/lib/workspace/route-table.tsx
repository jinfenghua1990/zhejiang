"use client";

import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { WORKBENCH_VIEWS, parseWorkbenchView, workbenchHref } from "@/lib/workbench-navigation";
import { isForeignTradeFinanceRoute } from "@/lib/workspace-scope";

/**
 * 工作区路由注册表：pathname → 页面组件 / Tab 标题 / Tab 身份。
 *
 * 工作区（components/workspace/workspace-host.tsx）接管页面渲染后，
 * 页面内容由这里挂载；Next 的路由只负责把地址栏同步成「激活 Tab 的 URL」。
 * 新增业务页面时：这里加一条注册 + lib/navigation.ts 加菜单，即自动接入工作区 Tabs。
 */
export type WorkspaceKey = "domestic" | "foreign";

export type RouteEntry = {
  pathname: string;
  /** 固定工作台归属；未填写默认内销。 */
  workspace?: WorkspaceKey;
  /** 同一页面按 query 进入不同工作台时使用；优先级高于 workspace。 */
  workspaceFor?: (search: URLSearchParams) => WorkspaceKey;
  /** 默认 Tab 标题（列表/母页面） */
  title: string;
  /** 业务类型：master 母页面 / 业务对象详情页 */
  businessType: string;
  /** 参与 Tab 身份的业务参数：同 pathname 下这些参数不同 = 不同业务对象 = 不同 Tab */
  identityKeys?: string[];
  /** 有业务参数时更精确的标题（返回 null 用默认标题） */
  titleFor?: (search: URLSearchParams) => string | null;
  /** 默认固定（首页/工作台） */
  pinned?: boolean;
  /** 是否允许关闭（首页不可关） */
  closable?: boolean;
  load: () => Promise<{ default: ComponentType }>;
};

const ROUTES: RouteEntry[] = [
  {
    pathname: "/",
    title: "经营总览",
    businessType: "master",
    pinned: true,
    closable: false,
    load: () => import("@/app/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade",
    title: "外贸总览",
    businessType: "master",
    pinned: true,
    closable: false,
    load: () => import("@/app/foreign-trade/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/orders",
    title: "外贸订单",
    businessType: "master",
    load: () => import("@/app/foreign-trade/orders/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/dealers",
    title: "B2B 客户",
    businessType: "dealer",
    load: () => import("@/app/foreign-trade/dealers/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/channels",
    title: "渠道管理",
    businessType: "master",
    load: () => import("@/app/foreign-trade/channels/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/sku-mappings",
    title: "海外 SKU 映射",
    businessType: "master",
    load: () => import("@/app/foreign-trade/sku-mappings/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/fulfillment",
    title: "履约中心",
    businessType: "master",
    load: () => import("@/app/foreign-trade/fulfillment/page"),
  },
  {
    workspace: "foreign",
    pathname: "/foreign-trade/alsvid",
    title: "Alsvid",
    businessType: "master",
    load: () => import("@/app/foreign-trade/alsvid/page"),
  },
  {
    pathname: "/sales",
    title: "销售",
    businessType: "master",
    titleFor: (search) => (search.get("tab") === "detail" ? "销售明细" : null),
    load: () => import("@/app/sales/page"),
  },
  {
    pathname: "/products",
    title: "货品档案",
    businessType: "master",
    titleFor: (search) => {
      if (search.get("kind") === "consumable") return "耗材档案";
      const tab = search.get("tab") ?? search.get("productTab");
      if (tab === "bundles") return "套装档案";
      if (tab === "taxRules" || tab === "tax-rules") return "财务分类";
      return null;
    },
    load: () => import("@/app/products/page"),
  },
  {
    pathname: "/inventory",
    title: "库存总览",
    businessType: "master",
    titleFor: (search) => {
      const tab = search.get("tab");
      if (tab === "consumables") return "耗材库存";
      if (tab === "goods") return "正品库存";
      if (tab === "bundle") return "虚拟组合库存";
      return null;
    },
    load: () => import("@/app/inventory/page"),
  },
  {
    pathname: "/inventory/transactions",
    title: "库存流水",
    businessType: "master",
    load: () => import("@/app/inventory/transactions/page"),
  },
  {
    pathname: "/inventory/adjustments",
    title: "库存盘点",
    businessType: "master",
    load: () => import("@/app/inventory/adjustments/page"),
  },
  {
    pathname: "/inventory/operations",
    title: "耗材作业",
    businessType: "master",
    load: () => import("@/app/inventory/operations/page"),
  },
  {
    pathname: "/supply-chain/warehouses",
    title: "仓库档案",
    businessType: "master",
    load: () => import("@/app/supply-chain/warehouses/page"),
  },
  {
    pathname: "/suppliers",
    title: "供应商档案",
    businessType: "supplier",
    load: () => import("@/app/suppliers/page"),
  },
  {
    pathname: "/finance/partners",
    title: "往来单位档案",
    businessType: "master",
    load: () => import("@/app/finance/partners/page"),
  },
  {
    pathname: "/purchase/workbench",
    title: "采购订单",
    businessType: "purchase",
    // 采购单号是业务对象：同一张采购单只保留一个 Tab，工作台母页面另外保留一个
    identityKeys: ["order"],
    titleFor: (search) => {
      const order = search.get("order");
      if (order) return `采购单 · ${order}`;
      const view = search.get("view");
      return view ? WORKBENCH_VIEWS[parseWorkbenchView(view)] : null;
    },
    load: () => import("@/app/purchase/workbench/page"),
  },
  {
    pathname: "/supply-chain/production",
    title: "生产订单",
    businessType: "master",
    load: () => import("@/app/supply-chain/production/page"),
  },
  {
    pathname: "/supply-chain/production/manual",
    title: "特殊 / 内部生产",
    businessType: "master",
    load: () => import("@/app/supply-chain/production/manual/page"),
  },
  {
    pathname: "/supply-chain/receiving",
    title: "到仓入库",
    businessType: "master",
    titleFor: (search) => (search.get("panel") === "jackyun" ? "吉客云入库导入" : null),
    load: () => import("@/app/supply-chain/receiving/page"),
  },
  {
    pathname: "/supply-chain/receiving/manual",
    title: "手工入库单",
    businessType: "master",
    load: () => import("@/app/supply-chain/receiving/manual/page"),
  },
  {
    pathname: "/supply-chain/material-flow",
    title: "耗材流转",
    businessType: "master",
    load: () => import("@/app/supply-chain/material-flow/page"),
  },
  {
    pathname: "/data-center-import",
    title: "数据中台",
    businessType: "master",
    titleFor: (search) => {
      const titles: Record<string, string> = {
        alibaba1688: "1688 订单接入",
        external_orders: "其他渠道采购订单",
        jackyun: "吉客云业务单据",
        tax: "税务发票清单",
      };
      return titles[search.get("tab") ?? ""] ?? null;
    },
    load: () => import("@/app/data-center-import/page"),
  },
  {
    pathname: "/exceptions",
    title: "异常中心",
    businessType: "master",
    load: () => import("@/app/exceptions/page"),
  },
  {
    pathname: "/automation",
    title: "自动化",
    businessType: "master",
    load: () => import("@/app/automation/page"),
  },
  {
    pathname: "/finance",
    title: "财务中心",
    businessType: "master",
    workspaceFor: (search) => isForeignTradeFinanceRoute("/finance", search) ? "foreign" : "domestic",
    titleFor: (search) => isForeignTradeFinanceRoute("/finance", search) ? "外贸财务" : null,
    load: () => import("@/app/finance/page"),
  },
  {
    pathname: "/finance/product-categories",
    title: "财务分类",
    businessType: "master",
    load: () => import("@/app/finance/product-categories/page"),
  },
  {
    pathname: "/finance/monthly-send",
    title: "月度资料",
    businessType: "master",
    load: () => import("@/app/finance/monthly-send/page"),
  },
  {
    pathname: "/finance/bank-transactions",
    title: "银行",
    businessType: "master",
    load: () => import("@/app/finance/bank-transactions/page"),
  },
  {
    pathname: "/finance/invoices",
    title: "发票管理",
    businessType: "master",
    load: () => import("@/app/finance/invoices/page"),
  },
  {
    pathname: "/finance/tax-accounting",
    title: "税务数据",
    businessType: "master",
    load: () => import("@/app/finance/tax-accounting/page"),
  },
  {
    pathname: "/finance/opening",
    title: "期初数据",
    businessType: "master",
    load: () => import("@/app/finance/opening/page"),
  },
  {
    pathname: "/logistics/workbench",
    title: "物流工作台",
    businessType: "master",
    load: () => import("@/app/logistics/workbench/page"),
  },
  {
    pathname: "/logistics/bills",
    title: "物流对账",
    businessType: "master",
    load: () => import("@/app/logistics/bills/page"),
  },
  {
    pathname: "/settings/backup",
    title: "备份与容灾",
    businessType: "master",
    load: () => import("@/app/settings/backup/page"),
  },
  {
    pathname: "/settings/update",
    title: "系统更新",
    businessType: "master",
    load: () => import("@/app/settings/update/page"),
  },
];

const ENTRY_BY_PATH = new Map(ROUTES.map((entry) => [entry.pathname, entry]));

/**
 * 页内重定向表：这些地址只有兼容意义，直接归一化到正式页面，避免开出一个「正在跳转」的空 Tab。
 * 采购域旧地址由 lib/workbench-navigation.ts 的 workbenchHref() 统一处理。
 */
const IN_PAGE_REDIRECTS: Record<string, string> = {
  "/settings": "/settings/backup",
  "/supply-chain": "/purchase/workbench?view=orders",
  "/foreign-trade/finance": "/finance?scope=foreign_trade",
  "/supply-chain/receiving/jackyun": "/supply-chain/receiving?panel=jackyun",
  // 这两个旧导入地址必须直连数据中心：经采购工作台的 imports 视图会先开一个「数据接入」Tab
  // 再被工作台自己重定向走，留下一个用户没要过的残留 Tab。
  "/alibaba1688-import": "/data-center-import?tab=alibaba1688",
  "/jackyun-import": "/data-center-import?tab=jackyun",
  "/payments": "/finance/monthly-send",
  "/profit": "/finance/monthly-send",
  "/tax-invoices": "/finance/invoices",
  "/finance/bank-summary": "/finance/bank-transactions?view=summary",
  "/settings/warehouses": "/supply-chain/warehouses",
  "/finance/tax-accounting/categories": "/finance/product-categories?productTab=tax-rules",
  "/products/inventory-goods": "/inventory?tab=goods",
  "/products/inventory-consumables": "/inventory?tab=consumables",
};

/** 把地址栏地址归一化成工作区可识别的正式地址（旧地址 → 新地址）。 */
export function normalizeRoute(href: string): string {
  // 先看原始地址：有些旧地址同时出现在 workbenchHref 的兼容表里，直连比绕一层更干净
  const [rawPathname] = href.split("?", 2);
  const direct = IN_PAGE_REDIRECTS[rawPathname];
  if (direct) return direct;

  const legacy = workbenchHref(href);
  const [pathname, search = ""] = legacy.split("?", 2);
  const target = IN_PAGE_REDIRECTS[pathname];
  if (target) return target;
  return search ? `${pathname}?${search}` : pathname;
}

export type ResolvedRoute = { pathname: string; search: string; entry: RouteEntry; workspace: WorkspaceKey };

export function routeWorkspace(pathname: string, search = ""): WorkspaceKey {
  const entry = ENTRY_BY_PATH.get(pathname);
  if (!entry) return "domestic";
  return entry.workspaceFor?.(new URLSearchParams(search)) ?? entry.workspace ?? "domestic";
}

/** 解析地址：返回注册表条目与按 query 推导后的工作台归属；未注册地址返回 null。 */
export function resolveRoute(href: string): ResolvedRoute | null {
  const normalized = normalizeRoute(href);
  const [pathname, search = ""] = normalized.split("?", 2);
  const entry = ENTRY_BY_PATH.get(pathname);
  if (!entry) return null;
  return { pathname, search, entry, workspace: routeWorkspace(pathname, search) };
}

/** Tab 标题：优先用业务参数推导（采购单 · CG001），否则用默认标题。 */
export function tabTitle(entry: RouteEntry, search: string): string {
  return entry.titleFor?.(new URLSearchParams(search))?.trim() || entry.title;
}

/** Tab 身份：pathname + 业务参数，用于去重（同一业务对象只保留一个 Tab）。 */
export function tabIdentity(pathname: string, search: string, entry: RouteEntry): string {
  const keys = entry.identityKeys ?? [];
  if (keys.length === 0) return pathname;
  const params = new URLSearchParams(search);
  return `${pathname}?${keys.map((key) => `${key}=${params.get(key) ?? ""}`).join("&")}`;
}

/** 业务对象标识（如采购单号），没有则为 null。 */
export function tabBusinessId(entry: RouteEntry, search: string): string | null {
  const keys = entry.identityKeys ?? [];
  const params = new URLSearchParams(search);
  for (const key of keys) {
    const value = params.get(key);
    if (value) return value;
  }
  return null;
}

/** 懒加载组件按 pathname 缓存，保证同一个页面在不同 Tab 里是同一个组件类型（不会互相重挂载）。 */
const LAZY_CACHE = new Map<string, LazyExoticComponent<ComponentType>>();

export function routeComponent(pathname: string): LazyExoticComponent<ComponentType> | null {
  const entry = ENTRY_BY_PATH.get(pathname);
  if (!entry) return null;
  let cached = LAZY_CACHE.get(pathname);
  if (!cached) {
    cached = lazy(entry.load);
    LAZY_CACHE.set(pathname, cached);
  }
  return cached;
}
