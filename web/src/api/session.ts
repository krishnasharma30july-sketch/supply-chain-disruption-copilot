import { useSyncExternalStore } from "react";

export type Role = "analyst" | "approver" | "admin";
export interface SessionUser { email: string; name: string; role: Role }
export interface Session { token: string; expiresAt: string; user: SessionUser }

const KEY = "dc.session";
const RANK: Record<Role, number> = { analyst: 0, approver: 1, admin: 2 };
const listeners = new Set<() => void>();

function read(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as Session;
    return new Date(s.expiresAt).getTime() > Date.now() ? s : null;
  } catch {
    return null;
  }
}

let current: Session | null = read();

function emit() {
  listeners.forEach((l) => l());
}

export function setSession(s: Session | null) {
  current = s;
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable: the session lives in memory for this tab */
  }
  emit();
}

export function getToken(): string | null {
  if (current && new Date(current.expiresAt).getTime() <= Date.now()) setSession(null);
  return current?.token ?? null;
}

export function useSession(): Session | null {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => current,
  );
}

export function hasRole(s: Session | null, role: Role, authEnabled = true): boolean {
  if (!authEnabled) return true;
  return !!s && RANK[s.user.role] >= RANK[role];
}
