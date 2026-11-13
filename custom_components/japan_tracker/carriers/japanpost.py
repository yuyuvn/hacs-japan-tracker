"""Japan Post (日本郵便) tracker: domestic (ゆうパック etc.) and international (EMS etc.)."""
from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup

from .base import (
    STATUS_NOT_FOUND,
    BatchResult,
    CarrierError,
    TrackingResult,
    all_failed,
    clean,
    decode,
    fill_missing,
    status_from_events,
)

URL = "https://trackings.post.japanpost.jp/services/srv/search/direct"
MULTI_URL = "https://trackings.post.japanpost.jp/services/srv/search/"
PUBLIC_URL = URL + "?reqCodeNo1={number}&searchKind=S002&locale=ja"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-JapanTracker"}
TIMEOUT = aiohttp.ClientTimeout(total=30)
_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}")
_POSTCODE_RE = re.compile(r"^\d{3}-\d{4}$")


async def _get(session: aiohttp.ClientSession, url: str, params: dict[str, str]) -> str:
    async with session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        return decode(await resp.read())


async def fetch(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    """Full history for one number (the detail page)."""
    try:
        html = await _get(session, URL, {"reqCodeNo1": number, "searchKind": "S002", "locale": "ja"})
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CarrierError(f"Japan Post request failed: {err}") from err
    return parse(html)


async def fetch_many(
    session: aiohttp.ClientSession, numbers: list[str], previous: dict[str, TrackingResult] | None = None
) -> BatchResult:
    """One multi-search request gives each number's *latest* status only.

    The full-history page is then fetched only for parcels whose latest status
    differs from what we already have, so unchanged parcels cost nothing extra.
    """
    previous = previous or {}
    params = {f"requestNo{i}": n for i, n in enumerate(numbers, start=1)}
    params.update({"search.x": "1", "search.y": "1", "startingUrlPatten": "", "locale": "ja"})
    try:
        latest = parse_summary(await _get(session, MULTI_URL, params))
    except (aiohttp.ClientError, TimeoutError) as err:
        return all_failed(numbers, CarrierError(f"Japan Post request failed: {err}"))
    except CarrierError as err:
        return all_failed(numbers, err)

    out: BatchResult = {}
    for number in numbers:
        if number not in latest:
            continue
        summary = latest[number]
        if summary is None:
            out[number] = TrackingResult(status=STATUS_NOT_FOUND, status_text="お問い合わせ番号が見つかりません")
        elif _unchanged(previous.get(number), *summary):
            out[number] = previous[number]
        else:
            try:  # sequential on purpose: gentle on the site
                out[number] = await fetch(session, number)
            except CarrierError as err:
                out[number] = err
    return fill_missing(numbers, out, "Japan Post")


def _unchanged(prev: TrackingResult | None, date: str, state: str) -> bool:
    last = prev.last_event if prev else None
    return bool(last and last.get("time") == date and last.get("description", "").startswith(state))


def parse_summary(html: str) -> dict[str, tuple[str, str] | None]:
    """Multi-search result -> {number: (latest date, latest state)}, None = not found."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", summary="照会結果")
    if table is None:
        raise CarrierError("Japan Post search page layout not recognised")
    out: dict[str, tuple[str, str] | None] = {}
    for tr in table.find_all("tr"):
        cells = _cells(tr)
        if len(cells) < 2:
            continue
        # Number cell is spaced/hyphenated: "EA 123 456 789 JP", "1234-5678-9012".
        number = re.sub(r"[^0-9A-Za-z]", "", cells[0]).upper()
        if not number or number == "お問い合わせ番号":
            continue
        if "見つかりません" in " ".join(cells[1:]):
            out[number] = None
        elif len(cells) >= 4 and _DATE_RE.match(cells[2]):
            out[number] = (cells[2], cells[3])  # 最新年月日, 最新状態
    return out


def _cells(tr) -> list[str]:
    return [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]


def parse(html: str) -> TrackingResult:
    soup = BeautifulSoup(html, "html.parser")

    history = soup.find("table", summary="履歴情報")
    if history is None:
        result_table = soup.find("table", summary="照会結果")
        if result_table and "見つかりません" in result_table.get_text():
            return TrackingResult(status=STATUS_NOT_FOUND, status_text="お問い合わせ番号が見つかりません")
        raise CarrierError("Japan Post page layout not recognised")

    # Each event is a row [date, status, detail, office, prefecture/country],
    # followed by a one-cell row holding the office's postcode.
    events: list[dict[str, str]] = []
    for tr in history.find_all("tr"):
        cells = _cells(tr)
        if len(cells) >= 2 and _DATE_RE.match(cells[0]):
            date, status, detail, office, region = (cells + [""] * 5)[:5]
            place = f"{office} ({region})" if office and region else office or region
            events.append(
                {
                    "description": f"{status} {detail}".strip(),
                    "time": date,
                    "location": place,
                }
            )
        elif len(cells) == 1 and _POSTCODE_RE.match(cells[0]) and events:
            events[-1]["postcode"] = cells[0]

    extra: dict[str, str] = {}
    info = soup.find("table", summary="配達状況詳細")
    if info:
        rows = [_cells(tr) for tr in info.find_all("tr")]
        if len(rows) >= 2 and len(rows[1]) >= 2:
            extra["product"] = rows[1][1]
            if len(rows[1]) >= 3 and rows[1][2]:
                extra["services"] = rows[1][2]
    office = soup.find("table", summary="窓口店")
    if office:
        for row in (_cells(tr) for tr in office.find_all("tr")[1:]):
            if len(row) >= 3 and row[0] == "配達":
                extra["delivery_office"] = row[1]
                extra["delivery_office_tel"] = row[2]

    if not events:
        return TrackingResult(status=STATUS_NOT_FOUND, status_text="No history yet", extra=extra)

    return TrackingResult(
        status=status_from_events(events),
        status_text=events[-1]["description"],
        events=events,
        extra=extra,
    )
