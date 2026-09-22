export default function MetricCard({
  label,
  value,
  hint,
  delta,
  deltaSuffix,
}: {
  label: string;
  value: string;
  hint?: string;
  /** 环比百分比（正=涨绿、负=跌红）；null/undefined 不显示 */
  delta?: number | null;
  deltaSuffix?: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1.5 text-xl font-semibold tabular-nums">{value}</div>
      {delta !== undefined && delta !== null && Number.isFinite(delta) && (
        <div className={`mt-1 text-[11px] font-medium tabular-nums ${delta >= 0 ? "text-emerald-600" : "text-red-500"}`}>
          {delta >= 0 ? "↑" : "↓"} {Math.abs(delta).toFixed(1)}% {deltaSuffix ?? "环比"}
        </div>
      )}
      {hint && <div className="mt-1 text-[11px] text-gray-400">{hint}</div>}
    </div>
  );
}
