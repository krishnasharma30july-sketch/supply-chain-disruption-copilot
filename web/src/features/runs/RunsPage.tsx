import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { RunStatus } from "../../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Loading } from "../../components/ui";
import { fmtInt, fmtTimestamp } from "../../lib/format";

const PAGE = 20;
const STATUS_STYLE: Record<RunStatus, string> = {
  queued: "bg-slate-100 text-slate-700 ring-slate-300",
  running: "bg-signal-50 text-signal-800 ring-signal-300",
  succeeded: "bg-brand-50 text-brand-800 ring-brand-300",
  failed: "bg-red-50 text-red-800 ring-red-300",
  canceled: "bg-slate-100 text-slate-500 ring-slate-300",
};

export default function RunsPage() {
  const [offset, setOffset] = useState(0);
  const q = useQuery({ queryKey: ["runs", offset], queryFn: () => api.runs(PAGE, offset), refetchInterval: 5000 });
  return (
    <div className="space-y-5">
      <div>
        <p className="label text-signal-700">History</p>
        <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900">Runs</h1>
        <p className="mt-1 text-sm text-slate-600">Every analysis is kept with its interpretation, configuration snapshot, decisions and emails.</p>
      </div>
      <Card title={q.data ? `${fmtInt(q.data.total)} runs` : "Runs"}>
        {q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : !q.data || q.data.items.length === 0 ? (
          <Empty>
            No runs yet. <Link to="/" className="text-brand-700 underline">Start with a scenario</Link>.
          </Empty>
        ) : (
          <>
            <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left">
                <tr className="[&>th]:label [&>th]:pb-2"><th>Scenario</th><th>Status</th><th>LLM</th><th className="text-right">Delayed</th><th className="text-right">Missing promise</th><th className="pl-4">Created</th></tr>
              </thead>
              <tbody>
                {q.data.items.map((r) => (
                  <tr key={r.id} className="border-t border-slate-100 hover:bg-slate-50/70">
                    <td className="py-2.5 pr-3">
                      <Link to={`/runs/${r.id}`} className="font-medium text-brand-800 hover:text-brand-950 hover:underline">{r.title}</Link>
                      {r.raw_query && <p className="max-w-md truncate text-xs text-slate-500">{r.raw_query}</p>}
                    </td>
                    <td><Badge className={STATUS_STYLE[r.status]}>{r.status}</Badge></td>
                    <td className="text-xs text-slate-600">{r.llm_mode}</td>
                    <td className="figure text-right">{fmtInt(r.metrics?.orders_delayed)}</td>
                    <td className="figure text-right">{fmtInt(r.metrics?.orders_breaching)}</td>
                    <td className="pl-4 text-xs text-slate-600">{fmtTimestamp(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button variant="secondary" disabled={offset + PAGE >= q.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
