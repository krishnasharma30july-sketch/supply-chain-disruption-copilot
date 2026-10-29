import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { DisruptionSpec, ParseResponse, PreviewCounts, RunOptions } from "../../api/types";
import { Button, Card, ErrorBox, Notice } from "../../components/ui";
import InterpretationCard from "./InterpretationCard";
import PreviewPanel from "./PreviewPanel";

const EXAMPLES = [
  "Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days",
  "Last-mile courier strike in Rio de Janeiro for 3 days",
  "Supplier shortage delaying furniture and other bulky home goods for 10 days",
  "Flooding within 80 km of Porto Alegre for one week",
  "Highway closure blocking freight from SP to the Northeast for 4 days",
];

export default function ScenarioPage() {
  const navigate = useNavigate();
  const meta = useQuery({ queryKey: ["meta"], queryFn: api.meta });
  const [query, setQuery] = useState(EXAMPLES[0]);
  const [options, setOptions] = useState<RunOptions>({ locale: "en", max_llm_emails: null, date_rebase: false, use_llm: true });
  const [parsed, setParsed] = useState<ParseResponse | null>(null);
  const [spec, setSpec] = useState<DisruptionSpec | null>(null);
  const [preview, setPreview] = useState<PreviewCounts | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [previewing, setPreviewing] = useState(false);
  const lastPreviewed = useRef<string>("");

  const llmAvailable = meta.data?.llm.available ?? false;

  // Editing the text invalidates the interpretation: never run an interpretation of different text.
  const changeQuery = (value: string) => {
    setQuery(value);
    if (parsed) {
      setParsed(null);
      setSpec(null);
      setPreview(null);
      setPreviewError(null);
      lastPreviewed.current = "";
    }
  };

  const parse = useMutation({
    mutationFn: () => api.parse(query, options.use_llm && llmAvailable),
    onSuccess: (r) => {
      setParsed(r);
      setSpec(r.spec);
      setPreview(r.preview);
      setPreviewError(null);
      lastPreviewed.current = JSON.stringify(r.spec);
    },
  });

  const createRun = useMutation({
    mutationFn: () => api.createRun({ query, spec: spec ?? undefined, options }),
    onSuccess: (run) => navigate(`/runs/${run.id}`),
  });

  // Live preview of the edited interpretation (debounced).
  const specKey = useMemo(() => (spec ? JSON.stringify(spec) : ""), [spec]);
  const currentKey = useRef("");
  currentKey.current = specKey;
  useEffect(() => {
    if (!spec || specKey === lastPreviewed.current) return;
    const sentKey = specKey;
    const timer = window.setTimeout(async () => {
      setPreviewing(true);
      try {
        const res = await api.preview(spec);
        if (currentKey.current !== sentKey) return; // a newer edit superseded this request
        const normalized = JSON.stringify(res.spec);
        lastPreviewed.current = normalized;
        setPreview(res.preview);
        setPreviewError(null);
        if (normalized !== sentKey) setSpec(res.spec);
      } catch (e) {
        if (currentKey.current !== sentKey) return;
        lastPreviewed.current = sentKey;
        setPreviewError(e);
      } finally {
        setPreviewing(false);
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [specKey, spec]);

  return (
    <div className="space-y-6">
      <div>
        <p className="label text-signal-700">Disruption intake</p>
        <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900">What is disrupting the network?</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          Describe a disruption in plain language. The system interprets it, finds the affected in-flight orders, sellers and SKUs,
          scores the delay risk, decides how to mitigate each order and drafts customer emails. You review the interpretation
          before anything runs.
        </p>
      </div>

      {meta.data && !meta.data.dataset_ready && (
        <Notice tone="warning">The dataset is still loading (the seed container builds the snapshot on first start). Try again in a minute.</Notice>
      )}

      <Card title="1. Describe the disruption">
        <label htmlFor="scenario" className="sr-only">Disruption scenario</label>
        <textarea
          id="scenario"
          value={query}
          onChange={(e) => changeQuery(e.target.value)}
          rows={3}
          maxLength={2000}
          className="w-full rounded-lg border border-slate-300 p-3 text-sm shadow-inner focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="e.g. Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days"
        />
        <div className="mt-2 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => changeQuery(ex)}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-700 hover:border-brand-300 hover:bg-brand-50"
            >
              {ex}
            </button>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-4 border-t border-slate-100 pt-4 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={options.use_llm && llmAvailable}
              disabled={!llmAvailable}
              onChange={(e) => setOptions({ ...options, use_llm: e.target.checked })}
            />
            Use the LLM {llmAvailable ? "" : "(offline: rules + templates)"}
          </label>
          <label className="flex items-center gap-2">
            Email language
            <select
              value={options.locale}
              onChange={(e) => setOptions({ ...options, locale: e.target.value as RunOptions["locale"] })}
              className="rounded-md border border-slate-300 px-2 py-1"
            >
              <option value="en">English</option>
              <option value="pt-BR">Português (Brasil)</option>
            </select>
          </label>
          <label className="flex items-center gap-2">
            LLM-drafted emails (max)
            <input
              type="number"
              min={0}
              max={500}
              value={options.max_llm_emails ?? ""}
              placeholder={String(meta.data?.llm.max_llm_emails ?? 25)}
              onChange={(e) => setOptions({ ...options, max_llm_emails: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })}
              className="w-20 rounded-md border border-slate-300 px-2 py-1"
            />
          </label>
          <label className="flex items-center gap-2" title="Shift email dates so the snapshot date maps to today (presentation only)">
            <input type="checkbox" checked={options.date_rebase} onChange={(e) => setOptions({ ...options, date_rebase: e.target.checked })} />
            Rebase email dates to today
          </label>
          <div className="ml-auto flex gap-2">
            <Button onClick={() => parse.mutate()} loading={parse.isPending} disabled={query.trim().length < 5}>
              Interpret
            </Button>
          </div>
        </div>
        <div className="mt-3">
          <ErrorBox error={parse.error} title="Could not interpret the scenario" />
        </div>
      </Card>

      {parsed && spec && (
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <InterpretationCard parsed={parsed} spec={spec} onChange={setSpec} />
          </div>
          <div className="space-y-4">
            <PreviewPanel preview={preview} loading={previewing} error={previewError} />
            <Card title="3. Run the agents">
              <p className="text-sm text-slate-600">
                Runs the Scout, Logistics Analyst, Mitigation, Customer Success, QA Critic and Reporter agents on this interpretation.
              </p>
              <Button
                className="mt-3 w-full"
                onClick={() => createRun.mutate()}
                loading={createRun.isPending}
                disabled={!!previewError || previewing}
              >
                Run impact analysis
              </Button>
              <div className="mt-3">
                <ErrorBox error={createRun.error} title="Could not start the run" />
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
