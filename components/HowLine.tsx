"use client";

import { useState } from "react";

export function HowLine({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="how">
      <button className="how-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "Hide how this was worked out" : "How was this worked out?"}
      </button>
      {open && <div className="how-body">{children}</div>}
    </div>
  );
}
