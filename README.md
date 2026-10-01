# Residual

Settlement tools for merchants. Upload your Razorpay report, see where your money went.

**Live:** [residual-beta.vercel.app](https://residual-beta.vercel.app) · free · nothing you upload is stored

## The problem

A merchant captures ₹8.9 lakh in a week and ₹7.7 lakh reaches the bank. The difference is fees, GST, TDS, refunds, disputes, holds and T+2 timing — and sometimes a payout that never arrived. Finding out which, by hand, from two exports that don't share a key, takes a day. I wanted it to take a few seconds and be provably right.

## The tools

| Tool | What it does |
|---|---|
| Settlement Reconciler | Breaks the gap between captured and banked into its causes |
| Missing Payout Finder | Finds payouts the gateway sent that never reached your bank |
| Fee Checker | Checks every fee against your contracted rate |
| GST Credit Checker | Finds GST you paid but can't claim yet |
| Statement Reader | Reads a bank statement (CSV or PDF, even password-protected) and checks it against its own balance |
| Ask Your Ledger | Answers any question about your payments |

Each result says what went in, what was checked, what needs chasing, and what it assumed — so if an assumption is wrong for you, you can see it.

## Decisions I made

**The answer is arithmetic, not a guess.** Every payment becomes double-entry bookkeeping, so the gap *has* to equal the movement of every other account. If anything is left unexplained, that's a bug, not a judgement call. Every week in the benchmark closes to ₹0.00.

**AI never produces a number.** The models only word explanations, write queries, and draft emails. Every figure they write is checked against what the books returned — if a model invents an amount or a UTR, its version is thrown away.

**Agents do the follow-up work.** After a result, an agent can independently re-derive a flagged line as a second opinion, or draft the email to send the gateway — payout trace, fee dispute, GST follow-up — ready to copy.

**Nothing is kept.** Files live in memory for 45 minutes and are gone. There's no login because there's nothing to log into.

## How it works

```mermaid
flowchart LR
  F["Your files"] --> P["Parse and check"]
  P --> L["Double-entry ledger"]
  L --> C["17 checks"]
  C --> R["Result: zero left unexplained"]
  R --> E["Explain"]
  R --> A["Agents: second opinion, draft email"]
  E --> G{"Every figure verified?"}
  A --> G
  G -->|yes| U["Shown to you"]
  G -->|no| T["Discarded, safe fallback used"]
```

```mermaid
flowchart LR
  B["Browser"] --> V["Vercel"]
  V --> N["Next.js tools"]
  V --> API["Python API: FastAPI + DuckDB"]
  API -. optional .-> M["Groq / Gemini / Claude"]
```

## Does it hold up

| | |
|---|---|
| Weekly closes reaching ₹0.00 unexplained | 13 / 13 |
| Causes the agent traced on its own | 87 / 87 |
| Fault-injection runs with every invariant holding | 3,000 |
| Tests | 620 |

I also ran it against real Razorpay test-mode payments. That caught a bug the simulated data never would: Razorpay reports its fee *including* GST, and I was counting the GST twice. Four real payments, four banks, all at exactly 2.0000% once fixed.

## Run it locally

```bash
uv sync --all-extras && npm install
npm run api     # engine on :8000
npm run dev     # tools on :3000
```

No API key needed. Add `GROQ_API_KEY` to `.env` if you want model-written explanations.

**Stack:** Python, DuckDB, FastAPI, Next.js, TypeScript, Tailwind, Vercel.

## What's left

- Check the live site actually uses the Groq key end to end
- Test it properly on a phone
- Show real progress from the server instead of timed steps
- Give Statement Reader its own agent
- Read scanned statements (needs OCR)
- Let merchants export results to Excel or PDF
- Remove the old CLI-era dashboard routes that nothing uses now

## Licence

MIT
