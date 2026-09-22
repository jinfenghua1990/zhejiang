// ---------- 登录令牌（localStorage 保存，请求统一携带 Bearer） ----------

const TOKEN_KEY = "ecdp_access_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function redirectToLogin() {
  if (typeof window !== "undefined") {
    clearToken();
    window.location.href = "/login";
  }
}

/** 所有业务请求统一携带令牌，并在服务端判定失效时回登录页。 */
export async function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401 && process.env.NEXT_PUBLIC_ACCESS_MODE !== "open") {
    redirectToLogin();
    throw new Error("登录已过期");
  }
  return res;
}

/**
 * 下载受保护的二进制文件。
 *
 * 归档文件接口属于鉴权 API，不能用普通 <a href> 直接打开，否则浏览器
 * 不会携带 Bearer 令牌，页面看起来像“写入成功但下载失败/401”。
 */
export async function downloadAuthenticatedFile(url: string, filename = "下载文件"): Promise<void> {
  const res = await authenticatedFetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(await readErrorDetail(res, "下载失败"));
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename || "下载文件";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

/** FastAPI 的 detail 可能是字符串（HTTPException）或 422 校验错误数组；统一转成可读文案。 */
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        const d = item as { msg?: string; loc?: unknown[] };
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body") : [];
        return `${loc.length ? `${loc.join(".")}: ` : ""}${d.msg ?? ""}`.trim();
      })
      .filter(Boolean);
    if (parts.length) return parts.join("；");
  }
  if (detail && typeof detail === "object") {
    try { return JSON.stringify(detail); } catch { /* 退回 fallback */ }
  }
  return fallback;
}

/** 读取带认证请求失败时的 detail 文案。 */
async function readErrorDetail(res: Response, fallback = "请求失败"): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return detailToMessage(body.detail, `${fallback}（${res.status}）`);
}

// ---------- 认证 ----------

export type RuntimeAuthConfig = {
  accessMode: "rbac" | "open";
};

export async function getRuntimeAuthConfig(): Promise<RuntimeAuthConfig> {
  const res = await fetch("/api/v1/auth/config", { cache: "no-store" });
  if (!res.ok) throw new Error(`auth config ${res.status}`);
  return res.json();
}

export type AuthUser = {
  id: number;
  username: string;
  displayName: string;
  roles: string[];
  isActive: boolean;
};

export async function login(
  username: string,
  password: string,
  rememberMe = false
): Promise<AuthUser> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember_me: rememberMe }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `登录失败（${res.status}）`));
  }
  const data = (await res.json()) as { accessToken: string; user: AuthUser };
  setToken(data.accessToken);
  return data.user;
}

export async function fetchMe(): Promise<AuthUser> {
  const res = await authenticatedFetch("/api/v1/auth/me");
  if (!res.ok) throw new Error(`me ${res.status}`);
  return res.json();
}

export async function logout(): Promise<void> {
  try {
    await authenticatedFetch("/api/v1/auth/logout", { method: "POST" });
  } finally {
    clearToken();
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const res = await authenticatedFetch("/api/v1/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `修改失败（${res.status}）`));
  }
  clearToken();
  window.location.href = "/login?passwordChanged=1";
}

export type IntegrationStatus = {
  id: string;
  name: string;
  mode: string;
  phase: number;
  status: string;
  lastTestedAt: string | null;
  lastSuccessAt: string | null;
  errorSummary: string | null;
};

export type Overview = {
  phase: number;
  phaseName: string;
  accessMode: string;
  dataState: string;
  integrations: IntegrationStatus[];
  pendingExceptions: number;
  nextMilestone: string;
  metrics: Record<string, number | null>;
};

export type SystemHealth = {
  status: "ready" | "degraded" | string;
  components: Record<string, "up" | "down" | string>;
};

let overviewCache: { value: Overview; expiresAt: number } | null = null;
let overviewRequest: Promise<Overview> | null = null;

export async function getOverview(): Promise<Overview> {
  if (overviewCache && overviewCache.expiresAt > Date.now()) {
    return overviewCache.value;
  }
  if (overviewRequest) return overviewRequest;
  overviewRequest = authenticatedFetch("/api/v1/system/overview", {
    cache: "no-store",
  })
    .then(async (res) => {
      if (!res.ok) throw new Error(`overview ${res.status}`);
      const value = (await res.json()) as Overview;
      // 页面切换时短时间内会同时挂载状态栏和业务页，共享同一份读取结果。
      overviewCache = { value, expiresAt: Date.now() + 10_000 };
      return value;
    })
    .finally(() => {
      overviewRequest = null;
    });
  return overviewRequest;
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const res = await authenticatedFetch("/api/v1/system/health", {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

export type SystemUpdateMode = "manual" | "auto_download" | "auto_update";
export type SystemUpdateLevel = "patch" | "feature" | "major";

export type SystemUpdateSettings = {
  enabled: boolean;
  mode: SystemUpdateMode;
  checkIntervalMinutes: number;
  autoUpdateHour: number;
  autoUpdateWindowMinutes: number;
  autoInstallLevel: SystemUpdateLevel;
  branch: string;
  remote: string;
};

export type SystemUpdateCommit = {
  sha: string;
  shortSha: string;
  subject: string;
  committedAt: string;
  version?: string;
  modules?: string[];
};

export type SystemUpdateModuleVersion = {
  key: string;
  label: string;
  status: "latest" | "update" | "untracked";
  currentVersion: string;
  currentSha: string;
  currentSubject: string;
  currentCommittedAt: string;
  latestVersion: string;
  latestSha: string;
  latestSubject: string;
  latestCommittedAt: string;
};

export type SystemUpdateHistory = {
  at: string;
  result: "success" | "failed" | "rolled_back" | string;
  message: string;
  actor: string;
  fromSha: string;
  toSha: string;
  error?: string;
  rollbackErrors?: string[];
};

export type SystemUpdateReadinessCheck = {
  key: string;
  label: string;
  status: "ok" | "warn" | "error";
  detail: string;
  blocking: boolean;
};

export type SystemUpdateReadiness = {
  ready: boolean;
  checks: SystemUpdateReadinessCheck[];
  blockingCount: number;
  warningCount: number;
  branch: string;
  remote: string;
  checkedAt: string;
};

export type SystemRuntimeRelease = {
  appEnv: "production" | "staging" | "development" | string;
  releaseChannel: string;
  deploymentMode: "native" | "container" | string;
  managedBy: "github_ghcr" | "git_native" | string;
  gitSha: string;
  imageRef: string;
  imageTag: string;
  version: string;
  alembicRevision: string;
  inAppUpdateEnabled: boolean;
};

export type SystemUpdateStatus = {
  phase?: string;
  progress?: number;
  message?: string;
  currentSha?: string;
  latestSha?: string;
  downloadedSha?: string;
  previousSha?: string;
  targetSha?: string;
  currentBranch?: string;
  configuredBranch?: string;
  dirty?: boolean;
  updateAvailable?: boolean;
  diverged?: boolean;
  running?: boolean;
  latestCommit?: SystemUpdateCommit | null;
  currentCommit?: SystemUpdateCommit | null;
  changes?: SystemUpdateCommit[];
  lastCheckAt?: string;
  lastCheckError?: string;
  lastAutoError?: string;
  startedAt?: string;
  finishedAt?: string;
  startedBy?: string;
  backupDb?: string;
  backupData?: string;
  error?: string;
  rollbackErrors?: string[];
  lastInstallResult?: "success" | "failed" | "rolled_back" | string;
  lastInstallAt?: string;
  lastInstallFromSha?: string;
  lastInstallToSha?: string;
  logs?: string[];
  history?: SystemUpdateHistory[];
  runtime?: SystemRuntimeRelease;
  updateLevel?: SystemUpdateLevel;
  updateLevelLabel?: string;
  impactedModules?: string[];
  moduleVersions?: SystemUpdateModuleVersion[];
  changedFiles?: string[];
  changedFileCount?: number;
  hasMigration?: boolean;
  classificationReasons?: string[];
  autoInstallEligible?: boolean;
  autoInstallBlockedReason?: string;
  settings: SystemUpdateSettings;
};

export type SystemLocalChangeFile = {
  path: string;
  status: string;
  tracked: boolean;
  eligible: boolean;
  excludedReason?: string;
  added?: number | null;
  deleted?: number | null;
  size?: number;
};

export type SystemLocalHandoffRecord = {
  branch: string;
  commitSha: string;
  shortSha: string;
  baseSha: string;
  uploadedAt: string;
  uploadedBy: string;
  fileCount: number;
  excludedCount: number;
  files: string[];
  worktreePreserved: boolean;
};

export type SystemLocalSyncRecord = {
  branch: string;
  commitSha: string;
  shortSha: string;
  baseSha: string;
  remoteShaBefore: string;
  syncedAt: string;
  syncedBy: string;
  fileCount: number;
  excludedCount: number;
  files: string[];
  worktreePreserved: boolean;
};

export type SystemLocalChanges = {
  dirty: boolean;
  baseSha: string;
  currentBranch: string;
  eligibleCount: number;
  excludedCount: number;
  totalAdded: number;
  totalDeleted: number;
  impactedModules: string[];
  files: SystemLocalChangeFile[];
  excludedFiles: SystemLocalChangeFile[];
  protectedRules: string[];
  diffPreview?: string;
  lastUpload?: SystemLocalHandoffRecord | null;
  lastSync?: SystemLocalSyncRecord | null;
  worktreePreserved: boolean;
  checkedAt: string;
};

export type SystemLocalUploadResult = SystemLocalHandoffRecord & {
  ok: boolean;
  message: string;
  localChanges: SystemLocalChanges;
};

export type SystemLocalSyncResult = SystemLocalSyncRecord & {
  ok: boolean;
  message: string;
  localChanges: SystemLocalChanges;
  status?: SystemUpdateStatus;
};

export const systemUpdateApi = {
  status: () => jsonFetch<SystemUpdateStatus>("/api/v1/system/update/status"),
  readiness: () => jsonFetch<SystemUpdateReadiness>("/api/v1/system/update/readiness"),
  localChanges: (includeDiff = false) =>
    jsonFetch<SystemLocalChanges>(`/api/v1/system/update/local-changes?include_diff=${includeDiff ? "true" : "false"}`),
  uploadLocalChanges: () =>
    jsonFetch<SystemLocalUploadResult>("/api/v1/system/update/local-changes/upload", { method: "POST" }),
  syncLocalChanges: () =>
    jsonFetch<SystemLocalSyncResult>("/api/v1/system/update/local-changes/sync", { method: "POST" }),
  check: () => jsonFetch<SystemUpdateStatus>("/api/v1/system/update/check", { method: "POST" }),
  saveSettings: (body: Partial<Pick<SystemUpdateSettings, "enabled" | "mode" | "checkIntervalMinutes" | "autoUpdateHour" | "autoUpdateWindowMinutes" | "autoInstallLevel">>) =>
    jsonFetch<{ ok: boolean; settings: SystemUpdateSettings }>("/api/v1/system/update/settings", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  apply: () => jsonFetch<SystemUpdateStatus & { started: boolean; reason?: string }>("/api/v1/system/update/apply", { method: "POST" }),
};

export async function testJackyun(): Promise<{
  ok: boolean; status?: string; businessReady?: boolean; tools?: string[]; error?: string;
}> {
  const res = await authenticatedFetch("/api/v1/integrations/jackyun/test", {
    method: "POST",
  });
  return res.json();
}

export type R2BackupConfig = {
  configured: boolean;
  enabled: boolean;
  endpointUrl: string;
  bucket: string;
  prefix: string;
  accessKeyHint: string;
  fullIntervalDays: number;
  mode: "backup" | string;
  readEnabled: true;
  scheduler: { mode: string; managed: boolean; label: string; message: string };
};

export type R2BackupConfigInput = {
  endpointUrl: string;
  bucket: string;
  prefix: string;
  accessKey?: string;
  secretKey?: string;
  enabled: boolean;
  fullIntervalDays: number;
};

export function getR2BackupConfig(): Promise<R2BackupConfig> {
  return jsonFetch<R2BackupConfig>("/api/v1/integrations/r2-backup");
}

export function saveR2BackupConfig(body: R2BackupConfigInput): Promise<R2BackupConfig> {
  return jsonFetch<R2BackupConfig>("/api/v1/integrations/r2-backup", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testR2BackupConnection(): Promise<{
  ok: boolean;
  status: number;
  bucket: string;
  message: string;
}> {
  return jsonFetch("/api/v1/integrations/r2-backup/test", { method: "POST" });
}

export function runR2Backup(mode: "auto" | "daily" | "full" = "auto"): Promise<{
  started: boolean;
  target: "r2";
  mode: string;
  log: string;
}> {
  return jsonFetch("/api/v1/integrations/r2-backup/run", {
    method: "POST",
    body: JSON.stringify({ mode }),
  });
}

export type BackupRecord = {
  timestamp: string;
  time: string | null;
  type: "local" | "r2_daily" | "r2_full" | "kodo_full" | "webdav_full" | string;
  target: string;
  status: string;
  detail: string;
  snapshotObjectKey?: string;
  recoverable?: boolean;
  verificationLevel?: "local_manifest" | "uploaded" | "restore_ready" | "upload_acknowledged" | string;
  uploadedCount?: number;
  reusedCount?: number;
};

export type BackupStatus = {
  backupDir: string;
  records: BackupRecord[];
  lastLocal: BackupRecord | null;
  lastR2: BackupRecord | null;
  lastR2Full: BackupRecord | null;
  lastKodo: BackupRecord | null;
  lastWebdav: BackupRecord | null;
  lastAttemptR2: BackupJobAttempt | null;
  lastAttemptKodo: BackupJobAttempt | null;
  lastAttemptWebdav: BackupJobAttempt | null;
  health: {
    local: BackupHealth;
    r2: BackupHealth;
    kodo: BackupHealth;
    webdav: BackupHealth;
  };
};

export type BackupJobAttempt = {
  target: "r2" | "kodo" | "webdav" | string;
  status: "running" | "success" | "failed" | "skipped" | string;
  startedAt: string | null;
  finishedAt: string | null;
  exitCode: number | null;
  mode?: string;
};

export type BackupHealth = {
  status: "healthy" | "stale" | "failed" | "running" | "never" | string;
  ageHours: number | null;
  message: string;
};

export function getBackupStatus(limit = 50): Promise<BackupStatus> {
  return jsonFetch<BackupStatus>(`/api/v1/integrations/backup-status?limit=${limit}`);
}

export function prepareR2Restore(snapshotObjectKey = ""): Promise<{
  started: boolean;
  target: "r2";
  action: "prepare_restore";
  snapshotObjectKey: string;
  log: string;
}> {
  return jsonFetch("/api/v1/integrations/r2-backup/prepare-restore", {
    method: "POST",
    body: JSON.stringify({ snapshotObjectKey }),
  });
}

export type KodoColdBackupConfig = {
  configured: boolean;
  enabled: boolean;
  bucket: string;
  uploadUrl: string;
  prefix: string;
  accessKeyHint: string;
  mode: "upload_only" | string;
  readEnabled: false;
  scheduler: { mode: string; managed: boolean; label: string; message: string };
};

export type KodoColdBackupConfigInput = {
  bucket: string;
  uploadUrl: string;
  prefix: string;
  accessKey?: string;
  secretKey?: string;
  enabled: boolean;
};

export function getKodoColdBackupConfig(): Promise<KodoColdBackupConfig> {
  return jsonFetch<KodoColdBackupConfig>("/api/v1/integrations/kodo-cold");
}

export function saveKodoColdBackupConfig(
  body: KodoColdBackupConfigInput,
): Promise<KodoColdBackupConfig> {
  return jsonFetch<KodoColdBackupConfig>("/api/v1/integrations/kodo-cold", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testKodoColdBackupWrite(): Promise<{
  ok: boolean;
  status: number;
  bucket: string;
  message: string;
}> {
  return jsonFetch("/api/v1/integrations/kodo-cold/test-write", { method: "POST" });
}

export function runKodoColdBackup(): Promise<{
  started: boolean;
  target: "kodo";
  mode: "full";
  log: string;
}> {
  return jsonFetch("/api/v1/integrations/kodo-cold/run", { method: "POST" });
}

export type WebdavBackupConfig = {
  configured: boolean;
  enabled: boolean;
  baseUrl: string;
  remotePath: string;
  username: string;
  usernameHint: string;
  connectionStatus: "configured" | "connected" | "error" | string;
  lastTestedAt: string | null;
  lastSuccessAt: string | null;
  errorSummary: string | null;
  mode: "backup" | string;
  readEnabled: true;
  scheduler: { mode: string; managed: boolean; label: string; message: string };
};

export type WebdavBackupConfigInput = {
  baseUrl: string;
  remotePath: string;
  username: string;
  appPassword?: string;
  enabled: boolean;
};

export function getWebdavBackupConfig(): Promise<WebdavBackupConfig> {
  return jsonFetch<WebdavBackupConfig>("/api/v1/integrations/webdav-backup");
}

export function saveWebdavBackupConfig(body: WebdavBackupConfigInput): Promise<WebdavBackupConfig> {
  return jsonFetch<WebdavBackupConfig>("/api/v1/integrations/webdav-backup", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function testWebdavBackupConnection(): Promise<{
  ok: boolean;
  status: number;
  baseUrl: string;
  remotePath: string;
  message: string;
}> {
  return jsonFetch("/api/v1/integrations/webdav-backup/test", { method: "POST" });
}

export function runWebdavBackup(): Promise<{
  started: boolean;
  target: "webdav";
  mode: "full";
  log: string;
}> {
  return jsonFetch("/api/v1/integrations/webdav-backup/run", { method: "POST" });
}

export type ExceptionRow = {
  id: number;
  code: string;
  type: string;
  severity: string;
  title: string;
  detail: Record<string, unknown>;
  status: string;
  createdAt: string | null;
  handledBy: string;
  note: string;
};

export async function getExceptions(): Promise<ExceptionRow[]> {
  const res = await authenticatedFetch("/api/v1/exceptions", {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`exceptions ${res.status}`);
  return res.json();
}

export async function updateExceptionStatus(
  id: number,
  status: string,
  note: string
): Promise<{ ok: boolean; status: string; workflow?: { purchaseStatus?: string; adjustmentAmount?: string } }> {
  const res = await authenticatedFetch(`/api/v1/exceptions/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note }),
  });
  if (!res.ok) throw new Error(`异常处理失败（${res.status}）`);
  return res.json();
}

// ---------- 回款中心（Phase 5） ----------

export type RuleRow = {
  id: number;
  matchPattern: string;
  matchType: string;
  platform: string;
  enabled: boolean;
  note: string;
};

export type ReconTxn = {
  id: number;
  txnDate: string;
  direction: string;
  amount: string;
  accountNo?: string;
  accountCode?: string;
  accountName?: string;
  bankName?: string;
  counterpartyName: string;
  summary: string;
  serialNo: string;
  voucherNo: string;
  transactionTime?: string | null;
  sourceRowNumber?: number | null;
  importBatchId?: number | null;
  rawAvailable?: boolean;
  accountSource?: "file" | "manual" | string;
  /** 兼容字段：收入=settlementMatched；支出=invoicePaymentMatched。新代码优先使用显式域字段。 */
  matched: boolean;
  settlementMatched?: boolean;
  settlementMatchStatus?: "matched" | "unmatched" | "not_applicable" | string;
  invoiceMatched?: boolean;
  invoiceMatchStatus?: "matched" | "partial" | "unmatched" | "not_applicable" | string;
  invoicePaymentMatched?: boolean;
  invoicePaymentMatchStatus?: "matched" | "partial" | "unmatched" | "not_applicable" | string;
  invoiceMatchedAmount?: string;
  invoiceRemainingAmount?: string;
  invoicePaymentMatchedAmount?: string;
  invoicePaymentRemainingAmount?: string;
  matchStatus?: "matched" | "partial" | "unmatched" | "not_applicable" | string;
  settlementMatchedAt?: string | null;
  invoicePaymentMatchedAt?: string | null;
  /** 兼容时间：收入=settlementMatchedAt；支出=invoicePaymentMatchedAt。 */
  matchedAt?: string | null;
};

export type SettlementRow = {
  id: number;
  platform: string;
  storeName: string;
  period: string;
  expectedAmount: string;
  settledAmount: string;
  status: string;
};

export type Suggestion = {
  txnId: number;
  txnDate: string;
  counterparty: string;
  amount: string;
  settlementId: number;
  platform: string;
  period: string;
  expectedAmount: string;
  score: number;
  confidence: string;
  reasons: string[];
};

export type ReconOverview = {
  receivable: string;
  received: string;
  pending: string;
  byPlatform: Record<string, { expected: string; settled: string }>;
};

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `HTTP ${res.status}`));
  }
  return res.json();
}

/**
 * 创建耗材采购单使用请求标识幂等，因此网络在服务重启窗口短暂中断时可以安全重试一次。
 * 若第二次仍无法连通，给出可操作的中文提示，不把浏览器原始的 Failed to fetch 直接展示给用户。
 */
async function jsonFetchWithNetworkRetry<T>(url: string, init?: RequestInit): Promise<T> {
  try {
    return await jsonFetch<T>(url, init);
  } catch (caught) {
    if (!(caught instanceof TypeError) || !/fetch|network/i.test(caught.message)) throw caught;
    await new Promise((resolve) => window.setTimeout(resolve, 600));
    try {
      return await jsonFetch<T>(url, init);
    } catch (retryCaught) {
      if (retryCaught instanceof TypeError && /fetch|network/i.test(retryCaught.message)) {
        throw new Error("采购服务暂时无法连接，请稍后重试；系统会自动避免重复建单");
      }
      throw retryCaught;
    }
  }
}

/** 仅校验状态码的 DELETE（软删除接口返回 204 无响应体）。 */
async function noContentFetch(url: string): Promise<void> {
  const res = await authenticatedFetch(url, { method: "DELETE" });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `HTTP ${res.status}`));
  }
}

export const reconApi = {
  overview: () => jsonFetch<ReconOverview>("/api/v1/reconciliation/overview"),
  rules: () => jsonFetch<RuleRow[]>("/api/v1/reconciliation/rules"),
  createRule: (body: { match_pattern: string; match_type: string; platform: string; note?: string }) =>
    jsonFetch<{ id: number }>("/api/v1/reconciliation/rules", { method: "POST", body: JSON.stringify(body) }),
  deleteRule: (id: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/reconciliation/rules/${id}`, { method: "DELETE" }),
  transactions: (limit = 500) => jsonFetch<ReconTxn[]>(`/api/v1/reconciliation/transactions?limit=${limit}`),
  createTxn: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; created: boolean; fingerprint: string }>("/api/v1/reconciliation/transactions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  settlements: () => jsonFetch<SettlementRow[]>("/api/v1/reconciliation/settlements"),
  createSettlement: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number }>("/api/v1/reconciliation/settlements", { method: "POST", body: JSON.stringify(body) }),
  suggestions: () => jsonFetch<Suggestion[]>("/api/v1/reconciliation/suggestions"),
  confirm: (txnId: number, settlementId: number) =>
    jsonFetch<{ id: number; score: number; confidence: string }>("/api/v1/reconciliation/confirm", {
      method: "POST",
      body: JSON.stringify({ txn_id: txnId, settlement_id: settlementId }),
    }),
  reject: (txnId: number, settlementId: number) =>
    jsonFetch<{ ok: boolean }>("/api/v1/reconciliation/reject", {
      method: "POST",
      body: JSON.stringify({ txn_id: txnId, settlement_id: settlementId }),
    }),
};

// ---------- 利润中心（Phase 5） ----------

export type CostRow = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  period: string;
  values: { actual: string | null; purchOrder: string | null; default: string | null; estimated: string | null };
  effectiveCost: string | null;
  effectiveSource: string | null;
  version: number;
};

export type ProfitOverview = {
  skuCount: number;
  snapshotCount: number;
  coverage: Record<string, number>;
  contributionProfitEnabled: boolean;
};

export type ProfitCompute = {
  period: string;
  netSales: string | null;
  goodsCost: string | null;
  grossProfit: string | null;
  costMissingSkus: number[];
  costMissingDetail?: { skuId: number; skuCode: string; skuName: string; quantity: string }[];
  unmappedItems: number[];
  quantityMissingItems: number[];
  costMissing: boolean;
  warning?: string | null;
  error?: string | null;
  note: string;
};

export const profitApi = {
  overview: () => jsonFetch<ProfitOverview>("/api/v1/profit/overview"),
  costs: (year?: number, month?: number) => {
    const q = new URLSearchParams();
    if (year) q.set("period_year", String(year));
    if (month) q.set("period_month", String(month));
    return jsonFetch<CostRow[]>(`/api/v1/profit/costs${q.toString() ? `?${q}` : ""}`);
  },
  upsertCost: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; version: number }>("/api/v1/profit/costs", { method: "POST", body: JSON.stringify(body) }),
  compute: (year: number, month: number) =>
    jsonFetch<ProfitCompute>(`/api/v1/profit/compute?period_year=${year}&period_month=${month}`),
};

// ---------- 经营看板（Phase 2） ----------

export type TrendPoint = { date: string; orders: number; salesAmount: string | null; costAmount?: string | null; grossProfit?: string | null; costIncomplete?: boolean };
export type PlatformRow = { platform: string; orders: number; salesAmount: string | null; costAmount?: string | null; grossProfit?: string | null; costIncomplete?: boolean };
export type SkuRow = { skuCode: string; goodsName: string; orders: number; salesAmount: string | null };
export type InventorySummary = {
  skuCount: number;
  lastDocumentAt: string | null;
  totalQuantity: string | null;
  byWarehouse: { warehouseId: number | null; warehouseName?: string; quantity: string | null; skus: number }[];
  positionSource?: string;
  appliedDocumentCount?: number;
  note?: string;
};
/** 正品 SKU 逐笔出入库流水行：来自吉客云入库/出库单明细的真实单据事实。 */
export type SkuTransactionRow = {
  occurredAt: string | null;
  direction: "inbound" | "outbound";
  documentNo: string;
  warehouseId: number | null;
  warehouseName: string;
  quantity: number;
  balanceBefore: number;
  balanceAfter: number;
  supplierName: string;
  companyName: string;
  matchedSkuId: number | null;
  matchStatus: string;
};

export type SkuTransactionPayload = {
  sku: {
    skuId: number;
    skuCode: string;
    skuName: string;
    barcode: string;
    unit: string;
    goodsName: string;
  };
  total: number;
  rows: SkuTransactionRow[];
  balance: number;
};

export type InventorySkuRow = {
  skuId: number;
  jackyunSkuId: string;
  skuCode: string;
  productType: "single" | "bundle" | "virtual_bundle";
  skuName: string;
  goodsName: string;
  barcode: string;
  unit: string;
  status: string;
  quantity: string | null;
  hasMovement: boolean;
  warehouses: { warehouseId: number | null; warehouseName: string; quantity: string | null }[];
  lastDocumentAt: string | null;
};
export type SalesOrderRow = {
  id: number; orderNo: string; platform: string; storeName: string;
  orderStatus: string; payStatus: string;
  orderAmount: string | null; paidAmount: string | null; orderedAt: string | null;
  itemName: string; quantity: string | null; itemCount: number;
};
export type AftersaleRow = {
  id: number; aftersaleNo: string; orderNo: string; type: string; status: string;
  refundAmount: string | null; reason: string; createdAt: string | null;
};
export type CatalogSkuRow = {
  id: number; jackyunSkuId: string; skuCode: string; skuName: string; goodsName: string;
  productType: "single" | "bundle" | "virtual_bundle";
  barcode: string; unit: string; salePrice: string | null; defaultCost: string | null;
  costMode: "fixed" | "dynamic"; costTolerancePct: string; taxCode: string;
  taxCategoryRuleId: number | null; taxCategoryRuleName: string; status: string;
  consumablePolicy?: "auto" | "none";
};

export type TaxCategoryRule = {
  id: number; pattern: string; categoryName: string; itemName: string; taxCode: string;
  matchKeyword: string; matchMode: "contains" | "exact" | "prefix";
  priority: number; enabled: boolean; note: string;
};

export type LinkedSkuRef = { skuId: number; skuCode: string; skuName: string };

export type BundleBulkDeleteResult = {
  ok: boolean;
  dryRun: boolean;
  requested: number;
  deleted: number;
  deletedIds: number[];
  blocked: Array<{
    id: number;
    skuCode: string;
    reason: string;
    references: Array<{ label: string; count: number }>;
  }>;
  invalid: Array<{ id: number; skuCode: string; reason: string }>;
  notFound: number[];
};

export type CatalogBulkDeleteItem = { kind: "goods" | "consumable"; id: number };
export type CatalogBulkDeleteResult = {
  ok: boolean;
  dryRun: boolean;
  requested: number;
  deleted: number;
  deletedItems: Array<{ kind: "goods" | "consumable"; id: number; code: string }>;
  blocked: Array<{
    kind: "goods" | "consumable";
    id: number;
    code: string;
    reason: string;
    references: Array<{ label: string; count: number; numbers?: string[] }>;
  }>;
  invalid: Array<{ kind: "goods" | "consumable"; id: number; code: string; reason: string }>;
  notFound: Array<{ kind: "goods" | "consumable"; id: number }>;
};

export type CatalogBulkStatusResult = {
  updated: number;
  items: Array<{ kind: "goods" | "consumable"; id: number; code: string; status: string }>;
  notFound: Array<{ kind: "goods" | "consumable"; id: number }>;
};

/** 统一货品档案行：kind=goods（正品，库存读吉客云快照）/ kind=consumable（耗材，库存本系统三仓维护）。 */
export type UnifiedCatalogRow = {
  kind: "goods" | "consumable";
  id: number; code: string; name: string; goodsName: string;
  jackyunSkuId?: string;
  barcode: string; unit: string; category: string; goodsCategory: string; status: string;
  stockOwn: string | null;   // 正品=独立运算库存；耗材=自有仓
  stockFactory: string | null; // 仅耗材：工厂仓
  stockTransit: string | null; // 仅耗材：在途
  minStock: string | null;   // 仅耗材：安全库存
  lowStock: boolean;         // 仅耗材：可用≤安全库存或负库存
  hasMovement?: boolean;     // 仅正品：是否存在已纳入运算的出入库单据
  linkedSkus: LinkedSkuRef[];
  costMode: "fixed" | "dynamic" | null;
  costTolerancePct: string | null;
  taxCode: string;
  taxCategoryRuleId: number | null;
  taxCategoryRuleName: string;
  salePrice: string | null;
  defaultCost: string | null;
  purchaseUnitCost?: string | null;
};

export type ConsumableRow = {
  id: number; code: string; name: string; barcode: string; category: string; unit: string;
  purchaseUnitCost: string | null; purchasedQty: string; usedQty: string;
  stockQty: string; factoryQty: string; transitQty: string; availableQty: string;
  minStockQty: string; usageRate: string; taxCode: string;
  taxCategoryRuleId: number | null; taxCategoryRuleName: string;
  status: string; mappingCount: number; linkedSkus: LinkedSkuRef[]; lowStock: boolean;
  mappingDetails?: Array<LinkedSkuRef & { usagePerUnit: string | null }>;
  totalStockQty?: string; totalUsedQty?: string; currentStockQty?: string;
  lossQty?: string; adjustmentQty?: string; pendingProductionQty?: string;
  usagePerUnit?: string | null; supportQty?: string | null; coveragePct?: string | null;
  gapQty?: string | null; inventoryStatus?: "正常" | "偏低" | "缺货";
};
export type ConsumableMappingRow = {
  id: number; skuId: number; skuCode: string; skuName: string;
  consumableId: number; consumableCode: string; consumableName: string;
  usagePerUnit: string; note: string;
};
export type ConsumableTransactionRow = {
  id: number; transactionType: string; quantity: string; unitCost: string | null;
  location: string | null; warehouseId: number | null; warehouseCode: string; warehouseName: string;
  stockBefore: string | null; stockAfter: string | null;
  factoryBefore: string | null; factoryAfter: string | null;
  sourceType: string; sourceId: number | null; note: string; occurredAt: string | null;
  purchaseId: number | null; orderId: number | null;
  inboundDocumentId: number | null; inboundDocumentNo: string; inboundDocumentAt: string | null; inboundWarehouseName: string;
};

export type ConsumablePurchaseItem = {
  id: number; consumableId: number; code: string; name: string; unit: string;
  quantity: string; receivedQty: string; unitCost: string;
};
export type ConsumablePurchaseRow = {
  id: number; number: string; supplierName: string; orderedOn: string;
  sourceOrderId: number | null; sourceOrderNo: string | null; referenceNo: string;
  status: "ordered" | "partial" | "received" | "cancelled";
  note: string; amount: string; receivedAmount: string; items: ConsumablePurchaseItem[];
};
export type ConsumablePurchaseDetail = ConsumablePurchaseRow & {
  receipts: Array<{ id: number; number: string; receivedOn: string; note: string; createdBy: string; location: string;
    warehouseId?: number | null; warehouseCode?: string; warehouseName?: string;
    items: Array<{ consumableId: number; name: string; quantity: string; unit: string }> }>;
};
export type ConsumablePurchaseSource = { id: number; orderNo: string; supplierName: string };
export type WarehouseRow = {
  id: number; code: string; name: string; warehouseType: "factory" | "b2c" | "other";
  purpose: "goods" | "consumable" | "both"; isSellable: boolean; status: "active" | "inactive";
  note: string; jackyunWarehouseId: string | null;
};

export const dashboardApi = {
  salesTrend: (days = 30, start?: string, end?: string) =>
    jsonFetch<TrendPoint[]>(`/api/v1/dashboard/sales-trend?days=${days}${start ? `&start=${start}` : ""}${end ? `&end=${end}` : ""}`),
  platformRanking: (start?: string, end?: string) =>
    jsonFetch<PlatformRow[]>(`/api/v1/dashboard/platform-ranking${start ? `?start=${start}${end ? `&end=${end}` : ""}` : end ? `?end=${end}` : ""}`),
  skuRanking: (limit = 20, start?: string, end?: string) =>
    jsonFetch<SkuRow[]>(`/api/v1/dashboard/sku-ranking?limit=${limit}${start ? `&start=${start}` : ""}${end ? `&end=${end}` : ""}`),
  inventory: () => jsonFetch<InventorySummary>("/api/v1/dashboard/inventory"),
  inventorySkus: (search = "", limit = 1000) => {
    const q = new URLSearchParams({ search, limit: String(limit) });
    return jsonFetch<InventorySkuRow[]>(`/api/v1/dashboard/inventory/skus?${q}`);
  },
  /** 正品 SKU 逐笔出入库流水（本地吉客云单据事实，口径与库存运算一致）。 */
  skuTransactions: (skuId: number, limit = 500) =>
    jsonFetch<SkuTransactionPayload>(`/api/v1/dashboard/inventory/sku-transactions?sku_id=${skuId}&limit=${limit}`),
  products: (search = "", limit = 200) => {
    const q = new URLSearchParams({ search, limit: String(limit) });
    return jsonFetch<CatalogSkuRow[]>(`/api/v1/dashboard/products?${q}`);
  },
  updateCostPolicy: (skuId: number, costMode: "fixed" | "dynamic", costTolerancePct?: string) =>
    jsonFetch<{ id: number; costMode: string; costTolerancePct: string }>(`/api/v1/dashboard/products/${skuId}/cost-policy`, {
      method: "POST", body: JSON.stringify({ cost_mode: costMode, cost_tolerance_pct: costTolerancePct }),
    }),
  updateConsumablePolicy: (skuId: number, policy: "auto" | "none") =>
    jsonFetch<{ id: number; consumablePolicy: "auto" | "none" }>(`/api/v1/dashboard/products/${skuId}/consumable-policy`, {
      method: "POST", body: JSON.stringify({ policy }),
    }),
  saveProduct: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; skuCode: string; jackyunSkuId: string }>("/api/v1/dashboard/products/save", { method: "POST", body: JSON.stringify(body) }),
  catalogUnified: (kind: "all" | "goods" | "consumable" = "all", search = "", limit = 500) => {
    const q = new URLSearchParams({ kind, search, limit: String(limit) });
    return jsonFetch<UnifiedCatalogRow[]>(`/api/v1/dashboard/catalog-unified?${q}`);
  },
  bulkSetTaxCode: (items: { kind: "goods" | "consumable"; id: number }[], taxCode: string, overwrite: boolean) =>
    jsonFetch<{ updated: number; skipped: number; missing: number }>("/api/v1/dashboard/catalog/tax-code/bulk", {
      method: "POST", body: JSON.stringify({ items, tax_code: taxCode, overwrite }),
    }),
  bulkDeleteBundles: (ids: number[], dryRun = false) =>
    jsonFetch<BundleBulkDeleteResult>("/api/v1/dashboard/catalog/bundles/bulk-delete", {
      method: "POST", body: JSON.stringify({ ids, dry_run: dryRun }),
    }),
  bulkDeleteCatalog: (items: CatalogBulkDeleteItem[], dryRun = false) =>
    jsonFetch<CatalogBulkDeleteResult>("/api/v1/dashboard/catalog/bulk-delete", {
      method: "POST", body: JSON.stringify({ items, dry_run: dryRun }),
    }),
  bulkCatalogStatus: (items: CatalogBulkDeleteItem[], status: "active" | "inactive") =>
    jsonFetch<CatalogBulkStatusResult>("/api/v1/dashboard/catalog/bulk-status", {
      method: "POST", body: JSON.stringify({ items, status }),
    }),
  updateCategory: (kind: "goods" | "consumable", id: number, category: string) =>
    jsonFetch<{ ok: boolean; category: string }>("/api/v1/dashboard/catalog/category", {
      method: "POST", body: JSON.stringify({ kind, id, category }),
    }),
  orders: (status?: string) =>
    jsonFetch<SalesOrderRow[]>(`/api/v1/dashboard/orders${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  aftersales: () => jsonFetch<AftersaleRow[]>("/api/v1/dashboard/aftersales"),
};

export const taxAccountingApi = {
  categoryRules: () => jsonFetch<{ items: TaxCategoryRule[] }>("/api/v1/tax-accounting/category-rules?include_disabled=true"),
};

export type FinanceLegalEntity = {
  id: number;
  code: string;
  name: string;
  countryCode: string;
  baseCurrency: string;
  taxId: string;
  status: string;
  isDefault: boolean;
  businessScopes: string[];
  note: string;
};

export type FinanceVoucherLine = {
  seq: number;
  accountCode: string;
  accountName: string;
  direction: "debit" | "credit" | string;
  amount: number;
  taxAmount: number;
  summary: string;
  entryId: number | null;
};

export type FinanceVoucher = {
  id: number;
  voucherNo: string;
  year: number;
  month: number;
  voucherDate: string | null;
  currency: string;
  source: string;
  status: "draft" | "posted" | string;
  note: string;
  debitTotal: number;
  creditTotal: number;
  balanced: boolean;
  lines: FinanceVoucherLine[];
};

export const financeEntityApi = {
  list: () => jsonFetch<{ items: FinanceLegalEntity[] }>("/api/v1/finance/entities"),
};

export const financeVoucherApi = {
  list: (params: { legalEntityId: number; year: number; month: number }) => {
    const query = new URLSearchParams({
      legal_entity_id: String(params.legalEntityId),
      year: String(params.year),
      month: String(params.month),
    });
    return jsonFetch<FinanceVoucher[]>(`/api/v1/finance/vouchers?${query.toString()}`);
  },
  generate: (params: { legalEntityId: number; year: number; month: number }) =>
    jsonFetch<{ legalEntityId: number; year: number; month: number; generated: number; skipped: number; source: string }>(
      "/api/v1/finance/vouchers",
      {
        method: "POST",
        body: JSON.stringify({
          legal_entity_id: params.legalEntityId,
          year: params.year,
          month: params.month,
        }),
      },
    ),
  post: (voucherId: number) =>
    jsonFetch<{ id: number; status: string }>(`/api/v1/finance/vouchers/${voucherId}/post`, { method: "POST" }),
};

export const consumablesApi = {
  list: (search = "") => jsonFetch<ConsumableRow[]>(`/api/v1/consumables?search=${encodeURIComponent(search)}`),
  save: (body: Record<string, unknown>) => jsonFetch<ConsumableRow>("/api/v1/consumables", { method: "POST", body: JSON.stringify(body) }),
  importXlsx: async (file: File) => {
    const form = new FormData(); form.append("file", file);
    const res = await authenticatedFetch("/api/v1/consumables/import-xlsx", { method: "POST", body: form });
    if (!res.ok) { const data = await res.json().catch(() => ({})) as { detail?: unknown }; throw new Error(detailToMessage(data.detail, `导入失败（${res.status}）`)); }
    return res.json() as Promise<{ ok: boolean; created: number; updated: number; skipped: number; mappings: number }>;
  },
  transactions: (id: number) => jsonFetch<ConsumableTransactionRow[]>(`/api/v1/consumables/${id}/transactions`),
  addTransaction: (id: number, body: Record<string, unknown>) => jsonFetch<{ id: number }>(`/api/v1/consumables/${id}/transactions`, { method: "POST", body: JSON.stringify(body) }),
  mappings: (consumableId?: number) => jsonFetch<ConsumableMappingRow[]>(`/api/v1/consumables/mappings/list${consumableId ? `?consumable_id=${consumableId}` : ""}`),
  saveMapping: (body: Record<string, unknown>) => jsonFetch<{ id: number }>("/api/v1/consumables/mappings", { method: "POST", body: JSON.stringify(body) }),
  deleteMapping: (id: number) => jsonFetch<{ ok: boolean }>(`/api/v1/consumables/mappings/${id}`, { method: "DELETE" }),
  purchases: (search = "", sourceOrderId?: number, orderNo?: string) => jsonFetch<ConsumablePurchaseRow[]>(`/api/v1/consumables/purchases?search=${encodeURIComponent(search)}${sourceOrderId ? `&source_order_id=${sourceOrderId}` : ""}${orderNo ? `&order_no=${encodeURIComponent(orderNo)}` : ""}`),
  purchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}`),
  purchaseSources: (search = "") => jsonFetch<ConsumablePurchaseSource[]>(`/api/v1/consumables/purchases/source-orders?search=${encodeURIComponent(search)}`),
  createPurchase: (body: Record<string, unknown>) => jsonFetchWithNetworkRetry<ConsumablePurchaseDetail>("/api/v1/consumables/purchases", { method: "POST", body: JSON.stringify(body) }),
  receivePurchase: (id: number, body: Record<string, unknown>) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/receipts`, { method: "POST", body: JSON.stringify(body) }),
  cancelPurchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/cancel`, { method: "POST" }),
  reopenPurchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/reopen`, { method: "POST" }),
  updatePurchase: (id: number, body: Record<string, unknown>) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePurchase: (id: number) => jsonFetch<{ ok: boolean; purchaseId: number }>(`/api/v1/consumables/purchases/${id}`, { method: "DELETE" }),
};

export type InventoryStocktakeCandidate = {
  kind: "goods" | "consumable";
  id: number;
  code: string;
  name: string;
  goodsName: string;
  category: string;
  unit: string;
  bookQty: string;
  warehouseId: number;
  warehouseName: string;
};

export type InventoryStocktakeItem = {
  id: number;
  kind: "goods" | "consumable";
  refId: number;
  code: string;
  name: string;
  goodsName: string;
  category: string;
  unit: string;
  bookQty: string;
  actualQty: string | null;
  differenceQty: string | null;
  reason: string;
};

export type InventoryStocktakeTask = {
  id: number;
  number: string;
  scope: "all" | "partial";
  scopeLabel: string;
  status: "pending" | "counting" | "review" | "completed" | "cancelled";
  statusLabel: string;
  warehouseId: number;
  warehouseName: string;
  itemKinds: Array<"goods" | "consumable">;
  searchText: string;
  categoryFilter: string;
  note: string;
  createdBy: string;
  confirmedBy: string;
  confirmedAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  itemCount: number;
  countedCount: number;
  differenceCount: number;
  differenceAbsQty: string;
  items?: InventoryStocktakeItem[];
};

export const inventoryStocktakeApi = {
  list: (status = "") =>
    jsonFetch<InventoryStocktakeTask[]>(`/api/v1/inventory/stocktakes${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  candidates: (warehouseId: number, kinds: Array<"goods" | "consumable">, search = "", category = "") => {
    const q = new URLSearchParams({
      warehouse_id: String(warehouseId),
      kinds: kinds.join(","),
      search,
      category,
    });
    return jsonFetch<InventoryStocktakeCandidate[]>(`/api/v1/inventory/stocktakes/candidates?${q}`);
  },
  create: (body: {
    scope: "all" | "partial";
    warehouse_id: number;
    item_kinds: Array<"goods" | "consumable">;
    selected_items?: Array<{ kind: "goods" | "consumable"; id: number }>;
    search?: string;
    category?: string;
    note?: string;
  }) =>
    jsonFetch<InventoryStocktakeTask>("/api/v1/inventory/stocktakes", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  detail: (id: number) =>
    jsonFetch<InventoryStocktakeTask>(`/api/v1/inventory/stocktakes/${id}`),
  saveCounts: (id: number, items: Array<{ id: number; actual_qty: string | null; reason: string }>) =>
    jsonFetch<InventoryStocktakeTask>(`/api/v1/inventory/stocktakes/${id}/counts`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  confirm: (id: number) =>
    jsonFetch<InventoryStocktakeTask>(`/api/v1/inventory/stocktakes/${id}/confirm`, { method: "POST" }),
  cancel: (id: number) =>
    jsonFetch<InventoryStocktakeTask>(`/api/v1/inventory/stocktakes/${id}/cancel`, { method: "POST" }),
};

export const warehousesApi = {
  list: (includeInactive = false) =>
    jsonFetch<WarehouseRow[]>(`/api/v1/warehouses?include_inactive=${includeInactive ? "true" : "false"}`),
  receiveConsumablePurchase: (purchaseId: number, body: Record<string, unknown>) =>
    jsonFetch<ConsumablePurchaseDetail>(`/api/v1/warehouses/consumable-purchases/${purchaseId}/receipts`, {
      method: "POST", body: JSON.stringify(body),
    }),
};

// ---------- 期初初始化（Phase 6） ----------

export type OpeningRow = {
  id: number; kind: string; ref: string; amount: string | null; quantity: string | null;
  asOfDate: string | null; note: string; createdAt: string | null;
};
export type OpeningData = {
  summary: { byKind: Record<string, string>; adjustmentCount: number; differencePool: string; skuWithCost: number };
  items: OpeningRow[];
};

export const openingApi = {
  list: () => jsonFetch<OpeningData>("/api/v1/opening"),
  upsert: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number }>("/api/v1/opening", { method: "POST", body: JSON.stringify(body) }),
  remove: (id: number) => jsonFetch<{ ok: boolean }>(`/api/v1/opening/${id}`, { method: "DELETE" }),
  adjust: (id: number, delta: string, reason: string) =>
    jsonFetch<{ id: number; delta: string }>(`/api/v1/opening/${id}/adjust`, {
      method: "POST",
      body: JSON.stringify({ delta, reason }),
    }),
};

// ---------- 月结快照（Phase 6） ----------

export type ClosingRow = {
  id: number; periodId: number; version: number; isCurrent: boolean; period: string;
  calculatedAt: string | null; grossProfit: string | null;
  receivable: string | null; received: string | null; createdAt: string | null;
};

export const closingApi = {
  versions: (year?: number, month?: number) => {
    const q = new URLSearchParams();
    if (year) q.set("year", String(year));
    if (month) q.set("month", String(month));
    return jsonFetch<ClosingRow[]>(`/api/v1/closing/versions${q.toString() ? `?${q}` : ""}`);
  },
  snapshot: (year: number, month: number) =>
    jsonFetch<{ id: number; version: number; period: string; note?: string }>("/api/v1/closing/snapshot", {
      method: "POST",
      body: JSON.stringify({ year, month }),
    }),
  recalc: (year: number, month: number) =>
    jsonFetch<{ id: number; version: number; period: string; note: string }>("/api/v1/closing/recalc", {
      method: "POST",
      body: JSON.stringify({ year, month }),
    }),
};

// ---------- 自动化（Phase 6） ----------

export type ScheduleItem = { task: string; args: string; label: string; frequency: string };
export type SyncJobRow = {
  id: number; provider: string; jobType: string; status: string;
  startedAt: string | null; finishedAt: string | null;
  stats: Record<string, unknown>; errorSummary: string;
};
export type SyncLogRow = {
  id: number; provider: string; level: string; message: string; jobId: number | null;
};

export const automationApi = {
  schedule: () => jsonFetch<{ items: ScheduleItem[]; note: string }>("/api/v1/automation/schedule"),
  jobs: (limit = 50) => jsonFetch<SyncJobRow[]>(`/api/v1/automation/jobs?limit=${limit}`),
  logs: (limit = 100) => jsonFetch<SyncLogRow[]>(`/api/v1/automation/logs?limit=${limit}`),
  runJackyun: (jobType: string) =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>(`/api/v1/automation/run/jackyun/${encodeURIComponent(jobType)}`, { method: "POST" }),
  runJkyOrders: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>("/api/v1/jky-orders/sync", { method: "POST" }),
  run1688: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>("/api/v1/automation/run/1688", { method: "POST" }),
};

export type JkyOrderChannel = {
  provider: string;
  label: string;
  priority: number | null;
  configured: boolean;
  status: string;
  verified: boolean;
  lastTestedAt: string | null;
  lastSuccessAt: string | null;
  errorSummary: string | null;
};

export type JkyOrderStatus = {
  providerPriority: string[];
  priorityWarnings: string[];
  channels: JkyOrderChannel[];
  lastRun: {
    id: number | null;
    status: string | null;
    startedAt: string | null;
    finishedAt: string | null;
    stats: Record<string, unknown>;
    errorSummary: string;
  };
  checkpoint: Record<string, unknown>;
};

export const jkyOrderApi = {
  status: () => jsonFetch<JkyOrderStatus>("/api/v1/jky-orders/status"),
  sync: () => automationApi.runJkyOrders(),
};

// ---------- 吉客云客户端文件导入 ----------

export type JackyunFileImportRow = {
  id: number;
  originalName: string;
  size: number;
  sha256: string;
  reportType: string;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  sheetName: string;
  headers: string[];
  rowCount: number;
  stagedRowCount: number;
  errorSummary: string;
  uploader: string;
  parsedAt: string | null;
  createdAt: string | null;
  /** 确认时后端按表头自动映射的结果（采购单 / 入库申请单货品 / 无匹配则仅存档）。 */
  mapResult?: {
    ok: boolean;
    mapper?: string;
    skipped?: boolean;
    reason?: string;
    documents?: number;
    matched?: number;
    matchedItems?: number;
    filledFields?: number;
    sourceRows?: number;
    relationshipRows?: number;
    uniqueRelationships?: number;
    createdLinks?: number;
    alreadyLinked?: number;
    pendingLinks?: number;
    rejectedLinks?: number;
    createdExternalOrders?: number;
    externalOrderNos?: string[];
    allocSeeded?: number;
    missingRk?: string[];
    mapped?: number;
    updated?: number;
    error?: string;
  };
  /** 采购报表确认时后端自动映射为业务采购单的结果（其他类型无此字段）。 */
  mapPurchase?: {
    ok: boolean;
    detailReport?: boolean;
    groups?: number;
    mapped?: number;
    updated?: number;
    skipped?: { row: number; reason: string }[];
    error?: string;
  };
};

export type SupplierRecord = {
  id: number;
  partnerId: number | null;
  name: string;
  platform: string;
  externalShopId: string;
  contact: string;
  taxNo: string;
  phone: string;
  address: string;
  notes: string;
  isTemp: boolean;
  purchaseType: "regular" | "temporary";
  orderCount?: number;
  formerNames: string[];
  createdAt: string | null;
};

export type SupplierInput = {
  name: string;
  platform?: string;
  externalShopId?: string;
  contact?: string;
  taxNo?: string;
  phone?: string;
  address?: string;
  notes?: string;
  isTemp?: boolean;
};

export const supplierApi = {
  list: (keyword = "", status = "all") =>
    jsonFetch<SupplierRecord[]>(
      `/api/v1/suppliers?keyword=${encodeURIComponent(keyword)}&status=${encodeURIComponent(status)}`,
    ),
  create: async (payload: SupplierInput) => {
    const res = await authenticatedFetch("/api/v1/suppliers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    return (await res.json()) as SupplierRecord;
  },
  update: async (id: number, payload: SupplierInput) => {
    const res = await authenticatedFetch(`/api/v1/suppliers/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    return (await res.json()) as SupplierRecord;
  },
  remove: async (id: number) => {
    const res = await authenticatedFetch(`/api/v1/suppliers/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    return (await res.json()) as { ok: boolean };
  },
  resolve: (taxNos: string[]) =>
    jsonFetch<{ matched: Record<string, { id: number; name: string; isTemp: boolean }>; unmatched: string[] }>(
      "/api/v1/suppliers/resolve",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ taxNos }) },
    ),
};

// ---------- 财务中心：统一往来单位档案 ----------

export type BusinessPartnerRole = "supplier" | "customer" | "counterparty";

export type BusinessPartnerBankAccount = {
  bankName: string;
  accountNo: string;
  accountName: string;
  isPrimary: boolean;
};

export type BusinessPartnerIdentifier = {
  id: number;
  kind: "name" | "alias" | "tax_no" | "bank_account" | "customer_code" | string;
  value: string;
  isPrimary: boolean;
  source: string;
};

export type BusinessPartnerSummary = {
  purchaseOrderCount: number;
  purchaseAmount: number;
  inboundCount: number;
  inboundAmount: number;
  invoiceCount: number;
  invoiceAmount: number;
  bankTransactionCount: number;
  bankPaidAmount: number;
  bankReceivedAmount: number;
  purchasePaymentDifference: number;
  invoicePaymentDifference: number;
  salesOrderCount: number;
  salesReceivedAmount: number;
  needsReviewCount: number;
};

export type BusinessPartnerListItem = {
  id: number;
  name: string;
  taxNo: string;
  roles: BusinessPartnerRole[];
  status: string;
  identifiers: BusinessPartnerIdentifier[];
  formerNames: string[];
  legacySupplierId: number | null;
  summary: BusinessPartnerSummary;
  possibleDuplicateCount: number;
};

export type BusinessPartnerDuplicate = {
  id: number;
  name: string;
  taxNo: string;
};

export type BusinessPartnerDetail = BusinessPartnerListItem & {
  possibleDuplicates: BusinessPartnerDuplicate[];
  contact: string;
  phone: string;
  address: string;
  bankName: string;
  bankAccountNo: string;
  bankAccountName: string;
  bankAccounts: BusinessPartnerBankAccount[];
  notes: string;
  createdAt: string | null;
  updatedAt: string | null;
  purchases: Array<{
    sourceType: string; id: number; no: string; platform: string; title: string; date: string | null;
    amount: number; paidAmount: number; status: string;
  }>;
  inbounds: Array<{
    sourceType: string; id: number; no: string; date: string | null; warehouse: string;
    quantity: number; amount: number;
  }>;
  invoices: Array<{
    id: number; no: string; date: string | null; direction: string; status: string;
    sellerName: string; buyerName: string; amount: number; effectiveAmount?: number; bankPaidAmount: number;
    bankRemainingAmount: number; matchStatus: string; category: string; verified: boolean;
    invoiceColor?: "blue" | "red" | "unknown" | string;
    invoiceStatusLabel?: string;
    redStatus?: string;
    redOffsetAmount?: number;
    redRelatedInvoiceNo?: string;
    accountingException?: string;
  }>;
  payments: Array<{
    id: number; date: string | null; transactionTime: string | null; direction: string; amount: number;
    counterpartyName: string; counterpartyAccount: string; summary: string; serialNo: string;
    voucherNo: string; sourceRowNumber: number | null; rawAvailable: boolean; rawUrl: string;
    invoices: Array<{ invoiceId: number; invoiceNo: string; allocatedAmount: number }>;
  }>;
  sales: Array<{
    id: number; no: string; sourceNo: string; date: string | null; paidAt: string | null;
    platform: string; customerCode: string; amount: number; paidAmount: number; status: string;
  }>;
  reviewItems: Array<{
    linkId: number; sourceType: string; sourceLabel: string; sourceId: number; relationRole: string;
    rawName: string; rawTaxNo: string; rawAccountNo: string; candidatePartnerIds: number[];
    evidence: Record<string, unknown>; no: string; date: string | null; amount: number;
  }>;
};

export type PartnerReferenceCoverage = {
  sources: Record<string, {
    total: number;
    linked: number;
    unlinked: number;
    coverage: number;
  }>;
  totalFacts: number;
  linkedFacts: number;
  unlinkedFacts: number;
  coverage: number;
};

export type BusinessPartnerInput = {
  name: string;
  roles: BusinessPartnerRole[];
  taxNo?: string;
  contact?: string;
  phone?: string;
  address?: string;
  bankName?: string;
  bankAccountNo?: string;
  bankAccountName?: string;
  formerNames?: string[];
  bankAccounts?: BusinessPartnerBankAccount[];
  notes?: string;
};

export const businessPartnerApi = {
  list: (keyword = "", role: "all" | BusinessPartnerRole = "all") =>
    jsonFetch<{ total: number; items: BusinessPartnerListItem[] }>(
      `/api/v1/finance/partners?keyword=${encodeURIComponent(keyword)}&role=${encodeURIComponent(role)}`,
    ),
  detail: (id: number) => jsonFetch<BusinessPartnerDetail>(`/api/v1/finance/partners/${id}`),
  coverage: () =>
    jsonFetch<PartnerReferenceCoverage>("/api/v1/finance/partners/coverage"),
  recheck: (id: number) => jsonFetch<{
    ok: boolean;
    partnerId: number;
    createdLinks: number;
    updatedLinks: number;
    needsReview: number;
    partnerMatchesAdded: number;
    partnerInvoicePaidBefore: number;
    partnerInvoicePaidAfter: number;
    bankInvoiceMatchesCreated: number;
    bankInvoiceRepaired: number;
    bankInvoiceAmbiguous: number;
    detail: BusinessPartnerDetail;
  }>(`/api/v1/finance/partners/${id}/recheck`, { method: "POST" }),
  sync: () => jsonFetch<{
    ok: boolean;
    createdPartners: number;
    updatedPartners: number;
    createdLinks: number;
    updatedLinks: number;
    needsReview: number;
    bankInvoicePeriods: number;
    bankInvoiceMatchesCreated: number;
    bankInvoiceRepaired: number;
    bankInvoiceAmbiguous: number;
  }>(
    "/api/v1/finance/partners/sync", { method: "POST" },
  ),
  create: (body: BusinessPartnerInput) =>
    jsonFetch<BusinessPartnerDetail>("/api/v1/finance/partners", { method: "POST", body: JSON.stringify(body) }),
  update: (id: number, body: BusinessPartnerInput) =>
    jsonFetch<BusinessPartnerDetail>(`/api/v1/finance/partners/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  addIdentifier: (id: number, kind: BusinessPartnerIdentifier["kind"], value: string) =>
    jsonFetch<BusinessPartnerDetail>(`/api/v1/finance/partners/${id}/identifiers`, {
      method: "POST", body: JSON.stringify({ kind, value }),
    }),
  decideDuplicate: (id: number, otherId: number, body: { same: boolean; note?: string }) =>
    jsonFetch<BusinessPartnerDetail>(`/api/v1/finance/partners/${id}/duplicates/${otherId}/decide`, {
      method: "POST", body: JSON.stringify(body),
    }),
  claimReview: (id: number, linkId: number, note = "") =>
    jsonFetch<BusinessPartnerDetail>(`/api/v1/finance/partners/${id}/review-links/${linkId}/claim`, {
      method: "POST", body: JSON.stringify({ note }),
    }),
};

export const jackyunFileApi = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<JackyunFileImportRow[]>(`/api/v1/jackyun-files/imports${q}`);
  },
  upload: async (file: File, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = `?auto_confirm=${autoConfirm ? "true" : "false"}`;
    const res = await authenticatedFetch(`/api/v1/jackyun-files/imports${query}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{ duplicate: boolean; lifecycle: string; import: JackyunFileImportRow }>;
  },
  confirm: (id: number) =>
    jsonFetch<JackyunFileImportRow>(`/api/v1/jackyun-files/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/jackyun-files/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<JackyunFileImportRow>(`/api/v1/jackyun-files/imports/${id}/restore`, { method: "POST" }),
  records: (id: number) =>
    jsonFetch<JackyunRecordPreview[]>(`/api/v1/jackyun-files/imports/${id}/records`),
  deleteRow: (id: number, rowIndex: number) =>
    noContentFetch(`/api/v1/jackyun-files/imports/${id}/records/${rowIndex}`),
  restoreRow: (id: number, rowIndex: number) =>
    jsonFetch<{ rowIndex: number; rowStatus: string }>(
      `/api/v1/jackyun-files/imports/${id}/records/${rowIndex}/restore`,
      { method: "POST" }
    ),
};

export type MasterDataImportResult = {
  ok: boolean;
  dataset: string;
  sheet: string;
  rows: number;
  skipped?: number;
  created: number;
  updated: number;
  mappingsCreated?: number;
  mappingsRemoved?: number;
  linkedProducts?: number;
  linkedConsumables?: number;
  ignoredInventoryFields?: string[];
};

export const masterDataApi = {
  importXlsx: async (dataset: "catalog" | "bundles" | "tax_rules" | "warehouses" | "suppliers" | "external_orders", file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await authenticatedFetch(`/api/v1/data/import/${dataset}`, { method: "POST", body: form });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `回导失败（${res.status}）`));
    }
    return res.json() as Promise<MasterDataImportResult>;
  },
};

// ---------- 其他渠道采购订单主档 ----------

export type ExternalPurchaseOrderRow = {
  id: number;
  externalOrderId: string;
  platform: "pdd" | "taobao" | "other" | "1688" | string;
  buyerAccount: string;
  supplierName: string;
  title: string;
  orderAmount: string | null;
  paidAmount: string | null;
  orderedAt: string | null;
  orderStatus: string;
  purchaseStatus: string;
  unallocated: string;
  balanced: boolean;
  source: string;
  sourceImportId: number | null;
};

export const externalPurchaseOrderApi = {
  list: (query = "") => {
    const qs = new URLSearchParams({ platform: "non_1688" });
    if (query.trim()) qs.set("q", query.trim());
    return jsonFetch<ExternalPurchaseOrderRow[]>(`/api/v1/purchase/orders?${qs}`);
  },
  create: (body: {
    external_order_id: string;
    platform: string;
    supplier_name: string;
    title?: string;
    ordered_at?: string | null;
    order_amount?: string | null;
    paid_amount?: string | null;
  }) => jsonFetch<{ id: number; workbenchOrderId: number; externalOrderId: string }>(
    "/api/v1/purchase/orders",
    { method: "POST", body: JSON.stringify(body) }
  ),
  update: (id: number, body: {
    platform?: string;
    supplier_name?: string;
    title?: string;
    ordered_at?: string | null;
    order_amount?: string;
    paid_amount?: string;
  }) => jsonFetch<{ ok: boolean; id: number; externalOrderId: string; platform: string }>(
    `/api/v1/purchase/orders/${id}`,
    { method: "PATCH", body: JSON.stringify(body) }
  ),
};

export type JackyunRecordPreview = {
  rowIndex: number;
  rowStatus: string;
  payload: Record<string, string>;
};

// ---------- 1688 订单导入 ----------

export type Alibaba1688ImportRow = {
  id: number;
  fileName: string;
  fileHash: string;
  fileSize: number;
  orderCount: number;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  errorMessage: string | null;
  uploader: string;
  createdAt: string | null;
};

export const alibaba1688Api = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<Alibaba1688ImportRow[]>(`/api/v1/alibaba1688-imports/imports${q}`);
  },
  upload: async (file: File, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = `?auto_confirm=${autoConfirm ? "true" : "false"}`;
    const res = await authenticatedFetch(`/api/v1/alibaba1688-imports/upload${query}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{
      duplicate: boolean;
      lifecycle: string;
      import: Alibaba1688ImportRow;
      message?: string;
      import_id?: number;
      order_count?: number;
    }>;
  },
  confirm: (id: number) =>
    jsonFetch<Alibaba1688ImportRow>(`/api/v1/alibaba1688-imports/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/alibaba1688-imports/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<Alibaba1688ImportRow>(`/api/v1/alibaba1688-imports/imports/${id}/restore`, { method: "POST" }),
  orders: (id: number) =>
    jsonFetch<Alibaba1688OrderPreview[]>(`/api/v1/alibaba1688-imports/imports/${id}/orders`),
  deleteRow: (id: number, orderId: number) =>
    noContentFetch(`/api/v1/alibaba1688-imports/imports/${id}/orders/${orderId}`),
  restoreRow: (id: number, orderId: number) =>
    jsonFetch<Alibaba1688OrderPreview>(
      `/api/v1/alibaba1688-imports/imports/${id}/orders/${orderId}/restore`,
      { method: "POST" }
    ),
};

// ---------- 1688 浏览器直采通道 ----------

export type Alibaba1688BrowserStatus = {
  enabled: boolean;
  profileDir: string;
  headless: boolean;
  status: string;
  account: string | null;
  lastSyncAt: string | null;
  lastSyncSummary: string | null;
  lastLoginCheckAt: string | null;
  errorSummary: string | null;
  lastSyncJobId: number | null;
  maxPages: number;
  stopAfterKnown: number;
  lookbackDays: number;
};

export type Alibaba1688BrowserJob = {
  id: number;
  jobType: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  stats: Record<string, unknown>;
  errorSummary: string;
};

export type Alibaba1688SyncMode = "incremental" | "range" | "single";
export type Alibaba1688TimeField = "order_time" | "pay_time";
export type Alibaba1688SyncOptions = {
  mode: Alibaba1688SyncMode;
  startDate?: string;
  endDate?: string;
  orderNo?: string;
  supplier?: string;
  keyword?: string;
  onlyUnfinished?: boolean;
  timeField?: Alibaba1688TimeField;
};

export const alibaba1688BrowserApi = {
  status: () => jsonFetch<Alibaba1688BrowserStatus>("/api/v1/alibaba1688-browser/status"),
  login: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string; message: string }>(
      "/api/v1/alibaba1688-browser/login",
      { method: "POST" }
    ),
  sync: (options: Alibaba1688SyncOptions = { mode: "incremental" }) =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>(
      "/api/v1/alibaba1688-browser/sync",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(options) }
    ),
  jobs: (limit = 10) =>
    jsonFetch<Alibaba1688BrowserJob[]>(`/api/v1/alibaba1688-browser/jobs?limit=${limit}`),
};

// ---------- 吉客云 Web 连接器（V1 主通道：网页登录态直读） ----------

export type JkyWebStatus = {
  adapter: string;
  status: string;
  sessionUpdatedAt: string | null;
  lastSyncAt: string | null;
  lastSyncStatus: string | null;
  lastSyncStats: Record<string, unknown>;
  errorSummary: string;
  counts: {
    salesOrders: number;
    salesItems: number;
    stockinOrders: number;
    stockinItems: number;
    totalStockSkus: number;
    warehouseStockRows: number;
  };
};

export type JkyWebJob = {
  id: number;
  jobType: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  stats: Record<string, unknown>;
  errorSummary: string;
};

export const jkyWebApi = {
  status: () => jsonFetch<JkyWebStatus>("/api/v1/jky-web/status"),
  updateSession: (curl: string) =>
    jsonFetch<{ ok: boolean; endpoints: string[]; hasCommonVerify: boolean }>(
      "/api/v1/jky-web/session",
      { method: "POST", body: JSON.stringify({ curl }) }
    ),
  sync: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string; message: string }>(
      "/api/v1/jky-web/sync",
      { method: "POST" }
    ),
  jobs: (limit = 10) => jsonFetch<JkyWebJob[]>(`/api/v1/jky-web/jobs?limit=${limit}`),
};

export type Alibaba1688OrderPreview = {
  id: number;
  rowStatus: string;
  externalOrderId: string;
  buyerCompanyName: string;
  buyerMemberName: string;
  sellerCompanyName: string;
  sellerMemberName: string;
  goodsTotal: string;
  freight: string;
  discount: string;
  actualPayment: string;
  orderStatus: string;
  orderRemark: string;
  orderTime: string | null;
  payTime: string | null;
};

// ---------- 税务系统官方发票清单 ----------

export type TaxInvoiceImportRow = {
  id: number;
  originalName: string;
  size: number;
  sha256: string;
  sourceSystem: string;
  period: string;
  sheetName: string;
  headers: string[];
  mapping: Record<string, string>;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  rowCount: number;
  recognizedRowCount: number;
  matchedRowCount: number;
  needsReviewCount: number;
  errorSummary: string;
  uploader: string;
  importedAt: string | null;
  createdAt: string | null;
};

export type TaxInvoiceProcessingStatus = "pending" | "required" | "not_required";

/** 进项发票 v2 分类（6 个 key + 空=待判断），与 processing_status 合并后的单一分类字段 */
export type TaxInvoiceCategoryV2 =
  | "goods"
  | "platform_fee"
  | "operating_other"
  | "reimburse_advance"
  | "reimburse_operating"
  | "excluded";

/** 销项发票分类（独立枚举，与进项 6+1 互斥）：空=待判断 */
export type TaxInvoiceCategoryOutput = "buyer_sales" | "platform_service";

export type TaxInvoiceRow = {
  id: number;
  direction: "input" | "output" | "unknown";
  invoiceCode: string;
  invoiceNumber: string;
  invoiceType: string;
  status: "issued" | "void" | "red" | "unknown";
  issueDate: string | null;
  sellerName: string;
  sellerTaxId: string;
  buyerName: string;
  buyerTaxId: string;
  amountExclTax: string | null;
  taxAmount: string | null;
  totalAmount: string | null;
  currency: string;
  /** 兼容字段；新页面使用 businessMatchStatus。 */
  matchStatus: "matched" | "partial" | "unmatched" | "needs_review";
  businessMatchStatus: "matched" | "partial" | "unmatched" | "needs_review";
  businessMatchedAmount: string;
  businessRemainingAmount: string;
  businessOvermatchedAmount: string;
  businessMatchException: string;
  matchNote: string;
  businessMatchNote: string;
  /** 进项发票独立的银行付款核对状态，与 businessMatchStatus 完全无关。 */
  bankPaymentStatus: "matched" | "partial" | "unmatched" | "not_applicable" | "overpaid_after_red" | "red_overpayment_settled";
  bankPaidAmount: string;
  bankRemainingAmount: string;
  bankOverpaidAmount: string;
  bankOverpaidSettledAmount?: string;
  bankOverpaidUnsettledAmount?: string;
  bankEffectiveInvoiceAmount: string;
  processingStatus: TaxInvoiceProcessingStatus;
  /** 分类 key：进项 goods/platform_fee/operating_other/reimburse_advance/reimburse_operating/excluded；销项 buyer_sales/platform_service；空=待判断 */
  category: string;
  /** 分类中文文案（后端权威，与 category 一致） */
  categoryLabel: string;
  sourceImportId: number | null;
  sourceRowIndex: number | null;
  /** 进项发票是否已勾选认证（税务侧抵扣） */
  verified: boolean;
  /** 认证所属月份，如 2026-08 */
  verifiedMonth: string;
  /** 进项发票最终付款方式：corporate=对公；personal=个人垫付；platform_auto_debit=平台自动扣款货款；mixed=对公+个人；空=未设置。销项不适用。 */
  paymentMethod: "corporate" | "personal" | "platform_auto_debit" | "mixed" | "";
  /** 人工补充字段：personal=个人垫付；platform_auto_debit=平台自动扣款货款；空=未设置。 */
  manualPaymentMethod: "personal" | "platform_auto_debit" | "";
  /** 凭证颜色与红冲生命周期分离：blue=蓝字原票，red=红字冲销票。 */
  invoiceColor: "blue" | "red" | "unknown" | string;
  invoiceStatusLabel: string;
  redStatus:
    | "none"
    | "partially_red_offset"
    | "fully_red_offset"
    | "over_red_offset"
    | "blue_red_pending"
    | "red_invoice"
    | "red_invoice_unpaired"
    | "red_invoice_ambiguous"
    | "void"
    | "unknown"
    | string;
  redPairStatus: "none" | "paired" | "paired_manual" | "unpaired" | "ambiguous" | "over_offset" | "counterpart_missing" | string;
  redPairMethod: "" | "official_ref" | "manual" | string;
  redRelatedInvoiceId: number | null;
  redRelatedInvoiceNo: string;
  redRelatedInvoiceIds: number[];
  redRelatedInvoiceNos: string[];
  redNoticeNo: string;
  redRelatedInvoiceDate?: string | null;
  redRelatedInvoicePeriod?: string;
  redCrossPeriod?: boolean;
  redOffsetAmount: string;
  remainingAfterRedAmount: string;
  accountingNetAmount: string;
  accountingNetIncluded: boolean;
  accountingException: string;
  redSettlementStatus?: "not_applicable" | "unsettled" | "partial" | "settled" | "over_settled" | string;
  redSettlementTargetAmount?: string;
  redSettledAmount?: string;
  redSettlementRemainingAmount?: string;
  redSettlementOverAmount?: string;
  redSettlements?: Array<{ linkId: number; type: string; amount: string; note: string; targetId: number | null; targetLabel: string; txnDate?: string | null; voucherNo?: string }>;
  vatDeductibleStatus?: "not_applicable" | "non_deductible" | "deductible" | "verified_pending_tax_filing" | "pending" | string;
  vatDeductibleAmount?: string;
  vatDeductibleManual?: boolean;
  inputVatTransferStatus?: "not_applicable" | "required_confirmation" | "completed" | "not_required_unverified_blue" | string;
  inputVatTransferAmount?: string;
  inputVatTransferManual?: boolean;
  /** 红字票分类由对应蓝字票继承。 */
  categoryInherited?: boolean;
  categoryInheritedFromInvoiceId?: number | null;
  /** 发票池展示用的采购/入库关联摘要 */
  links: Array<{
    /** tax_invoice_links 主键，解除关联时使用 */
    id: number;
    targetType: string;
    targetId: number;
    targetNo: string;
    targetLabel: string;
    allocatedAmount: number | null;
    matchMethod: string;
    confirmed: boolean;
    note: string;
  }>;
  purchaseOrderNos: string[];
  inboundNos: string[];
  /** 当前有效导入批次中解析出的开票明细摘要 */
  lineItems: Array<{
    goodsName: string | null;
    spec: string | null;
    unit: string | null;
    quantity: string | number | null;
    unitPrice: string | number | null;
    amount: string | number | null;
    taxRate: string | number | null;
    taxAmount: string | number | null;
    totalAmount: string | number | null;
    remark: string | null;
  }>;
  lineItemCount: number;
};

/** 发票人工关联候选（销项→销售订单；采购类→1688 文件订单 / 吉客云采购单） */
export type TaxInvoicePurchaseCandidate = {
  targetType: string;
  targetId: number;
  orderNo: string;
  supplier: string;
  /** 销售订单候选：买家/客户（采购候选为空） */
  buyer?: string;
  orderDate: string | null;
  amount: string | null;
  amountDiff: string | null;
  linkedInvoiceId: number | null;
  linkedInvoiceNo: string | null;
};

export type TaxInvoiceRedBlueCandidate = {
  invoiceId: number; invoiceNumber: string; invoiceCode: string; issueDate: string | null;
  sellerName: string; buyerName: string; totalAmount: string; taxPartyMatched: boolean; amountCanCoverRed: boolean;
};

export type TaxInvoiceRefundCandidate = {
  txnId: number; txnDate: string; amount: string; counterpartyName: string; voucherNo: string; summary: string;
  supplierMatched: boolean; amountMatched: boolean;
};
export type TaxInvoiceSummary = {
  total: number;
  byDirection: Record<string, number>;
  byStatus: Record<string, number>;
  byMatchStatus: Record<string, number>;
  byProcessing?: Record<string, number>;
  /** v2 分类计数（进项；key 为 6 个分类 + ""=待判断） */
  byCategory?: Record<string, number>;
  /** 派生组计数：operating=计入运营成本 / reimburse=计入报销成本 / excluded=不计入 / pending=待判断 */
  byCategoryGroup?: Record<string, number>;
  byVerification: Record<string, number>;
  inputVerification: Record<string, number>;
  inputTotalAmount: number;
  rawInputTotalAmount: number;
  /** 兼容字段：蓝字票已显示红冲但对应红字凭证尚未补齐时，被隔离的原票金额。 */
  excludedRedAmount: number;
  byRedStatus?: Record<string, number>;
  redPairExceptionCount?: number;
  redInvoiceCount?: number;
  partiallyRedOffsetBlueCount?: number;
  fullyRedOffsetBlueCount?: number;
  activeBatchCount: number;
  sourceRowCount: number;
  duplicateRowCount: number;
};

/** 发票货物明细行（官方清单导入记录中的一行；解析失败的数字保留原文） */
export type TaxInvoiceLine = {
  goodsName: string | null;
  spec: string | null;
  unit: string | null;
  quantity: string | number | null;
  unitPrice: string | number | null;
  amount: string | number | null;
  taxRate: string | number | null;
  taxAmount: string | number | null;
  totalAmount: string | number | null;
  remark: string | null;
};

export type TaxInvoiceLinesResponse = {
  items: TaxInvoiceLine[];
  total: number;
  sumAmount: number | null;
  sumTax: number | null;
  sumTotal: number | null;
};

export const taxInvoiceApi = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<TaxInvoiceImportRow[]>(`/api/v1/tax-invoices/imports${q}`);
  },
  summary: () => jsonFetch<TaxInvoiceSummary>("/api/v1/tax-invoices/summary"),
  invoices: (params: { direction?: string; status?: string; matchStatus?: string; processingStatus?: string; category?: string; verified?: boolean; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.direction) q.set("direction", params.direction);
    if (params.status) q.set("status", params.status);
    if (params.matchStatus) q.set("match_status", params.matchStatus);
    if (params.processingStatus) q.set("processing_status", params.processingStatus);
    if (params.category !== undefined) q.set("category", params.category);
    if (params.verified !== undefined) q.set("verified", String(params.verified));
    if (params.limit !== undefined) q.set("limit", String(params.limit));
    if (params.offset !== undefined) q.set("offset", String(params.offset));
    return jsonFetch<TaxInvoiceRow[]>(`/api/v1/tax-invoices${q.toString() ? `?${q}` : ""}`);
  },
  lines: (invoiceId: number) =>
    jsonFetch<TaxInvoiceLinesResponse>(`/api/v1/tax-invoices/${invoiceId}/lines`),
  setProcessingStatus: (invoiceId: number, processingStatus: "pending" | "required" | "not_required") =>
    jsonFetch<TaxInvoiceRow>(`/api/v1/tax-invoices/${invoiceId}/processing-status`, {
      method: "PATCH",
      body: JSON.stringify({ processing_status: processingStatus }),
    }),
  bulkVerify: (invoiceIds: number[], verified: boolean, verifiedMonth: string) =>
    jsonFetch<{ ok: boolean; processed: number; items: unknown[] }>("/api/v1/tax-invoices/bulk-verify", {
      method: "POST",
      body: JSON.stringify({ invoice_ids: invoiceIds, verified, verified_month: verifiedMonth }),
    }),
  bulkSetCategory: (invoiceIds: number[], category: string) =>
    jsonFetch<{ ok: boolean; processed: number }>("/api/v1/tax-invoices/bulk-category", {
      method: "POST",
      body: JSON.stringify({ invoice_ids: invoiceIds, category }),
    }),
  bulkSetPaymentMethod: (invoiceIds: number[], paymentMethod: string) =>
    jsonFetch<{ ok: boolean; processed: number }>("/api/v1/tax-invoices/bulk-payment-method", {
      method: "POST",
      body: JSON.stringify({ invoice_ids: invoiceIds, payment_method: paymentMethod }),
    }),
  redBlueCandidates: (invoiceId: number, keyword = "") => {
    const q = new URLSearchParams();
    if (keyword) q.set("keyword", keyword);
    return jsonFetch<TaxInvoiceRedBlueCandidate[]>(`/api/v1/tax-invoices/${invoiceId}/red-blue-candidates${q.toString() ? `?${q}` : ""}`);
  },
  setRedBlueLink: (redInvoiceId: number, blueInvoiceId: number, note = "") =>
    jsonFetch<Record<string, unknown>>(`/api/v1/tax-invoices/${redInvoiceId}/red-blue-link`, { method: "POST", body: JSON.stringify({ blue_invoice_id: blueInvoiceId, note }) }),
  clearRedBlueLink: (redInvoiceId: number) =>
    jsonFetch<Record<string, unknown>>(`/api/v1/tax-invoices/${redInvoiceId}/red-blue-link`, { method: "DELETE" }),
  refundCandidates: (invoiceId: number, keyword = "") => {
    const q = new URLSearchParams();
    if (keyword) q.set("keyword", keyword);
    return jsonFetch<TaxInvoiceRefundCandidate[]>(`/api/v1/tax-invoices/${invoiceId}/refund-candidates${q.toString() ? `?${q}` : ""}`);
  },
  addRedSettlement: (invoiceId: number, body: { settlementType: string; amount: string | number; targetId?: number | null; note?: string }) =>
    jsonFetch<Record<string, unknown>>(`/api/v1/tax-invoices/${invoiceId}/red-settlements`, {
      method: "POST",
      body: JSON.stringify({ settlement_type: body.settlementType, amount: body.amount, target_id: body.targetId ?? null, note: body.note ?? "" }),
    }),
  removeRedSettlement: (invoiceId: number, linkId: number) =>
    jsonFetch<Record<string, unknown>>(`/api/v1/tax-invoices/${invoiceId}/red-settlements/${linkId}`, { method: "DELETE" }),
  setVatReview: (
    invoiceId: number,
    body: {
      vatDeductibleStatus?: "pending" | "deductible" | "non_deductible";
      inputVatTransferStatus?: "required_confirmation" | "completed" | "not_required_unverified_blue" | "not_applicable";
      inputVatTransferAmount?: string | number | null;
      note?: string;
    },
  ) =>
    jsonFetch<Record<string, unknown>>(`/api/v1/tax-invoices/${invoiceId}/vat-review`, {
      method: "PATCH",
      body: JSON.stringify({
        vat_deductible_status: body.vatDeductibleStatus,
        input_vat_transfer_status: body.inputVatTransferStatus,
        input_vat_transfer_amount: body.inputVatTransferAmount ?? null,
        note: body.note ?? "",
      }),
    }),
  purchaseCandidates: (invoiceId: number, keyword = "") => {
    const q = new URLSearchParams();
    if (keyword) q.set("keyword", keyword);
    return jsonFetch<TaxInvoicePurchaseCandidate[]>(
      `/api/v1/tax-invoices/${invoiceId}/purchase-link-candidates${q.toString() ? `?${q}` : ""}`
    );
  },
  linkPurchase: (
    invoiceId: number,
    body: { targetType: string; targetId: number; allocatedAmount?: string | number | null; note?: string }
  ) =>
    jsonFetch<{ ok: boolean; id: number; orderNo: string }>(`/api/v1/tax-invoices/${invoiceId}/purchase-links`, {
      method: "POST",
      body: JSON.stringify({
        target_type: body.targetType,
        target_id: body.targetId,
        allocated_amount: body.allocatedAmount ?? undefined,
        note: body.note ?? "",
      }),
    }),
  unlinkPurchase: (invoiceId: number, linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/tax-invoices/${invoiceId}/purchase-links/${linkId}`, { method: "DELETE" }),
  upload: async (file: File, period?: string, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = new URLSearchParams();
    if (period) {
      const [year, month] = period.split("-");
      if (year) query.set("period_year", year);
      if (month) query.set("period_month", String(Number(month)));
    }
    query.set("auto_confirm", autoConfirm ? "true" : "false");
    const res = await authenticatedFetch(`/api/v1/tax-invoices/imports${query.toString() ? `?${query}` : ""}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{ duplicate: boolean; lifecycle: string; import: TaxInvoiceImportRow }>;
  },
  confirm: (id: number) =>
    jsonFetch<TaxInvoiceImportRow>(`/api/v1/tax-invoices/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/tax-invoices/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<TaxInvoiceImportRow>(`/api/v1/tax-invoices/imports/${id}/restore`, { method: "POST" }),
  records: (id: number) =>
    jsonFetch<TaxInvoiceRecordPreview[]>(`/api/v1/tax-invoices/imports/${id}/records`),
  deleteRow: (id: number, rowIndex: number) =>
    noContentFetch(`/api/v1/tax-invoices/imports/${id}/records/${rowIndex}`),
  restoreRow: (id: number, rowIndex: number) =>
    jsonFetch<{ rowIndex: number; rowStatus: string }>(
      `/api/v1/tax-invoices/imports/${id}/records/${rowIndex}/restore`,
      { method: "POST" }
    ),
};

export type TaxInvoiceRecordPreview = {
  rowIndex: number;
  rowStatus: string;
  recognitionStatus: string;
  invoiceId: number | null;
  errorSummary: string;
  payload: Record<string, string>;
};

// ---------- 采购全链路（1688订单 → SKU → 吉客云采购单 → 入库 → 发票 → 付款 → 认证） ----------

/** 环节数据归属维度：1688 导出 / 本平台采购中心 / 吉客云 / 税务 */
export type ChainDimension = "1688" | "purchase" | "jackyun" | "tax";

/** 漏斗统计里的单个环节 */
export type ChainStageStat = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: ChainDimension;
  count: number;
  pct: number;
};

/** 单行订单里单个环节的完成状态 */
export type ChainStageState = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: ChainDimension;
  done: boolean;
  detail: string;
  amount: number | null;
};

export type ChainOverview = {
  total: number;
  stageTotal: number;
  stages: ChainStageStat[];
  refined: number;
  jackyunLinked: number;
  inbound: number;
  invoiced: number;
  paid: number;
  verified: number;
  pending: number;
};

export type ChainInbound = {
  linkId: number | null;
  targetId: number;
  goodsdocNo: string;
  source?: string;
  isLocal?: boolean;
  platformPurchaseOrderNo?: string;
  externalReferenceNo?: string;
  date: string | null;
  warehouseName: string;
  supplier: string;
  amount: number | null;
  matchMethod: string;
  confidence: number | null;
  note: string;
  consumableUsageDecided: boolean;
  consumableUsageEnabled: boolean | null;
  consumableUsageItems: Array<{
    id: number;
    consumableId: number;
    consumableCode: string;
    consumableName: string;
    unit: string;
    quantity: string;
    note: string;
  }>;
};

export type ChainSettlement = {
  linkId: number;
  targetId: number;
  settlementNo: string;
  date: string | null;
  amount: number | null;
  paidAmount: number | null;
  paid: boolean;
  status: string;
  matchMethod: string;
  confidence: number | null;
  note: string;
};

export type ChainInvoice = {
  invoiceId: number;
  invoiceNo: string;
  amount: number | null;
  issueDate: string | null;
  verified: boolean;
  verifiedMonth: string;
  confirmed: boolean;
};

export type ChainAllocation = {
  id: number;
  skuId: number | null;
  skuCode: string;
  goodsName: string;
  quantity: number | null;
  unitPrice: number | null;
  amount: number | null;
  note: string;
};

export type ChainExpense = {
  id: number;
  expenseType: string;
  amount: number | null;
  note: string;
};

export type ChainPurchaseOrder = {
  id: number;
  purchNo: string;
  supplierName: string;
  amount: number | null;
  status: string;
  linkId?: number;
  relationKind?: string; // ''=普通 / merged=合并 / split=拆分
  allocAmount?: number | null; // 分摊金额
};

export type ChainOrderRow = {
  orderId: number;
  fileOrderId: number | null;
  externalPoId: number | null;
  source: "file" | "workflow";
  orderNo: string;
  platform?: string;
  supplier: string;
  buyer: string;
  amount: number | null;
  paidAmount: number | null;
  title: string;
  orderStatus: string;
  orderDate: string | null;
  purchaseStatus: string;
  purchaseContentComplete: boolean;
  unallocatedAmount: number | null;
  allocations: ChainAllocation[];
  expenses: ChainExpense[];
  purchaseOrders: ChainPurchaseOrder[];
  inbound: ChainInbound[];
  settlement: ChainSettlement[];
  invoice: ChainInvoice[];
  verified: boolean;
  pendingCount: number;
  /** 7 环节完成状态，顺序即链路顺序 */
  stages: ChainStageState[];
  doneCount: number;
  stageTotal: number;
};

export type PendingLink = {
  kind: "chain" | "settlement" | "invoice";
  linkId: number;
  orderId: number;
  orderNo: string;
  supplier: string;
  targetId: number;
  targetType: string;
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  confidence: number | null;
  note: string;
};

export type ChainWorkspace = {
  overview: ChainOverview;
  orders: { total: number; items: ChainOrderRow[] };
  pending: { items: PendingLink[] };
};

/** 单订单详情内的待确认关联建议。 */
export type ChainSuggestion = {
  kind: "chain" | "settlement" | "invoice";
  linkId: number;
  targetId: number;
  targetType: string;
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  confidence: number | null;
  note: string;
};

export type ChainLinkItemDetail = {
  goodsName: string;
  spec: string;
  quantity: number | null;
  applyQuantity: number | null;
  remainQuantity: number | null;
  barcode: string;
  unitName: string;
  unitPriceTax: number | null;
  amountTax: number | null;
  skuId: number | null;
  matchStatus: string;
};

export type ChainLinkCandidate = {
  targetId: number;
  targetType: "inbound" | "settlement";
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  warehouseName: string;
  score: number;
  reason: string;
  requiredSkuCount: number;
  matchedSkuCount: number;
  skuOverlapRatio: number | null;
  linkId: number | null;
  currentlyLinked: boolean;
  pendingSuggestion: boolean;
  previouslyRejected: boolean;
  linkedOrderNos: string[];
  details: ChainLinkItemDetail[];
};

/** 多因子预关联执行结果。 */
export type PreLinkItem = {
  orderNo: string;
  orderId: number;
  goodsdocNo: string;
  targetId: number;
  score: string;
  reason: string;
  linkId: number;
};
export type PreLinkResult = {
  autoLinked: PreLinkItem[];
  pendingSuggested: PreLinkItem[];
  stats: {
    autoLinked: number;
    pendingSuggested: number;
    fullyLinkedOrders: number;
    noAllocOrders: number;
  };
  autoConfirmed?: {
    confirmedChain?: number;
    resolvedInboundUsage?: number;
    confirmedInvoices?: number;
    skippedOrphans?: number;
    allocSeeded?: number;
    allocSkipped?: number;
    seedError?: string;
  };
};

/** 1688↔RK 对照表预览/应用结果。 */
export type XrefPreview = {
  totalXrefRows: number;
  totalPairs: number;
  stats: {
    to_create: number;
    missing_order: number;
    missing_rk: number;
    already_linked: number;
    combo_skipped: number;
    existing_noise: number;
  };
  toCreate: Array<{
    order_no: string;
    rk_no: string;
    barcodes: string[];
    products: string[];
    qty_boxes: number;
    note_extra: string;
  }>;
  missingOrder: string[];
  missingRk: string[];
  noiseLinkCount: number;
};
export type XrefApplyResult = {
  created: number;
  total_xref_rows: number;
  total_pairs: number;
  missing_order: string[];
  missing_rk: string[];
  combo_skipped_count: number;
  already_linked_count: number;
  noise_links: Array<{ id: number; target_id: number; order_id: number | null }>;
};
export type XrefFile = {
  exists: boolean;
  content: string;
  modifiedAt: number | null;
  size: number;
};

/** 二级页面：单订单链路详情。 */
export type ChainOrderDetail = ChainOrderRow & {
  suggestions: ChainSuggestion[];
  supplierHistory?: ChainSupplierHistory;
};

/** 采购执行中心顶部统计卡。 */
export type ExecutionOverview = {
  totalOrders: number;
  pendingProcess: number;
  pendingRefine: number;
  pendingSku: number;
  pendingPo: number;
  pendingInvoice: number;
};

/** 常购 SKU（供应商维度）。 */
export type OftenSku = {
  skuCode: string;
  goodsName: string;
  count: number;
};

/** 供应商历史（订单详情右侧展示）。 */
export type ChainSupplierHistory = {
  orderCount: number;
  totalPurchase: number;
  uninvoiced: number;
  lastOrderDate: string | null;
  oftenSkus: OftenSku[];
  recentOrders: ChainOrderRow[];
};

/** 供应商聚合列表项。 */
export type SupplierSummary = ChainSupplierHistory & {
  supplierName: string;
  uninbound: number;
};

/** 平铺全量记录：每行一条单据，标记数据维度与环节。 */
export type ChainRecord = {
  recordId: string;
  dimension: "1688" | "jackyun" | "tax";
  stage: "order" | "inbound" | "settlement" | "invoice";
  no: string;
  counterparty: string;
  amount: number | null;
  date: string | null;
  status: string;
  statusTone: "ok" | "warn" | "info" | "muted";
  linkedOrderNos: string[];
  pendingCount: number;
  invoiceId: number | null;
  verified: boolean;
  verifiedMonth: string;
};

export type ChainRecordList = {
  total: number;
  items: ChainRecord[];
};

export const procurementChainApi = {
  workspace: (limit = 500) =>
    jsonFetch<ChainWorkspace>(`/api/v1/procurement-chain/workspace?limit=${limit}`),
  overview: () => jsonFetch<ChainOverview>("/api/v1/procurement-chain/overview"),
  orders: (limit = 500) =>
    jsonFetch<{ total: number; items: ChainOrderRow[] }>(`/api/v1/procurement-chain/orders?limit=${limit}`),
  records: (limit = 2000) =>
    jsonFetch<ChainRecordList>(`/api/v1/procurement-chain/records?limit=${limit}`),
  pending: () => jsonFetch<{ items: PendingLink[] }>("/api/v1/procurement-chain/pending"),
  candidates: (orderId: number, targetType: "inbound" | "settlement", q = "", limit = 100) => {
    const sp = new URLSearchParams({ order_id: String(orderId), target_type: targetType, limit: String(limit) });
    if (q) sp.set("q", q);
    return jsonFetch<{ total: number; items: ChainLinkCandidate[] }>(`/api/v1/procurement-chain/candidates?${sp}`);
  },
  orderDetail: (orderId: number) =>
    jsonFetch<ChainOrderDetail>(`/api/v1/procurement-chain/orders/${orderId}`),
  executionOverview: () => jsonFetch<ExecutionOverview>("/api/v1/procurement-chain/execution-overview"),
  suppliers: (limit = 200) =>
    jsonFetch<{ total: number; items: SupplierSummary[] }>(`/api/v1/procurement-chain/suppliers?limit=${limit}`),
  supplierDetail: (supplierName: string) =>
    jsonFetch<SupplierSummary>(`/api/v1/procurement-chain/suppliers/${encodeURIComponent(supplierName)}`),
  runMatch: (autoConfirm = true) =>
    jsonFetch<{ created: number; skipped: number; requiresConfirmation: boolean; [key: string]: unknown }>(
      `/api/v1/procurement-chain/run-match${autoConfirm ? "" : "?auto_confirm=false"}`,
      { method: "POST" }
    ),
  autoConfirm: () =>
    jsonFetch<{
      confirmedChain: number;
      resolvedInboundUsage: number;
      confirmedInvoices: number;
      skippedOrphans: number;
      allocSeeded: number;
      allocSkipped: number;
      seedError: string;
    }>("/api/v1/procurement-chain/auto-confirm", { method: "POST" }),
  prelink: (auto = true) =>
    jsonFetch<PreLinkResult>("/api/v1/procurement-chain/prelink", {
      method: "POST",
      body: JSON.stringify({ auto }),
    }),
  xrefFile: () => jsonFetch<XrefFile>("/api/v1/procurement-chain/xref/file"),
  xrefPreview: (content: string) =>
    jsonFetch<XrefPreview>("/api/v1/procurement-chain/xref/preview", {
      method: "POST",
      body: JSON.stringify({ content, persist_to_file: false }),
    }),
  xrefApply: (content: string, persistToFile = true) =>
    jsonFetch<XrefApplyResult>("/api/v1/procurement-chain/xref/apply", {
      method: "POST",
      body: JSON.stringify({ content, persist_to_file: persistToFile }),
    }),
  confirmLink: (linkId: number) =>
    jsonFetch<{ id: number; confirmed: boolean }>(`/api/v1/procurement-chain/links/${linkId}/confirm`, { method: "POST" }),
  deleteLink: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-chain/links/${linkId}`, { method: "DELETE" }),
  confirmInvoiceLink: (linkId: number) =>
    jsonFetch<{ id: number; confirmed: boolean }>(`/api/v1/procurement-chain/invoice-links/${linkId}/confirm`, { method: "POST" }),
  deleteInvoiceLink: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-chain/invoice-links/${linkId}`, { method: "DELETE" }),
  verifyInvoice: (invoiceId: number, verified: boolean, verifiedMonth: string) =>
    jsonFetch<{ invoiceId: number; verified: boolean; verifiedMonth: string }>(
      `/api/v1/procurement-chain/invoices/${invoiceId}/verify`,
      { method: "POST", body: JSON.stringify({ verified, verified_month: verifiedMonth }) }
    ),
  manualLink: (orderId: number, targetType: string, targetId: number, note: string, consumableUsageEnabled?: boolean, consumableUsageItems?: Array<{ consumable_id: number; quantity: string }>) =>
    jsonFetch<{ id: number; confirmed: boolean }>("/api/v1/procurement-chain/links", {
      method: "POST",
      body: JSON.stringify({ order_id: orderId, target_type: targetType, target_id: targetId, note, consumable_usage_enabled: consumableUsageEnabled, consumable_usage_items: consumableUsageItems ?? [] }),
    }),
  autoLinkSettlement: (orderId: number) =>
    jsonFetch<{ linked: boolean; settlementNo?: string; reason?: string }>(`/api/v1/procurement-chain/orders/${orderId}/auto-link-settlement`, { method: "POST" }),
  manualInvoiceLink: (orderId: number, invoiceId: number, note: string) =>
    jsonFetch<{ id: number; confirmed: boolean }>("/api/v1/procurement-chain/invoice-links", {
      method: "POST",
      body: JSON.stringify({ order_id: orderId, invoice_id: invoiceId, note }),
    }),
  replaceLink: (linkId: number, targetId: number, note: string, consumableUsageEnabled?: boolean, consumableUsageItems?: Array<{ consumable_id: number; quantity: string }>) =>
    jsonFetch<{ id: number; confirmed: boolean; targetId: number }>(`/api/v1/procurement-chain/links/${linkId}`, {
      method: "PUT",
      body: JSON.stringify({ target_id: targetId, note, consumable_usage_enabled: consumableUsageEnabled, consumable_usage_items: consumableUsageItems ?? [] }),
    }),
  setInboundConsumableUsage: (linkId: number, enabled: boolean, items: Array<{ consumable_id: number; quantity: string }>, note = "") =>
    jsonFetch<{ linkId: number; decided: boolean; enabled: boolean }>(`/api/v1/procurement-chain/links/${linkId}/consumable-usage`, {
      method: "POST",
      body: JSON.stringify({ enabled, items, note }),
    }),
  correctInboundAmount: (documentId: number, amount: string, note = "") =>
    jsonFetch<{ ok: boolean; documentId: number; amount: string }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/amount`, {
      method: "PATCH",
      body: JSON.stringify({ amount, note }),
    }),
  recalcInboundAmount: (documentId: number) =>
    jsonFetch<{ ok: boolean; before: string | null; after: string }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/recalc-amount`, {
      method: "POST",
    }),
  correctInboundDate: (documentId: number, inboundAt: string, note = "") =>
    jsonFetch<{ ok: boolean; documentId: number; before: string | null; after: string }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/date`, {
      method: "PATCH",
      body: JSON.stringify({ inbound_at: inboundAt, note }),
    }),
  correctInboundItemPrice: (documentId: number, itemId: number, unitPriceTax: string, note = "") =>
    jsonFetch<{
      ok: boolean;
      documentId: number;
      itemId: number;
      lineNo: number;
      before: string | null;
      after: string;
      amountTaxAfter: string | null;
      documentAmountAfter: string;
    }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/items/${itemId}/price`, {
      method: "PATCH",
      body: JSON.stringify({ unit_price_tax: unitPriceTax, note }),
    }),
  createLocalInbound: (body: {
    order_id: number;
    inbound_no?: string;
    inbound_at?: string | null;
    warehouse_id?: number | null;
    note?: string;
    items: Array<{ allocation_id: number; quantity: string; unit_price?: string | null }>;
    consumable_usage_enabled?: boolean | null;
    consumable_usage_items?: Array<{ consumable_id: number; quantity: string }>;
  }) => jsonFetch<{
    ok: boolean;
    documentId: number;
    inboundNo: string;
    linkId: number;
    source: string;
    usage: { status: string; items: unknown[] };
  }>("/api/v1/procurement-chain/local-inbounds", { method: "POST", body: JSON.stringify(body) }),
  deleteLocalInbound: (documentId: number) =>
    jsonFetch<{ ok: boolean; documentId: number; inboundNo: string }>(
      `/api/v1/procurement-chain/inbound-documents/${documentId}`,
      { method: "DELETE" },
    ),
};

// ---------- 采购执行中心（5 步骤任务视角） ----------

export type WorkbenchDimension = "1688" | "purchase" | "jackyun" | "tax";

export type WorkbenchStep = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: WorkbenchDimension;
  href: string;
  act: string;
  count?: number;
  pct?: number;
};

export type WorkbenchStepState = {
  done: boolean;
  detail: string;
  amount: number | null;
};

export type WorkbenchFunnel = {
  total: number;
  stepTotal: number;
  steps: (WorkbenchStep & { count: number; pct: number })[];
};

export type WorkbenchTodo = {
  total: number;
  pending: number;
  items: (WorkbenchStep & { count: number })[];
};

export type WorkbenchSummary = {
  totalOrders: number;
  newOrders: number;
  pendingSku: number;
  pendingPo: number;
  pendingInbound: number;
  pendingInvoice: number;
  exceptionCount: number;
  goodsOrders: number;
  consumableOrders: number;
  goodsProducing: number;
  goodsInbound: number;
  consumableTransit: number;
  consumableInbound: number;
  transitOrders: number;
  completedOrders: number;
  paidRate: number;
  paidAmount: number;
  totalAmount: number;
  funnel: WorkbenchFunnel;
  todos: WorkbenchTodo;
};

/** 采购工作台异常摘要：由异常中心记录压缩而来，message 为可直接展示的原因。 */
export type WorkbenchExceptionInfo = {
  type: string;
  title: string;
  status: string;
  severity: string;
  reason: string;
  actionRequired: string;
  paidAmount: number | null;
  inboundAmount: number | null;
  difference: number | null;
  suggestedAdjustment: number | null;
  message: string;
  createdAt: string | null;
};

export type WorkbenchOrderItem = {
  orderId: number;
  externalPoId: number | null;
  orderNo: string;
  /** 采购渠道：1688 / pdd（拼多多）/ taobao（淘宝）。后端暂未下发时兜底为 1688。 */
  platform?: string;
  /** 订单类型：goods=正常货品 / consumable=耗材（包材）采购 */
  orderKind?: "goods" | "consumable";
  /** 人工覆盖的类型（goods/consumable/空=自动判定） */
  orderKindOverride?: string;
  supplier: string;
  supplierPartnerId?: number | null;
  amount: number | null;
  paidAmount?: number | null;
  freight: number | null;
  orderDate: string | null;
  orderStatus: string;
  purchaseStatus: string;
  hasException: boolean;
  /** 命中的异常摘要（报错原因，供界面直接展示）。 */
  exceptionInfo?: WorkbenchExceptionInfo[];
  firstUndone: string | null;
  firstUndoneLabel: string;
  firstUndoneShort: string;
  firstUndoneDimension: WorkbenchDimension;
  stepStates: Record<string, WorkbenchStepState>;
  inboundDone: boolean;
  invoiceDone: boolean;
  /** 收尾环节卡点：awaiting_inbound / awaiting_invoice / awaiting_payment / awaiting_verification / done / open */
  closeoutStage?: string | null;
  /** 供应商待开发票：pending=完全未开票 / partial=部分 / done=已开够 / none=无实付可比 */
  invoiceStatus?: "pending" | "partial" | "done" | "none" | string;
  /** 已收票金额（正式税票优先；红冲按有效蓝字净额；旧手工票仅兼容兜底） */
  invoicedAmount?: number | null;
  /** 未开票金额 = 应开票目标 - 有效已收票（负数归 0） */
  invoiceOutstanding?: number | null;
  /** 发票事实是否存在冲突/歧义，需要财务或采购人工复核。 */
  invoiceNeedsReview?: boolean;
  /** 需复核原因；例如一票多单红冲后无法自动判断重新分摊。 */
  invoiceReviewReasons?: string[];
  /** 供应链工作台表格：由已确认 SKU / 原始商品行汇总得到。 */
  productName?: string;
  productQuantity?: number | null;
  productUnit?: string;
  itemCount?: number;
  warehouseName?: string;
  logisticsNo?: string;
  logisticsCompany?: string;
  expectedArrival?: string;
  shipAt?: string;
  jackyunInboundNo?: string;
  remark?: string;
};

export type WorkbenchOrderGroup = { label: string; items: WorkbenchOrderItem[] };

export type WorkbenchOrderList = {
  total: number;
  page: number;
  pageSize: number;
  /** 当前筛选结果合计未开票金额（催票总额） */
  invoiceOutstandingTotal?: number;
  groups: WorkbenchOrderGroup[];
};

export type WorkbenchOrder = {
  orderId: number;
  fileOrderId: number | null;
  externalPoId: number | null;
  orderNo: string;
  /** 采购渠道：1688 / pdd（拼多多）/ taobao（淘宝）。后端暂未下发时兜底为 1688。 */
  platform?: string;
  /** 订单类型：goods=正常货品 / consumable=耗材（包材）采购 */
  orderKind?: "goods" | "consumable";
  /** 人工覆盖的类型（goods/consumable/空=自动判定） */
  orderKindOverride?: string;
  supplier: string | null;
  supplierPartnerId?: number | null;
  buyer: string | null;
  amount: number | null;
  goodsTotal: number | null;
  freight: number | null;
  discount: number | null;
  paidAmount: number | null;
  /** 1688 源单实付大于 0，表示平台付款事实已确认；付款时间可能因导入字段缺失而为空。 */
  paidOn1688?: boolean;
  paidOn1688At?: string | null;
  /** 1688 微调金额：红包等导致开票金额与订单实付的零头差；平衡目标 = 实付 + 微调 */
  adjustmentAmount?: number | null;
  adjustmentNote?: string;
  orderDate: string | null;
  orderStatus: string | null;
  purchaseStatus: string;
  /** 后端根据入库/发票/付款事实计算出的收尾卡点。 */
  closeoutStage?: string | null;
  /** 发票池自动匹配结果：采购订单详情只读展示，不在订单弹窗内人工维护。 */
  invoiceStatus?: "pending" | "partial" | "done" | "needs_review" | "none" | string;
  invoicedAmount?: number | null;
  invoiceOutstanding?: number | null;
  invoiceNeedsReview?: boolean;
  invoiceReviewReasons?: string[];
  /** 采购单步骤按 Excel 口径跳过（吉客云未建采购单、入库闭环即放行） */
  jackyunPoBypassed?: boolean;
  title: string | null;
  hasException: boolean;
  /** 命中的异常摘要（报错原因，供界面直接展示）。 */
  exceptionInfo?: WorkbenchExceptionInfo[];
  logistics?: Record<string, unknown>;
  shipStatus?: string;
  /** 采购订单计划入库仓库；实际入库后以入库单仓库为准。 */
  warehouseId?: number | null;
  warehouseName?: string;
};

export type WorkbenchDetail = {
  order: WorkbenchOrder;
  stepStates: Record<string, WorkbenchStepState>;
  detail: {
    allocations: unknown[];
    orderItems?: unknown[];
    consumable?: unknown | null;
    expenses: unknown[];
    purchaseOrders: unknown[];
    poAmountClosure?: { relevant: boolean; allocTotal: number | null; gap: number | null; closed: boolean } | null;
    inbound: unknown[];
    invoice: unknown[];
    settlement: unknown[];
    unallocatedAmount: number | null;
  };
  stepTotal: number;
  warehouse?: {
    warehouseName: string;
    warehouseId?: number | null;
    targetWarehouseId?: number | null;
    targetWarehouseName?: string;
    jackyunWarehouseId: string;
    isSellable: boolean | null;
    currentStock: number | null;
    inTransitQty: number;
  } | null;
  supplierHistory: {
    orderCount: number;
    totalPurchase: number;
    uninvoiced: number;
    lastOrderDate: string | null;
    oftenSkus: { skuCode: string; goodsName: string; count: number }[];
    recentOrders: WorkbenchOrder[];
  } | null;
};

export type WorkbenchSupplierSummary = {
  partnerId: number | null;
  supplierName: string;
  orderCount: number;
  totalPurchase: number;
  uninvoiced: number;
  uninbound: number;
  lastOrderDate: string | null;
  oftenSkus: { skuCode: string; goodsName: string; count: number }[];
};

export type WorkbenchSupplierDetail = WorkbenchSupplierSummary & {
  recentOrders: WorkbenchOrder[];
};

export type InvoiceReconciliation = {
  tolerance: number;
  matchingRule: "invoice_issue_date_cutoff_then_order_date_fifo" | string;
  skippedZeroOrders: number;
  suppliers: Array<{
    partnerId?: number | null; identityKey?: string;
    supplier: string; supplierNorm: string; hasOrders: boolean;
    orderCount: number; orderTotal: number; invoiceCount: number; invoiceTotal: number;
    matchedTotal: number; remainingOrders: number; remainingOrderTotal: number;
    pendingOrders: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
      orderAmount: number; remaining: number; partial: boolean }>;
    orders: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
      orderAmount: number; remaining: number }>;
    months: Array<{ month: string; invoices: Array<{
      invoiceId: number; invoiceNo: string; issueDate: string | null; seller: string;
      amount: number; originalAmount?: number; redOffsetAmount?: number; redStatus?: string; invoiceStatusLabel?: string;
      coveredTotal: number; diff: number; status: "matched" | "short";
      shortReason?: "date_cutoff" | "insufficient_orders" | "explicit_link_issue" | null;
      futureOrderCount?: number;
      manualLinked: boolean;
      explicitLinked?: boolean;
      explicitIssues?: string[];
      covered: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
        orderAmount: number; allocatedAmount?: number; consumed: number; partial: boolean;
        allocationIssue?: boolean;
        source: "manual" | "source_ref" | "auto"; linkId?: number }>;
    }> }>;
  }>;
  expenseSellers: Array<{ partnerId?: number | null; seller: string; invoiceCount: number; invoiceTotal: number }>;
};

export const procurementWorkbenchApi = {
  funnel: () => jsonFetch<WorkbenchFunnel>("/api/v1/procurement-workbench/funnel"),
  todos: () => jsonFetch<WorkbenchTodo>("/api/v1/procurement-workbench/todos"),
  summary: () => jsonFetch<WorkbenchSummary>("/api/v1/procurement-workbench/summary"),
  orders: (
    params: {
      status?: "all" | "pending" | "done" | "order" | "content" | "sku" | "jackyun_po" | "closeout" | "refine" | "po" | "inbound" | "transit" | "invoice" | "exception";
      q?: string;
      sortBy?: "date" | "amount" | "status";
      page?: number;
      pageSize?: number;
      startDate?: string;
      endDate?: string;
      channel?: "all" | "1688" | "pdd" | "taobao" | "other";
      kind?: "all" | "goods" | "consumable";
      warehouse?: string;
    } = {}
  ) => {
    const sp = new URLSearchParams();
    if (params.status && params.status !== "all") sp.set("status", params.status);
    if (params.q) sp.set("q", params.q);
    if (params.sortBy && params.sortBy !== "date") sp.set("sort_by", params.sortBy);
    if (params.page) sp.set("page", String(params.page));
    if (params.pageSize) sp.set("page_size", String(params.pageSize));
    if (params.startDate) sp.set("start_date", params.startDate);
    if (params.endDate) sp.set("end_date", params.endDate);
    if (params.channel && params.channel !== "all") sp.set("channel", params.channel);
    if (params.kind && params.kind !== "all") sp.set("kind", params.kind);
    if (params.warehouse) sp.set("warehouse", params.warehouse);
    const qs = sp.toString();
    return jsonFetch<WorkbenchOrderList>(`/api/v1/procurement-workbench/orders${qs ? "?" + qs : ""}`);
  },
  workbench: (orderId: number) =>
    jsonFetch<WorkbenchDetail>(`/api/v1/procurement-workbench/orders/${orderId}/workbench`),
  softDeleteOrder: (orderId: number) =>
    jsonFetch<{
      ok: boolean;
      orderId: number;
      orderNo: string;
      removedPoIds?: number[];
      removedInboundIds?: number[];
      recoverable?: boolean;
    }>(`/api/v1/procurement-workbench/orders/${orderId}/soft-delete`, { method: "POST" }),
  /** 编辑订单主档（供应商/标题/金额等）：1688 单改源单事实并同步副本，独有单改副本；后端留审计 */
  editOrderMain: (
    orderId: number,
    body: Partial<{
      supplier_name: string;
      title: string;
      external_order_id: string;
      ordered_at: string;
      goods_total: string;
      freight: string;
      discount: string;
      actual_payment: string;
      order_amount: string;
      paid_amount: string;
      platform: string;
      warehouse_id: number | null;
    }>
  ) =>
    jsonFetch<{ ok: boolean; mode: "file" | "external"; orderNo: string }>(
      `/api/v1/procurement-workbench/orders/${orderId}/main-fields`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  /** 人工覆盖订单类型（正品/耗材）：goods / consumable / 空串=恢复自动判定 */
  setOrderKindOverride: (orderId: number, kind: "goods" | "consumable" | "") =>
    jsonFetch<{ orderId: number; orderKindOverride: string }>(
      `/api/v1/procurement-workbench/orders/${orderId}/kind-override`,
      { method: "PATCH", body: JSON.stringify({ kind }) }
    ),
  /** 发票维度对账：V2 以 canonical partnerId 为主，supplier 仅作历史兼容。 */
  invoiceReconciliation: (params?: { partnerId?: number; supplier?: string }) => {
    const q = new URLSearchParams();
    if (params?.partnerId != null) q.set("partner_id", String(params.partnerId));
    if (params?.supplier) q.set("supplier", params.supplier);
    return jsonFetch<import("./api").InvoiceReconciliation>(
      `/api/v1/procurement-workbench/invoice-reconciliation${q.toString() ? `?${q}` : ""}`
    );
  },
  /** 供应商画像手工微调：把采购订单挂到进项发票（manual 关联，落库） */
  createInvoiceMatch: (invoiceId: number, poId: number) =>
    jsonFetch<{ ok: boolean; id: number }>("/api/v1/procurement-workbench/invoice-match", {
      method: "POST",
      body: JSON.stringify({ invoice_id: invoiceId, po_id: poId }),
    }),
  /** 解除供应商画像里的手工发票匹配 */
  deleteInvoiceMatch: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-workbench/invoice-match/${linkId}`, { method: "DELETE" }),
  suppliers: (limit = 200) =>
    jsonFetch<{ total: number; items: WorkbenchSupplierSummary[] }>(
      `/api/v1/procurement-workbench/suppliers?limit=${limit}`
    ),
  supplierDetail: (supplierName: string) =>
    jsonFetch<WorkbenchSupplierDetail>(
      `/api/v1/procurement-workbench/suppliers/${encodeURIComponent(supplierName)}`
    ),
  supplierDetailByPartner: (partnerId: number) =>
    jsonFetch<WorkbenchSupplierDetail>(
      `/api/v1/procurement-workbench/suppliers/by-partner/${partnerId}`
    ),
  /** 供应商改名/归一：该供应商全部订单统一改为新名称，同名自动合并（双副本同步+审计） */
  renameSupplier: (oldName: string, newName: string) =>
    jsonFetch<{ ok: boolean; oldName: string; newName: string; renamedOrders: number; renamedFileOrders: number; merged: boolean }>(
      "/api/v1/procurement-workbench/suppliers/rename",
      { method: "POST", body: JSON.stringify({ old_name: oldName, new_name: newName }) }
    ),
};

// ---------- 采购工作台（截图版）----------

export type BoardOverview = {
  todayNewOrders: number;
  todayDelta: number;
  pendingRefine: number;
  pendingPo: number;
  pendingInbound: number;
  pendingInvoice: number;
  paidRate: number;
  paidAmount: number;
  totalAmount: number;
};

export type BoardStepProgress = { label: string; done: boolean };

export type BoardOrderItem = {
  orderId: number;
  orderNo: string;
  supplier: string;
  amount: number | null;
  orderDate: string | null;
  orderStatus: string;
  firstUndone: string | null;
  firstUndoneLabel: string;
  stepProgress: BoardStepProgress[];
};

export type BoardOrderGroup = { label: string; items: BoardOrderItem[] };
export type BoardOrderList = {
  total: number;
  page: number;
  pageSize: number;
  groups: BoardOrderGroup[];
};

export type BoardFlowStep = { no: number; label: string; done: boolean; detail: string };

export type BoardSupplierProfile = {
  supplierName: string;
  badge: string;
  orderCount30d: number;
  totalPurchase: number;
  lastOrderDate: string | null;
  lastRecentLabel: string;
};

export type BoardOftenSku = {
  skuCode: string;
  goodsName: string;
  purchaseCount: number;
  qty: number;
  unitPrice: number;
  amount: number;
};

export type BoardPaymentBreakdown = {
  goodsAmount: number;
  freight: number;
  extra: number;
  discount: number;
  totalDue: number;
  paidAmount: number;
  unpaidAmount: number;
  diffAmount: number;
};

export type BoardOrder = {
  orderId: number;
  orderNo: string;
  supplier: string | null;
  buyer: string | null;
  amount: number | null;
  orderDate: string | null;
  orderStatus: string | null;
  title: string | null;
  freight: number | null;
  discount: number | null;
  goodsTotal: number | null;
};

export type BoardOrderDetail = {
  order: BoardOrder;
  flowStatus: BoardFlowStep[];
  supplierProfile: BoardSupplierProfile | null;
  oftenSkus: BoardOftenSku[];
  allocations: ChainAllocation[];
  purchaseOrders: ChainPurchaseOrder[];
  inbound: ChainInbound[];
  invoice: ChainInvoice[];
  verified: boolean;
  paymentBreakdown: BoardPaymentBreakdown;
};

export const procurementBoardApi = {
  overview: () => jsonFetch<BoardOverview>("/api/v1/procurement-board/overview"),
  orders: (
    params: {
      status?: "all" | "pending" | "refine" | "po" | "inbound" | "invoice" | "done";
      q?: string;
      page?: number;
      pageSize?: number;
    } = {}
  ) => {
    const sp = new URLSearchParams();
    if (params.status && params.status !== "all") sp.set("status", params.status);
    if (params.q) sp.set("q", params.q);
    if (params.page) sp.set("page", String(params.page));
    if (params.pageSize) sp.set("page_size", String(params.pageSize));
    const qs = sp.toString();
    return jsonFetch<BoardOrderList>(`/api/v1/procurement-board/orders${qs ? "?" + qs : ""}`);
  },
  orderDetail: (orderId: number) =>
    jsonFetch<BoardOrderDetail>(`/api/v1/procurement-board/orders/${orderId}/detail`),
};

// ---------- SKU 匹配工作台 ----------

export type InboundMatchAnomaly = {
  itemId: number;
  documentId: number;
  goodsdocNo: string;
  goodsNo: string;
  goodsName: string;
  quantity: number | null;
  amountTax: number | null;
  matchedSkuId: number | null;
  status: string;
  note: string;
};

export type InboundMatchSummary = {
  total: number;
  counts: Record<string, number>;
  anomalies: InboundMatchAnomaly[];
  manualMatches: InboundMatchAnomaly[];
};

export type SkuCandidate = {
  skuId: number;
  skuCode: string;
  skuName: string;
  barcode: string;
  unit: string;
  defaultCost: number | null;
  reason: string;
};

export type PendingAllocation = {
  poId: number;
  fileOrderId: number | null;
  externalOrderId: string;
  supplierName: string;
  orderedAt: string | null;
  paidAmount: number | null;
  allocationCount: number;
  purchaseStatus: string;
  editable: boolean;
  balance: {
    allocated: number;
    paid: number;
    diff: number;
    balanced: boolean;
    abnormalNote: string;
  };
};

export type AllocationBodyInput = {
  sku_id: number;
  quantity: string;
  amount: string;
  unit_price?: string | null;
};

export const skuMatchingApi = {
  inboundSummary: () => jsonFetch<InboundMatchSummary>("/api/v1/purchase/sku-matching/inbound"),
  runInboundAuto: () =>
    jsonFetch<{ ok: boolean; stats: Record<string, number> }>(
      "/api/v1/purchase/sku-matching/inbound-auto",
      { method: "POST" }
    ),
  runOutboundAuto: () =>
    jsonFetch<{ ok: boolean; stats: Record<string, number> }>(
      "/api/v1/purchase/sku-matching/outbound-auto",
      { method: "POST" }
    ),
  pending: (limit = 50) =>
    jsonFetch<PendingAllocation[]>(`/api/v1/purchase/sku-matching/pending?limit=${limit}`),
  candidates: (poId: number) =>
    jsonFetch<{ poId: number; candidates: SkuCandidate[] }>(
      `/api/v1/purchase/sku-matching/${poId}/candidates`
    ),
  manualInbound: (itemId: number, skuId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/purchase/sku-matching/inbound/${itemId}/manual`, {
      method: "POST",
      body: JSON.stringify({ sku_id: skuId }),
    }),
  clearManualInbound: (itemId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/purchase/sku-matching/inbound/${itemId}/manual`, {
      method: "DELETE",
    }),
  acceptCost: (itemId: number) =>
    jsonFetch<{ ok: boolean; oldCost: string | null; newCost: string }>(
      `/api/v1/purchase/sku-matching/inbound/${itemId}/accept-cost`,
      { method: "POST", body: JSON.stringify({ confirm: true }) }
    ),
  batchAcceptCost: (documentId: number) =>
    jsonFetch<{ ok: boolean; documentId: number; accepted: number; failed: { itemId: number; reason: string }[] }>(
      "/api/v1/purchase/sku-matching/inbound/batch-accept-cost",
      { method: "POST", body: JSON.stringify({ document_id: documentId, confirm: true }) }
    ),
  addAllocation: (poId: number, body: AllocationBodyInput) =>
    jsonFetch<{ id: number; amount: string; balance: Record<string, unknown> }>(
      `/api/v1/purchase/orders/${poId}/allocations`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  skus: () =>
    jsonFetch<CatalogSkuRow[]>("/api/v1/dashboard/products?limit=500"),
};

// ---------- 快递物流（物流成本管理） ----------

export type LogisticsMonthRow = {
  type: "month" | "bill";
  period: string;
  periodLabel: string;
  carrier: string;
  billId: number | null;
  shippedCount: number;
  unitPrice: string | null;
  unitPriceSource?: "smart" | "default";
  estimatedAmount: string;
  actualAmount: string | null;
  actualUnitPrice: string | null;
  difference: string | null;
  status: string;
  statusLabel: string;
  invoiceStatus: string;
};

export type LogisticsRegionalModel = {
  month: string;
  province: string;
  carrier: string;
  weightBand: string;
  sampleCount: number;
  avgFee: string;
  medianFee: string;
  confidence: "high" | "medium" | "low";
};

export type LogisticsSmartEstimate = {
  available: boolean;
  sampleCount: number;
  averageFee: string | null;
  suggestedUnitPrice: string | null;
  confidence: "high" | "medium" | "low" | "none";
  method: string;
};

export type LogisticsWorkbench = {
  cards: {
    monthShippedCount: number;
    monthEstimatedAmount: string;
    pendingEstimatedAmount: string;
    latestActualUnitPrice: string | null;
    annualLogisticsCost: string;
    estimateUnitPrice?: string;
    estimateUnitPriceSource?: "smart" | "default";
  };
  months: LogisticsMonthRow[];
  settings: { defaultUnitPrice: string };
  smartEstimate?: LogisticsSmartEstimate;
  regionalModels?: LogisticsRegionalModel[];
};

export type LogisticsBill = {
  id: number;
  periodLabel: string;
  periodStart: string | null;
  periodEnd: string | null;
  carrier: string;
  waybillCount: number | null;
  estimatedAmount: string | null;
  actualAmount: string | null;
  actualUnitPrice: string | null;
  difference: string | null;
  status: string;
  statusLabel: string;
  invoiceStatus: string;
  invoiceStatusLabel: string;
  note: string;
  attachmentName?: string;
  importSource?: string | null;
  importSummary?: {
    summaryWarehouse?: string;
    detailWarehouse?: string;
    shippingAmount?: string;
    pickupAmount?: string;
    valueAddedAmount?: string;
    grossAmount?: string;
    adjustmentAmount?: string;
    actualAmount?: string;
    directChargeAmount?: string;
    overheadFactor?: string;
  } | null;
  matchedCount: number | null;
  unmatchedCount: number | null;
  duplicateCount: number | null;
  abnormalCount: number | null;
  createdAt: string | null;
};

export type LogisticsBillImportPreview = {
  fileName: string;
  fileHash: string;
  duplicateBillId: number | null;
  periodLabel: string;
  periodStart: string | null;
  periodEnd: string | null;
  summaryWarehouse: string;
  detailWarehouse: string;
  carrier: string;
  carriers: Array<{ name: string; count: number }>;
  shipmentCount: number;
  pickupCount: number;
  waybillCount: number;
  shippingAmount: string;
  pickupAmount: string;
  valueAddedAmount: string;
  grossAmount: string;
  adjustmentAmount: string;
  actualAmount: string;
  directChargeAmount: string;
  overheadFactor: string;
  matchedCount: number;
  unmatchedCount: number;
  duplicateCount: number;
  abnormalCount: number;
  warnings: string[];
  regionalModels: LogisticsRegionalModel[];
  sampleRows: Array<Record<string, unknown>>;
};

export const logisticsApi = {
  workbench: () => jsonFetch<LogisticsWorkbench>("/api/v1/logistics/workbench"),
  settings: () => jsonFetch<{ defaultUnitPrice: string }>("/api/v1/logistics/settings"),
  updateSetting: (defaultUnitPrice: string) =>
    jsonFetch<{ defaultUnitPrice: string }>("/api/v1/logistics/settings", {
      method: "PUT",
      body: JSON.stringify({ default_unit_price: defaultUnitPrice }),
    }),
  bills: () => jsonFetch<{ items: LogisticsBill[] }>("/api/v1/logistics/bills"),
  bill: (id: number) => jsonFetch<LogisticsBill>(`/api/v1/logistics/bills/${id}`),
  importBillXlsx: async (file: File, confirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const res = await authenticatedFetch(`/api/v1/logistics/bills/import-xlsx?confirm=${confirm ? "true" : "false"}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `物流账单导入失败（${res.status}）`));
    }
    return res.json() as Promise<{ ok: boolean; preview: LogisticsBillImportPreview; bill?: LogisticsBill }>;
  },
  createBill: (body: Record<string, unknown>) =>
    jsonFetch<LogisticsBill>("/api/v1/logistics/bills", { method: "POST", body: JSON.stringify(body) }),
  settleBill: (id: number) =>
    jsonFetch<LogisticsBill>(`/api/v1/logistics/bills/${id}/settle`, { method: "POST" }),
  deleteBill: (id: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/logistics/bills/${id}`, { method: "DELETE" }),
};
