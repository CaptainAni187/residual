"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  type Draft,
  type Investigation,
  draftAction,
  investigate,
} from "@/lib/api";
import type { AgentAction } from "@/lib/tools";

const TOOL_WORDS: Record<string, string> = {
  unbalanced_accounts: "Checked which accounts are short",
  account_movement: "Measured an account",
  event_types_touching: "Looked at what moved it",
  fee_at_contracted_rate: "Priced fees at your contract",
  fee_as_billed: "Read the fees actually billed",
  run_query: "Ran a query",
};

function Transcript({ run }: { run: Investigation }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    setShown(0);
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) {
      setShown(run.steps.length + 1);
      return;
    }
    const timer = setInterval(() => setShown((n) => (n > run.steps.length ? n : n + 1)), 420);
    return () => clearInterval(timer);
  }, [run]);

  return (
    <div className="transcript">
      {run.steps.slice(0, shown).map((step, i) => (
        <div className="t-step" key={i}>
          <span className="t-n">{i + 1}</span>
          <span className="t-what">
            {TOOL_WORDS[step.tool] ?? step.tool}
            {Object.keys(step.args).length > 0 && (
              <em> · {Object.values(step.args).map(String).join(", ").replace(/_/g, " ")}</em>
            )}
          </span>
          <span className="t-found mono">{step.found.replace("INR ", "₹")}</span>
        </div>
      ))}
      {shown > run.steps.length && (
        <div className={`t-verdict ${run.accepted ? "ok" : "no"}`}>
          {run.accepted
            ? `Independently confirmed — traced ${run.short_by.replace("INR ", "₹")} to the same cause, without being told what caused it.`
            : `Could not confirm — ${run.verdict}`}
        </div>
      )}
      {shown <= run.steps.length && <div className="t-typing" aria-hidden="true"><i /><i /><i /></div>}
    </div>
  );
}

function DraftCard({ draft }: { draft: Draft }) {
  const [copied, setCopied] = useState(false);
  const mail = `mailto:?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
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
        <a className="btn btn-sm btn-ghost" href={mail}>Open in mail app</a>
        <span className="draft-src">
          {draft.source === "template" ? "Written from verified figures" : `Worded by ${draft.source}, every figure checked`}
        </span>
      </div>
    </div>
  );
}

export function AgentPanel({ token, actions }: { token: string; actions: AgentAction[] }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, Investigation>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  if (!actions.length) return null;

  const go = async (key: string, action: AgentAction) => {
    setBusy(key);
    setErrors((e) => ({ ...e, [key]: "" }));
    try {
      if (action.kind === "draft") {
        const out = await draftAction(token, action.draft);
        setDrafts((d) => ({ ...d, [key]: out }));
      } else {
        const out = await investigate(token, action.cause ?? "");
        setRuns((r) => ({ ...r, [key]: out }));
      }
    } catch (problem) {
      const text = problem instanceof ApiError ? [problem.message, problem.fix].filter(Boolean).join(" ") : "The agent could not finish.";
      setErrors((e) => ({ ...e, [key]: text }));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="agents">
      <div className="agents-head">
        <span className="agent-mark" aria-hidden="true">✦</span>
        <b>Agents</b>
        <span className="sub">They work only from what was verified above.</span>
      </div>
      <div className="agent-grid">
        {actions.map((action, i) => {
          const key = `${action.kind}-${i}`;
          const run = runs[key];
          const draft = drafts[key];
          return (
            <div className="agent-card" key={key} data-open={!!(run || draft)}>
              <div className="agent-top">
                <div>
                  <b>{action.label}</b>
                  <p>{action.does}</p>
                </div>
                <button className="btn btn-sm" onClick={() => go(key, action)} disabled={busy !== null}>
                  {busy === key ? "Working…" : run || draft ? "Run again" : "Run"}
                </button>
              </div>
              {busy === key && !run && !draft && <div className="t-typing"><i /><i /><i /></div>}
              {errors[key] && <p className="err">{errors[key]}</p>}
              {run && <Transcript run={run} />}
              {draft && <DraftCard draft={draft} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}
