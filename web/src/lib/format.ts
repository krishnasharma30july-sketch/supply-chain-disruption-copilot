import type { Action, Tier } from "../api/types";

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const num = new Intl.NumberFormat("en-US");

export const fmtBRL = (v: number | null | undefined) => (v == null ? "-" : brl.format(v));
export const fmtNum = (v: number | null | undefined, digits = 0) =>
  v == null ? "-" : v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const fmtInt = (v: number | null | undefined) => (v == null ? "-" : num.format(Math.round(v)));
export const fmtPct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);

// Backend datetimes are naive local times (Brazil); parse without a timezone shift.
function parse(iso: string): Date {
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return new Date(iso);
  return new Date(+m[1], +m[2] - 1, +m[3], m[4] ? +m[4] : 0, m[5] ? +m[5] : 0);
}

export const fmtDate = (iso: string | null | undefined) =>
  iso ? parse(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "-";

export const fmtDateTime = (iso: string | null | undefined) =>
  iso
    ? parse(iso).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" })
    : "-";

export const fmtTimestamp = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "-");

export const TIER_STYLES: Record<Tier, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-300 text-amber-950",
  low: "bg-slate-200 text-slate-700",
};
export const TIER_COLORS: Record<Tier, string> = {
  critical: "#dc2626",
  high: "#f97316",
  medium: "#fbbf24",
  low: "#a39d8f",
};
export const TIERS: Tier[] = ["critical", "high", "medium", "low"];

export const ACTION_LABELS: Record<Action, string> = {
  MONITOR: "Monitor",
  NOTIFY_ONLY: "Notify only",
  CANCEL_REFUND: "Cancel & refund",
  REROUTE_ALT_SELLER: "Reroute: alt seller",
  REROUTE_ALT_ROUTE: "Reroute: alt route",
  DELAY_WITH_COMPENSATION: "Delay + compensate",
};
export const ACTION_STYLES: Record<Action, string> = {
  MONITOR: "bg-slate-100 text-slate-700 ring-slate-300",
  NOTIFY_ONLY: "bg-sky-50 text-sky-800 ring-sky-300",
  CANCEL_REFUND: "bg-red-50 text-red-800 ring-red-300",
  REROUTE_ALT_SELLER: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  REROUTE_ALT_ROUTE: "bg-teal-50 text-teal-800 ring-teal-300",
  DELAY_WITH_COMPENSATION: "bg-amber-50 text-amber-900 ring-amber-300",
};

export const STAGE_LABELS: Record<string, string> = {
  pending_approval: "Pending approval",
  awaiting_pickup: "Awaiting pickup",
  in_transit: "In transit",
  delivered: "Delivered",
};

export const LEG_LABELS: Record<string, string> = {
  first_mile: "First mile (pickup at origin)",
  linehaul: "Linehaul (inter-state)",
  last_mile: "Last mile (delivery)",
};

export const SEVERITY_LABELS: Record<string, string> = {
  sla_breach: "SLA breach (days late)",
  breach_probability: "Breach probability",
  freight_weight: "Freight weight",
  order_value: "Order value",
  seller_reliability: "Seller reliability",
  complexity: "Order complexity",
  not_delayed: "Not delayed",
};

export const pretty = (s: string) => s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
