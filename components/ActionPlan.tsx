"use client";

import { useState } from "react";
import { type Action, ApiError, type Draft, type DraftKind, draftAction, rupees } from "@/lib/api";

const URGENCY = { high: "Do now", medium: "This week", low: "When you can" } as const;

function DraftBox({ draft }: { draft: Draft }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="draft">
      <div className="draft-row"><span>To</span><b>{draft.to}</b></div>
      <div className="draft-row"><span>Subject</span><b>{draft.subject}</b></div>
      <pre className="draft-body">{draft.body}</pre>
      <div className="draft-actions">
        <button
          className="btn btn-sm"
          onClick={async () => {
            await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
          }}
        >
          {copied ? "Copied ✓" : "Copy email"}
        </button>
        <a className="btn btn-sm btn-ghost" href={`mailto:?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`}>
          Open in mail app
        </a>
        <span className="draft-src">
          {draft.source === "template" ? "Written from verified figures" : `Worded by ${draft.source}, every figure checked`}
        </span>
      </div>
    </div>
  );
}

export function ActionPlan({ token, actions, title = "What to do" }: { token: string; actions: Action[]; title?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<Record<string, string>>({});

  if (!actions.length) {
    return (
      <section className="plan">
        <div className="plan-head"><b>{title}</b></div>
        <p className="sub" style={{ margin: 0 }}>Nothing to chase. Every rupee here is ordinary fees, refunds or timing.</p>
      </section>
    );
  }

  const write = async (a: Action) => {
    setOpen(a.cause);
    if (drafts[a.cause] || !a.draft) return;
    setBusy(a.cause);
    try {
      const out = await draftAction(token, a.draft as DraftKind);
      setDrafts((d) => ({ ...d, [a.cause]: out }));
    } catch (e) {
      setError((x) => ({ ...x, [a.cause]: e instanceof ApiError ? `${e.message} ${e.fix}` : "Could not write the draft." }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="plan">
      <div className="plan-head">
        <b>{title}</b>
        <span className="sub">Ranked by money and urgency.</span>
      </div>
      <ol className="plan-list">
        {actions.map((a, i) => (
          <li key={a.cause} className="plan-item row-in" data-urgency={a.urgency} style={{ animationDelay: `${i * 60}ms` }}>
            <span className="plan-rank">{i + 1}</span>
            <div className="plan-main">
              <div className="plan-top">
                <b>{a.title}</b>
                <span className="mono plan-amt">{rupees(a.amount_paise)}</span>
              </div>
              <p className="plan-why">{a.why}</p>
              <div className="plan-meta">
                <span className={`pill pill-${a.urgency}`}>{URGENCY[a.urgency]}</span>
                <span>{a.impact}</span>
                <span className="plan-dot">·</span>
                <span>{a.who}</span>
                <span className="plan-dot">·</span>
                <span>{a.deadline}</span>
              </div>
              {a.draft && (
                <button className="btn btn-sm btn-ghost plan-btn" onClick={() => (open === a.cause ? setOpen(null) : write(a))}>
                  {busy === a.cause ? "Drafting…" : open === a.cause ? "Hide email" : "Draft the email"}
                </button>
              )}
              {open === a.cause && error[a.cause] && <p className="err">{error[a.cause]}</p>}
              {open === a.cause && drafts[a.cause] && <DraftBox draft={drafts[a.cause]} />}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
