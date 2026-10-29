import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./client";
import type { RunEvent } from "./types";

const REFRESH_ON = new Set(["stage_completed", "run_completed", "run_failed", "run_canceled"]);

/** Streams run events over SSE (replays history first; the browser reconnects with Last-Event-ID).
 *  `attemptKey` (the run's started_at) re-subscribes when a run is retried after its stream ended. */
export function useRunEvents(runId: string, attemptKey?: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const qc = useQueryClient();

  useEffect(() => {
    setEvents([]);
    const es = new EventSource(api.eventsUrl(runId));
    es.onopen = () => setConnected(true);
    es.addEventListener("run_event", (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as RunEvent;
      setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev].sort((a, b) => a.seq - b.seq)));
      if (REFRESH_ON.has(ev.type)) qc.invalidateQueries({ queryKey: ["run", runId] });
    });
    es.addEventListener("end", () => {
      es.close();
      setConnected(false);
      qc.invalidateQueries({ queryKey: ["run", runId] });
    });
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [runId, attemptKey, qc]);

  return { events, connected };
}
