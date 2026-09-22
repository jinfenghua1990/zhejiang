export type Theme = "light" | "dark";
export type ThemeMode = "system" | Theme;

const STORAGE_KEY = "app-theme-mode";
const LEGACY_STORAGE_KEY = "app-theme";

export function getSystemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function getStoredThemeMode(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "system" || stored === "light" || stored === "dark") return stored;

    // 兼容旧版只保存 light/dark 的 app-theme。
    const legacy = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    if (legacy === "light" || legacy === "dark") return legacy;
  } catch {
    // 浏览器策略禁用 localStorage 时，当前标签页仍可正常使用主题。
  }
  return "system";
}

export function resolveTheme(mode: ThemeMode): Theme {
  return mode === "system" ? getSystemTheme() : mode;
}

export function applyThemeMode(mode: ThemeMode): Theme {
  const resolved = resolveTheme(mode);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.dataset.themeMode = mode;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  return resolved;
}

export function setStoredThemeMode(mode: ThemeMode) {
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
    window.localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    // 持久化不可用时不阻断当前页面主题切换。
  }
}

export function setThemeMode(mode: ThemeMode): Theme {
  setStoredThemeMode(mode);
  return applyThemeMode(mode);
}

/** 监听系统主题；仅在 mode=system 时通知页面切换。 */
export function watchSystemTheme(onChange: (theme: Theme) => void): () => void {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = () => {
    if (getStoredThemeMode() === "system") {
      onChange(mq.matches ? "dark" : "light");
    }
  };
  mq.addEventListener("change", handler);
  return () => mq.removeEventListener("change", handler);
}

export function watchStoredThemeMode(onChange: (mode: ThemeMode) => void): () => void {
  const handler = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === LEGACY_STORAGE_KEY || event.key === null) {
      onChange(getStoredThemeMode());
    }
  };
  window.addEventListener("storage", handler);
  return () => window.removeEventListener("storage", handler);
}

/** 兼容旧调用。 */
export function getInitialTheme(): Theme {
  return resolveTheme(getStoredThemeMode());
}

/** 兼容旧调用。 */
export function applyThemeClass(theme: Theme) {
  applyThemeMode(theme);
}

/** 兼容旧调用。 */
export function setStoredTheme(theme: Theme) {
  setThemeMode(theme);
}
