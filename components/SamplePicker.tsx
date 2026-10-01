"use client";

import { useState } from "react";
import { Info } from "@/components/Info";
import manifest from "@/public/samples/manifest.json";

type SampleSet = { title: string; story: string; files: Record<string, string>; password?: string };
export type Loaded = { files: Record<string, File>; password: string; rates: string; title: string };

const SETS = manifest.sets as Record<string, SampleSet>;
const KIND: Record<string, string> = { recon: "Report", statement: "Statement", gstr2b: "GSTR-2B" };

async function fetchFile(name: string): Promise<File> {
  const reply = await fetch(`/samples/${name}`);
  if (!reply.ok) throw new Error(`could not load ${name}`);
  const blob = await reply.blob();
  return new File([blob], name, { type: blob.type || "application/octet-stream" });
}

export function SamplePicker({
  tool,
  slots,
  onRun,
  disabled,
}: {
  tool: keyof typeof manifest.tools;
  slots: string[];
  onRun: (loaded: Loaded) => void;
  disabled?: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const keys = manifest.tools[tool] ?? [];

  const run = async (key: string) => {
    const set = SETS[key];
    setBusy(key);
    setError("");
    try {
      const wanted = Object.entries(set.files).filter(([slot]) => slots.includes(slot));
      const files = Object.fromEntries(await Promise.all(wanted.map(async ([slot, name]) => [slot, await fetchFile(name)])));
      onRun({ files, password: set.password ?? "", rates: manifest.rates, title: set.title });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load that sample.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="samples">
      <div className="samples-head">
        <b>No files to hand? Use a sample</b>
        <Info label="About the samples">
          These are real files, not saved results. Running one uploads it and processes it live, exactly like your own.
          Download one, change anything you like, and drop it back in — the result follows your edit.
        </Info>
      </div>
      <div className="sample-grid">
        {keys.map((key) => {
          const set = SETS[key];
          return (
            <div className="sample" key={key}>
              <b>{set.title}</b>
              <p>{set.story}</p>
              <div className="sample-files">
                {Object.entries(set.files)
                  .filter(([slot]) => slots.includes(slot))
                  .map(([slot, name]) => (
                    <a key={name} href={`/samples/${name}`} download className="sample-file" title={`Download ${name}`}>
                      ↓ {KIND[slot] ?? slot}
                    </a>
                  ))}
              </div>
              <button className="btn btn-sm" onClick={() => run(key)} disabled={disabled || busy !== null}>
                {busy === key ? "Loading…" : "Run it"}
              </button>
            </div>
          );
        })}
      </div>
      {error && <p className="err">{error}</p>}
    </section>
  );
}
