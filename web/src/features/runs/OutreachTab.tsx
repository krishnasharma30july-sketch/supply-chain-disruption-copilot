import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { api } from "../../api/client";
import type { OutreachItem } from "../../api/types";
import { Badge, Button, Card, Empty, ErrorBox, Loading, Notice } from "../../components/ui";

/** Drafts for people: the care team's call briefs for critical customers, and notices for the most exposed sellers. */
export default function OutreachTab({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["outreach", runId], queryFn: () => api.outreach(runId) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const items = q.data ?? [];
  const briefs = items.filter((i) => i.kind === "call_brief");
  const notices = items.filter((i) => i.kind === "seller_notice");
  if (items.length === 0) {
    return <Empty>No call briefs or seller notices were needed for this scenario (no critical-severity customers and no exposed sellers).</Empty>;
  }
  return (
    <div className="space-y-6">
      <Notice>
        These are drafts for your team to use. Nothing here is sent automatically. Each text was fact-checked against the
        decisions, and a verified template is used when the AI is unavailable or fails the check.
      </Notice>
      <Section
        title="Care-team call briefs"
        hint="Talking points for the critical-severity customers the report asks care to call before the emails go out."
        items={briefs}
      />
      <Section
        title="Seller notices"
        hint="Operational notices for the sellers with the most affected orders: hold handovers, re-plan, confirm stock."
        items={notices}
      />
    </div>
  );
}

function Section({ title, hint, items }: { title: string; hint: string; items: OutreachItem[] }) {
  if (items.length === 0) return null;
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">
          {title} <span className="font-normal text-slate-500">({items.length})</span>
        </h2>
        <p className="text-xs text-slate-500">{hint}</p>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {items.map((i) => (
          <OutreachCard key={i.id} item={i} />
        ))}
      </div>
    </section>
  );
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  };
  return (
    <Button variant="secondary" onClick={copy}>
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy text"}
    </Button>
  );
}

function OutreachCard({ item }: { item: OutreachItem }) {
  const checks = item.qa?.checks ?? [];
  const failed = checks.filter((c) => !c.passed);
  const text = item.subject ? `${item.subject}\n\n${item.body}` : item.body;
  const facts = item.data?.facts as { orders_affected?: number; value?: string; awaiting_pickup?: number } | undefined;
  return (
    <Card
      title={item.title}
      actions={
        <span className="flex gap-1">
          <Badge className={item.generator === "llm" ? "bg-brand-900 text-brand-50 ring-brand-900" : undefined}>{item.generator}</Badge>
          <Badge className={item.status === "draft" ? "bg-sky-50 text-sky-800 ring-sky-300" : "bg-red-50 text-red-800 ring-red-300"}>
            {item.status.replace("_", " ")}
          </Badge>
        </span>
      }
    >
      {item.subject && <p className="text-sm font-semibold">{item.subject}</p>}
      {facts && (
        <p className="mt-1 text-xs text-slate-500">
          {facts.orders_affected} affected order(s) · {facts.value}
          {facts.awaiting_pickup ? ` · ${facts.awaiting_pickup} awaiting pickup` : ""}
        </p>
      )}
      <pre className="mt-2 max-h-[40vh] overflow-y-auto whitespace-pre-wrap rounded-md bg-slate-50 p-3 font-sans text-sm leading-6 text-slate-800">
        {item.body}
      </pre>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <CopyButton text={text} />
        <span className={clsx("text-xs", failed.length ? "text-red-700" : "text-emerald-800")}>
          {failed.length ? `Fact check: ${failed.map((c) => c.name.replace(/_/g, " ")).join(", ")} failed` : "✓ Fact check passed"}
          {item.model ? ` · ${item.model}` : ""}
        </span>
      </div>
    </Card>
  );
}
