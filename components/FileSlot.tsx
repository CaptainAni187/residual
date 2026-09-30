"use client";

import { useRef, useState } from "react";
import { Info } from "@/components/Info";
import { size } from "@/lib/api";
import type { Slot } from "@/lib/tools";

export type SlotProblem = { message: string; fix?: string } | null;

const MAX = 4 * 1024 * 1024;

function precheck(slot: Slot, file: File): string | null {
  const name = file.name.toLowerCase();
  const allowed = slot.accept.split(",").filter((a) => a.startsWith(".")).map((a) => a.trim());
  if (allowed.length && !allowed.some((ext) => name.endsWith(ext))) {
    return `${file.name} is not ${slot.formats.split(" · ")[0]}.`;
  }
  if (file.size === 0) return `${file.name} is empty.`;
  if (file.size > MAX) return `${file.name} is ${size(file.size)} — the limit is 4 MB.`;
  return null;
}

export function FileSlot({
  slot,
  file,
  onChange,
  problem,
  password,
  onPassword,
  askPassword,
}: {
  slot: Slot;
  file: File | null;
  onChange: (file: File | null) => void;
  problem: SlotProblem;
  password?: string;
  onPassword?: (value: string) => void;
  askPassword?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [local, setLocal] = useState<string | null>(null);

  const take = (chosen: File | undefined) => {
    if (!chosen) return;
    const why = precheck(slot, chosen);
    setLocal(why);
    onChange(why ? null : chosen);
  };

  const shown = local ? { message: local, fix: `Choose a ${slot.formats.split(" · ")[0]} file.` } : problem;

  return (
    <div className="slot" data-state={shown ? "error" : file ? "ready" : "empty"}>
      <div className="slot-head">
        <span className="slot-label">{slot.label}</span>
        <span className={`badge ${slot.required ? "badge-req" : "badge-opt"}`}>{slot.required ? "Required" : "Optional"}</span>
        <Info label={`About the ${slot.label}`}>{slot.help}</Info>
      </div>

      {file ? (
        <div className="chip">
          <span className="chip-icon" aria-hidden="true">
            {file.name.toLowerCase().endsWith(".pdf") ? "PDF" : file.name.toLowerCase().endsWith(".csv") ? "CSV" : "JSON"}
          </span>
          <span className="chip-name">{file.name}</span>
          <span className="chip-size">{size(file.size)}</span>
          <button
            type="button"
            className="chip-x"
            aria-label={`Remove ${file.name}`}
            onClick={() => {
              setLocal(null);
              onChange(null);
            }}
          >
            ×
          </button>
        </div>
      ) : (
        <div
          className="drop"
          data-over={over}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            take(e.dataTransfer.files?.[0]);
          }}
        >
          <span className="drop-main">{over ? "Release to add" : "Drop file here or click to choose"}</span>
          <span className="drop-sub">{slot.formats}</span>
        </div>
      )}

      <input
        ref={input}
        type="file"
        accept={slot.accept}
        hidden
        onChange={(e) => {
          take(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      {shown && (
        <div className="slot-err" role="alert">
          <b>{shown.message}</b>
          {shown.fix && <span>{shown.fix}</span>}
        </div>
      )}
      {askPassword && file && (
        <div className="pw">
          <label className="lbl" htmlFor={`${slot.id}-pw`}>Statement password</label>
          <input
            id={`${slot.id}-pw`}
            className="field"
            type="password"
            autoComplete="off"
            value={password ?? ""}
            onChange={(e) => onPassword?.(e.target.value)}
            placeholder="Used once to open the file, never stored"
          />
        </div>
      )}

    </div>
  );
}
