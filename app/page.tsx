"use client";

import Link from "next/link";
import { useState } from "react";
import { Icon } from "@/components/Icon";
import { useFiles } from "@/lib/files";
import { CATEGORIES, TOOLS } from "@/lib/tools";

export default function Home() {
  const [cat, setCat] = useState<(typeof CATEGORIES)[number]>("All");
  const { files } = useFiles();
  const carried = Object.values(files).filter(Boolean).length;
  const shown = TOOLS.filter((t) => cat === "All" || t.category === cat);

  return (
    <main className="home">
      <div className="hero-wrap rise">
        <h1 className="hero">Every settlement tool a merchant needs, in one place</h1>
        <p className="hero-sub">Free. Nothing you upload is kept.</p>
      </div>

      <div className="filters rise" role="tablist" aria-label="Filter tools">
        {CATEGORIES.map((c) => (
          <button key={c} role="tab" aria-selected={cat === c} className="filter" data-on={cat === c} onClick={() => setCat(c)}>
            {c}
          </button>
        ))}
      </div>

      {carried > 0 && (
        <p className="carried rise">
          {carried} file{carried === 1 ? " is" : "s are"} ready from your last tool — open another and it is already added.
        </p>
      )}

      <div className="grid">
        {shown.map((tool, i) => (
          <Link key={tool.slug} href={`/${tool.slug}`} className="card card-in" style={{ animationDelay: `${i * 55}ms` }}>
            <span className="card-icon"><Icon name={tool.icon} size={22} /></span>
            <span className="card-name">{tool.name}</span>
            <span className="card-does">{tool.does}</span>
            <span className="card-go" aria-hidden="true">Open →</span>
          </Link>
        ))}
      </div>
    </main>
  );
}
