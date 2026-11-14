"""Coordinator: the configured to-do list is the source of parcels.

Each open (needs_action) item containing a tracking number is tracked. The
carrier is auto-detected by probing candidate carriers and remembered. When a
parcel is delivered, the item is marked completed (moves to the list's done
section) and drops out of tracking. Completed items stay visible as
`delivered` (last known result kept; no further carrier queries).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .carriers import (
    BATCH_SIZE,
    FETCHERS,
    BatchResult,
    CarrierError,
    TrackingResult,
    candidates,
    parse_item,
)
from .carriers.base import STATUS_DELIVERED, STATUS_ERROR, STATUS_NOT_FOUND, STATUS_PENDING
from .const import (
    CARRIERS,
    CONF_AUTO_COMPLETE,
    CONF_SCAN_INTERVAL,
    CONF_TODO_ENTITY,
    CONF_WRITE_STATUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


@dataclass
class Parcel:
    number: str
    name: str
    uid: str
    description: str  # current to-do item description
    carrier: str | None = None
    forced: bool = False  # carrier given via "carrier:" prefix
    result: TrackingResult = field(default_factory=lambda: TrackingResult(status=STATUS_PENDING))
    fetched_at: Any = None  # datetime of last carrier query


def option(entry: ConfigEntry, key: str, default: Any = None) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


class JapanTrackerCoordinator(DataUpdateCoordinator[dict[str, Parcel]]):
    """Data is keyed by tracking number."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.interval = timedelta(minutes=option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=self.interval)
        self.entry = entry
        self.todo_entity: str = option(entry, CONF_TODO_ENTITY)
        self.auto_complete: bool = option(entry, CONF_AUTO_COMPLETE, True)
        self.write_status: bool = option(entry, CONF_WRITE_STATUS, True)
        self.session = async_get_clientsession(hass)
        self._store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._detected: dict[str, str] = {}  # number -> carrier
        # Completed items on the list (not queried any more), list order, newest ticks first.
        self.delivered: dict[str, Parcel] = {}

    async def async_load(self) -> None:
        self._detected = await self._store.async_load() or {}

    # ---- to-do list access -------------------------------------------------

    async def _items(self) -> list[dict[str, Any]]:
        if self.hass.states.get(self.todo_entity) is None:
            raise UpdateFailed(f"To-do list {self.todo_entity} not found")
        resp = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": self.todo_entity, "status": ["needs_action", "completed"]},
            blocking=True,
            return_response=True,
        )
        return (resp or {}).get(self.todo_entity, {}).get("items", [])

    def _supports(self, feature: TodoListEntityFeature) -> bool:
        state = self.hass.states.get(self.todo_entity)
        return bool(state and state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) & feature)

    async def _update_item(self, uid: str, **fields: Any) -> bool:
        try:
            await self.hass.services.async_call(
                "todo", "update_item", {"entity_id": self.todo_entity, "item": uid, **fields}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - never let a list write break tracking
            _LOGGER.warning("Could not update to-do item %s: %s", uid, err)
            return False
        return True

    # ---- carrier queries -----------------------------------------------------

    async def _carrier_batches(
        self, carrier: str, numbers: list[str], previous: dict[str, TrackingResult]
    ) -> BatchResult:
        """All numbers for one carrier, BATCH_SIZE per request, one request at a time."""
        out: BatchResult = {}
        for i in range(0, len(numbers), BATCH_SIZE):
            chunk = numbers[i : i + BATCH_SIZE]
            try:
                out.update(await FETCHERS[carrier](self.session, chunk, previous))
            except Exception as err:  # noqa: BLE001 - a carrier bug must not stop the others
                _LOGGER.exception("Unexpected error querying %s", carrier)
                out.update({n: CarrierError(str(err)) for n in chunk})
        return out

    async def _query(self, due: list[Parcel], old: dict[str, Parcel]) -> None:
        """Query all due parcels with as few requests as possible.

        Each round groups parcels by the carrier to try next and sends one
        batch per carrier (carriers in parallel). Parcels whose carrier is
        still unknown move on to their next candidate carrier in the next round.
        """
        by_number = {p.number: p for p in due}
        # Known carrier (detected earlier or forced): just that one, and its
        # "not found" is the answer. Unknown: probe candidates in order.
        probing = {p.number: p.carrier is None for p in due}
        queue = {p.number: [p.carrier] if p.carrier else candidates(p.number) for p in due}
        errors: dict[str, list[str]] = {n: [] for n in by_number}
        not_found_at = dict.fromkeys(by_number, 0)
        resolved: set[str] = set()

        while pending := [n for n, q in queue.items() if q and n not in resolved]:
            groups: dict[str, list[str]] = {}
            for n in pending:
                groups.setdefault(queue[n].pop(0), []).append(n)
            previous = {n: old[n].result for n in pending if n in old}
            carrier_list = list(groups)
            batches = await asyncio.gather(
                *(self._carrier_batches(c, groups[c], previous) for c in carrier_list)
            )
            for carrier, batch in zip(carrier_list, batches):
                for number, result in batch.items():
                    if isinstance(result, CarrierError):
                        errors[number].append(f"{carrier}: {result}")
                    elif result.status == STATUS_NOT_FOUND and probing[number]:
                        not_found_at[number] += 1  # try the next candidate next round
                    else:
                        self._resolve(by_number[number], carrier, result)
                        resolved.add(number)

        now = dt_util.utcnow()
        for parcel in due:
            parcel.fetched_at = now
            if parcel.number in resolved:
                continue
            errs, prev = errors[parcel.number], old.get(parcel.number)
            for err in errs:
                _LOGGER.warning("Tracking %s failed: %s", parcel.number, err)
            if prev and prev.result.events:
                parcel.result = prev.result  # temporary failure: keep last good data
            elif errs and not not_found_at[parcel.number]:
                parcel.result = TrackingResult(status=STATUS_ERROR, status_text="; ".join(errs))
            else:  # no carrier knows it yet (just shipped?) - retried next interval
                names = "/".join(CARRIERS[c].split(" (")[0] for c in candidates(parcel.number))
                parcel.result = TrackingResult(status=STATUS_NOT_FOUND, status_text=f"Not registered yet ({names})")

    def _resolve(self, parcel: Parcel, carrier: str, result: TrackingResult) -> None:
        parcel.carrier, parcel.result = carrier, result
        if not parcel.forced and self._detected.get(parcel.number) != carrier:
            self._detected[parcel.number] = carrier
            self._store.async_delay_save(lambda: self._detected, 5)

    def _due(self, parcel: Parcel, previous: Parcel | None) -> bool:
        if previous is None or previous.fetched_at is None or previous.carrier != parcel.carrier:
            return True
        # 90% so a scheduled refresh never just misses the interval.
        return dt_util.utcnow() - previous.fetched_at >= self.interval * 0.9

    # ---- main loop -----------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Parcel]:
        old = self.data or {}
        parcels: dict[str, Parcel] = {}
        items = await self._items()
        for item in items:
            if item.get("status") != "needs_action":
                continue
            parsed = parse_item(item.get("summary", ""))
            if parsed is None or parsed.number in parcels:
                continue
            carrier = parsed.carrier or self._detected.get(parsed.number)
            parcels[parsed.number] = Parcel(
                number=parsed.number,
                name=parsed.name or parsed.number,
                uid=item["uid"],
                description=item.get("description") or "",
                carrier=carrier,
                forced=parsed.carrier is not None,
            )

        due, fresh = [], []
        for number, parcel in parcels.items():
            previous = old.get(number)
            if self._due(parcel, previous):
                due.append(parcel)
            else:
                parcel.result, parcel.fetched_at = previous.result, previous.fetched_at
            fresh.append(parcel)
        if due:
            await self._query(due, old)

        # Items we just ticked as done leave tracking now, rather than on the
        # next (debounced) refresh triggered by the list change.
        ticked = {n: parcels.pop(n) for n in await self._sync_list(fresh) if n in parcels}
        self.delivered = {**ticked, **self._done_parcels(items, parcels, ticked, old)}

        # Forget detections for numbers no longer on the list.
        if stale := set(self._detected) - set(parcels) - set(self.delivered):
            for number in stale:
                self._detected.pop(number)
            self._store.async_delay_save(lambda: self._detected, 5)
        return parcels

    def _done_parcels(
        self, items: list[dict[str, Any]], open_: dict[str, Parcel], ticked: dict[str, Parcel], old: dict[str, Parcel]
    ) -> dict[str, Parcel]:
        """Completed list items, reusing the last tracking result we have for each."""
        done: dict[str, Parcel] = {}
        for item in items:
            if item.get("status") != "completed":
                continue
            parsed = parse_item(item.get("summary", ""))
            if parsed is None or parsed.number in open_ or parsed.number in ticked or parsed.number in done:
                continue
            name, description = parsed.name or parsed.number, item.get("description") or ""
            prev = self.delivered.get(parsed.number) or old.get(parsed.number)
            if prev is not None:
                prev.name, prev.uid, prev.description = name, item["uid"], description
                done[parsed.number] = prev
                continue
            # Ticked before this run (e.g. HA restarted): only the list text is known.
            done[parsed.number] = Parcel(
                number=parsed.number,
                name=name,
                uid=item["uid"],
                description=description,
                carrier=parsed.carrier or self._detected.get(parsed.number),
                forced=parsed.carrier is not None,
                result=TrackingResult(status=STATUS_DELIVERED, status_text=description),
            )
        return done

    async def _sync_list(self, parcels: list[Parcel]) -> set[str]:
        """Write statuses / tick delivered items. Returns numbers marked completed."""
        completed: set[str] = set()
        can_describe = self.write_status and self._supports(TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM)
        can_update = self._supports(TodoListEntityFeature.UPDATE_TODO_ITEM)
        if not can_update:
            return completed
        for parcel in parcels:
            fields: dict[str, Any] = {}
            if can_describe:
                text = describe(parcel)
                if text != parcel.description:
                    fields["description"] = text
                    parcel.description = text
            if self.auto_complete and parcel.result.status == STATUS_DELIVERED:
                fields["status"] = "completed"
            if fields and await self._update_item(parcel.uid, **fields) and "status" in fields:
                completed.add(parcel.number)
        return completed


def describe(parcel: Parcel) -> str:
    """One-line status written into the to-do item's description."""
    carrier = CARRIERS[parcel.carrier].split(" (")[0] if parcel.carrier else "Detecting carrier"
    res = parcel.result
    parts = [f"{carrier}: {res.status_text or res.status}"]
    if last := res.last_event:
        when_where = " ".join(x for x in (last.get("time"), last.get("location")) if x)
        if when_where:
            parts.append(when_where)
    return " · ".join(parts)
