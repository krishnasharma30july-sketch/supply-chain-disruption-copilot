import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import { Badge, Button, Card, Empty, ErrorBox, Loading } from "../../components/ui";

const TYPES = ["order", "product", "seller", "category", "policy"] as const;
type Mode = "hybrid" | "dense" | "sparse";

export default function ExplorerPage() {
  const [input, setInput] = useState("heavy bulky furniture shipped from Sao Paulo");
  const [submitted, setSubmitted] = useState<Record<string, unknown> | null>(null);
  const [mode, setMode] = useState<Mode>("hybrid");
  const [types, setTypes] = useState<string[]>([]);
  const [origin, setOrigin] = useState("");
  const [dest, setDest] = useState("");
  const [rerank, setRerank] = useState(true);

  const q = useQuery({
    queryKey: ["search", submitted],
    queryFn: () => api.search(submitted!),
    enabled: !!submitted,
  });

  const search = () =>
    setSubmitted({ q: input, mode, rerank, doc_type: types, origin_uf: origin || undefined, dest_uf: dest || undefined, limit: 20 });

  return (
    <div className="space-y-4">
      <div>
        <p className="label text-signal-700">Knowledge index</p>
        <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900">Retrieval explorer</h1>
        <p className="mt-1 text-sm text-slate-600">
          Inspect the hybrid index the agents use: about 10k orders, their products and sellers, the 73 catalog categories and the
          policy knowledge base. Compare dense, sparse and hybrid (RRF) rankings.
        </p>
      </div>
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && input.trim().length >= 2 && search()}
            className="min-w-[280px] flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
            placeholder="Search orders, products, sellers, categories, policy..."
          />
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} className="rounded-md border border-slate-300 px-2 py-2 text-sm">
            <option value="hybrid">Hybrid (RRF)</option>
            <option value="dense">Dense only</option>
            <option value="sparse">Sparse only</option>
          </select>
          <input value={origin} onChange={(e) => setOrigin(e.target.value.toUpperCase().slice(0, 2))} placeholder="Origin UF" className="w-24 rounded-md border border-slate-300 px-2 py-2 text-sm" />
          <input value={dest} onChange={(e) => setDest(e.target.value.toUpperCase().slice(0, 2))} placeholder="Dest UF" className="w-24 rounded-md border border-slate-300 px-2 py-2 text-sm" />
          <Button onClick={search} disabled={input.trim().length < 2}>Search</Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-3 text-sm">
          {TYPES.map((t) => (
            <label key={t} className="flex items-center gap-1.5 capitalize">
              <input type="checkbox" checked={types.includes(t)} onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))} />
              {t}
            </label>
          ))}
          <span className="text-xs text-slate-500">(none checked = all types; UF filters apply to orders)</span>
          <label className="ml-auto flex items-center gap-1.5" title="Re-score the top hybrid candidates with a cross-encoder">
            <input type="checkbox" checked={rerank} disabled={mode !== "hybrid"} onChange={(e) => setRerank(e.target.checked)} />
            Cross-encoder rerank
          </label>
        </div>
      </Card>

      {q.isFetching && <Loading label="Searching..." />}
      <ErrorBox error={q.error} />
      {q.data && (
        <Card title={`${q.data.hits.length} results`} actions={<Badge>effective mode: {q.data.effective_mode}</Badge>}>
          {q.data.hits.length === 0 ? (
            <Empty>No results.</Empty>
          ) : (
            <ol className="space-y-3">
              {q.data.hits.map((h, i) => (
                <li key={h.doc_id} className="border-b border-slate-100 pb-3 last:border-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-slate-400">#{i + 1}</span>
                    <Badge>{h.doc_type}</Badge>
                    <span className="font-medium">{h.title}</span>
                    <span className="ml-auto text-xs tabular-nums text-slate-500">
                      {h.rerank_score != null && <>rerank {h.rerank_score.toFixed(2)} · </>}
                      RRF {h.score.toFixed(4)} · dense #{h.dense_rank ?? "-"} · sparse #{h.sparse_rank ?? "-"}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-line text-xs text-slate-600">{h.snippet}</p>
                </li>
              ))}
            </ol>
          )}
        </Card>
      )}
    </div>
  );
}
