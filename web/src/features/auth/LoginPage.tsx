import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { setSession } from "../../api/session";
import type { AuthConfig } from "../../api/types";
import { LogoMark, Wordmark } from "../../components/brand";
import { Button, ErrorBox } from "../../components/ui";

const ROLE_HELP: Record<string, string> = {
  analyst: "runs scenarios, edits and drafts emails",
  approver: "also approves, rejects and sends emails",
  admin: "everything",
};

const STEPS = [
  ["Interpret", "Free-text disruption → validated scope"],
  ["Assess", "Orders, SKUs and lanes it touches, new ETAs"],
  ["Decide", "Cancel, reroute or delay, inside policy"],
  ["Communicate", "Fact-checked emails, human-approved"],
];

/** Stylised Brazil route network for the brand panel (decorative). */
function RouteNetwork() {
  const nodes: [number, number][] = [[70, 60], [150, 95], [230, 70], [300, 140], [210, 190], [120, 230], [260, 270], [180, 320], [320, 330]];
  const edges: [number, number][] = [[0, 1], [1, 2], [2, 3], [1, 4], [4, 3], [4, 5], [4, 6], [5, 7], [6, 7], [6, 8], [3, 8]];
  return (
    <svg viewBox="0 0 380 380" className="h-full w-full" aria-hidden>
      {edges.map(([a, b], i) => (
        <line key={i} x1={nodes[a][0]} y1={nodes[a][1]} x2={nodes[b][0]} y2={nodes[b][1]}
          stroke={i === 4 || i === 6 ? "#fba424" : "#337a65"} strokeWidth={i === 4 || i === 6 ? 2 : 1.4}
          strokeDasharray={i === 4 || i === 6 ? "5 5" : undefined} opacity={0.9} />
      ))}
      {nodes.map(([x, y], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r={i === 4 ? 14 : 9} fill={i === 4 ? "rgba(251,164,36,0.18)" : "rgba(123,182,163,0.12)"} />
          <circle cx={x} cy={y} r={i === 4 ? 5 : 3.5} fill={i === 4 ? "#fba424" : "#acd3c5"} />
        </g>
      ))}
    </svg>
  );
}

export default function LoginPage({ config }: { config?: AuthConfig }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => api.login(email, password),
    onSuccess: (r) => setSession({ token: r.access_token, expiresAt: r.expires_at, user: r.user }),
  });
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const signup = useMutation({
    mutationFn: () => api.signup(name, email, password),
    onSuccess: (r) => setSession({ token: r.access_token, expiresAt: r.expires_at, user: r.user }),
  });
  const signupEnabled = !!config?.signup_enabled;
  const domains = config?.signup_domains ?? [];
  const switchMode = (m: "signin" | "signup") => {
    setMode(m);
    setLocalError(null);
    login.reset();
    signup.reset();
    setPassword("");
    setConfirm("");
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalError(null);
    if (mode === "signin") {
      if (email && password) login.mutate();
      return;
    }
    if (password.length < 8) return setLocalError("The password must have at least 8 characters.");
    if (password !== confirm) return setLocalError("The passwords do not match.");
    signup.mutate();
  };
  const demo = config?.demo_accounts ?? [];
  const input = "mt-1.5 w-full rounded-md border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20";

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <section className="relative hidden overflow-hidden bg-brand-950 p-12 text-white lg:flex lg:flex-col">
        <div className="absolute inset-0 bg-[radial-gradient(90%_70%_at_10%_0%,rgba(51,122,101,0.45),transparent_60%)]" />
        <div className="absolute -right-16 bottom-0 h-[460px] w-[460px] opacity-80"><RouteNetwork /></div>
        <div className="relative flex items-center gap-3">
          <LogoMark className="h-10 w-10" />
          <Wordmark />
        </div>
        <div className="relative mt-auto max-w-md">
          <p className="text-[11px] font-semibold uppercase tracking-label text-signal-300">When the network breaks</p>
          <h2 className="mt-3 text-3xl font-semibold leading-tight">
            From a disruption report to approved customer emails, with every number traceable.
          </h2>
          <ol className="mt-8 grid grid-cols-2 gap-3">
            {STEPS.map(([t, d], i) => (
              <li key={t} className="rounded-lg bg-white/5 p-3 ring-1 ring-white/10">
                <span className="figure text-xs text-signal-300">0{i + 1}</span>
                <p className="mt-1 text-sm font-medium">{t}</p>
                <p className="mt-0.5 text-xs text-brand-200">{d}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <LogoMark className="h-10 w-10" />
            <Wordmark tone="dark" />
          </div>
          <p className="label">{mode === "signin" ? "Welcome back" : "New here"}</p>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">{mode === "signin" ? "Sign in" : "Create your account"}</h1>
          {mode === "signup" && (
            <p className="mt-2 text-sm text-slate-600">
              New accounts are <strong>analysts</strong>: you can run scenarios and prepare emails. An administrator grants
              approval rights.{domains.length > 0 && <> Use your {domains.map((d) => "@" + d).join(" or ")} address.</>}
            </p>
          )}
          <form onSubmit={submit} className="mt-6 space-y-4">
            {mode === "signup" && (
              <label className="block text-sm">
                <span className="font-medium text-slate-700">Full name</span>
                <input autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} className={input} maxLength={80} required />
              </label>
            )}
            <label className="block text-sm">
              <span className="font-medium text-slate-700">Email</span>
              <input type="email" autoComplete={mode === "signin" ? "username" : "email"} value={email} onChange={(e) => setEmail(e.target.value)} className={input} required />
            </label>
            <label className="block text-sm">
              <span className="font-medium text-slate-700">Password</span>
              <input type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} value={password}
                onChange={(e) => setPassword(e.target.value)} className={input} minLength={mode === "signup" ? 8 : undefined} required />
              {mode === "signup" && <span className="mt-1 block text-xs text-slate-500">At least 8 characters.</span>}
            </label>
            {mode === "signup" && (
              <label className="block text-sm">
                <span className="font-medium text-slate-700">Confirm password</span>
                <input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} className={input} required />
              </label>
            )}
            {localError && <ErrorBox error={{ title: "Check the form", detail: localError }} />}
            <ErrorBox error={mode === "signin" ? login.error : signup.error} title={mode === "signin" ? "Sign-in failed" : "Sign-up failed"} />
            <Button type="submit" loading={login.isPending || signup.isPending} className="w-full py-2.5">
              {mode === "signin" ? "Sign in" : "Create account"}
            </Button>
          </form>
          {signupEnabled && (
            <p className="mt-4 text-center text-sm text-slate-600">
              {mode === "signin" ? "No account yet? " : "Already have an account? "}
              <button type="button" className="font-medium text-brand-700 hover:text-brand-900 hover:underline"
                onClick={() => switchMode(mode === "signin" ? "signup" : "signin")}>
                {mode === "signin" ? "Create one" : "Sign in"}
              </button>
            </p>
          )}
          {mode === "signin" && demo.length > 0 && (
            <div className="mt-8 rounded-lg border border-dashed border-slate-300 bg-white/70 p-4 text-sm">
              <p className="label">Demo accounts · local stack only</p>
              <ul className="mt-3 space-y-2">
                {demo.map((d) => (
                  <li key={d.email}>
                    <button type="button" className="font-medium text-brand-700 hover:text-brand-900 hover:underline"
                      onClick={() => { setEmail(d.email); setPassword(d.password); }}>
                      {d.email}
                    </button>
                    <span className="block text-xs text-slate-500">{d.role}: {ROLE_HELP[d.role] ?? d.role}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-slate-500">Click an account to fill the form. Set DEMO_USERS=false to disable them.</p>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
