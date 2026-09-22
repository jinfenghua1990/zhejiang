"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { JackyunPanel } from "@/app/data-center-import/panels/jackyun";
import InboundDocumentsBoard from "./inbound-documents-board";

export default function ReceivingPage() {
  // 工作区下每个 Tab 有自己冻结的 URL：读注入的 searchParams，不要读 window.location.search
  const router = useRouter();
  const searchParams = useSearchParams();
  const [showJackyunImport, setShowJackyunImport] = useState(false);

  useEffect(() => {
    setShowJackyunImport(searchParams.get("panel") === "jackyun");
  }, [searchParams]);

  function closeJackyunImport() {
    setShowJackyunImport(false);
    window.dispatchEvent(new Event("inbound-documents-updated"));
    if (searchParams.get("panel") === "jackyun") {
      // 用客户端路由清参数：history.replaceState 会绕过工作区，让 Tab 的地址与现场脱节
      router.replace("/supply-chain/receiving");
    }
  }

  return (
    <div className="space-y-3">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 bg-white/95 px-8 py-3.5 backdrop-blur">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">到仓入库</h1>
            <span className="text-xs font-medium text-violet-600">库存中心 / 到仓入库</span>
          </div>
          <p className="mt-1 text-xs text-slate-500">按仓库查看到仓入库明细，覆盖本系统入库、吉客云入库单与耗材入库；点单据打开明细弹窗。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setShowJackyunImport(true)} className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100">吉客云入库单</button>
        </div>
      </header>
      <InboundDocumentsBoard />
      {showJackyunImport && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-slate-950/35 p-4 pt-8 backdrop-blur-[2px]" role="dialog" aria-modal="true" aria-label="吉客云入库单导入">
          <div className="flex max-h-[calc(100vh-4rem)] w-full max-w-[1180px] flex-col overflow-hidden rounded-2xl border border-slate-200 bg-[#f7f9fc] shadow-2xl">
            <div className="flex shrink-0 items-start justify-between gap-4 border-b border-slate-200 bg-white px-6 py-4">
              <div>
                <div className="text-xs font-medium text-indigo-600">到仓入库 / 吉客云入库单</div>
                <h2 className="mt-1 text-xl font-semibold text-slate-900">上传吉客云入库单</h2>
                <p className="mt-1 text-sm text-slate-500">在当前页面上传并识别文件；数据作为历史外部参考，不会替代本系统采购入库主单。</p>
              </div>
              <button type="button" onClick={closeJackyunImport} aria-label="关闭" className="rounded-lg px-2 py-1 text-2xl leading-none text-slate-400 hover:bg-slate-100 hover:text-slate-700">×</button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              <JackyunPanel />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
