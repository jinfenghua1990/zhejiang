export default function PhasePlaceholder({
  phase,
  title,
  description,
  points,
}: {
  phase: number;
  title: string;
  description: string;
  points: string[];
}) {
  return (
    <div>
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">{description}</p>
      <div className="mt-6 max-w-3xl rounded-xl border border-dashed border-gray-300 bg-white p-6">
        <div className="text-xs font-medium uppercase tracking-wider text-indigo-500">
          Phase {phase} 规划范围
        </div>
        <ul className="mt-3 space-y-1.5 text-sm text-gray-600">
          {points.map((p) => (
            <li key={p} className="flex gap-2">
              <span className="text-gray-300">·</span>
              {p}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-gray-400">
          本页面将在对应 Phase 依据真实数据落地后开放，不展示模拟数据。
        </p>
      </div>
    </div>
  );
}
