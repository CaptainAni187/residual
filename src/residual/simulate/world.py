
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from residual.domain.calendar import add_bank_days, is_bank_day, next_bank_day
from residual.domain.causes import Cause
from residual.ledger import events as ev
from residual.ledger.money import Money
from residual.ledger.store import EventLog
from residual.simulate.narration import make_narration
from residual.simulate.truth import Attribution, GroundTruth, TimingFact

GST_ON_FEE = "18.00"


class ScenarioDidNotFire(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FeeHike:

    day: int
    method: ev.Method
    new_rate: str


@dataclass(frozen=True, slots=True)
class RiskHold:
    day: int
    amount: str
    release_after: int
    reason: ev.HoldReason = ev.HoldReason.RISK_REVIEW


@dataclass(frozen=True, slots=True)
class LostPayout:

    day: int


@dataclass(frozen=True, slots=True)
class InstantPull:

    day: int
    amount: str


@dataclass(frozen=True, slots=True)
class PartialSettlement:

    day: int
    share: float = 0.6


@dataclass(frozen=True, slots=True)
class DisputeSpike:
    day: int
    count: int


@dataclass(frozen=True, slots=True)
class MerchantConfig:
    seed: int = 20260822
    start: date = date(2026, 1, 5)
    days: int = 90

    base_daily_orders: int = 45
    weekday_multiplier: tuple[float, ...] = (1.0, 1.05, 1.05, 1.1, 1.25, 0.85, 0.6)

    annual_growth: float = 0.32
    ticket_min: str = "199"
    ticket_max: str = "8999"

    method_mix: tuple[tuple[ev.Method, float], ...] = (
        (ev.Method.UPI, 0.72),
        (ev.Method.CARD, 0.14),
        (ev.Method.NETBANKING, 0.07),
        (ev.Method.WALLET, 0.05),
        (ev.Method.EMI, 0.02),
    )

    base_rates: tuple[tuple[ev.Method, str], ...] = (
        (ev.Method.UPI, "2.00"),
        (ev.Method.CARD, "2.00"),
        (ev.Method.NETBANKING, "2.00"),
        (ev.Method.WALLET, "2.00"),
        (ev.Method.EMI, "2.00"),
    )

    failure_rate: float = 0.09

    refund_rate: float = 0.06

    dispute_rate: float = 0.006
    dispute_win_rate: float = 0.45
    route_split_rate: float = 0.02
    route_share: str = "12.00"
    tds_rate: str = "0.10"
    tds_share: float = 0.15
    instant_settlement_rate: float = 0.03

    instant_fee_rate: str = "0.20"

    settlement_lag_days: int = 2
    credit_same_day_prob: float = 0.85

    narration_mess: float = 0.55

    bank_charge_day: int = 28
    bank_charge_amount: str = "826"

    scenarios: tuple[
        FeeHike | RiskHold | LostPayout | DisputeSpike | InstantPull | PartialSettlement,
        ...,
    ] = ()


@dataclass(slots=True)
class SimResult:
    log: EventLog
    truth: GroundTruth
    config: MerchantConfig
    fired: frozenset[object] = frozenset()

    @property
    def unfired(self) -> tuple[object, ...]:
        return tuple(s for s in self.config.scenarios if s not in self.fired)

    def require_all_scenarios_fired(self) -> None:
        if self.unfired:
            raise ScenarioDidNotFire(
                "configured but never occurred: "
                + "; ".join(repr(s) for s in self.unfired)
            )

    @property
    def start(self) -> date:
        return self.config.start

    @property
    def end(self) -> date:
        return self.config.start + timedelta(days=self.config.days - 1)


@dataclass(slots=True)
class _Pending:
    payment_id: str
    net: Money
    captured_on: date
    eligible_on: date
    naive_on: date


def simulate(config: MerchantConfig | None = None) -> SimResult:
    return _World(config or MerchantConfig()).run()


class _World:
    def __init__(self, config: MerchantConfig) -> None:
        self.cfg = config
        self.rng = random.Random(config.seed)
        self.log = EventLog()
        self.truth = GroundTruth()
        self.n = 0

        self.rates: dict[ev.Method, str] = dict(config.base_rates)
        self.base_rates: dict[ev.Method, str] = dict(config.base_rates)

        self.unsettled: list[_Pending] = []
        self.deductions: list[tuple[Money, str]] = []
        self.captured: list[tuple[str, Money, date, ev.Method]] = []
        self.refunded: set[str] = set()
        self.disputed: set[str] = set()
        self.scheduled_disputes: dict[str, tuple[date, date]] = {}
        self.holds: list[tuple[str, Money, date]] = []

        self.hikes = {s.day: s for s in config.scenarios if isinstance(s, FeeHike)}
        self.risk_holds = {s.day: s for s in config.scenarios if isinstance(s, RiskHold)}
        self.lost_payouts = {s.day for s in config.scenarios if isinstance(s, LostPayout)}
        self.spikes = {s.day: s for s in config.scenarios if isinstance(s, DisputeSpike)}
        self.pulls = {s.day: s for s in config.scenarios if isinstance(s, InstantPull)}
        self.partials = {
            s.day: s for s in config.scenarios if isinstance(s, PartialSettlement)
        }
        self.fired: set[object] = set()


    def _id(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}_{self.n:06d}"

    def _emit(self, event: ev.EventBase) -> None:
        self.log.append(event)

    def _pick_method(self) -> ev.Method:
        r, acc = self.rng.random(), 0.0
        for method, weight in self.cfg.method_mix:
            acc += weight
            if r <= acc:
                return method
        return self.cfg.method_mix[-1][0]

    def _ticket(self) -> Money:
        lo = Money.parse(self.cfg.ticket_min).paise
        hi = Money.parse(self.cfg.ticket_max).paise
        u = self.rng.random() ** 2.2
        return Money(int(lo + u * (hi - lo)) // 100 * 100)


    def run(self) -> SimResult:
        for offset in range(self.cfg.days):
            day = self.cfg.start + timedelta(days=offset)
            self._apply_scenarios(offset, day)
            self._orders(day)
            self._refunds(day)
            self._disputes(day, offset)
            self._settle(day)
            self._bank_charges(day)
        return SimResult(self.log, self.truth, self.cfg, frozenset(self.fired))

    def _apply_scenarios(self, offset: int, day: date) -> None:
        if hike := self.hikes.get(offset):
            self.fired.add(hike)
            old = self.rates[hike.method]
            self.rates[hike.method] = hike.new_rate
            self._emit(
                ev.FeeScheduleChanged(
                    event_id=self._id("fsc"), occurred_at=day, recorded_at=day,
                    method=hike.method, old_rate=old, new_rate=hike.new_rate,
                    effective_from=day,
                )
            )
        if hold := self.risk_holds.get(offset):
            self.fired.add(hold)
            amount = Money.parse(hold.amount)
            hid = self._id("hold")
            self._emit(
                ev.RiskHoldApplied(
                    event_id=self._id("rha"), occurred_at=day, recorded_at=day,
                    hold_id=hid, amount=amount, reason=hold.reason,
                )
            )
            self.truth.add(
                Attribution(
                    event_id=hid, occurred_at=day, cause=Cause.RISK_HOLD,
                    amount=amount, entity_id=hid, note=str(hold.reason),
                )
            )
            self.deductions.append((amount, hid))
            if hold.release_after >= 0:
                self.holds.append((hid, amount, day + timedelta(days=hold.release_after)))

    def _orders(self, day: date) -> None:
        mult = self.cfg.weekday_multiplier[day.weekday()]
        elapsed = (day - self.cfg.start).days / 365
        growth = (1 + self.cfg.annual_growth) ** elapsed
        count = max(0, int(self.rng.gauss(self.cfg.base_daily_orders * mult * growth, 6)))
        for _ in range(count):
            gross = self._ticket()
            method = self._pick_method()
            oid = self._id("ord")

            if self.rng.random() < self.cfg.failure_rate:
                self._emit(
                    ev.PaymentFailed(
                        event_id=self._id("pf"), occurred_at=day, recorded_at=day,
                        payment_id=self._id("pay"), order_id=oid, gross=gross,
                        method=method, error_code=self.rng.choice(
                            ["BAD_REQUEST_ERROR", "GATEWAY_ERROR", "INSUFFICIENT_FUNDS"]
                        ),
                    )
                )
                continue

            pid = self._id("pay")
            rate = self.rates[method]
            fee = gross.apply_rate(rate)
            base_fee = gross.apply_rate(self.base_rates[method])
            tax = fee.apply_rate(GST_ON_FEE)
            tds = (
                gross.apply_rate(self.cfg.tds_rate)
                if self.rng.random() < self.cfg.tds_share
                else Money(0)
            )
            self._emit(
                ev.PaymentCaptured(
                    event_id=self._id("cap"), occurred_at=day, recorded_at=day,
                    payment_id=pid, order_id=oid, gross=gross, method=method,
                    fee=fee, tax=tax, tds=tds,
                    card_network=self.rng.choice(["Visa", "MasterCard", "RuPay"])
                    if method is ev.Method.CARD
                    else None,
                )
            )
            self.captured.append((pid, gross, day, method))

            if method in (ev.Method.CARD, ev.Method.EMI) and (
                self.rng.random() < self.cfg.dispute_rate
            ):
                raised = day + timedelta(days=self.rng.randint(2, 6))
                self.scheduled_disputes[pid] = (
                    raised, raised + timedelta(days=self.rng.randint(7, 21))
                )

            self.truth.add(Attribution(pid, day, Cause.NORMAL_FEE, base_fee, pid, str(method)))
            if fee.paise != base_fee.paise:
                self.truth.add(
                    Attribution(
                        pid, day, Cause.FEE_RATE_INCREASE, fee - base_fee, pid,
                        f"{method}: {self.base_rates[method]}% -> {rate}%",
                    )
                )
            self.truth.add(Attribution(pid, day, Cause.GST_ON_FEE, tax, pid))
            self.truth.add(Attribution(pid, day, Cause.TDS_194O, tds, pid))

            net = gross - fee - tax - tds

            if self.rng.random() < self.cfg.route_split_rate:
                share = gross.apply_rate(self.cfg.route_share)
                if share.paise and share.paise < net.paise:
                    tid = self._id("trf")
                    self._emit(
                        ev.RouteTransfer(
                            event_id=self._id("rt"), occurred_at=day, recorded_at=day,
                            transfer_id=tid, payment_id=pid, amount=share,
                            to_account="acc_linked_partner",
                        )
                    )
                    self.truth.add(Attribution(tid, day, Cause.ROUTE_SPLIT, share, pid))
                    net = net - share

            naive = day + timedelta(days=self.cfg.settlement_lag_days)
            actual = add_bank_days(day, self.cfg.settlement_lag_days)
            self.unsettled.append(_Pending(pid, net, day, actual, naive))
            self.truth.add_timing(TimingFact(pid, day, net, naive, actual))

    def _refunds(self, day: date) -> None:
        eligible = [c for c in self.captured if 0 < (day - c[2]).days <= 21]
        for pid, gross, _, _ in eligible:
            if pid in self.refunded or self.rng.random() >= self.cfg.refund_rate / 21:
                continue
            portion = self.rng.choice([Decimal(100), Decimal(50), Decimal(30)])
            amount = gross.apply_rate(portion)
            if not amount.paise:
                continue
            rid = self._id("rfnd")
            self._emit(
                ev.RefundIssued(
                    event_id=self._id("ri"), occurred_at=day, recorded_at=day,
                    refund_id=rid, payment_id=pid, amount=amount,
                )
            )
            self.refunded.add(pid)
            self.truth.add(Attribution(rid, day, Cause.REFUNDS_ISSUED, amount, pid))
            self.deductions.append((amount, rid))

    def _disputes(self, day: date, offset: int) -> None:
        due = [pid for pid, (_, notified) in self.scheduled_disputes.items() if notified == day]

        if spike := self.spikes.get(offset):
            pool = [
                c for c in self.captured
                if 5 <= (day - c[2]).days <= 40
                and c[3] in (ev.Method.CARD, ev.Method.EMI)
                and c[0] not in self.disputed
                and c[0] not in self.refunded
                and c[0] not in due
            ]
            self.rng.shuffle(pool)
            for pid, _, cap_day, _ in pool[: spike.count]:
                self.scheduled_disputes.setdefault(
                    pid, (cap_day + timedelta(days=self.rng.randint(2, 6)), day)
                )
                due.append(pid)
            if pool:
                self.fired.add(spike)

        for pid in due:
            if pid in self.disputed or pid in self.refunded:
                continue
            gross = next((g for p, g, _, _ in self.captured if p == pid), None)
            if gross is None:
                continue
            self.disputed.add(pid)
            did = self._id("disp")
            raised, _ = self.scheduled_disputes[pid]
            self._emit(
                ev.DisputeOpened(
                    event_id=self._id("do"), occurred_at=raised, recorded_at=day,
                    dispute_id=did, payment_id=pid, amount=gross,
                    reason_code=self.rng.choice(["4853", "4855", "10.4", "13.1"]),
                )
            )
            self.truth.add(
                Attribution(did, raised, Cause.DISPUTE_RESERVE_HELD, gross, pid,
                            f"reserve held, notified {day}")
            )
            self.deductions.append((gross, did))

            if self.rng.random() < 0.6:
                resolved = day + timedelta(days=self.rng.randint(20, 40))
                won = self.rng.random() < self.cfg.dispute_win_rate
                if resolved <= self.cfg.start + timedelta(days=self.cfg.days - 1):
                    self._emit(
                        ev.DisputeResolved(
                            event_id=self._id("dr"), occurred_at=resolved,
                            recorded_at=resolved, dispute_id=did, payment_id=pid,
                            amount=gross, won=won,
                        )
                    )
                    self.truth.add(
                        Attribution(did, resolved, Cause.DISPUTE_RESERVE_HELD, -gross,
                                    pid, "reserve released on resolution")
                    )
                    if not won:
                        self.truth.add(
                            Attribution(did, resolved, Cause.CHARGEBACK_LOST, gross, pid)
                        )

    def _settle(self, day: date) -> None:
        for hid, amount, release_day in list(self.holds):
            if release_day == day:
                self._emit(
                    ev.RiskHoldReleased(
                        event_id=self._id("rhr"), occurred_at=day, recorded_at=day,
                        hold_id=hid, amount=amount,
                    )
                )
                self.truth.add(
                    Attribution(hid, day, Cause.RISK_HOLD, -amount, hid, "hold released")
                )
                self.holds.remove((hid, amount, release_day))

        if not is_bank_day(day):
            return

        self._instant_pull(day)

        due = [p for p in self.unsettled if p.eligible_on <= day]
        if not due:
            return

        if partial := self.partials.get((day - self.cfg.start).days):
            keep = max(1, int(len(due) * partial.share))
            due, held_back = due[:keep], due[keep:]
            if held_back:
                self.fired.add(partial)

        gross_net = Money(sum(p.net.paise for p in due))
        deducted = Money(sum(a.paise for a, _ in self.deductions))
        payable = gross_net - deducted
        if payable.paise <= 0:
            return
        settled = {p.payment_id for p in due}
        self.unsettled = [
            p for p in self.unsettled
            if p.eligible_on > day or p.payment_id not in settled
        ]
        self.deductions.clear()

        instant = self.rng.random() < self.cfg.instant_settlement_rate
        ifee = payable.apply_rate(self.cfg.instant_fee_rate) if instant else Money(0)
        igst = ifee.apply_rate(GST_ON_FEE)
        net = payable - ifee - igst
        sid, utr = self._id("setl"), f"{day:%Y%m%d}{self.rng.randrange(10**6):06d}"

        self._emit(
            ev.SettlementExecuted(
                event_id=self._id("se"), occurred_at=day, recorded_at=day,
                settlement_id=sid, utr=utr, net=net,
                covers=tuple(p.payment_id for p in due),
                instant=instant, instant_fee=ifee + igst,
            )
        )
        if ifee.paise:
            self.truth.add(
                Attribution(sid, day, Cause.INSTANT_SETTLEMENT_FEE, ifee + igst, utr)
            )

        offset = (day - self.cfg.start).days
        if offset in self.lost_payouts:
            self.fired.update(
                sc for sc in self.cfg.scenarios
                if isinstance(sc, LostPayout) and sc.day == offset
            )
            self.truth.add(
                Attribution(sid, day, Cause.SETTLEMENT_NEVER_ARRIVED, net, utr,
                            f"settlement {sid} executed, no bank credit")
            )
            return

        credit_day = day if self.rng.random() < self.cfg.credit_same_day_prob else (
            next_bank_day(day + timedelta(days=1))
        )
        btx = self._id("btx")
        narration = make_narration(self.rng, utr, mess=self.cfg.narration_mess)
        self._emit(
            ev.BankCreditReceived(
                event_id=self._id("bc"), occurred_at=credit_day, recorded_at=credit_day,
                bank_txn_id=btx, amount=net,
                narration=narration.text, value_date=credit_day,
            )
        )
        self.truth.links[btx] = sid
        self.truth.styles[btx] = str(narration.style)

    def _instant_pull(self, day: date) -> None:
        pull = self.pulls.get((day - self.cfg.start).days)
        if not pull:
            return
        amount = Money.parse(pull.amount)
        available = Money(sum(p.net.paise for p in self.unsettled))
        if available.paise < amount.paise:
            return
        self.fired.add(pull)

        self.unsettled.sort(key=lambda p: p.eligible_on)
        taken, drawn = Money.zero(), []
        while self.unsettled and taken.paise < amount.paise:
            p = self.unsettled.pop(0)
            taken = taken + p.net
            drawn.append(p)
        if taken.paise > amount.paise:
            last = drawn[-1]
            self.unsettled.insert(
                0,
                _Pending(last.payment_id, Money(taken.paise - amount.paise),
                         last.captured_on, last.eligible_on, last.naive_on),
            )

        ifee = amount.apply_rate(self.cfg.instant_fee_rate)
        igst = ifee.apply_rate(GST_ON_FEE)
        net = amount - ifee - igst
        sid = self._id("setl")
        utr = f"{day:%Y%m%d}{self.rng.randrange(10**6):06d}"
        self._emit(
            ev.SettlementExecuted(
                event_id=self._id("se"), occurred_at=day, recorded_at=day,
                settlement_id=sid, utr=utr, net=net,
                covers=tuple(p.payment_id for p in drawn),
                instant=True, instant_fee=ifee + igst,
            )
        )
        self.truth.add(
            Attribution(sid, day, Cause.INSTANT_SETTLEMENT_FEE, ifee + igst, utr)
        )

        btx = self._id("btx")
        narration = make_narration(self.rng, utr, terse=True)
        self._emit(
            ev.BankCreditReceived(
                event_id=self._id("bc"), occurred_at=day, recorded_at=day,
                bank_txn_id=btx, amount=net,
                narration=narration.text, value_date=day,
            )
        )
        self.truth.links[btx] = sid
        self.truth.styles[btx] = str(narration.style)

    def _bank_charges(self, day: date) -> None:
        if day.day != self.cfg.bank_charge_day:
            return
        amount = Money.parse(self.cfg.bank_charge_amount)
        btx = self._id("btx")
        self._emit(
            ev.BankChargeApplied(
                event_id=self._id("bca"), occurred_at=day, recorded_at=day,
                bank_txn_id=btx, amount=amount,
                narration="ACCT MAINT CHRG INCL GST",
            )
        )
        self.truth.add(Attribution(btx, day, Cause.BANK_CHARGES, amount, btx))
