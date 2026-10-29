import { getToken, setSession, type SessionUser } from "./session";
import type {
  AdminUser,
  Assumptions,
  AuthConfig,
  Communication,
  LoginResponse,
  DisruptionSpec,
  Meta,
  OrderDetail,
  OrderRow,
  Page,
  ParseResponse,
  PreviewCounts,
  Run,
  RunEvent,
  RunOptions,
  SearchResponse,
  Summary,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public title: string, public detail: string, public requestId?: string) {
    super(detail || title);
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e: any) => `${(e.loc || []).filter((p: unknown) => p !== "body").join(".")}: ${e.msg}`)
      .join("; ");
  }
  return detail ? JSON.stringify(detail) : "";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let body: any = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    if (res.status === 401 && token) setSession(null); // expired or revoked: back to the sign-in page
    throw new ApiError(res.status, body?.title || res.statusText, formatDetail(body?.detail), body?.request_id);
  }
  return (await res.json()) as T;
}

/** EventSource and download links cannot send headers: GET endpoints also accept the token as a query parameter. */
function withToken(url: string): string {
  const token = getToken();
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(token)}`;
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => sp.append(k, String(x)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  authConfig: () => request<AuthConfig>("/auth/config"),
  login: (email: string, password: string) => post<LoginResponse>("/auth/login", { email, password }),
  signup: (name: string, email: string, password: string) => post<LoginResponse>("/auth/signup", { name, email, password }),
  me: () => request<SessionUser>("/auth/me"),
  users: () => request<AdminUser[]>("/users"),
  updateUser: (id: string, patch: { role?: AdminUser["role"]; active?: boolean }) =>
    request<AdminUser>(`/users/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),

  meta: () => request<Meta>("/reference/meta"),
  assumptions: () => request<Assumptions>("/reference/assumptions"),
  states: () => request<{ states: { code: string; name: string }[]; regions: Record<string, string[]> }>("/reference/states"),

  parse: (query: string, use_llm: boolean) => post<ParseResponse>("/scenarios/parse", { query, use_llm }),
  preview: (spec: DisruptionSpec) => post<{ spec: DisruptionSpec; preview: PreviewCounts }>("/scenarios/preview", { spec }),

  createRun: (body: { query?: string; spec?: DisruptionSpec; options: RunOptions }) => post<Run>("/runs", body),
  runs: (limit = 20, offset = 0) => request<Page<Run>>(`/runs${qs({ limit, offset })}`),
  run: (id: string) => request<Run>(`/runs/${id}`),
  cancelRun: (id: string) => post<Run>(`/runs/${id}/cancel`),
  retryRun: (id: string) => post<Run>(`/runs/${id}/retry`),
  events: (id: string, after = 0) => request<RunEvent[]>(`/runs/${id}/events/list${qs({ after })}`),
  summary: (id: string) => request<Summary>(`/runs/${id}/summary`),
  orders: (id: string, params: Record<string, unknown>) => request<Page<OrderRow>>(`/runs/${id}/orders${qs(params)}`),
  order: (id: string, orderId: string) => request<OrderDetail>(`/runs/${id}/orders/${orderId}`),
  communications: (id: string, params: Record<string, unknown>) =>
    request<Page<Communication>>(`/runs/${id}/communications${qs(params)}`),
  bulkApprove: (id: string, ids: string[]) => post<{ updated: number; skipped: number }>(`/runs/${id}/communications/approve`, { ids }),
  bulkDraft: (id: string, ids: string[]) => post<{ updated: number; skipped: number }>(`/runs/${id}/communications/draft`, { ids }),

  editCommunication: (id: string, subject: string, body: string) =>
    request<Communication>(`/communications/${id}`, { method: "PATCH", body: JSON.stringify({ subject, body }) }),
  approve: (id: string) => post<Communication>(`/communications/${id}/approve`),
  resend: (id: string) => post<Communication>(`/communications/${id}/send`),
  reject: (id: string) => post<Communication>(`/communications/${id}/reject`),
  regenerate: (id: string) => post<Communication>(`/communications/${id}/regenerate`),

  search: (params: Record<string, unknown>) => request<SearchResponse>(`/search${qs(params)}`),
  exportUrl: (id: string, format: "md" | "csv" | "json") => withToken(`${BASE}/runs/${id}/export?format=${format}`),
  eventsUrl: (id: string) => withToken(`${BASE}/runs/${id}/events`),
};
