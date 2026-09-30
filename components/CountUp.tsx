"use client";

import { useEffect, useState } from "react";
import { rupees } from "@/lib/api";

export function CountUp({ paise, className }: { paise: number; className?: string }) {
  const [shown, setShown] = useState(0);

  useEffect(() => {
    const still = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) {
      setShown(paise);
      return;
    }
    let frame = 0;
    const began = performance.now();
    const run = (now: number) => {
      const t = Math.min(1, (now - began) / 750);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(Math.round(paise * eased));
      if (t < 1) frame = requestAnimationFrame(run);
    };
    frame = requestAnimationFrame(run);
    return () => cancelAnimationFrame(frame);
  }, [paise]);

  return <span className={className}>{rupees(shown)}</span>;
}
