import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { RunStatus } from "../../api/types";
import { useRunEvents } from "../../api/useRunEvents";
import { Badge, Button, Card, ErrorBox, Loading, Tabs } from "../../components/ui";
import { fmtTimestamp } from "../../lib/format";
import CommsTab from "./CommsTab";
import DetailsTab from "./DetailsTab";
import EvidenceTab from "./EvidenceTab";
import OrdersTab from "./OrdersTab";
import Overview from "./Overview";
import Timeline from "./Timeline";

const TERMINAL: RunStatus[] = ["succeeded", "failed", "canceled"];
const STATUS_STYLE: Record<RunStatus, string> = {
  queued: "bg-slate-100 text-slate-700 ring-slate-300",
  running: "bg-brand-50 text-brand-800 ring-brand-300",
  succeeded: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  failed: "bg-red-50 text-red-800 ring-red-300",
  canceled: "bg-amber-50 text-amber-900 ring-amber-300",
};

type Tab = "overview" | "orders" | "comms" | "evidence" | "details";

export default function RunPage() {
  const { runId = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const runQ = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    refetchInterval: (q) => (q.state.data && TERMINAL.includes(q.state.data.status) ? false : 3000),
  });
  const { events } = useRunEvents(runId, runQ.data?.started_at ?? null);
  const cancel = useMutation({ mutationFn: () => api.cancelRun(runId), onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }) });
  const retry = useMutation({ mutationFn: () => api.retryRun(runId), onSuccess: () => qc.invalidateQueries({ queryKey: ["run", runId] }) });

  if (runQ.isLoading) return <Loading label="Loading run..." />;
  if (runQ.error || !runQ.data) return <ErrorBox error={runQ.error} title="Could not load the run" />;
  const run = runQ.data;
  const done = run.status === "succeeded";
  const metrics = run.metrics || {};

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs text-slate-500">
            <Link to="/runs" className="hover:underline">Runs</Link> / {run.id.slice(0, 8)}
          </p>
          <h1 className="mt-1 text-xl font-semibold tracking-tight">{run.title}</h1>
          {run.raw_query && <p className="mt-1 text-sm text-slate-600">“{run.raw_query}”</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge className={STATUS_STYLE[run.status]}>{run.status}</Badge>
            <Badge title="LLM mode for this run">LLM {run.llm_mode}</Badge>
            {run.spec && <Badge>interpretation: {run.spec.source}</Badge>}
            <Badge>created {fmtTimestamp(run.created_at)}</Badge>
            {metrics.duration_ms !== undefined && <Badge>took {(metrics.duration_ms / 1000).toFixed(1)}s</Badge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {(run.status === "queued" || run.status === "running") && (
            <Button variant="secondary" onClick={() => cancel.mutate()} loading={cancel.isPending}>Cancel</Button>
          )}
          {(run.status === "failed" || run.status === "canceled") && (
            <Button variant="secondary" onClick={() => retry.mutate()} loading={retry.isPending}>Retry</Button>
          )}
          {done && (
            <>
              <a className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50" href={api.exportUrl(run.id, "md")}>Report (.md)</a>
              <a className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50" href={api.exportUrl(run.id, "csv")}>Orders (.csv)</a>
              <a className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50" href={api.exportUrl(run.id, "json")}>Full (.json)</a>
            </>
          )}
        </div>
      </div>
      <ErrorBox error={cancel.error || retry.error} />
      {run.error && <ErrorBox error={{ title: `Failed during ${run.error.stage}`, detail: run.error.message }} />}

      <div className={done ? "grid gap-6 xl:grid-cols-4" : ""}>
        <Card title="Agent timeline" className={done ? "xl:col-span-1 xl:self-start" : ""}>
          <Timeline events={events} status={run.status} />
        </Card>
        {done && (
          <div className="space-y-4 xl:col-span-3">
            <Tabs<Tab>
              value={tab}
              onChange={setTab}
              tabs={[
                { id: "overview", label: "Impact summary" },
                { id: "orders", label: "Orders & decisions" },
                { id: "comms", label: "Customer emails" },
                { id: "evidence", label: "Retrieval evidence" },
                { id: "details", label: "Run details" },
              ]}
            />
            {tab === "overview" && <Overview run={run} />}
            {tab === "orders" && <OrdersTab runId={run.id} />}
            {tab === "comms" && <CommsTab runId={run.id} />}
            {tab === "evidence" && <EvidenceTab runId={run.id} />}
            {tab === "details" && <DetailsTab runId={run.id} />}
          </div>
        )}
      </div>
    </div>
  );
}
