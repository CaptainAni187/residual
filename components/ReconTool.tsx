"use client";

import Link from "next/link";
import { Fragment, useMemo, useState } from "react";
import { ActionPlan } from "@/components/ActionPlan";
import { AgentPanel } from "@/components/AgentPanel";
import { BucketBars } from "@/components/BucketBars";
import { CountUp } from "@/components/CountUp";
import { FileSlot, type SlotProblem } from "@/components/FileSlot";
import { Info } from "@/components/Info";
import { Assumed, Verdict, WhatWentIn } from "@/components/Report";
import { type Loaded, SamplePicker } from "@/components/SamplePicker";
import { Stages } from "@/components/Stages";
import { ToolShell } from "@/components/ToolShell";
import {
  ApiError,
  type CloseResult,
  type Explanation,
  type Finding,
  closeUpload,
  explainCause,
  rupees,
} from "@/lib/api";
import { useFiles } from "@/lib/files";
import { RATES_HELP, bySlug } from "@/lib/tools";

export type Mode = "reconcile" | "missing" | "fees";

const FEE_CAUSES = ["normal_fee", "fee_rate_increase", "gst_on_fee", "instant_settlement_fee", "tds_194o"];
const MISSING_CAUSES = ["settlement_never_arrived", "settlement_in_flight"];
const PLAN: Record<Mode, string[] | null> = {
  reconcile: null,
  missing: ["settlement_never_arrived"],
  fees: ["fee_rate_increase", "gst_on_fee", "tds_194o"],
};

const VERB: Record<Mode, string> = { reconcile: "Reconcile", missing: "Find missing payouts", fees: "Check my fees" };
const FINE: Record<Mode, string> = {
  reconcile: "Every rupee of the difference is ordinary: fees, tax, refunds and timing. The books balance with nothing left over.",
  missing: "Every payout the gateway sent in this period reached your bank, or is still inside its normal settlement window.",
  fees: "Every fee in this period was charged at your contracted rate.",
};

function slice(mode: Mode, result: CloseResult): Finding[] {
  if (mode === "fees") return result.findings.filter((f) => FEE_CAUSES.includes(f.cause));
  if (mode === "missing") return result.findings.filter((f) => MISSING_CAUSES.includes(f.cause));
  return result.findings;
}

function headline(mode: Mode, rows: Finding[], result: CloseResult) {
  if (mode === "missing") {
    const lost = rows.find((f) => f.cause === "settlement_never_arrived");
    return lost && lost.amount_paise > 0
      ? { paise: lost.amount_paise, tone: "bad", line: "sent by the gateway, never credited to your bank" }
      : { paise: 0, tone: "good", line: "missing — every payout arrived" };
  }
  if (mode === "fees") {
    const over = rows.find((f) => f.cause === "fee_rate_increase");
    const total = rows.reduce((sum, f) => sum + f.amount_paise, 0);
    return over && over.amount_paise > 0
      ? { paise: over.amount_paise, tone: "bad", line: "charged above your contracted rate" }
      : { paise: total, tone: "good", line: "charged in fees and tax, all at your contracted rate" };
  }
  return {
    paise: result.gap_paise,
    tone: "",
    line: `difference between ${rupees(result.gross_paise)} captured and ${rupees(result.landed_paise)} banked`,
  };
}

export function ReconTool({ mode }: { mode: Mode }) {
  const tool = bySlug(mode)!;
  const { files, put, rates, setRates } = useFiles();
  const [password, setPassword] = useState("");
  const [askPassword, setAskPassword] = useState(false);
  const [phase, setPhase] = useState<"input" | "running" | "done">("input");
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<CloseResult | null>(null);
  const [problems, setProblems] = useState<Record<string, SlotProblem>>({});
  const [general, setGeneral] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, Explanation | "loading" | "failed">>({});

  const steps = useMemo(
    () => [
      "Reading your file",
      "Turning it into double-entry books",
      `Running ${mode === "fees" ? "fee" : "17"} checks`,
      "Working out what needs attention",
    ],
    [mode],
  );

  const missingRequired = tool.slots.filter((s) => s.required && !files[s.id]);
  const ratesNeeded = tool.rates === "required" && !rates.trim();

  const run = async (work: () => Promise<CloseResult>) => {
    setPhase("running");
    setFailed(false);
    setGeneral(null);
    setProblems({});
    setOpen(null);
    setSaid({});
    try {
      const out = await work();
      setResult(out);
      setPhase("done");
      setAskPassword(false);
    } catch (problem) {
      setFailed(true);
      const err = problem instanceof ApiError ? problem : new ApiError("Something went wrong.", "error", "Try again.");
      if (err.code === "needs_password" || err.code === "bad_password") setAskPassword(true);
      if (err.field) setProblems({ [err.field]: { message: err.message, fix: err.fix } });
      else setGeneral(err);
      setTimeout(() => setPhase("input"), 700);
    }
  };

  const submit = () => {
    if (missingRequired.length) {
      setProblems(Object.fromEntries(missingRequired.map((s) => [s.id, { message: `Add your ${s.label} to continue.` }])));
      return;
    }
    if (ratesNeeded) {
      setProblems({ contract: { message: "Your contracted rates are needed to check fees." } });
      return;
    }
    run(() => closeUpload(files.recon!, tool.slots.some((s) => s.id === "statement") ? files.statement ?? null : null, rates, password));
  };

  const sample = (l: Loaded) => {
    Object.entries(l.files).forEach(([id, f]) => put(id, f));
    setRates(l.rates);
    setPassword(l.password);
    run(() => closeUpload(l.files.recon, l.files.statement ?? null, l.rates, l.password));
  };

  const pick = async (finding: Finding) => {
    const next = open === finding.cause ? null : finding.cause;
    setOpen(next);
    if (!next || said[finding.cause] || !result) return;
    setSaid((s) => ({ ...s, [finding.cause]: "loading" }));
    try {
      const told = await explainCause(result.token, finding.cause);
      setSaid((s) => ({ ...s, [finding.cause]: told }));
    } catch {
      setSaid((s) => ({ ...s, [finding.cause]: "failed" }));
    }
  };

  const rows = result ? slice(mode, result) : [];
  const peak = Math.max(...rows.map((f) => Math.abs(f.amount_paise)), 1);
  const head = result ? headline(mode, rows, result) : null;

  return (
    <ToolShell slug={mode}>
      {phase !== "done" && (
        <div className="panel inputs rise">
          <div className="stack">
            {tool.slots.map((slot) => (
              <FileSlot
                key={slot.id}
                slot={slot}
                file={files[slot.id] ?? null}
                onChange={(f) => {
                  put(slot.id, f);
                  setProblems((p) => ({ ...p, [slot.id]: null }));
                  if (slot.id === "statement") {
                    setAskPassword(false);
                    setPassword("");
                  }
                }}
                problem={problems[slot.id] ?? null}
                askPassword={slot.id === "statement" && askPassword}
                password={password}
                onPassword={setPassword}
              />
            ))}

            {tool.rates && (
              <div className="slot" data-state={problems.contract ? "error" : "ready"}>
                <div className="slot-head">
                  <label className="slot-label" htmlFor="rates">Contracted rates</label>
                  <span className={`badge ${tool.rates === "required" ? "badge-req" : "badge-opt"}`}>
                    {tool.rates === "required" ? "Required" : "Optional"}
                  </span>
                  <Info label="About contracted rates">{RATES_HELP}</Info>
                </div>
                <input
                  id="rates"
                  className="field"
                  value={rates}
                  onChange={(e) => {
                    setRates(e.target.value);
                    setProblems((p) => ({ ...p, contract: null }));
                  }}
                  spellCheck={false}
                  placeholder="card=2.00,upi=0,netbanking=2.00"
                />
                <span className="hint">method=percent, separated by commas</span>
                {problems.contract && (
                  <div className="slot-err" role="alert">
                    <b>{problems.contract.message}</b>
                    {problems.contract.fix && <span>{problems.contract.fix}</span>}
                  </div>
                )}
              </div>
            )}
          </div>

          {phase === "running" ? (
            <Stages steps={steps} done={false} failed={failed} />
          ) : (
            <div className="cta-row">
              <button className="btn btn-lg" onClick={submit}>{VERB[mode]}</button>
              {missingRequired.length > 0 && (
                <span className="hint">Needs: {missingRequired.map((s) => s.label).join(", ")}</span>
              )}
            </div>
          )}

          {general && (
            <div className="banner" role="alert">
              <b>{general.message}</b>
              {general.fix && <span>{general.fix}</span>}
              <button className="btn btn-sm btn-ghost" onClick={submit}>Try again</button>
            </div>
          )}
          <SamplePicker tool={mode} slots={tool.slots.map((s) => s.id)} onRun={sample} disabled={phase === "running"} />
        </div>
      )}

      {phase === "done" && result && head && (
        <div className="stack result">
          <div className="result-bar rise">
            <span className="sub">Result for {result.inputs.files.join(" + ")}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => { setPhase("input"); setResult(null); }}>
              Start over
            </button>
          </div>

          <WhatWentIn inputs={result.inputs} />

          <div className="panel hero-result rise">
            <CountUp paise={head.paise} className={`headline ${head.tone}`} />
            <div className="sub">{head.line}</div>
          </div>

          <Verdict flagged={rows.filter((f) => f.alarming)} total={rows.length} fine={FINE[mode]} />

          {rows.length > 0 ? (
            <div className="panel wide rise">
              <div className="table-cap">
                <b>Where it went</b>
                <span className="sub">Click any line for a plain-English explanation and the query behind it.</span>
              </div>
              <table className="data">
                <thead>
                  <tr><th>What</th><th style={{ width: 120 }} /><th className="r">Amount</th></tr>
                </thead>
                <tbody>
                  {rows.map((finding, i) => {
                    const told = said[finding.cause];
                    const width = `${(Math.abs(finding.amount_paise) / peak) * 49}%`;
                    return (
                      <Fragment key={finding.cause}>
                        <tr className="click row-in" style={{ animationDelay: `${i * 45}ms` }} onClick={() => pick(finding)} aria-expanded={open === finding.cause}>
                          <td>
                            <span className="row-title">{finding.title}</span>
                            {finding.alarming && <span className="flag flag-red">check this</span>}
                          </td>
                          <td>
                            <span className="gauge" aria-hidden="true">
                              <s />
                              {finding.amount_paise < 0
                                ? <i className="dn grow" style={{ right: "50%", width }} />
                                : <i className="grow" style={{ left: "50%", width }} />}
                            </span>
                          </td>
                          <td className="r">{rupees(finding.amount_paise)}</td>
                        </tr>
                        {open === finding.cause && (
                          <tr className="expand">
                            <td colSpan={3}>
                              <p className="said">
                                {told === "loading" || told === undefined
                                  ? "Reading the ledger…"
                                  : told === "failed"
                                    ? finding.note
                                    : told.text}
                              </p>
                              {typeof told === "object" && (
                                <span className="said-src">
                                  {told.source === "offline" ? "Written from the verifier's output" : `Worded by ${told.source} · every figure checked`}
                                </span>
                              )}
                              {finding.refs.length > 0 && (
                                <p className="refs">References: <span className="mono">{finding.refs.join(", ")}</span></p>
                              )}
                              <details className="sql-wrap">
                                <summary>Show the query</summary>
                                <pre className="sql">{finding.sql}</pre>
                              </details>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {mode === "reconcile" && (
                <div className="balance-line">
                  <span>Everything above adds up to the difference.</span>
                  <b className="mono">{rupees(result.residual_paise)} left unexplained</b>
                </div>
              )}
            </div>
          ) : (
            <div className="panel rise"><p className="said" style={{ margin: 0 }}>{FINE[mode]}</p></div>
          )}

          {mode === "reconcile" && (
            <div className="panel rise">
              <div className="table-cap"><b>What kind of money it is</b></div>
              <BucketBars totals={result.insight.totals} labels={result.insight.labels} gap={result.gap_paise} />
            </div>
          )}
          <ActionPlan
            token={result.token}
            actions={result.insight.actions.filter((a) => !PLAN[mode] || PLAN[mode]!.includes(a.cause))}
          />
          <AgentPanel token={result.token} actions={tool.agents} />
          <Assumed assumptions={result.assumptions} />

          {(
            <Link className="next-link" href="/dashboard">See your whole period on the dashboard →</Link>
          )}
        </div>
      )}
    </ToolShell>
  );
}
