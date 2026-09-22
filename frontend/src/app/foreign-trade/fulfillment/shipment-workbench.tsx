"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type ShipmentCosts = {
  cifValue: string;
  customsDuty: string;
  antiDumpingDuty: string;
  countervailingDuty: string;
  importVatBase: string;
  importVat: string;
  importTaxTotal: string;
  importAncillaryFees: string;
  landedCashRequirement: string;
  landedCostExRecoverableVat: string;
  perUnitLandedCostExRecoverableVat: string;
  estimatedExportRefundCny: string;
  chinaNetCostEstimatedCny: string;
  chinaNetCostActualCny: string;
};

type LegalEntity = {
  id: number;
  code: string;
  name: string;
  countryCode: string;
  baseCurrency: string;
  isDefault: boolean;
};

type Milestone = {
  code: string;
  label?: string;
  location?: string;
  occurredAt?: string;
  note?: string;
};

type Shipment = {
  id: number;
  shipmentNo: string;
  orderNos: string[];
  brand: string;
  originCountry: string;
  destinationCountry: string;
  destinationCity: string;
  transportMode: string;
  incoterm: string;
  status: string;
  exporterLegalEntityId: number | null;
  importerKind: string;
  importerLegalEntityId: number | null;
  carrier: string;
  bookingNo: string;
  billOfLadingNo: string;
  containerNo: string;
  trackingNo: string;
  exportCustomsNo: string;
  importCustomsNo: string;
  commercialInvoiceNo: string;
  eoriNo: string;
  hsCode: string;
  cnCode: string;
  manufacturerName: string;
  taricAdditionalCode: string;
  taxRateSource: string;
  taxRateCheckedAt: string | null;
  currency: string;
  quantity: string;
  declaredValue: string;
  freightToEu: string;
  insurance: string;
  customsRate: string;
  antiDumpingRate: string;
  countervailingRate: string;
  importVatRate: string;
  importVatRecoverable: boolean;
  importVatAdditionalBase: string;
  clearanceFee: string;
  portFee: string;
  lastMileFee: string;
  otherImportFee: string;
  exportPurchaseCostCny: string;
  domesticExportCostCny: string;
  exportRefundBaseCny: string;
  exportRefundRate: string;
  actualExportRefundCny: string;
  exportRefundStatus: string;
  exportRefundReceivedAt: string | null;
  eurToCny: string;
  etd: string | null;
  eta: string | null;
  departedAt: string | null;
  arrivedEuAt: string | null;
  customsClearedAt: string | null;
  deliveredAt: string | null;
  milestones: Milestone[];
  note: string;
  costs: ShipmentCosts;
};

type ShipmentDraft = {
  shipment_no: string;
  order_nos: string[];
  brand: string;
  origin_country: string;
  destination_country: string;
  destination_city: string;
  transport_mode: string;
  incoterm: string;
  status: string;
  exporter_legal_entity_id: number | null;
  importer_kind: string;
  importer_legal_entity_id: number | null;
  carrier: string;
  booking_no: string;
  bill_of_lading_no: string;
  container_no: string;
  tracking_no: string;
  export_customs_no: string;
  import_customs_no: string;
  commercial_invoice_no: string;
  eori_no: string;
  hs_code: string;
  cn_code: string;
  manufacturer_name: string;
  taric_additional_code: string;
  tax_rate_source: string;
  tax_rate_checked_at: string | null;
  currency: string;
  quantity: string;
  declared_value: string;
  freight_to_eu: string;
  insurance: string;
  customs_rate: string;
  anti_dumping_rate: string;
  countervailing_rate: string;
  import_vat_rate: string;
  import_vat_recoverable: boolean;
  import_vat_additional_base: string;
  clearance_fee: string;
  port_fee: string;
  last_mile_fee: string;
  other_import_fee: string;
  export_purchase_cost_cny: string;
  domestic_export_cost_cny: string;
  export_refund_base_cny: string;
  export_refund_rate: string;
  actual_export_refund_cny: string;
  export_refund_status: string;
  export_refund_received_at: string | null;
  eur_to_cny: string;
  etd: string | null;
  eta: string | null;
  departed_at: string | null;
  arrived_eu_at: string | null;
  customs_cleared_at: string | null;
  delivered_at: string | null;
  milestones: Milestone[];
  documents: Record<string, unknown>[];
  note: string;
};

const EMPTY: ShipmentDraft = {
  shipment_no: "",
  order_nos: [],
  brand: "Alsvid",
  origin_country: "CN",
  destination_country: "AT",
  destination_city: "",
  transport_mode: "sea",
  incoterm: "FOB",
  status: "preparing",
  exporter_legal_entity_id: null,
  importer_kind: "external_customer",
  importer_legal_entity_id: null,
  carrier: "",
  booking_no: "",
  bill_of_lading_no: "",
  container_no: "",
  tracking_no: "",
  export_customs_no: "",
  import_customs_no: "",
  commercial_invoice_no: "",
  eori_no: "",
  hs_code: "871160",
  cn_code: "",
  manufacturer_name: "",
  taric_additional_code: "C999",
  tax_rate_source: "保守估算：未匹配生产企业个别税率时按 C999 估算；正式报关前重新核对 TARIC 与商业发票。",
  tax_rate_checked_at: null,
  currency: "EUR",
  quantity: "1",
  declared_value: "0",
  freight_to_eu: "0",
  insurance: "0",
  customs_rate: "6",
  anti_dumping_rate: "62.1",
  countervailing_rate: "17.2",
  import_vat_rate: "20",
  import_vat_recoverable: true,
  import_vat_additional_base: "0",
  clearance_fee: "0",
  port_fee: "0",
  last_mile_fee: "0",
  other_import_fee: "0",
  export_purchase_cost_cny: "0",
  domestic_export_cost_cny: "0",
  export_refund_base_cny: "0",
  export_refund_rate: "0",
  actual_export_refund_cny: "0",
  export_refund_status: "pending",
  export_refund_received_at: null,
  eur_to_cny: "1",
  etd: null,
  eta: null,
  departed_at: null,
  arrived_eu_at: null,
  customs_cleared_at: null,
  delivered_at: null,
  milestones: [],
  documents: [],
  note: "",
};

const STATUS: Record<string, string> = {
  preparing: "准备出运",
  picked_up: "国内提货",
  export_customs: "出口报关",
  export_released: "出口放行",
  departed: "已离境",
  in_transit: "国际运输",
  arrived_eu: "抵达欧盟",
  import_customs: "进口清关",
  customs_cleared: "清关完成",
  last_mile: "奥地利派送",
  delivered: "已签收",
};

const MILESTONES = [
  ["picked_up", "国内提货"],
  ["export_declared", "出口报关"],
  ["export_released", "出口放行"],
  ["departed_china", "离开中国"],
  ["in_transit", "国际运输"],
  ["arrived_eu", "抵达欧盟"],
  ["import_declared", "进口申报"],
  ["customs_cleared", "进口清关完成"],
  ["last_mile", "奥地利末端派送"],
  ["delivered", "签收"],
] as const;

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, init);
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: string };
    throw new Error(body.detail || `请求失败（${res.status}）`);
  }
  return res.json();
}

function money(value: string | number, currency = "EUR") {
  const n = Number(value || 0);
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency, maximumFractionDigits: 2 }).format(Number.isFinite(n) ? n : 0);
}

function num(value: string | number) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n : 0;
}

function localInput(value: string | null) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromInput(value: string) {
  return value ? new Date(value).toISOString() : null;
}

function toDraft(row: Shipment): ShipmentDraft {
  return {
    shipment_no: row.shipmentNo,
    order_nos: row.orderNos,
    brand: row.brand,
    origin_country: row.originCountry,
    destination_country: row.destinationCountry,
    destination_city: row.destinationCity,
    transport_mode: row.transportMode,
    incoterm: row.incoterm,
    status: row.status,
    exporter_legal_entity_id: row.exporterLegalEntityId,
    importer_kind: row.importerKind,
    importer_legal_entity_id: row.importerLegalEntityId,
    carrier: row.carrier,
    booking_no: row.bookingNo,
    bill_of_lading_no: row.billOfLadingNo,
    container_no: row.containerNo,
    tracking_no: row.trackingNo,
    export_customs_no: row.exportCustomsNo,
    import_customs_no: row.importCustomsNo,
    commercial_invoice_no: row.commercialInvoiceNo,
    eori_no: row.eoriNo,
    hs_code: row.hsCode,
    cn_code: row.cnCode,
    manufacturer_name: row.manufacturerName,
    taric_additional_code: row.taricAdditionalCode,
    tax_rate_source: row.taxRateSource,
    tax_rate_checked_at: row.taxRateCheckedAt,
    currency: row.currency,
    quantity: row.quantity,
    declared_value: row.declaredValue,
    freight_to_eu: row.freightToEu,
    insurance: row.insurance,
    customs_rate: row.customsRate,
    anti_dumping_rate: row.antiDumpingRate,
    countervailing_rate: row.countervailingRate,
    import_vat_rate: row.importVatRate,
    import_vat_recoverable: row.importVatRecoverable,
    import_vat_additional_base: row.importVatAdditionalBase,
    clearance_fee: row.clearanceFee,
    port_fee: row.portFee,
    last_mile_fee: row.lastMileFee,
    other_import_fee: row.otherImportFee,
    export_purchase_cost_cny: row.exportPurchaseCostCny,
    domestic_export_cost_cny: row.domesticExportCostCny,
    export_refund_base_cny: row.exportRefundBaseCny,
    export_refund_rate: row.exportRefundRate,
    actual_export_refund_cny: row.actualExportRefundCny,
    export_refund_status: row.exportRefundStatus,
    export_refund_received_at: row.exportRefundReceivedAt,
    eur_to_cny: row.eurToCny,
    etd: row.etd,
    eta: row.eta,
    departed_at: row.departedAt,
    arrived_eu_at: row.arrivedEuAt,
    customs_cleared_at: row.customsClearedAt,
    delivered_at: row.deliveredAt,
    milestones: row.milestones,
    documents: [],
    note: row.note,
  };
}

function StatusPill({ value }: { value: string }) {
  const done = value === "delivered";
  const customs = value === "import_customs" || value === "export_customs";
  return (
    <span className={"inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium " + (
      done ? "bg-emerald-50 text-emerald-700" : customs ? "bg-amber-50 text-amber-700" : "bg-blue-50 text-blue-700"
    )}>
      {STATUS[value] || value}
    </span>
  );
}

export default function ShipmentWorkbench() {
  const [rows, setRows] = useState<Shipment[]>([]);
  const [entities, setEntities] = useState<LegalEntity[]>([]);
  const [selected, setSelected] = useState<Shipment | null>(null);
  const [draft, setDraft] = useState<ShipmentDraft>({ ...EMPTY });
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [milestoneCode, setMilestoneCode] = useState("picked_up");
  const [milestoneLocation, setMilestoneLocation] = useState("");
  const [milestoneNote, setMilestoneNote] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (q.trim()) params.set("q", q.trim());
      if (status) params.set("status", status);
      const data = await api<{ items: Shipment[] }>("/api/v1/foreign-trade/shipments?" + params.toString());
      setRows(data.items);
      setError("");
      if (selected) {
        const fresh = data.items.find((item) => item.id === selected.id);
        if (fresh) {
          setSelected(fresh);
          setDraft(toDraft(fresh));
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [q, selected, status]);

  useEffect(() => {
    api<{ items: LegalEntity[] }>("/api/v1/finance/entities")
      .then((data) => setEntities(data.items))
      .catch(() => setEntities([]));
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 120);
    return () => window.clearTimeout(timer);
  }, [q, status]); // eslint-disable-line react-hooks/exhaustive-deps

  const summary = useMemo(() => {
    return rows.reduce((acc, row) => {
      if (row.status === "delivered") acc.delivered += 1;
      else if (["import_customs", "customs_cleared"].includes(row.status)) acc.customs += 1;
      else acc.inTransit += 1;
      acc.importTax += num(row.costs.importTaxTotal);
      acc.refund += num(row.costs.estimatedExportRefundCny);
      return acc;
    }, { inTransit: 0, customs: 0, delivered: 0, importTax: 0, refund: 0 });
  }, [rows]);

  function openNew() {
    setSelected(null);
    const defaultEntity = entities.find((item) => item.isDefault) ?? entities[0];
    setDraft({
      ...EMPTY,
      exporter_legal_entity_id: defaultEntity?.id ?? null,
      shipment_no: "EXP-" + new Date().toISOString().slice(0, 10).replaceAll("-", "") + "-",
    });
    setFormOpen(true);
  }

  function openEdit(row: Shipment) {
    setSelected(row);
    setDraft(toDraft(row));
    setFormOpen(true);
  }

  async function save() {
    if (!draft.shipment_no.trim()) return setError("出运单号必须填写");
    setSaving(true);
    setError("");
    try {
      const url = selected ? `/api/v1/foreign-trade/shipments/${selected.id}` : "/api/v1/foreign-trade/shipments";
      const saved = await api<Shipment>(url, {
        method: selected ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      setSelected(saved);
      setDraft(toDraft(saved));
      setFormOpen(true);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function addMilestone() {
    if (!selected) return setError("请先保存出运单");
    const label = MILESTONES.find(([code]) => code === milestoneCode)?.[1] || milestoneCode;
    try {
      const saved = await api<Shipment>(`/api/v1/foreign-trade/shipments/${selected.id}/milestones`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: milestoneCode,
          label,
          location: milestoneLocation,
          note: milestoneNote,
        }),
      });
      setSelected(saved);
      setDraft(toDraft(saved));
      setMilestoneLocation("");
      setMilestoneNote("");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "节点更新失败");
    }
  }

  async function remove(row: Shipment) {
    if (!window.confirm(`删除出运单 ${row.shipmentNo}？`)) return;
    await api(`/api/v1/foreign-trade/shipments/${row.id}`, { method: "DELETE" });
    if (selected?.id === row.id) {
      setSelected(null);
      setFormOpen(false);
    }
    await load();
  }

  return (
    <div className="min-h-full bg-slate-50/70 px-5 py-5">
      <div className="mx-auto max-w-[1680px]">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-blue-500">FOREIGN TRADE · SHIPMENT</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-950">国际出运跟踪</h1>
            <p className="mt-1 text-sm text-slate-500">中国出货 → 出口报关 → 国际运输 → 欧盟进口清关 → 奥地利末端派送，同时跟踪税费、退税和落地成本。</p>
          </div>
          <button onClick={openNew} className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white">+ 新建出运单</button>
        </div>

        {error && <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>}

        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <Stat label="在途 / 待处理" value={String(summary.inTransit)} />
          <Stat label="清关阶段" value={String(summary.customs)} />
          <Stat label="已签收" value={String(summary.delivered)} />
          <Stat label="预计进口税费" value={money(summary.importTax, "EUR")} />
          <Stat label="预计出口退税" value={money(summary.refund, "CNY")} />
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex gap-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="出运单 / 提单 / 柜号 / 运单 / 生产企业" className="ft-input w-[320px]" />
            <select value={status} onChange={(e) => setStatus(e.target.value)} className="ft-input w-40">
              <option value="">全部状态</option>
              {Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </div>
          <div className="text-[10px] text-slate-400">税率是出运单快照；正式报关前必须按生产企业、HS/CN/TARIC 重新核对。</div>
        </div>

        <section className="mt-3 overflow-x-auto rounded-xl border border-slate-200 bg-white">
          {loading ? <div className="py-14 text-center text-sm text-slate-400">正在加载出运单…</div> : rows.length === 0 ? (
            <div className="py-14 text-center text-sm text-slate-400">暂无出运记录。</div>
          ) : (
            <table className="min-w-[1450px] w-full text-left text-xs">
              <thead className="bg-slate-50 text-slate-500">
                <tr><th className="px-4 py-3">出运单</th><th>路线 / 方式</th><th>状态</th><th>提单 / 柜号</th><th>ETA</th><th>生产企业 / TARIC</th><th>反倾销 + 反补贴</th><th>预计进口税费</th><th>预计退税</th><th>落地成本/台</th><th className="pr-4 text-right">操作</th></tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id} className="border-t border-slate-100 align-middle hover:bg-slate-50/50">
                    <td className="px-4 py-3">
                      <div className="font-semibold text-slate-800">{row.shipmentNo}</div>
                      <div className="mt-0.5 text-[10px] text-slate-400">{row.orderNos.join(" / ") || row.brand || "—"}</div>
                    </td>
                    <td><div>{row.originCountry} → {row.destinationCountry}{row.destinationCity ? " · " + row.destinationCity : ""}</div><div className="text-[10px] text-slate-400">{row.transportMode.toUpperCase()} · {row.incoterm}</div></td>
                    <td><StatusPill value={row.status} /></td>
                    <td><div>{row.billOfLadingNo || row.trackingNo || "—"}</div><div className="text-[10px] text-slate-400">{row.containerNo || "—"}</div></td>
                    <td>{row.eta ? new Date(row.eta).toLocaleDateString("zh-CN") : "—"}</td>
                    <td><div className="max-w-44 truncate" title={row.manufacturerName}>{row.manufacturerName || "未确认"}</div><div className="text-[10px] text-slate-400">{row.taricAdditionalCode || "—"}</div></td>
                    <td><div>{num(row.antiDumpingRate).toFixed(1)}% + {num(row.countervailingRate).toFixed(1)}%</div><div className="text-[10px] text-slate-400">普通关税 {num(row.customsRate).toFixed(1)}%</div></td>
                    <td className="font-medium text-amber-700">{money(row.costs.importTaxTotal, row.currency)}</td>
                    <td className="font-medium text-emerald-700">{money(row.costs.estimatedExportRefundCny, "CNY")}</td>
                    <td><div className="font-semibold text-slate-800">{money(row.costs.perUnitLandedCostExRecoverableVat, row.currency)}</div><div className="text-[10px] text-slate-400">{row.importVatRecoverable ? "已排除可抵扣 VAT" : "含不可抵扣 VAT"}</div></td>
                    <td className="pr-4 text-right whitespace-nowrap">
                      <button className="text-blue-600" onClick={() => openEdit(row)}>查看 / 编辑</button>
                      <button className="ml-3 text-rose-500" onClick={() => void remove(row)}>删除</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        {formOpen && (
          <section className="mt-4 app-card rounded-xl p-4">
            <div className="flex items-center justify-between gap-3 border-b border-slate-100 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">{selected ? "出运单详情" : "新建出运单"}</h2>
                <p className="mt-0.5 text-[10px] text-slate-400">物流节点、进口税费和中国出口退税使用同一票据归集。</p>
              </div>
              <div className="flex gap-2">
                <button onClick={() => setFormOpen(false)} className="app-button-secondary rounded-lg px-3 py-1.5 text-[11px]">收起</button>
                <button onClick={() => void save()} disabled={saving} className="app-button-primary rounded-lg px-4 py-1.5 text-[11px] font-medium disabled:opacity-50">{saving ? "保存中…" : "保存"}</button>
              </div>
            </div>

            <SectionTitle title="1. 出运与运输" desc="一票从中国离境到奥地利签收的唯一主记录。" />
            <div className="grid gap-2 md:grid-cols-4 xl:grid-cols-6">
              <Field label="出运单号"><input className="ft-input" value={draft.shipment_no} onChange={(e) => setDraft({ ...draft, shipment_no: e.target.value })} /></Field>
              <Field label="关联订单号"><input className="ft-input" value={draft.order_nos.join(", ")} onChange={(e) => setDraft({ ...draft, order_nos: e.target.value.split(/[,，]/).map(v => v.trim()).filter(Boolean) })} /></Field>
              <Field label="品牌"><input className="ft-input" value={draft.brand} onChange={(e) => setDraft({ ...draft, brand: e.target.value })} /></Field>
              <Field label="运输方式"><select className="ft-input" value={draft.transport_mode} onChange={(e) => setDraft({ ...draft, transport_mode: e.target.value })}><option value="sea">海运</option><option value="air">空运</option><option value="rail">铁路</option><option value="road">公路</option><option value="express">快递</option></select></Field>
              <Field label="贸易条款"><select className="ft-input" value={draft.incoterm} onChange={(e) => setDraft({ ...draft, incoterm: e.target.value })}><option>EXW</option><option>FOB</option><option>CIF</option><option>DAP</option><option>DDP</option></select></Field>
              <Field label="状态"><select className="ft-input" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}>{Object.entries(STATUS).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></Field>

              <Field label="出口公司主体">
                <select className="ft-input" value={draft.exporter_legal_entity_id ?? ""} onChange={(e) => setDraft({ ...draft, exporter_legal_entity_id: Number(e.target.value) || null })}>
                  <option value="">默认主体</option>
                  {entities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </Field>
              <Field label="进口责任方">
                <select
                  className="ft-input"
                  value={draft.importer_kind}
                  onChange={(e) => setDraft({
                    ...draft,
                    importer_kind: e.target.value,
                    importer_legal_entity_id: e.target.value === "own_entity" ? draft.importer_legal_entity_id : null,
                  })}
                >
                  <option value="external_customer">海外客户自行进口</option>
                  <option value="dealer">经销商进口</option>
                  <option value="own_entity">我方海外公司进口</option>
                  <option value="agent">第三方进口代理</option>
                </select>
              </Field>
              <Field label="进口公司主体">
                <select
                  className="ft-input"
                  disabled={draft.importer_kind !== "own_entity"}
                  value={draft.importer_legal_entity_id ?? ""}
                  onChange={(e) => setDraft({ ...draft, importer_legal_entity_id: Number(e.target.value) || null })}
                >
                  <option value="">{draft.importer_kind === "own_entity" ? "请选择主体" : "非我方主体"}</option>
                  {entities.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </Field>

              <Field label="目的城市"><input className="ft-input" value={draft.destination_city} onChange={(e) => setDraft({ ...draft, destination_city: e.target.value })} placeholder="Vienna / Graz..." /></Field>
              <Field label="船司 / 货代"><input className="ft-input" value={draft.carrier} onChange={(e) => setDraft({ ...draft, carrier: e.target.value })} /></Field>
              <Field label="Booking No."><input className="ft-input" value={draft.booking_no} onChange={(e) => setDraft({ ...draft, booking_no: e.target.value })} /></Field>
              <Field label="提单号"><input className="ft-input" value={draft.bill_of_lading_no} onChange={(e) => setDraft({ ...draft, bill_of_lading_no: e.target.value })} /></Field>
              <Field label="柜号"><input className="ft-input" value={draft.container_no} onChange={(e) => setDraft({ ...draft, container_no: e.target.value })} /></Field>
              <Field label="末端运单号"><input className="ft-input" value={draft.tracking_no} onChange={(e) => setDraft({ ...draft, tracking_no: e.target.value })} /></Field>

              <Field label="ETD"><input type="datetime-local" className="ft-input" value={localInput(draft.etd)} onChange={(e) => setDraft({ ...draft, etd: fromInput(e.target.value) })} /></Field>
              <Field label="ETA"><input type="datetime-local" className="ft-input" value={localInput(draft.eta)} onChange={(e) => setDraft({ ...draft, eta: fromInput(e.target.value) })} /></Field>
              <Field label="出口报关单号"><input className="ft-input" value={draft.export_customs_no} onChange={(e) => setDraft({ ...draft, export_customs_no: e.target.value })} /></Field>
              <Field label="进口申报号"><input className="ft-input" value={draft.import_customs_no} onChange={(e) => setDraft({ ...draft, import_customs_no: e.target.value })} /></Field>
              <Field label="商业发票号"><input className="ft-input" value={draft.commercial_invoice_no} onChange={(e) => setDraft({ ...draft, commercial_invoice_no: e.target.value })} /></Field>
              <Field label="奥地利 EORI"><input className="ft-input" value={draft.eori_no} onChange={(e) => setDraft({ ...draft, eori_no: e.target.value })} /></Field>
            </div>

            <SectionTitle title="2. 欧盟进口税费" desc="税率按每票实际生产企业和 TARIC 附加代码锁定；C999 只作为保守估算。" />
            <div className="grid gap-2 md:grid-cols-4 xl:grid-cols-7">
              <Field label="HS Code"><input className="ft-input" value={draft.hs_code} onChange={(e) => setDraft({ ...draft, hs_code: e.target.value })} /></Field>
              <Field label="CN / TARIC Code"><input className="ft-input" value={draft.cn_code} onChange={(e) => setDraft({ ...draft, cn_code: e.target.value })} placeholder="87116010 / ..." /></Field>
              <Field label="实际生产企业"><input className="ft-input" value={draft.manufacturer_name} onChange={(e) => setDraft({ ...draft, manufacturer_name: e.target.value })} /></Field>
              <Field label="TARIC 附加码"><input className="ft-input" value={draft.taric_additional_code} onChange={(e) => setDraft({ ...draft, taric_additional_code: e.target.value.toUpperCase() })} /></Field>
              <Field label="普通关税 %"><input className="ft-input" inputMode="decimal" value={draft.customs_rate} onChange={(e) => setDraft({ ...draft, customs_rate: e.target.value })} /></Field>
              <Field label="反倾销税 %"><input className="ft-input" inputMode="decimal" value={draft.anti_dumping_rate} onChange={(e) => setDraft({ ...draft, anti_dumping_rate: e.target.value })} /></Field>
              <Field label="反补贴税 %"><input className="ft-input" inputMode="decimal" value={draft.countervailing_rate} onChange={(e) => setDraft({ ...draft, countervailing_rate: e.target.value })} /></Field>

              <Field label="数量"><input className="ft-input" inputMode="decimal" value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: e.target.value })} /></Field>
              <Field label="申报货值"><input className="ft-input" inputMode="decimal" value={draft.declared_value} onChange={(e) => setDraft({ ...draft, declared_value: e.target.value })} /></Field>
              <Field label="国际运费"><input className="ft-input" inputMode="decimal" value={draft.freight_to_eu} onChange={(e) => setDraft({ ...draft, freight_to_eu: e.target.value })} /></Field>
              <Field label="保险"><input className="ft-input" inputMode="decimal" value={draft.insurance} onChange={(e) => setDraft({ ...draft, insurance: e.target.value })} /></Field>
              <Field label="奥地利进口 VAT %"><input className="ft-input" inputMode="decimal" value={draft.import_vat_rate} onChange={(e) => setDraft({ ...draft, import_vat_rate: e.target.value })} /></Field>
              <Field label="VAT 追加计税基础"><input className="ft-input" inputMode="decimal" value={draft.import_vat_additional_base} onChange={(e) => setDraft({ ...draft, import_vat_additional_base: e.target.value })} /></Field>
              <label className="flex h-[58px] items-end pb-2 text-[11px] text-slate-600"><input type="checkbox" checked={draft.import_vat_recoverable} onChange={(e) => setDraft({ ...draft, import_vat_recoverable: e.target.checked })} className="mr-2" />进口 VAT 可抵扣</label>

              <Field label="清关费"><input className="ft-input" inputMode="decimal" value={draft.clearance_fee} onChange={(e) => setDraft({ ...draft, clearance_fee: e.target.value })} /></Field>
              <Field label="港杂 / 码头费"><input className="ft-input" inputMode="decimal" value={draft.port_fee} onChange={(e) => setDraft({ ...draft, port_fee: e.target.value })} /></Field>
              <Field label="奥地利末端费"><input className="ft-input" inputMode="decimal" value={draft.last_mile_fee} onChange={(e) => setDraft({ ...draft, last_mile_fee: e.target.value })} /></Field>
              <Field label="其他进口费用"><input className="ft-input" inputMode="decimal" value={draft.other_import_fee} onChange={(e) => setDraft({ ...draft, other_import_fee: e.target.value })} /></Field>
              <div className="md:col-span-4 xl:col-span-3">
                <Field label="税率依据 / 备注"><input className="ft-input" value={draft.tax_rate_source} onChange={(e) => setDraft({ ...draft, tax_rate_source: e.target.value })} /></Field>
              </div>
            </div>

            {selected && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
                <Calc label="CIF" value={money(selected.costs.cifValue, selected.currency)} />
                <Calc label="普通关税" value={money(selected.costs.customsDuty, selected.currency)} />
                <Calc label="反倾销税" value={money(selected.costs.antiDumpingDuty, selected.currency)} />
                <Calc label="反补贴税" value={money(selected.costs.countervailingDuty, selected.currency)} />
                <Calc label="进口 VAT" value={money(selected.costs.importVat, selected.currency)} />
                <Calc label="落地成本 / 台" value={money(selected.costs.perUnitLandedCostExRecoverableVat, selected.currency)} strong />
              </div>
            )}

            <SectionTitle title="3. 中国出口与退税" desc="退税率不写死，按实际中国商品编码和税务口径填写，并区分预计退税与实际到账。" />
            <div className="grid gap-2 md:grid-cols-4 xl:grid-cols-7">
              <Field label="采购含税成本 ¥"><input className="ft-input" inputMode="decimal" value={draft.export_purchase_cost_cny} onChange={(e) => setDraft({ ...draft, export_purchase_cost_cny: e.target.value })} /></Field>
              <Field label="国内出口费用 ¥"><input className="ft-input" inputMode="decimal" value={draft.domestic_export_cost_cny} onChange={(e) => setDraft({ ...draft, domestic_export_cost_cny: e.target.value })} /></Field>
              <Field label="退税计税基础 ¥"><input className="ft-input" inputMode="decimal" value={draft.export_refund_base_cny} onChange={(e) => setDraft({ ...draft, export_refund_base_cny: e.target.value })} /></Field>
              <Field label="出口退税率 %"><input className="ft-input" inputMode="decimal" value={draft.export_refund_rate} onChange={(e) => setDraft({ ...draft, export_refund_rate: e.target.value })} /></Field>
              <Field label="实际退税到账 ¥"><input className="ft-input" inputMode="decimal" value={draft.actual_export_refund_cny} onChange={(e) => setDraft({ ...draft, actual_export_refund_cny: e.target.value })} /></Field>
              <Field label="退税状态"><select className="ft-input" value={draft.export_refund_status} onChange={(e) => setDraft({ ...draft, export_refund_status: e.target.value })}><option value="pending">待申报</option><option value="submitted">已申报</option><option value="reviewing">审核中</option><option value="approved">已核准</option><option value="paid">已到账</option><option value="rejected">异常/退回</option></select></Field>
              <Field label="退税到账时间"><input type="datetime-local" className="ft-input" value={localInput(draft.export_refund_received_at)} onChange={(e) => setDraft({ ...draft, export_refund_received_at: fromInput(e.target.value) })} /></Field>
              <Field label="EUR → CNY"><input className="ft-input" inputMode="decimal" value={draft.eur_to_cny} onChange={(e) => setDraft({ ...draft, eur_to_cny: e.target.value })} /></Field>
            </div>

            {selected && (
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <Calc label="预计出口退税" value={money(selected.costs.estimatedExportRefundCny, "CNY")} strong />
                <Calc label="预计中国侧净成本" value={money(selected.costs.chinaNetCostEstimatedCny, "CNY")} />
                <Calc label="按实际到账后的净成本" value={money(selected.costs.chinaNetCostActualCny, "CNY")} />
              </div>
            )}

            <SectionTitle title="4. 出运时间线" desc="每次更新节点都会保留时间、地点和备注，后续可以接船司/货代 Tracking API 自动写入。" />
            {selected ? (
              <>
                <div className="grid gap-2 md:grid-cols-[190px_1fr_1fr_auto]">
                  <select className="ft-input" value={milestoneCode} onChange={(e) => setMilestoneCode(e.target.value)}>{MILESTONES.map(([code,label]) => <option key={code} value={code}>{label}</option>)}</select>
                  <input className="ft-input" value={milestoneLocation} onChange={(e) => setMilestoneLocation(e.target.value)} placeholder="地点：宁波港 / Hamburg / Vienna…" />
                  <input className="ft-input" value={milestoneNote} onChange={(e) => setMilestoneNote(e.target.value)} placeholder="节点备注" />
                  <button onClick={() => void addMilestone()} className="app-button-secondary rounded-lg px-4 text-[11px] font-medium">记录节点</button>
                </div>
                <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                  {[...(selected.milestones || [])].reverse().map((item, idx) => (
                    <div key={idx} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                      <div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-slate-700">{item.label || item.code}</span><span className="text-[9px] text-slate-400">{item.occurredAt ? new Date(item.occurredAt).toLocaleString("zh-CN", { hour12: false }) : "—"}</span></div>
                      <div className="mt-1 text-[10px] text-slate-500">{item.location || "—"}</div>
                      {item.note && <div className="mt-1 text-[10px] text-slate-400">{item.note}</div>}
                    </div>
                  ))}
                  {selected.milestones.length === 0 && <div className="text-[11px] text-slate-400">还没有时间线节点。</div>}
                </div>
              </>
            ) : <div className="text-[11px] text-slate-400">先保存出运单，再记录物流节点。</div>}

            <div className="mt-4">
              <Field label="备注"><textarea className="ft-input min-h-20" value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} /></Field>
            </div>
          </section>
        )}
      </div>

      <style jsx global>{`
        .ft-input{width:100%;height:34px;border:1px solid var(--app-border);border-radius:.5rem;background:var(--app-input);padding:.38rem .6rem;font-size:.75rem;line-height:1rem;color:var(--app-text);outline:none}
        .ft-input:focus{border-color:var(--app-accent);box-shadow:0 0 0 3px var(--app-focus)}
        textarea.ft-input{height:auto}
      `}</style>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1 block text-[10px] font-medium text-slate-500">{label}</span>{children}</label>;
}

function SectionTitle({ title, desc }: { title: string; desc: string }) {
  return <div className="mb-2 mt-5 flex flex-wrap items-baseline gap-x-3 border-t border-slate-100 pt-4 first:mt-0 first:border-0 first:pt-0"><h3 className="text-[12px] font-semibold text-slate-800">{title}</h3><p className="text-[10px] text-slate-400">{desc}</p></div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="app-card rounded-xl px-4 py-3"><div className="text-[10px] text-slate-400">{label}</div><div className="mt-1 text-lg font-semibold text-slate-900">{value}</div></div>;
}

function Calc({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return <div className="rounded-lg bg-slate-50 px-3 py-2"><div className="text-[9px] text-slate-400">{label}</div><div className={"mt-1 text-[12px] " + (strong ? "font-semibold text-slate-900" : "font-medium text-slate-700")}>{value}</div></div>;
}
