import clsx from "clsx";
import type { RunEvent, RunStatus } from "../../api/types";
import { Spinner } from "../../components/ui";

const STAGES = [
  { id: "intake", name: "Intake", role: "Interprets the scenario into a validated spec" },
  { id: "scout", name: "Scout", role: "Exact scope filters + hybrid retrieval evidence" },
  { id: "analyst", name: "Logistics Analyst", role: "ETA, breach probability, severity" },
  { id: "mitigation", name: "Mitigation", role: "Policy rules + bounded LLM adjudication" },
  { id: "customer_success", name: "Customer Success", role: "Grounded, personalised emails" },
  { id: "qa_critic", name: "QA Critic", role: "Fact, policy and tone checks; redrafts" },
  { id: "reporter", name: "Reporter", role: "Stats, recommendations, narrative" },
];

type StageState = "pending" | "running" | "done" | "failed";

export default function Timeline({ events: allEvents, status }: { events: RunEvent[]; status: RunStatus }) {
  // A retried run keeps the events of earlier attempts; only the latest attempt (from its run_started) counts.
  const lastStart = allEvents.reduce((acc, e) => (e.type === "run_started" ? e.seq : acc), 0);
  const events = allEvents.filter((e) => e.seq >= lastStart);
  const state = (id: string): StageState => {
    if (events.some((e) => e.stage === id && e.type === "run_failed")) return "failed";
    if (events.some((e) => e.stage === id && e.type === "stage_completed")) return "done";
    if (events.some((e) => e.stage === id && e.type === "stage_started")) return status === "running" ? "running" : "failed";
    return "pending";
  };
  const runLevel = events.filter((e) => e.stage === "run" || e.stage === "setup");

  return (
    <ol className="space-y-3">
      {STAGES.map((s, i) => {
        const st = state(s.id);
        const done = events.find((e) => e.stage === s.id && e.type === "stage_completed");
        const messages = events.filter((e) => e.stage === s.id && ["result", "warning", "info", "run_failed"].includes(e.type));
        return (
          <li key={s.id} className="flex gap-3">
            <div className="flex flex-col items-center">
              <span
                className={clsx(
                  "grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-semibold",
                  st === "done" && "bg-emerald-600 text-white",
                  st === "running" && "bg-brand-600 text-white",
                  st === "failed" && "bg-red-600 text-white",
                  st === "pending" && "bg-slate-200 text-slate-600",
                )}
              >
                {st === "running" ? <Spinner small /> : st === "done" ? "✓" : st === "failed" ? "!" : i + 1}
              </span>
              {i < STAGES.length - 1 && <span className="mt-1 w-px flex-1 bg-slate-200" />}
            </div>
            <div className="min-w-0 flex-1 pb-2">
              <p className="text-sm font-semibold text-slate-800">
                {s.name}
                <span className="ml-2 text-xs font-normal text-slate-500">{s.role}</span>
                {done?.payload?.duration_ms !== undefined && (
                  <span className="ml-2 text-xs font-normal text-slate-400">{(done.payload.duration_ms / 1000).toFixed(1)}s</span>
                )}
              </p>
              <ul className="mt-1 space-y-0.5">
                {messages.map((m) => (
                  <li
                    key={m.seq}
                    className={clsx(
                      "text-xs",
                      m.level === "warning" && "text-amber-700",
                      m.level === "error" && "text-red-700",
                      m.type === "result" ? "font-medium text-slate-800" : m.level === "info" && "text-slate-600",
                    )}
                  >
                    {m.message}
                  </li>
                ))}
              </ul>
            </div>
          </li>
        );
      })}
      {runLevel
        .filter((e) => e.level !== "info" || e.type === "run_completed")
        .map((e) => (
          <li key={e.seq} className={clsx("text-xs", e.level === "error" ? "text-red-700" : "text-slate-500")}>
            {e.message}
          </li>
        ))}
    </ol>
  );
}
