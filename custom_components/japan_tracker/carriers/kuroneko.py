"""Kuroneko Yamato (ヤマト運輸) tracker.

The lookup form takes up to 10 numbers (number01..number10) and returns one
`.parts-tracking-invoice-block` per number, headed "N件目：4444-5555-6666".
"""
from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup, Tag

from .base import (
    STATUS_NOT_FOUND,
    BatchResult,
    CarrierError,
    TrackingResult,
    all_failed,
    clean,
    decode,
    fetch_single,
    fill_missing,
    status_from_events,
)

URL = "https://toi.kuronekoyamato.co.jp/cgi-bin/tneko"
PUBLIC_URL = URL  # POST only; the card links to the generic search page
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-JapanTracker"}
# Matched against each parcel's state title only, never the whole page: generic
# notices on every page contain phrases like "ご確認ください".
NOT_FOUND_MARKERS = ("伝票番号誤り", "伝票番号未登録", "見つかりません", "該当なし")


async def fetch_many(
    session: aiohttp.ClientSession, numbers: list[str], previous: dict[str, TrackingResult] | None = None
) -> BatchResult:
    data = {"number00": "1", **{f"number{i:02d}": n for i, n in enumerate(numbers, start=1)}}
    try:
        async with session.post(
            URL, data=data, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            html = decode(await resp.read())
    except (aiohttp.ClientError, TimeoutError) as err:
        return all_failed(numbers, CarrierError(f"Kuroneko request failed: {err}"))
    return parse_many(html, numbers)


async def fetch(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    return await fetch_single(fetch_many, session, number)


def parse_many(html: str, numbers: list[str]) -> BatchResult:
    soup = BeautifulSoup(html, "html.parser")
    blocks = soup.select(".parts-tracking-invoice-block")
    if not blocks:
        return all_failed(numbers, CarrierError("Kuroneko page layout not recognised"))

    out: BatchResult = {}
    for block in blocks:
        title = block.select_one(".tracking-invoice-block-title")
        # "1件目：4444-5555-6666" -> "444455556666"
        number = re.sub(r"\D", "", (title.get_text() if title else "").split("：")[-1])
        if number in numbers:
            try:
                out[number] = parse_block(block)
            except CarrierError as err:
                out[number] = err
    return fill_missing(numbers, out, "Kuroneko")


def _text(block: Tag, selector: str) -> str:
    el = block.select_one(selector)
    return clean(el.get_text(" ")) if el else ""


def parse_block(block: Tag) -> TrackingResult:
    events = []
    for li in block.select(".tracking-invoice-block-detail li"):
        if li.select_one(".item") is None:
            continue
        events.append(
            {
                "description": _text(li, ".item"),
                "time": _text(li, ".date"),
                "location": _text(li, ".name"),
            }
        )

    title = _text(block, ".tracking-invoice-block-state-title")
    summary = _text(block, ".tracking-invoice-block-state-summary")

    if not events:
        if any(m in title for m in NOT_FOUND_MARKERS):
            return TrackingResult(status=STATUS_NOT_FOUND, status_text=f"{title} – {summary}".strip(" –"))
        raise CarrierError("Kuroneko parcel block not recognised")

    # "商品名： 宅急便コンパクト お届け予定日時： 09/11"
    extra: dict[str, str] = {"message": summary}
    info = _text(block, ".tracking-invoice-block-summary")
    for key, label in (("product", "商品名"), ("scheduled_delivery", "お届け予定日時")):
        m = re.search(rf"{label}\s*[：:]\s*(.+?)(?=\s+\S+\s*[：:]|$)", info)
        if m:
            extra[key] = m.group(1).strip()

    return TrackingResult(
        status=status_from_events(events),
        status_text=title or events[-1]["description"],
        events=events,
        extra=extra,
    )
