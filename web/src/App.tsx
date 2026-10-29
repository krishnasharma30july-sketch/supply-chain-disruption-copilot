import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { lazy, Suspense, useEffect, type ComponentType, type SVGProps } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { setSession } from "./api/session";
import { IconBook, IconLogout, IconRuns, IconScenario, IconSearch, IconUsers, LogoMark, Wordmark } from "./components/brand";
import { ErrorBox, Loading } from "./components/ui";
import LoginPage from "./features/auth/LoginPage";
import { useAuth, useSessionSync } from "./features/auth/useAuth";
import ScenarioPage from "./features/scenario/ScenarioPage";
import { fmtDate } from "./lib/format";

// Route-level code splitting: the charting and table code loads only when a run is opened.
const RunPage = lazy(() => import("./features/runs/RunPage"));
const RunsPage = lazy(() => import("./features/runs/RunsPage"));
const ExplorerPage = lazy(() => import("./features/explorer/ExplorerPage"));
const AboutPage = lazy(() => import("./features/about/AboutPage"));
const UsersPage = lazy(() => import("./features/admin/UsersPage"));

type NavItem = { to: string; label: string; end?: boolean; icon: ComponentType<SVGProps<SVGSVGElement>> };
const NAV: NavItem[] = [
  { to: "/", label: "New scenario", end: true, icon: IconScenario },
  { to: "/runs", label: "Runs", icon: IconRuns },
  { to: "/explorer", label: "Retrieval explorer", icon: IconSearch },
  { to: "/about", label: "How it works", icon: IconBook },
];

function Dot({ tone }: { tone: "ok" | "warn" | "off" }) {
  return (
    <span aria-hidden className={clsx("inline-block h-1.5 w-1.5 rounded-full",
      tone === "ok" ? "bg-emerald-500 shadow-[0_0_0_3px_rgba(16,185,129,0.18)]" : tone === "warn" ? "bg-signal-500" : "bg-slate-400")} />
  );
}

function StatusBar() {
  const { data } = useQuery({ queryKey: ["meta"], queryFn: api.meta, refetchInterval: 60_000 });
  if (!data) return null;
  const llm = data.llm;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
      <span className="flex items-center gap-1.5" title="Simulation clock: order state is reconstructed as of this instant">
        <Dot tone={data.dataset ? "ok" : "warn"} />
        {data.dataset ? <>Snapshot <span className="figure text-slate-900">{fmtDate(data.dataset.as_of)}</span></> : "Dataset not loaded"}
      </span>
      <span className="flex items-center gap-1.5" title={llm.breaker_reason ?? undefined}>
        <Dot tone={llm.available ? "ok" : llm.mode === "offline" ? "off" : "warn"} />
        {llm.available ? <>LLM <span className="figure text-slate-900">{llm.models.email ?? llm.provider}</span></>
          : llm.mode === "offline" ? "LLM offline · rules + templates" : "LLM degraded"}
      </span>
    </div>
  );
}

function UserCard() {
  const { enabled, session } = useAuth();
  if (!enabled || !session) return null;
  const initials = session.user.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  return (
    <div className="flex items-center gap-2.5 rounded-lg bg-white/5 p-2.5 ring-1 ring-white/10">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-brand-700 text-xs font-semibold text-brand-100">{initials}</span>
      <span className="min-w-0 flex-1 leading-tight">
        <span className="block truncate text-[13px] font-medium text-white" title={session.user.email}>{session.user.name}</span>
        <span className="block text-[10px] font-semibold uppercase tracking-label text-signal-300">{session.user.role}</span>
      </span>
      <button className="rounded-md p-1.5 text-brand-200 hover:bg-white/10 hover:text-white" onClick={() => setSession(null)}
        aria-label="Sign out" title="Sign out">
        <IconLogout className="h-4 w-4" />
      </button>
    </div>
  );
}

const ADMIN_NAV: NavItem = { to: "/users", label: "Users", icon: IconUsers };

function useNav(): NavItem[] {
  const { can, enabled } = useAuth();
  return enabled && can("admin") ? [...NAV, ADMIN_NAV] : NAV;
}

function SideNav() {
  const nav = useNav();
  return (
    <nav className="space-y-0.5">
      {nav.map((n) => (
        <NavLink key={n.to} to={n.to} end={n.end}
          className={({ isActive }) => clsx(
            "group flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
            isActive ? "bg-white/10 font-medium text-white shadow-[inset_2px_0_0_#fba424]" : "text-brand-200 hover:bg-white/5 hover:text-white",
          )}
        >
          <n.icon className="h-[18px] w-[18px] shrink-0 opacity-90" />
          {n.label}
        </NavLink>
      ))}
    </nav>
  );
}

export default function App() {
  const { config, enabled, session } = useAuth();
  const nav = useNav();
  useSessionSync(enabled);
  const qc = useQueryClient();
  const userKey = session?.user.email ?? "";
  // A different user signing in must not see the previous user's cached data or permissions.
  useEffect(() => {
    qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth-config" });
  }, [userKey, qc]);

  if (config.isLoading) return <Loading label="Starting..." />;
  if (config.error) {
    return (
      <div className="mx-auto max-w-xl p-6">
        <ErrorBox error={config.error} title="The API is not reachable" />
      </div>
    );
  }
  if (enabled && !session) return <LoginPage config={config.data} />;
  return (
    <div className="min-h-screen lg:pl-64" key={userKey}>
      {/* Desktop: fixed control-room sidebar */}
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col bg-brand-950 bg-[radial-gradient(120%_60%_at_0%_0%,rgba(51,122,101,0.35),transparent_60%)] px-4 py-5 lg:flex">
        <NavLink to="/" className="mb-8 flex items-center gap-3 px-1">
          <LogoMark className="h-9 w-9" />
          <Wordmark />
        </NavLink>
        <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-label text-brand-400">Workspace</p>
        <SideNav />
        <div className="mt-auto space-y-3">
          <p className="px-1 text-[11px] leading-relaxed text-brand-300/80">
            Olist marketplace · decisions are policy-checked, emails are fact-checked before anyone sees them.
          </p>
          <UserCard />
        </div>
      </aside>

      {/* Mobile and tablet: compact top bar */}
      <header className="sticky top-0 z-30 bg-brand-950 text-white lg:hidden">
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <NavLink to="/" className="flex items-center gap-2.5">
            <LogoMark className="h-8 w-8" />
            <Wordmark />
          </NavLink>
          {enabled && session && (
            <button className="rounded-md p-2 text-brand-200 hover:bg-white/10" onClick={() => setSession(null)} aria-label="Sign out" title="Sign out">
              <IconLogout className="h-5 w-5" />
            </button>
          )}
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
              className={({ isActive }) => clsx("shrink-0 rounded-md px-3 py-1.5 text-sm",
                isActive ? "bg-white/10 font-medium text-white" : "text-brand-200 hover:text-white")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
      </header>

      <div className="sticky top-0 z-20 hidden border-b border-slate-200/80 bg-[#f4f2ed]/85 backdrop-blur lg:block">
        <div className="mx-auto flex max-w-7xl items-center justify-end px-6 py-2.5">
          <StatusBar />
        </div>
      </div>
      <div className="border-b border-slate-200/80 px-4 py-2 lg:hidden"><StatusBar /></div>

      <main className="mx-auto max-w-7xl px-4 py-6 lg:px-6 lg:py-8">
        <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/" element={<ScenarioPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/explorer" element={<ExplorerPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="*" element={<p className="text-slate-600">Page not found.</p>} />
        </Routes>
        </Suspense>
      </main>
    </div>
  );
}
