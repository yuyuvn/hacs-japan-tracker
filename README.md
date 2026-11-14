# Japan Parcel Tracker (HACS)

Track **Sagawa (佐川急便)**, **Kuroneko Yamato (ヤマト運輸)**, **Japan Post (日本郵便, domestic + EMS/international)** and **Cainiao (AliExpress)** parcels in Home Assistant, using a **to-do list** as the input.

Amazon Japan is not supported: it has no public tracking page (tracking requires an Amazon login). If an Amazon order shows a Yamato, Sagawa or Japan Post number, add that instead.

## Install
1. HACS → ⋮ → Custom repositories → add `https://github.com/yuyuvn/hacs-japan-tracker` as type **Integration**.
2. Install, restart Home Assistant.
3. (Recommended) Create a dedicated list: Settings → Devices & services → Add integration → **Local To-do**, name it e.g. "Parcels".
4. Add Integration → **Japan Parcel Tracker** → pick that to-do list.

## Usage
Add one item per parcel to the list, from the to-do panel, the mobile app, voice, the dashboard card, or automations (`todo.add_item`):

| Item text | Result |
|---|---|
| `111122223333` | carrier auto-detected |
| `Headphones 1111-2222-3333` | name "Headphones", hyphens/full-width digits OK |
| `EA123456789JP` | Japan Post (international) |
| `Shoes E12345678901` | Cainiao |
| `sagawa: 111122223333` | force a carrier (`sagawa:`, `yamato:`, `jp:`, `ali:`; also 佐川/ヤマト/郵便) |

Items without a tracking number (e.g. "milk") are ignored.

**Carrier detection:** international `XX123456789JP` → Japan Post; other international formats and AliExpress numbers → Cainiao. Plain 10–12 digit numbers are shared by Yamato, Sagawa and Japan Post (all use the same mod-7 check digit), so they are tried in that order and the first carrier that knows the number is remembered. A number no carrier knows yet (just shipped) is retried every update.

**Automatic list updates** (both can be turned off under *Configure*):
- the latest status is written into each item's description, so the list itself shows progress;
- delivered parcels are **marked as done** and move to the list's completed section.

## Dashboard
Two cards are registered automatically (no resource to add); both appear in the dashboard card picker and can be configured from the visual editor (or in YAML).

**Status card**: visual delivery progress for each parcel:

```yaml
type: custom:japan-tracker-status-card
entity: sensor.japan_tracker_packages   # default
title: Deliveries                        # default
show_number: true                        # show tracking numbers under names
layout: auto                             # auto | full | compact (auto: one line per parcel above 4 parcels)
```

- Summary chips at the top (e.g. "1 out for delivery · 1 missed · 2 in transit")
- A 4-step tracker per parcel: Picked up → In transit → Out for delivery → Delivered; a missed delivery is flagged in orange
- Last event, delivery estimate when the carrier gives one (Yamato, Sagawa, Japan Post redelivery date), Cainiao's progress %
- Expandable history timeline; most urgent parcels first
- Follows your HA theme (light/dark) and works at phone width

**List card**: compact list with a form to add parcels to the to-do list:

```yaml
type: custom:japan-tracker-card
entity: sensor.japan_tracker_packages
delivered_entity: sensor.japan_tracker_delivered   # default; "Delivered" section
show_delivered: true                                # false hides the "Delivered" section
title: Parcels
```

**Show conditions** (both cards, under *Show conditions* in the editor):

```yaml
statuses: [out_for_delivery, failed_attempt]   # only parcels with these statuses (default: all)
carriers: [sagawa, kuroneko]                   # only these carriers: sagawa, kuroneko, japanpost, cainiao (default: all)
hide_empty: true                               # hide the whole card when no parcel matches
```

A card hidden by `hide_empty` stays visible while editing the dashboard. For other card-level conditions (time of day, user, screen size, another entity's state) use the editor's built-in **Visibility** tab.

Or simply put a **To-do list card** for the list on your dashboard: the descriptions show the status.

## Entities
- `sensor.japan_tracker_packages` – number of undelivered parcels; attribute `packages` holds all open parcels.
- `sensor.japan_tracker_delivered` – number of completed (delivered) items on the list; attribute `packages` holds them (newest first, up to 20) with their last known tracking result. Delivered parcels are no longer queried.
- `sensor.parcel_<name>` – one per open list item, created/removed automatically. State: `pending`, `info_received`, `in_transit`, `out_for_delivery`, `failed_attempt`, `delivered`, `not_found`, `error`, `unknown`.

Example automation trigger: a parcel sensor changes to `out_for_delivery`.

## Notes
- No official APIs are used; the integration reads the carriers' public tracking pages, so a site redesign can break parsing (parsers are isolated in `carriers/`).
- Default update interval is 60 min (min 15). New list items are looked up immediately; renaming an item is picked up at the next update.
- Requests are batched: all due parcels for the same carrier go in one request (up to 10 numbers each; more are split and sent one after another). Different carriers are queried in parallel. Japan Post's batch page only gives the latest status, so a parcel's full history is fetched only when that status changed.
- Several lists can be tracked by adding the integration once per list.

## Development
```bash
pip install pytest-homeassistant-custom-component beautifulsoup4 home-assistant-frontend "pycares<4.6"
pytest
```
`home-assistant-frontend` must match the installed HA version (see `homeassistant/components/frontend/manifest.json`). Newer `pycares` starts a background thread that HA's test teardown flags as a leak; pinning avoids that false error.

Carrier parsers were verified against the live sites with real tracking numbers; the HA tests use fake carriers so they run offline:
```bash
pytest tests/test_todo_flow.py -q
```
