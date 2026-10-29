import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../../api/client";
import type { Action, Run, Tier } from "../../api/types";
import { BrazilTileMap } from "../../components/BrazilTileMap";
import { Badge, Card, ErrorBox, Kpi, Loading } from "../../components/ui";
import { ACTION_LABELS, TIERS, TIER_COLORS, fmtBRL, fmtInt, fmtNum } from "../../lib/format";

export default function Overview({ run }: { run: Run }) {
  const q = useQuery({ queryKey: ["summary", run.id], queryFn: () => api.summary(run.id) });
  const [mapMode, setMapMode] = useState<"dest" | "origin">("dest");
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const { stats, headline, narrative, recommendations, generator, number_check } = q.data;
  const s = stats.scope;
  const severityData = TIERS.map((t) => ({ tier: t, orders: stats.severity[t] ?? 0 }));
  const actionData = Object.entries(stats.actions as Record<Action, number>)
    .filter(([a]) => a !== "MONITOR")
    .map(([a, n]) => ({ action: ACTION_LABELS[a as Action] ?? a, orders: n }));
  const categoryData = (stats.top_categories as { category: string; orders: number }[]).slice(0, 8);
  const spec = run.spec;
  const highlight = spec ? [...spec.origin.states, ...spec.destination.states] : [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Orders delayed" value={fmtInt(s.orders_delayed)} hint={`of ${fmtInt(s.orders_in_scope)} in scope`} />
        <Kpi label="Will miss promise" value={fmtInt(s.orders_breaching)} tone="danger" hint={`${fmtInt(s.orders_newly_breaching)} only because of this event`} />
        <Kpi label="Customers affected" value={fmtInt(s.customers_delayed)} hint={`${fmtInt(stats.communications?.total)} emails drafted`} />
        <Kpi label="Order value delayed" value={fmtBRL(s.gmv_delayed_brl)} hint={`${fmtNum(s.billable_kg_delayed)} kg billable`} />
        <Kpi label="Sellers affected" value={fmtInt(s.sellers_delayed)} />
        <Kpi label="SKUs affected" value={fmtInt(s.skus_delayed)} hint={`${fmtInt(s.bulky_orders_delayed)} bulky orders`} />
        <Kpi label="Average hold" value={`${fmtNum(s.avg_hold_days, 1)} d`} hint="disruption window + recovery" tone="warn" />
        <Kpi label="Mitigation cost (est.)" value={fmtBRL(stats.cost.total_brl)} hint="refunds + vouchers + reroutes" />
      </div>

      <Card
        title={headline}
        actions={
          generator === "llm" ? (
            <Badge className="bg-emerald-50 text-emerald-800 ring-emerald-300" title="Every number in the narrative was checked against the computed statistics">
              LLM narrative · numbers verified
            </Badge>
          ) : (
            <Badge title={number_check?.problems?.join("; ") || "Deterministic template"}>Template narrative</Badge>
          )
        }
      >
        <p className="whitespace-pre-line text-sm leading-6 text-slate-700">{narrative}</p>
        {recommendations.length > 0 && (
          <>
            <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Recommended operational actions</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-800">
              {recommendations.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Delayed orders by severity">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={severityData} margin={{ left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="tier" tick={{ fontSize: 12 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="orders" radius={[4, 4, 0, 0]}>
                  {severityData.map((d) => (
                    <Cell key={d.tier} fill={TIER_COLORS[d.tier as Tier]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Mitigation decisions">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={actionData} layout="vertical" margin={{ left: 40 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="action" width={120} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="orders" fill="#256452" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card title="Most affected categories">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={categoryData} layout="vertical" margin={{ left: 40 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" allowDecimals={false} tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="category" width={140} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="orders" fill="#f5820b" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card
          title="Where the delays are"
          actions={
            <select value={mapMode} onChange={(e) => setMapMode(e.target.value as "dest" | "origin")} className="rounded-md border border-slate-300 px-2 py-1 text-xs">
              <option value="dest">Destination (orders)</option>
              <option value="origin">Origin (items)</option>
            </select>
          }
        >
          <BrazilTileMap
            values={mapMode === "dest" ? stats.dest_states : stats.origin_states}
            label={mapMode === "dest" ? "delayed orders (destination)" : "delayed items (origin)"}
            highlight={highlight}
          />
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Most exposed sellers">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr><th className="py-1">Seller</th><th>Location</th><th className="text-right">Orders</th><th className="text-right">Value</th></tr>
            </thead>
            <tbody>
              {(stats.top_sellers as any[]).map((t) => (
                <tr key={t.seller_id} className="border-t border-slate-100">
                  <td className="py-1.5 font-mono text-xs">{t.seller_id.slice(0, 8)}</td>
                  <td className="capitalize">{t.city}/{t.state}</td>
                  <td className="text-right tabular-nums">{fmtInt(t.orders)}</td>
                  <td className="text-right tabular-nums">{fmtBRL(t.value_brl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Heaviest delayed items">
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr><th className="py-1">SKU</th><th>Category</th><th>Profile</th><th className="text-right">kg billable</th></tr>
            </thead>
            <tbody>
              {(stats.heaviest_delayed_items as any[]).map((h) => (
                <tr key={`${h.order_id}-${h.product_id}`} className="border-t border-slate-100">
                  <td className="py-1.5 font-mono text-xs">{h.product_id.slice(0, 8)}</td>
                  <td>{h.category}</td>
                  <td>{h.profile}</td>
                  <td className="text-right tabular-nums">{fmtNum(h.billable_kg, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
