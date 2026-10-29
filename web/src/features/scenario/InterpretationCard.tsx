import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { api } from "../../api/client";
import type { DisruptionSpec, DisruptionType, GeoScope, Leg, ParseResponse, Role } from "../../api/types";
import { Badge, Card, Notice } from "../../components/ui";
import { LEG_LABELS, fmtInt } from "../../lib/format";

const TYPES: DisruptionType[] = ["weather", "strike", "infrastructure", "capacity", "supplier", "seller_outage", "other"];
const LEGS: Leg[] = ["first_mile", "linehaul", "last_mile"];

function Chip({ children, onRemove }: { children: ReactNode; onRemove?: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-800 ring-1 ring-brand-200">
      {children}
      {onRemove && (
        <button aria-label="Remove" onClick={onRemove} className="ml-0.5 rounded-full px-1 text-brand-500 hover:bg-brand-100 hover:text-brand-900">
          ×
        </button>
      )}
    </span>
  );
}

function GeoEditor({
  label,
  geo,
  onChange,
  states,
}: {
  label: string;
  geo: GeoScope;
  onChange: (g: GeoScope) => void;
  states: { code: string; name: string }[];
}) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        {geo.states.map((s) => (
          <Chip key={s} onRemove={() => onChange({ ...geo, states: geo.states.filter((x) => x !== s) })}>
            {s} (state)
          </Chip>
        ))}
        {geo.cities.map((c) => (
          <Chip key={`${c.name}-${c.state}`} onRemove={() => onChange({ ...geo, cities: geo.cities.filter((x) => x !== c) })}>
            {c.label} (city)
          </Chip>
        ))}
        {geo.radius && (
          <Chip onRemove={() => onChange({ ...geo, radius: null })}>
            ≤ {geo.radius.km} km of {geo.radius.center.label}
          </Chip>
        )}
        <select
          aria-label={`Add ${label.toLowerCase()} state`}
          value=""
          onChange={(e) => e.target.value && onChange({ ...geo, states: [...geo.states, e.target.value] })}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs"
        >
          <option value="">+ state</option>
          {states.filter((s) => !geo.states.includes(s.code)).map((s) => (
            <option key={s.code} value={s.code}>
              {s.code} – {s.name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export default function InterpretationCard({
  parsed,
  spec,
  onChange,
}: {
  parsed: ParseResponse;
  spec: DisruptionSpec;
  onChange: (s: DisruptionSpec) => void;
}) {
  const statesQ = useQuery({ queryKey: ["states"], queryFn: api.states, staleTime: Infinity });
  const states = statesQ.data?.states ?? [];
  const [term, setTerm] = useState("");
  const update = (patch: Partial<DisruptionSpec>) => onChange({ ...spec, ...patch, source: "user" });

  // Removing a place that came from an ambiguity must also drop that ambiguity for the role; otherwise the server
  // re-applies the chosen option on the next preview and the chip reappears.
  const updateGeo = (role: Role, geo: GeoScope) => {
    const before = role === "origin" ? spec.origin : spec.destination;
    const removedStates = before.states.filter((s) => !geo.states.includes(s));
    const removedCities = before.cities.filter((c) => !geo.cities.some((x) => x.name === c.name && x.state === c.state));
    const ambiguities = spec.ambiguities
      .map((a) => {
        const touched = a.options.some(
          (o) =>
            (o.kind === "state" && removedStates.includes(o.state)) ||
            (o.kind === "city" && o.city && removedCities.some((c) => c.name === o.city!.name && c.state === o.city!.state)),
        );
        return touched ? { ...a, roles: a.roles.filter((r) => r !== role) } : a;
      })
      .filter((a) => a.roles.length > 0);
    update(role === "origin" ? { origin: geo, ambiguities } : { destination: geo, ambiguities });
  };

  // Preview counts were computed at interpretation time; look them up by mention (indexes shift if an ambiguity is removed).
  const optionPreview = (mention: string, oi: number) => {
    const ai = parsed.spec.ambiguities.findIndex((a) => a.mention === mention);
    if (ai < 0) return undefined;
    if (oi === parsed.spec.ambiguities[ai].chosen) return parsed.preview;
    return parsed.option_previews.find((p) => p.ambiguity_index === ai && p.option_index === oi)?.preview;
  };

  return (
    <Card
      title="2. Check the interpretation"
      actions={
        <div className="flex items-center gap-2">
          <Badge className={spec.source === "llm" ? "bg-emerald-50 text-emerald-800 ring-emerald-300" : spec.source === "user" ? "bg-brand-50 text-brand-800 ring-brand-300" : "bg-slate-100 text-slate-700 ring-slate-300"}>
            {spec.source === "llm" ? `AI (${parsed.model ?? "LLM"})` : spec.source === "user" ? "Edited by you" : "Deterministic parser"}
          </Badge>
          <Badge title="Interpreter confidence">confidence {Math.round(spec.confidence * 100)}%</Badge>
        </div>
      }
    >
      <div className="space-y-5">
        {parsed.warnings.length > 0 && (
          <Notice tone="warning">
            <ul className="list-disc pl-4">
              {parsed.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          </Notice>
        )}

        {spec.ambiguities.map((amb, ai) => (
          <fieldset key={amb.mention} className="rounded-lg border border-amber-300 bg-amber-50 p-3">
            <legend className="px-1 text-sm font-semibold text-amber-900">Ambiguous place: “{amb.mention}”</legend>
            <p className="text-xs text-amber-900">{amb.reason}</p>
            <div className="mt-2 space-y-1.5">
              {amb.options.map((opt, oi) => {
                const p = optionPreview(amb.mention, oi);
                return (
                  <label key={opt.label} className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="radio"
                      name={`amb-${ai}`}
                      checked={amb.chosen === oi}
                      onChange={() =>
                        update({ ambiguities: spec.ambiguities.map((a, i) => (i === ai ? { ...a, chosen: oi } : a)) })
                      }
                    />
                    <span className="font-medium">{opt.label}</span>
                    {p && <span className="text-xs text-slate-600">: {fmtInt(p.orders_delayed)} delayed orders, {fmtInt(p.orders_breaching)} missing promise</span>}
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm sm:col-span-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Title</span>
            <input value={spec.title} onChange={(e) => update({ title: e.target.value })} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5" />
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Type</span>
            <select
              value={spec.disruption_type}
              onChange={(e) => update({ disruption_type: e.target.value as DisruptionType })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 capitalize"
            >
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {t.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Starts</span>
            <input
              type="datetime-local"
              value={spec.start_at.slice(0, 16)}
              onChange={(e) => e.target.value && update({ start_at: `${e.target.value}:00` })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Duration (days)</span>
            <input
              type="number" min={0.1} max={60} step={0.5} value={spec.duration_days}
              onChange={(e) => update({ duration_days: Number(e.target.value) })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500" title="Backlog clearance after the disruption ends (assumption A8)">
              Recovery (days)
            </span>
            <input
              type="number" min={0} max={30} step={1} value={spec.recovery_days}
              onChange={(e) => update({ recovery_days: Number(e.target.value) })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Explicit delay (days, optional)</span>
            <input
              type="number" min={0} max={60} step={0.5} value={spec.explicit_delay_days ?? ""}
              placeholder="derived from the window"
              onChange={(e) => update({ explicit_delay_days: e.target.value === "" ? null : Number(e.target.value) })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5"
            />
          </label>
          <label className="text-sm">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Match</span>
            <select
              value={spec.match_mode}
              onChange={(e) => update({ match_mode: e.target.value as DisruptionSpec["match_mode"] })}
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5"
            >
              <option value="any">Origin OR destination</option>
              <option value="lane">Lane only (origin AND destination)</option>
            </select>
          </label>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <GeoEditor label="Origin (freight leaving)" geo={spec.origin} states={states} onChange={(g) => updateGeo("origin", g)} />
          <GeoEditor label="Destination (deliveries into)" geo={spec.destination} states={states} onChange={(g) => updateGeo("destination", g)} />
        </div>

        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {LEGS.map((leg) => (
            <label key={leg} className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={spec.legs.includes(leg)}
                onChange={(e) =>
                  update({ legs: e.target.checked ? [...spec.legs, leg] : spec.legs.filter((l) => l !== leg) })
                }
              />
              {LEG_LABELS[leg]}
            </label>
          ))}
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={spec.nationwide} onChange={(e) => update({ nationwide: e.target.checked })} />
            Nationwide
          </label>
          <label className="flex items-center gap-2" title="Blocks alternate carriers and air corridors too">
            <input type="checkbox" checked={spec.all_modes} onChange={(e) => update({ all_modes: e.target.checked })} />
            All transport modes blocked
          </label>
        </div>

        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Product filter (resolved semantically)</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {spec.category_terms.length === 0 && <span className="text-xs text-slate-500">All products</span>}
            {spec.category_terms.map((t) => (
              <Chip key={t} onRemove={() => update({ category_terms: spec.category_terms.filter((x) => x !== t) })}>
                {t}
              </Chip>
            ))}
            <input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && term.trim()) {
                  update({ category_terms: [...spec.category_terms, term.trim()] });
                  setTerm("");
                }
              }}
              placeholder="+ term, Enter (e.g. bulky, toys)"
              className="w-56 rounded-md border border-slate-300 px-2 py-1 text-xs"
            />
          </div>
        </div>

        {spec.notes.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-600">
            {spec.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
