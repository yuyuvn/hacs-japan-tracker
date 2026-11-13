"""Sagawa Express (佐川急便) tracker.

Sagawa's tracking site (e-customer.sagawa-exp.co.jp/k2k) is a Next.js app. The
lookup is a server action named ``searchPackages``, called with a multipart
form body. The action ID changes when Sagawa redeploys, so it is discovered
from the page's JS chunks and cached, with a known ID as a fallback.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp

from .base import (
    STATUS_NOT_FOUND,
    STATUS_UNKNOWN,
    BatchResult,
    CarrierError,
    TrackingResult,
    all_failed,
    classify,
    clean,
    fetch_single,
    fill_missing,
    status_from_events,
)

_LOGGER = logging.getLogger(__name__)

BASE = "https://e-customer.sagawa-exp.co.jp"
PAGE_URL = BASE + "/k2k"
PUBLIC_URL = PAGE_URL  # the new site has no per-number GET URL
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-JapanTracker"}
TIMEOUT = aiohttp.ClientTimeout(total=30)

FALLBACK_ACTION_ID = "60e3b76ba7f1d28cf3c3fa01680217528f16cd1984"
_CHUNK_RE = re.compile(r'/k2k/_next/static/chunks/[^"\']+\.js')
_ACTION_RE = re.compile(r'createServerReference\)\("([0-9a-f]{32,})"[^)]*?"searchPackages"')
_NOT_FOUND_WORDS = ("該当", "見つかりません", "登録されていません", "未登録")

_action_id: str | None = None


async def _discover_action_id(session: aiohttp.ClientSession) -> str:
    """Find the current searchPackages action ID in the page's JS chunks."""
    async with session.get(PAGE_URL, headers=HEADERS, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()
    for path in dict.fromkeys(_CHUNK_RE.findall(html)):
        async with session.get(BASE + path, headers=HEADERS, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                continue
            match = _ACTION_RE.search(await resp.text())
            if match:
                return match.group(1)
    raise CarrierError("Could not find Sagawa searchPackages action ID")


async def _call_action(session: aiohttp.ClientSession, action_id: str, numbers: list[str]) -> str:
    # Same body the site's form sends: one `_1_tracking_number` field per
    # number (max 10), then the action arguments referencing that form data.
    fields = [("_1_tracking_number", n) for n in numbers] + [("0", '[null,"$K1"]')]
    with aiohttp.MultipartWriter("form-data") as mp:
        for name, value in fields:
            part = mp.append(value, {"Content-Type": "text/plain; charset=utf-8"})
            part.set_content_disposition("form-data", name=name)
            # Browsers don't send a Content-Type per text field; keep it minimal.
            part.headers.pop("Content-Type", None)
    async with session.post(
        PAGE_URL,
        data=mp,
        headers={
            **HEADERS,
            "Accept": "text/x-component",
            "Next-Action": action_id,
            "Origin": BASE,
        },
        timeout=TIMEOUT,
    ) as resp:
        if resp.status in (400, 404):  # Next.js answers an unknown action ID with 400
            raise _StaleAction
        resp.raise_for_status()
        return await resp.text()


class _StaleAction(Exception):
    """The cached action ID is no longer valid."""


def _payload(body: str) -> dict[str, Any]:
    """Extract the action result object from a React Server Components stream."""
    for line in body.splitlines():
        _, sep, rest = line.partition(":")
        if not sep or not rest.startswith("{"):
            continue
        try:
            obj = json.loads(rest)
        except ValueError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            return obj
    if "Server action not found" in body or "Failed to find Server Action" in body:
        raise _StaleAction
    raise CarrierError("Unexpected Sagawa response format")


async def fetch_many(
    session: aiohttp.ClientSession, numbers: list[str], previous: dict[str, TrackingResult] | None = None
) -> BatchResult:
    """Look up to BATCH_SIZE numbers in one request."""
    global _action_id  # noqa: PLW0603 - module-level cache shared by all parcels
    try:
        for attempt in range(2):
            action_id = _action_id or FALLBACK_ACTION_ID
            try:
                obj = _payload(await _call_action(session, action_id, numbers))
                break
            except _StaleAction:
                if attempt:
                    raise CarrierError("Sagawa action ID is stale even after rediscovery") from None
                _LOGGER.debug("Sagawa action ID %s stale, rediscovering", action_id)
                _action_id = await _discover_action_id(session)
        if not obj.get("ok"):
            raise CarrierError(obj.get("message") or "Sagawa returned an error")
    except (aiohttp.ClientError, TimeoutError) as err:
        return all_failed(numbers, CarrierError(f"Sagawa request failed: {err}"))
    except CarrierError as err:
        return all_failed(numbers, err)

    out: BatchResult = {}
    for res in obj.get("results") or []:
        number = str(res.get("trackingNumber") or "").replace("-", "")
        if number in numbers:
            out[number] = parse_result(res)
    return fill_missing(numbers, out, "Sagawa")


async def fetch(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    return await fetch_single(fetch_many, session, number)


def _strip_mark(text: str) -> str:
    """'↓集荷' / '⇒輸送中' -> '集荷' / '輸送中'."""
    return clean(re.sub(r"^[^\w぀-ヿ一-鿿]+", "", text or ""))


def parse_result(res: dict[str, Any]) -> TrackingResult:
    """Parse one entry of the action's `results` list."""
    top = clean(res.get("status"))
    messages = " ".join(clean(m) for m in res.get("messageLines") or [])
    events = [
        {
            "description": _strip_mark(h.get("status", "")),
            "time": clean(h.get("date")),
            "location": clean(h.get("office")),
        }
        for h in res.get("history") or []
    ]

    if not events and any(w in f"{top} {messages}" for w in _NOT_FOUND_WORDS):
        return TrackingResult(status=STATUS_NOT_FOUND, status_text=top or messages)

    status = classify(top)
    if status == STATUS_UNKNOWN:
        status = status_from_events(events)

    delivery = res.get("deliveryOffice") or {}
    pickup = res.get("pickupOffice") or {}
    return TrackingResult(
        status=status,
        status_text=" – ".join(x for x in (top, messages) if x),
        events=events,
        extra={
            "shipped_date": res.get("shippedDate", ""),
            "package_count": res.get("packageCount", ""),
            "pickup_office": pickup.get("name", ""),
            "delivery_office": delivery.get("name", ""),
            "delivery_office_tel": delivery.get("tel", ""),
            "delivery_dates": [clean(d) for d in res.get("dateLines") or []],
        },
    )
