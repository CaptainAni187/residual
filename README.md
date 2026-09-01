# Residual

A reconciliation tool for merchants on a payment gateway. It answers one
question: money was captured, less money reached the bank, where did the
difference go?

The output is a decomposition — every cause with an exact rupee amount and the
SQL query behind it. When it can't explain something it says so instead of
guessing.

## Quick start

```bash
uv sync --all-extras
uv run residual demo        # guided walkthrough, eight steps
uv run residual serve       # same thing in a browser
```

No API key or network needed for anything here.

## Example

```
Close 2026-03-02 .. 2026-03-08     17 hypotheses checked

  gross captured    INR 8,89,262.00
  cash landed       INR 7,71,724.29
  gap               INR 1,17,537.71

  risk_hold                   -INR 4,12,000.00  !   funds released this window
  captured_not_yet_settled     INR 2,01,849.03      ordinary T+2 timing
  bank_holiday_delay           INR 1,77,398.78      T+2 fell on a bank holiday
  settlement_never_arrived     INR 1,09,084.16  !   no credit links to this UTR
  refunds_issued                 INR 22,639.90
  normal_fee                     INR 17,785.24      priced at contracted rates
  dispute_reserve_held           -INR 5,305.00      reserve released
  gst_on_fee                      INR 3,283.40
  route_split                     INR 2,220.96
  fee_rate_increase                 INR 455.90  !   card billed above contract
  tds_194o                          INR 125.34

  residual                 INR 0.00
```

## How it works

Every event becomes double-entry postings that sum to zero, enforced when the
entry is constructed. Because each entry sums to zero, so does the movement of
every account over any window. Rearranged:

```
gross captured − cash landed = Σ (movement of every other account)
```

So the decomposition is an accounting identity, not a heuristic. A non-zero
residual means an event moved money without a posting.

Two things guard it:

- `test_variance_always_closes_to_zero` runs the identity over generated event
  streams and every sub-window of them.
- `test_verifiers_partition_every_account` proves each account is claimed by its
  verifiers exactly once. A zero residual could be reached by two mistakes
  cancelling; a clean partition can't.

## Results

13 weekly closes over a 90-day simulated merchant, 4,602 events, ₹1.12 Cr
captured. Regenerate with `residual evaluate` and `residual ablate`.

| | |
|---|---|
| Residual close rate | 100.00% |
| Hallucinated-cause rate | 0.00% |
| Cause precision / recall | 100% / 100% |
| Rupee error | ₹0.00 |
| Linkage coverage | 96.9% |
| Silently wrong links | 0 |

### What each design decision is worth

| | with | without |
|---|---|---|
| Linkage layer vs `narration LIKE '%utr%'` | 1 settlement reported missing | 23 reported missing |
| Two-pass ambiguity detection vs greedy | 0 silently wrong, 2 abstained | 2 silently wrong, 0 abstained |
| Verifier-grounded memo vs raw-ledger prose | 0/78 figures untraceable | 66/66 untraceable |

One settlement was actually lost. Substring matching calls another 22 lost too —
they arrived with a truncated or missing reference. Greedy matching never
abstains, so it always looks more confident; when two identical credits arrive
out of order it is wrong with the same confidence it has when it's right.

## Design notes

**The model never asserts a number.** It starts with no figures at all and can
only learn one by calling a verifier that runs real SQL. The memo is then parsed
back and every rupee figure matched against what those calls returned. A figure
with no source means the memo is withheld.

**Uncertainty lives in one place.** Linkage — deciding which bank credit cleared
which payout — is the only genuinely uncertain step, and the arithmetic never
depends on it. A credit posts to the bank whether or not we can name its payout.
So the matcher is allowed to abstain, and the metric is the silently-wrong rate
rather than accuracy.

**Two clocks.** Every event carries `occurred_at` and `recorded_at`. A chargeback
happens on the day of the payment and is notified a fortnight later, so a close
signed on the 10th can't contain it. Replaying with a `known_by` cutoff
reproduces the close as it could actually have been run. Nine of thirteen closes
in the benchmark move after signing.

**Money is integer paise.** No code path builds money from a float. Currency is
carried on the value, so a stray USD line can't enter an INR position.

**The banking calendar is data.** RBI banks close on the second and fourth
Saturday and stay open on the first, third and fifth. It's loaded as a DuckDB
table so a verifier reasoning about timing does it in the SQL it hands back.

## Calibrated against published rates

`residual calibration` checks each simulator parameter against what the model
emits.

| parameter | published | simulated | source |
|---|---|---|---|
| gateway fee | 2.00% | 2.03% | Razorpay rate card |
| GST on fee | 18.00% | 18.00% | GST Act |
| TDS s.194-O | 0.10% | 0.10% | Finance (No. 2) Act 2024 |
| card chargebacks | 0.60% | 0.50% | e-commerce average; Visa acts at 0.90% |
| settlement cycle | T+2 working | T+2 working | Razorpay settlement docs |
| annual growth | +32% | +32% | Indian D2C GMV, FY26 |

UPI carries ~85% of India's retail digital volume; this models an online
merchant on a gateway where cards and netbanking carry higher-value baskets, so
the mix is 72%. Razorpay's pricing page says T+1 while the settlement docs say
T+2 working days — the settlement docs win.

## Cash forecast

The deliverable is a floor, not a point estimate:

```
at least INR 15,75,830.28 by 2026-11-15 (90% confidence, INR 16,93,604.19 expected)
```

Split conformal assumes exchangeability, which a time series violates, and
under-covers. Adaptive conformal inference (Gibbs and Candès, 2021) updates its
own level after every observation:

```
α(t+1) = α(t) + γ · (α − breached(t))
```

Measured across five merchant-years, both land at or above a 90% target with the
floor at 91% of what actually arrived. Tightness is reported next to coverage
because a floor of zero is never breached and never useful.

Point-estimate accuracy is 4.9% MAPE at fourteen days on an ordinary merchant,
15.5% on the benchmark quarter — which deliberately holds a risk hold, a lost
payout, a partial settlement and a dispute cluster.

The model also reports when it has stopped working. When volume halved in
testing, the method absorbed it: the interval widened until the floor sat on the
new level and reported everything was fine. It was — and it was useless. So the
signal is the width the model needs to stay honest.

## Deterministic simulation testing

Faults are injected on a seeded schedule and every invariant is checked after
every delivery: `duplicate_batch`, `crash_and_replay`, `reorder`, `split`,
`delay`, `replay_old`, `truncate`.

```bash
uv run residual dst --seeds 3000
```

The property that matters is convergence — for any schedule of faults the system
claims to survive, the final books must be identical to a clean run. Truncation
is excluded: if a file's tail never arrives, those facts are genuinely absent.

A failure prints its seed, `residual dst --seed 8214` replays it exactly, and it
then shrinks to the minimal set of faults that still breaks it. Three bugs are
planted in the test suite and each must be caught.

## Real files

```bash
uv run residual reconcile \
  --recon tests/fixtures/recon_march.json \
  --statement tests/fixtures/statements/hdfc.pdf \
  --contract "card=2.00" \
  --gstr2b tests/fixtures/gst/gstr2b_march.json
```

HDFC, ICICI, SBI and Axis CSV formats parse, plus generated PDFs. It handles
CRLF and BOM from Windows exports, cp1252 encoding, commas inside quoted
narrations, opening balance rows, summary blocks, overdrawn accounts and a
running balance printed only on the last line of each day.

A statement carries a running balance, so the file contains its own check: the
change in balance must equal credit minus debit. For PDFs that check also picks
the extraction strategy — every strategy runs and the balance decides.

Debits split two ways: charges the bank levied, and the merchant's own spending.
Counting all of it as bank charges once put ₹3.3L of salary into the cost of the
payment relationship.

## Tax

GST on gateway fees is only claimable if the gateway declared the invoice in
their GSTR-1, from where it reaches GSTR-2B. The tax paid and the tax claimable
are two numbers from two systems.

```
At risk  — not part of the gap
  INR 1,800.00  Credit sitting against a GSTIN that does not validate
  INR   276.53  Input credit paid but not available to claim
```

A risk is never a cause. A finding explains cash that already moved and sums
into the gap; a risk explains cash that will move if nobody chases a filing.

## Throughput

`residual benchmark --days 365 --volume 260` — 115,625 events, 505,159 postings,
₹28.8 Cr across 53 closes, ~40 ms per close, every residual zero. No model is
called during a close.

## Commands

```bash
uv run pytest                          # 428 tests
uv run residual demo                   # guided walkthrough
uv run residual serve                  # dashboard
uv run residual close --week 8 --show-sql
uv run residual evaluate
uv run residual ablate
uv run residual dst --seeds 3000
uv run residual calibration
uv run residual certify-forecast
uv run residual outlook --history 400
uv run residual backtest-forecast --horizon 14
uv run residual memo --week 8
uv run residual restate-close --week 2
uv run residual pack --week 8
uv run residual ask "which settlements never arrived?"
uv run residual bank-statement --file tests/fixtures/statements/hdfc.pdf
uv run residual ingest --file tests/fixtures/recon_sample.json
uv run residual simulate-world && uv run residual verify-log
uv run residual benchmark --days 365 --volume 260
uv run residual check-live             # needs test-mode keys in .env
```

## Layout

```
domain/     vocabulary shared by everything — causes, banking calendar
ledger/     money, accounts, events, postings, hash-chained log, DuckDB view
recon/      matching bank credits to settlements
simulate/   the generated merchant and its ground truth
position/   cash position, forecast, conformal intervals
explain/    verifiers, close engine, agent, grounding, close pack
eval/       scoring against ground truth, ablations
ingest/     Razorpay recon, bank statements (CSV and PDF), GSTR-2B
dst/        fault injection and the simulation harness
web/        dashboard
```

Package dependencies are acyclic; production code never imports the simulator.

## Security

- SQL is bound, never formatted. Method names are checked against known
  instruments.
- Generated SQL for the Q&A layer is validated on DuckDB's parse tree, not a
  denylist. Fifteen attacks are refused in the test suite.
- Bank narrations are third-party text: normalised, wrapped, and flagged if they
  look like instructions.
- File and field size ceilings; results capped.
- Credentials are read from the environment, never written. Live keys refused.
- Every query runs on a cursor belonging to the calling thread.

## Limitations

- The world is generated. Cause-level scoring is measured against ground truth
  the simulator produced, which is the only way to grade explanations and also
  the ceiling on what those scores mean.
- The Razorpay adapter authenticates against the live test-mode API, but a fresh
  test account has no settled transactions to read — settlements need an
  approved KYC.
- Scanned statements need OCR, which isn't attempted.
- Single currency, single merchant. No subscriptions, payment links or virtual
  accounts.
- Form 26AS figures for the TDS check are supplied by hand; there's no parser.
- The dashboard is read-only and unauthenticated. It binds to localhost.

## Licence

MIT. Dependencies are MIT, BSD, Apache-2.0 or 0BSD; Hypothesis is MPL-2.0 and
reportlab is BSD, both test-only.

No Razorpay API secret is committed or required — the simulator generates the
world. A test-mode adapter for `GET /v1/settlements/recon/combined` is optional
and reads its key from a gitignored `.env`.
