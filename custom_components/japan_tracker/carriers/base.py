"""Shared models and helpers for carrier scrapers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUS_PENDING = "pending"
STATUS_INFO_RECEIVED = "info_received"
STATUS_IN_TRANSIT = "in_transit"
STATUS_OUT_FOR_DELIVERY = "out_for_delivery"
STATUS_FAILED_ATTEMPT = "failed_attempt"
STATUS_DELIVERED = "delivered"
STATUS_NOT_FOUND = "not_found"
STATUS_ERROR = "error"
STATUS_UNKNOWN = "unknown"

ALL_STATUSES = [
    STATUS_PENDING,
    STATUS_INFO_RECEIVED,
    STATUS_IN_TRANSIT,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_FAILED_ATTEMPT,
    STATUS_DELIVERED,
    STATUS_NOT_FOUND,
    STATUS_ERROR,
    STATUS_UNKNOWN,
]

# Order matters: first match wins.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # Avoid generic phrases like "received by" / "delivering": Cainiao uses them
    # for early stages ("information received by warehouse", status DELIVERING).
    (STATUS_DELIVERED, ("配達完了", "配達済", "お届け完了", "お届け済", "お渡し済", "delivered", "signed for")),
    (STATUS_FAILED_ATTEMPT, ("不在", "持戻", "持ち戻", "返送", "failed", "unsuccessful", "undeliverable")),
    (STATUS_OUT_FOR_DELIVERY, ("持出中", "持ち出し中", "配達中", "配達に出発", "out for delivery")),
    (STATUS_IN_TRANSIT, ("輸送中", "発送", "到着", "中継", "通過", "搬入", "搬出", "税関", "通関", "transit", "depart", "arriv", "customs", "export", "import", "sorting", "linehaul")),
    (STATUS_INFO_RECEIVED, ("荷物受付", "集荷", "受付", "引受", "received", "pick", "label created", "order")),
]


# Events that don't change where the parcel is (redelivery requests, held at
# office). They classify as unknown so the previous event's status is kept;
# e.g. Japan Post "配達希望受付" would otherwise match "受付" (info_received).
_NEUTRAL = ("配達希望", "再配達", "保管", "お届け予定")


def classify(text: str) -> str:
    """Map free text (Japanese or English) to a normalized status."""
    low = (text or "").lower()
    if any(w in low for w in _NEUTRAL):
        return STATUS_UNKNOWN
    for status, words in _KEYWORDS:
        if any(w in low for w in words):
            return status
    return STATUS_UNKNOWN


@dataclass
class TrackingResult:
    """Normalized tracking result. Events are ordered oldest -> newest."""

    status: str = STATUS_UNKNOWN
    status_text: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # carrier-specific details

    @property
    def last_event(self) -> dict[str, str] | None:
        return self.events[-1] if self.events else None


def status_from_events(events: list[dict[str, str]]) -> str:
    """Newest event that we can classify decides the status."""
    for ev in reversed(events):
        st = classify(ev.get("description", ""))
        if st != STATUS_UNKNOWN:
            return st
    return STATUS_UNKNOWN if events else STATUS_PENDING


class CarrierError(Exception):
    """Raised when a carrier site cannot be queried or parsed."""


# Every carrier's lookup form accepts up to 10 numbers per request.
BATCH_SIZE = 10

# fetch_many() result: one entry per requested number, either a result or the
# error for that number (a failed request gives every number the same error).
BatchResult = dict[str, "TrackingResult | CarrierError"]


def all_failed(numbers: list[str], err: CarrierError) -> BatchResult:
    return {n: err for n in numbers}


def fill_missing(numbers: list[str], results: BatchResult, carrier: str) -> BatchResult:
    """Numbers the carrier silently dropped from a batch response become errors."""
    for n in numbers:
        results.setdefault(n, CarrierError(f"{carrier} returned no result for {n}"))
    return results


async def fetch_single(fetch_many, session, number: str) -> TrackingResult:
    """Single-number convenience wrapper around a carrier's fetch_many()."""
    result = (await fetch_many(session, [number], {}))[number]
    if isinstance(result, CarrierError):
        raise result
    return result


def decode(raw: bytes) -> str:
    """Decode a response body that may be UTF-8 or Shift_JIS."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


def clean(text: Any) -> str:
    """Collapse whitespace; also flattens dicts/lists that APIs sometimes return
    where a plain string used to be (e.g. {"label": "...", "value": "..."})."""
    if isinstance(text, dict):
        text = " ".join(clean(v) for v in text.values())
    elif isinstance(text, (list, tuple)):
        text = " ".join(clean(v) for v in text)
    elif text is not None and not isinstance(text, str):
        text = str(text)
    return " ".join((text or "").split())
