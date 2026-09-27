"use client";

import { useState } from "react";
import { Dropzone } from "@/components/Dropzone";
import { HowLine } from "@/components/HowLine";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type GstOut, checkGst, rupees } from "@/lib/api";

export default function GstTool() {
  const [recon, setRecon] = useState<File | null>(null);
  const [book, setBook] = useState<File | null>(null);
  const [out, setOut] = useState<GstOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    if (!recon || !book) {
      setError("both files are needed");
      return;
    }
    setBusy(true);
    setError("");
    setOut(null);
    try {
      setOut(await checkGst(recon, book));
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : "could not read those files");
    } finally {
      setBusy(false);
    }
  };

  return (
    <ToolShell slug="gst">
      <div className="stack">
        <div className="row2">
          <Dropzone label="Razorpay report" accept=".json,application/json" picked={recon} onPick={setRecon} />
          <Dropzone label="GSTR-2B (JSON or CSV)" accept=".json,.csv" picked={book} onPick={setBook} />
        </div>
        <div>
          <button className="btn" onClick={run} disabled={busy}>
            {busy ? "Checking…" : "Check my credit"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
      </div>

      {out && (
        <div className="stack" style={{ marginTop: 26 }}>
          <div className="panel">
            <div className={`headline ${out.at_risk_paise > 0 ? "bad" : "good"}`}>
              {out.at_risk_paise > 0 ? rupees(out.at_risk_paise) : "All claimable"}
            </div>
            <div className="sub" style={{ marginTop: 4 }}>
              {out.at_risk_paise > 0
                ? "of input credit you have paid for but cannot claim yet"
                : "every rupee of GST you paid is available to claim"}
            </div>
          </div>

          <div className="tiles">
            <div className="tile">
              <div className="v">{rupees(out.paid_paise)}</div>
              <div className="k">GST you paid</div>
            </div>
            <div className="tile">
              <div className="v">{rupees(out.claimable_paise)}</div>
              <div className="k">showing in GSTR-2B</div>
            </div>
            <div className="tile">
              <div className="v">{out.invoices}</div>
              <div className="k">invoices in the return</div>
            </div>
          </div>

          {out.risks.map((risk) => (
            <div className="panel" key={risk.kind}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <b>{risk.title}</b>
                <span className="mono">{risk.amount}</span>
              </div>
              <p className="sub" style={{ margin: "6px 0 0" }}>{risk.detail}</p>
              <p className="said" style={{ margin: "8px 0 0" }}>{risk.action}</p>
            </div>
          ))}

          <HowLine>
            <p className="sub">
              GST on a gateway fee is only claimable once the gateway has declared that invoice, at
              which point it appears in your GSTR-2B. This compares the tax you actually paid, taken
              from your payment report, against what the return makes available.
            </p>
          </HowLine>
        </div>
      )}
    </ToolShell>
  );
}
