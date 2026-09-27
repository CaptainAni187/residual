"use client";

import { useState } from "react";
import { Dropzone } from "@/components/Dropzone";
import { HowLine } from "@/components/HowLine";
import { ToolShell } from "@/components/ToolShell";
import { ApiError, type Answer, type CloseResult, askQuestion, closeUpload, demoClose } from "@/lib/api";

const EXAMPLES = [
  "which settlements never arrived?",
  "how much did I pay in fees by method?",
  "what were my biggest payouts?",
  "how many payments failed last week?",
];

export default function AskTool() {
  const [recon, setRecon] = useState<File | null>(null);
  const [loaded, setLoaded] = useState<CloseResult | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = async (work: () => Promise<CloseResult>, label: string) => {
    setBusy(label);
    setError("");
    setAnswer(null);
    try {
      setLoaded(await work());
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : "could not read that file");
    } finally {
      setBusy("");
    }
  };

  const ask = async (text: string) => {
    if (!loaded || text.trim().length < 2) return;
    setQuestion(text);
    setBusy("ask");
    setError("");
    try {
      setAnswer(await askQuestion(loaded.token, text.trim()));
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : "could not answer that");
    } finally {
      setBusy("");
    }
  };

  return (
    <ToolShell slug="ask">
      {!loaded ? (
        <div className="stack">
          <Dropzone label="Drop your Razorpay report here" accept=".json,application/json" picked={recon} onPick={setRecon} />
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button
              className="btn"
              disabled={!!busy}
              onClick={() => {
                if (!recon) {
                  setError("choose your Razorpay report first");
                  return;
                }
                load(() => closeUpload(recon, null, ""), "run");
              }}
            >
              {busy === "run" ? "Loading…" : "Load it"}
            </button>
            <button className="btn btn-ghost" disabled={!!busy} onClick={() => load(() => demoClose(), "demo")}>
              {busy === "demo" ? "Loading…" : "Try it with sample data"}
            </button>
          </div>
          {error && <p className="err">{error}</p>}
        </div>
      ) : (
        <div className="stack">
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <input
              className="field"
              style={{ flex: 1, minWidth: 240 }}
              placeholder="Ask anything about your payments"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask(question)}
            />
            <button className="btn" onClick={() => ask(question)} disabled={!!busy}>
              {busy === "ask" ? "Thinking…" : "Ask"}
            </button>
          </div>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {EXAMPLES.map((example) => (
              <button key={example} className="btn btn-ghost" style={{ fontSize: 12.5, padding: "6px 12px" }} onClick={() => ask(example)}>
                {example}
              </button>
            ))}
          </div>

          {error && <p className="err">{error}</p>}

          {answer && (
            <>
              {answer.rows.length > 0 ? (
                <div className="panel wide">
                  <table className="data">
                    <thead>
                      <tr>{answer.columns.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr>
                    </thead>
                    <tbody>
                      {answer.rows.map((row, i) => (
                        <tr key={i}>{row.map((cell, j) => <td key={j} className="r">{cell}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="panel">
                  <p className="said" style={{ margin: 0 }}>{answer.note || "Nothing matched that."}</p>
                </div>
              )}
              {answer.sql && (
                <HowLine>
                  <pre className="sql">{answer.sql}</pre>
                </HowLine>
              )}
            </>
          )}

          <p className="sub">Loaded: {loaded.source}</p>
        </div>
      )}
    </ToolShell>
  );
}
