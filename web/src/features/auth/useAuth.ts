import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../../api/client";
import { hasRole, setSession, useSession, type Role } from "../../api/session";

/** Auth configuration (public endpoint) plus the current session and a role check for the UI. */
export function useAuth() {
  const config = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig, staleTime: Infinity, retry: 3 });
  const session = useSession();
  const enabled = config.data?.auth_enabled ?? true;
  return {
    config,
    enabled,
    session,
    can: (role: Role) => hasRole(session, role, enabled),
  };
}

/** The server is the source of truth for the account: role changes and deactivation by an admin reach the UI
 *  within a minute (a deactivated account gets 401 and is signed out by the API client). */
export function useSessionSync(enabled: boolean) {
  const session = useSession();
  const me = useQuery({
    queryKey: ["auth-me", session?.user.email],
    queryFn: api.me,
    enabled: enabled && !!session,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    const u = me.data;
    if (!session || !u) return;
    if (u.role !== session.user.role || u.name !== session.user.name) {
      setSession({ ...session, user: { ...session.user, role: u.role, name: u.name } });
    }
  }, [me.data, session]);
}
