import { rupees } from "@/lib/api";

const ORDER = ["chase", "tax", "lost", "cost", "timing"] as const;
const ACT = new Set(["chase", "tax"]);

export function BucketBars({ totals, labels, gap }: { totals: Record<string, number>; labels: Record<string, string>; gap: number }) {
  const peak = Math.max(...ORDER.map((k) => Math.abs(totals[k] ?? 0)), 1);
  return (
    <div className="buckets">
      {ORDER.map((key, i) => {
        const value = totals[key] ?? 0;
        const share = gap ? Math.round((value / gap) * 100) : 0;
        return (
          <div className="bucket row-in" key={key} style={{ animationDelay: `${i * 50}ms` }} title={`${labels[key]}: ${rupees(value)}`}>
            <span className="bucket-name">
              {labels[key]}
              {ACT.has(key) && value > 0 && <span className="pill pill-act">act on</span>}
            </span>
            <span className="bucket-track">
              <i className={`grow ${ACT.has(key) ? "act" : key === "lost" ? "lost" : ""}`} style={{ width: `${(Math.abs(value) / peak) * 100}%` }} />
            </span>
            <span className="mono bucket-v">{rupees(value)}</span>
            <span className="bucket-pct">{value ? `${share}%` : ""}</span>
          </div>
        );
      })}
    </div>
  );
}
