import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Badge, Card, Empty, ErrorBox, Loading } from "../../components/ui";

export default function EvidenceTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["summary", runId], queryFn: () => api.summary(runId) });
  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorBox error={q.error} />;
  const r = q.data.retrieval;
  return (
    <div className="space-y-4">
      <Card title="How retrieval is used">
        <p className="text-sm text-slate-700">
          Scope is decided by <strong>exact structured filters</strong> (states, cities, radius, stage), because a vector search
          cannot guarantee recall on geography. Retrieval handles what is fuzzy: product terms are resolved to categories with
          lexical, keyword and semantic matching, and <strong>hybrid search</strong> (pgvector dense + Postgres full-text, fused
          with Reciprocal Rank Fusion) ranks impacted orders, heavy or bulky SKUs, exposed sellers and the policy sections
          the agents cite. Mode for this run: <Badge>{r.mode ?? "n/a"}</Badge>
        </p>
      </Card>

      {r.scope_check && r.scope_check.recall != null && (
        <Card title="Retrieve-then-verify: could semantic search alone find the impacted orders?">
          <p className="text-sm text-slate-700">
            Hybrid search over all in-flight orders, using the scenario text, proposed its top <strong>{r.scope_check.n}</strong>{" "}
            orders. Checked against the exact scope, it recovered <strong>{Math.round(r.scope_check.recall * 100)}%</strong>{" "}
            of the impacted orders (precision {Math.round((r.scope_check.precision ?? 0) * 100)}%). This is why the scope
            is decided by exact structured filters, while retrieval resolves concepts, ranks evidence and cites policy.
          </p>
        </Card>
      )}

      <Card title="Product terms → catalog">
        {r.concepts && r.concepts.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {r.concepts.map((c) => (
              <li key={c.term}>
                <span className="font-medium">“{c.term}”</span>
                <span className="ml-2 inline-flex flex-wrap gap-1">
                  {c.matches.length === 0 && <span className="text-xs text-amber-700">no match</span>}
                  {c.matches.map((m) => (
                    <Badge key={`${m.kind}-${m.value}`} title={`${m.kind} via ${m.via}, score ${m.score}`}>
                      {m.label} · {m.via}
                    </Badge>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No product terms in this scenario: all products in scope are considered.</Empty>
        )}
      </Card>

      {(r.queries ?? []).map((qq) => (
        <Card key={qq.purpose} title={qq.purpose} actions={<Badge>{qq.restricted_to_scope ? "within impacted scope" : "knowledge base"}</Badge>}>
          <p className="mb-2 text-xs text-slate-500">Query: “{qq.query}”</p>
          {qq.hits.length === 0 ? (
            <Empty>No hits.</Empty>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-slate-500">
                <tr><th className="py-1">Document</th><th className="text-right">Rerank</th><th className="text-right">RRF score</th><th className="text-right">Dense rank</th><th className="text-right">Sparse rank</th></tr>
              </thead>
              <tbody>
                {qq.hits.map((h) => (
                  <tr key={h.ref_id} className="border-t border-slate-100 align-top">
                    <td className="py-1.5">
                      <p className="font-medium">{h.title}</p>
                      <p className="line-clamp-2 text-xs text-slate-500">{h.snippet}</p>
                    </td>
                    <td className="text-right tabular-nums">{h.rerank_score != null ? h.rerank_score.toFixed(2) : "-"}</td>
                    <td className="text-right tabular-nums">{h.score.toFixed(4)}</td>
                    <td className="text-right tabular-nums">{h.dense_rank ?? "-"}</td>
                    <td className="text-right tabular-nums">{h.sparse_rank ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      ))}
    </div>
  );
}
