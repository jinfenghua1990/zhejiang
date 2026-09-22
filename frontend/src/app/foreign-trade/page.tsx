"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type StatusTone = "ready" | "planning" | "shared";

const TONE: Record<StatusTone, string> = {
  ready: "border-emerald-200 bg-emerald-50 text-emerald-700",
  planning: "border-amber-200 bg-amber-50 text-amber-700",
  shared: "border-blue-200 bg-blue-50 text-blue-700",
};

function Status({ children, tone }: { children: React.ReactNode; tone: StatusTone }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${TONE[tone]}`}>{children}</span>;
}

function Arrow() {
  return (
    <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4" aria-hidden="true">
      <path d="M5 10h10m-4-4 4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ChannelCard({
  title,
  eyebrow,
  description,
  details,
  status,
}: {
  title: string;
  eyebrow: string;
  description: string;
  details: string[];
  status: React.ReactNode;
}) {
  return (
    <article className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[11px] font-medium uppercase tracking-[0.12em] text-slate-400">{eyebrow}</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-900">{title}</h2>
        </div>
        {status}
      </div>
      <p className="mt-3 text-sm leading-6 text-slate-500">{description}</p>
      <div className="mt-4 space-y-2">
        {details.map((detail) => (
          <div key={detail} className="flex items-center gap-2 text-[12px] text-slate-600">
            <span className="h-1.5 w-1.5 rounded-full bg-slate-300" />
            {detail}
          </div>
        ))}
      </div>
    </article>
  );
}

function SharedLink({ href, title, description }: { href: string; title: string; description: string }) {
  return (
    <Link
      href={href}
      prefetch={false}
      className="group flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white px-4 py-3 transition hover:border-blue-200 hover:shadow-sm"
    >
      <div className="min-w-0">
        <div className="text-[13px] font-semibold text-slate-800">{title}</div>
        <div className="mt-0.5 truncate text-[11px] text-slate-400">{description}</div>
      </div>
      <span className="shrink-0 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-blue-500"><Arrow /></span>
    </Link>
  );
}

export default function ForeignTradeWorkbenchPage() {
  const [summary, setSummary] = useState({ orders: 0, pendingProcurement: 0, pendingFulfillment: 0, channels: 0, skuMappings: 0, pendingSkuMappings: 0, salesAmount: "0", profitCny: "0" });
  const [overviewFailed, setOverviewFailed] = useState(false);
  useEffect(() => {
    authenticatedFetch("/api/v1/foreign-trade/overview", { cache: "no-store" })
      .then((res) => res.ok ? res.json() : null)
      .then((data) => { setOverviewFailed(false); if (data) setSummary(data); })
      .catch(() => setOverviewFailed(true));
  }, []);
  const metrics = [
    { label: "外贸订单", value: String(summary.orders), hint: "全部渠道订单" },
    { label: "待采购", value: String(summary.pendingProcurement), hint: "需要采购处理" },
    { label: "待履约", value: String(summary.pendingFulfillment), hint: "尚未完成发货" },
    { label: "渠道", value: String(summary.channels), hint: `SKU 映射 ${summary.skuMappings} 条` },
    { label: "累计利润", value: `¥${Number(summary.profitCny || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`, hint: "按订单汇率折算人民币" },
  ];

  return (
    <div className="min-h-full bg-slate-50/70 px-5 py-5">
      <div className="mx-auto max-w-[1500px]">
        {overviewFailed && <div role="alert" className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">外贸概览加载失败，以下指标为默认值；请刷新重试。</div>}
        <section className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-blue-500">FOREIGN TRADE WORKBENCH</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">外贸总览</h1>
            <p className="mt-1 text-sm text-slate-500">独立站、一件代发与 Alsvid 共用商品、采购、库存、物流和财务底座。</p>
          </div>
          <div className="flex items-center gap-2">
            <Status tone="shared">底层数据共用</Status>
            <Status tone="planning">业务工作台建设中</Status>
          </div>
        </section>

        <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded-xl border border-slate-200 bg-white px-4 py-3.5 shadow-[0_5px_18px_rgba(15,23,42,0.025)]">
              <div className="text-[11px] font-medium text-slate-400">{metric.label}</div>
              <div className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">{metric.value}</div>
              <div className="mt-1 truncate text-[11px] text-slate-400">{metric.hint}</div>
            </div>
          ))}
        </section>

        <section className="mt-5 grid gap-4 xl:grid-cols-3">
          <ChannelCard
            eyebrow="DTC / B2C"
            title="独立站"
            description="承接海外消费者订单，后续优先连接 Shopify；订单进入中台后再走采购、履约和利润核算。"
            details={["网站订单统一进入外贸订单池", "支持海外售价、币种与支付手续费", "与国内商品档案建立 SKU 映射"]}
            status={<Status tone="planning">待接入</Status>}
          />
          <ChannelCard
            eyebrow="DROPSHIPPING"
            title="一件代发"
            description="客户在你的站点下单后，由系统生成供应采购与海外履约任务，避免人工重复抄单。"
            details={["客户订单 → 供应采购 → 海外发货", "记录供应商采购价与实际物流费", "异常订单单独进入待办队列"]}
            status={<Status tone="planning">流程设计</Status>}
          />
          <ChannelCard
            eyebrow="BRAND"
            title="ALSVID"
            description="德国 / 奥地利自行车业务作为独立品牌线管理，同时复用中台商品、采购、物流与财务能力。"
            details={["Germany + Austria", "German + English / EUR", "B2C 消费者 + Dealers/B2B 经销商"]}
            status={<Status tone="ready">首期规划明确</Status>}
          />
        </section>

        <section className="mt-4 grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-slate-900">外贸链路</h2>
                <p className="mt-1 text-[11px] text-slate-400">先把订单闭环做通，再逐步增加营销与自动化。</p>
              </div>
              <Status tone="planning">V1</Status>
            </div>
            <div className="mt-5 grid gap-2 md:grid-cols-5">
              {[
                ["1", "渠道订单", "Shopify / B2B / 代发"],
                ["2", "商品匹配", "海外 SKU ↔ 中台货品"],
                ["3", "采购决策", "现货 / 国内采购 / 海外采购"],
                ["4", "履约物流", "发货、运单、实际费用"],
                ["5", "财务利润", "收款、手续费、汇率、利润"],
              ].map(([step, title, sub]) => (
                <div key={step} className="relative rounded-xl border border-slate-200 bg-slate-50/60 p-3">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-[11px] font-semibold text-white">{step}</div>
                  <div className="mt-3 text-[13px] font-semibold text-slate-800">{title}</div>
                  <div className="mt-1 text-[11px] leading-5 text-slate-400">{sub}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
            <h2 className="text-base font-semibold text-slate-900">首期需要完成</h2>
            <p className="mt-1 text-[11px] text-slate-400">先解决真实经营必需项，不做无数据的复杂看板。</p>
            <div className="mt-4 space-y-3">
              {[
                ["01", "渠道连接", "Shopify / 外部订单来源"],
                ["02", "海外 SKU 映射", "与现有货品档案共用"],
                ["03", "订单履约状态", "采购、发货、取消、退款"],
                ["04", "收款与费用", "支付手续费 / 汇率 / 物流"],
              ].map(([no, title, sub]) => (
                <div key={no} className="flex items-start gap-3 border-b border-slate-100 pb-3 last:border-0 last:pb-0">
                  <span className="text-[11px] font-semibold text-blue-500">{no}</span>
                  <div>
                    <div className="text-[13px] font-medium text-slate-700">{title}</div>
                    <div className="mt-0.5 text-[11px] text-slate-400">{sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_8px_24px_rgba(15,23,42,0.03)]">
          <div>
            <h2 className="text-base font-semibold text-slate-900">共用底层能力</h2>
            <p className="mt-1 text-[11px] text-slate-400">外贸不重复维护一套基础资料，需要时直接进入已有模块处理。</p>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <SharedLink href="/foreign-trade/orders" title="外贸订单" description="录单、采购、履约状态流转" />
            <SharedLink href="/foreign-trade/channels" title="渠道管理" description="Shopify / B2B / 一件代发" />
            <SharedLink href="/foreign-trade/sku-mappings" title="海外 SKU 映射" description={`待匹配 ${summary.pendingSkuMappings} 条`} />
            <SharedLink href="/foreign-trade/finance" title="收款与利润" description="实收、费用、汇率与利润" />
          </div>
        </section>
      </div>
    </div>
  );
}
