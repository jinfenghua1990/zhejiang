const COLORS: Record<string, string> = {
  connected: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  transport_connected: "bg-sky-50 text-sky-700 ring-sky-200",
  available: "bg-sky-50 text-sky-700 ring-sky-200",
  untested: "bg-amber-50 text-amber-700 ring-amber-200",
  unconfigured: "bg-gray-100 text-gray-500 ring-gray-200",
  blocked: "bg-amber-50 text-amber-800 ring-amber-300",
  error: "bg-red-50 text-red-700 ring-red-200",
  needs_login: "bg-amber-50 text-amber-700 ring-amber-200",
  needs_user_verify: "bg-orange-50 text-orange-700 ring-orange-200",
};

const LABELS: Record<string, string> = {
  connected: "已连接",
  transport_connected: "MCP 通道已连通",
  available: "文件导入可用",
  untested: "已配置·未测试",
  unconfigured: "未配置",
  blocked: "业务权限未开通",
  error: "错误",
  needs_login: "登录已失效",
  needs_user_verify: "需要安全验证",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        COLORS[status] ?? COLORS.unconfigured
      }`}
    >
      {LABELS[status] ?? status}
    </span>
  );
}
