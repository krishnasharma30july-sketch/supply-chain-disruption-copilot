import type { SVGProps } from "react";

/** Brand mark: a delivery route with a diverted leg (origin, disruption, reroute, destination). */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <rect width="32" height="32" rx="7" fill="#16362e" />
      <path d="M7 23 L13 14 L20 18 L25 9" fill="none" stroke="#7bb6a3" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 14 L16 8.5 L25 9" fill="none" stroke="#fba424" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="2.2 2.4" />
      <circle cx="7" cy="23" r="2.4" fill="#d5e9e2" />
      <circle cx="13" cy="14" r="2.4" fill="#fba424" />
      <circle cx="25" cy="9" r="2.4" fill="#d5e9e2" />
    </svg>
  );
}

export function Wordmark({ tone = "light" }: { tone?: "light" | "dark" }) {
  return (
    <span className="leading-tight">
      <span className={tone === "light" ? "block text-[15px] font-semibold text-white" : "block text-[15px] font-semibold text-slate-900"}>
        Disruption Copilot
      </span>
      <span className={tone === "light" ? "block text-[10px] font-medium uppercase tracking-label text-brand-300" : "block text-[10px] font-medium uppercase tracking-label text-brand-600"}>
        Supply chain control room
      </span>
    </span>
  );
}

type IconProps = SVGProps<SVGSVGElement>;
const base = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

export const IconScenario = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <path d="M12 3 2.5 19.5h19L12 3Z" />
    <path d="M12 10v4.5M12 17.2v.01" />
  </svg>
);
export const IconRuns = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <circle cx="5" cy="6" r="2" />
    <circle cx="5" cy="18" r="2" />
    <path d="M10 6h11M10 12h11M10 18h11M5 8v8" />
  </svg>
);
export const IconSearch = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="m20 20-4.8-4.8" />
  </svg>
);
export const IconBook = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Z" />
    <path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5" />
  </svg>
);
export const IconUsers = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <circle cx="9" cy="8" r="3.5" />
    <path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.6a3.5 3.5 0 0 1 0 6.8M18.5 14a6.5 6.5 0 0 1 3 6" />
  </svg>
);
export const IconLogout = (p: IconProps) => (
  <svg {...base} {...p} aria-hidden>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11" />
  </svg>
);
