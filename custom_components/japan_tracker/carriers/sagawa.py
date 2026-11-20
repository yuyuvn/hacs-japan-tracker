"""Sagawa Express (佐川急便) tracker.

Sagawa's tracking page (e-customer.sagawa-exp.co.jp/k2k) now redirects to the
classic JSF site on k2k.sagawa-exp.co.jp. Its input form takes up to 10
numbers (``main:no1`` .. ``main:no10``) and needs the page's
``javax.faces.ViewState``, so a lookup is a GET of the form followed by one
POST. Pages are Shift_JIS (Windows-31J).
"""
from __future__ import annotations

import re
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

from .base import (
    BATCH_SIZE,
    STATUS_NOT_FOUND,
    STATUS_UNKNOWN,
    BatchResult,
    CarrierError,
    TrackingResult,
    all_failed,
    classify,
    clean,
    decode,
    fetch_single,
    fill_missing,
    status_from_events,
)

BASE = "https://k2k.sagawa-exp.co.jp"
FORM_URL = BASE + "/p/sagawa/web/okurijoinput.jsp"
PUBLIC_URL = BASE + "/p/web/okurijosearch.do?okurijoNo={number}"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HomeAssistant-JapanTracker"}
TIMEOUT = aiohttp.ClientTimeout(total=30)

_NOT_FOUND_WORDS = ("該当", "見つかりません", "登録されていません", "未登録", "お確かめください")
_BUSY_WORDS = ("混み合", "暫くたってから", "しばらくたってから")


async def _get(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> str:
    async with session.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs) as resp:
        resp.raise_for_status()
        return decode(await resp.read())


async def _post(session: aiohttp.ClientSession, data: dict[str, str]) -> str:
    headers = {**HEADERS, "Origin": BASE, "Referer": FORM_URL}
    async with session.post(FORM_URL, data=data, headers=headers, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        return decode(await resp.read())


def _view_state(html: str) -> str:
    tag = BeautifulSoup(html, "html.parser").find("input", attrs={"name": "javax.faces.ViewState"})
    if tag is None or not tag.get("value"):
        _raise_busy(html)
        raise CarrierError("Sagawa input form layout not recognised")
    return tag["value"]


def _raise_busy(html: str) -> None:
    """Sagawa answers with a 200 'service is busy, try later' page when overloaded."""
    if any(w in html for w in _BUSY_WORDS):
        raise CarrierError("Sagawa tracking service is busy, try again later")


async def fetch_many(
    session: aiohttp.ClientSession, numbers: list[str], previous: dict[str, TrackingResult] | None = None
) -> BatchResult:
    """Look up to BATCH_SIZE numbers in one request."""
    try:
        view_state = _view_state(await _get(session, FORM_URL))
        data = {f"main:no{i + 1}": "" for i in range(BATCH_SIZE)}
        data.update({f"main:no{i + 1}": n for i, n in enumerate(numbers)})
        data.update(
            {
                "main:toiStart": "お問い合わせ開始",
                "main:correlation": "1",
                "main_SUBMIT": "1",
                "javax.faces.ViewState": view_state,
            }
        )
        results = parse_results(await _post(session, data))
    except (aiohttp.ClientError, TimeoutError) as err:
        return all_failed(numbers, CarrierError(f"Sagawa request failed: {err}"))
    except CarrierError as err:
        return all_failed(numbers, err)

    out: BatchResult = {n: r for n, r in results.items() if n in numbers}
    return fill_missing(numbers, out, "Sagawa")


async def fetch(session: aiohttp.ClientSession, number: str) -> TrackingResult:
    return await fetch_single(fetch_many, session, number)


def _text(tag: Any) -> str:
    return clean(tag.get_text(" ")) if tag is not None else ""


def _strip_mark(text: Any) -> str:
    """'↓集荷' / '⇒輸送中' -> '集荷' / '輸送中'."""
    return re.sub(r"^[^\w぀-ヿ一-鿿]+", "", clean(text))


def parse_results(html: str) -> dict[str, TrackingResult]:
    """Result page -> {number: result}, one entry per searched number."""
    soup = BeautifulSoup(html, "html.parser")
    toggle = soup.find("dl", class_="toggle")
    if toggle is None:
        _raise_busy(html)
        raise CarrierError("Sagawa result page layout not recognised")

    out: dict[str, TrackingResult] = {}
    for dt in toggle.find_all("dt", id=re.compile(r"^list\d+$")):
        number = re.sub(r"\D", "", _text(dt.find("strong")))
        if not number:
            continue
        detail = toggle.find("dd", id="detail" + dt["id"][len("list"):])
        out[number] = parse_entry(dt, detail)
    return out


def parse_entry(dt: Any, dd: Any) -> TrackingResult:
    """Parse one number's summary (<dt>) and detail (<dd>) blocks."""
    top = _text(dt.find("span", class_="state"))

    # Summary message line, plus optional date lines (e.g. 配達完了日) in dl.okurijo_info.
    dates: list[str] = []
    message = ""
    cell = dt.find("td")
    if cell is not None:
        info = cell.find("dl", class_="okurijo_info")
        if info is not None:
            for item in info.find_all("dt"):
                label = _text(item).rstrip(":： ")
                value = _text(item.find_next_sibling("dd"))
                line = f"{label}: {value}" if label and value else label or value
                if line:
                    dates.append(line)
            info.extract()
        message = _text(cell)

    tables = dd.find_all("table") if dd is not None else []
    info_rows: dict[str, Any] = {}
    if tables:
        for tr in tables[0].find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if th is not None and td is not None:
                info_rows[_text(th)] = td

    events = []
    if len(tables) > 1:
        for tr in tables[1].find_all("tr"):
            cells = [_text(td) for td in tr.find_all("td")]
            if len(cells) >= 2 and cells[0]:
                events.append(
                    {
                        "description": _strip_mark(cells[0]),
                        "time": cells[1],
                        "location": cells[2] if len(cells) > 2 else "",
                    }
                )

    if not events and any(w in f"{top} {message}" for w in _NOT_FOUND_WORDS):
        return TrackingResult(status=STATUS_NOT_FOUND, status_text=top or message)

    status = classify(top)
    if status == STATUS_UNKNOWN:
        status = status_from_events(events)

    # Detail rows such as お届け予定日 / 配達完了日 also count as delivery dates.
    for label, td in info_rows.items():
        value = _text(td)
        if value and label != "出荷日" and "日" in label and f"{label}: {value}" not in dates:
            dates.append(f"{label}: {value}")

    def office(label: str) -> tuple[str, str]:
        td = info_rows.get(label)
        spans = [_text(s) for s in td.find_all("span", class_="eigyousyo")] if td is not None else []
        name = spans[0] if spans else ""
        tel = next((s.split(":", 1)[-1].strip() for s in spans[1:] if s.upper().startswith("TEL")), "")
        return name, tel

    pickup_name, _ = office("集荷に関するお問い合せ")
    delivery_name, delivery_tel = office("配達に関するお問い合せ")
    return TrackingResult(
        status=status,
        status_text=" – ".join(x for x in (top, message) if x),
        events=events,
        extra={
            "shipped_date": _text(info_rows.get("出荷日")),
            "package_count": _text(info_rows.get("お荷物個数")),
            "pickup_office": pickup_name,
            "delivery_office": delivery_name,
            "delivery_office_tel": delivery_tel,
            "delivery_dates": dates,
        },
    )
