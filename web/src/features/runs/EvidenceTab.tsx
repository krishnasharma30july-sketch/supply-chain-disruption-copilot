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
          with Reciprocal Rank Fusion, then re-ranked) ranks impacted orders, heavy or bulky SKUs, exposed sellers and policy
          sections. Retrieved policy text also reaches the AI: the investigator&apos;s policy tool and the adjudicator&apos;s context
          are filled by this search, and the summary may cite the sections it was offered. Search never decides who is impacted
          or what they are owed. Mode for this run: <Badge>{r.mode ?? "n/a"}</Badge>
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

      {r.scope_check?.near_misses && r.scope_check.near_misses.length > 0 && (
        <Card
          title="Similar orders outside the exact scope (for review)"
          actions={<Badge>{r.scope_check.near_miss_total} found by search</Badge>}
        >
          <p className="mb-2 text-xs text-slate-500">
            Search ranked these orders as similar to the scenario, but the structured filters did not select them. They are shown so
            an analyst can spot a gap in the interpretation (for example a missing region). They are never added to the scope
            automatically.
          </p>
          <table className="w-full text-sm" data-testid="near-misses">
            <thead className="text-left text-xs uppercase text-slate-500">
              <tr><th className="py-1">Order</th><th>Why the filters left it out</th><th className="text-right">Search rank</th></tr>
            </thead>
            <tbody>
              {r.scope_check.near_misses.map((m) => (
                <tr key={m.order_ref} className="border-t border-slate-100 align-top">
                  <td className="py-1.5">
                    <p className="font-medium">{m.title}</p>
                    <p className="line-clamp-1 text-xs text-slate-500">{m.snippet}</p>
                  </td>
                  <td className="pr-3 text-slate-700">{m.reason}</td>
                  <td className="text-right tabular-nums">{m.rank}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
                      {h.exposure && (
                        <p className="text-xs font-medium text-amber-700">
                          {h.exposure.orders} affected order{h.exposure.orders === 1 ? "" : "s"} · R$ {h.exposure.value_brl.toLocaleString("en-US", { minimumFractionDigits: 2 })} · ranked by exposure and search relevance
                        </p>
                      )}
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
