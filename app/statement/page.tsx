"use client";

import Link from "next/link";
import { useState } from "react";
import { FileSlot, type SlotProblem } from "@/components/FileSlot";
import { Assumed } from "@/components/Report";
import { type Loaded, SamplePicker } from "@/components/SamplePicker";
import { Stages } from "@/components/Stages";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type StatementOut, readStatement, rupees } from "@/lib/api";
import { useFiles } from "@/lib/files";
import { bySlug } from "@/lib/tools";

export default function StatementTool() {
  const tool = bySlug("statement")!;
  const slot = tool.slots[0];
  const { files, put } = useFiles();
  const [password, setPassword] = useState("");
  const [askPassword, setAskPassword] = useState(false);
  const [phase, setPhase] = useState<"input" | "running" | "done">("input");
  const [failed, setFailed] = useState(false);
  const [out, setOut] = useState<StatementOut | null>(null);
  const [problem, setProblem] = useState<SlotProblem>(null);
  const [filter, setFilter] = useState<"all" | "in" | "out">("all");

  const run = async (given?: Loaded) => {
    const file = given?.files.statement ?? files.statement;
    const pass = given ? given.password : password;
    if (!file) { setProblem({ message: "Add your bank statement to continue." }); return; }
    if (given) { put("statement", file); setPassword(pass); }
    setPhase("running");
    setFailed(false);
    setProblem(null);
    try {
      setOut(await readStatement(file, pass));
      setPhase("done");
      setAskPassword(false);
    } catch (err) {
      setFailed(true);
      const e = err instanceof ApiError ? err : new ApiError("Something went wrong.", "error", "Try again.");
      if (e.code === "needs_password" || e.code === "bad_password") setAskPassword(true);
      setProblem({ message: e.message, fix: e.fix });
      setTimeout(() => setPhase("input"), 700);
    }
  };

  const rows = out?.rows.filter((r) => filter === "all" || (filter === "in" ? r.credit_paise > 0 : r.debit_paise > 0)) ?? [];

  return (
    <ToolShell slug="statement">
      {phase !== "done" && (
        <div className="panel inputs rise">
          <FileSlot
            slot={slot}
            file={files.statement ?? null}
            onChange={(f) => { put("statement", f); setProblem(null); setAskPassword(false); setPassword(""); }}
            problem={problem}
            askPassword={askPassword}
            password={password}
            onPassword={setPassword}
          />
          {phase === "running" ? (
            <Stages steps={["Opening the file", "Detecting the layout", "Reading transactions", "Checking every running balance"]} done={false} failed={failed} />
          ) : (
            <div className="cta-row"><button className="btn btn-lg" onClick={() => run()}>{askPassword ? "Unlock and read" : "Read statement"}</button></div>
          )}
          <SamplePicker tool="statement" slots={["statement"]} onRun={run} disabled={phase === "running"} />
        </div>
      )}

      {phase === "done" && out && (
        <div className="stack result">
          <div className="result-bar rise">
            <span className="sub">{out.source} · {out.first} to {out.last}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => { setPhase("input"); setOut(null); }}>Start over</button>
          </div>

          <div className={`verdict ${out.ties_to_balance ? "good" : "warn"} rise`}>
            <span className="v-mark" aria-hidden="true">{out.ties_to_balance ? "✓" : "!"}</span>
            <div>
              <b>{out.ties_to_balance ? "Every balance checks out." : out.balances_checked ? `${out.rows_disagreeing} balance${out.rows_disagreeing === 1 ? "" : "s"} do not add up.` : "This statement cannot check itself."}</b>
              <p>
                {out.ties_to_balance
                  ? `All ${out.balances_checked} running balances agree with the transactions before them, so nothing is missing or edited.`
                  : out.balances_checked
                    ? "Rows may be missing or the file may have been edited. Download it again from net banking."
                    : "It prints no running balance, so the rows are shown as given."}
              </p>
            </div>
          </div>

          <div className="tiles rise">
            <div className="tile"><div className="v">{out.rows.length}</div><div className="k">transactions</div></div>
            <div className="tile"><div className="v">{rupees(out.credits_paise)}</div><div className="k">money in</div></div>
            <div className="tile"><div className="v">{rupees(out.debits_paise)}</div><div className="k">money out</div></div>
            <div className="tile"><div className="v">{out.gateway_credits}</div><div className="k">credits from the gateway</div></div>
          </div>

          <div className="panel wide rise">
            <div className="table-cap">
              <b>Transactions</b>
              <div className="seg" role="tablist">
                {(["all", "in", "out"] as const).map((f) => (
                  <button key={f} role="tab" aria-selected={filter === f} data-on={filter === f} onClick={() => setFilter(f)}>
                    {f === "all" ? "All" : f === "in" ? "Money in" : "Money out"}
                  </button>
                ))}
              </div>
            </div>
            <table className="data">
              <thead><tr><th>Date</th><th>Narration</th><th className="r">Out</th><th className="r">In</th><th className="r">Balance</th></tr></thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i} className="row-in" style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}>
                    <td className="mono nowrap">{row.date}</td>
                    <td className="narr">{row.narration}</td>
                    <td className="r">{row.debit_paise ? rupees(row.debit_paise) : "—"}</td>
                    <td className="r">{row.credit_paise ? rupees(row.credit_paise) : "—"}</td>
                    <td className="r">{row.balance_paise === null ? "—" : rupees(row.balance_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Assumed assumptions={out.assumptions} />
          {tool.next && <Link className="next-link" href={`/${tool.next.slug}`}>{tool.next.label} →</Link>}
        </div>
      )}
    </ToolShell>
  );
}
