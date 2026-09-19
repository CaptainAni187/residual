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

No API key, no account, no network. Nothing in this project calls an external
service.

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
| Grounded memo vs prose off the raw ledger | 0/78 figures untraceable | 66/66 untraceable |

One settlement was actually lost. Substring matching calls another 22 lost too —
they arrived with a truncated or missing reference. Greedy matching never
abstains, so it always looks more confident; when two identical credits arrive
out of order it is wrong with the same confidence it has when it's right.

## Design notes

**Nothing that writes prose is allowed to assert a number.** The memo writer
starts with no figures at all and can only obtain one by calling a verifier that
runs real SQL. The finished text is then parsed back and every rupee figure
matched against what those calls returned. A figure with no source means the
memo is withheld rather than printed. The gate is a boundary, not a property of
whoever is writing: swap in a different writer and it is held to the same rule,
which is what `test_an_ungrounded_memo_is_withheld_not_printed` checks by
handing it a writer that fabricates.

**Uncertainty lives in one place.** Linkage — deciding which bank credit cleared
which payout — is the only genuinely uncertain step, and the arithmetic never
depends on it. A credit posts to the bank whether or not we can name its payout.
So the matcher is allowed to abstain, and the metric is the silently-wrong rate
rather than accuracy.

**Two clocks.** Every event carries `occurred_at` and `recorded_at`. A chargeback
happens on the day of the payment and is notified a fortnight later, so a close
signed on the 10th can't contain it. Replaying with a `known_by` cutoff
reproduces the close as it could actually have been run. Six of thirteen closes
in the benchmark change composition after signing; none of them change the gap
total. The period still adds up — what moves is what it was made of.

**Money is integer paise.** No code path builds money from a float. Currency is
carried on the value, so a stray USD line can't enter an INR position.

**The banking calendar is data.** RBI banks close on the second and fourth
Saturday and stay open on the first, third and fifth. It's loaded as a DuckDB
table so a verifier reasoning about timing does it in the SQL it hands back.

## Calibrated against published rates

`residual calibration` checks each simulator parameter against what the
simulator emits.

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

The forecast also reports when it has stopped working. When volume halved in
testing, the method absorbed it: the interval widened until the floor sat on the
new level and reported everything was fine. It was — and it was useless. So the
signal is the width it needs to stay honest.

## An agent that investigates what the verifiers missed

The close itself is deliberately not agentic. Its value is that it is
deterministic and provable, and putting a model in that path would be a
downgrade. But one task here is irreducibly open-ended: when the books do not
close, somebody has to work out why — query, look, form a hypothesis, query
again.

`residual investigate` removes a verifier. The residual opens by exactly what
that verifier used to claim, and an agent is handed a toolbelt over the ledger:

| tool | what it answers |
|---|---|
| `unbalanced_accounts` | which accounts are short, and by how much |
| `event_types_touching` | what kind of event moved an account |
| `account_movement` | net movement on one account, or one slice of it |
| `fee_at_contracted_rate` | what the fee should have been under the contract |
| `fee_as_billed` | what the gateway actually charged |
| `run_query` | arbitrary read-only SQL, validated on the parse tree |

It has to name the mechanism and hand back one query. The answer is accepted
only if that query returns the shortfall **to the paise** and adding its claim
leaves **every** account balanced. Hitting the number on the wrong account is
refused.

### A real transcript

The longest investigation in the quarter — `fee_rate_increase`, short by ₹455.90:

```
 1  unbalanced_accounts()
 2  account_movement(fee_expense)                        INR 18,241.14
 3  event_types_touching(fee_expense)
 4  account_movement(fee_expense, payment_captured)      INR 18,241.14
 5  account_movement(fee_expense, settlement_executed)        INR 0.00
 6  fee_at_contracted_rate()                             INR 17,785.24
 7  fee_as_billed()                                      INR 18,241.14
 -> accepted - INR 455.90 on fee_expense
```

It establishes that all of `fee_expense` moved on capture and none on
settlement, then prices those captures at the contracted rate and compares that
against what was billed. The difference is the overcharge. None of it is a fixed
script — steps 4 and 5 exist because step 3 said those were the event types
worth looking at.

### The result

| driver | solved | wrongly accepted |
|---|---|---|
| largest moving account (baseline) | 0 / 87 | 0 |
| `Analyst` — deterministic policy over the toolbelt | **87 / 87** | **0** |

Median 2 tool calls, 7 at worst. A further 13 cases are excluded by design, for
a reason worth stating: `captured_not_yet_settled` claims the whole receivable
and `bank_holiday_delay` *refines* it, so removing the parent leaves *parent
minus child* — an arithmetic artifact rather than a cause anyone would name.
`test_the_refinement_artifact_is_the_parent_minus_the_child` proves that
identity instead of asserting it.

```bash
uv run residual investigate --week 8 --cause fee_rate_increase --trace
uv run residual investigate --all-weeks
```

### Putting a model in the same seat

The driver is the only swappable part. `ModelDriver` speaks Anthropic's tool-use
API; `OpenAIDriver` speaks the OpenAI chat-completions shape, which covers Groq,
Gemini, Grok and OpenRouter — several of which have a free tier, so the model arm
can be measured without a paid key.

```bash
uv run residual investigate --all-weeks --provider groq      # GROQ_API_KEY
uv run residual investigate --all-weeks --provider gemini    # GEMINI_API_KEY
uv run residual investigate --all-weeks --provider anthropic # ANTHROPIC_API_KEY
```

Whichever drives, the tools, the transcript and the adjudicator are identical, so
the numbers are comparable. Nothing a driver says is believed until the books
agree with it.

### Two things to be straight about

**The agent is told the shortfall.** It calls `unbalanced_accounts` and learns
which account is short and by how much. That is the observable a controller
actually has — you know you are short, you do not know why. What it is never
told is the *mechanism*, which is the part it has to find.

**`Analyst` is a deterministic policy over the tools, not a language model.** It
perceives, decides, acts and observes in a real loop with a real transcript, and
that loop is what the 87/87 measures. The model drivers sit in the same seat with
the same tools and the same adjudicator; they are covered by tests against
stubbed clients and need an API key to run for real, so **no score is claimed for
any model here.** The architecture is the point: the generator is swappable, the
checker is exact.

## Deterministic simulation testing

Faults are injected on a seeded schedule and every invariant is checked after
every delivery: `duplicate_batch`, `crash_and_replay`, `reorder`, `split`,
`delay`, `replay_old`, `truncate`.

```bash
uv run residual dst --seeds 3000
uv run residual rediscover --all-weeks
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

## Real Razorpay data

Orders created through the live test-mode API, paid through real Checkout, then
pulled back from `GET /v1/payments` and folded into books:

```
Live payments  5 payment(s), 1 refund(s) from the Razorpay API

  4 captured, 1 failed, 1 refund(s)
  6 ledger events, every entry sums to zero

  fee_expense                      INR 195.68
  gateway_receivable             INR 7,053.10
  gst_input_credit                  INR 35.22
  refunds                        INR 2,500.00
  revenue                       -INR 9,784.00
```

Four captures across four banks, one genuine failure, one partial refund, all
reconciled. The failed payment moves no money and posts nothing, which is
checked rather than assumed.

### What the real response caught

Razorpay reports `fee` **inclusive** of `tax`. A ₹7,500 netbanking payment comes
back as `fee: 17700, tax: 2700`. This ledger keeps the two apart, because GST on
a fee is reclaimable input credit and the fee itself is not. The first mapping
passed both through untouched and so charged the GST twice — ₹204 of cost
against a merchant who was billed ₹177.

The arithmetic settles it, not the documentation:

| payment | gross | billed | fee − tax | rate | GST |
|---|---|---|---|---|---|
| BARB_R | ₹7,500.00 | ₹177.00 | ₹150.00 | 2.0000% | 18.00% |
| PUNB_R | ₹485.00 | ₹11.44 | ₹9.70 | 2.0000% | 17.94% |
| DLXB | ₹1,299.00 | ₹30.66 | ₹25.98 | 2.0000% | 18.01% |
| UCBA | ₹500.00 | ₹11.80 | ₹10.00 | 2.0000% | 18.00% |

Exactly 2.0000% four times, from four different banks. Under the other reading
none of them lands on a rate that exists. The GST column drifts by a paise
either way because it is rounded per payment, which is the reason money is
integer paise here and not a float.

`test_every_real_capture_lands_on_the_published_rate` pins both ratios against
the saved response, and a payment whose tax exceeds the fee containing it is
refused rather than posted.

Worth noting *how* this was caught. The books balanced perfectly under the wrong
mapping — a mis-split is not an imbalance, so the zero-residual check was blind
to it. What exposed it was the effective rate: 2.72% is not a netbanking rate
that exists anywhere.

### The refund it caught next

A payment carries `amount_refunded`, and reading a refund out of that field is
the obvious shortcut. It is also wrong in three ways at once: the refund gets an
invented id, it gets dated at the **capture** rather than at the refund, and two
partial refunds on one payment collapse into a single event. Refunds usually
land days after the capture, so dating them at the capture files them in the
wrong weekly close — in a bi-temporal ledger that is not a rounding problem, it
is the wrong answer.

So refunds are read from `/v1/refunds` and keep their own id, their own date and
their own speed. A payment that reports `amount_refunded` with no matching
refund record is **refused**, rather than quietly synthesised:

```
payment(s) ['pay_...'] report a refund but no refund record was supplied;
synthesising one would invent its id and date it at the capture, which files it
in the wrong window
```

Customer email, phone, VPA and notes are dropped at the ingest boundary and never
reach the ledger. `test_customer_details_never_reach_the_ledger` serialises every
event and asserts none of it survives; the committed fixture is redacted.

```bash
uv run python scripts/probe_live.py            # read-only
uv run python scripts/probe_live.py --write    # create a test order
uv run python scripts/probe_live.py --checkout # build the pay page
uv run residual live-payments
```

Settlements are not reachable from a fresh test account — they need an approved
KYC — so the settlement side is still exercised only against the simulator and
the saved recon fixtures.

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
₹28.77 Cr across 53 closes, ~50 ms per close, every residual zero.

## Using it from Python

Everything the CLI does is reachable as a library. `import residual` is lazy —
it costs about 5 ms and pulls in DuckDB only when you touch something that
needs it.

```python
from datetime import date

import residual

events = residual.EventLog.read_jsonl("data/events.jsonl").events()
books = residual.Warehouse.build(events)
rates = {"card": "2.00", "upi": "2.36", "netbanking": "2.00", "wallet": "2.00"}

close = residual.run_close(events, date(2026, 3, 2), date(2026, 3, 8), rates, books)

for finding in close.findings:
    print(f"{finding.cause:28} {finding.amount}")
print("residual", close.residual, close.closes)
```

Reading your own files instead of the generated log:

```python
statement = residual.read_statement("march.pdf")
recon = residual.read_recon(rows, on=date(2026, 3, 31))
credit = residual.read_gstr2b("gstr2b_march.json")
```

The rest of the surface: `Money` and `allocate` for money that never touches a
float, `fold` and `position_at` for balances, `forecast` and `backtest` for the
cash floor, `ask` for the question catalogue, `build_pack` and `verify_pack` for
a sealed close, `restate` for a close replayed as of a cutoff, `assess_tax` for
input-credit risk. `residual.__all__` lists all 47 names.

## Commands

```bash
uv run pytest                          # 558 tests
uv run residual demo                   # guided walkthrough
uv run residual serve                  # dashboard
uv run residual close --week 8 --show-sql
uv run residual evaluate
uv run residual ablate
uv run residual dst --seeds 3000
uv run residual calibration
uv run residual certify-forecast
uv run residual outlook --history 400
uv run residual position
uv run residual backtest-forecast --horizon 14
uv run residual memo --week 8
uv run residual restate-close --week 2
uv run residual pack --week 8
uv run residual ask "which settlements never arrived?"
uv run residual live-payments
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

Three layers, enforced by tests rather than convention:

- **core** — `domain`, `ledger`, `recon`, `position`, `explain`, `ingest`. The
  product. Never imports anything below.
- **harness** — `simulate`, `dst`, `eval`. The evidence that the core works.
  Not needed to use it.
- **surface** — `cli`, `web`. Presentation only, and the only layer allowed to
  reach for the sample world.

`test_core_never_imports_the_harness_or_a_surface` and
`test_the_package_graph_is_acyclic` hold that shape in place.

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
- Payments are read from the live test-mode API and reconciled. Settlements are
  not: they need an approved KYC, so that half is exercised against the
  simulator and saved recon fixtures only.
- Scanned statements need OCR, which isn't attempted.
- Single currency, single merchant. No subscriptions, payment links or virtual
  accounts.
- Form 26AS figures for the TDS check are supplied by hand; there's no parser.
- The dashboard is read-only and unauthenticated. It binds to localhost.
- The question layer answers from a fixed catalogue of queries unless a model is
  configured. Outside that catalogue it says so rather than guessing.
- Rediscovery runs on the deterministic searcher and needs no model. The
  optional `--model` arm has never been run against a live model: the loop and
  its adjudication are tested against a stubbed client, and the recorded 6/9 is
  one model's one-shot attempt, not a benchmark of models.
- The searcher's hypothesis space is account slices plus contract-derived fee
  candidates. A cause expressible in neither would not be found, and the honest
  signal for that is that it returns no proposal rather than a wrong one.

## Licence

MIT. Dependencies are MIT, BSD, Apache-2.0 or 0BSD; Hypothesis is MPL-2.0 and
reportlab is BSD, both test-only.

No Razorpay API secret is committed or required — the simulator generates the
world. A test-mode adapter for `GET /v1/settlements/recon/combined` is optional
and reads its key from a gitignored `.env`.
