"use client";

import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  systemUpdateApi,
  type SystemLocalChanges,
  type SystemLocalSyncResult,
  type SystemUpdateMode,
  type SystemUpdateLevel,
  type SystemUpdateReadiness,
  type SystemUpdateSettings,
  type SystemUpdateStatus,
} from "@/lib/api";

const MODE_COPY: Record<SystemUpdateMode, { title: string; desc: string }> = {
  manual: {
    title: "手动",
    desc: "只有你点击“检查更新”时才检查；安装始终由你确认。",
  },
  auto_download: {
    title: "自动检查",
    desc: "后台定时检查 GitHub；发现新版本后，只提示你，不会自动安装。",
  },
  auto_update: {
    title: "自动安装",
    desc: "后台定时检查，并只对符合“自动安装范围”的版本在维护窗口内自动更新。",
  },
};

const LEVEL_COPY: Record<SystemUpdateLevel, { label: string; desc: string; tone: string }> = {
  patch: {
    label: "小版本",
    desc: "UI、文案、普通 Bug 与低风险调整；默认允许自动安装。",
    tone: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  },
  feature: {
    label: "功能版本",
    desc: "新功能、数据模型或数据库迁移；默认等待人工确认。",
    tone: "bg-blue-50 text-blue-700 ring-blue-200",
  },
  major: {
    label: "重大版本",
    desc: "部署、安全或应用核心结构变化；默认必须人工确认。",
    tone: "bg-rose-50 text-rose-700 ring-rose-200",
  },
};

const PHASE_LABEL: Record<string, string> = {
  idle: "等待操作",
  checking: "检查 GitHub",
  queued: "准备更新",
  preflight: "更新前检查",
  backup: "备份数据",
  quiescing: "暂停后台任务",
  installing: "安装代码",
  migrating: "升级数据库",
  building: "构建前端",
  restarting: "重启服务",
  healthcheck: "验证新版本",
  rollback: "自动回滚",
  success: "更新完成",
  rolled_back: "已自动回滚",
  failed: "更新失败",
};

function fmtDate(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function shortSha(value?: string | null) {
  return value ? value.slice(0, 10) : "—";
}

function updateState(status: SystemUpdateStatus) {
  if (status.running) {
    return {
      label: PHASE_LABEL[status.phase || "idle"] || "更新中",
      tone: "bg-blue-50 text-blue-700 ring-blue-200",
    };
  }
  if (status.phase === "rolled_back") {
    return { label: "已回滚", tone: "bg-amber-50 text-amber-700 ring-amber-200" };
  }
  if (status.lastCheckError || status.lastAutoError || status.phase === "failed") {
    return { label: "需要处理", tone: "bg-rose-50 text-rose-700 ring-rose-200" };
  }
  if (status.updateAvailable) {
    return { label: "有新版本", tone: "bg-amber-50 text-amber-700 ring-amber-200" };
  }
  return { label: "版本一致", tone: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30" };
}

function blockerReason(
  status: SystemUpdateStatus,
  readiness: SystemUpdateReadiness | null,
  busy: string,
  isContainer: boolean,
) {
  if (isContainer) return "当前为容器托管模式，版本通过 GitHub / GHCR 发布。";
  if (busy) return "正在执行其他更新操作。";
  if (status.running) return "更新正在执行。";
  if (status.dirty) return "本地存在未提交修改，为避免覆盖，已禁止自动更新。";
  if (status.diverged) return "本地与远端分支已经分叉，需要先人工处理 Git 历史。";
  if (readiness && !readiness.ready) {
    const blocker = readiness.checks.find((item) => item.blocking && item.status === "error");
    return blocker
      ? `${blocker.label}：${blocker.detail}`
      : `环境自检有 ${readiness.blockingCount} 项阻塞，请先处理。`;
  }
  if (!status.updateAvailable) return "当前没有待安装的新版本。";
  return "";
}

export default function SystemUpdatePage() {
  const [status, setStatus] = useState<SystemUpdateStatus | null>(null);
  const [readiness, setReadiness] = useState<SystemUpdateReadiness | null>(null);
  const [draft, setDraft] = useState<SystemUpdateSettings | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [localChanges, setLocalChanges] = useState<SystemLocalChanges | null>(null);
  const [localSync, setLocalSync] = useState<SystemLocalSyncResult | null>(null);
  const [localError, setLocalError] = useState("");
  const [showLocalFiles, setShowLocalFiles] = useState(false);
  const [showAllChecks, setShowAllChecks] = useState(false);
  const [showChangedFiles, setShowChangedFiles] = useState(false);
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false);
  const [updateConfirmed, setUpdateConfirmed] = useState(false);
  const [showModalLogs, setShowModalLogs] = useState(false);
  const [activeTab, setActiveTab] = useState<"overview" | "strategy" | "logs" | "history">("overview");
  const [autoScrollLogs, setAutoScrollLogs] = useState(true);
  const logRef = useRef<HTMLPreElement | null>(null);
  const firstCheckRef = useRef(false);
  const sawRunningRef = useRef(false);
  const reloadScheduledRef = useRef(false);

  const load = useCallback(async (preserveDraft = false) => {
    try {
      const next = await systemUpdateApi.status();
      setStatus(next);
      if (!preserveDraft) setDraft(next.settings);
      const activeError =
        next.lastCheckError ||
        next.lastAutoError ||
        (next.phase === "failed" ? next.error || "" : "");
      setError(activeError);
      return next;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return null;
    }
  }, []);

  const loadReadiness = useCallback(async () => {
    try {
      const next = await systemUpdateApi.readiness();
      setReadiness(next);
      return next;
    } catch (caught) {
      setReadiness(null);
      setError((current) => current || (caught instanceof Error ? caught.message : String(caught)));
      return null;
    }
  }, []);

  const loadLocalChanges = useCallback(async () => {
    try {
      const next = await systemUpdateApi.localChanges();
      setLocalChanges(next);
      setLocalError("");
      return next;
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : String(caught));
      return null;
    }
  }, []);

  useEffect(() => {
    void Promise.all([load(), loadReadiness()]);
  }, [load, loadReadiness]);

  useEffect(() => {
    if (!status?.running) return;
    sawRunningRef.current = true;
    setUpdateDialogOpen(true);
    setUpdateConfirmed(true);
    const timer = window.setInterval(() => void load(true), 1800);
    return () => window.clearInterval(timer);
  }, [load, status?.running]);


  useEffect(() => {
    if (activeTab !== "logs" || !autoScrollLogs || !logRef.current) return;
    logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [activeTab, autoScrollLogs, status?.logs]);

  useEffect(() => {
    if (
      !status
      || status.running
      || status.phase !== "success"
      || !sawRunningRef.current
      || reloadScheduledRef.current
    ) return;

    reloadScheduledRef.current = true;
    setNotice("更新完成，正在重新载入最新页面…");
    const timer = window.setTimeout(() => window.location.reload(), 2400);
    return () => window.clearTimeout(timer);
  }, [status]);

  const runtime = status?.runtime;
  const isContainer = runtime?.deploymentMode === "container";

  useEffect(() => {
    if (!status || isContainer) return;
    void loadLocalChanges();
  }, [isContainer, loadLocalChanges, status?.currentSha]);

  useEffect(() => {
    if (!status || isContainer || firstCheckRef.current) return;
    firstCheckRef.current = true;
    if (status.lastCheckAt && status.moduleVersions?.length) return;

    void (async () => {
      setBusy("check");
      try {
        const next = await systemUpdateApi.check();
        setStatus(next);
        setDraft(next.settings);
        setError(next.lastCheckError || "");
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusy("");
      }
    })();
  }, [isContainer, status]);

  const checks = readiness?.checks ?? [];
  const saveChanged = useMemo(() => {
    if (!status || !draft) return false;
    const source = status.settings;
    return source.enabled !== draft.enabled
      || source.mode !== draft.mode
      || source.checkIntervalMinutes !== draft.checkIntervalMinutes
      || source.autoUpdateHour !== draft.autoUpdateHour
      || source.autoUpdateWindowMinutes !== draft.autoUpdateWindowMinutes
      || source.autoInstallLevel !== draft.autoInstallLevel;
  }, [draft, status]);

  async function refreshRuntime() {
    setBusy("refresh");
    setError("");
    setNotice("");
    try {
      await Promise.all([load(), loadReadiness(), isContainer ? Promise.resolve(null) : loadLocalChanges()]);
    } finally {
      setBusy("");
    }
  }

  async function checkNow() {
    setBusy("check");
    setError("");
    setNotice("");
    try {
      const next = await systemUpdateApi.check();
      setStatus(next);
      setDraft(next.settings);
      setError(next.lastCheckError || "");
      setNotice(
        next.lastCheckError
          ? ""
          : next.updateAvailable
            ? `发现 ${next.changes?.length || 1} 项更新，可以直接点击“从云端同步到本地”。`
            : "检查完成，当前已经是最新版本。",
      );
      await loadReadiness();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  async function scanLocalChanges() {
    setBusy("local-scan");
    setLocalError("");
    setNotice("");
    try {
      const next = await loadLocalChanges();
      if (next) {
        setNotice(
          next.dirty
            ? `检测到 ${next.eligibleCount} 个可同步代码文件，${next.excludedCount} 个受保护文件不会上传。`
            : "本地工作区干净，没有待同步修改。",
        );
      }
    } finally {
      setBusy("");
    }
  }

  async function syncLocalVersion() {
    if (!localChanges?.eligibleCount) return;
    const confirmed = window.confirm(
      `将 ${localChanges.eligibleCount} 个代码文件提交并推送到当前配置的 ${status?.settings.branch || "develop"} 分支。\n\n同步前会先检查云端是否有新提交；如果云端领先或历史分叉，操作会停止，不会强制覆盖。\n受保护的 Excel、数据库、备份和凭证不会上传。\n\n确认继续？`,
    );
    if (!confirmed) return;

    setBusy("local-sync");
    setLocalError("");
    setNotice("");
    try {
      const result = await systemUpdateApi.syncLocalChanges();
      setLocalSync(result);
      setLocalChanges(result.localChanges);
      setNotice(`本地修改已同步到云端：${result.branch} · ${result.shortSha}。`);
      await load(true);
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  function openUpdateDialog() {
    setError("");
    setNotice("");
    setShowModalLogs(false);
    setUpdateConfirmed(false);
    setUpdateDialogOpen(true);
  }

  function closeUpdateDialog() {
    if (status?.running || busy === "apply") return;
    setUpdateDialogOpen(false);
    setUpdateConfirmed(false);
    setShowModalLogs(false);
  }

  async function startUpdate() {
    setBusy("apply");
    setError("");
    setNotice("");
    setUpdateConfirmed(true);
    sawRunningRef.current = true;
    try {
      const next = await systemUpdateApi.apply();
      setStatus(next);
      if (next.started === false) {
        sawRunningRef.current = false;
        setUpdateConfirmed(false);
        setUpdateDialogOpen(false);
        setNotice("当前已经是最新版本，无需安装。");
      } else {
        setNotice("");
      }
    } catch (caught) {
      sawRunningRef.current = false;
      setUpdateConfirmed(false);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  async function saveSettings() {
    if (!draft) return;
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const result = await systemUpdateApi.saveSettings({
        enabled: draft.enabled,
        mode: draft.mode,
        checkIntervalMinutes: draft.checkIntervalMinutes,
        autoUpdateHour: draft.autoUpdateHour,
        autoUpdateWindowMinutes: draft.autoUpdateWindowMinutes,
        autoInstallLevel: draft.autoInstallLevel,
      });
      setDraft(result.settings);
      setNotice("更新策略已保存并立即生效。");
      if (result.settings.enabled && result.settings.mode !== "manual") {
        const next = await systemUpdateApi.check();
        setStatus(next);
        setError(next.lastCheckError || "");
      } else {
        await load();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy("");
    }
  }

  function patchDraft(patch: Partial<SystemUpdateSettings>) {
    setDraft((current) => current ? { ...current, ...patch } : current);
    setNotice("设置已修改，保存后生效。");
  }

  if (!status || !draft) {
    return (
      <div className="pb-10">
        <header className="app-page-header -mx-1 pb-4">
          <h1 className="text-xl font-semibold text-slate-900 dark:text-slate-100">系统更新</h1>
          <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-300">正在读取当前版本和更新状态。</p>
        </header>
        <div className="app-card rounded-xl p-6 text-sm text-slate-500 dark:text-slate-300">
          {error ? "系统更新加载失败：" + error : "正在连接更新服务…"}
        </div>
      </div>
    );
  }

  const state = updateState(status);
  const progress = Math.max(0, Math.min(100, Number(status.progress || 0)));
  const currentSha = runtime?.gitSha || status.currentSha || "";
  const targetSha = status.latestSha || status.targetSha || status.downloadedSha || "";
  const reason = blockerReason(status, readiness, busy, Boolean(isContainer));
  const installDisabled = Boolean(reason);
  const latestSubject = status.latestCommit?.subject || status.changes?.[0]?.subject || "";
  const updateLevel = (status.updateLevel || "patch") as SystemUpdateLevel;
  const levelMeta = LEVEL_COPY[updateLevel];
  const autoInstallLevelMeta = LEVEL_COPY[draft.autoInstallLevel || "patch"];
  const visibleChanges = (status.changes || []).slice(0, 6);
  const moduleVersions = status.moduleVersions || [];
  const moduleUpdateCount = moduleVersions.filter((item) => item.status === "update").length;
  const updateDialogProgress = Math.max(0, Math.min(100, status.running ? Math.max(progress, 4) : status.phase === "success" ? 100 : progress));
  const updateDialogResult = status.phase === "failed"
    ? "failed"
    : status.phase === "rolled_back"
      ? "rolled_back"
      : status.phase === "success" && sawRunningRef.current
        ? "success"
        : status.running || busy === "apply" || updateConfirmed
          ? "running"
          : "confirm";

  const phaseStep: Record<string, number> = {
    checking: 0,
    queued: 0,
    preflight: 0,
    backup: 1,
    quiescing: 1,
    installing: 2,
    migrating: 2,
    building: 2,
    restarting: 3,
    healthcheck: 3,
    success: 4,
    rolled_back: 3,
    rollback: 3,
    failed: 3,
  };
  const activeStep = phaseStep[status.phase || "idle"] ?? 0;
  const stageStatus = (index: number): "done" | "active" | "pending" | "error" => {
    if (status.phase === "success" && !status.running) return "done";
    if (status.phase === "failed" && index === activeStep) return "error";
    if (status.phase === "rolled_back" && index === activeStep) return "error";
    if (status.running) {
      if (index < activeStep) return "done";
      if (index === activeStep) return "active";
      return "pending";
    }
    if (index === 0 && readiness?.ready) return "done";
    return "pending";
  };
  const stageMessage = status.running
    ? PHASE_LABEL[status.phase || "idle"] || "更新中"
    : status.phase === "success"
      ? "更新完成"
      : status.updateAvailable
        ? "等待开始"
        : "当前为最新版本";

  return (
    <div className="pb-10 text-slate-900 dark:text-slate-100">
      <header className="app-page-header -mx-1 pb-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-600/10 text-blue-600 dark:bg-blue-400/10 dark:text-blue-300">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" aria-hidden="true">
              <path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.64 5.64l2.12 2.12m8.48 8.48 2.12 2.12m0-12.72-2.12 2.12m-8.48 8.48-2.12 2.12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              <circle cx="12" cy="12" r="3.5" stroke="currentColor" strokeWidth="1.8" />
            </svg>
          </div>
          <div>
            <h1 className="text-[22px] font-semibold tracking-tight text-slate-900 dark:text-slate-100">系统更新</h1>
            <p className="mt-1 text-[13px] leading-6 text-slate-500 dark:text-slate-300">
              版本更新、更新策略、更新日志和历史记录分开管理。
            </p>
          </div>
        </div>
      </header>

      <nav className="mb-4 flex items-center gap-1 border-b border-slate-200 dark:border-slate-700" aria-label="系统更新页签">
        {([
          ["overview", "版本更新"],
          ["strategy", "更新策略"],
          ["logs", "更新日志"],
          ["history", "历史记录"],
        ] as const).map(([key, label]) => (
          <button
            type="button"
            key={key}
            onClick={() => setActiveTab(key)}
            className={`relative px-4 py-2.5 text-[12px] font-medium transition-colors ${
              activeTab === key
                ? "text-blue-600 dark:text-blue-300"
                : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            {label}
            {key === "logs" && status.logs?.length ? <span className="ml-1.5 rounded-full bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-300">{status.logs.length}</span> : null}
            {activeTab === key && <span className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-blue-600 dark:bg-blue-400" />}
          </button>
        ))}
      </nav>

      {activeTab === "overview" && (
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <div className="min-w-0 space-y-4">
            {!isContainer && (
              <section className="app-card overflow-hidden rounded-2xl">
                <div className="p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <h2 className="text-[14px] font-semibold tracking-tight text-slate-900 dark:text-slate-100">本地版本管理</h2>
                        {localChanges ? (
                          <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ring-1 ring-inset ${
                            localChanges.dirty
                              ? "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:ring-amber-500/30"
                              : "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30"
                          }`}>
                            {localChanges.dirty ? "有本地修改" : "工作区干净"}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-1 text-[11px] leading-5 text-slate-400 dark:text-slate-400">
                        检测你直接在本机修改的代码，并安全提交到当前配置的 {status.settings.branch} 分支；云端领先或历史分叉时会停止，不会强制覆盖。
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => void scanLocalChanges()}
                        disabled={Boolean(busy || status.running)}
                        className="app-button-secondary h-8 rounded-lg px-3 text-[10px] font-medium disabled:opacity-40"
                      >
                        {busy === "local-scan" ? "检测中…" : "检测本地修改"}
                      </button>
                      <button
                        type="button"
                        onClick={() => void syncLocalVersion()}
                        disabled={Boolean(busy || status.running || !localChanges?.eligibleCount)}
                        className="app-button-primary h-8 rounded-lg px-3 text-[10px] font-semibold shadow-none disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {busy === "local-sync" ? "同步中…" : "同步到云端"}
                      </button>
                    </div>
                  </div>

                  {localChanges ? (
                    <>
                      <div className="mt-4 grid gap-2 sm:grid-cols-4">
                        <div className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800/45">
                          <div className="text-[10px] text-slate-400">当前分支</div>
                          <div className="mt-1 truncate font-mono text-[11px] font-semibold text-slate-700 dark:text-slate-100" title={localChanges.currentBranch}>
                            {localChanges.currentBranch || "(detached)"}
                          </div>
                        </div>
                        <div className="rounded-xl border border-slate-100 bg-slate-50/70 px-3 py-2.5 dark:border-slate-700 dark:bg-slate-800/45">
                          <div className="text-[10px] text-slate-400">基准版本</div>
                          <div className="mt-1 font-mono text-[11px] font-semibold text-slate-700 dark:text-slate-100">{shortSha(localChanges.baseSha)}</div>
                        </div>
                        <div className="rounded-xl border border-blue-100 bg-blue-50/60 px-3 py-2.5 dark:border-blue-500/20 dark:bg-blue-500/10">
                          <div className="text-[10px] text-blue-500 dark:text-blue-300">可同步代码</div>
                          <div className="mt-1 text-[14px] font-semibold text-blue-700 dark:text-blue-200">{localChanges.eligibleCount} 个文件</div>
                        </div>
                        <div className="rounded-xl border border-amber-100 bg-amber-50/60 px-3 py-2.5 dark:border-amber-500/20 dark:bg-amber-500/10">
                          <div className="text-[10px] text-amber-600 dark:text-amber-300">受保护 / 排除</div>
                          <div className="mt-1 text-[14px] font-semibold text-amber-700 dark:text-amber-200">{localChanges.excludedCount} 个文件</div>
                        </div>
                      </div>

                      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-100 px-3 py-2.5 dark:border-slate-700">
                        <div className="text-[11px] text-slate-500 dark:text-slate-300">
                          {localChanges.eligibleCount
                            ? <>代码变化 <strong className="font-semibold text-emerald-600">+{localChanges.totalAdded}</strong> / <strong className="font-semibold text-rose-600">-{localChanges.totalDeleted}</strong>
                                {localChanges.impactedModules?.length ? <> · {localChanges.impactedModules.join("、")}</> : null}
                              </>
                            : "没有可同步的代码修改"}
                        </div>
                        {(localChanges.files.length || localChanges.excludedFiles.length) ? (
                          <button
                            type="button"
                            onClick={() => setShowLocalFiles((value) => !value)}
                            className="text-[11px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-300"
                          >
                            {showLocalFiles ? "收起文件明细" : "查看修改文件"}
                          </button>
                        ) : null}
                      </div>

                      {showLocalFiles && (
                        <div className="mt-3 overflow-hidden rounded-xl border border-slate-100 dark:border-slate-700">
                          {localChanges.files.slice(0, 80).map((item) => (
                            <div key={`safe-${item.path}`} className="grid grid-cols-[34px_minmax(0,1fr)_90px] items-center gap-2 border-b border-slate-100 px-3 py-2 text-[10px] last:border-b-0 dark:border-slate-700">
                              <span className="font-mono font-semibold text-blue-600 dark:text-blue-300">{item.status}</span>
                              <span className="truncate font-mono text-slate-600 dark:text-slate-200" title={item.path}>{item.path}</span>
                              <span className="text-right text-slate-400">
                                {item.added != null || item.deleted != null ? `+${item.added || 0} / -${item.deleted || 0}` : item.tracked ? "已跟踪" : "新文件"}
                              </span>
                            </div>
                          ))}
                          {localChanges.excludedFiles.slice(0, 40).map((item) => (
                            <div key={`blocked-${item.path}`} className="grid grid-cols-[34px_minmax(0,1fr)_150px] items-center gap-2 border-b border-amber-100 bg-amber-50/50 px-3 py-2 text-[10px] last:border-b-0 dark:border-amber-500/20 dark:bg-amber-500/5">
                              <span className="font-mono font-semibold text-amber-600">{item.status}</span>
                              <span className="truncate font-mono text-slate-600 dark:text-slate-200" title={item.path}>{item.path}</span>
                              <span className="truncate text-right text-amber-700 dark:text-amber-200" title={item.excludedReason}>{item.excludedReason || "不会上传"}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {(localSync || localChanges.lastSync) && (
                        <div className="mt-3 rounded-xl border border-emerald-100 bg-emerald-50/60 px-3 py-2.5 text-[11px] leading-5 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                          最近同步：
                          <span className="ml-1 font-mono font-semibold">{(localSync || localChanges.lastSync)?.branch}</span>
                          {" · "}
                          <span className="font-mono">{(localSync || localChanges.lastSync)?.shortSha}</span>
                          {" · "}
                          {fmtDate((localSync || localChanges.lastSync)?.syncedAt)}
                          <div className="mt-0.5 text-[10px] opacity-80">GitHub 与本地 develop 已完成快进同步。</div>
                        </div>
                      )}

                      <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[10px] leading-5 text-slate-500 dark:bg-slate-800/60 dark:text-slate-300">
                        {(localChanges.protectedRules || []).join("；")}
                      </div>
                    </>
                  ) : (
                    <div className="mt-4 rounded-xl border border-slate-100 px-4 py-5 text-[11px] text-slate-400 dark:border-slate-700">
                      正在读取本地 Git 工作区状态…
                    </div>
                  )}

                  {localError && (
                    <div className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200">
                      本地版本检测失败：{localError}
                    </div>
                  )}
                </div>
              </section>
            )}

            <section className="app-card overflow-hidden rounded-2xl">
              <div>
                <div className="min-w-0 p-5">
                  <div className="mb-4 flex flex-wrap items-center gap-2">
                    <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${state.tone}`}>
                      {state.label}
                    </span>
                    {status.updateAvailable && (
                      <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ring-inset ${levelMeta.tone}`}>
                        {levelMeta.label}
                      </span>
                    )}
                    {!isContainer && (
                      <span className="text-[11px] text-slate-400 dark:text-slate-400">
                        {status.settings.remote}/{status.settings.branch}
                      </span>
                    )}
                  </div>

                  <div className="text-[14px] font-semibold tracking-tight text-slate-900 dark:text-slate-100">版本概览</div>
                  <div className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">当前运行版本与 GitHub 最新版本</div>

                  <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 dark:border-slate-700">
                    <VersionRow
                      label="当前版本"
                      version={status.currentCommit?.version}
                      sha={currentSha}
                      note={status.currentCommit?.subject || "当前正在运行的版本"}
                      kind="current"
                      updateLevel={updateLevel}
                      hasUpdate={Boolean(status.updateAvailable)}
                    />
                    <VersionRow
                      label="最新版本"
                      version={status.latestCommit?.version || status.currentCommit?.version}
                      sha={targetSha || currentSha}
                      note={latestSubject || status.currentCommit?.subject || "GitHub 最新版本"}
                      kind="latest"
                      updateLevel={updateLevel}
                      hasUpdate={Boolean(status.updateAvailable)}
                      actions={!isContainer ? (
                        <div className="flex flex-wrap items-center justify-end gap-1.5">
                          <button
                            type="button"
                            onClick={() => void checkNow()}
                            disabled={Boolean(busy || status.running)}
                            className="app-button-secondary h-8 rounded-lg px-2.5 text-[10px] font-medium disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            {busy === "check" ? "检查中…" : "检查更新"}
                          </button>
                          <button
                            type="button"
                            onClick={openUpdateDialog}
                            disabled={status.updateAvailable ? installDisabled : true}
                            className={
                              status.updateAvailable
                                ? "app-button-primary h-8 rounded-lg px-3 text-[10px] font-semibold shadow-none disabled:cursor-not-allowed disabled:opacity-40"
                                : "h-8 cursor-default rounded-lg border border-emerald-200 bg-emerald-50 px-3 text-[10px] font-semibold text-emerald-700 dark:border-emerald-500/30 dark:bg-emerald-500/10 dark:text-emerald-300"
                            }
                          >
                            {status.running ? "同步进行中…" : status.updateAvailable ? "从云端同步到本地" : "当前已是最新版本"}
                          </button>
                          <button
                            type="button"
                            onClick={() => void refreshRuntime()}
                            disabled={Boolean(busy)}
                            className="app-button-secondary h-8 rounded-lg px-2.5 text-[10px] font-medium disabled:opacity-40"
                          >
                            {busy === "refresh" ? "刷新中…" : "刷新状态"}
                          </button>
                        </div>
                      ) : undefined}
                    />
                  </div>

                  <div className={`mt-3 rounded-lg px-3 py-2 text-[11px] leading-5 ${
                    status.updateAvailable
                      ? updateLevel === "major"
                        ? "bg-rose-50 font-semibold text-rose-700 dark:bg-rose-500/10 dark:text-rose-200"
                        : updateLevel === "feature"
                          ? "bg-amber-50 font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-200"
                          : "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-200"
                      : "border border-emerald-100 bg-emerald-50/70 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                  }`}>
                    {status.updateAvailable
                      ? updateLevel === "major"
                        ? "检测到重大版本更新：建议查看影响模块和变更内容后再安装。"
                        : updateLevel === "feature"
                          ? "检测到功能版本更新：包含功能、模型或数据库层面的变化，请确认后安装。"
                          : "检测到小版本更新：通常为 UI、文案或普通缺陷修复。"
                      : "当前版本与 GitHub 最新版本一致，无需更新。"}
                  </div>

                  {reason && reason !== "当前没有待安装的新版本。" && !status.running && (
                    <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-[10px] leading-5 text-slate-500 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
                      {reason}
                    </div>
                  )}

                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-4 dark:border-slate-700/70">
                    <span className="mr-1 text-[11px] font-semibold text-slate-600 dark:text-slate-300">影响模块</span>
                    {(status.impactedModules?.length ? status.impactedModules : ["公共代码"]).map((module) => (
                      <span key={module} className="rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-600 dark:border-slate-600 dark:bg-slate-800/70 dark:text-slate-200">
                        {module}
                      </span>
                    ))}
                    {status.hasMigration && (
                      <span className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                        包含数据库迁移
                      </span>
                    )}
                  </div>

                  <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-700/70">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">本次更新内容</div>
                        <div className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">
                          {status.updateAvailable ? `共 ${status.changes?.length || 1} 项变更` : "当前版本已与远端一致"}
                        </div>
                      </div>
                      {status.changedFileCount ? (
                        <button
                          type="button"
                          onClick={() => setShowChangedFiles((value) => !value)}
                          className="text-[11px] font-medium text-blue-600 hover:text-blue-700 dark:text-blue-300"
                        >
                          {showChangedFiles ? "收起变更文件" : `查看变更文件 · ${status.changedFileCount}`}
                        </button>
                      ) : null}
                    </div>

                    <div className="mt-3 overflow-hidden rounded-xl border border-slate-100 dark:border-slate-700">
                      {visibleChanges.length ? visibleChanges.map((item, index) => (
                        <div key={item.sha} className={`flex items-start gap-3 px-3 py-2.5 ${index ? "border-t border-slate-100 dark:border-slate-700" : ""}`}>
                          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-300">
                            {index + 1}
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-[12px] font-medium text-slate-700 dark:text-slate-100" title={item.subject}>{item.subject}</div>
                            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-400">
                              <span>{fmtDate(item.committedAt)} · {item.shortSha}</span>
                              {(item.modules || []).slice(0, 2).map((module) => (
                                <span key={module} className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-300">
                                  {module}
                                </span>
                              ))}
                            </div>
                          </div>
                        </div>
                      )) : (
                        <div className="flex min-h-[78px] items-center px-4 py-4 text-[12px] text-slate-400 dark:text-slate-400">
                          当前没有待安装更新。检测到新版本后，这里会直接列出本次改动。
                        </div>
                      )}
                    </div>

                    {showChangedFiles && status.changedFiles?.length ? (
                      <div className="mt-3 overflow-hidden rounded-xl border border-slate-100 bg-slate-50/60 dark:border-slate-700 dark:bg-slate-900/30">
                        <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2 dark:border-slate-700">
                          <span className="text-[11px] font-semibold text-slate-700 dark:text-slate-100">本次变更文件</span>
                          <span className="text-[10px] text-slate-400">{status.changedFiles.length} 个</span>
                        </div>
                        <div className="max-h-52 overflow-auto py-1">
                          {status.changedFiles.map((file) => (
                            <div key={file} className="border-b border-slate-100/80 px-3 py-1.5 font-mono text-[10px] leading-5 text-slate-500 last:border-b-0 dark:border-slate-700/70 dark:text-slate-300">
                              {file}
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : null}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-slate-400 dark:text-slate-400">
                    <span>最后检查：{fmtDate(status.lastCheckAt)}</span>
                    {status.changedFileCount ? <span>文件变化：{status.changedFileCount} 个</span> : null}
                    {status.lastInstallAt && <span>上次安装：{fmtDate(status.lastInstallAt)}</span>}
                  </div>
                </div>

              </div>
            </section>

            {error && (
              <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12px] leading-5 text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200">
                <div className="font-semibold">更新服务需要处理</div>
                <div className="mt-1 break-all">{error}</div>
              </div>
            )}
            {notice && !error && (
              <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-[12px] text-blue-700 dark:border-blue-500/30 dark:bg-blue-500/10 dark:text-blue-200">
                {notice}
              </div>
            )}
          </div>

          {!isContainer && (
            <aside className="app-card self-start overflow-hidden rounded-2xl">
              <div className="border-b border-slate-100 px-4 py-4 dark:border-slate-700">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">模块版本总览</h2>
                  {moduleUpdateCount > 0 ? (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-200">
                      {moduleUpdateCount} 个有更新
                    </span>
                  ) : moduleVersions.length ? (
                    <span className="rounded-full border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300">
                      全部最新
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">10 个中心 · 当前版本 / GitHub 最新版本</p>
              </div>

              <div className="grid grid-cols-[112px_minmax(0,1fr)_minmax(0,1fr)_60px] gap-2 border-b border-slate-100 bg-slate-50/55 px-4 py-2 text-[10px] font-medium text-slate-400 dark:border-slate-700 dark:bg-slate-800/45">
                <span>模块</span><span>当前</span><span>GitHub</span><span className="text-right">状态</span>
              </div>

              {moduleVersions.length ? (
                <div className="divide-y divide-slate-100 dark:divide-slate-700">
                  {moduleVersions.map((module, index) => {
                    const hasUpdate = module.status === "update";
                    const tracked = module.status !== "untracked";
                    const severityClass = !hasUpdate
                      ? "border border-emerald-100 bg-emerald-50 text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-300"
                      : updateLevel === "major"
                        ? "bg-rose-50 font-semibold text-rose-700 dark:bg-rose-500/10 dark:text-rose-200"
                        : updateLevel === "feature"
                          ? "bg-amber-50 font-semibold text-amber-700 dark:bg-amber-500/10 dark:text-amber-200"
                          : "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-200";
                    return (
                      <div key={module.key} className="grid grid-cols-[112px_minmax(0,1fr)_minmax(0,1fr)_60px] items-center gap-2 px-4 py-2.5">
                        <div className="truncate text-[11px] font-semibold text-slate-800 dark:text-slate-100">{index + 1}. {module.label}</div>
                        <div className="truncate font-mono text-[10px] text-slate-500 dark:text-slate-300" title={module.currentVersion}>{module.currentVersion || "—"}</div>
                        <div className={`truncate font-mono text-[10px] ${hasUpdate ? "font-semibold text-slate-800 dark:text-slate-100" : "text-slate-500 dark:text-slate-300"}`} title={module.latestVersion}>
                          {module.latestVersion || module.currentVersion || "—"}
                        </div>
                        <div className="text-right">
                          <span className={`inline-flex rounded-full px-1.5 py-0.5 text-[9px] font-medium ${severityClass}`}>
                            {hasUpdate ? (updateLevel === "major" ? "重大" : updateLevel === "feature" ? "功能" : "更新") : tracked ? "最新" : "待建"}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="px-4 py-8 text-center text-[11px] text-slate-400 dark:text-slate-400">正在生成模块版本信息…</div>
              )}

              <div className="border-t border-slate-100 bg-slate-50/60 px-4 py-3 text-[10px] leading-5 text-slate-400 dark:border-slate-700 dark:bg-slate-800/40">
                模块版本表示该中心最近一次代码变更所属的平台版本，不是独立安装包。
              </div>
            </aside>
          )}
        </div>
      )}

      {activeTab === "strategy" && (
        <section className="app-card rounded-2xl p-5">
          <div className="mb-4">
            <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">更新策略</h2>
            <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">非日常设置；调整检查频率、安装方式和自动安装范围。</p>
          </div>
          {!isContainer ? (
            <section className="rounded-xl border border-slate-100 p-4 dark:border-slate-700">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">自动更新策略</h3>
                  <p className="mt-1 text-[11px] leading-5 text-slate-400 dark:text-slate-400">日常建议保持自动检查，安装由你确认。</p>
                </div>
                <label className="flex items-center gap-2 text-[11px] text-slate-600 dark:text-slate-300">
                  <input type="checkbox" checked={draft.enabled} onChange={(event) => patchDraft({ enabled: event.target.checked })} />
                  启用
                </label>
              </div>

              <div className="mt-3 grid grid-cols-3 gap-2">
                {(Object.keys(MODE_COPY) as SystemUpdateMode[]).map((mode) => (
                  <button
                    type="button"
                    key={mode}
                    onClick={() => patchDraft({ mode })}
                    className={`rounded-lg border px-2 py-2.5 text-center text-[11px] font-semibold transition ${draft.mode === mode ? "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-400/50 dark:bg-blue-500/10 dark:text-blue-200" : "border-slate-200 text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"}`}
                  >
                    {MODE_COPY[mode].title}
                  </button>
                ))}
              </div>
              <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500 dark:bg-slate-800/70 dark:text-slate-300">{MODE_COPY[draft.mode].desc}</div>

              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <label className="text-[11px] text-slate-500 dark:text-slate-300">检查间隔
                  <div className="mt-1 flex items-center gap-1.5"><input type="number" min={5} max={1440} value={draft.checkIntervalMinutes} onChange={(event) => patchDraft({ checkIntervalMinutes: Number(event.target.value || 10) })} className="app-input-control w-full rounded-lg px-3 py-2 text-xs" /><span>分钟</span></div>
                </label>
                <label className="text-[11px] text-slate-500 dark:text-slate-300">自动安装时间
                  <div className="mt-1 flex items-center gap-1.5"><input type="number" min={0} max={23} value={draft.autoUpdateHour} onChange={(event) => patchDraft({ autoUpdateHour: Number(event.target.value || 0) })} className="app-input-control w-full rounded-lg px-3 py-2 text-xs" /><span>点</span></div>
                </label>
                <label className="text-[11px] text-slate-500 dark:text-slate-300">自动安装范围
                  <select value={draft.autoInstallLevel} onChange={(event) => patchDraft({ autoInstallLevel: event.target.value as SystemUpdateLevel })} className="app-input-control mt-1 w-full rounded-lg px-3 py-2 text-xs" disabled={draft.mode !== "auto_update"}>
                    <option value="patch">小版本</option><option value="feature">功能版本</option><option value="major">所有版本</option>
                  </select>
                </label>
              </div>

              <div className="mt-3 text-[11px] text-slate-400 dark:text-slate-400">当前自动安装范围：{autoInstallLevelMeta.label}及以下。</div>
              <button type="button" onClick={() => void saveSettings()} disabled={!saveChanged || Boolean(busy)} className="app-button-primary mt-4 w-full rounded-lg px-3 py-2.5 text-[11px] font-medium disabled:opacity-40">
                {busy === "save" ? "保存中…" : saveChanged ? "保存更新策略" : "更新策略已保存"}
              </button>
            </section>
          ) : (
            <div className="rounded-xl border border-slate-100 p-4 text-[11px] text-slate-500 dark:border-slate-700 dark:text-slate-300">
              当前为容器托管模式，版本由 GitHub / GHCR / Compose 管理。
            </div>
          )}
        </section>
      )}

      {activeTab === "logs" && (
        <section className="app-card overflow-hidden rounded-2xl">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-5 py-4 dark:border-slate-700">
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">更新日志</h2>
                {status.running && <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-medium text-blue-700 dark:bg-blue-500/10 dark:text-blue-200">实时更新</span>}
                {status.backupDb && <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">已生成备份</span>}
              </div>
              <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">一般无需查看；更新失败或排查问题时再进入此页签。</p>
            </div>
            <div className="flex items-center gap-2">
              <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-300">
                <input type="checkbox" checked={autoScrollLogs} onChange={(event) => setAutoScrollLogs(event.target.checked)} className="h-3.5 w-3.5 rounded border-slate-300" />
                自动滚动
              </label>
              <button type="button" onClick={() => void load(true)} className="app-button-secondary h-8 rounded-lg px-2.5 text-[11px] font-medium">刷新</button>
              <button
                type="button"
                onClick={() => {
                  const text = (status.logs || []).join("\n");
                  if (!text) return;
                  void navigator.clipboard.writeText(text).then(
                    () => setNotice("更新日志已复制。"),
                    () => setNotice("复制失败，请手动选择日志内容。"),
                  );
                }}
                disabled={!status.logs?.length}
                className="app-button-secondary h-8 rounded-lg px-2.5 text-[11px] font-medium disabled:opacity-40"
              >
                复制日志
              </button>
            </div>
          </div>
          <div className="flex items-center gap-4 border-b border-slate-200 bg-slate-50/70 px-5 py-3 text-[11px] text-slate-500 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300">
            <span>{status.logs?.length || 0} 条记录</span>
            <span>当前阶段：<strong className="font-medium text-slate-700 dark:text-slate-100">{stageMessage}</strong></span>
          </div>
          <pre ref={logRef} className="min-h-[420px] max-h-[68vh] overflow-auto whitespace-pre-wrap break-all bg-slate-950 px-5 py-4 font-mono text-[11px] leading-6 text-slate-300">
            {(status.logs || []).join("\n") || "暂无执行日志。开始更新后，这里会显示环境检查、备份、安装、迁移、构建、重启和验证记录。"}
          </pre>
        </section>
      )}

      {activeTab === "history" && (
        <section className="app-card rounded-2xl p-5">
          <div className="mb-4">
            <h2 className="text-[15px] font-semibold text-slate-900 dark:text-slate-100">历史记录</h2>
            <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">查看历史安装、回滚和版本切换记录。</p>
          </div>
          <section className="rounded-xl border border-slate-100 dark:border-slate-700">
            <div className="border-b border-slate-100 px-4 py-3 text-[13px] font-semibold text-slate-800 dark:border-slate-700 dark:text-slate-100">历史更新记录</div>
            <div className="max-h-[340px] divide-y divide-slate-100 overflow-auto px-4 dark:divide-slate-700">
              {(status.history || []).slice(0, 10).map((item, index) => (
                <div key={item.at + index} className="py-3">
                  <div className="flex items-center justify-between gap-3">
                    <span className={`text-[11px] font-medium ${item.result === "success" ? "text-emerald-700 dark:text-emerald-300" : item.result === "rolled_back" ? "text-amber-700 dark:text-amber-200" : "text-rose-700 dark:text-rose-200"}`}>{item.message}</span>
                    <span className="shrink-0 text-[11px] text-slate-400">{fmtDate(item.at)}</span>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-slate-400">{shortSha(item.fromSha)} → {shortSha(item.toSha)} · {item.actor}</div>
                  {item.error && <div className="mt-1 text-[11px] text-rose-600 dark:text-rose-300">{item.error}</div>}
                </div>
              ))}
              {(!status.history || status.history.length === 0) && <div className="py-10 text-center text-xs text-slate-400">还没有更新记录</div>}
            </div>
          </section>
        </section>
      )}
      {updateDialogOpen && !isContainer && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/45 p-5 backdrop-blur-sm">
          <div
            role="dialog"
            aria-modal="true"
            aria-label="系统更新"
            className="flex max-h-[calc(100vh-40px)] w-full max-w-5xl flex-col overflow-hidden rounded-[24px] border border-white/50 bg-white shadow-2xl dark:border-slate-700 dark:bg-slate-900"
          >
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-7 py-5 dark:border-slate-700">
              <div>
                <div className="flex items-center gap-2">
                  <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${
                    updateDialogResult === "failed" || updateDialogResult === "rolled_back"
                      ? "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300"
                      : updateDialogResult === "success"
                        ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300"
                        : "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-300"
                  }`}>
                    {updateDialogResult === "success" ? "✓" : updateDialogResult === "failed" || updateDialogResult === "rolled_back" ? "!" : "↻"}
                  </span>
                  <div>
                    <h2 className="text-[19px] font-semibold tracking-tight text-slate-900 dark:text-slate-100">
                      {updateDialogResult === "confirm"
                        ? "准备安装系统更新"
                        : updateDialogResult === "success"
                          ? "更新完成"
                          : updateDialogResult === "failed"
                            ? "更新失败"
                            : updateDialogResult === "rolled_back"
                              ? "更新未完成，已回滚"
                              : "正在更新系统"}
                    </h2>
                    <p className="mt-1 text-[11px] text-slate-400 dark:text-slate-400">
                      {updateDialogResult === "confirm"
                        ? "开始后系统会依次备份、安装、迁移并验证新版本。"
                        : status.message || "系统正在执行安全更新流程。"}
                    </p>
                  </div>
                </div>
              </div>
              {updateDialogResult !== "running" && (
                <button
                  type="button"
                  onClick={closeUpdateDialog}
                  className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-100"
                  aria-label="关闭"
                >
                  ×
                </button>
              )}
            </div>

            <div className="overflow-auto px-7 py-6">
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_36px_minmax(0,1fr)]">
                <div className="rounded-xl border border-slate-200 px-4 py-3 dark:border-slate-700">
                  <div className="text-[10px] text-slate-400">当前版本</div>
                  <div className="mt-1 font-mono text-[17px] font-semibold text-slate-800 dark:text-slate-100">{status.currentCommit?.version || shortSha(currentSha)}</div>
                </div>
                <div className="hidden items-center justify-center text-lg text-slate-300 sm:flex">→</div>
                <div className="rounded-xl border border-blue-200 bg-blue-50/50 px-4 py-3 dark:border-blue-500/30 dark:bg-blue-500/5">
                  <div className="text-[10px] text-blue-500 dark:text-blue-300">目标版本</div>
                  <div className="mt-1 font-mono text-[17px] font-semibold text-slate-800 dark:text-slate-100">{status.latestCommit?.version || shortSha(targetSha)}</div>
                </div>
              </div>

              {updateDialogResult === "confirm" ? (
                <div className="mt-6">
                  <div className="rounded-2xl border border-slate-100 bg-slate-50/70 p-5 dark:border-slate-700 dark:bg-slate-800/50">
                    <div className="text-[13px] font-semibold text-slate-800 dark:text-slate-100">更新前确认</div>
                    <div className="mt-3 grid gap-3 text-[11px] text-slate-500 sm:grid-cols-3 dark:text-slate-300">
                      <div><span className="block text-slate-400">变更级别</span><strong className="mt-1 block font-semibold text-slate-700 dark:text-slate-100">{levelMeta.label}</strong></div>
                      <div><span className="block text-slate-400">变更文件</span><strong className="mt-1 block font-semibold text-slate-700 dark:text-slate-100">{status.changedFileCount || 0} 个</strong></div>
                      <div><span className="block text-slate-400">影响模块</span><strong className="mt-1 block truncate font-semibold text-slate-700 dark:text-slate-100">{status.impactedModules?.join("、") || "公共代码"}</strong></div>
                    </div>
                    <p className="mt-4 text-[11px] leading-5 text-slate-400">
                      系统会先备份数据库和业务文件，再更新代码、执行数据库迁移、重建前端并进行健康检查；失败时自动尝试回滚。
                    </p>
                  </div>

                  {error && (
                    <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[11px] text-rose-700 dark:border-rose-500/40 dark:bg-rose-500/10 dark:text-rose-200">{error}</div>
                  )}

                  <div className="mt-6 flex justify-end gap-3">
                    <button type="button" onClick={closeUpdateDialog} className="app-button-secondary h-11 rounded-xl px-5 text-[12px] font-medium">取消</button>
                    <button type="button" onClick={() => void startUpdate()} disabled={busy === "apply"} className="app-button-primary h-11 rounded-xl px-6 text-[12px] font-semibold disabled:opacity-50">
                      {busy === "apply" ? "正在启动更新…" : "确认并开始更新"}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <div className="mt-7 text-center">
                    <div className={`font-mono text-[54px] font-semibold tracking-[-0.04em] ${
                      updateDialogResult === "failed" || updateDialogResult === "rolled_back"
                        ? "text-rose-600 dark:text-rose-300"
                        : updateDialogResult === "success"
                          ? "text-emerald-600 dark:text-emerald-300"
                          : "text-slate-900 dark:text-slate-100"
                    }`}>
                      {updateDialogProgress}%
                    </div>
                    <div className="mx-auto mt-4 h-3 max-w-3xl overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${
                          updateDialogResult === "failed" || updateDialogResult === "rolled_back"
                            ? "bg-rose-500"
                            : updateDialogResult === "success"
                              ? "bg-emerald-500"
                              : "bg-blue-600"
                        }`}
                        style={{ width: `${updateDialogProgress}%` }}
                      />
                    </div>
                    <div className="mt-3 text-[13px] font-semibold text-slate-700 dark:text-slate-100">{stageMessage}</div>
                    <div className="mt-1 text-[11px] text-slate-400">{status.message || "正在执行更新任务…"}</div>
                  </div>

                  <div className="mt-7 grid overflow-hidden rounded-2xl border border-slate-100 md:grid-cols-4 dark:border-slate-700">
                    <UpdateStep index={1} title="环境检查" desc="环境、依赖、版本" status={stageStatus(0)} time={stageStatus(0) === "done" ? "已通过" : ""} />
                    <UpdateStep index={2} title="自动备份" desc="数据库与业务文件" status={stageStatus(1)} time={status.backupDb ? "备份已生成" : ""} />
                    <UpdateStep index={3} title="安装升级" desc="代码、依赖、数据库" status={stageStatus(2)} time={status.running && activeStep === 2 ? status.message : ""} />
                    <UpdateStep index={4} title="验证 / 回滚" desc="健康检查与服务重启" status={stageStatus(3)} time={status.running && activeStep === 3 ? status.message : ""} last />
                  </div>

                  <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
                    <div className="text-[10px] text-slate-400">
                      {status.running ? "更新任务已进入后台执行器；请勿手动重启 API / Worker / 数据库。" : updateDialogResult === "success" ? "验证通过，页面即将自动载入新版。" : "可查看日志定位本次更新结果。"}
                    </div>
                    <div className="flex items-center gap-2">
                      <button type="button" onClick={() => setShowModalLogs((value) => !value)} className="app-button-secondary h-9 rounded-lg px-3 text-[11px] font-medium">
                        {showModalLogs ? "收起日志" : `查看日志${status.logs?.length ? ` · ${status.logs.length}` : ""}`}
                      </button>
                      {updateDialogResult !== "running" && updateDialogResult !== "success" && (
                        <button type="button" onClick={closeUpdateDialog} className="app-button-primary h-9 rounded-lg px-4 text-[11px] font-medium">返回更新页面</button>
                      )}
                    </div>
                  </div>

                  {showModalLogs && (
                    <pre className="mt-4 max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-slate-950 px-4 py-3 font-mono text-[10px] leading-5 text-slate-300">
                      {(status.logs || []).slice(-60).join("\n") || "暂时还没有更新日志。"}
                    </pre>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

function VersionRow({
  label,
  version,
  sha,
  note,
  kind,
  updateLevel,
  hasUpdate,
  actions,
}: {
  label: string;
  version?: string | null;
  sha?: string | null;
  note: string;
  kind: "current" | "latest";
  updateLevel: SystemUpdateLevel;
  hasUpdate: boolean;
  actions?: ReactNode;
}) {
  const latest = kind === "latest";
  let rowTone = latest
    ? "bg-emerald-50/30 dark:bg-emerald-500/5"
    : "bg-white dark:bg-slate-900";
  let versionTone = "text-slate-900 dark:text-slate-100";
  let badgeTone = latest
    ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
    : "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-300";
  let badgeText = latest ? "最新版本" : "当前运行中";
  let weight = "font-semibold";

  if (latest && hasUpdate) {
    if (updateLevel === "major") {
      rowTone = "bg-rose-50/70 dark:bg-rose-500/10";
      versionTone = "text-rose-800 dark:text-rose-100";
      badgeTone = "bg-rose-100 font-semibold text-rose-700 dark:bg-rose-400/15 dark:text-rose-200";
      badgeText = "重大版本";
      weight = "font-bold";
    } else if (updateLevel === "feature") {
      rowTone = "bg-amber-50/70 dark:bg-amber-500/10";
      versionTone = "text-amber-800 dark:text-amber-100";
      badgeTone = "bg-amber-100 font-semibold text-amber-700 dark:bg-amber-400/15 dark:text-amber-200";
      badgeText = "功能版本";
      weight = "font-bold";
    } else {
      rowTone = "bg-blue-50/60 dark:bg-blue-500/10";
      versionTone = "text-blue-800 dark:text-blue-100";
      badgeTone = "bg-blue-100 text-blue-700 dark:bg-blue-400/15 dark:text-blue-200";
      badgeText = "小版本";
    }
  }

  return (
    <div className={`grid grid-cols-[78px_minmax(0,1fr)] items-center gap-x-3 px-4 py-3.5 first:border-b first:border-slate-100 lg:grid-cols-[78px_minmax(0,1fr)_auto] dark:first:border-slate-700 ${rowTone}`}>
      <div className="text-[11px] font-medium text-slate-500 dark:text-slate-300">{label}</div>
      <div className="min-w-0">
        <div className={`font-mono text-[17px] tracking-[-0.02em] ${weight} ${versionTone}`}>{version || shortSha(sha)}</div>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-[10px] text-slate-400">
          <span className="shrink-0 font-mono">{shortSha(sha)}</span>
          <span className="truncate" title={note}>{note}</span>
        </div>
      </div>
      <div className="col-span-2 mt-2 flex justify-end lg:col-span-1 lg:mt-0">
        {actions || <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${badgeTone}`}>{badgeText}</span>}
      </div>
    </div>
  );
}

function UpdateStep({
  index,
  title,
  desc,
  status,
  time,
  last = false,
}: {
  index: number;
  title: string;
  desc: string;
  status: "done" | "active" | "pending" | "error";
  time?: string;
  last?: boolean;
}) {
  const circle = status === "done"
    ? "bg-emerald-500 text-white"
    : status === "active"
      ? "bg-blue-600 text-white ring-4 ring-blue-100 dark:ring-blue-500/20"
      : status === "error"
        ? "bg-rose-500 text-white"
        : "bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-300";
  const statusText = status === "done" ? "已完成" : status === "active" ? "进行中…" : status === "error" ? "需要处理" : "等待中";
  return (
    <div className={`relative px-5 py-4 ${last ? "" : "border-b border-slate-100 md:border-b-0 md:border-r dark:border-slate-700"}`}>
      <div className="flex items-start gap-3">
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${circle}`}>
          {status === "done" ? "✓" : index}
        </span>
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-slate-800 dark:text-slate-100">{index}　{title}</div>
          <div className="mt-1 text-[11px] leading-5 text-slate-400 dark:text-slate-400">{desc}</div>
          <div className={`mt-1.5 text-[11px] font-medium ${status === "done" ? "text-emerald-600 dark:text-emerald-300" : status === "active" ? "text-blue-600 dark:text-blue-300" : status === "error" ? "text-rose-600 dark:text-rose-300" : "text-slate-400"}`}>
            {time || statusText}
          </div>
        </div>
      </div>
    </div>
  );
}
