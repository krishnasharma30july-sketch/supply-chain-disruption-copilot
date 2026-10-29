"""Prometheus metrics.

Process-local counters/histograms (HTTP in the API; LLM calls and runs in whichever process makes them) plus a
collector that reads business state from Postgres at scrape time, so totals are correct with any number of API
replicas or workers. The API serves /metrics; the worker serves its own on WORKER_METRICS_PORT.
"""

from __future__ import annotations

import re

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["agent", "model", "status", "cached"])
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens", ["model", "direction"])
LLM_LATENCY = Histogram("llm_call_duration_seconds", "LLM call latency (uncached)", ["model"],
                        buckets=(0.5, 1, 2, 5, 10, 20, 40, 80))
RUNS_FINISHED = Counter("pipeline_runs_finished_total", "Pipeline runs finished", ["status"])
RUN_DURATION = Histogram("pipeline_run_duration_seconds", "Pipeline run duration", ["status"],
                         buckets=(5, 10, 30, 60, 120, 300, 600, 1200, 3600))

_ID = re.compile(r"/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})(?=/|$)")


def route_label(scope: dict) -> str:
    """Route template when the router matched (bounded cardinality), else the path with ids collapsed."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return _ID.sub("/{id}", scope.get("path", ""))[:120] or "/"


def record_llm_call(agent: str, model: str, status: str, cached: bool, in_tok: int | None, out_tok: int | None,
                    latency_ms: int | None) -> None:
    LLM_CALLS.labels(agent, model, status, str(cached).lower()).inc()
    if in_tok:
        LLM_TOKENS.labels(model, "input").inc(in_tok)
    if out_tok:
        LLM_TOKENS.labels(model, "output").inc(out_tok)
    if not cached and latency_ms:
        LLM_LATENCY.labels(model).observe(latency_ms / 1000)


class DatabaseCollector(Collector):
    """Business gauges computed from Postgres on each scrape."""

    def __init__(self, engine_factory):
        self.engine_factory = engine_factory

    def collect(self):
        from sqlalchemy import text

        runs = GaugeMetricFamily("copilot_runs", "Runs by status", labels=["status"])
        comms = GaugeMetricFamily("copilot_communications", "Customer emails by status", labels=["status"])
        llm = GaugeMetricFamily("copilot_llm_calls_24h", "LLM calls in the last 24 hours", labels=["status"])
        stale = GaugeMetricFamily("copilot_runs_without_heartbeat", "Running runs with no heartbeat for 90 s")
        up = GaugeMetricFamily("copilot_database_up", "1 when the metrics query succeeded")
        try:
            with self.engine_factory().connect() as conn:
                for status, n in conn.execute(text("SELECT status, count(*) FROM runs GROUP BY status")):
                    runs.add_metric([status], n)
                for status, n in conn.execute(text("SELECT status, count(*) FROM communications GROUP BY status")):
                    comms.add_metric([status], n)
                for status, n in conn.execute(text(
                    "SELECT status, count(*) FROM llm_calls WHERE ts > now() - interval '24 hours' GROUP BY status"
                )):
                    llm.add_metric([status], n)
                stale.add_metric([], conn.execute(text(
                    "SELECT count(*) FROM runs WHERE status = 'running' "
                    "AND coalesce(heartbeat_at, started_at) < now() - interval '90 seconds'"
                )).scalar_one())
            up.add_metric([], 1)
        except Exception:
            up.add_metric([], 0)
        yield from (runs, comms, llm, stale, up)
