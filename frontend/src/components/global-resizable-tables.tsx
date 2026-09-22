"use client";

import { useEffect } from "react";

const MIN_WIDTH = 56;
const MAX_WIDTH = 960;
const STORAGE_PREFIX = "ecommerce-table-widths:v1";

type WidthMap = Record<string, number>;

function normalizeText(value: string | null | undefined) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function headerRowOf(table: HTMLTableElement) {
  if (table.tHead?.rows.length) return table.tHead.rows[table.tHead.rows.length - 1];
  return Array.from(table.rows).find((row) =>
    Array.from(row.cells).some((cell) => cell.tagName === "TH"),
  );
}

function tableStorageKey(table: HTMLTableElement, tableIndex: number) {
  const explicit = table.dataset.resizableTableKey;
  if (explicit) return `${STORAGE_PREFIX}:${window.location.pathname}:${explicit}`;

  const headerRow = headerRowOf(table);
  const signature = headerRow
    ? Array.from(headerRow.cells)
        .map((cell) => normalizeText(cell.textContent))
        .filter(Boolean)
        .slice(0, 24)
        .join("|")
    : "";

  return `${STORAGE_PREFIX}:${window.location.pathname}:${signature || `table-${tableIndex}`}`;
}

function readWidths(key: string): WidthMap {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || "{}") as WidthMap;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function writeWidths(key: string, widths: WidthMap) {
  try {
    window.localStorage.setItem(key, JSON.stringify(widths));
  } catch {
    // localStorage 被禁用时，拖拽仍然生效，只是不持久化。
  }
}

function setColumnWidth(table: HTMLTableElement, index: number, width: number) {
  const px = `${Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(width)))}px`;

  for (const row of Array.from(table.rows)) {
    const cell = row.cells[index] as HTMLTableCellElement | undefined;
    if (!cell || cell.colSpan !== 1) continue;
    cell.style.width = px;
    cell.style.minWidth = px;
    cell.style.maxWidth = px;
    cell.classList.add("global-column-sized");
  }
}

function clearColumnWidth(table: HTMLTableElement, index: number) {
  for (const row of Array.from(table.rows)) {
    const cell = row.cells[index] as HTMLTableCellElement | undefined;
    if (!cell || cell.colSpan !== 1) continue;
    cell.style.removeProperty("width");
    cell.style.removeProperty("min-width");
    cell.style.removeProperty("max-width");
    cell.classList.remove("global-column-sized");
  }
}

function installResizableTable(table: HTMLTableElement, tableIndex: number) {
  if (table.dataset.globalResizableInstalled === "1") return;
  if (table.dataset.resizable === "false") return;

  const headerRow = headerRowOf(table);
  if (!headerRow || headerRow.cells.length === 0) return;

  const headers = Array.from(headerRow.cells).filter(
    (cell): cell is HTMLTableCellElement => cell instanceof HTMLTableCellElement && cell.tagName === "TH",
  );
  if (headers.length === 0) return;

  table.dataset.globalResizableInstalled = "1";
  table.classList.add("global-resizable-table");

  const parent = table.parentElement;
  if (parent) parent.classList.add("global-table-scroll-host");

  const key = tableStorageKey(table, tableIndex);
  const widths = readWidths(key);

  headers.forEach((header, index) => {
    const saved = widths[String(index)];
    if (Number.isFinite(saved) && saved >= MIN_WIDTH) {
      setColumnWidth(table, index, saved);
    }

    if (header.querySelector(":scope > .global-column-resizer")) return;

    const handle = document.createElement("span");
    handle.className = "global-column-resizer";
    handle.setAttribute("role", "separator");
    handle.setAttribute("aria-orientation", "vertical");
    handle.setAttribute("aria-label", `调整“${normalizeText(header.textContent) || `第 ${index + 1} 列`}”列宽`);
    handle.title = "拖动调整列宽；双击恢复默认宽度";

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();

      const startX = event.clientX;
      const startWidth = header.getBoundingClientRect().width;
      document.documentElement.classList.add("global-column-resizing");
      handle.classList.add("is-active");

      const onMove = (moveEvent: PointerEvent) => {
        const nextWidth = startWidth + moveEvent.clientX - startX;
        setColumnWidth(table, index, nextWidth);
      };

      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        document.documentElement.classList.remove("global-column-resizing");
        handle.classList.remove("is-active");

        const finalWidth = header.getBoundingClientRect().width;
        const current = readWidths(key);
        current[String(index)] = Math.round(finalWidth);
        writeWidths(key, current);
      };

      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp, { once: true });
      window.addEventListener("pointercancel", onUp, { once: true });
    };

    handle.addEventListener("pointerdown", onPointerDown);
    handle.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      clearColumnWidth(table, index);
      const current = readWidths(key);
      delete current[String(index)];
      writeWidths(key, current);
    });

    header.appendChild(handle);
  });
}

function scanTables(root: ParentNode | Element = document) {
  const tables: HTMLTableElement[] = [];
  if (root instanceof HTMLTableElement) tables.push(root);
  tables.push(...Array.from(root.querySelectorAll("table")) as HTMLTableElement[]);
  tables.forEach((table, index) => installResizableTable(table, index));
}

export default function GlobalResizableTables() {
  useEffect(() => {
    let frame = window.requestAnimationFrame(() => scanTables());
    const pendingRoots = new Set<Element>();

    const scheduleScan = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        for (const root of pendingRoots) scanTables(root);
        pendingRoots.clear();
      });
    };

    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type !== "childList" || mutation.addedNodes.length === 0) continue;
        for (const node of Array.from(mutation.addedNodes)) {
          if (!(node instanceof Element)) continue;
          if (node.matches("table") || node.querySelector("table")) pendingRoots.add(node);
        }
      }
      if (pendingRoots.size) scheduleScan();
    });

    observer.observe(document.body, { childList: true, subtree: true });

    return () => {
      window.cancelAnimationFrame(frame);
      pendingRoots.clear();
      observer.disconnect();
      document.documentElement.classList.remove("global-column-resizing");
    };
  }, []);

  return null;
}
