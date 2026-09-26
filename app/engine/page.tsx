const LAYERS = [
  ["domain", "vocabulary everything shares — causes, the RBI banking calendar", "—"],
  ["ledger", "money as integer paise, accounts, events, postings, a hash-chained log, the DuckDB view", "domain"],
  ["recon", "matching bank credits to payouts, and abstaining when it cannot", "domain, ledger"],
  ["position", "cash position, forecasting, conformal intervals", "domain, ledger, recon"],
  ["explain", "seventeen verifiers, the close engine, the grounding gate", "domain, ledger, position, recon"],
  ["agents", "a toolbelt over the ledger and the loop that drives it", "core"],
  ["ingest", "Razorpay recon and payments, bank statements, GSTR-2B", "ledger"],
  ["simulate / dst / eval", "the generated merchant, fault injection, scoring — never imported by product code", "everything"],
];

const INVARIANTS = [
  ["Entries sum to zero at construction", "An entry that does not balance cannot be built, so the identity below is arithmetic rather than a check that might be skipped."],
  ["Every account's claims sum to its movement", "A zero residual could be two errors cancelling. This rules that out: each account is claimed exactly as much as it moved."],
  ["Money is integer paise, currency-tagged", "No code path builds money from a float, and a stray USD line cannot enter an INR position."],
  ["Two clocks on every event", "occurred_at and recorded_at, so a close can be replayed as it could actually have been run on the day it was signed."],
  ["Facts are deduplicated by content digest", "The same file delivered twice changes nothing, which is what makes replay after a crash safe."],
];

const NUMBERS = [
  ["573", "tests, at 92% coverage"],
  ["87 / 87", "held-out causes the agent recovered"],
  ["0.00", "rupee error across the benchmark quarter"],
  ["3,000", "fault-injection seeds swept, every invariant held"],
  ["505,159", "postings folded at ~50 ms per close"],
  ["8 MB", "runtime dependencies, after dropping the dataframe layer"],
];

export default function Engine() {
  return (
    <main className="pb-16">
      <section className="band py-6">
        <h2 className="sect">The identity everything rests on</h2>
        <pre className="sql mt-3">
{`gross captured  −  cash landed  =  Σ movement of every other account`}
        </pre>
        <p className="sans mt-3 max-w-[74ch] text-[13.5px]" style={{ color: "var(--ink-2)" }}>
          Because every entry is validated to sum to zero when it is constructed, the movement of all
          accounts over any window sums to zero too. Rearranged, that gives the line above. So the
          decomposition on the workspace is an accounting identity, not a heuristic — and a non-zero
          residual means an event moved money without a posting, which is a bug rather than a
          judgement call.
        </p>
      </section>

      <section className="band py-6">
        <h2 className="sect">What is enforced, and why it is worth enforcing</h2>
        <div className="mt-3">
          {INVARIANTS.map(([rule, why]) => (
            <div key={rule} className="py-3" style={{ borderBottom: "1px solid var(--rule)" }}>
              <div className="text-[12.5px]">{rule}</div>
              <p className="sans mt-1 max-w-[76ch] text-[12.5px]" style={{ color: "var(--ink-3)" }}>
                {why}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="band py-6">
        <h2 className="sect">Layers</h2>
        <div className="wide mt-3">
          <table className="ledger">
            <thead>
              <tr>
                <th>Package</th>
                <th>Holds</th>
                <th>May import</th>
              </tr>
            </thead>
            <tbody>
              {LAYERS.map(([name, holds, imports]) => (
                <tr key={name}>
                  <td style={{ whiteSpace: "nowrap" }}>{name}</td>
                  <td className="note" style={{ paddingLeft: 0 }}>{holds}</td>
                  <td className="note">{imports}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="sans mt-3 max-w-[74ch] text-[12.5px]" style={{ color: "var(--ink-3)" }}>
          The graph is asserted acyclic by a test, not by convention. It has already earned its keep:
          it caught an agents → explain → agents cycle the day the agent layer landed.
        </p>
      </section>

      <section className="py-6">
        <h2 className="sect">Measured, not asserted</h2>
        <div className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-2 md:grid-cols-3">
          {NUMBERS.map(([value, what]) => (
            <div key={what} style={{ borderTop: "1px solid var(--rule)" }} className="pt-2">
              <div className="n text-[18px] font-medium">{value}</div>
              <div className="sans text-[12px]" style={{ color: "var(--ink-3)" }}>{what}</div>
            </div>
          ))}
        </div>
        <p className="sans mt-5 max-w-[74ch] text-[12.5px]" style={{ color: "var(--ink-3)" }}>
          Every figure here is regenerated in CI, so none of it depends on the author's machine. The
          agent result is a deterministic policy over the toolbelt rather than a language model; the
          model drivers sit in the same seat behind the same adjudicator and are unmeasured, so no
          score is claimed for them.
        </p>
      </section>
    </main>
  );
}
