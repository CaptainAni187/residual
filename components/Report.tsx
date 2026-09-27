import type { CloseResult, Finding } from "@/lib/api";

export function WhatWentIn({ result }: { result: CloseResult }) {
  const { inputs } = result;
  return (
    <div className="strip">
      <div>
        <b>{inputs.files.join(" + ")}</b>
        <span>{inputs.kind}</span>
      </div>
      <div>
        <b className="mono">{inputs.rows_in.toLocaleString("en-IN")}</b>
        <span>rows read</span>
      </div>
      <div>
        <b className="mono">{inputs.period}</b>
        <span>{inputs.days} days, taken from the file</span>
      </div>
      <div>
        <b className="mono">{inputs.checks_run}</b>
        <span>checks run against it</span>
      </div>
    </div>
  );
}

export function Verdict({ flagged, total }: { flagged: Finding[]; total: number }) {
  if (total === 0) return null;
  if (flagged.length === 0) {
    return (
      <div className="verdict good">
        <b>Nothing here needs chasing.</b>
        <p>
          Every rupee of the difference is ordinary: fees, tax, refunds and timing. The books
          balance with nothing left over.
        </p>
      </div>
    );
  }
  return (
    <div className="verdict warn">
      <b>
        {flagged.length} thing{flagged.length === 1 ? "" : "s"} worth chasing
      </b>
      <ul>
        {flagged.map((f) => (
          <li key={f.cause}>
            <span className="mono">{f.amount}</span> — {f.title.toLowerCase()}
          </li>
        ))}
      </ul>
      <p>Everything else in the list is ordinary fees, tax, refunds or timing.</p>
    </div>
  );
}

export function Assumed({ assumptions }: { assumptions: string[] }) {
  if (!assumptions.length) return null;
  return (
    <div className="assumed">
      <b>What this assumed</b>
      <p className="sub">If any of these is wrong for you, the numbers above will be too.</p>
      <ul>
        {assumptions.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}
