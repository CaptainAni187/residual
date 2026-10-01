export type Finding = {
  cause: string;
  title: string;
  amount_paise: number;
  amount: string;
  alarming: boolean;
  note: string;
  sql: string;
  refs: string[];
};

export type Inputs = {
  files: string[];
  kind: string;
  rows_in: number;
  events: number;
  period: string;
  covers: string;
  days: number;
  checks_run: number;
};

export type Action = {
  cause: string;
  title: string;
  why: string;
  amount_paise: number;
  urgency: "high" | "medium" | "low";
  who: string;
  deadline: string;
  draft: string;
  impact: string;
};

export type Insight = {
  totals: Record<string, number>;
  labels: Record<string, string>;
  recoverable_paise: number;
  actions: Action[];
};

export type Week = {
  start: string;
  end: string;
  gross_paise: number;
  gap_paise: number;
  totals: Record<string, number>;
  fee_rate: number;
  contract_rate: number;
  overcharge_paise: number;
  flagged: number;
};

export type Quarter = {
  source: string;
  covers: string;
  days: number;
  gross_paise: number;
  landed_paise: number;
  gap_paise: number;
  residual_paise: number;
  insight: Insight;
  weeks: Week[];
  causes: { cause: string; title: string; amount_paise: number; bucket: string; note: string }[];
  hike_started: string;
  contracted_rate: number;
  headline: string[];
  assumptions: string[];
};

export type CloseResult = {
  insight: Insight;
  inputs: Inputs;
  assumptions: string[];
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
  mode: "open_residual" | "second_opinion";
  cause: string;
  driver: string;
  short_by: string;
  steps: Step[];
  proposal: { name: string; accounts: string[]; sql: string } | null;
  verdict: string;
  accepted: boolean;
};

export type DraftKind = "escalate" | "payout_trace" | "fee_dispute" | "gst_followup";

export type Draft = {
  kind: DraftKind;
  to: string;
  subject: string;
  body: string;
  source: string;
  reason: string;
  items: number;
};

export type StatementRow = {
  date: string;
  narration: string;
  ref: string;
  debit_paise: number;
  credit_paise: number;
  balance_paise: number | null;
};

export type StatementOut = {
  source: string;
  rows: StatementRow[];
  strategy: string;
  ties_to_balance: boolean;
  balances_checked: number;
  rows_disagreeing: number;
  skipped: string[];
  credits_paise: number;
  debits_paise: number;
  first: string;
  last: string;
  gateway_credits: number;
  assumptions: string[];
};

export type GstOut = {
  token: string;
  source: string;
  period: string;
  paid_paise: number;
  claimable_paise: number;
  at_risk_paise: number;
  invoices: number;
  risks: { kind: string; title: string; amount: string; amount_paise: number; detail: string; action: string }[];
  assumptions: string[];
};

export type Providers = { configured: string[]; using: string; note: string };

export class ApiError extends Error {
  code: string;
  fix: string;
  field: string;
  status: number;

  constructor(message: string, code = "error", fix = "", field = "", status = 0) {
    super(message);
    this.code = code;
    this.fix = fix;
    this.field = field;
    this.status = status;
  }
}

async function unwrap<T>(reply: Response): Promise<T> {
  let body: unknown = null;
  try {
    body = await reply.json();
  } catch {
    body = null;
  }
  if (!reply.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
    if (detail && typeof detail === "object" && "message" in detail) {
      const d = detail as { code?: string; message: string; fix?: string; field?: string };
      throw new ApiError(d.message, d.code ?? "error", d.fix ?? "", d.field ?? "", reply.status);
    }
    if (Array.isArray(detail)) {
      throw new ApiError("Some of the details entered are not valid.", "invalid", "Check the highlighted fields.", "", reply.status);
    }
    if (typeof detail === "string") throw new ApiError(detail, "error", "", "", reply.status);
    if (reply.status >= 500 || reply.status === 0) {
      throw new ApiError(
        "The server could not be reached.",
        "unreachable",
        "It may be starting up. Try again in a few seconds — your files are still selected.",
        "",
        reply.status,
      );
    }
    throw new ApiError(`That did not work (${reply.status}).`, "error", "Try again.", "", reply.status);
  }
  return body as T;
}

async function send<T>(path: string, init: RequestInit): Promise<T> {
  let reply: Response;
  try {
    reply = await fetch(path, init);
  } catch {
    throw new ApiError(
      "Could not reach the server.",
      "offline",
      "Check your connection and try again — your files are still selected.",
    );
  }
  return unwrap<T>(reply);
}

const json = (payload: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(payload),
});

export const providers = () => send<Providers>("/api/providers", { method: "GET" });

export function closeUpload(recon: File, statement: File | null, contract: string, password = "") {
  const form = new FormData();
  form.append("recon", recon);
  if (statement) form.append("statement", statement);
  form.append("statement_password", password);
  form.append("contract", contract);
  return send<CloseResult>("/api/close", { method: "POST", body: form });
}

export const demoClose = () => send<CloseResult>("/api/demo", { method: "POST" });

export function readStatement(file: File, password = "") {
  const form = new FormData();
  form.append("statement", file);
  form.append("password", password);
  return send<StatementOut>("/api/statement", { method: "POST", body: form });
}

export function checkGst(recon: File, gstr2b: File) {
  const form = new FormData();
  form.append("recon", recon);
  form.append("gstr2b", gstr2b);
  return send<GstOut>("/api/gst", { method: "POST", body: form });
}

export const explainCause = (token: string, cause: string) => send<Explanation>("/api/explain", json({ token, cause }));
export const askQuestion = (token: string, question: string) => send<Answer>("/api/ask", json({ token, question }));
export const investigate = (token: string, cause = "") => send<Investigation>("/api/investigate", json({ token, cause }));
export const quarter = (token: string) => send<Quarter>("/api/quarter", json({ token }));
export const draftAction = (token: string, kind: DraftKind) => send<Draft>("/api/draft", json({ token, kind }));

export function rupees(paise: number): string {
  const negative = paise < 0;
  const whole = Math.abs(Math.round(paise));
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
  return `${negative ? "−" : ""}₹${digits}.${pa}`;
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
