// CSV the way Excel opens it cleanly: a byte-order mark for ₹ and Indian names, every cell quoted,
// and money as plain numbers so it can be summed, not "₹1,23,456.00" text.

export type Cell = string | number | null | undefined;

const quote = (value: Cell) => `"${String(value ?? "").replace(/"/g, '""')}"`;

export const rupeesCell = (paise: number | null | undefined) => (paise === null || paise === undefined ? "" : (paise / 100).toFixed(2));

export function downloadCsv(filename: string, header: string[], rows: Cell[][]) {
  const text = [header, ...rows].map((row) => row.map(quote).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["﻿", text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename.replace(/[^\w.-]+/g, "_");
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const stem = (name: string) => name.replace(/\.[^.]+$/, "");
