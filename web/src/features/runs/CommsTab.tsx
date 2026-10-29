import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { Communication } from "../../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Notice } from "../../components/ui";
import { fmtTimestamp } from "../../lib/format";
import { useAuth } from "../auth/useAuth";

const STATUSES = ["all", "draft", "qa_failed", "approved", "sent", "rejected"] as const;
type StatusFilter = (typeof STATUSES)[number];
const STATUS_STYLE: Record<Communication["status"], string> = {
  draft: "bg-sky-50 text-sky-800 ring-sky-300",
  qa_failed: "bg-red-50 text-red-800 ring-red-300",
  approved: "bg-emerald-50 text-emerald-800 ring-emerald-300",
  sent: "bg-teal-50 text-teal-800 ring-teal-300",
  rejected: "bg-slate-100 text-slate-600 ring-slate-300",
};
const PAGE = 50;
const NEEDS_APPROVER = "Requires the approver role";

export default function CommsTab({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const canApprove = useAuth().can("approver");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const params = { status: status === "all" ? undefined : [status], limit: PAGE, offset };
  // After an approval the mail worker delivers in the background: poll briefly so "approved" turns into "sent".
  const [watchUntil, setWatchUntil] = useState(0);
  const listQ = useQuery({
    queryKey: ["comms", runId, params],
    queryFn: () => api.communications(runId, params),
    placeholderData: keepPreviousData,
    refetchInterval: () => (Date.now() < watchUntil ? 2000 : false),
  });
  const items = listQ.data?.items ?? [];
  const selected = items.find((c) => c.id === selectedId) ?? items[0] ?? null;
  const refresh = () => {
    setWatchUntil(Date.now() + 20_000);
    return qc.invalidateQueries({ queryKey: ["comms", runId] });
  };

  const bulkApprove = useMutation({
    mutationFn: () => api.bulkApprove(runId, items.filter((c) => c.status === "draft").map((c) => c.id)),
    onSuccess: refresh,
  });
  // The list is sorted by severity, so these are the most important template emails on the page. Capped per click:
  // each AI draft costs requests against a small (free-tier) daily quota.
  const MAX_AI_DRAFTS = 10;
  const draftTargets = items
    .filter((c) => c.generator === "template" && c.status !== "approved" && c.status !== "sent")
    .slice(0, MAX_AI_DRAFTS);
  const bulkDraft = useMutation({ mutationFn: () => api.bulkDraft(runId, draftTargets.map((c) => c.id)) });
  const templates = draftTargets.length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => { setStatus(s); setOffset(0); }}
            className={clsx("rounded-full px-3 py-1 text-xs font-medium ring-1", status === s ? "bg-brand-600 text-white ring-brand-600" : "bg-white text-slate-700 ring-slate-300")}
          >
            {s.replace("_", " ")}
          </button>
        ))}
        <div className="ml-auto flex gap-2">
          <Button variant="secondary" onClick={() => bulkDraft.mutate()} loading={bulkDraft.isPending} disabled={templates === 0}
            title={`Queue LLM drafting for the ${MAX_AI_DRAFTS} highest-severity template emails on this page`}>
            Draft top {templates} with AI
          </Button>
          <Button onClick={() => bulkApprove.mutate()} loading={bulkApprove.isPending}
            disabled={!canApprove || !items.some((c) => c.status === "draft")}
            title={canApprove ? "Approve and send every QA-passing draft on this page" : NEEDS_APPROVER}>
            Approve & send all passing on this page
          </Button>
        </div>
      </div>
      {bulkDraft.isSuccess && <Notice>Queued. Refresh in a little while to see AI drafts (each one is QA-checked before it replaces the template).</Notice>}
      <ErrorBox error={bulkApprove.error || bulkDraft.error} />

      {listQ.isLoading ? (
        <Loading />
      ) : listQ.error ? (
        <ErrorBox error={listQ.error} />
      ) : items.length === 0 ? (
        <Empty>No emails in this view.</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <ul className="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto rounded-xl border border-slate-200 bg-white">
              {items.map((c) => (
                <li key={c.id}>
                  <button
                    onClick={() => setSelectedId(c.id)}
                    className={clsx("w-full px-3 py-2.5 text-left hover:bg-slate-50", selected?.id === c.id && "bg-brand-50")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{c.first_name}</span>
                      <span className="flex gap-1">
                        <Badge className={c.generator === "llm" ? "bg-brand-900 text-brand-50 ring-brand-900" : undefined}>{c.generator}</Badge>
                        <Badge className={STATUS_STYLE[c.status]}>{c.status.replace("_", " ")}</Badge>
                      </span>
                    </div>
                    <p className="mt-0.5 truncate text-xs text-slate-600">{c.subject}</p>
                    <p className="text-[11px] text-slate-400">severity {Math.round(c.max_severity)} · {c.order_ids.length} order(s)</p>
                  </button>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex items-center justify-between text-xs text-slate-600">
              <span>{offset + 1}–{Math.min(offset + PAGE, listQ.data?.total ?? 0)} of {listQ.data?.total}</span>
              <div className="flex gap-2">
                <Button variant="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
                <Button variant="secondary" disabled={offset + PAGE >= (listQ.data?.total ?? 0)} onClick={() => setOffset(offset + PAGE)}>Next</Button>
              </div>
            </div>
          </div>
          <div className="lg:col-span-3">
            {selected && <EmailEditor key={`${selected.id}-${selected.version}`} comm={selected} onChanged={refresh} canApprove={canApprove} />}
          </div>
        </div>
      )}
    </div>
  );
}

function EmailEditor({ comm, onChanged, canApprove }: { comm: Communication; onChanged: () => void; canApprove: boolean }) {
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(comm.subject);
  const [body, setBody] = useState(comm.body);
  useEffect(() => {
    setSubject(comm.subject);
    setBody(comm.body);
  }, [comm.subject, comm.body]);

  const save = useMutation({ mutationFn: () => api.editCommunication(comm.id, subject, body), onSuccess: () => { setEditing(false); onChanged(); } });
  const approve = useMutation({ mutationFn: () => api.approve(comm.id), onSuccess: onChanged });
  const reject = useMutation({ mutationFn: () => api.reject(comm.id), onSuccess: onChanged });
  const regenerate = useMutation({ mutationFn: () => api.regenerate(comm.id), onSuccess: onChanged });
  const resend = useMutation({ mutationFn: () => api.resend(comm.id), onSuccess: onChanged });
  const checks = comm.qa?.checks ?? [];
  const locked = comm.status === "approved" || comm.status === "sent";
  const sent = comm.status === "sent";

  return (
    <Card
      title={<span>To {comm.first_name} <span className="font-normal text-slate-500">· orders {comm.order_ids.map((o) => o.slice(0, 8).toUpperCase()).join(", ")}</span></span>}
      actions={<Badge>{comm.locale} · v{comm.version}{comm.model ? ` · ${comm.model}` : ""}</Badge>}
    >
      {editing ? (
        <div className="space-y-2">
          <input value={subject} onChange={(e) => setSubject(e.target.value)} className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm font-medium" />
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={16} className="w-full rounded-md border border-slate-300 p-2 font-sans text-sm" />
        </div>
      ) : (
        <>
          <p className="text-sm font-semibold">{comm.subject}</p>
          <pre className="mt-2 max-h-[45vh] overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 font-sans text-sm leading-6 text-slate-800">{comm.body}</pre>
        </>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {editing ? (
          <>
            <Button onClick={() => save.mutate()} loading={save.isPending}>Save & re-check</Button>
            <Button variant="ghost" onClick={() => { setEditing(false); setSubject(comm.subject); setBody(comm.body); }}>Cancel</Button>
          </>
        ) : (
          <>
            <Button onClick={() => approve.mutate()} loading={approve.isPending}
              disabled={!canApprove || locked || comm.status === "qa_failed"} title={canApprove ? "Approve and send" : NEEDS_APPROVER}>
              Approve & send
            </Button>
            <Button variant="secondary" onClick={() => setEditing(true)} disabled={locked}>Edit</Button>
            <Button variant="secondary" onClick={() => regenerate.mutate()} loading={regenerate.isPending} disabled={locked}>Regenerate with AI</Button>
            <Button variant="ghost" onClick={() => reject.mutate()} loading={reject.isPending}
              disabled={!canApprove || sent || comm.status === "rejected"} title={canApprove ? undefined : NEEDS_APPROVER}>
              Reject
            </Button>
          </>
        )}
      </div>
      {(comm.status === "approved" || sent) && (
        <div className={clsx("mt-3 rounded-md px-3 py-2 text-xs", comm.send_error ? "bg-amber-50 text-amber-900" : "bg-teal-50 text-teal-900")}>
          {sent ? (
            <>Sent to {comm.email_to} on {fmtTimestamp(comm.sent_at)}{comm.approved_by ? `, approved by ${comm.approved_by}` : ""}.</>
          ) : comm.send_error ? (
            <span className="flex flex-wrap items-center gap-2">
              Delivery failed: {comm.send_error}
              {canApprove && <Button variant="secondary" onClick={() => resend.mutate()} loading={resend.isPending}>Retry send</Button>}
            </span>
          ) : (
            <>Approved{comm.approved_by ? ` by ${comm.approved_by}` : ""}; queued for delivery.</>
          )}
        </div>
      )}
      <div className="mt-2">
        <ErrorBox error={save.error || approve.error || reject.error || regenerate.error || resend.error} />
      </div>

      <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
        QA critic {comm.qa?.passed ? "· passed" : "· failed"}
      </h3>
      <ul className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
        {checks.map((c) => (
          <li key={c.name} className={clsx("rounded-md px-2 py-1", c.passed ? "bg-emerald-50 text-emerald-900" : "bg-red-50 text-red-900")} title={c.detail}>
            {c.passed ? "✓" : "✗"} {c.name.replace(/_/g, " ")}
            {!c.passed && <span className="block text-[11px]">{c.detail}</span>}
          </li>
        ))}
      </ul>
    </Card>
  );
}
