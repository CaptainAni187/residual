"use client";

import { useState } from "react";
import type { Finding, Inputs } from "@/lib/api";

export function WhatWentIn({ inputs }: { inputs: Inputs }) {
  return (
    <div className="strip rise">
      <div><b>{inputs.files.join(" + ")}</b><span>{inputs.kind}</span></div>
      <div><b className="mono">{inputs.rows_in.toLocaleString("en-IN")}</b><span>rows read</span></div>
      <div><b className="mono">{inputs.period}</b><span>{inputs.days} days, from the file</span></div>
      <div><b className="mono">{inputs.checks_run}</b><span>checks run</span></div>
    </div>
  );
}

export function Verdict({ flagged, total, fine }: { flagged: Finding[]; total: number; fine: string }) {
  if (total === 0) return null;
  if (!flagged.length) {
    return (
      <div className="verdict good rise">
        <span className="v-mark" aria-hidden="true">✓</span>
        <div><b>Nothing here needs chasing.</b><p>{fine}</p></div>
      </div>
    );
  }
  return (
    <div className="verdict warn rise">
      <span className="v-mark" aria-hidden="true">!</span>
      <div>
        <b>{flagged.length} thing{flagged.length === 1 ? "" : "s"} worth chasing</b>
        <ul>
          {flagged.map((f) => (
            <li key={f.cause}><span className="mono">{f.amount.replace("INR ", "₹")}</span> — {f.title.toLowerCase()}</li>
          ))}
        </ul>
        <p>Everything else is ordinary fees, tax, refunds or timing. The agents below can draft the follow-up.</p>
      </div>
    </div>
  );
}

export function Disclosure({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="disc" data-open={open}>
      <button className="disc-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="disc-icon" aria-hidden="true">{open ? "−" : "+"}</span>
        {title}
        {count !== undefined && <span className="disc-count">{count}</span>}
      </button>
      {open && <div className="disc-body">{children}</div>}
    </div>
  );
}

export function Assumed({ assumptions }: { assumptions: string[] }) {
  if (!assumptions.length) return null;
  return (
    <Disclosure title="What this assumed" count={assumptions.length}>
      <p className="sub" style={{ marginTop: 0 }}>If any of these is wrong for you, the numbers are too.</p>
      <ul className="assumed-list">{assumptions.map((line) => <li key={line}>{line}</li>)}</ul>
    </Disclosure>
  );
}
