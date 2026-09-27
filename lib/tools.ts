export type Tool = {
  slug: string;
  name: string;
  does: string;
  needs: string;
  icon: "scale" | "search" | "percent" | "receipt" | "table" | "ask";
};

export const TOOLS: Tool[] = [
  {
    slug: "reconcile",
    name: "Settlement Reconciler",
    does: "See exactly where the difference between captured and banked went.",
    needs: "Razorpay report · bank statement",
    icon: "scale",
  },
  {
    slug: "missing",
    name: "Missing Payout Finder",
    does: "Find payouts the gateway sent that never reached your bank.",
    needs: "Razorpay report",
    icon: "search",
  },
  {
    slug: "fees",
    name: "Fee Checker",
    does: "Check what you were charged against your contracted rates.",
    needs: "Razorpay report · your rates",
    icon: "percent",
  },
  {
    slug: "gst",
    name: "GST Credit Checker",
    does: "Find input credit you have paid for but cannot claim yet.",
    needs: "Razorpay report · GSTR-2B",
    icon: "receipt",
  },
  {
    slug: "statement",
    name: "Statement Reader",
    does: "Turn a bank statement into a clean table that ties to its own balance.",
    needs: "Bank statement CSV",
    icon: "table",
  },
  {
    slug: "ask",
    name: "Ask Your Ledger",
    does: "Ask anything about your payments and get the answer.",
    needs: "Razorpay report",
    icon: "ask",
  },
];

export const bySlug = (slug: string) => TOOLS.find((t) => t.slug === slug);
