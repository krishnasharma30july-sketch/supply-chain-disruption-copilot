// Mirrors backend/app/api/schemas.py and backend/app/domain/models.py.

export type Tier = "critical" | "high" | "medium" | "low";
export type Leg = "first_mile" | "linehaul" | "last_mile";
export type Role = "origin" | "destination";
export type DisruptionType =
  | "weather" | "strike" | "infrastructure" | "capacity" | "supplier" | "seller_outage" | "other";
export type Action =
  | "MONITOR" | "NOTIFY_ONLY" | "CANCEL_REFUND" | "REROUTE_ALT_SELLER" | "REROUTE_ALT_ROUTE" | "DELAY_WITH_COMPENSATION";

export interface CityRef { name: string; state: string; label: string }
export interface RadiusRef { center: CityRef; km: number; lat: number; lng: number }
export interface GeoScope { states: string[]; cities: CityRef[]; radius: RadiusRef | null }
export interface AmbiguityOption { label: string; kind: "state" | "city"; state: string; city: CityRef | null }
export interface Ambiguity { mention: string; roles: Role[]; options: AmbiguityOption[]; chosen: number; reason: string }

export interface DisruptionSpec {
  title: string;
  disruption_type: DisruptionType;
  legs: Leg[];
  origin: GeoScope;
  destination: GeoScope;
  match_mode: "any" | "lane";
  nationwide: boolean;
  category_terms: string[];
  seller_ids: string[];
  start_at: string;
  duration_days: number;
  explicit_delay_days: number | null;
  recovery_days: number;
  all_modes: boolean;
  confidence: number;
  ambiguities: Ambiguity[];
  notes: string[];
  source: "llm" | "rules" | "user";
}

export interface PreviewCounts {
  orders_in_scope: number;
  orders_delayed: number;
  orders_breaching: number;
  customers_delayed: number;
  sellers_delayed: number;
  items_delayed: number;
  gmv_delayed_brl: number;
  warnings: string[];
}

export interface OptionPreview { ambiguity_index: number; option_index: number; label: string; preview: PreviewCounts }

export interface ParseResponse {
  spec: DisruptionSpec;
  source: string;
  model: string | null;
  warnings: string[];
  preview: PreviewCounts;
  option_previews: OptionPreview[];
}

export interface RunOptions { locale: "en" | "pt-BR"; max_llm_emails: number | null; date_rebase: boolean; use_llm: boolean }

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "canceled";

export interface Run {
  id: string;
  title: string;
  raw_query: string | null;
  status: RunStatus;
  spec: DisruptionSpec | null;
  spec_source: string | null;
  options: RunOptions;
  llm_mode: string;
  metrics: Record<string, any>;
  error: { stage?: string; message?: string } | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  config_snapshot?: Record<string, any>;
}

export interface Page<T> { total: number; items: T[] }

export interface RunEvent {
  seq: number;
  stage: string;
  type: string;
  level: "info" | "warning" | "error";
  message: string;
  payload: Record<string, any>;
  ts: string;
}

export interface OrderRow {
  order_id: string;
  customer_unique_id: string;
  stage: string;
  origin_states: string[];
  dest_state: string;
  categories: string[];
  product_classes: string[];
  handling_profile: string;
  value_brl: number;
  billable_weight_kg: number;
  affected: boolean;
  hold_days: number;
  days_late: number;
  resolution_days_late: number;
  p_breach: number;
  severity_score: number;
  severity_tier: Tier;
  action: Action;
  offer_cancellation: boolean;
  needs_review: boolean;
  decided_by: string;
  est_cost_brl: number;
  sla_ts: string;
  projected_eta: string;
  resolution_eta: string;
}

export interface QACheck { name: string; passed: boolean; detail: string }

export interface Communication {
  id: string;
  run_id: string;
  customer_unique_id: string;
  order_ids: string[];
  first_name: string;
  locale: string;
  subject: string;
  body: string;
  generator: "llm" | "template" | "human";
  model: string | null;
  prompt_version: string | null;
  status: "draft" | "qa_failed" | "approved" | "sent" | "rejected";
  qa: { passed?: boolean; checks?: QACheck[]; generator?: string };
  max_severity: number;
  version: number;
  attempts: number;
  created_at: string;
  updated_at: string;
  email_to: string | null;
  approved_by: string | null;
  sent_at: string | null;
  message_id: string | null;
  send_error: string | null;
}

export interface AuthConfig {
  auth_enabled: boolean;
  signup_enabled: boolean;
  signup_domains: string[];
  demo_accounts: { email: string; role: string; password: string }[];
}

export interface AdminUser {
  id: string;
  email: string;
  name: string;
  role: "analyst" | "approver" | "admin";
  active: boolean;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: { email: string; name: string; role: "analyst" | "approver" | "admin" };
}

export interface TraceStep { kind: string; title: string; detail: string }

export interface SeverityComponent { name: string; value: number; weight: number; points: number; explanation: string }

export interface LineImpact {
  order_item_id: number;
  product_id: string;
  seller_id: string;
  seller_city: string;
  seller_state: string;
  category_en: string;
  category_pt: string | null;
  product_class: string;
  handling_profile: string;
  billable_weight_kg: number;
  price: number;
  freight: number;
  affected: boolean;
  lane: string;
  hold_days: number;
  legs: { leg: Leg; hold_days: number; note: string }[];
  p_breach: number;
}

export interface AltOption {
  kind: "alt_seller" | "alt_route";
  order_item_id: number;
  seller_id: string | null;
  seller_state: string | null;
  seller_city: string | null;
  eta: string | null;
  gain_days: number;
  price_delta_pct: number | null;
  extra_cost_brl: number;
  accepted: boolean;
  reason: string;
}

export interface Decision {
  action: Action;
  rule_id: string;
  reason_codes: string[];
  explanation: string;
  line_decisions: { order_item_id: number; action: Action; rule_id: string; reason: string }[];
  split_shipment: boolean;
  offer_cancellation: boolean;
  alternatives: AltOption[];
  resolution_eta: string;
  resolution_eta_p90: string;
  resolution_days_late: number;
  compensation: {
    tier_label: string;
    freight_refund_brl: number;
    voucher_pct: number;
    voucher_brl: number;
    refund_total_brl: number;
    human_callback: boolean;
  };
  est_cost_brl: number;
  needs_review: boolean;
  review_reasons: string[];
  decided_by: string;
  llm_rationale: string | null;
  policy_refs: string[];
}

export interface OrderDetail {
  row: OrderRow;
  impact: {
    lines: LineImpact[];
    severity_components: SeverityComponent[];
    baseline_eta: string;
    projected_eta: string;
    projected_eta_p90: string;
    p_breach_baseline: number;
    cust_city: string;
    cust_state: string;
    purchase_ts: string;
    [k: string]: any;
  };
  decision: Decision;
  trace: TraceStep[];
  policy_citations: { id: string; title: string; text: string }[];
  communication: Communication | null;
}

export interface RetrievalHit {
  doc_type: string;
  ref_id: string;
  title: string;
  snippet: string;
  score: number;
  dense_rank: number | null;
  sparse_rank: number | null;
  rerank_score?: number | null;
}

export interface Summary {
  run_id: string;
  stats: Record<string, any>;
  retrieval: {
    concepts?: { term: string; matches: { kind: string; value: string; label: string; score: number; via: string }[] }[];
    queries?: { purpose: string; query: string; doc_types: string[]; restricted_to_scope: boolean; hits: RetrievalHit[] }[];
    scope_check?: { n: number; retrieved?: number; verified?: number; recall: number | null; precision: number | null };
    mode?: string;
  };
  headline: string;
  narrative: string;
  recommendations: string[];
  number_check: { checked?: boolean; problems?: string[]; fallback?: string };
  generator: string;
}

export interface SearchHit extends RetrievalHit { doc_id: string; metadata: Record<string, any> }
export interface SearchResponse { query: string; mode: string; effective_mode: string; hits: SearchHit[] }

export interface Meta {
  dataset: {
    as_of: string;
    corpus_size: number;
    counts: Record<string, any>;
    built_at: string;
    embed_model: string;
  } | null;
  dataset_ready: boolean;
  llm: {
    mode: string;
    available: boolean;
    provider: string;
    models: Record<string, string>;
    max_llm_emails: number;
    breaker_reason: string | null;
  };
  versions: Record<string, any>;
  embed_model: string;
}

export interface Assumptions {
  version: string;
  params: Record<string, number>;
  items: { id: string; title: string; statement: string; impact: string }[];
}
