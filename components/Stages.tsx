"use client";

import { useEffect, useState } from "react";

export function Stages({ steps, done, failed }: { steps: string[]; done: boolean; failed: boolean }) {
  const [at, setAt] = useState(0);

  useEffect(() => {
    if (done || failed) return;
    const timer = setInterval(() => setAt((i) => Math.min(i + 1, steps.length - 1)), 650);
    return () => clearInterval(timer);
  }, [done, failed, steps.length]);

  const current = done ? steps.length : at;
  return (
    <ol className="stages" aria-live="polite">
      {steps.map((step, i) => {
        const state = i < current ? "done" : i === current ? (failed ? "fail" : "run") : "wait";
        return (
          <li key={step} data-state={state} style={{ animationDelay: `${i * 60}ms` }}>
            <span className="stage-mark" aria-hidden="true">
              {state === "done" ? "✓" : state === "fail" ? "!" : ""}
            </span>
            {step}
          </li>
        );
      })}
    </ol>
  );
}
