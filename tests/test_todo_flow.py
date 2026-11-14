"""End-to-end test in a real Home Assistant core with a real Local To-do list.

Carrier HTTP calls are replaced by fakes (the live carrier parsers are tested
separately against the real sites); everything else is real: config flow,
to-do list services, coordinator, sensor creation/removal, entity registry.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.japan_tracker import carriers
from custom_components.japan_tracker.carriers.base import (
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_NOT_FOUND,
    TrackingResult,
)
from custom_components.japan_tracker.const import DOMAIN

TODO = "todo.parcels"

# (carrier, number) -> result. Anything else is "not found" at that carrier.
WORLD = {
    ("sagawa", "111122223333"): TrackingResult(
        status=STATUS_IN_TRANSIT, status_text="輸送中",
        events=[{"description": "輸送中", "time": "11/13 17:40", "location": "テスト中継センター"}],
    ),
    ("kuroneko", "444455556666"): TrackingResult(
        status=STATUS_DELIVERED, status_text="配達完了（宅配ボックス）",
        events=[{"description": "配達完了（宅配ボックス）", "time": "09月11日 10:26", "location": "サンプル営業所"}],
    ),
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_http_listen():
    """frontend/http register routes for real, but don't open a listening socket."""
    with patch("homeassistant.components.http.HomeAssistantHTTP.start"):
        yield


@pytest.fixture(autouse=True)
def writable_config_dir(hass: HomeAssistant, tmp_path):
    """Local To-do saves an .ics file under <config>/.storage."""
    (tmp_path / ".storage").mkdir()
    hass.config.config_dir = str(tmp_path)


class Calls(list):
    """Every fake carrier request, as (carrier, (numbers...))."""

    def numbers(self) -> list[tuple[str, str]]:
        """Flattened (carrier, number) pairs, in request order."""
        return [(c, n) for c, batch in self for n in batch]


@pytest.fixture
def calls():
    """Fake batch carriers (fetch_many) recording each request."""
    log = Calls()

    def make(carrier):
        async def fetch_many(session, numbers, previous):
            assert len(numbers) <= carriers.BATCH_SIZE
            log.append((carrier, tuple(numbers)))
            return {n: WORLD.get((carrier, n), TrackingResult(status=STATUS_NOT_FOUND)) for n in numbers}
        return fetch_many

    with patch.dict(carriers.FETCHERS, {c: make(c) for c in carriers.FETCHERS}):
        yield log


async def _items(hass: HomeAssistant, status: str) -> dict[str, dict]:
    resp = await hass.services.async_call(
        "todo", "get_items", {"entity_id": TODO, "status": [status]}, blocking=True, return_response=True
    )
    return {i["summary"]: i for i in resp[TODO]["items"]}


async def _add(hass: HomeAssistant, text: str) -> None:
    await hass.services.async_call("todo", "add_item", {"entity_id": TODO, "item": text}, blocking=True)
    await hass.async_block_till_done()


def _sensor(hass: HomeAssistant, number: str):
    reg = er.async_get(hass)
    entry = next(e for e in hass.config_entries.async_entries(DOMAIN))
    eid = reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{number}")
    return eid and hass.states.get(eid)


async def test_todo_list_flow(hass: HomeAssistant, calls) -> None:
    # A real Local To-do list named "Parcels".
    todo_entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Parcels", "storage_key": "parcels"})
    todo_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(todo_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(TODO)

    await _add(hass, "Headphones 1111-2222-3333")  # Sagawa, needs probing past Kuroneko
    await _add(hass, "444455556666")  # Kuroneko, already delivered
    await _add(hass, "milk")  # not a parcel: ignored

    # Setup via the real config flow, choosing the list.
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"todo_entity": TODO})
    assert result["type"] == "create_entry", result
    assert result["title"] == "Parcels: Parcels"
    await hass.async_block_till_done()

    # Auto-detection, batched: round 1 asks Kuroneko about BOTH numbers in one
    # request; round 2 asks Sagawa only about the one Kuroneko didn't know.
    assert list(calls) == [
        ("kuroneko", ("111122223333", "444455556666")),
        ("sagawa", ("111122223333",)),
    ]

    s = _sensor(hass, "111122223333")
    assert s.state == STATUS_IN_TRANSIT
    assert s.attributes["carrier"] == "sagawa"
    assert s.attributes["name"] == "Headphones"

    # Delivered parcel ticked as done; status written into descriptions.
    open_items, done = await _items(hass, "needs_action"), await _items(hass, "completed")
    assert "444455556666" in done
    assert done["444455556666"]["description"].startswith("Kuroneko Yamato: 配達完了")
    assert open_items["Headphones 1111-2222-3333"]["description"] == "Sagawa Express: 輸送中 · 11/13 17:40 テスト中継センター"
    assert "description" not in open_items["milk"] or not open_items["milk"].get("description")

    # Completing the item removed its sensor; summary counts open parcels.
    await hass.async_block_till_done()
    assert _sensor(hass, "444455556666") is None
    summary = hass.states.get("sensor.japan_tracker_packages")
    assert summary.state == "1"
    assert summary.attributes["todo_entity"] == TODO
    assert [p["number"] for p in summary.attributes["packages"]] == ["111122223333"]
    # ...but it stays visible as delivered, with its full tracking result.
    done_sensor = hass.states.get("sensor.japan_tracker_delivered")
    assert done_sensor.state == "1"
    [delivered] = done_sensor.attributes["packages"]
    assert delivered["number"] == "444455556666"
    assert delivered["status"] == STATUS_DELIVERED
    assert delivered["carrier"] == "kuroneko"
    assert delivered["events"]

    # Adding an item reacts immediately; existing parcels are NOT re-queried
    # (detected carrier is remembered and they're not due yet).
    calls.clear()
    await _add(hass, "jp: EA123456789JP gift")
    assert list(calls) == [("japanpost", ("EA123456789JP",))]  # forced carrier, no probing
    s = _sensor(hass, "EA123456789JP")
    assert s.state == STATUS_NOT_FOUND
    assert s.attributes["carrier"] == "japanpost"

    # Deleting an item removes its sensor. List changes in quick succession are
    # batched by HA's refresh debouncer (10 s cooldown), so advance the clock.
    uid = (await _items(hass, "needs_action"))["Headphones 1111-2222-3333"]["uid"]
    await hass.services.async_call("todo", "remove_item", {"entity_id": TODO, "item": uid}, blocking=True)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert _sensor(hass, "111122223333") is None
    assert hass.states.get("sensor.japan_tracker_packages").state == "1"  # the EA… parcel
    assert not calls[1:]  # still no extra carrier queries


async def _setup(hass: HomeAssistant, items: list[str]):
    todo_entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Parcels", "storage_key": "parcels"})
    todo_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(todo_entry.entry_id)
    await hass.async_block_till_done()
    for text in items:
        await hass.services.async_call("todo", "add_item", {"entity_id": TODO, "item": text}, blocking=True)
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=TODO, data={"todo_entity": TODO})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id]


async def test_batches_per_carrier_and_chunks_of_ten(hass: HomeAssistant, calls) -> None:
    """12 Sagawa parcels (known carrier) + 2 Cainiao: Sagawa in 10+2, Cainiao in 1."""
    sagawa = [f"sagawa: 1111222233{i:02d}" for i in range(12)]
    cainiao = ["E12345678901", "LP00123456789012"]
    for line in sagawa:
        n = carriers.parse_item(line).number
        WORLD[("sagawa", n)] = TrackingResult(status=STATUS_IN_TRANSIT, status_text="輸送中")
    await _setup(hass, sagawa + cainiao)

    assert sorted((c, len(b)) for c, b in calls) == [("cainiao", 2), ("sagawa", 2), ("sagawa", 10)]
    summary = hass.states.get("sensor.japan_tracker_packages")
    assert summary.state == "14"

    # Next scheduled refresh within the interval: nothing is due, no requests.
    calls.clear()
    coordinator = hass.data[DOMAIN][next(iter(hass.data[DOMAIN]))]
    await coordinator.async_refresh()
    assert calls == []


async def test_completed_items_listed_as_delivered(hass: HomeAssistant, calls) -> None:
    """Items already ticked before setup (e.g. after a restart) show as delivered, never queried."""
    todo_entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Parcels", "storage_key": "parcels"})
    todo_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(todo_entry.entry_id)
    await hass.async_block_till_done()
    await _add(hass, "sagawa: 999988887777 Old box")
    uid = (await _items(hass, "needs_action"))["sagawa: 999988887777 Old box"]["uid"]
    await hass.services.async_call(
        "todo", "update_item",
        {"entity_id": TODO, "item": uid, "status": "completed", "description": "Sagawa Express: 配達完了"},
        blocking=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=TODO, data={"todo_entity": TODO})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls == []
    summary = hass.states.get("sensor.japan_tracker_packages")
    assert summary.state == "0"
    assert hass.states.get("sensor.japan_tracker_delivered").state == "1"
    [d] = hass.states.get("sensor.japan_tracker_delivered").attributes["packages"]
    assert (d["number"], d["name"], d["carrier"], d["status"]) == ("999988887777", "Old box", "sagawa", STATUS_DELIVERED)
    assert d["status_text"] == "Sagawa Express: 配達完了"
    assert d["url"]


async def test_partial_batch_failure(hass: HomeAssistant, calls) -> None:
    """One number erroring inside a batch doesn't affect the others."""
    async def flaky(session, numbers, previous):
        calls.append(("kuroneko", tuple(numbers)))
        return {
            "444455556666": WORLD[("kuroneko", "444455556666")],
            "111111111111": carriers.CarrierError("boom"),
        }

    with patch.dict(carriers.FETCHERS, {"kuroneko": flaky}):
        await _setup(hass, ["yamato: 444455556666", "yamato: 111111111111"])

    assert list(calls) == [("kuroneko", ("444455556666", "111111111111"))]
    assert _sensor(hass, "111111111111").state == "error"
    # The healthy one was delivered and ticked off as usual.
    assert "yamato: 444455556666" in await _items(hass, "completed")


async def test_options_flow(hass: HomeAssistant, calls) -> None:
    todo_entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Parcels", "storage_key": "parcels"})
    todo_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(todo_entry.entry_id)
    entry = MockConfigEntry(domain=DOMAIN, version=2, unique_id=TODO, data={"todo_entity": TODO})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"todo_entity": TODO, "auto_complete": False, "write_status": False, "scan_interval": 30},
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.auto_complete is False and coordinator.write_status is False

    # With auto-complete off, delivered items stay open and descriptions untouched.
    await _add(hass, "444455556666")
    await coordinator.async_refresh()
    assert "444455556666" in await _items(hass, "needs_action")
    assert not (await _items(hass, "needs_action"))["444455556666"].get("description")


async def test_duplicate_list_rejected(hass: HomeAssistant, calls) -> None:
    todo_entry = MockConfigEntry(domain="local_todo", data={"todo_list_name": "Parcels", "storage_key": "parcels"})
    todo_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(todo_entry.entry_id)
    MockConfigEntry(domain=DOMAIN, version=2, unique_id=TODO, data={"todo_entity": TODO}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"todo_entity": TODO})
    assert result["type"] == "abort" and result["reason"] == "already_configured"
