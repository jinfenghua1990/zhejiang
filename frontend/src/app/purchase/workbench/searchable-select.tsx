"use client";

import { useEffect, useRef, useState } from "react";
import { useTabActive } from "@/lib/workspace/tab-store";

export type SearchSelectOption = { value: string; label: string; keywords?: string };

/** 可搜索下拉：输入关键词按编码/名称过滤，点击选择。
 *  用于替代原生 <select>（耗材等条目变多后无法检索）。
 *  必须放在 <form> 内使用时保持 type="button"，避免触发提交。 */
export function SearchableSelect({
  options, value, onChange, placeholder = "选择", ariaLabel, disabled, className, compact, align = "left",
}: {
  options: SearchSelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel?: string;
  disabled?: boolean;
  className?: string;
  compact?: boolean;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value);
  // 工作区下隐藏 Tab 常驻挂载：不监听别的 Tab 里的点击，否则切走再回来下拉会被误关
  const tabActive = useTabActive();

  useEffect(() => {
    if (!open || !tabActive) return;
    const handler = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, tabActive]);

  const keyword = query.trim().toLowerCase();
  const filtered = keyword
    ? options.filter((option) => `${option.label} ${option.keywords ?? ""}`.toLowerCase().includes(keyword))
    : options;

  const btnBase = compact
    ? "h-6 rounded border px-1 text-[11px]"
    : "h-8 rounded-md border px-2 text-xs";
  return (
    <div ref={boxRef} className={`relative min-w-0 ${className ?? ""}`}>
      <button
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => { setOpen((v) => !v); setQuery(""); }}
        className={`flex w-full items-center justify-between gap-1 border-slate-200 bg-white outline-none focus:border-indigo-400 disabled:bg-slate-50 disabled:text-slate-400 ${btnBase} ${value ? "text-slate-700" : "text-slate-400"}`}
      >
        <span className="truncate">{selected?.label || placeholder}</span>
        <span className="shrink-0 text-slate-300" aria-hidden>▾</span>
      </button>
      {open && (
        <div className={`absolute z-dropdown mt-1 w-full min-w-[200px] rounded-md border border-slate-200 bg-white shadow-lg ${align === "right" ? "right-0" : "left-0"}`}>
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}
            placeholder="搜索编码或名称"
            aria-label={`${ariaLabel ?? "选择"}搜索`}
            className="w-full border-b border-slate-100 px-2 py-1.5 text-xs outline-none placeholder:text-slate-300"
          />
          <div className="max-h-56 overflow-y-auto py-0.5" role="listbox">
            {value !== "" && (
              <button type="button" onClick={() => { onChange(""); setOpen(false); }}
                className="block w-full px-2 py-1.5 text-left text-[11px] text-slate-400 hover:bg-slate-50">清空选择</button>
            )}
            {filtered.map((option) => (
              <button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => { onChange(option.value); setOpen(false); }}
                className={`block w-full truncate px-2 py-1.5 text-left text-xs hover:bg-indigo-50 ${option.value === value ? "bg-indigo-50 font-medium text-indigo-700" : "text-slate-600"}`}
              >{option.label}</button>
            ))}
            {filtered.length === 0 && <p className="px-2 py-2 text-[11px] text-slate-400">无匹配项</p>}
          </div>
        </div>
      )}
    </div>
  );
}
