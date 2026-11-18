"""Japan Parcel Tracker: Sagawa, Kuroneko Yamato, Japan Post and Cainiao.

Parcels are entered as items on a Home Assistant to-do list chosen at setup.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.todo.const import DATA_COMPONENT as TODO_DATA_COMPONENT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType

from .const import CARD_URL, DOMAIN
from .coordinator import JapanTrackerCoordinator

PLATFORMS = ["sensor"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Serve + auto-load the dashboard card.
    js = Path(__file__).parent / "frontend" / "japan-tracker-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(js), False)])
    # Version the URL by content so browsers and HA's service worker drop a cached old card after an update.
    digest = await hass.async_add_executor_job(lambda: hashlib.sha1(js.read_bytes()).hexdigest()[:10])
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={digest}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = JapanTrackerCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # React quickly when the list changes. Only new parcels are queried; others
    # wait their turn.
    @callback
    def _refresh(*_: Any) -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    # The entity's state is only its open-item count, so removing completed
    # items (clearing the done list) or editing one doesn't change it. Item
    # updates are pushed to subscribers, like the frontend's list card.
    subscription: dict[str, Any] = {"entity": None, "unsub": None}

    @callback
    def _subscribe_items() -> None:
        component = hass.data.get(TODO_DATA_COMPONENT)
        entity = component.get_entity(coordinator.todo_entity) if component else None
        if entity is subscription["entity"]:
            return
        if subscription["unsub"]:
            subscription["unsub"]()
        subscription["entity"] = entity
        subscription["unsub"] = entity.async_subscribe_updates(_refresh) if entity else None

    @callback
    def _unsubscribe_items() -> None:
        if subscription["unsub"]:
            subscription["unsub"]()

    # State changes still trigger a refresh, and re-subscribe if the list
    # entity was (re)loaded after us.
    @callback
    def _state_changed(event: Event[EventStateChangedData]) -> None:
        _subscribe_items()
        _refresh()

    _subscribe_items()
    entry.async_on_unload(_unsubscribe_items)
    entry.async_on_unload(
        async_track_state_change_event(hass, [coordinator.todo_entity], _state_changed)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
