import type { PreviewCounts } from "../../api/types";
import { Card, ErrorBox, Spinner } from "../../components/ui";
import { fmtBRL, fmtInt } from "../../lib/format";

export default function PreviewPanel({ preview, loading, error }: { preview: PreviewCounts | null; loading: boolean; error: unknown }) {
  return (
    <Card title={<span className="flex items-center gap-2">Impact preview {loading && <Spinner small />}</span>}>
      {error ? (
        <ErrorBox error={error} title="This interpretation is not valid" />
      ) : preview ? (
        <>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <Stat label="Orders in scope" value={fmtInt(preview.orders_in_scope)} />
            <Stat label="Orders delayed" value={fmtInt(preview.orders_delayed)} strong />
            <Stat label="Will miss promise" value={fmtInt(preview.orders_breaching)} strong />
            <Stat label="Customers" value={fmtInt(preview.customers_delayed)} />
            <Stat label="Sellers" value={fmtInt(preview.sellers_delayed)} />
            <Stat label="Items" value={fmtInt(preview.items_delayed)} />
            <div className="col-span-2">
              <Stat label="Order value delayed" value={fmtBRL(preview.gmv_delayed_brl)} />
            </div>
          </dl>
          {preview.warnings.length > 0 && (
            <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-800">
              {preview.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-xs text-slate-500">Deterministic preview (no LLM). Updates as you edit the interpretation.</p>
        </>
      ) : (
        <p className="text-sm text-slate-500">Interpret a scenario to see its impact.</p>
      )}
    </Card>
  );
}

function Stat({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="rounded-lg bg-slate-50 p-2.5">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className={strong ? "text-lg font-semibold text-slate-900 tabular-nums" : "text-base font-medium tabular-nums"}>{value}</dd>
    </div>
  );
}
