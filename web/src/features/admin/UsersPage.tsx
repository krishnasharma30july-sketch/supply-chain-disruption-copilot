import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { api } from "../../api/client";
import type { AdminUser } from "../../api/types";
import { Badge, Card, Empty, ErrorBox, Loading } from "../../components/ui";
import { fmtTimestamp } from "../../lib/format";
import { useAuth } from "../auth/useAuth";

const ROLES: { id: AdminUser["role"]; help: string }[] = [
  { id: "analyst", help: "Runs scenarios, edits and drafts emails" },
  { id: "approver", help: "Also approves, rejects and sends customer emails" },
  { id: "admin", help: "Also manages users" },
];

export default function UsersPage() {
  const { session, can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: api.users, enabled: can("admin") });
  const update = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: { role?: AdminUser["role"]; active?: boolean } }) => api.updateUser(id, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
  });

  if (!can("admin")) return <Empty>User management requires the admin role.</Empty>;
  const users = q.data ?? [];
  const counts = ROLES.map((r) => ({ ...r, n: users.filter((u) => u.role === r.id && u.active).length }));

  return (
    <div className="space-y-5">
      <div>
        <p className="label text-signal-700">Administration</p>
        <h1 className="mt-1 text-[28px] font-semibold leading-tight tracking-tight text-slate-900">Users and roles</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          People who sign up get the analyst role. Grant approver to those who may send customer emails. Changes apply on the
          user's next request; deactivated users are signed out.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {counts.map((r) => (
          <div key={r.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
            <p className="label">{r.id}s</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{r.n}</p>
            <p className="mt-1 text-xs text-slate-500">{r.help}</p>
          </div>
        ))}
      </div>

      <ErrorBox error={update.error} title="Could not update the user" />
      <Card title={`${users.length} accounts`}>
        {q.isLoading ? (
          <Loading />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left">
                <tr className="[&>th]:label [&>th]:pb-2"><th>User</th><th>Role</th><th>Status</th><th>Created</th><th className="text-right">Access</th></tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const self = u.email === session?.user.email;
                  return (
                    <tr key={u.id} className={clsx("border-t border-slate-100", !u.active && "text-slate-400")}>
                      <td className="py-2.5 pr-3">
                        <p className="font-medium text-slate-900">{u.name}{self && <span className="ml-2 text-xs font-normal text-slate-500">(you)</span>}</p>
                        <p className="text-xs text-slate-500">{u.email}</p>
                      </td>
                      <td>
                        <select aria-label={`Role of ${u.email}`} value={u.role} disabled={self || update.isPending}
                          onChange={(e) => update.mutate({ id: u.id, patch: { role: e.target.value as AdminUser["role"] } })}
                          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm disabled:bg-slate-50">
                          {ROLES.map((r) => <option key={r.id} value={r.id}>{r.id}</option>)}
                        </select>
                      </td>
                      <td>
                        <Badge className={u.active ? "bg-brand-50 text-brand-800 ring-brand-300" : "bg-slate-100 text-slate-500 ring-slate-300"}>
                          {u.active ? "active" : "deactivated"}
                        </Badge>
                      </td>
                      <td className="text-xs text-slate-600">{fmtTimestamp(u.created_at)}</td>
                      <td className="text-right">
                        <button disabled={self || update.isPending}
                          onClick={() => update.mutate({ id: u.id, patch: { active: !u.active } })}
                          className={clsx("rounded-md px-2.5 py-1.5 text-xs font-medium ring-1 disabled:cursor-not-allowed disabled:opacity-40",
                            u.active ? "text-red-700 ring-red-200 hover:bg-red-50" : "text-brand-800 ring-brand-300 hover:bg-brand-50")}>
                          {u.active ? "Deactivate" : "Reactivate"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
