"use client";

import { Fragment, useState } from "react";
import { Dropzone } from "@/components/Dropzone";
import { HowLine } from "@/components/HowLine";
import { ToolShell } from "@/components/ToolShell";
import {
  ApiError,
  type CloseResult,
  type Explanation,
  type Finding,
  closeUpload,
  demoClose,
  explainCause,
  rupees,
} from "@/lib/api";

export type Mode = "reconcile" | "missing" | "fees";

const FEE_CAUSES = ["normal_fee", "fee_rate_increase", "gst_on_fee", "instant_settlement_fee", "tds_194o"];
const MISSING_CAUSES = ["settlement_never_arrived", "settlement_in_flight"];

function slice(mode: Mode, result: CloseResult): Finding[] {
  if (mode === "fees") return result.findings.filter((f) => FEE_CAUSES.includes(f.cause));
  if (mode === "missing") return result.findings.filter((f) => MISSING_CAUSES.includes(f.cause));
  return result.findings;
}

function verdict(mode: Mode, rows: Finding[], result: CloseResult) {
  if (mode === "missing") {
    const total = rows.reduce((sum, f) => sum + f.amount_paise, 0);
    return total > 0
      ? { big: rupees(total), tone: "bad", line: `across ${rows.length} payout${rows.length === 1 ? "" : "s"} that have not reached your bank` }
      : { big: "Nothing missing", tone: "good", line: "every payout in this file reached the bank" };
  }
  if (mode === "fees") {
    const over = rows.find((f) => f.cause === "fee_rate_increase");
    const total = rows.reduce((sum, f) => sum + f.amount_paise, 0);
    return over && over.amount_paise > 0
      ? { big: rupees(over.amount_paise), tone: "bad", line: "charged above your contracted rate" }
      : { big: rupees(total), tone: "good", line: "charged in total, all at your contracted rate" };
  }
  return {
    big: rupees(result.gap_paise),
    tone: "",
    line: `between ${rupees(result.gross_paise)} captured and ${rupees(result.landed_paise)} banked`,
  };
}

export function ReconTool({ mode }: { mode: Mode }) {
  const [recon, setRecon] = useState<File | null>(null);
  const [statement, setStatement] = useState<File | null>(null);
  const [rates, setRates] = useState("card=2.00,upi=2.36,netbanking=2.00");
  const [result, setResult] = useState<CloseResult | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, Explanation>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const go = async (work: () => Promise<CloseResult>, label: string) => {
    setBusy(label);
    setError("");
    setOpen(null);
    setSaid({});
    try {
      setResult(await work());
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : "could not read that file");
    } finally {
      setBusy("");
    }
  };

  const pick = async (finding: Finding) => {
    const next = open === finding.cause ? null : finding.cause;
    setOpen(next);
    if (!next || said[finding.cause] || !result) return;
    try {
      const told = await explainCause(result.token, finding.cause);
      setSaid((prior) => ({ ...prior, [finding.cause]: told }));
    } catch {
      /* the basis line still shows */
    }
  };

  const rows = result ? slice(mode, result) : [];
  const peak = Math.max(...rows.map((f) => Math.abs(f.amount_paise)), 1);
  const head = result ? verdict(mode, rows, result) : null;

  return (
    <ToolShell slug={mode}>
      <div className="stack">
        <Dropzone
          label="Drop your Razorpay report here"
          accept=".json,application/json"
          picked={recon}
          onPick={setRecon}
        />
        {mode === "reconcile" && (
          <Dropzone
            label="Bank statement CSV (optional)"
            accept=".csv,text/csv"
            picked={statement}
            onPick={setStatement}
          />
        )}
        {mode !== "missing" && (
          <div>
            <label className="lbl" htmlFor="rates">Your contracted rates</label>
            <input id="rates" className="field" value={rates} onChange={(e) => setRates(e.target.value)} spellCheck={false} />
          </div>
        )}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button
            className="btn"
            disabled={!!busy}
            onClick={() => {
              if (!recon) {
                setError("choose your Razorpay report first");
                return;
              }
              go(() => closeUpload(recon, statement, rates), "run");
            }}
          >
            {busy === "run" ? "Working…" : "Run it"}
          </button>
          <button className="btn btn-ghost" disabled={!!busy} onClick={() => go(() => demoClose(), "demo")}>
            {busy === "demo" ? "Loading…" : "Try it with sample data"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
      </div>

      {result && head && (
        <div className="stack" style={{ marginTop: 26 }}>
          <div className="panel">
            <div className={`headline ${head.tone}`}>{head.big}</div>
            <div className="sub" style={{ marginTop: 4 }}>{head.line}</div>
          </div>

          {rows.length > 0 && (
            <div className="panel wide">
              <table className="data">
                <thead>
                  <tr>
                    <th>What</th>
                    <th style={{ width: 110 }} />
                    <th className="r">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((finding) => (
                    <Fragment key={finding.cause}>
                      <tr className="click" onClick={() => pick(finding)}>
                        <td>
                          {finding.title}
                          {finding.alarming && <span className="flag flag-red" style={{ marginLeft: 8 }}>check this</span>}
                          <div className="why">{finding.note}</div>
                        </td>
                        <td>
                          <span className="gauge" aria-hidden="true">
                            <s />
                            {finding.amount_paise < 0 ? (
                              <i className="dn" style={{ right: "50%", width: `${(Math.abs(finding.amount_paise) / peak) * 49}%` }} />
                            ) : (
                              <i style={{ left: "50%", width: `${(Math.abs(finding.amount_paise) / peak) * 49}%` }} />
                            )}
                          </span>
                        </td>
                        <td className="r">{rupees(finding.amount_paise)}</td>
                      </tr>
                      {open === finding.cause && (
                        <tr>
                          <td colSpan={3} style={{ background: "var(--sunken)" }}>
                            <p className="said" style={{ margin: "2px 0 10px" }}>
                              {said[finding.cause]?.text ?? "Reading the ledger…"}
                            </p>
                            <pre className="sql">{finding.sql}</pre>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {mode === "reconcile" && (
            <div className="panel" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <span className="sub">Everything above adds up to the difference, with nothing left over.</span>
              <span className="headline good" style={{ fontSize: 20 }}>{rupees(result.residual_paise)} unexplained</span>
            </div>
          )}

          <HowLine>
            <p className="sub" style={{ marginBottom: 10 }}>
              Your report becomes double-entry bookkeeping, so the difference has to equal the
              movement of every other account. Each line below is measured with its own query
              against your data — click any row above to see the query that produced it.
            </p>
            <p className="sub">
              {result.checked} checks ran over {result.start} to {result.end}. Source: {result.source}.
            </p>
          </HowLine>
        </div>
      )}
    </ToolShell>
  );
}
