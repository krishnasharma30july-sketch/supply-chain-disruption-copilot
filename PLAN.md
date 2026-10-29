# Supply Chain Disruption Copilot — System Blueprint

> **Status:** Design v1.0 (pre-implementation) · **Date:** 2026-10-28 · **Author:** Krishna Sharma
> **Note:** this is the original blueprint. The as-built system, its verified results, the review fixes and every
> deviation from this plan (e.g. Gemini as the LLM provider, no Alembic/OpenTelemetry) are documented in
> [`README.md`](README.md), which is authoritative.
> **Scope:** End-to-end design (data → retrieval → agents → API → UI → ops) for an AI-assisted
> *decision system* (not a chatbot) that turns a natural-language disruption into per-order
> impact, mitigation decisions and customer communications, over the Olist Brazilian E-Commerce dataset.

---

## 0. TL;DR

- **One input, one traceable chain:** `Disruption text → DisruptionSpec → affected sellers / lanes → in-flight orders → SKUs (product attributes) → severity → mitigation action → customer email`, with every hop recorded so any number in the UI can be explained.
- **Deterministic where correctness matters, LLM where language matters.** Geography, ETA math, severity scoring, policy decisions and compensation amounts are code and versioned config. LLMs do four things: parse the free-text scenario into a typed spec, adjudicate flagged edge cases within a fixed set of allowed actions, write empathetic emails, and write the executive narrative. Every LLM output is schema-validated and fact-checked against the deterministic facts.
- **Hybrid retrieval with clear roles:** exact SQL filters for scope (vectors are bad at "seller_state = SP"); dense + sparse (BM25-style) search fused with RRF for fuzzy concepts ("bulky home goods", "fragile", "gifts") over order, product and seller documents and a policy knowledge base, which agents cite.
- **Time-travel snapshot:** Olist is historical (2016–2018), so the system reconstructs which orders were *in flight* at a configurable `as_of` instant and masks everything after it, with no data leakage. This also enables a **backtest against the real May 2018 Brazilian truckers' strike**.
- **Stack:** FastAPI (Python 3.12, async SQLAlchemy 2, Pydantic v2), PostgreSQL 16 + pgvector, Redis + arq worker, **Google Gemini (default, free AI Studio key) via the official `google-genai` SDK** with schema-constrained JSON output (Claude is an optional, pluggable provider), fastembed (local ONNX multilingual embeddings); React 18 + TypeScript + Vite + TanStack Query/Table + Tailwind/shadcn; Docker Compose.
- **Runs fully offline:** without an API key, a deterministic rule parser and template emails take over (clearly badged in the UI), so reviewers can always run it.

---

## 1. Problem Understanding

### 1.1 What the case study asks for (traceability matrix)

| Requirement (from PDF) | Where it is satisfied in this design |
|---|---|
| Use Orders, Order Items, Products, Sellers | §3 data model; ETL §3.4 |
| AI-assisted *decision system*, not a chatbot | Single-shot scenario → structured run; no conversational loop (§5) |
| Specialized agents/services that coordinate | Orchestrated agent DAG (§5): Intake, Scout, Logistics Analyst, Mitigation, Customer Success, QA Critic, Reporter |
| Hypothetical disruption query in, autonomous coordination | `POST /runs` with free text → full pipeline (§7) |
| Document assumptions and how they shape design | §2 (assumption register with design impact) + surfaced in UI/README |
| RAG: load ~10k active/recent orders into a vector store | §4: 10,000 most recent orders at `as_of` in pgvector + policy KB |
| Semantic/hybrid search for orders, heavy/bulky products, impacted sellers | §4.3 hybrid retrieval (dense + sparse + filters, RRF) |
| Logistics Analyst: delayed orders, severity from freight weight and original SLA | §5.4: ETA model + P(breach) + weighted severity |
| Mitigation Agent: cancel / reroute / delay by product type | §5.5: policy-as-code decision table over product class |
| Customer Success Agent: personalized, empathetic email with specific resolution | §5.6 + QA critic loop §5.7 |
| Multi-step reasoning: geo disruption → SKUs → customer output | §5.9 reasoning trace (persisted, shown per order) |
| Interface: script / CLI / web app | React web app (§8) **and** Typer CLI (§9.3) |
| Deliverables: code, Docker, samples for 1–2 scenarios, README | §11, §12 |
| Evaluation: correctness, design clarity, LLM vs deterministic, retrieval, explainability | §6 (LLM-vs-code table), §10 (evals), §5.9 (trace) |

### 1.2 Non-goals (v1)

- Sending real emails (we use a simulated outbox with an approval workflow).
- A real carrier or TMS integration (we model rerouting with configurable assumptions).
- A conversational chatbot.
- Multi-tenant SaaS (we design the seams; we don't build tenancy).

### 1.3 Personas

| Persona | Needs |
|---|---|
| **Ops Analyst** | Enter a scenario, check the interpretation, see impact fast, drill into orders |
| **CX Lead / Approver** | Review, edit and approve customer emails in bulk; check policy compliance |
| **Ops Manager** | Executive summary, cost of mitigation, top sellers and lanes at risk, exportable report |
| **Engineer / Reviewer** | Reproducible runs, audit trail, evals, offline mode |

---

## 2. Assumption Register (dataset limitations → design decisions)

Every assumption lives in `config/assumptions.yaml` with an ID. The run snapshot stores the
values that were used, the UI shows them on an "Assumptions" panel, and the README lists them.

| ID | Assumption | Why it is needed | Design impact |
|---|---|---|---|
| A1 | **Simulation clock:** "now" = configurable `AS_OF` (default `2018-08-01T00:00:00-03:00`). Order state is reconstructed from timestamps ≤ `AS_OF`; later timestamps are masked. | The dataset is historical; there are no live orders. | Snapshot builder (§3.3). No leakage: lane stats use only deliveries before `AS_OF`. |
| A2 | **Display rebasing (optional):** dates shown in emails/UI can be shifted so `AS_OF` maps to today (`DATE_REBASE=true`). | Emails dated 2018 read strangely in a demo. | Presentation layer only; stored data is unchanged. Off by default in the evals. |
| A3 | **Order stages** derived from timestamps: `pending_approval` (approved_at null/after T), `awaiting_pickup` (approved, not handed to carrier), `in_transit` (handed to carrier, not delivered), `delivered`. Statuses `canceled`/`unavailable` are excluded. | No explicit shipment events in the data. | Stage drives which legs a disruption can hit and which actions are feasible. |
| A4 | **SLA = `order_estimated_delivery_date`** (customer promise). **Seller SLA = `shipping_limit_date`** (handover deadline). | The only promise fields available. | Slack and breach are computed against these. |
| A5 | **Freight leg model:** shipments leave from the seller's city/state (first mile), move on an inter-state lane (linehaul), and are delivered in the customer's city/state (last mile). There are no hubs or carriers in the data. | No routing data. | A disruption targets a leg plus a geography; matching is deterministic (§5.3). |
| A6 | **Lane transit time** = empirical distribution of (`delivered_customer` − `delivered_carrier`) per `seller_state → customer_state`, from orders delivered before T. Lanes with n < 30 fall back to the destination region, then to the national level. | No carrier ETA. | ETA model + P(breach) use the ECDF (§5.4). |
| A7 | **In-transit dwell:** an order handed to the carrier within the last `origin_dwell_days` (default 1.0) is assumed to still be inside the origin region. | No GPS/scan events. | Decides whether an in-transit order is caught by an origin disruption. |
| A8 | **Backlog recovery:** after a disruption ends, the backlog adds `recovery_factor × duration` days (default 0.3, rounded up). | Real networks don't recover instantly. | Projected delay = window overlap + recovery. |
| A9 | **Billable weight** = max(actual kg, L×W×H/6000) (Correios-style volumetric divisor). | Weight alone understates bulky goods. | Handling profile + severity weight component. |
| A10 | **Handling profiles:** PARCEL (≤5 kg billable and max dim ≤60 cm), MEDIUM (≤30 kg), BULKY (>30 kg billable, or any dim >100 cm, or L+W+H >200 cm). | No "bulky" flag in the data. | Drives reroute feasibility and severity. |
| A11 | **Product class** via a curated category→class map (PERISHABLE, FRAGILE, HIGH_VALUE_TECH, ESSENTIAL, GIFT_SEASONAL, FURNITURE_HOME, STANDARD). Unmapped/null categories are classified by embedding similarity to class prototypes, and the confidence is recorded. | Categories are Portuguese labels with no semantics. | Mitigation rules key on class + profile. |
| A12 | **Alternate fulfilment:** the same `product_id` sold by another seller outside the disruption scope (with a sale in the last 90 days before T) is a reroute candidate. Assumes stock is available. | No inventory data. | Enables the data-grounded `REROUTE_ALT_SELLER` action. |
| A13 | **Alternate route:** PARCEL-profile shipments can switch to an alternate carrier/air corridor at a freight cost uplift of ×1.6 and a transit of lane p50 + 1 day, unless the disruption is nationwide or `all_modes`. | No carrier options in the data. | Enables `REROUTE_ALT_ROUTE`; the cost estimate goes into the summary. |
| A14 | **Customer identity:** Olist has no names or emails. Greeting names are synthetic, generated deterministically (Faker pt_BR seeded by `customer_unique_id`) and flagged as synthetic. Emails go to a simulated outbox. | Privacy and missing PII. | Personalization uses order facts, not identity; LGPD-friendly. |
| A15 | **Language:** emails default to English for the reviewer; `pt-BR` is supported (real customers are Brazilian). | Audience. | Locale is a run option and is passed to the prompt. |
| A16 | **Compensation & mitigation policy** is invented by us (`config/policy.yaml`, KB docs) and versioned. | No company policy is given. | Policy-as-code; the LLM can never exceed it (validator). |
| A17 | **One email per customer per run** (customers with several affected orders get a single consolidated email). | Avoids spamming customers. | Communications are keyed by `customer_unique_id`. |
| A18 | **"Sao Paulo" ambiguity:** city vs state is resolved explicitly ("state of", "SP state", "city of"); otherwise the system flags the ambiguity, defaults to **state** for freight/logistics phrasing, and shows the counts for both options in the preview so the user can switch. | A common real ambiguity. | HITL confirmation step (§8.2). |
| A19 | Corpus = the **10,000 most recent orders purchased ≤ T** (non-canceled). Only non-delivered ones are actionable; delivered ones stay as context (seller history, retrieval). | Matches the "~10k active/recent orders" requirement. | Bounded index size and reproducible runs. |

---

## 3. Data Layer

### 3.1 Source tables (Olist, Kaggle `olistbr/brazilian-ecommerce`)

| File | Rows (≈) | Used for | Required by PDF? |
|---|---|---|---|
| `olist_orders_dataset.csv` | 99k | Status, timestamps, SLA | ✅ |
| `olist_order_items_dataset.csv` | 113k | SKU lines, seller, price, freight, `shipping_limit_date` | ✅ |
| `olist_products_dataset.csv` | 33k | Category, weight, dimensions | ✅ |
| `olist_sellers_dataset.csv` | 3.1k | Seller origin (zip, city, state) | ✅ |
| `olist_customers_dataset.csv` | 99k | Destination geography, `customer_unique_id` | Supporting (destination/last-mile disruptions, dedupe) |
| `product_category_name_translation.csv` | 71 | English category names | Supporting |
| `olist_geolocation_dataset.csv` | 1M | Zip-prefix centroids → radius queries, distance | Supporting (optional; degrades gracefully) |

Known data issues to handle explicitly:
- Typos in column names (`product_name_lenght`, `product_description_lenght`): rename in ETL.
- About 610 products with a null category and 2 with null weight/dims: impute dims from the category median and flag `imputed=true`.
- City spellings are messy (`sao paulo`, `são paulo`, `sao paulo - sp`): normalize with `unidecode` + lowercase + suffix strip, then match against the geolocation gazetteer.
- Some `delivered` orders have a null `delivered_customer_date`: treat as unknown and exclude them from lane statistics.
- Two categories are missing from the translation file (`pc_gamer`, `portateis_cozinha_e_preparadores_de_alimentos`): add manual translations.
- Each `order_item_id` is one unit, so quantity = count of rows per (`order_id`, `product_id`).

### 3.2 Ingestion

- `etl/download.py`: uses `kagglehub.dataset_download("olistbr/brazilian-ecommerce")` (as in `dataset.txt`) and falls back to CSVs placed manually in `data/raw/`. It validates the expected file set and records SHA-256 hashes in `data/manifest.json`.
- `etl/validate.py`: **pandera** schemas (types, nullability, value ranges, FK integrity: items→orders/products/sellers). The run fails loudly on violations.
- Pipeline is **idempotent**: `make seed` can be re-run safely (upserts plus a dataset hash check that skips unchanged steps).

### 3.3 Snapshot builder (time travel)

```
for each order with purchase_ts ≤ T and status ∉ {canceled, unavailable}:
    mask every timestamp > T to NULL           # no leakage
    stage = pending_approval  if approved_at is NULL
          | awaiting_pickup   if delivered_carrier is NULL
          | in_transit        if delivered_customer is NULL
          | delivered
corpus = 10,000 most recent by purchase_ts   (A19)
actionable = corpus where stage != delivered
```

The original, unmasked actuals are kept in a separate `order_actuals` table that **only the eval/backtest module** reads (to score ETA predictions and the strike backtest). The runtime services never read it; this is enforced by repository boundaries and tested.

### 3.4 Derived features (deterministic, computed in ETL with polars)

| Feature | Definition |
|---|---|
| `billable_weight_kg` | max(weight_g/1000, L·W·H/6000) per unit, summed per order |
| `handling_profile` | A10 thresholds over the order's largest line |
| `product_class` | A11 map / embedding fallback (+ `class_confidence`) |
| `lane_stats` | per (origin_uf, dest_uf): n, mean, p50, p90, 20-bucket ECDF of transit days (pre-T only) |
| `seller_stats` | volume, late-handover rate (`delivered_carrier > shipping_limit`), late-delivery rate, median handling days, categories sold (pre-T only) |
| `alt_sellers` | per product_id: other sellers with sales in [T−90d, T] plus their state |
| `geo_centroids` | zip-prefix → mean lat/lng (outliers outside Brazil's bounding box dropped) |

### 3.5 Relational schema (PostgreSQL 16)

```
-- reference / domain (read-mostly, loaded by ETL)
sellers(seller_id PK, zip_prefix, city, city_norm, state, lat, lng)
customers(customer_id PK, customer_unique_id, zip_prefix, city, city_norm, state, lat, lng, synthetic_first_name)
categories(name_pt PK, name_en, product_class, class_source, class_confidence)
products(product_id PK, category_pt FK, weight_g, length_cm, height_cm, width_cm,
         billable_weight_kg, handling_profile, imputed bool)
orders(order_id PK, customer_id FK, status_raw, stage, purchase_ts, approved_ts,
       carrier_ts, delivered_ts, estimated_delivery_ts, in_corpus bool, as_of)
order_items(order_id FK, order_item_id, product_id FK, seller_id FK,
            shipping_limit_ts, price, freight_value, PK(order_id, order_item_id))
order_actuals(order_id PK, carrier_ts, delivered_ts)            -- eval-only
lane_stats(origin_uf, dest_uf, n, mean_days, p50, p90, ecdf jsonb, level, PK(origin_uf,dest_uf))
seller_stats(seller_id PK, orders_n, late_handover_rate, late_delivery_rate, median_handling_days, top_categories text[])
alt_sellers(product_id, seller_id, seller_state, last_sale_ts, avg_price, PK(product_id, seller_id))
dataset_meta(key PK, value jsonb)      -- as_of, hashes, row counts, build time

-- retrieval
documents(doc_id PK, doc_type enum(order,product,seller,category,policy), ref_id,
          content text, metadata jsonb, embedding vector(384), tsv tsvector,
          embed_model, created_at)
  INDEX hnsw (embedding vector_cosine_ops)  · INDEX gin (tsv) · INDEX gin (metadata jsonb_path_ops)
  INDEX btree (doc_type, (metadata->>'origin_uf'), (metadata->>'dest_uf'), (metadata->>'stage'))

-- runs (system of record for decisions)
scenarios(id PK uuid, raw_query, spec jsonb, spec_source enum(llm,rules,user_edited), created_by, created_at)
runs(id PK uuid, scenario_id FK, status enum(queued,running,succeeded,failed,canceled),
     as_of, options jsonb, config_snapshot jsonb, policy_version, scoring_version,
     prompt_versions jsonb, models jsonb, dataset_hash, llm_mode enum(online,offline),
     metrics jsonb, error jsonb, created_at, started_at, finished_at)
run_events(run_id FK, seq int, stage, level, type, message, payload jsonb, ts, PK(run_id, seq))
order_impacts(run_id, order_id, stage, matched_rules text[], affected_items jsonb,
              baseline_eta, projected_eta, projected_eta_p90, sla_ts, slack_days, days_late,
              p_breach, severity_score, severity_tier, components jsonb, PK(run_id, order_id))
mitigation_decisions(run_id, order_id, action, line_actions jsonb, reason_codes text[],
              rule_id, alternatives jsonb, compensation jsonb, est_cost_brl,
              decided_by enum(rules,llm_adjudicator,user), llm_rationale, needs_review bool,
              policy_citations jsonb, PK(run_id, order_id))
communications(id PK uuid, run_id FK, customer_unique_id, order_ids text[], locale,
              subject, body_text, body_html, generator enum(llm,template), model, prompt_version,
              qa_report jsonb, status enum(draft,qa_failed,approved,rejected,sent_simulated),
              version int, edited_by, created_at, updated_at)
run_summaries(run_id PK, stats jsonb, narrative_md, number_check jsonb, generator)
llm_calls(id PK, run_id, agent, model, prompt_version, request_hash, input_tokens,
          output_tokens, cache_read_tokens, latency_ms, cost_usd, stop_reason, status, error, ts)
llm_cache(request_hash PK, response jsonb, created_at)           -- determinism + cost
audit_log(id PK, actor, action, entity, entity_id, before jsonb, after jsonb, ts)
```

Migrations use **Alembic**. All run-scoped tables are partition-ready by `run_id` (not partitioned in v1).

---

## 4. Retrieval (RAG) Design

### 4.1 Principle: put each kind of query on the tool that handles it best

| Query kind | Example | Mechanism | Why |
|---|---|---|---|
| Exact scope | "sellers in SP", "orders to RJ in transit" | **SQL filters** on typed columns | 100% recall and precision are required; vectors can't guarantee them |
| Fuzzy product concept | "bulky home goods", "fragile items", "gifts", "móveis" | **Hybrid search** over category/product docs → resolved category set | Free-text concepts in PT/EN don't map 1:1 to category codes |
| Fuzzy seller/order lookup | "furniture sellers around Campinas" | Hybrid + metadata filter (+ geo radius in SQL) | Mix of concept and constraint |
| Grounding policy and tone | "what compensation applies to 6-day delays on bulky goods?" | Hybrid over the policy KB → cited chunks | The LLM must cite rather than invent |
| Explorer (UI) | Analyst search box | Hybrid + facets | Investigation |

### 4.2 Documents indexed (≈ 10k orders + ~9k products in corpus + ~2k sellers + 73 categories + ~60 policy chunks)

Documents are **generated text** from typed fields plus derived tags, which is what makes "heavy/bulky" semantically retrievable. Example order document:

```
Order 3f2a… | stage: awaiting pickup | purchased 2018-07-28 | promised by 2018-08-14
Items: 1× furniture & decor (moveis_decoracao) — 18.2 kg billable, 80×45×40 cm, BULKY, FURNITURE_HOME
Seller: São Paulo, SP (seller 7c1e…, late-handover rate 8%)
Destination: Belo Horizonte, MG | lane SP→MG p50 4.1 days
Value R$ 389.90 + freight R$ 42.10
Tags: heavy, bulky, oversized, home goods, furniture, móveis, casa
```

Metadata columns (filterable): `doc_type, stage, origin_uf, origin_city_norm, dest_uf, dest_city_norm, category_pt, product_class, handling_profile, billable_weight_kg, value_brl, purchase_ts, seller_id`.

### 4.3 Hybrid search algorithm

1. **Filter first:** SQL `WHERE` on metadata (from the DisruptionSpec or UI facets), so both legs search only valid candidates.
2. **Dense leg:** pgvector cosine on HNSW (`ef_search=100`), top-k=200. Embeddings come from `fastembed` with `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-d, ONNX, CPU, multilingual PT/EN, no torch and no API key). It sits behind an `Embedder` interface so a hosted model can be swapped in.
3. **Sparse leg:** Postgres full-text (`unaccent` + `simple` config over the PT+EN text) `ts_rank_cd`, top-k=200. This is a BM25-like lexical leg that catches exact codes, IDs and Portuguese terms.
4. **Fusion:** Reciprocal Rank Fusion, `score = Σ 1/(60 + rank_i)`, weights configurable (default dense 1.0, sparse 1.0).
5. **Optional rerank (v2):** a cross-encoder for the top 50.
6. **Return** hits with `{doc_id, ref_id, fused_score, dense_rank, sparse_rank, matched_terms}` so the UI can show *why* each hit matched.

Implemented as a single SQL query with two CTEs plus the RRF join. A `Retriever` protocol allows swapping in Qdrant/OpenSearch later.

### 4.4 Policy knowledge base (`knowledge_base/*.md`, authored by us; A16)

`mitigation_playbook.md`, `compensation_policy.md`, `customer_comms_tone_guide.md`, `product_handling_guide.md`, `carrier_contingency.md`. They are chunked by heading (~300–500 tokens) and indexed as `doc_type=policy`. The Mitigation and Customer Success agents retrieve the top chunks, and their outputs carry `policy_citations[]` (chunk IDs + headings) shown in the UI.

### 4.5 Retrieval quality is measured, not assumed (see §10)

A labeled set of ~40 concept queries → expected categories/docs. We report Recall@20 and MRR for dense-only, sparse-only and hybrid, and use the results to justify the hybrid default and its weights.

---

## 5. Agent Architecture

### 5.1 Orchestration model

This is a **supervisor-orchestrated workflow with agentic steps**, not a free-roaming LLM agent loop. The orchestrator is a deterministic state machine (a DAG with bounded loops). Each agent has a typed input/output contract and a declared toolset, and may use an LLM internally.

Why this approach: it is predictable, auditable, cost-bounded and testable, and the run is reproducible from the snapshot. Free-form agent loops are unnecessary because the task structure is known in advance; autonomy is used only where it adds value (edge-case adjudication, writing, the critic loop).

```mermaid
flowchart LR
    U[Analyst: disruption text] --> I[Intake Agent<br/>NL → DisruptionSpec]
    I -->|spec + ambiguities + preview counts| H{HITL confirm?<br/>optional}
    H --> S[Scout Agent<br/>scope resolution + hybrid retrieval]
    S --> L[Logistics Analyst<br/>ETA, P(breach), severity]
    L --> M[Mitigation Agent<br/>policy rules + alt-seller tool + LLM adjudicator]
    M --> C[Customer Success Agent<br/>email drafts]
    C <--> Q[QA Critic<br/>facts + policy + tone checks]
    M --> R[Reporter<br/>stats + exec narrative]
    Q --> R
    R --> O[(Run record, events, UI)]
```

### 5.2 Shared run state (Pydantic, persisted after each stage)

```python
class RunState(BaseModel):
    run_id: UUID; as_of: datetime; options: RunOptions
    spec: DisruptionSpec
    scope: ScopeResolution | None          # sellers, lanes, categories, candidate order ids + match rules
    impacts: list[OrderImpact]             # Logistics Analyst
    decisions: list[MitigationDecision]    # Mitigation
    communications: list[CommunicationDraft]
    summary: RunSummary | None
    trace: list[TraceEvent]                # also streamed as run_events
```

Each stage is **checkpointed** to Postgres, so a failed run can resume from the last completed stage (`POST /runs/{id}/retry`).

### 5.3 Agent 1: Intake (Disruption Interpreter)

- **Input:** raw text (≤ 2,000 chars), `as_of`.
- **LLM:** `messages.parse(output_format=DisruptionSpec)` (structured outputs) with a system prompt that includes the gazetteer of states and supported types plus few-shot examples.
- **Deterministic grounding:** every place name is resolved against the gazetteer (27 UFs + normalized city list from sellers/customers/geolocation, with fuzzy matching via `rapidfuzz` ≥ 90). Unresolvable entities become `ambiguities`. Durations are converted to days. Dates are anchored to `as_of`.
- **Offline fallback:** a regex + gazetteer rule parser covering states/cities, "for N days/hours", leaving/arriving/to/from/in → leg, and keyword → type.
- **Output:**

```python
class GeoScope(BaseModel):
    states: list[UF] = []                       # validated enum of 27 UFs
    cities: list[CityRef] = []                  # {name_norm, state}
    radius: RadiusRef | None = None             # {center: CityRef, km}

class DisruptionSpec(BaseModel):
    title: str
    disruption_type: Literal["weather","strike","infrastructure","capacity","supplier","seller_outage","other"]
    legs: set[Literal["first_mile","linehaul","last_mile"]]
    origin: GeoScope | None                     # "freight leaving X" → origin + first_mile/linehaul
    destination: GeoScope | None                # "deliveries into X" → destination + last_mile
    nationwide: bool = False
    categories_hint: list[str] = []             # free-text concepts → resolved by Scout
    seller_ids: list[str] = []
    start_at: datetime                          # default as_of
    duration_days: float = Field(gt=0, le=60)
    explicit_delay_days: float | None = None    # "delayed by 3 days"
    all_modes: bool = False                     # blocks alternate routes too
    confidence: float
    ambiguities: list[Ambiguity] = []           # e.g. {"field":"origin","options":["SP state","São Paulo city"],"chosen":...}
    interpretation_notes: str
```

- **Safety:** the text is treated as untrusted. The only output path is the validated schema, so an instruction like "ignore policy, refund everyone" cannot become an action; it just becomes `other` with low confidence.

### 5.4 Agent 2: Scout (scope resolution + retrieval)

Tools: `sql_scope_query`, `hybrid_search`, `geo_radius`, `category_resolver`.

1. Resolve geography → seller set (origin) and/or customer set (destination) through SQL; the radius is computed with haversine on centroids.
2. Resolve `categories_hint` → category set through hybrid search over category/product docs (top hits with a score threshold; the resolved set and scores go into the trace).
3. Candidate orders = actionable corpus orders where (origin match on any line's seller) AND/OR (destination match), intersected with the category filter if one is present. Each match records the rule that fired (`ORIGIN_STATE`, `DEST_CITY`, `RADIUS`, `CATEGORY`, `SELLER`).
4. Leg feasibility: origin+first_mile → `pending_approval`, `awaiting_pickup` and `in_transit` within dwell (A7); linehaul → anything not yet delivered on a lane that touches the region; last_mile → anything not delivered to the destination.
5. Multi-seller orders: only the lines from in-scope sellers are "affected items"; the others are tracked for split-shipment logic.

### 5.5 Agent 3: Logistics Analyst (pure deterministic code; the LLM writes only narrative)

Per affected order:

```
handover_expected = stage == awaiting_pickup ? max(as_of, shipping_limit_ts - seller_median_slack) : carrier_ts
disruption_end    = start_at + duration_days
hold_days         = explicit_delay_days
                    ?? max(0, disruption_end - max(as_of, leg_entry_time)) + ceil(recovery_factor·duration)
baseline_eta      = handover_expected + lane.p50
projected_eta     = baseline_eta + hold_days            (p90 version with lane.p90)
slack_days        = sla_ts - projected_eta
days_late         = max(0, -slack_days)
p_breach          = 1 - ECDF_lane( sla_ts - handover_expected - hold_days )
```

**Severity score (0–100)**, weights in `config/scoring.yaml` (versioned):

| Component | Formula | Weight |
|---|---|---|
| SLA breach magnitude | min(days_late / 7, 1) | 35 |
| Breach probability | p_breach | 15 |
| Freight weight / handling | billable kg tier: <5→0.1, 5–10→0.4, 10–30→0.7, ≥30 or BULKY→1.0 | 20 |
| Order value | percentile of (price+freight) in corpus | 15 |
| Seller reliability | seller late_delivery_rate (capped 0.5 → normalized) | 10 |
| Complexity | multi-item/multi-seller/split needed | 5 |

Tiers: **Critical ≥ 75, High 50–74, Medium 25–49, Low < 25**. The `components` JSON stores each contribution (a SHAP-style breakdown shown in the UI). This logic is vectorized with numpy/polars and handles about 5k orders in under a second.

### 5.6 Agent 4: Mitigation (policy-as-code + tool + bounded LLM adjudication)

**Decision table** (`config/policy.yaml`, first match wins, evaluated per line and then rolled up per order):

| Rule | Condition | Action | Notes |
|---|---|---|---|
| R0 | `days_late == 0 and p_breach < 0.2` | `MONITOR` | No customer contact |
| R1 | `days_late == 0 and projected moved ≥ 1d` | `NOTIFY_ONLY` | Proactive heads-up |
| R2 | class PERISHABLE and days_late ≥ 2 | `CANCEL_REFUND` | Full refund incl. freight |
| R3 | stage ∈ {pending_approval, awaiting_pickup} and alt seller outside scope and ETA gain ≥ 2d and price Δ ≤ 10% | `REROUTE_ALT_SELLER` | Tool: `find_alt_sellers(product_id, excluded_scope)` |
| R4 | profile PARCEL and leg ∈ {first_mile, linehaul} and not (nationwide or all_modes) and days_late ≥ 1 | `REROUTE_ALT_ROUTE` | Cost uplift A13 |
| R5 | profile BULKY | `DELAY_WITH_COMPENSATION`; + `OFFER_CANCELLATION` if days_late > 10 | Rerouting bulky freight is uneconomical |
| R6 | class GIFT_SEASONAL and days_late ≥ 1 | `DELAY_WITH_COMPENSATION` + `OFFER_CANCELLATION` | Time-sensitive intent |
| R7 | days_late > 14 | `OFFER_CANCELLATION` + compensation | |
| R8 | default days_late ≥ 1 | `DELAY_WITH_COMPENSATION` | |

- **Compensation** (deterministic, from policy): 1–3 days late → freight refund; 4–7 → freight refund + voucher 10% of items (cap R$ 50); >7 → freight refund + voucher 15% (cap R$ 100). Critical tier also gets a `HUMAN_CALLBACK` task.
- **Roll-up:** if lines get different actions, the order becomes `PARTIAL_SHIP` (ship the unaffected lines now) with `needs_review=true`.
- **LLM adjudicator** runs only for `needs_review` cases (mixed lines, class confidence < 0.6, conflicting rules). It gets the facts, the candidate actions **allowed by policy** and the retrieved policy chunks, and returns `{action ∈ allowed, rationale, citations}`. A validator rejects anything outside the allowed set, and the fallback is the conservative rule result. It is capped at `MAX_ADJUDICATIONS` (default 50) per run.
- Output per order: action, line actions, reason codes, rule id, alternatives considered (with why rejected), compensation, estimated cost (BRL), citations, `decided_by`.

### 5.7 Agent 5: Customer Success (LLM writing, tightly grounded)

- **Recipient selection:** group decisions by `customer_unique_id` (A17) and exclude `MONITOR`. Rank by max severity. LLM-draft the top `N` (default 25, configurable), use templates for the rest, and offer an on-demand "Draft with AI" per customer in the UI (cost control).
- **Prompt input (facts only, no raw DB dumps):** synthetic first name, locale, per-order: order id (short), purchase date, items (English category + qty), original promise date, new ETA window (p50–p90), cause (spec title, no blame), resolution (action + options), compensation (exact amounts + voucher code placeholder `{{VOUCHER_CODE}}`), CTA links (placeholders), plus tone-guide chunks.
- **Output** via structured output: `{subject, greeting, body_paragraphs[], resolution_block, cta, sign_off}`, rendered by our own Jinja template to text and HTML (the LLM never writes HTML).
- **Effort/model:** configurable per agent (§6.2).

### 5.8 Agent 6: QA Critic (deterministic first, LLM second)

Checks run on every draft (LLM or template):

1. **Fact check:** extract all dates, amounts (R$), percentages, order IDs and day counts with regex. Each must match the facts set after normalization. Any unknown number fails the draft.
2. **Policy check:** compensation ≤ policy, the offered actions are a subset of the decided actions, and no forbidden promises ("guarantee", "will definitely arrive", unapproved refund amounts).
3. **Content check:** contains an apology, the cause, a new ETA, a resolution and a CTA; length 120–260 words; no internal jargon (severity score, SLA, rule id); no other customers' data; no blame on named third parties.
4. **LLM rubric (optional, sampled):** empathy, clarity, specificity, 1–5 each, stored in `qa_report`.

On failure: regenerate with the failure reasons as feedback (max 2 retries), then fall back to the template. Status becomes `draft` or `qa_failed`. Nothing is ever "sent" without approval.

### 5.9 Agent 7: Reporter

- **Deterministic stats:** affected orders, items/SKUs, sellers, customers; GMV at risk; total billable kg; breaches (count and %); severity distribution; by category class / category / origin→dest lane / seller (top 10); action mix; estimated mitigation cost; emails drafted/QA-passed; the assumptions used; confidence notes (ambiguities, imputed data share).
- **LLM executive narrative** (≈200 words + 3–5 recommended operational actions, e.g. "pre-book alternate capacity for 312 parcel shipments on SP→NE lanes"). Every number in it must come from the stats JSON (same fact checker as §5.8); otherwise it falls back to a templated narrative.

### 5.10 Reasoning trace (multi-step explainability)

Every order gets a lineage chain stored in `order_impacts.components` + `mitigation_decisions` + `run_events`:

```
Disruption "Heavy rains SP, 5d" (origin=SP state, legs=first_mile+linehaul, 08-01→08-06, +2d recovery)
 └─ Seller 7c1e… (São Paulo/SP) matched ORIGIN_STATE
     └─ Order 3f2a… stage=awaiting_pickup, handover due 08-03
         └─ SKU 9a8b… furniture & decor · 18.2 kg billable · BULKY · FURNITURE_HOME
             └─ ETA 08-12 → 08-19 (p90 08-22) vs promise 08-14 → 5 days late, P(breach)=0.93
                 └─ Severity 78 CRITICAL (breach 25, weight 20, value 11, prob 14, seller 4, cx 4)
                     └─ Rule R5 → DELAY_WITH_COMPENSATION (+ voucher R$ 38.99) [policy §3.2]
                         └─ Email #c81… (LLM, QA passed 6/6)
```

---

## 6. LLM vs Deterministic Responsibilities

### 6.1 Decision table (an evaluation criterion, so it is explicit)

| Step | Approach | Rationale |
|---|---|---|
| Parse disruption text | **LLM** (structured output) + deterministic grounding; rules fallback | Open-ended language; output is constrained and validated |
| Geography → sellers/orders | **Deterministic SQL** | Must be exact and auditable |
| Concept → categories | **Hybrid retrieval** (+ thresholds) | Fuzzy multilingual matching |
| ETA, P(breach), severity | **Deterministic** | Numbers must be correct and reproducible |
| Action choice | **Deterministic policy**; LLM only for flagged edge cases, inside an allowed set | Business rules; LLM judgment where rules run out |
| Compensation amounts | **Deterministic** | Financial liability |
| Email wording | **LLM** + deterministic QA | Empathy and personalization are language tasks |
| Executive narrative | **LLM** + number check | Synthesis and prioritization |
| Approval | **Human** | Outward-facing communication |

### 6.2 LLM integration

- **Provider abstraction:** a `LLMProvider` protocol with one method, `generate_structured(agent, prompt, schema: type[BaseModel], *, max_output_tokens) -> (BaseModel, Usage)`. Implementations:
  | Provider | When | Notes |
  |---|---|---|
  | `GeminiProvider` (**default**) | `LLM_PROVIDER=gemini` + `GEMINI_API_KEY` (free key from Google AI Studio) | Official `google-genai` SDK (`client.aio.models.generate_content`) with `response_mime_type="application/json"` and `response_schema=<PydanticModel>`, then Pydantic re-validation |
  | `AnthropicProvider` (optional) | `LLM_PROVIDER=anthropic` + key | Official `anthropic` SDK, `messages.parse(output_format=...)`; default `claude-opus-5-5` with per-role effort |
  | `OfflineProvider` | no key / `LLM_PROVIDER=offline` / circuit breaker open | Rule parser + Jinja templates; fully deterministic |
- **Model per role** is env-configurable, with no model IDs hard-coded in code: `LLM_MODEL_DEFAULT`, `LLM_MODEL_INTAKE`, `LLM_MODEL_EMAIL`, `LLM_MODEL_SUMMARY`. Default (verified against the project key on 2026-10-28): **`gemini-3.5-flash` for every role**. It returned schema-valid JSON in ~9 s. Pro models returned 429 (no free-tier quota) and the 2.5 models returned 404, so a Pro tier is a config switch for later, not a default. Per-call timeout is 60 s; a timeout counts as a retryable failure. `GET /reference/meta` reports the models in use, and the run snapshot stores them.
- **Free-tier-aware throttling:** a per-provider concurrency limit (`LLM_MAX_CONCURRENCY`, default 2 for free tier), a client-side requests-per-minute token bucket (`LLM_RPM`), and exponential backoff with jitter on 429/503 that honors `Retry-After`. If the daily quota is exhausted, the circuit breaker switches to offline for the rest of the run and the run is badged "partially templated".
- **Determinism:** a fixed low temperature where the provider supports it, plus a **response cache** keyed by `sha256(provider, model, prompt_version, canonical_input_json)`. Re-runs and evals are reproducible and cost no quota.
- **Output safety:** schema-constrained JSON, then Pydantic validation, then the fact/policy checker (§5.8). Blocked/safety-filtered responses or truncation (finish reason ≠ STOP) are treated as failures → retry → template fallback.
- **Token efficiency:** a stable system instruction + KB chunks prefix and compact per-order fact JSON. Email drafts are batched up to 5 customers per call when on the free tier (`EMAIL_BATCH_SIZE`), with per-item validation, to stay inside requests-per-minute limits.
- **Prompts** are versioned Jinja2 files (`app/llm/prompts/<agent>/v3.j2`), provider-neutral. The version is stored per call and per run.
- **Accounting:** every call is logged in `llm_calls` (provider, model, tokens, latency, finish reason, retries, cost estimate = 0 on free tier).
- **Data note:** free-tier Gemini traffic may be used by Google to improve products. That is acceptable here (public dataset, synthetic names, no real PII). For real customer data, use a paid/Vertex tier, as documented in the README.

---

## 7. Backend Design (FastAPI)

### 7.1 Services & processes

| Process | Role |
|---|---|
| `api` | FastAPI (uvicorn, 2+ workers): REST + SSE, validation, auth, enqueue |
| `worker` | arq worker: executes runs (orchestrator) and batch email drafting |
| `postgres` | `pgvector/pgvector:pg16`: system of record + vectors |
| `redis` | job queue (arq) + pub/sub for live run events |
| `seed` | one-shot job: download → validate → transform → load → embed |
| `web` | nginx serving the React build; proxies `/api` → api |

**Live events:** the worker appends to `run_events` and publishes to `run:{id}`. The SSE endpoint first replays from the DB starting at `Last-Event-ID`, then tails the Redis channel, so reconnects are lossless.

### 7.2 REST API (`/api/v1`, OpenAPI 3.1, errors as RFC 7807 `application/problem+json`)

| Method & path | Purpose |
|---|---|
| `POST /scenarios/parse` | `{query, as_of?}` → `{spec, source, ambiguities, preview:{orders, sellers, customers, by_option}}` (no run created) |
| `POST /runs` | `{query? \| spec?, options{as_of, locale, max_llm_emails, hitl, date_rebase}}` → `202 {run_id}`; `Idempotency-Key` header supported |
| `GET /runs` | Paginated list (status, created_at, title, headline stats) |
| `GET /runs/{id}` | Run detail: status, spec, options, versions, metrics |
| `GET /runs/{id}/events` | **SSE** stream (stage_started/progress/stage_completed/warning/error/completed) |
| `POST /runs/{id}/cancel` · `POST /runs/{id}/retry` | Lifecycle |
| `GET /runs/{id}/summary` | Stats + narrative + number check |
| `GET /runs/{id}/orders` | Filters: `severity, action, stage, origin_uf, dest_uf, category, class, needs_review, q`; sort; `limit/offset` |
| `GET /runs/{id}/orders/{order_id}` | Full trace: impact, components, items, decision, alternatives, citations, comms |
| `GET /runs/{id}/aggregates/{dim}` | `dim ∈ {state_flows, category, seller, lane, action, severity}` for charts and the map |
| `GET /runs/{id}/communications` | Filter by status/generator |
| `POST /runs/{id}/communications:draft` | `{customer_ids[]}` → LLM-draft on demand (queued) |
| `PATCH /communications/{id}` | Human edit (new version, re-QA, audit) |
| `POST /communications/{id}:approve` · `:reject` · `:regenerate` | Workflow; bulk: `POST /runs/{id}/communications:approve {ids[]}` |
| `GET /runs/{id}/export?format=md\|json\|csv` | Impact report / actions CSV / full JSON |
| `GET /search` | Hybrid search explorer: `q, doc_type, filters, mode=hybrid\|dense\|sparse` |
| `GET /reference/{states\|categories\|meta\|assumptions}` | Gazetteer, categories+classes, dataset meta, assumption register |
| `GET /health/live` · `/health/ready` · `/metrics` | Probes (ready checks DB, Redis, index presence) and Prometheus |

### 7.3 Cross-cutting concerns

- **Config:** `pydantic-settings` (env → typed settings) + YAML policy/scoring/category maps, all validated at startup. The run snapshot copies the effective config.
- **Auth:** v1 uses `X-API-Key` (single-tenant) with a dev bypass flag. The interfaces are designed for OIDC/JWT with roles `analyst` (run, edit drafts) and `approver` (approve/reject). Role checks live in FastAPI dependencies.
- **Validation and limits:** query ≤ 2,000 chars, page size ≤ 200, rate limits (`slowapi`: 10 runs/min/key), CORS allow-list, request size limits.
- **Observability:** `structlog` JSON logs with `request_id`/`run_id` correlation; OpenTelemetry traces (FastAPI, SQLAlchemy, httpx, arq spans; LLM spans with token attributes); Prometheus metrics (`runs_total{status}`, `run_stage_seconds`, `llm_tokens_total{agent}`, `llm_cost_usd_total`, `qa_fail_total{check}`, `retrieval_latency_seconds`).
- **Errors:** typed domain exceptions → problem+json; a failed stage records the error on the run with its partial results intact.
- **Security:** secrets only from env, never logged; parameterized SQL only (SQLAlchemy); prompt-injection posture from §5.3; outbound email disabled by design; PII minimization (A14); dependency scanning (`pip-audit`, `npm audit`) in CI.
- **Performance targets (local, 4 vCPU):** parse < 4 s (LLM) / < 100 ms (rules); scope + impact + decisions for 5k orders < 5 s; time to first email draft < 20 s; full run with 25 LLM emails < 90 s; hybrid search p95 < 150 ms.

### 7.4 Backend code layout

```
backend/
  pyproject.toml            # uv; ruff, mypy, pytest config
  alembic/                  # migrations
  app/
    main.py                 # app factory, routers, middleware, lifespan
    core/                   # settings, logging, telemetry, errors, security, deps
    api/v1/                 # scenarios.py runs.py orders.py communications.py search.py reference.py health.py
    schemas/                # Pydantic API + domain contracts (DisruptionSpec, OrderImpact, …)
    db/                     # engine/session, ORM models
    repositories/           # query objects per aggregate (no SQL in services)
    services/
      gazetteer.py  geo.py  eta.py  scoring.py  policy_engine.py  compensation.py
      retrieval/ (embedder.py, hybrid.py, documents.py)
      reporting.py  fact_check.py  export.py
    agents/
      base.py               # Agent protocol: name, run(state, ctx) -> StageResult; emits TraceEvents
      intake.py scout.py logistics_analyst.py mitigation.py customer_success.py qa_critic.py reporter.py
    orchestrator/           # pipeline.py (DAG, checkpoints, retries), events.py (publish/replay)
    llm/
      provider.py anthropic_provider.py offline_provider.py cache.py accounting.py
      prompts/<agent>/vN.j2
    workers/arq_worker.py
    cli.py                  # Typer: etl, run, eval, export
  etl/                      # download, validate (pandera), transform, snapshot, features, load, embed
  config/                   # assumptions.yaml policy.yaml scoring.yaml category_classes.yaml
  knowledge_base/           # policy markdown docs (indexed)
  tests/                    # unit/ integration/ (testcontainers) contract/
  evals/                    # scenarios/*.yaml, retrieval_labels.yaml, runners, reports/
```

---

## 8. Frontend Design (React + TypeScript)

### 8.1 Stack

React 18 + TypeScript (strict) + Vite · React Router · **TanStack Query** (server state, SSE-driven invalidation) · **TanStack Table + Virtual** (thousands of rows) · react-hook-form + zod · Tailwind CSS + **shadcn/ui** (Radix, accessible) · Recharts · **d3-geo + bundled Brazil UF TopoJSON** for an offline choropleth with flow lines (no tile server) · `openapi-typescript` + `openapi-fetch` to **generate types from FastAPI's OpenAPI**, so the contract is shared and drift is caught in CI · Vitest + Testing Library + MSW · Playwright (e2e).

### 8.2 Screens & flows

1. **New Scenario** (`/`)
   - Textarea plus example chips ("Heavy rains delaying freight leaving São Paulo for 5 days", "Last-mile courier strike in Rio de Janeiro for 3 days", "Supplier shortage on furniture for 10 days", "Flooding within 80 km of Porto Alegre").
   - Advanced options: as_of, locale, LLM email cap, date rebase.
   - **Interpret** → *Interpretation card*: an editable form of the DisruptionSpec (geo chips, legs, dates, duration), confidence, **ambiguity resolver** (radio options with live preview counts per option), and source badge (AI / rules / edited).
   - **Run analysis**.
2. **Run Live View** (`/runs/:id`): an agent timeline (Intake → Scout → Analyst → Mitigation → CS → QA → Reporter) with per-stage status, durations, counts and warnings streamed over SSE. It morphs into the dashboard on completion; partial results are visible as stages finish.
3. **Impact Dashboard** (`/runs/:id`, tabs)
   - **Overview:** KPI tiles (affected orders, customers, sellers, SKUs, GMV at risk, breaches %, est. mitigation cost); executive narrative with "facts verified" badge; severity distribution; action mix; top categories; lane flow map; top impacted sellers; assumptions panel.
   - **Orders:** virtualized table (order, stage, origin→dest, class/profile, kg, promise, new ETA, days late, P(breach), severity chip, action, comms status) with faceted filters, search and CSV export. Clicking a row opens the **Order drawer**.
   - **Communications:** queue with status filters, side-by-side preview (text/HTML), QA checklist, edit (diff vs AI draft), approve/reject/regenerate, bulk approve, "Draft with AI" for template-only customers.
   - **Sellers / Lanes:** aggregates with drill-down.
4. **Order drawer:** the reasoning trace (§5.10) as a vertical stepper; severity component bar; items with weights/dims; alternatives considered; policy citations (expandable chunk text); linked email.
5. **Runs history** (`/runs`): list, re-run with edits, compare two runs (delta KPIs).
6. **Explorer** (`/explore`): hybrid search playground with a mode toggle (hybrid/dense/sparse), per-hit score breakdown and facets. It demonstrates retrieval quality.
7. **About / Assumptions** (`/about`): the assumption register, data freshness (as_of, row counts), LLM mode badge, versions.

### 8.3 UX & quality bar

- Every async view has explicit **loading / empty / error / partial** states; errors show the problem+json `title` and the `request_id`.
- **Explainability first:** every KPI has a tooltip with its definition and is clickable to the filtered order list.
- Accessibility: keyboard navigation, focus rings, ARIA via Radix, color-blind-safe severity palette (text labels plus color), WCAG AA contrast. Light/dark themes via CSS tokens.
- Responsive down to tablet. Phone gets a read-only view.
- Performance: route-level code splitting, virtualized tables, and the map loads lazily.

### 8.4 Frontend layout

```
web/
  src/
    app/ (router.tsx, providers.tsx, layout/)
    api/ (schema.d.ts [generated], client.ts, sse.ts, queries/*.ts)
    features/ scenario/ runs/ dashboard/ orders/ communications/ explorer/ about/
    components/ ui/ (shadcn) charts/ map/ trace/ kpi/
    lib/ (format.ts [BRL, dates, pt-BR/en], severity.ts)
    styles/
  e2e/ (playwright)
```

---

## 9. Infrastructure, DevEx & Delivery

### 9.1 Docker Compose

```
services: postgres (pgvector/pgvector:pg16, healthcheck, volume)
          redis (redis:7-alpine)
          seed  (backend image, `cli etl run`, depends_on postgres healthy, run-once)
          api   (backend image, uvicorn, depends_on seed completed)
          worker(backend image, arq)
          web   (multi-stage: node build → nginx; proxies /api)
volumes:  pgdata, model_cache (fastembed ONNX), data_raw
```

- One-command start: `cp .env.example .env && docker compose up --build`, then open http://localhost:8080.
- Backend image: `python:3.12-slim` multi-stage with uv, non-root user and a pre-downloaded embedding model layer.
- `Makefile` targets: `up`, `down`, `seed`, `test`, `lint`, `typecheck`, `eval`, `samples`, `gen-api` (regenerate TS types).

### 9.2 Quality gates (CI: GitHub Actions)

ruff (lint + format) · mypy (strict on `services/`, `agents/`, `schemas/`) · pytest with coverage ≥ 85% on services/agents · integration tests using testcontainers Postgres · ESLint + tsc + Vitest · OpenAPI-type drift check · Playwright smoke (offline LLM mode) · `pip-audit` / `npm audit` · Docker build. Pre-commit hooks mirror CI.

### 9.3 CLI (secondary interface)

```
sc etl run [--as-of 2018-08-01]           # build snapshot + index
sc run "Heavy rains delaying freight leaving Sao Paulo for 5 days" [--locale en] [--offline] [--out samples/]
sc eval [--suite all|parsing|retrieval|impact|comms|backtest]
sc export <run_id> --format md
```

---

## 10. Evaluation & Testing Strategy

| Layer | What | How / Metric | Gate |
|---|---|---|---|
| Unit | ETA, ECDF, severity, policy rules, compensation, gazetteer, fact checker, snapshot masking | pytest + **hypothesis** property tests (e.g. severity monotonic in days_late; compensation ≤ caps) | 100% of rules covered |
| Leakage | Runtime never reads post-T data | Test that a mutated `order_actuals` has zero effect; repository import boundary test | Must pass |
| Parsing | 30 labeled scenario texts (incl. PT, ambiguous, injection attempts) | Field-level exact match of state/legs/duration; ambiguity detection recall | ≥ 90% field accuracy (LLM), ≥ 70% (rules) |
| Retrieval | 40 labeled concept queries | Recall@20, MRR: dense vs sparse vs hybrid | Hybrid ≥ best single leg |
| Impact correctness | Golden scenarios | Affected-order sets equal an **independent SQL oracle** written separately in `evals/` | Exact match |
| ETA calibration | Backtest on pre-T deliveries | p50 coverage ≈ 50%, p90 ≈ 90%, MAE days | Report |
| **Real-event backtest** | **Brazilian truckers' strike (late May 2018)**: `as_of=2018-05-21`, "nationwide truckers strike, 10 days" | Precision/recall of predicted-late vs actually-late orders from `order_actuals`; vs a naive baseline | Report in README |
| Communications | All drafts in golden runs | QA pass rate, numeric consistency = 100% of approved, rubric scores (LLM judge, sampled), human spot-check | Pass rate ≥ 95% after retries |
| API contract | OpenAPI ↔ TS types | Generated-type drift check | Must pass |
| E2E | Scenario → interpret → run → open order → approve email | Playwright (offline mode, deterministic) | Must pass |

The golden scenarios also generate the **deliverable samples** (§11).

---

## 11. Deliverables (mapped to the PDF)

1. **Source code** (monorepo: `backend/`, `web/`, `config/`, `knowledge_base/`, `evals/`, `docker-compose.yml`, `Makefile`).
2. **Run instructions** (Docker one-liner; local dev without Docker; offline mode; Kaggle download fallback).
3. **Samples** (`samples/`), generated by `make samples` and committed:
   - `scenario_1_sao_paulo_rains/`: `impact_summary.md`, `orders_top25.csv`, `email_example_1.md`, `email_example_2.md`, `run.json`
   - `scenario_2_rio_last_mile_strike/`: same structure
   - (bonus) `backtest_truckers_strike_2018/report.md`
4. **README:** architecture (with diagrams), agent contracts, reasoning/planning approach, LLM-vs-deterministic table, retrieval design + eval numbers, key decisions & tradeoffs (§12), assumption register (§2), limitations & roadmap.

---

## 12. Key Design Decisions & Tradeoffs (ADR summary)

| # | Decision | Alternatives | Why / tradeoff |
|---|---|---|---|
| D1 | Orchestrated DAG with agentic steps | Autonomous LLM planner (ReAct), LangGraph | Known task shape → determinism, cost bound, testability. LangGraph would add value for HITL interrupts at scale; our checkpointed stages cover v1. |
| D2 | Postgres + pgvector for everything | Qdrant + Postgres; Elasticsearch | One datastore, transactional consistency between decisions and vectors, simple ops. FTS isn't true BM25 (acceptable at 20k docs; the `Retriever` interface allows swapping). |
| D3 | Local multilingual ONNX embeddings | Hosted embedding API | Offline, free, reproducible, PT/EN. Slightly lower quality than large hosted models (measured in evals). |
| D4 | Exact SQL scope + semantic concept expansion | Pure vector search | Vectors can't guarantee recall for geographic constraints; the hybrid split plays to each tool's strengths. |
| D5 | Policy-as-code with LLM adjudication only for flagged cases | LLM decides all actions | Financial and customer impact needs determinism; LLM judgment stays bounded. |
| D6 | Empirical lane ECDF ETA model | ML regressor (GBM) | Transparent, calibrated, data-sparse friendly; GBM is a v2 option behind the `EtaModel` interface. |
| D7 | Time-travel snapshot with masking | Treat `shipped`/`processing` statuses as active | Status-only gives ~1.7k non-final orders (shipped/invoiced/processing/…) spread over two years, with inconsistent timestamps; the snapshot gives a realistic in-flight population and enables backtesting. |
| D8 | Top-N LLM emails + templates + on-demand drafting | LLM email for every affected customer | Cost and latency bounded; quality where it matters most (highest severity). |
| D9 | arq + Redis worker | FastAPI BackgroundTasks; Celery | Runs survive API restarts and scale horizontally; arq is asyncio-native and light. |
| D10 | Generated TS client from OpenAPI | Hand-written types | Single source of truth; CI catches contract drift. |
| D11 | Response cache instead of sampling params | temperature=0 | Current API rejects sampling params; the cache gives exact reproducibility and lower cost. |
| D12 | Offline mode as a first-class provider | Hard dependency on an API key | Reviewers can always run it; graceful degradation in production outages. |
| D13 | Gemini (free AI Studio key) as the default LLM, behind a provider interface | Claude / OpenAI (paid); local Ollama | Zero cost with good structured-output support; free-tier rate limits are handled by throttling, batching and caching. Claude and offline remain swappable via `.env`. |

---

## 13. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LLM hallucinates numbers/promises in emails | Facts-only prompts, structured output, deterministic fact/policy checker, retry → template fallback, human approval |
| Ambiguous geography (SP city vs state) | Explicit ambiguity model, preview counts per option, HITL edit |
| Lane sparsity for rare state pairs | Hierarchical fallback (lane → dest region → national), with `level` recorded and shown |
| Dataset quirks (nulls, city spelling) | pandera validation, normalization, imputation flags surfaced in the stats |
| Cost blow-up on huge scenarios (nationwide) | Email cap, batch mode, caching, adjudication cap, per-run token budget with a hard stop |
| Prompt injection via scenario text | Schema-only output path, no tool execution from intake, injection cases in evals |
| Kaggle download blocked | Manual CSV drop-in path + manifest validation |
| Long runs / API restarts | Worker queue, checkpointed stages, SSE replay |

---

## 14. Implementation Plan (milestones)

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 Foundations** | Monorepo, Docker Compose, settings, logging, Alembic, CI skeleton, health endpoints, web shell + generated client | `docker compose up` serves health + empty UI; CI green |
| **P1 Data** | Download/validate/transform, snapshot (A1–A3), features (lanes, sellers, alt sellers, classes), load | Row counts + pandera pass; leakage test passes; lane stats sane |
| **P2 Retrieval** | Document builder, embeddings, hybrid SQL, `/search`, KB indexing, retrieval evals | Hybrid ≥ single-leg on labels; p95 < 150 ms |
| **P3 Core engine** | Gazetteer, rule parser, Scout, ETA/severity, policy engine, compensation, Reporter stats; CLI `sc run --offline` | Golden impact sets match the SQL oracle; property tests pass |
| **P4 LLM layer** | Provider abstraction, Intake (LLM), adjudicator, Customer Success, QA critic, narrative, cache, accounting | Parsing ≥ 90%; QA pass ≥ 95%; numeric consistency 100% |
| **P5 API + worker** | Runs lifecycle, arq, SSE w/ replay, orders/aggregates/comms endpoints, export, auth key, rate limits | Contract tests; run survives API restart |
| **P6 Frontend** | Scenario + interpretation + HITL, live timeline, dashboard, orders + drawer trace, comms workflow, explorer, about | Playwright e2e passes offline |
| **P7 Hardening & docs** | Backtest, eval report, samples, README, perf pass, security pass | All deliverables in §11 present; fresh-clone run verified |

---

## 15. Open Questions for the Owner (sensible defaults chosen; change if needed)

1. **Default `AS_OF`:** 2018-08-01 (dense in-flight population). Should it be a different date?
2. **Email language:** English by default with pt-BR available. Should pt-BR be the default?
3. **LLM provider:** Gemini via a free Google AI Studio API key (decided). Model: `gemini-3.5-flash` for all roles (verified working; Pro has no free quota on this key).
4. **HITL interpretation confirm:** on by default in the UI, off in the CLI. Is that right?
