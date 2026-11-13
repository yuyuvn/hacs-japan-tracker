"""Carrier registry, carrier auto-detection and to-do item parsing."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..const import CARRIER_CAINIAO, CARRIER_JAPANPOST, CARRIER_KURONEKO, CARRIER_SAGAWA
from . import cainiao, japanpost, kuroneko, sagawa
from .base import BATCH_SIZE, BatchResult, CarrierError, TrackingResult

# Batch lookups: fetch_many(session, numbers[<=BATCH_SIZE], previous) -> BatchResult.
FETCHERS = {
    CARRIER_SAGAWA: sagawa.fetch_many,
    CARRIER_KURONEKO: kuroneko.fetch_many,
    CARRIER_JAPANPOST: japanpost.fetch_many,
    CARRIER_CAINIAO: cainiao.fetch_many,
}

PUBLIC_URLS = {
    CARRIER_SAGAWA: sagawa.PUBLIC_URL,
    CARRIER_KURONEKO: "https://member.kms.kuronekoyamato.co.jp/parcel/detail?pno={number}",
    CARRIER_JAPANPOST: japanpost.PUBLIC_URL,
    CARRIER_CAINIAO: cainiao.PUBLIC_URL,
}

# Optional "carrier:" prefix on a to-do item forces the carrier.
ALIASES = {
    "sagawa": CARRIER_SAGAWA, "佐川": CARRIER_SAGAWA, "佐川急便": CARRIER_SAGAWA,
    "yamato": CARRIER_KURONEKO, "kuroneko": CARRIER_KURONEKO, "ヤマト": CARRIER_KURONEKO,
    "クロネコ": CARRIER_KURONEKO, "ヤマト運輸": CARRIER_KURONEKO,
    "jp": CARRIER_JAPANPOST, "japanpost": CARRIER_JAPANPOST, "yubin": CARRIER_JAPANPOST,
    "郵便": CARRIER_JAPANPOST, "日本郵便": CARRIER_JAPANPOST, "ゆうパック": CARRIER_JAPANPOST,
    "cainiao": CARRIER_CAINIAO, "ali": CARRIER_CAINIAO, "aliexpress": CARRIER_CAINIAO,
}

_PREFIX_RE = re.compile(r"^\s*([^\s:]+)\s*:\s*")
_S10_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{2}\d{9}[A-Z]{2})(?![A-Z0-9])")
_DIGITS_RE = re.compile(r"(?<![A-Z0-9-])(\d[\d-]{8,15}\d)(?![A-Z0-9-])")
# Mixed letters+digits with at least 8 digits, so product names like
# "iPhone15Pro" are not mistaken for tracking numbers.
_ALNUM_RE = re.compile(r"(?<![A-Z0-9])(?=(?:[A-Z]*\d){8})(?=[A-Z0-9]*[A-Z])([A-Z0-9]{10,32})(?![A-Z0-9])")


def _plausible_digits(raw: str) -> bool:
    """10-12 digits; if hyphenated, only the 4-4-x grouping carriers print
    (so phone numbers like 03-1234-5678 are not picked up)."""
    if not 10 <= len(raw.replace("-", "")) <= 12:
        return False
    return "-" not in raw or re.fullmatch(r"\d{4}-\d{4}-\d{2,4}", raw) is not None


@dataclass(frozen=True)
class ParsedItem:
    number: str
    carrier: str | None  # forced by prefix, else None (auto-detect)
    name: str | None


def parse_item(summary: str) -> ParsedItem | None:
    """Find a tracking number in a to-do item's text.

    Accepts e.g. "111122223333", "Headphones 1111-2222-3333",
    "sagawa: 111122223333 Headphones", "EA123456789JP", full-width digits.
    """
    text = unicodedata.normalize("NFKC", summary or "")
    carrier = None
    if m := _PREFIX_RE.match(text):
        if (alias := ALIASES.get(m.group(1).lower())) is not None:
            carrier = alias
            text = text[m.end():]

    upper = text.upper()
    number = span = None
    if m := _S10_RE.search(upper):
        number, span = m.group(1), m.span(1)
    elif m := next((m for m in _DIGITS_RE.finditer(upper) if _plausible_digits(m.group(1))), None):
        number, span = m.group(1).replace("-", ""), m.span(1)
    elif m := _ALNUM_RE.search(upper):
        number, span = m.group(1), m.span(1)
    if number is None:
        return None

    name = " ".join((text[: span[0]] + " " + text[span[1]:]).split()) or None
    return ParsedItem(number=number, carrier=carrier, name=name)


def candidates(number: str) -> list[str]:
    """Carriers to try, most likely first. Only the first that knows the number is kept."""
    if re.fullmatch(r"[A-Z]{2}\d{9}JP", number):
        return [CARRIER_JAPANPOST, CARRIER_CAINIAO]
    if re.fullmatch(r"[A-Z]{2}\d{9}[A-Z]{2}", number):
        return [CARRIER_CAINIAO, CARRIER_JAPANPOST]
    if number.isdigit():
        # Yamato, Sagawa and Japan Post all use 10-12 digits with a mod-7
        # check digit, so they can't be told apart offline: probe in order.
        return [CARRIER_KURONEKO, CARRIER_SAGAWA, CARRIER_JAPANPOST]
    return [CARRIER_CAINIAO]


__all__ = [
    "BATCH_SIZE",
    "BatchResult",
    "FETCHERS",
    "PUBLIC_URLS",
    "CarrierError",
    "ParsedItem",
    "TrackingResult",
    "candidates",
    "parse_item",
]
