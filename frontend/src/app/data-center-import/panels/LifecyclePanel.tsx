"use client";

import { useState, useRef, useEffect, ReactNode } from "react";

export type RowActionApi<Row, PreviewRow> = {
  imports: (lifecycle?: string) => Promise<Row[]>;
  upload: (
    file: File,
    autoConfirm: boolean
  ) => Promise<{ duplicate: boolean; lifecycle: string; import: Row; message?: string }>;
  confirm: (id: number) => Promise<Row>;
  softDelete: (id: number) => Promise<void>;
  restore: (id: number) => Promise<Row>;
  records: (id: number) => Promise<PreviewRow[]>;
  /** 明细核对：行级软删除（可恢复）。1688 传 orderId，吉客云/税务传 rowIndex。 */
  deleteRow: (id: number, rowKey: number) => Promise<unknown>;
  /** 明细核对：恢复一行已删除明细。 */
  restoreRow: (id: number, rowKey: number) => Promise<unknown>;
};

/** 传给 renderPreview 的行级操作入口。 */
export type PreviewRowActions = {
  onDeleteRow: (rowKey: number) => void;
  onRestoreRow: (rowKey: number) => void;
  busyRowKey: number | null;
  /** 导入整体在回收站时为 false，行级操作随之禁用。 */
  editable: boolean;
};

export type LifecyclePanelConfig<Row extends { id: number }, PreviewRow> = {
  title: string;
  description: string;
  accept: string;
  fileHint: string;
  api: RowActionApi<Row, PreviewRow>;
  /** 摘要列（文件名/大小/数量/状态/上传者/时间等），由各平台自行决定。 */
  renderSummary: (row: Row) => ReactNode;
  /** 展开后的明细预览：按导入表格原样直出，actions 提供行级删除/恢复；loading/error 由组件统一处理。 */
  renderPreview: (
    row: Row,
    rows: PreviewRow[] | null,
    loading: boolean,
    error?: string,
    actions?: PreviewRowActions
  ) => ReactNode;
  /** 确认成功后的提示文案（如吉客云采购报表确认后自动映射为采购单）；返回空串则不提示。 */
  buildConfirmNotice?: (row: Row) => string;
};

export function LifecyclePanel<Row extends { id: number }, PreviewRow>(
  config: LifecyclePanelConfig<Row, PreviewRow>
) {
  const { api, renderSummary, renderPreview } = config;
  const [autoConfirm, setAutoConfirm] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [drafts, setDrafts] = useState<Row[]>([]);
  const [actives, setActives] = useState<Row[]>([]);
  const [trash, setTrash] = useState<Row[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [previewData, setPreviewData] = useState<Record<number, PreviewRow[]>>({});
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rowBusyKey, setRowBusyKey] = useState<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadAll = async () => {
    try {
      const [d, a, t] = await Promise.all([
        api.imports("draft"),
        api.imports("active"),
        api.imports("deleted"),
      ]);
      setDrafts(d);
      setActives(a);
      setTrash(t);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    loadAll();
  }, []);

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    setMessage("");
    try {
      const result = await api.upload(file, autoConfirm);
      setMessage(
        result.message ||
          (result.duplicate
            ? "该文件已导入过"
            : autoConfirm
            ? "已上传并直接生效"
            : "已上传并进入「待确认」，请在下方核对明细")
      );
      // 新上传的 draft 默认展开明细并立即拉取，方便马上核对/删行
      if (result.import && (result.import as Row).id) {
        const newId = (result.import as Row).id;
        setExpandedId(newId);
        setPreviewLoading(true);
        setPreviewError("");
        try {
          const rows = await api.records(newId);
          setPreviewData((prev) => ({ ...prev, [newId]: rows }));
        } catch (e) {
          setPreviewError(String(e));
        } finally {
          setPreviewLoading(false);
        }
      }
      await loadAll();
    } catch (e) {
      setError(String(e));
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleConfirm = async (id: number) => {
    setBusyId(id);
    setError("");
    try {
      const row = await api.confirm(id);
      const notice = config.buildConfirmNotice?.(row);
      if (notice) setMessage(notice);
      await loadAll();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("确定删除该导入？删除后进入回收站，30 天内可在回收站恢复。")) return;
    setBusyId(id);
    setError("");
    try {
      await api.softDelete(id);
      await loadAll();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  const handleRestore = async (id: number) => {
    setBusyId(id);
    setError("");
    try {
      await api.restore(id);
      await loadAll();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusyId(null);
    }
  };

  // 操作成功后静默重拉明细，保持行状态（置灰/恢复）与后端一致
  const refreshPreview = async (importId: number) => {
    try {
      const rows = await api.records(importId);
      setPreviewData((prev) => ({ ...prev, [importId]: rows }));
    } catch {
      // 拉取失败则清空缓存，下次展开时重新加载
      setPreviewData((prev) => {
        const next = { ...prev };
        delete next[importId];
        return next;
      });
    }
  };

  const handleRowDelete = async (importId: number, rowKey: number) => {
    if (!window.confirm("确定删除该行明细？该行将不再参与业务数据，可随时恢复。")) return;
    setRowBusyKey(rowKey);
    setError("");
    try {
      await api.deleteRow(importId, rowKey);
      await refreshPreview(importId);
    } catch (e) {
      setError(String(e));
    } finally {
      setRowBusyKey(null);
    }
  };

  const handleRowRestore = async (importId: number, rowKey: number) => {
    setRowBusyKey(rowKey);
    setError("");
    try {
      await api.restoreRow(importId, rowKey);
      await refreshPreview(importId);
    } catch (e) {
      setError(String(e));
    } finally {
      setRowBusyKey(null);
    }
  };

  const toggleExpand = async (id: number) => {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (previewData[id] === undefined) {
      setPreviewLoading(true);
      setPreviewError("");
      try {
        const rows = await api.records(id);
        setPreviewData((prev) => ({ ...prev, [id]: rows }));
      } catch (e) {
        setPreviewError(String(e));
      } finally {
        setPreviewLoading(false);
      }
    }
  };

  const renderCard = (row: Row, lifecycle: "draft" | "active" | "deleted") => {
    const isExpanded = expandedId === row.id;
    const rows = previewData[row.id] ?? null;
    const actions: PreviewRowActions = {
      onDeleteRow: (key) => handleRowDelete(row.id, key),
      onRestoreRow: (key) => handleRowRestore(row.id, key),
      busyRowKey: rowBusyKey,
      editable: lifecycle !== "deleted",
    };
    return (
      <div key={row.id} className="rounded-lg border border-gray-200 bg-white">
        <div className="flex flex-wrap items-center justify-between gap-3 p-3">
          <div className="min-w-0 flex-1">{renderSummary(row)}</div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              onClick={() => toggleExpand(row.id)}
              className="rounded-md border border-gray-200 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50"
            >
              {isExpanded ? "收起明细" : "查看明细"}
            </button>
            {lifecycle === "draft" && (
              <button
                onClick={() => handleConfirm(row.id)}
                disabled={busyId === row.id}
                className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
              >
                确认并自动处理
              </button>
            )}
            {lifecycle === "active" && (
              <button
                onClick={() => handleDelete(row.id)}
                disabled={busyId === row.id}
                className="rounded-md border border-amber-300 px-2.5 py-1 text-xs text-amber-700 hover:bg-amber-50"
              >
                移到回收站
              </button>
            )}
            {lifecycle === "deleted" && (
              <button
                onClick={() => handleRestore(row.id)}
                disabled={busyId === row.id}
                className="rounded-md border border-indigo-300 px-2.5 py-1 text-xs text-indigo-700 hover:bg-indigo-50"
              >
                恢复
              </button>
            )}
            {lifecycle !== "deleted" && (
              <button
                onClick={() => handleDelete(row.id)}
                disabled={busyId === row.id}
                className="rounded-md px-2.5 py-1 text-xs text-gray-400 hover:text-red-600"
              >
                删除
              </button>
            )}
          </div>
        </div>
        {isExpanded && (
          <div className="border-t border-gray-100 bg-gray-50/60 p-3">
            {previewLoading ? (
              <div className="py-6 text-center text-sm text-gray-400">解析明细加载中…</div>
            ) : (
              renderPreview(row, rows, previewLoading, previewError || undefined, actions)
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div>
      <h1 className="text-xl font-semibold">{config.title}</h1>
      <p className="mt-1 max-w-3xl text-sm leading-6 text-gray-500">{config.description}</p>

      <section className="mt-5 rounded-xl border border-dashed border-indigo-300 bg-indigo-50/40 p-6">
        <input
          ref={fileInputRef}
          type="file"
          accept={config.accept}
          className="hidden"
          onChange={handleFile}
        />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="text-sm">
            <div className="font-medium text-indigo-900">上传文件</div>
            <div className="mt-1 text-xs leading-5 text-indigo-700">{config.fileHint}</div>
          </div>
          <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={autoConfirm}
              onChange={(e) => setAutoConfirm(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300"
            />
            上传后自动确认并执行全链路（默认开启）
          </label>
        </div>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          className="mt-4 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? "导入中…" : "选择并上传文件"}
        </button>
      </section>

      {error && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {message && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>}

      <Section title="待确认" hint="关闭自动确认时，解析完成的文件会暂存到这里" tone="amber" count={drafts.length}>
        {drafts.length === 0 ? (
          <Empty text="暂无待确认导入。当前默认上传后自动确认。" />
        ) : (
          <div className="space-y-2">{drafts.map((r) => renderCard(r, "draft"))}</div>
        )}
      </Section>

      <Section title="已生效" hint="已确认并进入采购链路 / 发票台账" tone="emerald" count={actives.length}>
        {actives.length === 0 ? (
          <Empty text="暂无已生效导入。" />
        ) : (
          <div className="space-y-2">{actives.map((r) => renderCard(r, "active"))}</div>
        )}
      </Section>

      <RecycleBin count={trash.length}>
        {trash.length === 0 ? (
          <Empty text="回收站为空。" />
        ) : (
          <div className="space-y-2">{trash.map((r) => renderCard(r, "deleted"))}</div>
        )}
      </RecycleBin>
    </div>
  );
}

function Section({
  title,
  hint,
  tone,
  count,
  children,
}: {
  title: string;
  hint: string;
  tone: "amber" | "emerald";
  count: number;
  children: ReactNode;
}) {
  const toneClass =
    tone === "amber"
      ? "bg-amber-50 text-amber-700"
      : "bg-emerald-50 text-emerald-700";
  return (
    <section className="mt-6">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
        <span className={`rounded-full px-2 py-0.5 text-xs ${toneClass}`}>{count}</span>
      </div>
      <p className="mt-1 text-xs text-gray-400">{hint}</p>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function RecycleBin({ count, children }: { count: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <section className="mt-6">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-left"
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-gray-700">
          回收站
          <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs text-gray-600">{count}</span>
        </span>
        <span className="text-xs text-gray-400">{open ? "收起" : "展开（30 天内可恢复）"}</span>
      </button>
      {open && <div className="mt-3">{children}</div>}
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-gray-200 py-6 text-center text-sm text-gray-400">{text}</div>;
}
