"""Cainiao (AliExpress) tracker, using the public global.cainiao.com JSON endpoint.

Status is derived from Cainiao's structured fields, not from English text:
the top-level ``status`` (e.g. ``DELIVERING``) spans the whole journey, so it
only tells us "delivered or not". Event ``actionCode``s and the progress
points ("Mainland China" -> "Japan" -> "Delivered") give the finer stage.
"""
from __future__ import annotations

from typing import Any

import aiohttp

from .base import (
    STATUS_DELIVERED,
    STATUS_FAILED_ATTEMPT,
    STATUS_IN_TRANSIT,
    STATUS_INFO_RECEIVED,
    STATUS_NOT_FOUND,
    STATUS_OUT_FOR_DELIVERY,
    STATUS_PENDING,
    BatchResult,
    CarrierError,
    TrackingResult,
    all_failed,
    clean,
    fetch_single,
)

URL = "https://global.cainiao.com/global/detail.json"
PUBLIC_URL = "https://global.cainiao.com/newDetail.htm?mailNoList={number}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-JapanTracker",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://global.cainiao.com/",
}

_DELIVERED_STATUSES = {"SIGN", "SIGNED", "DELIVERED"}
# Action codes seen before the parcel physically moves (order/label stage).
_INFO_ONLY_CODES = {"GWMS_ACCEPT", "PU_PICKUP_NOTIFY", "ORDER_CREATE", "CW_ACCEPT"}


async def fetch_many(
    session: aiohttp.ClientSession, numbers: list[str], previous: dict[str, TrackingResult] | None = None
) -> BatchResult:
    """`mailNos` takes a comma-separated list; one `module` entry comes back per number."""
    params = {"mailNos": ",".join(numbers), "lang": "en-US", "language": "en-US"}
    try:
        async with session.get(
            URL,
            params=params,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        return all_failed(numbers, CarrierError(f"Cainiao request failed: {err}"))

    modules = payload.get("module") or []
    if not payload.get("success", True) and not modules:
        return all_failed(numbers, CarrierError("Cainiao returned an unsuccessful response"))
    out: BatchResult = {}
    for mod in modules:
        number = str(mod.get("mailNo") or "").upper()
        if number in numbers:
            out[number] = parse_module(mod)
    # A number Cainiao doesn't list at all is simply unknown to it.
    for n in numbers:
        out.setdefault(n, TrackingResult(status=STATUS_NOT_FOUND, status_text="Not found"))
    return out


async def fetch(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    return await fetch_single(fetch_many, session, number)


def parse(payload: dict[str, Any]) -> TrackingResult:
    """Parse a single-number response (kept for offline tests)."""
    modules = payload.get("module") or []
    if not payload.get("success", True) and not modules:
        raise CarrierError("Cainiao returned an unsuccessful response")
    if not modules:
        return TrackingResult(status=STATUS_NOT_FOUND, status_text="Not found")
    return parse_module(modules[0])


def _event_status(code: str, text: str) -> str | None:
    """Status implied by a single event, or None if it doesn't decide anything."""
    code, low = code.upper(), text.lower()
    if "SIGN" in code or "delivered" in low or "signed for" in low:
        return STATUS_DELIVERED
    if "FAIL" in code or "RETURN" in code or "failed" in low or "unsuccessful" in low:
        return STATUS_FAILED_ATTEMPT
    if "out for delivery" in low or code.endswith("_DELIVERING"):
        return STATUS_OUT_FOR_DELIVERY
    return None


def parse_module(mod: dict[str, Any]) -> TrackingResult:
    """Parse one entry of the response's `module` list."""
    details = mod.get("detailList") or []  # newest first
    raw_status = str(mod.get("status") or "").upper()

    if not details and raw_status in ("", "NOT_LAZADA", "NOT_FOUND", "WAIT4SIGNIN_NONE"):
        return TrackingResult(status=STATUS_NOT_FOUND, status_text=mod.get("statusDesc") or "Not found")

    # `desc` is the specific text ("Last-mile delivery forecast"); `standerdDesc`
    # is sometimes generic ("Carrier update"), so it is only a fallback.
    events = [
        {
            "description": clean(d.get("desc") or d.get("standerdDesc")),
            "time": d.get("timeStr") or str(d.get("time", "")),
            "location": "",  # Cainiao gives no per-event place; group.nodeDesc is a stage label
            "code": d.get("actionCode") or "",
        }
        for d in reversed(details)  # oldest -> newest
    ]

    # Progress stage: last lit point, e.g. "Mainland China" / "Japan" / "Delivered".
    points = (mod.get("processInfo") or {}).get("progressPointList") or []
    lit = [p.get("pointName", "") for p in points if p.get("light")]
    stage = lit[-1] if lit else ""

    if raw_status in _DELIVERED_STATUSES or stage.lower() == "delivered":
        status = STATUS_DELIVERED
    elif not events:
        status = STATUS_PENDING
    else:
        latest = events[-1]
        status = _event_status(latest["code"], latest["description"]) or (
            STATUS_INFO_RECEIVED
            if all(e["code"] in _INFO_ONLY_CODES for e in events)
            else STATUS_IN_TRANSIT
        )

    last_mile = mod.get("destCpInfo") or {}
    rate = (mod.get("processInfo") or {}).get("progressRate")
    status_text = events[-1]["description"] if events else (mod.get("statusDesc") or "")
    if stage and status != STATUS_DELIVERED:
        status_text = f"{status_text} ({stage})"

    for e in events:
        e.pop("code", None)

    return TrackingResult(
        status=status,
        status_text=status_text,
        events=events,
        extra={
            "stage": stage,
            "progress": round(rate * 100) if isinstance(rate, (int, float)) else None,
            "origin_country": mod.get("originCountry", ""),
            "destination_country": mod.get("destCountry", ""),
            "last_mile_carrier": last_mile.get("cpName", ""),
            "last_mile_url": last_mile.get("url", ""),
            "last_mile_phone": clean(last_mile.get("phone")),
            "days_in_transit": clean(str(mod.get("daysNumber") or "")),
        },
    )
