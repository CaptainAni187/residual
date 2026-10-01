export type Category = "Reconcile" | "Fees & tax" | "Statements" | "Ask";

export type Slot = {
  id: "recon" | "statement" | "gstr2b";
  label: string;
  required: boolean;
  accept: string;
  formats: string;
  help: string;
};

export type AgentAction =
  | { kind: "second_opinion"; cause?: string; label: string; does: string }
  | { kind: "draft"; draft: "escalate" | "payout_trace" | "fee_dispute" | "gst_followup"; label: string; does: string };

export type Tool = {
  slug: string;
  name: string;
  does: string;
  category: Category;
  icon: "scale" | "search" | "percent" | "receipt" | "table" | "ask";
  slots: Slot[];
  rates?: "optional" | "required";
  about: string;
  agents: AgentAction[];
  next?: { slug: string; label: string };
};

const REPORT: Slot = {
  id: "recon",
  label: "Razorpay report",
  required: true,
  accept: ".json,application/json",
  formats: "JSON · up to 4 MB",
  help: "Your settlement recon or payments report, downloaded as JSON from the reports section of your gateway dashboard. Pick the date range you want checked.",
};

const STATEMENT: Slot = {
  id: "statement",
  label: "Bank statement",
  required: false,
  accept: ".csv,.pdf,text/csv,application/pdf",
  formats: "CSV or PDF · up to 4 MB",
  help: "The statement for the account your payouts land in, from net banking, for the same dates. Password-protected PDFs are fine — you will be asked for the password, which is used once and never kept.",
};

export const RATES_HELP =
  "The fee you agreed per payment method, as a percentage. Leave the defaults if you are not sure — the result will say exactly what it assumed so you can correct it.";

export const TOOLS: Tool[] = [
  {
    slug: "reconcile",
    name: "Settlement Reconciler",
    does: "See exactly where the difference between captured and banked went.",
    category: "Reconcile",
    icon: "scale",
    slots: [REPORT, STATEMENT],
    rates: "optional",
    about:
      "Every payment becomes double-entry bookkeeping, so the difference between what you captured and what reached your bank has to equal the movement in every other account — fees, tax, refunds, holds, timing. Each line is measured with its own query, and the result only stands if nothing is left over.",
    agents: [
      { kind: "second_opinion", label: "Get a second opinion", does: "An agent is told only that the books are short, and has to work out on its own what caused the biggest flagged line." },
    ],
  },
  {
    slug: "missing",
    name: "Missing Payout Finder",
    does: "Find payouts the gateway sent that never reached your bank.",
    category: "Reconcile",
    icon: "search",
    slots: [REPORT, { ...STATEMENT, help: `${STATEMENT.help} Adding it lets this confirm against your bank rather than the gateway's record.` }],
    about:
      "A payout is marked missing when the gateway recorded it as executed and no bank credit can be linked to its UTR more than three working days later. Payouts still inside that window are counted as in transit, not lost.",
    agents: [
      { kind: "second_opinion", cause: "settlement_never_arrived", label: "Double-check it", does: "An agent has to trace the missing amount to its cause on its own, without being told it is a lost payout." },
    ],
  },
  {
    slug: "fees",
    name: "Fee Checker",
    does: "Check what you were charged against your contracted rates.",
    category: "Fees & tax",
    icon: "percent",
    slots: [REPORT],
    rates: "required",
    about:
      "Each captured payment is priced at your contracted rate for its method and compared with the fee actually billed. The gateway reports its fee including GST, so the tax is separated out first — otherwise every fee would look 18% too high.",
    agents: [
      { kind: "second_opinion", cause: "fee_rate_increase", label: "Double-check it", does: "An agent has to find the overcharge on its own, without being told it is a fee problem." },
    ],
  },
  {
    slug: "gst",
    name: "GST Credit Checker",
    does: "Find input credit you have paid for but cannot claim yet.",
    category: "Fees & tax",
    icon: "receipt",
    slots: [
      REPORT,
      {
        id: "gstr2b",
        label: "GSTR-2B",
        required: true,
        accept: ".json,.csv,application/json,text/csv",
        formats: "JSON or CSV · up to 4 MB",
        help: "Your auto-drafted input tax credit statement, downloaded from the GST portal for the same period as the report.",
      },
    ],
    about:
      "GST on a gateway's fee is only claimable once the gateway has declared that invoice, at which point it appears in your GSTR-2B. This compares the tax you actually paid with what the return makes available, and checks the supplier GSTIN is valid.",
    agents: [],
  },
  {
    slug: "statement",
    name: "Statement Reader",
    does: "Turn a bank statement into a clean table that ties to its own balance.",
    category: "Statements",
    icon: "table",
    slots: [{ ...STATEMENT, required: true }],
    about:
      "The layout is detected automatically. A statement prints a running balance, so it carries its own check: each balance must equal the one before plus credits minus debits. Any row that disagrees is shown.",
    agents: [],
    next: { slug: "reconcile", label: "Reconcile this statement against your gateway report" },
  },
  {
    slug: "ask",
    name: "Ask Your Ledger",
    does: "Ask anything about your payments and get the answer.",
    category: "Ask",
    icon: "ask",
    slots: [REPORT],
    about:
      "Your question is turned into a single read-only query over your data, and the query is shown with every answer. Anything that is not a plain read — changing or deleting data — is refused before it runs.",
    agents: [],
  },
];

export const CATEGORIES: ("All" | Category)[] = ["All", "Reconcile", "Fees & tax", "Statements", "Ask"];

export const bySlug = (slug: string) => TOOLS.find((t) => t.slug === slug);
