"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  getBackupStatus,
  getKodoColdBackupConfig,
  getR2BackupConfig,
  getWebdavBackupConfig,
  prepareR2Restore,
  runR2Backup,
  saveKodoColdBackupConfig,
  saveR2BackupConfig,
  saveWebdavBackupConfig,
  testKodoColdBackupWrite,
  testR2BackupConnection,
  testWebdavBackupConnection,
  type BackupStatus,
  type KodoColdBackupConfig,
  type R2BackupConfig,
  type WebdavBackupConfig,
} from "@/lib/api";

type TabKey = "overview" | "restore" | "records";
type Tone = "green" | "blue" | "amber" | "slate";
type StorageKey = "r2" | "kodo" | "webdav" | "nas";

function Pill({
  children,
  tone = "slate",
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  const cls =
    tone === "green"
      ? "bg-emerald-50 text-emerald-700"
      : tone === "blue"
        ? "bg-blue-50 text-blue-700"
        : tone === "amber"
          ? "bg-amber-50 text-amber-700"
          : "bg-slate-100 text-slate-500";
  return <span className={"rounded-full px-2 py-0.5 text-[10px] font-medium " + cls}>{children}</span>;
}

function ArchitectureItem({
  icon,
  title,
  desc,
  tone = "green",
  status,
  statusTone = "slate",
  onConfigure,
}: {
  icon: "app" | "db" | "folder" | "settings" | "sync" | "shield" | "cloud" | "nas" | "box";
  title: string;
  desc: string;
  tone?: Tone;
  status?: string;
  statusTone?: Tone;
  onConfigure?: () => void;
}) {
  const toneCls =
    tone === "green"
      ? "bg-emerald-50 text-emerald-600"
      : tone === "blue"
        ? "bg-blue-50 text-blue-600"
        : tone === "amber"
          ? "bg-amber-50 text-amber-600"
          : "bg-slate-100 text-slate-500";

  const iconNode =
    icon === "db" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <ellipse cx="12" cy="5.5" rx="6.5" ry="2.5" stroke="currentColor" strokeWidth="1.8" />
        <path d="M5.5 5.5v6c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-6M5.5 11.5v6c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-6" stroke="currentColor" strokeWidth="1.8" />
      </svg>
    ) : icon === "folder" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <path d="M3.5 6.5h6l1.8 2H20a1.5 1.5 0 0 1 1.5 1.5v7.5A2.5 2.5 0 0 1 19 20H5a2.5 2.5 0 0 1-2.5-2.5V8A1.5 1.5 0 0 1 4 6.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
    ) : icon === "settings" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.8" />
        <path d="M12 3.5v2M12 18.5v2M20.5 12h-2M5.5 12h-2M18 6l-1.4 1.4M7.4 16.6 6 18M18 18l-1.4-1.4M7.4 7.4 6 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    ) : icon === "sync" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <path d="M18.5 8A7 7 0 0 0 6 7l-1.5 1.5M5.5 4.5v4h4M5.5 16A7 7 0 0 0 18 17l1.5-1.5M18.5 19.5v-4h-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ) : icon === "shield" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <path d="M12 3 19 6v5.5c0 4.4-2.9 7.5-7 9.5-4.1-2-7-5.1-7-9.5V6l7-3Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
        <path d="m9 12 2 2 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    ) : icon === "cloud" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <path d="M7 18.5h10a4 4 0 0 0 .8-7.9A6 6 0 0 0 6.2 9.2 4.7 4.7 0 0 0 7 18.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
    ) : icon === "nas" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <rect x="4" y="5" width="16" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.8" />
        <rect x="4" y="13" width="16" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.8" />
        <path d="M8 8h.01M8 16h.01M12 8h4M12 16h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    ) : icon === "box" ? (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <path d="M4 7.5 12 4l8 3.5V17l-8 3-8-3V7.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
        <path d="m4.5 7.7 7.5 3.2 7.5-3.2M12 10.9v9" stroke="currentColor" strokeWidth="1.8" />
      </svg>
    ) : (
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
        <rect x="5" y="4" width="14" height="16" rx="2" stroke="currentColor" strokeWidth="1.8" />
        <path d="M8 8h8M8 12h8M8 16h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );

  return (
    <div className="flex items-center gap-3 rounded-lg bg-white px-3 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <span className={"flex h-9 w-9 shrink-0 items-center justify-center rounded-lg " + toneCls}>{iconNode}</span>
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold text-slate-800">{title}</div>
        <div className="mt-0.5 text-[9px] leading-4 text-slate-400">{desc}</div>
      </div>
      {status ? <Pill tone={statusTone}>{status}</Pill> : null}
      {onConfigure ? (
        <button
          type="button"
          onClick={onConfigure}
          className="shrink-0 rounded-md border border-slate-200 bg-white px-3 py-1.5 text-[9px] font-medium text-slate-600 hover:bg-slate-50"
        >
          配置
        </button>
      ) : null}
    </div>
  );
}

function StrategyOverviewCard({
  icon,
  title,
  tone,
  status,
  children,
}: {
  icon: "sync" | "db";
  title: string;
  tone: "green" | "blue";
  status: string;
  children: ReactNode;
}) {
  const shell = tone === "green" ? "border-emerald-100 bg-white" : "border-blue-100 bg-white";
  const iconCls = tone === "green" ? "bg-emerald-50 text-emerald-600" : "bg-blue-50 text-blue-600";
  return (
    <div className={"rounded-xl border p-3 " + shell}>
      <div className="flex items-start gap-3">
        <span className={"flex h-8 w-8 shrink-0 items-center justify-center rounded-lg " + iconCls}>
          {icon === "sync" ? (
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
              <path d="M18.5 8A7 7 0 0 0 6 7l-1.5 1.5M5.5 4.5v4h4M5.5 16A7 7 0 0 0 18 17l1.5-1.5M18.5 19.5v-4h-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5" aria-hidden="true">
              <ellipse cx="12" cy="5.5" rx="6.5" ry="2.5" stroke="currentColor" strokeWidth="1.8" />
              <path d="M5.5 5.5v6c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-6M5.5 11.5v6c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-6" stroke="currentColor" strokeWidth="1.8" />
            </svg>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="text-[11px] font-semibold text-slate-900">{title}</div>
            <Pill tone={tone}>{status}</Pill>
          </div>
          <div className="mt-1.5">{children}</div>
        </div>
      </div>
    </div>
  );
}

const STORAGE_DETAILS: Record<StorageKey, { title: string; role: string; desc: string }> = {
  r2: {
    title: "Cloudflare R2",
    role: "主存储",
    desc: "标准配置只保留必要的 ID 与密钥；技术参数统一放到高级配置。",
  },
  kodo: {
    title: "七牛云 Kodo",
    role: "国内冷备 · 只写入",
    desc: "标准配置只保留 Access Key 与 Secret Key；技术参数统一放到高级配置。",
  },
  webdav: {
    title: "坚果云 WebDAV",
    role: "异地备份 · 可读写",
    desc: "使用坚果云第三方应用密码；备份写入独立远程目录，不影响其他存储目标。",
  },
  nas: {
    title: "本地 NAS",
    role: "可选冷备",
    desc: "作为第三副本或本地快速恢复来源。",
  },
};

function backupTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function BackupSettingsPage() {
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [selectedStorage, setSelectedStorage] = useState<StorageKey>("r2");
  const [storageModalOpen, setStorageModalOpen] = useState(false);
  const [advancedConfigOpen, setAdvancedConfigOpen] = useState(false);
  const [notice, setNotice] = useState("");

  const [backupStatus, setBackupStatus] = useState<BackupStatus | null>(null);
  const [selectedRestoreKey, setSelectedRestoreKey] = useState("");
  const [restorePreparing, setRestorePreparing] = useState(false);

  const [r2Config, setR2Config] = useState<R2BackupConfig | null>(null);
  const [r2Loading, setR2Loading] = useState(true);
  const [r2Saving, setR2Saving] = useState(false);
  const [r2Running, setR2Running] = useState(false);
  const [r2Form, setR2Form] = useState({
    accountId: "",
    endpointUrl: "",
    bucket: "ecommerce-workspace-backup",
    prefix: "ecommerce-workspace/backup",
    accessKey: "",
    secretKey: "",
    enabled: true,
    fullIntervalDays: 10,
  });

  const [kodoConfig, setKodoConfig] = useState<KodoColdBackupConfig | null>(null);
  const [kodoLoading, setKodoLoading] = useState(true);
  const [kodoSaving, setKodoSaving] = useState(false);
  const [kodoForm, setKodoForm] = useState({
    bucket: "ecommerce-workspace-cold",
    uploadUrl: "https://upload.qiniup.com",
    prefix: "ecommerce-workspace/cold",
    accessKey: "",
    secretKey: "",
    enabled: true,
  });

  const [webdavConfig, setWebdavConfig] = useState<WebdavBackupConfig | null>(null);
  const [webdavLoading, setWebdavLoading] = useState(true);
  const [webdavSaving, setWebdavSaving] = useState(false);
  const [webdavForm, setWebdavForm] = useState({
    baseUrl: "https://dav.jianguoyun.com/dav/",
    remotePath: "ecommerce-workspace/webdav",
    username: "",
    appPassword: "",
    enabled: true,
  });

  const tabs = useMemo(
    () =>
      [
        ["overview", "概览"],
        ["restore", "恢复管理"],
        ["records", "备份记录"],
      ] as Array<[TabKey, string]>,
    [],
  );

  const r2FullRestorePoints = useMemo(
    () => (backupStatus?.records || []).filter((row) => row.type === "r2_full" && Boolean(row.snapshotObjectKey)),
    [backupStatus],
  );

  function showNotice(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 3600);
  }

  function openStorage(key: StorageKey) {
    setSelectedStorage(key);
    setAdvancedConfigOpen(false);
    setStorageModalOpen(true);
  }

  function closeStorageModal() {
    setStorageModalOpen(false);
    setAdvancedConfigOpen(false);
  }

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      getBackupStatus()
        .then((status) => {
          if (!cancelled) setBackupStatus(status);
        })
        .catch(() => {});
    };
    load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getWebdavBackupConfig()
      .then((config) => {
        if (cancelled) return;
        setWebdavConfig(config);
        setWebdavForm((current) => ({
          ...current,
          baseUrl: config.baseUrl || "https://dav.jianguoyun.com/dav/",
          remotePath: config.remotePath || "ecommerce-workspace/webdav",
          username: config.username || "",
          enabled: config.configured ? config.enabled : true,
          appPassword: "",
        }));
      })
      .catch(() => {
        if (!cancelled) setWebdavConfig(null);
      })
      .finally(() => {
        if (!cancelled) setWebdavLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getR2BackupConfig()
      .then((config) => {
        if (cancelled) return;
        const endpointUrl = config.endpointUrl || "";
        const accountMatch = endpointUrl.match(/^https?:\/\/([^.]+)\.r2\.cloudflarestorage\.com/i);
        setR2Config(config);
        setR2Form((current) => ({
          ...current,
          accountId: accountMatch?.[1] || "",
          endpointUrl,
          bucket: config.bucket || "ecommerce-workspace-backup",
          prefix: config.prefix || "ecommerce-workspace/backup",
          enabled: config.configured ? config.enabled : true,
          fullIntervalDays: config.fullIntervalDays || 10,
          accessKey: "",
          secretKey: "",
        }));
      })
      .catch(() => {
        if (!cancelled) setR2Config(null);
      })
      .finally(() => {
        if (!cancelled) setR2Loading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    getKodoColdBackupConfig()
      .then((config) => {
        if (cancelled) return;
        setKodoConfig(config);
        setKodoForm((current) => ({
          ...current,
          bucket: config.bucket || "ecommerce-workspace-cold",
          uploadUrl: config.uploadUrl || "https://upload.qiniup.com",
          prefix: config.prefix || "ecommerce-workspace/cold",
          enabled: config.configured ? config.enabled : true,
          accessKey: "",
          secretKey: "",
        }));
      })
      .catch(() => {
        if (!cancelled) setKodoConfig(null);
      })
      .finally(() => {
        if (!cancelled) setKodoLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveR2Config() {
    const accountId = r2Form.accountId.trim();
    const endpointUrl =
      r2Form.endpointUrl.trim() ||
      (accountId ? `https://${accountId}.r2.cloudflarestorage.com` : "");

    if (!endpointUrl || !r2Form.bucket.trim()) {
      showNotice("请填写账户 ID；特殊 Endpoint 或 Bucket 可在高级配置中调整。");
      return;
    }
    if (!r2Config?.configured && (!r2Form.accessKey.trim() || !r2Form.secretKey.trim())) {
      showNotice("首次配置需要填写 Access Key ID 和 Secret Access Key。");
      return;
    }
    if (Boolean(r2Form.accessKey.trim()) !== Boolean(r2Form.secretKey.trim())) {
      showNotice("Access Key ID 与 Secret Access Key 需要同时填写。");
      return;
    }

    setR2Saving(true);
    try {
      const saved = await saveR2BackupConfig({
        endpointUrl,
        bucket: r2Form.bucket.trim(),
        prefix: r2Form.prefix.trim() || "ecommerce-workspace/backup",
        accessKey: r2Form.accessKey.trim(),
        secretKey: r2Form.secretKey.trim(),
        enabled: r2Form.enabled,
        fullIntervalDays: Math.max(1, Math.min(365, Number(r2Form.fullIntervalDays) || 10)),
      });
      setR2Config(saved);
      setR2Form((current) => ({
        ...current,
        accountId: saved.endpointUrl.match(/^https?:\/\/([^.]+)\.r2\.cloudflarestorage\.com/i)?.[1] || current.accountId,
        endpointUrl: saved.endpointUrl,
        bucket: saved.bucket,
        prefix: saved.prefix,
        enabled: saved.enabled,
        fullIntervalDays: saved.fullIntervalDays,
        accessKey: "",
        secretKey: "",
      }));

      try {
        await testR2BackupConnection();
        closeStorageModal();
        showNotice("R2 配置已保存，连接测试通过。");
      } catch (error) {
        setAdvancedConfigOpen(true);
        showNotice("凭证已保存，但标准参数未通过连接测试，请检查高级配置：" + (error instanceof Error ? error.message : String(error)));
      }
    } catch (error) {
      showNotice("保存失败：" + (error instanceof Error ? error.message : String(error)));
    } finally {
      setR2Saving(false);
    }
  }

  async function saveKodoConfig() {
    if (!kodoForm.bucket.trim() || !kodoForm.uploadUrl.trim()) {
      showNotice("标准参数缺失，请展开高级配置检查 Bucket 和上传域名。");
      return;
    }
    if (!kodoConfig?.configured && (!kodoForm.accessKey.trim() || !kodoForm.secretKey.trim())) {
      showNotice("首次配置需要填写 Access Key 和 Secret Key。");
      return;
    }
    if (Boolean(kodoForm.accessKey.trim()) !== Boolean(kodoForm.secretKey.trim())) {
      showNotice("Access Key 与 Secret Key 需要同时填写。");
      return;
    }

    setKodoSaving(true);
    try {
      const saved = await saveKodoColdBackupConfig({
        bucket: kodoForm.bucket.trim(),
        uploadUrl: kodoForm.uploadUrl.trim(),
        prefix: kodoForm.prefix.trim() || "ecommerce-workspace/cold",
        accessKey: kodoForm.accessKey.trim(),
        secretKey: kodoForm.secretKey.trim(),
        enabled: kodoForm.enabled,
      });
      setKodoConfig(saved);
      setKodoForm((current) => ({
        ...current,
        bucket: saved.bucket,
        uploadUrl: saved.uploadUrl,
        prefix: saved.prefix,
        enabled: saved.enabled,
        accessKey: "",
        secretKey: "",
      }));

      try {
        await testKodoColdBackupWrite();
        closeStorageModal();
        showNotice("Kodo 配置已保存，只写测试通过。");
      } catch (error) {
        setAdvancedConfigOpen(true);
        showNotice("凭证已保存，但标准参数未通过只写测试，请检查高级配置：" + (error instanceof Error ? error.message : String(error)));
      }
    } catch (error) {
      showNotice("保存失败：" + (error instanceof Error ? error.message : String(error)));
    } finally {
      setKodoSaving(false);
    }
  }

  async function saveWebdavConfig() {
    if (!webdavForm.baseUrl.trim() || !webdavForm.remotePath.trim() || !webdavForm.username.trim()) {
      showNotice("请填写 WebDAV 服务器地址、远程目录和账号。");
      return;
    }
    if (!webdavConfig?.configured && !webdavForm.appPassword.trim()) {
      showNotice("首次配置需要填写坚果云应用密码。");
      return;
    }

    setWebdavSaving(true);
    try {
      const saved = await saveWebdavBackupConfig({
        baseUrl: webdavForm.baseUrl.trim(),
        remotePath: webdavForm.remotePath.trim(),
        username: webdavForm.username.trim(),
        appPassword: webdavForm.appPassword.trim(),
        enabled: webdavForm.enabled,
      });
      setWebdavConfig(saved);
      setWebdavForm((current) => ({
        ...current,
        baseUrl: saved.baseUrl,
        remotePath: saved.remotePath,
        username: saved.username,
        appPassword: "",
        enabled: saved.enabled,
      }));

      try {
        await testWebdavBackupConnection();
        closeStorageModal();
        showNotice("坚果云 WebDAV 已保存，连接、写入、读取和清理测试通过。");
      } catch (error) {
        setAdvancedConfigOpen(true);
        showNotice("凭据已保存，但 WebDAV 测试未通过，请检查服务器地址、远程目录和应用密码：" + (error instanceof Error ? error.message : String(error)));
      }
    } catch (error) {
      showNotice("保存失败：" + (error instanceof Error ? error.message : String(error)));
    } finally {
      setWebdavSaving(false);
    }
  }

  async function runR2() {
    if (!r2Config?.configured || !r2Config.enabled) {
      openStorage("r2");
      showNotice("请先完成 R2 配置。");
      return;
    }
    setR2Running(true);
    try {
      await runR2Backup("auto");
      showNotice("R2 备份已启动。");
    } catch (error) {
      showNotice("启动失败：" + (error instanceof Error ? error.message : String(error)));
    } finally {
      setR2Running(false);
    }
  }

  async function prepareSelectedR2Restore() {
    if (!r2Config?.configured || !r2Config.enabled) {
      openStorage("r2");
      showNotice("请先完成 R2 配置。");
      return;
    }
    setRestorePreparing(true);
    try {
      await prepareR2Restore(selectedRestoreKey);
      showNotice("恢复点已开始下载并校验，只进入 staging，不覆盖生产环境。");
    } catch (error) {
      showNotice("恢复准备启动失败：" + (error instanceof Error ? error.message : String(error)));
    } finally {
      setRestorePreparing(false);
    }
  }

  return (
    <div className="pb-10">
      <header className="app-page-header -mx-1 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-slate-900">备份与容灾</h1>
            <p className="mt-1.5 text-sm leading-6 text-slate-500">
              模块化备份、全量容灾、支持多存储，保障系统数据安全
            </p>
          </div>
          <button
            type="button"
            onClick={() => void runR2()}
            disabled={r2Running || r2Loading}
            className="app-button-primary inline-flex h-9 items-center gap-2 rounded-lg px-4 text-[11px] font-medium disabled:cursor-wait disabled:opacity-50"
          >
            <span>▶</span>
            {r2Running ? "启动中…" : r2Config?.configured && r2Config.enabled ? "立即 R2 备份" : "配置 R2"}
          </button>
        </div>
      </header>

      {notice ? (
        <div className="mt-3 max-w-7xl rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[10px] leading-5 text-blue-700">
          {notice}
        </div>
      ) : null}

      <div className="mt-4 max-w-7xl border-b border-slate-200">
        <div className="flex gap-1 overflow-x-auto">
          {tabs.map(([key, label]) => (
            <button
              type="button"
              key={key}
              onClick={() => setActiveTab(key)}
              className={
                "border-b-2 px-5 py-2.5 text-[11px] font-medium transition " +
                (activeTab === key
                  ? "border-blue-600 bg-blue-50/50 text-blue-700"
                  : "border-transparent text-slate-500 hover:text-slate-800")
              }
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "overview" ? (
        <div className="mt-4 max-w-7xl">
          <section className="app-card rounded-xl p-4">
            <div className="mb-3 text-[13px] font-semibold text-slate-900">备份架构总览</div>
            <div className="relative grid gap-4 xl:grid-cols-[1fr_1.2fr_1fr]">
              <span className="pointer-events-none absolute left-[31.8%] top-1/2 z-[1] hidden -translate-x-1/2 -translate-y-1/2 text-2xl font-light text-slate-400 xl:block">→</span>
              <span className="pointer-events-none absolute left-[69.1%] top-1/2 z-[1] hidden -translate-x-1/2 -translate-y-1/2 text-2xl font-light text-slate-400 xl:block">→</span>

              <div className="rounded-xl border border-emerald-100 bg-emerald-50/35 p-3">
                <div className="mb-3 text-center text-[11px] font-semibold text-slate-800">本地系统</div>
                <div className="space-y-2">
                  <ArchitectureItem icon="app" title="应用程序" desc="代码 / Docker / 配置" tone="green" />
                  <ArchitectureItem icon="db" title="数据库" desc="PostgreSQL" tone="green" />
                  <ArchitectureItem icon="folder" title="业务文件" desc="data/ 上传文件、附件等" tone="green" />
                  <ArchitectureItem icon="settings" title="系统配置" desc=".env / 部署配置 / 其他" tone="green" />
                </div>
              </div>

              <div className="rounded-xl border border-blue-100 bg-blue-50/35 p-3">
                <div className="mb-3 text-center text-[11px] font-semibold text-slate-800">备份策略</div>
                <div className="space-y-3">
                  <StrategyOverviewCard icon="sync" title="日常备份（模块化）" tone="green" status="策略已定义">
                    <ul className="space-y-0.5 text-[9px] leading-4 text-slate-500">
                      <li>• 每日检测有更新内容</li>
                      <li>• 模块化增量备份</li>
                      <li>• 未变化内容不重复上传</li>
                      <li>• 保留真实执行记录</li>
                    </ul>
                  </StrategyOverviewCard>
                  <StrategyOverviewCard icon="db" title="全量备份（容灾级）" tone="blue" status="策略已定义">
                    <ul className="space-y-0.5 text-[9px] leading-4 text-slate-500">
                      <li>• 默认每 10 天执行一次</li>
                      <li>• 软件 + 数据 + 配置完整备份</li>
                      <li>• 配置加密，恢复密钥独立保存</li>
                      <li>• 用于灾难恢复</li>
                    </ul>
                  </StrategyOverviewCard>
                </div>
              </div>

              <div className="rounded-xl border border-amber-100 bg-amber-50/35 p-3">
                <div className="mb-3 text-center text-[11px] font-semibold text-slate-800">存储目标</div>
                <div className="space-y-2">
                  <ArchitectureItem
                    icon="cloud"
                    title="Cloudflare R2（主存储）"
                    desc="每日模块化 + 周期全量容灾"
                    tone={r2Config?.configured && r2Config.enabled ? "green" : "amber"}
                    status={r2Loading ? "读取中" : r2Config?.configured ? (r2Config.enabled ? "已配置" : "已停用") : "待配置"}
                    statusTone={r2Config?.configured && r2Config.enabled ? "green" : "amber"}
                    onConfigure={() => openStorage("r2")}
                  />
                  <ArchitectureItem
                    icon="box"
                    title="七牛云 Kodo（国内冷备）"
                    desc="每日完整恢复点 · 内容去重 · 只写入"
                    tone={kodoConfig?.configured && kodoConfig.enabled ? "green" : "amber"}
                    status={kodoLoading ? "读取中" : kodoConfig?.configured ? (kodoConfig.enabled ? "已配置" : "已停用") : "待配置"}
                    statusTone={kodoConfig?.configured && kodoConfig.enabled ? "green" : "amber"}
                    onConfigure={() => openStorage("kodo")}
                  />
                  <ArchitectureItem
                    icon="cloud"
                    title="坚果云 WebDAV（异地备份）"
                    desc="每日完整恢复点 · 可读写 · 独立目录"
                    tone={webdavConfig?.connectionStatus === "connected" && webdavConfig.enabled ? "green" : "amber"}
                    status={webdavLoading ? "读取中" : webdavConfig?.connectionStatus === "error" ? "检测失败" : webdavConfig?.configured ? (webdavConfig.enabled ? "已配置" : "已停用") : "待配置"}
                    statusTone={webdavConfig?.connectionStatus === "connected" && webdavConfig.enabled ? "green" : "amber"}
                    onConfigure={() => openStorage("webdav")}
                  />
                  <ArchitectureItem
                    icon="nas"
                    title="本地 NAS（可选）"
                    desc="本地冷备 / 第三副本"
                    tone="slate"
                    status="未接入"
                    statusTone="slate"
                    onConfigure={() => openStorage("nas")}
                  />
                </div>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "restore" ? (
        <div className="mt-4 max-w-7xl">
          <section className="app-card rounded-xl p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-[13px] font-semibold text-slate-900">恢复管理</div>
                <p className="mt-1 text-[10px] leading-5 text-slate-400">
                  当前恢复执行器仅接入 Cloudflare R2；七牛云 Kodo 按“只存不取”执行。
                </p>
              </div>
              <Pill tone={r2Config?.configured && r2Config.enabled ? "green" : "amber"}>
                {r2Config?.configured && r2Config.enabled ? "R2 可准备恢复" : "R2 待配置"}
              </Pill>
            </div>

            <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_auto] lg:items-end">
              <label className="space-y-1.5">
                <span className="text-[10px] font-medium text-slate-600">R2 全量恢复点</span>
                <select
                  value={selectedRestoreKey}
                  onChange={(event) => setSelectedRestoreKey(event.target.value)}
                  disabled={!r2Config?.configured || !r2Config.enabled}
                  className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400 disabled:bg-slate-50 disabled:text-slate-400"
                >
                  <option value="">最新 R2 全量恢复点</option>
                  {r2FullRestorePoints.map((row) => (
                    <option key={row.snapshotObjectKey} value={row.snapshotObjectKey}>
                      {backupTime(row.time)} · {row.timestamp}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={restorePreparing || !r2Config?.configured || !r2Config.enabled}
                onClick={() => void prepareSelectedR2Restore()}
                className="app-button-primary h-9 rounded-lg px-4 text-[10px] font-medium disabled:opacity-40"
              >
                {restorePreparing ? "准备中…" : "准备恢复点"}
              </button>
            </div>

            <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[9px] leading-5 text-slate-500">
              安全流程：下载到 staging → SHA256 校验 → 临时恢复演练；不会直接覆盖生产 PostgreSQL、data/、应用代码或 .env。
            </div>
          </section>
        </div>
      ) : null}

      {activeTab === "records" ? (
        <div className="mt-4 max-w-7xl">
          <section className="app-card overflow-hidden rounded-xl">
            <div className="border-b border-slate-100 px-4 py-3">
              <div className="text-[13px] font-semibold text-slate-900">备份记录</div>
              <div className="mt-1 text-[10px] text-slate-400">来自本地 manifest、R2、Kodo 和坚果云 WebDAV 本地上传回执。</div>
            </div>
            <div className="grid grid-cols-[1.1fr_.9fr_.8fr_1.2fr_.7fr] border-b border-slate-100 bg-slate-50 px-4 py-2 text-[10px] font-medium text-slate-500">
              <span>时间</span><span>类型</span><span>目标</span><span>说明</span><span>状态</span>
            </div>
            <div className="divide-y divide-slate-100">
              {(backupStatus?.records || []).map((row) => (
                <div key={`${row.type}-${row.timestamp}-${row.target}`} className="grid grid-cols-[1.1fr_.9fr_.8fr_1.2fr_.7fr] items-center px-4 py-3 text-[10px]">
                  <span className="text-slate-600">{backupTime(row.time)}</span>
                  <span className="font-medium text-slate-700">
                    {row.type === "r2_full" ? "全量容灾" : row.type === "r2_daily" ? "日常模块化" : row.type === "kodo_full" ? "冷备恢复点" : row.type === "webdav_full" ? "WebDAV 恢复点" : row.type === "r2_attempt" || row.type === "kodo_attempt" || row.type === "webdav_attempt" ? "任务状态" : "本地基础"}
                  </span>
                  <span className="text-slate-600">{row.target}</span>
                  <span className="text-slate-500">{row.detail}</span>
                  <span className={row.status === "success" ? "text-emerald-700" : row.status === "running" ? "text-blue-700" : row.status === "failed" ? "text-red-600" : "text-amber-700"}>
                    {row.status === "success" ? "成功" : row.status === "running" ? "执行中" : row.status === "failed" ? "失败" : row.status === "skipped" ? "已跳过" : row.status}
                  </span>
                </div>
              ))}
              {!backupStatus?.records.length ? (
                <div className="px-4 py-12 text-center text-[11px] text-slate-400">暂无备份记录</div>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}

      {storageModalOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4 py-6">
          <button
            type="button"
            aria-label="关闭配置"
            onClick={closeStorageModal}
            className="absolute inset-0 bg-slate-950/30"
          />
          <section className="relative z-10 w-full max-w-xl overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
            <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
              <div>
                <div className="text-[14px] font-semibold text-slate-900">{STORAGE_DETAILS[selectedStorage].title} 配置</div>
                <div className="mt-1 text-[10px] font-medium text-blue-600">{STORAGE_DETAILS[selectedStorage].role}</div>
                <p className="mt-1.5 text-[10px] leading-5 text-slate-500">{STORAGE_DETAILS[selectedStorage].desc}</p>
              </div>
              <button type="button" onClick={closeStorageModal} className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100">×</button>
            </div>

            {selectedStorage === "r2" ? (
              <>
                <div className="space-y-3 px-5 py-4">
                  <div className="rounded-lg bg-blue-50 px-3 py-2 text-[9px] leading-5 text-blue-700">
                    标准配置只填 ID 与密钥。系统自动使用标准 Endpoint / Bucket；特殊环境再展开高级配置人工调整。
                  </div>

                  <label className="block space-y-1.5">
                    <span className="text-[10px] font-medium text-slate-600">账户 ID</span>
                    <input
                      value={r2Form.accountId}
                      onChange={(event) => {
                        const accountId = event.target.value;
                        setR2Form((current) => ({
                          ...current,
                          accountId,
                          endpointUrl: accountId.trim()
                            ? `https://${accountId.trim()}.r2.cloudflarestorage.com`
                            : current.endpointUrl,
                        }));
                      }}
                      placeholder="Cloudflare Account ID"
                      className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                    />
                  </label>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">
                        Access Key ID {r2Config?.accessKeyHint ? <span className="font-normal text-slate-400">（已保存 {r2Config.accessKeyHint}）</span> : null}
                      </span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={r2Form.accessKey}
                        onChange={(event) => setR2Form((current) => ({ ...current, accessKey: event.target.value }))}
                        placeholder={r2Config?.configured ? "留空保持原密钥" : "填写 Access Key ID"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">Secret Access Key</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={r2Form.secretKey}
                        onChange={(event) => setR2Form((current) => ({ ...current, secretKey: event.target.value }))}
                        placeholder={r2Config?.configured ? "留空保持原密钥" : "填写 Secret Key"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                  </div>

                  <button
                    type="button"
                    onClick={() => setAdvancedConfigOpen((value) => !value)}
                    className="text-[10px] font-medium text-slate-500 hover:text-slate-800"
                  >
                    {advancedConfigOpen ? "收起高级配置 ↑" : "高级配置 / 人工调整 ↓"}
                  </button>

                  {advancedConfigOpen ? (
                    <div className="grid gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3 sm:grid-cols-2">
                      <label className="block space-y-1.5 sm:col-span-2">
                        <span className="text-[9px] font-medium text-slate-500">Endpoint</span>
                        <input value={r2Form.endpointUrl} onChange={(event) => setR2Form((current) => ({ ...current, endpointUrl: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="block space-y-1.5">
                        <span className="text-[9px] font-medium text-slate-500">Bucket</span>
                        <input value={r2Form.bucket} onChange={(event) => setR2Form((current) => ({ ...current, bucket: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="block space-y-1.5">
                        <span className="text-[9px] font-medium text-slate-500">对象前缀</span>
                        <input value={r2Form.prefix} onChange={(event) => setR2Form((current) => ({ ...current, prefix: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="block space-y-1.5">
                        <span className="text-[9px] font-medium text-slate-500">全量容灾间隔（天）</span>
                        <input type="number" min={1} max={365} value={r2Form.fullIntervalDays} onChange={(event) => setR2Form((current) => ({ ...current, fullIntervalDays: Number(event.target.value) || 10 }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="flex items-center gap-2 self-end pb-2 text-[9px] text-slate-600">
                        <input type="checkbox" checked={r2Form.enabled} onChange={(event) => setR2Form((current) => ({ ...current, enabled: event.target.checked }))} />
                        启用 R2 备份
                      </label>
                    </div>
                  ) : null}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
                  <button type="button" onClick={closeStorageModal} className="app-button-secondary rounded-lg px-4 py-2 text-[10px] font-medium">取消</button>
                  <button type="button" disabled={r2Saving || r2Loading} onClick={() => void saveR2Config()} className="app-button-primary rounded-lg px-4 py-2 text-[10px] font-medium disabled:opacity-50">
                    {r2Saving ? "保存并检测…" : "保存并完成"}
                  </button>
                </div>
              </>
            ) : selectedStorage === "kodo" ? (
              <>
                <div className="space-y-3 px-5 py-4">
                  <div className="rounded-lg bg-blue-50 px-3 py-2 text-[9px] leading-5 text-blue-700">
                    标准配置只填 Access Key 与 Secret Key。系统使用标准上传地址与冷备空间；特殊环境再展开高级配置人工调整。
                  </div>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">
                        Access Key {kodoConfig?.accessKeyHint ? <span className="font-normal text-slate-400">（已保存 {kodoConfig.accessKeyHint}）</span> : null}
                      </span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={kodoForm.accessKey}
                        onChange={(event) => setKodoForm((current) => ({ ...current, accessKey: event.target.value }))}
                        placeholder={kodoConfig?.configured ? "留空保持原密钥" : "填写 Access Key"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">Secret Key</span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={kodoForm.secretKey}
                        onChange={(event) => setKodoForm((current) => ({ ...current, secretKey: event.target.value }))}
                        placeholder={kodoConfig?.configured ? "留空保持原密钥" : "填写 Secret Key"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                  </div>

                  <button
                    type="button"
                    onClick={() => setAdvancedConfigOpen((value) => !value)}
                    className="text-[10px] font-medium text-slate-500 hover:text-slate-800"
                  >
                    {advancedConfigOpen ? "收起高级配置 ↑" : "高级配置 / 人工调整 ↓"}
                  </button>

                  {advancedConfigOpen ? (
                    <div className="grid gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3 sm:grid-cols-2">
                      <label className="block space-y-1.5">
                        <span className="text-[9px] font-medium text-slate-500">Bucket</span>
                        <input value={kodoForm.bucket} onChange={(event) => setKodoForm((current) => ({ ...current, bucket: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="block space-y-1.5">
                        <span className="text-[9px] font-medium text-slate-500">上传域名</span>
                        <input value={kodoForm.uploadUrl} onChange={(event) => setKodoForm((current) => ({ ...current, uploadUrl: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="block space-y-1.5 sm:col-span-2">
                        <span className="text-[9px] font-medium text-slate-500">对象前缀</span>
                        <input value={kodoForm.prefix} onChange={(event) => setKodoForm((current) => ({ ...current, prefix: event.target.value }))} className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400" />
                      </label>
                      <label className="flex items-center gap-2 text-[9px] text-slate-600">
                        <input type="checkbox" checked={kodoForm.enabled} onChange={(event) => setKodoForm((current) => ({ ...current, enabled: event.target.checked }))} />
                        启用 Kodo 冷备
                      </label>
                    </div>
                  ) : null}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
                  <button type="button" onClick={closeStorageModal} className="app-button-secondary rounded-lg px-4 py-2 text-[10px] font-medium">取消</button>
                  <button type="button" disabled={kodoSaving || kodoLoading} onClick={() => void saveKodoConfig()} className="app-button-primary rounded-lg px-4 py-2 text-[10px] font-medium disabled:opacity-50">
                    {kodoSaving ? "保存并检测…" : "保存并完成"}
                  </button>
                </div>
              </>
            ) : selectedStorage === "webdav" ? (
              <>
                <div className="space-y-3 px-5 py-4">
                  <div className="rounded-lg bg-blue-50 px-3 py-2 text-[9px] leading-5 text-blue-700">
                    使用坚果云“第三方应用管理”生成的应用密码。系统会先做登录、写入、读取和清理探针，再启用每日完整容灾上传。
                  </div>

                  <label className="block space-y-1.5">
                    <span className="text-[10px] font-medium text-slate-600">WebDAV 服务器地址</span>
                    <input
                      value={webdavForm.baseUrl}
                      onChange={(event) => setWebdavForm((current) => ({ ...current, baseUrl: event.target.value }))}
                      placeholder="https://dav.jianguoyun.com/dav/"
                      className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                    />
                  </label>

                  <div className="grid gap-3 sm:grid-cols-2">
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">账号</span>
                      <input
                        type="email"
                        autoComplete="username"
                        value={webdavForm.username}
                        onChange={(event) => setWebdavForm((current) => ({ ...current, username: event.target.value }))}
                        placeholder={webdavConfig?.configured ? "留空保持原账号" : "坚果云账号"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-medium text-slate-600">
                        应用密码 {webdavConfig?.usernameHint ? <span className="font-normal text-slate-400">（已保存 {webdavConfig.usernameHint}）</span> : null}
                      </span>
                      <input
                        type="password"
                        autoComplete="new-password"
                        value={webdavForm.appPassword}
                        onChange={(event) => setWebdavForm((current) => ({ ...current, appPassword: event.target.value }))}
                        placeholder={webdavConfig?.configured ? "留空保持原应用密码" : "坚果云应用密码"}
                        className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                      />
                    </label>
                  </div>

                  <button
                    type="button"
                    onClick={() => setAdvancedConfigOpen((value) => !value)}
                    className="text-[10px] font-medium text-slate-500 hover:text-slate-800"
                  >
                    {advancedConfigOpen ? "收起高级配置 ↑" : "高级配置 / 人工调整 ↓"}
                  </button>

                  {advancedConfigOpen ? (
                    <div className="grid gap-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3 sm:grid-cols-2">
                      <label className="block space-y-1.5 sm:col-span-2">
                        <span className="text-[9px] font-medium text-slate-500">远程备份目录</span>
                        <input
                          value={webdavForm.remotePath}
                          onChange={(event) => setWebdavForm((current) => ({ ...current, remotePath: event.target.value }))}
                          className="h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-[10px] text-slate-700 outline-none focus:border-blue-400"
                        />
                      </label>
                      <label className="flex items-center gap-2 text-[9px] text-slate-600">
                        <input type="checkbox" checked={webdavForm.enabled} onChange={(event) => setWebdavForm((current) => ({ ...current, enabled: event.target.checked }))} />
                        启用 WebDAV 每日备份
                      </label>
                    </div>
                  ) : null}
                </div>
                <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
                  <button type="button" onClick={closeStorageModal} className="app-button-secondary rounded-lg px-4 py-2 text-[10px] font-medium">取消</button>
                  <button type="button" disabled={webdavSaving || webdavLoading} onClick={() => void saveWebdavConfig()} className="app-button-primary rounded-lg px-4 py-2 text-[10px] font-medium disabled:opacity-50">
                    {webdavSaving ? "保存并检测…" : "保存并完成"}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="px-5 py-6">
                  <div className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-4 text-[10px] leading-5 text-slate-600">
                    本地 NAS 当前仍是可选扩展目标，真实写入与恢复执行器尚未接入。这里暂不保存无效字段，避免出现“看起来已配置、实际上不可用”的状态。
                  </div>
                </div>
                <div className="flex justify-end border-t border-slate-100 px-5 py-3">
                  <button type="button" onClick={closeStorageModal} className="app-button-primary rounded-lg px-4 py-2 text-[10px] font-medium">完成</button>
                </div>
              </>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
}
