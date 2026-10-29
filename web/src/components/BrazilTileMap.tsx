// Schematic tile map of Brazil's 27 federative units (no map tiles or external data needed).
const TILES: Record<string, [number, number]> = {
  RR: [0, 2], AP: [0, 4],
  AM: [1, 1], PA: [1, 3], MA: [1, 4], CE: [1, 5], RN: [1, 6],
  AC: [2, 0], RO: [2, 1], MT: [2, 2], TO: [2, 3], PI: [2, 4], PE: [2, 5], PB: [2, 6],
  MS: [3, 2], GO: [3, 3], DF: [3, 4], BA: [3, 5], AL: [3, 6],
  PR: [4, 2], SP: [4, 3], MG: [4, 4], ES: [4, 5], SE: [4, 6],
  SC: [5, 2], RJ: [5, 4],
  RS: [6, 2],
};

export function BrazilTileMap({ values, label, highlight = [] }: { values: Record<string, number>; label: string; highlight?: string[] }) {
  const max = Math.max(1, ...Object.values(values));
  const size = 44;
  const gap = 4;
  return (
    <figure>
      <svg viewBox={`0 0 ${7 * (size + gap)} ${7 * (size + gap)}`} className="h-auto w-full max-w-sm" role="img" aria-label={label}>
        {Object.entries(TILES).map(([uf, [row, col]]) => {
          const v = values[uf] ?? 0;
          const t = v / max;
          const fill = v === 0 ? "#efede8" : `rgba(31, 81, 67, ${0.15 + 0.85 * t})`;
          const x = col * (size + gap);
          const y = row * (size + gap);
          const hl = highlight.includes(uf);
          return (
            <g key={uf}>
              <title>{`${uf}: ${v.toLocaleString()} ${label}`}</title>
              <rect x={x} y={y} width={size} height={size} rx={6} fill={fill} stroke={hl ? "#f5820b" : "#cdc8bc"} strokeWidth={hl ? 2.5 : 1} />
              <text x={x + size / 2} y={y + 18} textAnchor="middle" fontSize="11" fontWeight={600} fill={t > 0.55 ? "#fff" : "#1e1c19"}>
                {uf}
              </text>
              <text x={x + size / 2} y={y + 33} textAnchor="middle" fontSize="10" fill={t > 0.55 ? "#d5e9e2" : "#5d584d"}>
                {v ? v.toLocaleString() : ""}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1 text-xs text-slate-500">
        {label} by state. {highlight.length > 0 && "Amber outline: disrupted origin/destination states."}
      </figcaption>
    </figure>
  );
}
