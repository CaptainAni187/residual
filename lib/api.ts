export type Finding = {
  cause: string;
  title: string;
  amount_paise: number;
  amount: string;
  alarming: boolean;
  note: string;
  sql: string;
};

export type CloseResult = {
  token: string;
  source: string;
  start: string;
  end: string;
  gross_paise: number;
  landed_paise: number;
  gap_paise: number;
  residual_paise: number;
  closes: boolean;
  covered: boolean;
  checked: number;
  findings: Finding[];
  unresolved: { kind: string; detail: string; amount: string }[];
};

export type Explanation = { cause: string; text: string; source: string; reason: string };

export type Answer = {
  question: string;
  sql: string;
  source: string;
  columns: string[];
  rows: string[][];
  note: string;
};

export type Step = { tool: string; args: Record<string, unknown>; ok: boolean; found: string };

export type Investigation = {
  driver: string;
  short_by: string;
  steps: Step[];
  proposal: { name: string; accounts: string[]; sql: string } | null;
  verdict: string;
  accepted: boolean;
};

export type Providers = { configured: string[]; using: string; note: string };

export class ApiError extends Error {}

async function unwrap<T>(reply: Response): Promise<T> {
  const body = await reply.json().catch(() => null);
  if (!reply.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? String((body as { detail: unknown }).detail)
        : `request failed with ${reply.status}`;
    throw new ApiError(detail);
  }
  return body as T;
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  return unwrap<T>(
    await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function providers(): Promise<Providers> {
  return unwrap<Providers>(await fetch("/api/providers"));
}

export async function closeUpload(
  recon: File,
  statement: File | null,
  contract: string,
): Promise<CloseResult> {
  const form = new FormData();
  form.append("recon", recon);
  if (statement) form.append("statement", statement);
  form.append("contract", contract);
  return unwrap<CloseResult>(await fetch("/api/close", { method: "POST", body: form }));
}

export async function demoClose(week = 8): Promise<CloseResult> {
  return unwrap<CloseResult>(await fetch(`/api/demo?week=${week}`, { method: "POST" }));
}

export type StatementRow = {
  date: string;
  narration: string;
  ref: string;
  debit_paise: number;
  credit_paise: number;
  balance_paise: number | null;
};

export type StatementOut = {
  rows: StatementRow[];
  strategy: string;
  ties_to_balance: boolean;
  balances_checked: number;
  rows_disagreeing: number;
  skipped: string[];
  credits_paise: number;
  debits_paise: number;
};

export type GstOut = {
  source: string;
  paid_paise: number;
  claimable_paise: number;
  at_risk_paise: number;
  invoices: number;
  risks: { kind: string; title: string; amount: string; amount_paise: number; detail: string; action: string }[];
};

export async function readStatement(file: File): Promise<StatementOut> {
  const form = new FormData();
  form.append("statement", file);
  return unwrap<StatementOut>(await fetch("/api/statement", { method: "POST", body: form }));
}

export async function checkGst(recon: File, gstr2b: File): Promise<GstOut> {
  const form = new FormData();
  form.append("recon", recon);
  form.append("gstr2b", gstr2b);
  return unwrap<GstOut>(await fetch("/api/gst", { method: "POST", body: form }));
}

export const explainCause = (token: string, cause: string) =>
  post<Explanation>("/api/explain", { token, cause });

export const askQuestion = (token: string, question: string) =>
  post<Answer>("/api/ask", { token, question });

export const investigate = (token: string) =>
  post<Investigation>("/api/investigate", { token });

export function rupees(paise: number): string {
  const negative = paise < 0;
  const whole = Math.abs(paise);
  const pa = String(whole % 100).padStart(2, "0");
  let digits = String(Math.floor(whole / 100));
  if (digits.length > 3) {
    const tail = digits.slice(-3);
    let head = digits.slice(0, -3);
    const groups: string[] = [];
    while (head.length > 2) {
      groups.unshift(head.slice(-2));
      head = head.slice(0, -2);
    }
    if (head) groups.unshift(head);
    digits = `${groups.join(",")},${tail}`;
  }
  return `${negative ? "(" : ""}${digits}.${pa}${negative ? ")" : ""}`;
}
