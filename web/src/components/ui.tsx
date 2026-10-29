import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Action, Tier } from "../api/types";
import { ACTION_LABELS, ACTION_STYLES, TIER_STYLES } from "../lib/format";

type Variant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "primary",
  loading,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2 text-sm font-medium transition-colors",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-signal-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white",
        "disabled:cursor-not-allowed disabled:opacity-45",
        variant === "primary" && "bg-brand-700 text-white shadow-sm hover:bg-brand-800 active:bg-brand-900",
        variant === "secondary" && "border border-slate-300 bg-white text-slate-800 shadow-sm hover:border-brand-400 hover:text-brand-800",
        variant === "ghost" && "text-slate-700 hover:bg-slate-200/60",
        variant === "danger" && "bg-red-700 text-white hover:bg-red-800",
        className,
      )}
    >
      {loading && <Spinner small />}
      {children}
    </button>
  );
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-xl border border-slate-200 bg-white shadow-panel", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-200/80 bg-slate-50/60 px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-[13px] font-semibold text-slate-800">
            <span aria-hidden className="h-3 w-0.5 rounded-full bg-signal-500" />
            {title}
          </h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Badge({ children, className, title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={clsx("inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", className ?? "bg-slate-100 text-slate-700 ring-slate-200")}
    >
      {children}
    </span>
  );
}

export function TierBadge({ tier, score }: { tier: Tier; score?: number }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold capitalize", TIER_STYLES[tier])}>
      {tier}
      {score !== undefined && <span className="font-normal opacity-90">{Math.round(score)}</span>}
    </span>
  );
}

export function ActionBadge({ action }: { action: Action }) {
  return <Badge className={ACTION_STYLES[action]}>{ACTION_LABELS[action]}</Badge>;
}

export function Spinner({ small }: { small?: boolean }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={clsx("inline-block animate-spin rounded-full border-2 border-current border-t-transparent", small ? "h-3.5 w-3.5" : "h-5 w-5")}
    />
  );
}

export function Loading({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 p-6 text-sm text-slate-500">
      <Spinner /> {label}
    </div>
  );
}

export function ErrorBox({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  if (!error) return null;
  const e = error as Partial<ApiError> & { message?: string };
  return (
    <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      <p className="font-semibold">{e.title || title}</p>
      <p className="mt-0.5 whitespace-pre-wrap">{e.detail || e.message || String(error)}</p>
      {e.requestId && <p className="mt-1 text-xs text-red-600">Request ID: {e.requestId}</p>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warning"; children: ReactNode }) {
  return (
    <div
      className={clsx(
        "rounded-lg border p-3 text-sm",
        tone === "info" ? "border-brand-200 bg-brand-50 text-brand-900" : "border-signal-200 bg-signal-50 text-signal-900",
      )}
    >
      {children}
    </div>
  );
}

export function Kpi({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: string; tone?: "danger" | "warn" }) {
  return (
    <div
      className={clsx(
        "relative overflow-hidden rounded-xl border border-slate-200 bg-white p-4 shadow-panel",
        "before:absolute before:inset-y-0 before:left-0 before:w-1",
        tone === "danger" ? "before:bg-red-600" : tone === "warn" ? "before:bg-signal-500" : "before:bg-brand-600",
      )}
      title={hint}
    >
      <p className="label">{label}</p>
      <p className={clsx(
        "mt-1.5 whitespace-nowrap font-semibold leading-tight tabular-nums tracking-tight text-slate-900",
        // Long figures (currency) step down a size so they never wrap mid-number in a four-column row.
        typeof value === "string" && value.length > 9 ? "text-xl" : "text-2xl",
        tone === "danger" && "text-red-700", tone === "warn" && "text-signal-700",
      )}>
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b border-slate-300/70">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            "-mb-px border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
            value === t.id ? "border-signal-500 text-brand-900" : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-900",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">{children}</div>;
}
