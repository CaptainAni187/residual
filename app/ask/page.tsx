"use client";

import { useEffect, useRef, useState } from "react";
import { FileSlot, type SlotProblem } from "@/components/FileSlot";
import { SamplePicker } from "@/components/SamplePicker";
import { Stages } from "@/components/Stages";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type Answer, type CloseResult, askQuestion, closeUpload } from "@/lib/api";
import { useFiles } from "@/lib/files";
import { bySlug } from "@/lib/tools";

const STARTERS = [
  "Which payouts never reached my bank?",
  "How much did I pay in fees, by method?",
  "What were my five biggest payouts?",
  "How many payments failed, and by which method?",
  "How much did I refund this period?",
];

type Turn = { q: string; a?: Answer; err?: string };

export default function AskTool() {
  const tool = bySlug("ask")!;
  const { files, put } = useFiles();
  const [loaded, setLoaded] = useState<CloseResult | null>(null);
  const [phase, setPhase] = useState<"input" | "running">("input");
  const [failed, setFailed] = useState(false);
  const [problems, setProblems] = useState<Record<string, SlotProblem>>({});
  const [password, setPassword] = useState("");
  const [askPassword, setAskPassword] = useState(false);
  const [general, setGeneral] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [thinking, setThinking] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns, thinking]);

  const load = async (work: () => Promise<CloseResult>) => {
    setPhase("running");
    setFailed(false);
    setProblems({});
    setGeneral("");
    try {
      setLoaded(await work());
      setTurns([]);
    } catch (err) {
      setFailed(true);
      const e = err instanceof ApiError ? err : new ApiError("Something went wrong.", "error", "Try again.");
      if (e.code === "needs_password" || e.code === "bad_password") setAskPassword(true);
      if (e.field) setProblems({ [e.field]: { message: e.message, fix: e.fix } });
      else setGeneral([e.message, e.fix].filter(Boolean).join(" "));
    } finally {
      setTimeout(() => setPhase("input"), failed ? 700 : 0);
    }
  };

  const ask = async (text: string) => {
    const q = text.trim();
    if (!loaded || q.length < 2 || thinking) return;
    setDraft("");
    setTurns((t) => [...t, { q }]);
    setThinking(true);
    try {
      const a = await askQuestion(loaded.token, q);
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, a } : turn)));
    } catch (err) {
      const e = err instanceof ApiError ? err : null;
      const msg = e?.code === "session_expired" ? "Your data has expired. Load it again to keep asking." : e ? [e.message, e.fix].filter(Boolean).join(" ") : "Could not answer that.";
      setTurns((t) => t.map((turn, i) => (i === t.length - 1 ? { ...turn, err: msg } : turn)));
      if (e?.code === "session_expired") setLoaded(null);
    } finally {
      setThinking(false);
    }
  };

  return (
    <ToolShell slug="ask">
      {!loaded ? (
        <div className="panel inputs rise">
          <div className="stack">
            {tool.slots.map((slot) => (
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
          </div>
          {phase === "running" ? (
            <Stages steps={["Reading your file", "Building your books", "Getting ready for questions"]} done={false} failed={failed} />
          ) : (
            <div className="cta-row">
              <button
                className="btn btn-lg"
                onClick={() => (files.recon ? load(() => closeUpload(files.recon!, files.statement ?? null, "", password)) : setProblems({ recon: { message: "Add your Razorpay report to continue." } }))}
              >
                Load my data
              </button>
            </div>
          )}
          {general && <div className="banner" role="alert"><b>{general}</b></div>}
          <SamplePicker
            tool="ask"
            slots={["recon", "statement"]}
            onRun={(l) => {
              Object.entries(l.files).forEach(([id, f]) => put(id, f));
              load(() => closeUpload(l.files.recon, l.files.statement ?? null, l.rates));
            }}
            disabled={phase === "running"}
          />
        </div>
      ) : (
        <div className="chat rise">
          <div className="chat-bar">
            <span className="sub">Asking about <b>{loaded.inputs.files.join(" + ")}</b> · all data from {loaded.inputs.covers}</span>
            <button className="btn btn-sm btn-ghost" onClick={() => setLoaded(null)}>Change data</button>
          </div>

          <div className="chat-log">
            {turns.length === 0 && (
              <div className="starters">
                <p className="sub">Try one of these, or ask your own.</p>
                <div className="starter-grid">
                  {STARTERS.map((s) => <button key={s} className="starter" onClick={() => ask(s)}>{s}</button>)}
                </div>
              </div>
            )}
            {turns.map((turn, i) => (
              <div key={i} className="turn">
                <div className="bubble me">{turn.q}</div>
                {turn.err && <div className="bubble them err-bubble">{turn.err}</div>}
                {turn.a && (
                  <div className="bubble them">
                    {turn.a.rows.length > 0 ? (
                      <div className="wide">
                        <table className="data compact">
                          <thead><tr>{turn.a.columns.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
                          <tbody>{turn.a.rows.map((row, r) => <tr key={r}>{row.map((cell, c) => <td key={c} className="r">{cell}</td>)}</tr>)}</tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="said" style={{ margin: 0 }}>{turn.a.note || "Nothing in your data matched that."}</p>
                    )}
                    {turn.a.sql && (
                      <details className="sql-wrap">
                        <summary>Show the query · {turn.a.source.startsWith("catalogue") ? "standard question" : `written by ${turn.a.source}`}</summary>
                        <pre className="sql">{turn.a.sql}</pre>
                      </details>
                    )}
                  </div>
                )}
              </div>
            ))}
            {thinking && <div className="bubble them"><div className="t-typing"><i /><i /><i /></div></div>}
            <div ref={end} />
          </div>

          <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(draft); }}>
            <input
              className="field"
              placeholder="Ask anything about your payments…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              maxLength={400}
              aria-label="Your question"
            />
            <button className="btn" type="submit" disabled={thinking || draft.trim().length < 2}>Ask</button>
          </form>
        </div>
      )}
    </ToolShell>
  );
}
