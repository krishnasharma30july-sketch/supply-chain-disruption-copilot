import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Card, ErrorBox, Loading } from "../../components/ui";

export default function DetailsTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["run", runId], queryFn: () => api.run(runId) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const run = q.data;
  const usage = run.metrics?.llm_usage ?? {};
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="LLM usage">
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt className="text-slate-500">Calls</dt><dd className="tabular-nums">{usage.calls ?? 0} ({usage.cached ?? 0} from cache)</dd>
          <dt className="text-slate-500">Errors</dt><dd className="tabular-nums">{usage.errors ?? 0}</dd>
          <dt className="text-slate-500">Input / output tokens</dt><dd className="tabular-nums">{usage.input_tokens ?? 0} / {usage.output_tokens ?? 0}</dd>
          <dt className="text-slate-500">Degraded</dt><dd>{run.metrics?.llm_degraded ?? "no"}</dd>
        </dl>
      </Card>
      <Card title="Stage durations (ms)">
        <pre className="text-xs">{JSON.stringify(run.metrics?.stage_ms ?? {}, null, 2)}</pre>
      </Card>
      <Card title="Interpreted spec (as executed)" className="lg:col-span-2">
        <pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(run.spec, null, 2)}</pre>
      </Card>
      <Card title="Configuration snapshot (policy, scoring, assumptions, prompts, models)" className="lg:col-span-2">
        <pre className="max-h-96 overflow-auto text-xs">{JSON.stringify(run.config_snapshot, null, 2)}</pre>
      </Card>
    </div>
  );
}
