"use client";

import Link from "next/link";
import { useState } from "react";
import { ActionPlan } from "@/components/ActionPlan";
import { BucketBars } from "@/components/BucketBars";
import { FeeRate, WeeklyActionable } from "@/components/Charts";
import { CountUp } from "@/components/CountUp";
import { FileSlot, type SlotProblem } from "@/components/FileSlot";
import { Info } from "@/components/Info";
import { Assumed } from "@/components/Report";
import { type Loaded, SamplePicker } from "@/components/SamplePicker";
import { Stages } from "@/components/Stages";
import { ApiError, type CloseResult, type Quarter, closeUpload, quarter, rupees } from "@/lib/api";
import { useFiles } from "@/lib/files";
import { RATES_HELP, bySlug } from "@/lib/tools";

const BUCKET_WORD: Record<string, string> = {
  chase: "chase the gateway",
  tax: "claim on tax",
  timing: "timing",
  cost: "cost of doing business",
  lost: "lost",
};

function Report({ q, closed }: { q: Quarter; closed: CloseResult }) {
  const act = q.insight.recoverable_paise;
  return (
    <article className="report" aria-label="Printable report">
      <header className="rp-head">
        <div>
          <h1>Settlement review</h1>
          <p>{q.source.replace(/, week \d+$/, "")} · {q.covers}</p>
        </div>
        <span className="rp-brand">RESIDUAL</span>
      </header>

      <section>
        <h2>1. The problem</h2>
        <p>
          {rupees(q.gross_paise)} was captured over {q.days} days, but {rupees(q.landed_paise)} reached the bank.
          This review accounts for the {rupees(q.gap_paise)} difference and says what to do about it.
        </p>
      </section>

      <section>
        <h2>2. Data used</h2>
        <ul>
          {closed.inputs.files.map((f) => <li key={f}>{f.replace(/, week \d+$/, "")}</li>)}
          <li>{closed.inputs.rows_in.toLocaleString("en-IN")} rows read, {q.weeks.length} weeks analysed</li>
        </ul>
      </section>

      <section>
        <h2>3. Findings</h2>
        <table>
          <tbody>
            {["chase", "tax", "lost", "cost", "timing"].map((k) => (
              <tr key={k}><td>{q.insight.labels[k]}</td><td className="r">{rupees(q.insight.totals[k] ?? 0)}</td></tr>
            ))}
            <tr className="rp-total"><td>Difference accounted for</td><td className="r">{rupees(q.gap_paise)}</td></tr>
            <tr><td>Left unexplained</td><td className="r">{rupees(q.residual_paise)}</td></tr>
          </tbody>
        </table>
        <p><b>{rupees(act)}</b> of the difference is money you can act on.</p>
      </section>

      <section>
        <h2>4. Root causes</h2>
        <table>
          <tbody>
            {q.causes.slice(0, 10).map((c) => (
              <tr key={c.cause}>
                <td>{c.title}<small>{BUCKET_WORD[c.bucket]}</small></td>
                <td className="r">{rupees(c.amount_paise)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>5. Recommendations</h2>
        {q.insight.actions.length ? (
          <ol>
            {q.insight.actions.map((a) => (
              <li key={a.cause}>
                <b>{a.title}</b> — {rupees(a.amount_paise)}. {a.impact}. <i>{a.who}; {a.deadline}.</i>
              </li>
            ))}
          </ol>
        ) : (
          <p>Nothing needs chasing.</p>
        )}
      </section>

      <section>
        <h2>Assumptions</h2>
        <ul className="rp-small">{q.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </section>
    </article>
  );
}

export default function Dashboard() {
  const slots = bySlug("reconcile")!.slots;
  const { files, put, rates, setRates } = useFiles();
  const [phase, setPhase] = useState<"input" | "running" | "done">("input");
  const [failed, setFailed] = useState(false);
  const [problems, setProblems] = useState<Record<string, SlotProblem>>({});
  const [general, setGeneral] = useState<ApiError | null>(null);
  const [closed, setClosed] = useState<CloseResult | null>(null);
  const [q, setQ] = useState<Quarter | null>(null);
  const [password, setPassword] = useState("");
  const [askPassword, setAskPassword] = useState(false);

  const run = async (work: () => Promise<CloseResult>) => {
    setPhase("running");
    setFailed(false);
    setGeneral(null);
    setProblems({});
    try {
      const c = await work();
      setClosed(c);
      setQ(await quarter(c.token));
      setPhase("done");
    } catch (err) {
      setFailed(true);
      const e = err instanceof ApiError ? err : new ApiError("Something went wrong.", "error", "Try again.");
      if (e.code === "needs_password" || e.code === "bad_password") setAskPassword(true);
      if (e.field) setProblems({ [e.field]: { message: e.message, fix: e.fix } });
      else setGeneral(e);
      setTimeout(() => setPhase("input"), 700);
    }
  };

  const sample = (l: Loaded) => {
    Object.entries(l.files).forEach(([id, f]) => put(id, f));
    setRates(l.rates);
    setPassword(l.password);
    run(() => closeUpload(l.files.recon, l.files.statement ?? null, l.rates, l.password));
  };

  const submit = () => {
    const missing = slots.filter((s) => s.required && !files[s.id]);
    if (missing.length) {
      setProblems(Object.fromEntries(missing.map((s) => [s.id, { message: `Add your ${s.label} to continue.` }])));
      return;
    }
    run(() => closeUpload(files.recon!, files.statement ?? null, rates, password));
  };

  return (
    <main className="tool dash rise">
      <Link href="/" className="back no-print">← All tools</Link>
      <div className="tool-head no-print">
        <div>
          <h1 className="tool-name">
            Settlement dashboard
            <Info label="About the dashboard">
              Looks at every week in your data at once: where the money went, which of it you can get back, how your
              fees moved against your contract, and what to do first.
            </Info>
          </h1>
          <p className="tool-does">Your whole period on one page, with what to do first.</p>
        </div>
      </div>

      {phase !== "done" && (
        <div className="panel inputs no-print">
          <div className="stack">
            {slots.map((slot) => (
              <FileSlot
                key={slot.id}
                slot={slot}
                file={files[slot.id] ?? null}
                onChange={(f) => { put(slot.id, f); setProblems((p) => ({ ...p, [slot.id]: null })); }}
                problem={problems[slot.id] ?? null}
                askPassword={slot.id === "statement" && askPassword}
                password={password}
                onPassword={setPassword}
              />
            ))}
            <div className="slot" data-state={problems.contract ? "error" : "ready"}>
              <div className="slot-head">
                <label className="slot-label" htmlFor="dash-rates">Contracted rates</label>
                <span className="badge badge-opt">Optional</span>
                <Info label="About contracted rates">{RATES_HELP}</Info>
              </div>
              <input id="dash-rates" className="field" value={rates} onChange={(e) => setRates(e.target.value)} spellCheck={false} />
              {problems.contract && (
                <div className="slot-err" role="alert"><b>{problems.contract.message}</b><span>{problems.contract.fix}</span></div>
              )}
            </div>
          </div>
          {phase === "running" ? (
            <Stages
              steps={["Reading your file", "Building your books", "Closing every week", "Ranking what to do"]}
              done={false}
              failed={failed}
            />
          ) : (
            <div className="cta-row">
              <button className="btn btn-lg" onClick={submit}>Build my dashboard</button>
            </div>
          )}
          {general && <div className="banner" role="alert"><b>{general.message}</b><span>{general.fix}</span></div>}
          <SamplePicker tool="dashboard" slots={slots.map((s) => s.id)} onRun={sample} disabled={phase === "running"} />
        </div>
      )}

      {phase === "done" && q && closed && (
        <>
          <div className="stack result no-print">
            <div className="result-bar">
              <span className="sub">{q.source.replace(/, week \d+$/, "")} · {q.covers}</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn btn-sm" onClick={() => window.print()}>Download report (PDF)</button>
                <button className="btn btn-sm btn-ghost" onClick={() => { setPhase("input"); setQ(null); }}>Start over</button>
              </div>
            </div>

            <div className="kpis">
              <div className="kpi"><span>Captured</span><b className="mono">{rupees(q.gross_paise)}</b></div>
              <div className="kpi"><span>Reached your bank</span><b className="mono">{rupees(q.landed_paise)}</b></div>
              <div className="kpi"><span>Difference</span><b className="mono">{rupees(q.gap_paise)}</b></div>
              <div className="kpi kpi-hero">
                <span>You can act on <Info label="What counts">Money to chase from the gateway, plus tax credit to claim back. Excludes timing, which settles on its own, and ordinary fees.</Info></span>
                <CountUp paise={q.insight.recoverable_paise} className="mono" />
              </div>
            </div>

            <div className="panel story">
              {q.headline.map((line, i) => <p key={i} className="row-in" style={{ animationDelay: `${i * 80}ms` }}>{line}</p>)}
            </div>

            <div className="two">
              <div className="panel">
                <div className="table-cap"><b>Money you can act on, by week</b></div>
                <WeeklyActionable weeks={q.weeks} />
              </div>
              <div className="panel">
                <div className="table-cap">
                  <b>Your fee rate against your contract</b>
                  {q.hike_started && <span className="pill pill-high">Above contract</span>}
                </div>
                <FeeRate weeks={q.weeks} hikeStarted={q.hike_started} />
              </div>
            </div>

            <div className="panel">
              <div className="table-cap">
                <b>Where the {rupees(q.gap_paise)} went</b>
                <span className="sub">Everything adds up — {rupees(q.residual_paise)} left unexplained.</span>
              </div>
              <BucketBars totals={q.insight.totals} labels={q.insight.labels} gap={q.gap_paise} />
            </div>

            <ActionPlan token={closed.token} actions={q.insight.actions} title="Your action plan" />
            <Assumed assumptions={q.assumptions} />
          </div>
          <Report q={q} closed={closed} />
        </>
      )}
    </main>
  );
}
