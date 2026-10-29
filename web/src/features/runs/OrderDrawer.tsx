import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect } from "react";
import { api } from "../../api/client";
import { ActionBadge, Badge, ErrorBox, Loading, TierBadge } from "../../components/ui";
import { SEVERITY_LABELS, fmtBRL, fmtDate, fmtNum, fmtPct, pretty } from "../../lib/format";

const KIND_STYLE: Record<string, string> = {
  disruption: "bg-red-600",
  match: "bg-orange-500",
  order: "bg-slate-700",
  sku: "bg-slate-500",
  eta: "bg-sky-600",
  severity: "bg-amber-500",
  agent: "bg-signal-500",
  decision: "bg-brand-600",
  communication: "bg-emerald-600",
};

export default function OrderDrawer({ runId, orderId, onClose }: { runId: string; orderId: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["order", runId, orderId], queryFn: () => api.order(runId, orderId) });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Order ${orderId}`}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-2xl overflow-y-auto bg-white shadow-2xl"
      >
        <header className="sticky top-0 flex items-center justify-between border-b border-slate-200 bg-white px-5 py-3">
          <div>
            <p className="text-xs text-slate-500">Order</p>
            <h2 className="font-mono text-sm font-semibold">{orderId}</h2>
          </div>
          <button onClick={onClose} className="rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100" aria-label="Close">✕</button>
        </header>
        <div className="space-y-6 p-5">
          {q.isLoading && <Loading />}
          <ErrorBox error={q.error} />
          {q.data && (() => {
            const { row, impact, decision, trace, policy_citations, communication } = q.data;
            return (
              <>
                <div className="flex flex-wrap gap-2">
                  <TierBadge tier={row.severity_tier} score={row.severity_score} />
                  <ActionBadge action={row.action} />
                  {decision.needs_review && <Badge className="bg-amber-50 text-amber-900 ring-amber-300">needs review</Badge>}
                  {decision.decided_by === "llm_adjudicator" && <Badge className="bg-brand-900 text-brand-50 ring-brand-900">LLM-adjudicated</Badge>}
                  {decision.decided_by === "llm_investigator" && (
                    <Badge className="bg-brand-900 text-brand-50 ring-brand-900" title="Decided by the tool-using investigator agent; its steps are in the reasoning trace">
                      Investigated by AI agent
                    </Badge>
                  )}
                  {decision.offer_cancellation && <Badge>cancellation offered</Badge>}
                  {decision.split_shipment && <Badge>split shipment</Badge>}
                </div>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Reasoning trace</h3>
                  <ol className="mt-2 space-y-3">
                    {trace.map((s, i) => (
                      <li key={i} className="flex gap-3">
                        <span className={clsx("mt-1 h-2.5 w-2.5 shrink-0 rounded-full", KIND_STYLE[s.kind] ?? "bg-slate-400")} />
                        <div>
                          <p className="text-sm font-medium text-slate-800">{s.title}</p>
                          <p className="text-xs text-slate-600">{s.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Severity breakdown</h3>
                  <div className="mt-2 space-y-1.5">
                    {impact.severity_components.map((c) => (
                      <div key={c.name} title={c.explanation}>
                        <div className="flex justify-between text-xs">
                          <span>{SEVERITY_LABELS[c.name] ?? pretty(c.name)}</span>
                          <span className="tabular-nums text-slate-600">{fmtNum(c.points, 1)} / {c.weight}</span>
                        </div>
                        <div className="h-1.5 rounded bg-slate-100">
                          <div className="h-1.5 rounded bg-brand-500" style={{ width: `${c.weight ? (100 * c.points) / c.weight : 0}%` }} />
                        </div>
                        <p className="text-[11px] text-slate-500">{c.explanation}</p>
                      </div>
                    ))}
                  </div>
                </section>

                <section>
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Items</h3>
                  <table className="mt-2 w-full text-xs">
                    <thead className="text-left text-slate-500">
                      <tr><th className="py-1">SKU</th><th>Category</th><th>Seller</th><th>Profile</th><th className="text-right">kg</th><th className="text-right">Hold</th><th>Plan</th></tr>
                    </thead>
                    <tbody>
                      {impact.lines.map((li) => {
                        const plan = decision.line_decisions.find((d) => d.order_item_id === li.order_item_id);
                        return (
                          <tr key={li.order_item_id} className="border-t border-slate-100">
                            <td className="py-1 font-mono">{li.product_id.slice(0, 8)}</td>
                            <td>{pretty(li.category_en)}</td>
                            <td className="capitalize">{li.seller_city}/{li.seller_state}</td>
                            <td>{li.handling_profile}</td>
                            <td className="text-right tabular-nums">{fmtNum(li.billable_weight_kg, 1)}</td>
                            <td className="text-right tabular-nums">{li.affected ? `${fmtNum(li.hold_days, 1)}d` : "-"}</td>
                            <td>{plan ? <ActionBadge action={plan.action} /> : <span className="text-slate-500">ships now</span>}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </section>

                <section className="grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs text-slate-500">Promised / projected / after mitigation</p>
                    <p className="mt-1">{fmtDate(row.sla_ts)} → {fmtDate(row.projected_eta)} → <strong>{fmtDate(decision.resolution_eta)}</strong></p>
                    <p className="text-xs text-slate-500">P(miss promise) {fmtPct(row.p_breach)} (baseline {fmtPct(impact.p_breach_baseline)})</p>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-3">
                    <p className="text-xs text-slate-500">Compensation ({decision.compensation.tier_label || "none"})</p>
                    <p className="mt-1">
                      Refund {fmtBRL(decision.compensation.refund_total_brl)} · Voucher {fmtBRL(decision.compensation.voucher_brl)}
                    </p>
                    <p className="text-xs text-slate-500">Estimated cost {fmtBRL(decision.est_cost_brl)}{decision.compensation.human_callback ? " · human callback" : ""}</p>
                  </div>
                </section>

                {decision.llm_rationale && (
                  <section className="rounded-lg border border-brand-200 bg-brand-50 p-3 text-sm text-brand-900">
                    <p className="text-xs font-semibold uppercase tracking-wide">LLM adjudicator rationale</p>
                    <p className="mt-1">{decision.llm_rationale}</p>
                  </section>
                )}

                {decision.alternatives.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Alternatives considered</h3>
                    <ul className="mt-2 space-y-1 text-xs">
                      {decision.alternatives.map((a, i) => (
                        <li key={i} className={a.accepted ? "text-emerald-700" : "text-slate-600"}>
                          {a.accepted ? "✓" : "✗"} {a.kind === "alt_seller" ? `Seller ${a.seller_id?.slice(0, 8)} (${a.seller_city ?? ""}/${a.seller_state ?? ""})` : "Alternate route"}: {a.reason}
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

                {policy_citations.length > 0 && (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Policy citations</h3>
                    <div className="mt-2 space-y-1.5">
                      {policy_citations.map((c) => (
                        <details key={c.id} className="rounded-md border border-slate-200 p-2 text-xs">
                          <summary className="cursor-pointer font-medium">{c.title}</summary>
                          <p className="mt-1 whitespace-pre-line text-slate-600">{c.text}</p>
                        </details>
                      ))}
                    </div>
                  </section>
                )}

                {communication && (
                  <section>
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Customer email ({communication.generator}, {communication.status})</h3>
                    <p className="mt-2 text-sm font-medium">{communication.subject}</p>
                    <pre className="mt-1 whitespace-pre-wrap rounded-md bg-slate-50 p-3 font-sans text-xs text-slate-700">{communication.body}</pre>
                  </section>
                )}
              </>
            );
          })()}
        </div>
      </aside>
    </div>
  );
}
