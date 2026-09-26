"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  type Answer,
  type CloseResult,
  type Explanation,
  type Finding,
  type Investigation,
  type Providers,
  askQuestion,
  closeUpload,
  demoClose,
  explainCause,
  investigate,
  providers,
  rupees,
} from "@/lib/api";

function Gauge({ amount, peak }: { amount: number; peak: number }) {
  const span = (Math.abs(amount) / peak) * 49;
  return (
    <span className="gauge" aria-hidden="true">
      <s />
      {amount < 0 ? (
        <i className="dn" style={{ right: "50%", width: `${span}%` }} />
      ) : (
        <i style={{ left: "50%", width: `${span}%` }} />
      )}
    </span>
  );
}

export default function Workspace() {
  const [result, setResult] = useState<CloseResult | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<{ where: string; message: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [said, setSaid] = useState<Record<string, Explanation>>({});
  const [ai, setAi] = useState<Providers | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [found, setFound] = useState<Investigation | null>(null);
  const [over, setOver] = useState(false);
  const [contract, setContract] = useState("card=2.00,upi=2.36,netbanking=2.00");
  const recon = useRef<HTMLInputElement>(null);
  const statement = useRef<HTMLInputElement>(null);

  useEffect(() => {
    providers().then(setAi).catch(() => setAi(null));
  }, []);

  const guard = useCallback(
    async (label: string, where: string, work: () => Promise<void>) => {
      setBusy(label);
      setError(null);
      try {
        await work();
      } catch (problem) {
        setError({
          where,
          message: problem instanceof ApiError ? problem.message : "something went wrong",
        });
      } finally {
        setBusy("");
      }
    },
    [],
  );

  const Trouble = ({ where }: { where: string }) =>
    error && error.where === where ? <p className="err mt-3">{error.message}</p> : null;

  const reset = () => {
    setOpen(null);
    setSaid({});
    setAnswer(null);
    setFound(null);
  };

  const runDemo = () =>
    guard("demo", "upload", async () => {
      reset();
      setResult(await demoClose(8));
    });

  const runUpload = (files: { recon: File; statement: File | null }) =>
    guard("upload", "upload", async () => {
      reset();
      setResult(await closeUpload(files.recon, files.statement, contract));
    });

  const submit = () => {
    const file = recon.current?.files?.[0];
    if (!file) {
      setError({ where: "upload", message: "choose a Razorpay recon or payments export first" });
      return;
    }
    runUpload({ recon: file, statement: statement.current?.files?.[0] ?? null });
  };

  const pick = (finding: Finding) => {
    const next = open === finding.cause ? null : finding.cause;
    setOpen(next);
    if (!next || said[finding.cause] || !result) return;
    guard(`explain:${finding.cause}`, "explain", async () => {
      const out = await explainCause(result.token, finding.cause);
      setSaid((prior) => ({ ...prior, [finding.cause]: out }));
    });
  };

  const ask = () => {
    if (!result || question.trim().length < 2) return;
    guard("ask", "ask", async () => setAnswer(await askQuestion(result.token, question.trim())));
  };

  const dig = () => {
    if (!result) return;
    guard("investigate", "dig", async () => setFound(await investigate(result.token)));
  };

  const peak = result ? Math.max(...result.findings.map((f) => Math.abs(f.amount_paise)), 1) : 1;

  return (
    <main className="pb-16">
      <section className="band py-6">
        <h2 className="sect">Reconcile</h2>
        <p className="sans mt-2 max-w-[70ch] text-[13.5px]" style={{ color: "var(--ink-2)" }}>
          Upload a Razorpay recon or payments export, and a bank statement if you have one. Nothing
          is stored: the file is held in memory for the session and dropped. If you have neither to
          hand, run it on a generated merchant instead.
        </p>

        <div className="mt-4 grid gap-4 md:grid-cols-[1fr_auto]">
          <div
            className="drop"
            data-over={over}
            onClick={() => recon.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              const file = e.dataTransfer.files?.[0];
              if (file) runUpload({ recon: file, statement: null });
            }}
          >
            Drop a recon export here, or choose a file
            <input ref={recon} type="file" accept=".json,application/json" hidden />
          </div>
          <div className="flex flex-col gap-2">
            <button className="btn" onClick={submit} disabled={!!busy}>
              {busy === "upload" ? "reconciling…" : "Reconcile upload"}
            </button>
            <button className="btn btn-quiet" onClick={runDemo} disabled={!!busy}>
              {busy === "demo" ? "loading…" : "Use a generated merchant"}
            </button>
          </div>
        </div>

        <div className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="block">
            <span className="sans text-[11.5px]" style={{ color: "var(--ink-3)" }}>
              Bank statement, optional (CSV)
            </span>
            <input ref={statement} type="file" accept=".csv,text/csv" className="field mt-1" />
          </label>
          <label className="block">
            <span className="sans text-[11.5px]" style={{ color: "var(--ink-3)" }}>
              Your contracted rates
            </span>
            <input
              className="field mt-1"
              value={contract}
              onChange={(e) => setContract(e.target.value)}
              spellCheck={false}
            />
          </label>
        </div>

        <Trouble where="upload" />
        {ai && (
          <p className="sans mt-3 text-[12px]" style={{ color: "var(--ink-3)" }}>
            Explanations: <b style={{ color: "var(--ink)" }}>{ai.using}</b>. {ai.note}
          </p>
        )}
      </section>

      {result && (
        <>
          <section className="band py-6">
            <h2 className="sect">
              {result.source} &middot; {result.start} to {result.end}
            </h2>
            <div className="arith mt-3">
              <div>
                <span className="lbl">Gross captured</span>
                <span className="amt n">{rupees(result.gross_paise)}</span>
              </div>
              <div>
                <span className="lbl">Less cash landed in bank</span>
                <span className="amt n">{rupees(result.landed_paise)}</span>
              </div>
              <div className="tot">
                <span className="lbl">Variance to account for</span>
                <span className="amt n">{rupees(result.gap_paise)}</span>
              </div>
            </div>

            <div className="wide mt-4">
              <table className="ledger">
                <thead>
                  <tr>
                    <th>Cause</th>
                    <th style={{ width: 130 }}>&nbsp;</th>
                    <th className="r">Amount</th>
                    <th>Basis</th>
                  </tr>
                </thead>
                <tbody>
                  {result.findings.map((finding) => (
                    <tr key={finding.cause} className="pick" onClick={() => pick(finding)}>
                      <td style={{ whiteSpace: "nowrap" }}>
                        {finding.alarming && (
                          <span style={{ color: "var(--red)", fontWeight: 600, paddingRight: 4 }}>!</span>
                        )}
                        {finding.cause}
                      </td>
                      <td>
                        <Gauge amount={finding.amount_paise} peak={peak} />
                      </td>
                      <td className="r n">{rupees(finding.amount_paise)}</td>
                      <td className="note">{finding.note}</td>
                    </tr>
                  ))}
                  <tr className="total">
                    <td>Residual</td>
                    <td />
                    <td className="r n" style={{ color: "var(--green)" }}>
                      {rupees(result.residual_paise)}
                    </td>
                    <td className="note">
                      {result.covered
                        ? "every account's claims sum exactly to its movement"
                        : "a coverage gap is present"}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {open && (
              <div className="mt-4">
                {(() => {
                  const finding = result.findings.find((f) => f.cause === open);
                  const told = said[open];
                  if (!finding) return null;
                  return (
                    <>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="chip chip-quiet">{finding.cause}</span>
                        {finding.alarming && <span className="chip chip-red">escalate</span>}
                        {told && <span className="chip chip-quiet">via {told.source}</span>}
                      </div>
                      <p className="said mt-2 max-w-[74ch]">
                        {busy === `explain:${open}` ? "reading the ledger…" : told?.text ?? finding.note}
                      </p>
                      {told && (
                        <p className="sans mt-1 text-[11.5px]" style={{ color: "var(--ink-3)" }}>
                          {told.reason}
                        </p>
                      )}
                      <Trouble where="explain" />
                      <pre className="sql mt-3">{finding.sql}</pre>
                    </>
                  );
                })()}
              </div>
            )}
          </section>

          <section className="band py-6">
            <h2 className="sect">Ask the books</h2>
            <div className="mt-3 flex flex-wrap gap-2">
              <input
                className="field flex-1 min-w-[240px]"
                placeholder="which settlements never arrived?"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && ask()}
              />
              <button className="btn" onClick={ask} disabled={!!busy}>
                {busy === "ask" ? "asking…" : "Ask"}
              </button>
            </div>
            <Trouble where="ask" />
            {answer && (
              <div className="mt-3">
                <span className="chip chip-quiet">{answer.source}</span>
                {answer.rows.length > 0 ? (
                  <div className="wide mt-2">
                    <table className="ledger">
                      <thead>
                        <tr>
                          {answer.columns.map((column) => (
                            <th key={column}>{column}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {answer.rows.map((row, index) => (
                          <tr key={index}>
                            {row.map((cell, spot) => (
                              <td key={spot} className="n">
                                {cell}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="sans mt-2 text-[13px]" style={{ color: "var(--ink-2)" }}>
                    {answer.note || "nothing matched that"}
                  </p>
                )}
                {answer.sql && <pre className="sql mt-2">{answer.sql}</pre>}
              </div>
            )}
          </section>

          <section className="py-6">
            <h2 className="sect">Investigate what is left open</h2>
            <p className="sans mt-2 max-w-[70ch] text-[13.5px]" style={{ color: "var(--ink-2)" }}>
              When the books do not close, an agent is given a toolbelt over the ledger and has to
              work out the mechanism. Its answer is accepted only if the query it returns equals the
              shortfall and leaves every account balanced.
            </p>
            <button className="btn btn-quiet mt-3" onClick={dig} disabled={!!busy}>
              {busy === "investigate" ? "investigating…" : "Run the agent"}
            </button>
            <Trouble where="dig" />
            {found && (
              <div className="mt-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="chip chip-quiet">driver: {found.driver}</span>
                  <span className={`chip ${found.accepted ? "chip-green" : "chip-red"}`}>
                    {found.accepted ? "accepted" : "rejected"}
                  </span>
                  <span className="sans text-[12px]" style={{ color: "var(--ink-3)" }}>
                    short by {found.short_by}
                  </span>
                </div>
                <div className="mt-3" style={{ borderTop: "1px solid var(--rule)" }}>
                  {found.steps.map((step, index) => (
                    <div className="trace-row" key={index}>
                      <span style={{ color: "var(--ink-3)", fontSize: 11 }}>{index + 1}</span>
                      <span style={{ overflowWrap: "anywhere" }}>
                        {step.tool}
                        <span style={{ color: "var(--ink-3)" }}>
                          ({Object.entries(step.args)
                            .map(([key, value]) => `${key}=${String(value)}`)
                            .join(", ")})
                        </span>
                      </span>
                      <span className="n">{step.found}</span>
                    </div>
                  ))}
                </div>
                <p className="sans mt-2 text-[12.5px]" style={{ color: "var(--ink-2)" }}>
                  {found.verdict}
                </p>
                {found.proposal && <pre className="sql mt-2">{found.proposal.sql}</pre>}
              </div>
            )}
          </section>
        </>
      )}
    </main>
  );
}
