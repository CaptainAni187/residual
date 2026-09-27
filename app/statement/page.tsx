"use client";

import { useState } from "react";
import { Dropzone } from "@/components/Dropzone";
import { HowLine } from "@/components/HowLine";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type StatementOut, readStatement, rupees } from "@/lib/api";

export default function StatementTool() {
  const [file, setFile] = useState<File | null>(null);
  const [out, setOut] = useState<StatementOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (chosen: File) => {
    setBusy(true);
    setError("");
    setOut(null);
    try {
      setOut(await readStatement(chosen));
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : "could not read that file");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ToolShell slug="statement">
      <div className="stack">
        <Dropzone
          label="Drop your bank statement CSV here"
          accept=".csv,text/csv"
          picked={file}
          onPick={(f) => {
            setFile(f);
            run(f);
          }}
        />
        {busy && <p className="sub">Reading…</p>}
        {error && <p className="err">{error}</p>}
      </div>

      {out && (
        <div className="stack" style={{ marginTop: 22 }}>
          <div className="tiles">
            <div className="tile">
              <div className="v">{out.rows.length}</div>
              <div className="k">transactions</div>
            </div>
            <div className="tile">
              <div className="v">{rupees(out.credits_paise)}</div>
              <div className="k">money in</div>
            </div>
            <div className="tile">
              <div className="v">{rupees(out.debits_paise)}</div>
              <div className="k">money out</div>
            </div>
            <div className="tile">
              <div className="v" style={{ color: out.ties_to_balance ? "var(--green)" : "var(--red)" }}>
                {out.ties_to_balance ? "Ties" : "Off"}
              </div>
              <div className="k">against its own balance</div>
            </div>
          </div>

          <div className="panel wide">
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Narration</th>
                  <th className="r">Out</th>
                  <th className="r">In</th>
                  <th className="r">Balance</th>
                </tr>
              </thead>
              <tbody>
                {out.rows.map((row, i) => (
                  <tr key={i}>
                    <td className="mono">{row.date}</td>
                    <td>{row.narration}</td>
                    <td className="r">{row.debit_paise ? rupees(row.debit_paise) : "—"}</td>
                    <td className="r">{row.credit_paise ? rupees(row.credit_paise) : "—"}</td>
                    <td className="r">{row.balance_paise === null ? "—" : rupees(row.balance_paise)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <HowLine>
            <p className="sub">
              A statement carries a running balance, so it contains its own check: the change in
              balance has to equal credits minus debits. {out.balances_checked} row
              {out.balances_checked === 1 ? "" : "s"} were checked that way and{" "}
              {out.rows_disagreeing === 0 ? "all agreed" : `${out.rows_disagreeing} disagreed`}.
              {out.skipped.length > 0 && ` ${out.skipped.length} line(s) were skipped as non-transactional.`}
            </p>
          </HowLine>
        </div>
      )}
    </ToolShell>
  );
}
