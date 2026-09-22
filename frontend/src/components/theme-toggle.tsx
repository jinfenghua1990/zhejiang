"use client";

import { useEffect, useRef, useState } from "react";
import {
  applyThemeMode,
  getStoredThemeMode,
  setThemeMode,
  watchStoredThemeMode,
  watchSystemTheme,
  type Theme,
  type ThemeMode,
} from "@/lib/theme";

const OPTIONS: Array<{ mode: ThemeMode; label: string; desc: string }> = [
  { mode: "system", label: "跟随系统", desc: "设备变化时自动切换" },
  { mode: "light", label: "浅色", desc: "明亮、低对比的工作界面" },
  { mode: "dark", label: "深色", desc: "降低夜间环境的视觉刺激" },
];

function ModeGlyph({ mode, resolved }: { mode: ThemeMode; resolved: Theme }) {
  if (mode === "system") {
    return (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="13" rx="2" />
        <path d="M8 21h8M12 17v4" />
        <circle cx="17" cy="9" r="1.8" fill={resolved === "dark" ? "currentColor" : "none"} />
      </svg>
    );
  }

  if (mode === "dark") {
    return (
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />
      </svg>
    );
  }

  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  );
}

function Preview({ mode }: { mode: ThemeMode }) {
  const shell = mode === "dark"
    ? "border-slate-600 bg-[#0b1018]"
    : mode === "light"
      ? "border-slate-200 bg-[#f5f7fb]"
      : "border-slate-300 bg-gradient-to-r from-[#f5f7fb] from-50% to-[#0b1018] to-50%";
  const sidebar = mode === "dark" ? "bg-[#0b1625]" : "bg-[#14263d]";
  const header = mode === "dark" ? "bg-[#172033]" : "bg-white";
  const panel = mode === "dark" ? "border-[#263244] bg-[#111827]" : "border-[#e4e9f1] bg-white";

  return (
    <span className={"relative h-9 w-14 overflow-hidden rounded-md border " + shell}>
      <span className={"absolute left-1 top-1 h-7 w-2.5 rounded-sm " + sidebar} />
      <span className={"absolute left-[16px] right-1 top-1 h-2 rounded-sm " + header} />
      <span className={"absolute bottom-1 left-[16px] right-1 top-4 rounded-sm border " + panel} />
    </span>
  );
}

export default function ThemeToggle() {
  const [mode, setMode] = useState<ThemeMode>("system");
  const [resolved, setResolved] = useState<Theme>("light");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const sync = (nextMode: ThemeMode) => {
      setMode(nextMode);
      setResolved(applyThemeMode(nextMode));
    };
    sync(getStoredThemeMode());

    const stopSystem = watchSystemTheme((next) => {
      setResolved(next);
      applyThemeMode("system");
    });
    const stopStorage = watchStoredThemeMode(sync);

    return () => {
      stopSystem();
      stopStorage();
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const choose = (next: ThemeMode) => {
    setMode(next);
    setResolved(setThemeMode(next));
    setOpen(false);
  };

  const current = OPTIONS.find((item) => item.mode === mode) ?? OPTIONS[0];

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        title={"外观：" + current.label}
        aria-label={"外观：" + current.label}
        aria-expanded={open}
        className={"app-theme-button flex h-8 items-center gap-1.5 rounded-lg border px-2 text-slate-500 transition " + (open ? "ring-2 ring-indigo-100 text-indigo-600" : "")}
      >
        <ModeGlyph mode={mode} resolved={resolved} />
        <span className="hidden text-[11px] font-medium 2xl:inline">{mode === "system" ? "自动" : current.label}</span>
      </button>

      {open && (
        <div className="app-popover absolute right-0 top-full z-dropdown mt-1.5 w-[286px] rounded-xl border p-2 shadow-lg">
          <div className="flex items-center justify-between px-2.5 pb-2 pt-1">
            <div>
              <div className="text-[11px] font-semibold text-slate-700">界面外观</div>
              <div className="mt-0.5 text-[10px] text-slate-400">浅色和深色使用同一套层级与状态语义</div>
            </div>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[9px] text-slate-500">{resolved === "dark" ? "当前深色" : "当前浅色"}</span>
          </div>

          <div className="space-y-1">
            {OPTIONS.map((item) => {
              const active = item.mode === mode;
              return (
                <button
                  key={item.mode}
                  type="button"
                  onClick={() => choose(item.mode)}
                  className={"flex w-full items-center gap-3 rounded-lg border px-2.5 py-2 text-left transition " + (active ? "border-indigo-200 bg-indigo-50/70" : "border-transparent hover:border-slate-200 hover:bg-slate-50")}
                >
                  <Preview mode={item.mode} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-1.5 text-[12px] font-semibold text-slate-700">
                      <ModeGlyph mode={item.mode} resolved={resolved} />
                      {item.label}
                    </span>
                    <span className="mt-0.5 block text-[10px] leading-4 text-slate-400">{item.desc}</span>
                  </span>
                  <span className={"flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px] font-bold " + (active ? "border-indigo-600 bg-indigo-600 text-white" : "border-slate-300 text-transparent")}>✓</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
