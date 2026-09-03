from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

_EXPORTS: dict[str, tuple[str, str]] = {
    "Money": ("residual.ledger.money", "Money"),
    "CurrencyMismatch": ("residual.ledger.money", "CurrencyMismatch"),
    "allocate": ("residual.ledger.money", "allocate"),
    "EventLog": ("residual.ledger.store", "EventLog"),
    "Ingestion": ("residual.ledger.store", "Ingestion"),
    "ChainBroken": ("residual.ledger.store", "ChainBroken"),
    "content_digest": ("residual.ledger.store", "content_digest"),
    "Warehouse": ("residual.ledger.warehouse", "Warehouse"),
    "fold": ("residual.position.engine", "fold"),
    "InvariantViolation": ("residual.position.engine", "InvariantViolation"),
    "Position": ("residual.position.engine", "Position"),
    "position_at": ("residual.position.engine", "position_at"),
    "Variance": ("residual.position.engine", "Variance"),
    "decompose": ("residual.position.engine", "decompose"),
    "run_close": ("residual.explain.close", "run_close"),
    "Close": ("residual.explain.close", "Close"),
    "Finding": ("residual.explain.close", "Finding"),
    "Unresolved": ("residual.explain.close", "Unresolved"),
    "write_memo": ("residual.explain.agent", "write_memo"),
    "Memo": ("residual.explain.agent", "Memo"),
    "ask": ("residual.explain.qa", "ask"),
    "Answer": ("residual.explain.qa", "Answer"),
    "UnsafeQuestion": ("residual.explain.qa", "UnsafeQuestion"),
    "forecast": ("residual.position.forecast", "forecast"),
    "Outlook": ("residual.position.forecast", "Outlook"),
    "backtest": ("residual.position.forecast", "backtest"),
    "link_events": ("residual.recon.linkage", "link_events"),
    "Link": ("residual.recon.linkage", "Link"),
    "build_pack": ("residual.explain.pack", "build"),
    "verify_pack": ("residual.explain.pack", "verify"),
    "Pack": ("residual.explain.pack", "Pack"),
    "PackMismatch": ("residual.explain.pack", "PackMismatch"),
    "Proposal": ("residual.explain.propose", "Proposal"),
    "Verdict": ("residual.explain.propose", "Verdict"),
    "adjudicate": ("residual.explain.propose", "adjudicate"),
    "rediscover": ("residual.explain.propose", "rediscover"),
    "restate": ("residual.explain.restate", "restate"),
    "Restatement": ("residual.explain.restate", "Restatement"),
    "assess_tax": ("residual.explain.tax", "assess"),
    "Risk": ("residual.explain.tax", "Risk"),
    "read_statement": ("residual.ingest.bank", "load_any"),
    "UnreadableStatement": ("residual.ingest.bank", "UnreadableStatement"),
    "read_recon": ("residual.ingest.razorpay", "to_events"),
    "UnsupportedRow": ("residual.ingest.razorpay", "UnsupportedRow"),
    "read_gstr2b": ("residual.ingest.gst", "load"),
    "UnreadableReturn": ("residual.ingest.gst", "UnreadableReturn"),
}

__all__ = [
    "Answer",
    "ChainBroken",
    "Close",
    "CurrencyMismatch",
    "EventLog",
    "Finding",
    "Ingestion",
    "InvariantViolation",
    "Link",
    "Memo",
    "Money",
    "Outlook",
    "Pack",
    "PackMismatch",
    "Position",
    "Proposal",
    "Restatement",
    "Risk",
    "UnreadableReturn",
    "UnreadableStatement",
    "Unresolved",
    "UnsafeQuestion",
    "UnsupportedRow",
    "Variance",
    "Verdict",
    "Warehouse",
    "adjudicate",
    "allocate",
    "ask",
    "assess_tax",
    "backtest",
    "build_pack",
    "content_digest",
    "decompose",
    "fold",
    "forecast",
    "link_events",
    "position_at",
    "read_gstr2b",
    "read_recon",
    "read_statement",
    "rediscover",
    "restate",
    "run_close",
    "verify_pack",
    "write_memo",
]


def __getattr__(name: str) -> Any:
    try:
        module, attr = _EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module 'residual' has no attribute {name!r}") from None
    from importlib import import_module

    value = getattr(import_module(module), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return __all__
