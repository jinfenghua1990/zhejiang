"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** 兼容旧地址；财务分类规则已统一收拢到基础档案的财务分类页签。 */
export default function LegacyTaxCategoryRulesPage() {
  const router = useRouter();
  useEffect(() => {
    // 用客户端路由跳转：window.location.replace 会整页重载并清空所有工作区 Tab
    router.replace("/products?productTab=tax-rules");
  }, [router]);

  return (
    <div className="mx-auto max-w-xl rounded-2xl border border-slate-200 bg-white p-8 text-center">
      <h1 className="text-lg font-semibold text-slate-900">财务分类已合并到基础档案</h1>
      <p className="mt-2 text-sm text-slate-500">正在打开「货品档案 → 财务分类」。如果没有自动跳转，请点击下面的入口。</p>
      <Link href="/products?productTab=tax-rules" className="mt-5 inline-flex rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white">
        打开基础档案
      </Link>
    </div>
  );
}
