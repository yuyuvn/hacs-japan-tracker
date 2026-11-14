"""Sensors: one per tracked to-do item, plus summaries used by the dashboard cards
(parcels on the way, and delivered = completed items on the list).

Parcel sensors are created and removed as items appear on / leave the list.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .carriers import PUBLIC_URLS
from .carriers.base import ALL_STATUSES, STATUS_DELIVERED, STATUS_UNKNOWN
from .const import CARRIERS, DOMAIN
from .coordinator import JapanTrackerCoordinator, Parcel

SUMMARY_SUFFIX = "summary"
DELIVERED_SUFFIX = "delivered"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JapanTrackerCoordinator = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    known: set[str] = set()
    async_add_entities([SummarySensor(coordinator), DeliveredSensor(coordinator)])

    @callback
    def _sync() -> None:
        current = set(coordinator.data or {})
        if new := current - known:
            known.update(new)
            async_add_entities(PackageSensor(coordinator, n) for n in new)
        # Remove entities for items that were completed/deleted, including ones
        # left in the registry from before a restart.
        for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
            number = reg.unique_id.removeprefix(f"{entry.entry_id}_")
            if number not in (SUMMARY_SUFFIX, DELIVERED_SUFFIX) and number not in current:
                known.discard(number)
                registry.async_remove(reg.entity_id)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


def parcel_dict(p: Parcel) -> dict[str, Any]:
    res = p.result
    last = res.last_event or {}
    return {
        "number": p.number,
        "name": p.name,
        "uid": p.uid,
        "carrier": p.carrier,
        "carrier_name": CARRIERS[p.carrier] if p.carrier else "Detecting…",
        "status": res.status,
        "status_text": res.status_text,
        "last_description": last.get("description", ""),
        "last_time": last.get("time", ""),
        "last_location": last.get("location", ""),
        "events": list(reversed(res.events))[:10],  # newest first
        "url": PUBLIC_URLS[p.carrier].format(number=p.number) if p.carrier else "",
        **res.extra,
    }


class PackageSensor(CoordinatorEntity[JapanTrackerCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ALL_STATUSES
    _attr_icon = "mdi:package-variant-closed"

    def __init__(self, coordinator: JapanTrackerCoordinator, number: str) -> None:
        super().__init__(coordinator)
        self.number = number
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{number}"

    @property
    def _parcel(self) -> Parcel | None:
        return (self.coordinator.data or {}).get(self.number)

    @property
    def available(self) -> bool:
        return super().available and self._parcel is not None

    @property
    def name(self) -> str:
        p = self._parcel
        return f"Parcel {p.name if p else self.number}"

    @property
    def native_value(self) -> str:
        p = self._parcel
        return p.result.status if p else STATUS_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self._parcel
        return parcel_dict(p) if p else {}


class SummarySensor(CoordinatorEntity[JapanTrackerCoordinator], SensorEntity):
    """State = number of undelivered parcels; attributes carry every parcel."""

    _attr_icon = "mdi:truck-delivery"
    _attr_native_unit_of_measurement = "parcels"

    def __init__(self, coordinator: JapanTrackerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{SUMMARY_SUFFIX}"
        self._attr_name = "Japan Tracker Packages"

    @property
    def native_value(self) -> int:
        return sum(1 for p in (self.coordinator.data or {}).values() if p.result.status != STATUS_DELIVERED)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "todo_entity": self.coordinator.todo_entity,
            "packages": [parcel_dict(p) for p in (self.coordinator.data or {}).values()],
        }


class DeliveredSensor(CoordinatorEntity[JapanTrackerCoordinator], SensorEntity):
    """State = number of completed parcels on the list; attributes carry them (newest ticks first)."""

    _attr_icon = "mdi:package-variant-closed-check"
    _attr_native_unit_of_measurement = "parcels"
    MAX_LISTED = 20  # keep the state attributes small

    def __init__(self, coordinator: JapanTrackerCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{DELIVERED_SUFFIX}"
        self._attr_name = "Japan Tracker Delivered"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.delivered)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "todo_entity": self.coordinator.todo_entity,
            "packages": [parcel_dict(p) for p in list(self.coordinator.delivered.values())[: self.MAX_LISTED]],
        }
