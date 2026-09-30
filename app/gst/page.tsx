"use client";

import { useState } from "react";
import { AgentPanel } from "@/components/AgentPanel";
import { CountUp } from "@/components/CountUp";
import { FileSlot, type SlotProblem } from "@/components/FileSlot";
import { Assumed } from "@/components/Report";
import { Stages } from "@/components/Stages";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type GstOut, checkGst, rupees } from "@/lib/api";
import { useFiles } from "@/lib/files";
import { bySlug } from "@/lib/tools";

export default function GstTool() {
  const tool = bySlug("gst")!;
  const { files, put } = useFiles();
  const [phase, setPhase] = useState<"input" | "running" | "done">("input");
  const [failed, setFailed] = useState(false);
  const [out, setOut] = useState<GstOut | null>(null);
  const [problems, setProblems] = useState<Record<string, SlotProblem>>({});
  const [general, setGeneral] = useState<ApiError | null>(null);

  const run = async () => {
    const missing = tool.slots.filter((s) => s.required && !files[s.id]);
    if (missing.length) {
      setProblems(Object.fromEntries(missing.map((s) => [s.id, { message: `Add your ${s.label} to continue.` }])));
      return;
    }
    setPhase("running");
    setFailed(false);
    setGeneral(null);
    setProblems({});
    try {
      setOut(await checkGst(files.recon!, files.gstr2b!));
      setPhase("done");
    } catch (problem) {
      setFailed(true);
      const err = problem instanceof ApiError ? problem : new ApiError("Something went wrong.", "error", "Try again.");
      if (err.field) setProblems({ [err.field]: { message: err.message, fix: err.fix } });
      else setGeneral(err);
      setTimeout(() => setPhase("input"), 700);
    }
  };

  return (
    <ToolShell slug="gst">
      {phase !== "done" && (
        <div className="panel inputs rise">
          <div className="row2">
            {tool.slots.map((slot) => (
              <FileSlot
                key={slot.id}
                slot={slot}
                file={files[slot.id] ?? null}
                onChange={(f) => { put(slot.id, f); setProblems((p) => ({ ...p, [slot.id]: null })); }}
                problem={problems[slot.id] ?? null}
              />
            ))}
          </div>
          {phase === "running" ? (
            <Stages steps={["Reading your report", "Reading GSTR-2B", "Matching tax paid to credit available", "Checking supplier GSTINs"]} done={false} failed={failed} />
          ) : (
            <div className="cta-row"><button className="btn btn-lg" onClick={run}>Check my credit</button></div>
          )}
          {general && <div className="banner" role="alert"><b>{general.message}</b><span>{general.fix}</span></div>}
        </div>
      )}

      {phase === "done" && out && (
        <div className="stack result">
          <div className="result-bar rise">
            <span className="sub">Result for {out.source} · {out.period}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => { setPhase("input"); setOut(null); }}>Start over</button>
          </div>
          <div className="panel hero-result rise">
            {out.at_risk_paise > 0
              ? <CountUp paise={out.at_risk_paise} className="headline bad" />
              : <span className="headline good">All claimable</span>}
            <div className="sub">
              {out.at_risk_paise > 0 ? "of GST you paid but cannot claim yet" : "every rupee of GST you paid is available to claim"}
            </div>
          </div>
          <div className="tiles rise">
            <div className="tile"><div className="v">{rupees(out.paid_paise)}</div><div className="k">GST you paid</div></div>
            <div className="tile"><div className="v">{rupees(out.claimable_paise)}</div><div className="k">showing in GSTR-2B</div></div>
            <div className="tile"><div className="v">{out.invoices}</div><div className="k">invoices read</div></div>
          </div>
          {out.risks.map((risk, i) => (
            <div className="panel risk row-in" key={risk.kind} style={{ animationDelay: `${i * 60}ms` }}>
              <div className="risk-top"><b>{risk.title}</b><span className="mono">{risk.amount.replace("INR ", "₹")}</span></div>
              <p className="sub">{risk.detail}</p>
              <p className="said">→ {risk.action}</p>
            </div>
          ))}
          {out.risks.length > 0 && <AgentPanel token={out.token} actions={tool.agents} />}
          <Assumed assumptions={out.assumptions} />
        </div>
      )}
    </ToolShell>
  );
}
