import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Badge, Card, ErrorBox, Loading } from "../../components/ui";
import { fmtDate, fmtInt } from "../../lib/format";

const PIPELINE = [
  ["Intake", "LLM structured output → validated spec; gazetteer grounding; rule-parser fallback", "LLM + code"],
  ["Scout", "Exact scope filters (states, cities, radius, lanes, stage) + hybrid retrieval evidence", "Code + RAG"],
  ["Logistics Analyst", "Per-leg holds, empirical lane ETA (p50/p90), P(breach), severity 0–100", "Code"],
  ["Mitigation", "Policy-as-code rules R0–R8, alternate sellers/routes, compensation; LLM adjudicates flagged cases within allowed options", "Code + bounded LLM"],
  ["Customer Success", "Grounded fact sheets → LLM emails for the highest-severity customers, templates for the rest", "LLM + templates"],
  ["QA Critic", "Every date, amount and number checked against the facts; policy, tone and jargon checks; redraft loop", "Code"],
  ["Reporter", "Deterministic stats & recommendations; LLM narrative with number verification", "Code + LLM"],
];

export default function AboutPage() {
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const assumptions = useQuery({ queryKey: ["assumptions"], queryFn: api.assumptions, staleTime: Infinity });
  return (
    <div className="space-y-4">
      <div>
        <p className="label text-signal-700">System design</p>
        <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900">How it works</h1>
      </div>
      <Card title="Agent pipeline (supervisor-orchestrated, deterministic where correctness matters)">
        <ol className="grid gap-2 md:grid-cols-2">
          {PIPELINE.map(([name, desc, kind], i) => (
            <li key={name} className="rounded-lg border border-slate-200 p-3">
              <p className="text-sm font-semibold">{i + 1}. {name} <Badge className="ml-1">{kind}</Badge></p>
              <p className="mt-1 text-xs text-slate-600">{desc}</p>
            </li>
          ))}
        </ol>
      </Card>

      <Card title="Dataset & configuration">
        {meta.isLoading ? <Loading /> : meta.error ? <ErrorBox error={meta.error} /> : meta.data && (
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-slate-500">Snapshot as of</dt><dd>{fmtDate(meta.data.dataset?.as_of)}</dd></div>
            <div><dt className="text-xs text-slate-500">Orders indexed / actionable</dt><dd>{fmtInt(meta.data.dataset?.counts?.orders)} / {fmtInt(meta.data.dataset?.counts?.actionable_orders)}</dd></div>
            <div><dt className="text-xs text-slate-500">Retrieval documents</dt><dd>{fmtInt(meta.data.dataset?.counts?.documents)}</dd></div>
            <div><dt className="text-xs text-slate-500">Embedding model</dt><dd className="break-all">{meta.data.embed_model}</dd></div>
            <div><dt className="text-xs text-slate-500">LLM</dt><dd>{meta.data.llm.available ? `${meta.data.llm.provider}: ${Object.values(meta.data.llm.models).join(", ")}` : meta.data.llm.mode}</dd></div>
            <div><dt className="text-xs text-slate-500">Versions</dt><dd className="text-xs">{["policy", "scoring", "assumptions", "category_classes"].map((k) => `${meta.data!.versions[k]}`).join(" · ")}</dd></div>
          </dl>
        )}
      </Card>

      <Card title="Assumptions (dataset limitations → design decisions)">
        {assumptions.isLoading ? <Loading /> : assumptions.error ? <ErrorBox error={assumptions.error} /> : (
          <ul className="space-y-3">
            {assumptions.data?.items.map((a) => (
              <li key={a.id} className="text-sm">
                <p className="font-medium"><span className="mr-1 font-mono text-xs text-brand-700">{a.id}</span>{a.title}</p>
                <p className="text-slate-700">{a.statement}</p>
                <p className="text-xs text-slate-500">Impact: {a.impact}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
