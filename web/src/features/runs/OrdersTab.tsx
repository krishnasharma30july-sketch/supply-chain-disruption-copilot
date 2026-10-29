import { keepPreviousData, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { api } from "../../api/client";
import type { Action, Tier } from "../../api/types";
import { ActionBadge, Button, Card, Empty, ErrorBox, Loading, TierBadge } from "../../components/ui";
import { ACTION_LABELS, STAGE_LABELS, TIERS, fmtBRL, fmtDate, fmtNum, fmtPct } from "../../lib/format";
import OrderDrawer from "./OrderDrawer";

const PAGE = 50;

export default function OrdersTab({ runId }: { runId: string }) {
  const [tiers, setTiers] = useState<Tier[]>([]);
  const [action, setAction] = useState<Action | "">("");
  const [stage, setStage] = useState("");
  const [review, setReview] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"severity" | "days_late" | "value" | "weight">("severity");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);

  const params = {
    severity: tiers,
    action: action || undefined,
    stage: stage || undefined,
    needs_review: review ? true : undefined,
    affected: showAll ? undefined : true,
    q: q.trim() || undefined,
    sort,
    limit: PAGE,
    offset,
  };
  const ordersQ = useQuery({
    queryKey: ["orders", runId, params],
    queryFn: () => api.orders(runId, params),
    placeholderData: keepPreviousData,
  });
  const reset = () => setOffset(0);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <div className="flex gap-1" role="group" aria-label="Severity filter">
          {TIERS.map((t) => (
            <button
              key={t}
              onClick={() => {
                setTiers(tiers.includes(t) ? tiers.filter((x) => x !== t) : [...tiers, t]);
                reset();
              }}
              className={clsx(
                "rounded-full px-2.5 py-1 text-xs font-medium capitalize ring-1",
                tiers.includes(t) ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-700 ring-slate-300",
              )}
            >
              {t}
            </button>
          ))}
        </div>
        <select value={action} onChange={(e) => { setAction(e.target.value as Action | ""); reset(); }} className="rounded-md border border-slate-300 px-2 py-1">
          <option value="">All actions</option>
          {Object.entries(ACTION_LABELS).map(([a, l]) => (
            <option key={a} value={a}>{l}</option>
          ))}
        </select>
        <select value={stage} onChange={(e) => { setStage(e.target.value); reset(); }} className="rounded-md border border-slate-300 px-2 py-1">
          <option value="">All stages</option>
          {Object.entries(STAGE_LABELS).filter(([k]) => k !== "delivered").map(([k, l]) => (
            <option key={k} value={k}>{l}</option>
          ))}
        </select>
        <select value={sort} onChange={(e) => { setSort(e.target.value as typeof sort); reset(); }} className="rounded-md border border-slate-300 px-2 py-1">
          <option value="severity">Sort: severity</option>
          <option value="days_late">Sort: days late</option>
          <option value="value">Sort: value</option>
          <option value="weight">Sort: weight</option>
        </select>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={review} onChange={(e) => { setReview(e.target.checked); reset(); }} /> Needs review
        </label>
        <label className="flex items-center gap-1.5" title="Include orders in the geography whose timeline is not affected">
          <input type="checkbox" checked={showAll} onChange={(e) => { setShowAll(e.target.checked); reset(); }} /> Include not delayed
        </label>
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); reset(); }}
          placeholder="Order / customer id prefix"
          className="ml-auto w-52 rounded-md border border-slate-300 px-2 py-1"
        />
      </div>

      <div className="mt-3">
        {ordersQ.isLoading ? (
          <Loading />
        ) : ordersQ.error ? (
          <ErrorBox error={ordersQ.error} />
        ) : ordersQ.data && ordersQ.data.items.length === 0 ? (
          <Empty>No orders match these filters.</Empty>
        ) : (
          ordersQ.data && (
            <>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-sm">
                  <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
                    <tr>
                      <th className="py-2">Order</th>
                      <th>Stage</th>
                      <th>Lane</th>
                      <th>Category</th>
                      <th className="text-right">kg</th>
                      <th>Promised</th>
                      <th>New ETA</th>
                      <th className="text-right">Late</th>
                      <th className="text-right">P(miss)</th>
                      <th>Severity</th>
                      <th>Action</th>
                      <th className="text-right">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ordersQ.data.items.map((o) => (
                      <tr
                        key={o.order_id}
                        onClick={() => setSelected(o.order_id)}
                        className="cursor-pointer border-t border-slate-100 hover:bg-brand-50/50"
                      >
                        <td className="py-2 font-mono text-xs">
                          {o.order_id.slice(0, 8).toUpperCase()}
                          {o.needs_review && <span className="ml-1 text-amber-600" title="Flagged for review">●</span>}
                        </td>
                        <td className="text-xs">{STAGE_LABELS[o.stage] ?? o.stage}</td>
                        <td className="text-xs">{o.origin_states.join(",")}→{o.dest_state}</td>
                        <td className="max-w-[160px] truncate text-xs" title={o.categories.join(", ")}>{o.categories.join(", ").replace(/_/g, " ")}</td>
                        <td className="text-right tabular-nums">{fmtNum(o.billable_weight_kg, 1)}</td>
                        <td className="text-xs">{fmtDate(o.sla_ts)}</td>
                        <td className="text-xs">{fmtDate(o.resolution_eta)}</td>
                        <td className={clsx("text-right tabular-nums", o.resolution_days_late > 0 && "font-semibold text-red-600")}>
                          {o.resolution_days_late > 0 ? `${o.resolution_days_late}d` : "on time"}
                        </td>
                        <td className="text-right tabular-nums">{fmtPct(o.p_breach)}</td>
                        <td><TierBadge tier={o.severity_tier} score={o.severity_score} /></td>
                        <td><ActionBadge action={o.action} /></td>
                        <td className="text-right tabular-nums">{fmtBRL(o.value_brl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex items-center justify-between text-xs text-slate-600">
                <span>
                  {offset + 1}–{Math.min(offset + PAGE, ordersQ.data.total)} of {ordersQ.data.total} orders · "Late" is after mitigation
                </span>
                <div className="flex gap-2">
                  <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                  <Button variant="secondary" disabled={offset + PAGE >= ordersQ.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
                </div>
              </div>
            </>
          )
        )}
      </div>
      {selected && <OrderDrawer runId={runId} orderId={selected} onClose={() => setSelected(null)} />}
    </Card>
  );
}
