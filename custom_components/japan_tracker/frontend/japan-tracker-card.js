/* Japan Parcel Tracker cards. Both are configurable from the dashboard editor (getConfigForm).
 * Show conditions shared by both cards:
 *   statuses: [out_for_delivery, failed_attempt]   only parcels with these statuses (empty = all)
 *   carriers: [sagawa, kuroneko]                   only parcels from these carriers (empty = all)
 *   hide_empty: true                               hide the whole card when no parcel matches
 * The editor's built-in "Visibility" tab still works for card-level conditions (entity state, screen, user). */
const STATUS_OPTIONS = [
  ["out_for_delivery", "Out for delivery"], ["failed_attempt", "Missed delivery"], ["in_transit", "In transit"],
  ["info_received", "Picked up"], ["pending", "Pending"], ["not_found", "Not found"], ["error", "Error"],
  ["unknown", "Unknown"], ["delivered", "Delivered"],
].map(([value, label]) => ({ value, label }));
const CARRIER_OPTIONS = [
  ["sagawa", "Sagawa"], ["kuroneko", "Kuroneko Yamato"], ["japanpost", "Japan Post"], ["cainiao", "Cainiao"],
].map(([value, label]) => ({ value, label }));

const asList = (v) => (Array.isArray(v) ? v : v ? [v] : []);
// Apply the show conditions to a list of parcels.
const matches = (config) => {
  const statuses = asList(config.statuses), carriers = asList(config.carriers);
  return (p) => (!statuses.length || statuses.includes(p.status)) && (!carriers.length || carriers.includes(p.carrier));
};

const CONDITIONS_SCHEMA = {
  name: "", type: "expandable", title: "Show conditions", icon: "mdi:filter-variant",
  schema: [
    { name: "statuses", selector: { select: { multiple: true, mode: "list", options: STATUS_OPTIONS } } },
    { name: "carriers", selector: { select: { multiple: true, mode: "list", options: CARRIER_OPTIONS } } },
    { name: "hide_empty", selector: { boolean: {} } },
  ],
};
const FORM_LABELS = {
  entity: "Parcels sensor", delivered_entity: "Delivered sensor", show_delivered: "Show delivered section",
  title: "Title", layout: "Layout", show_number: "Show tracking numbers",
  statuses: "Only these statuses", carriers: "Only these carriers", hide_empty: "Hide card when no parcel matches",
};
const FORM_HELPERS = {
  statuses: "Leave empty to show every status.",
  carriers: "Leave empty to show every carrier.",
  hide_empty: "Hidden on the dashboard only; the editor preview keeps showing it.",
  layout: "Auto switches to one line per parcel above 4 parcels.",
};
const configForm = (schema) => ({
  schema: [...schema, CONDITIONS_SCHEMA],
  computeLabel: (s) => FORM_LABELS[s.name],
  computeHelper: (s) => FORM_HELPERS[s.name],
});
const sensorSelector = { entity: { domain: "sensor", integration: "japan_tracker" } };

// Hide the card (hide_empty) except in the editor, and tell HA's grid so it can drop the slot.
function setCardHidden(card, hide) {
  const editing = card.editMode || card.preview || card.hasAttribute("preview");
  const hidden = hide && !editing;
  if (card.hidden !== hidden) {
    card.hidden = hidden;
    card.style.display = hidden ? "none" : "";
    card.dispatchEvent(new Event("card-visibility-changed", { bubbles: true, composed: true }));
  }
  return hide && editing; // caller shows a "hidden on the dashboard" note
}
// HA sets editMode (dashboard edit mode) / preview (card editor) on cards; re-render so a hidden card shows up there.
function trackEditing(cls) {
  for (const prop of ["editMode", "preview"]) {
    Object.defineProperty(cls.prototype, prop, {
      get() { return this[`_${prop}`]; },
      set(v) {
        this[`_${prop}`] = v;
        this._sig = undefined;
        if (this._hass && this._config) this.hass = this._hass;
      },
    });
  }
}
const HIDDEN_NOTE = '<div class="hidden-note">No parcel matches the show conditions: this card is hidden on the dashboard.</div>';
const HIDDEN_NOTE_CSS = ".hidden-note{margin:8px 0;padding:6px 10px;border-radius:6px;font-size:12px;color:var(--secondary-text-color);border:1px dashed var(--divider-color)}";

const COLORS = {
  delivered: "var(--success-color, #43a047)",
  out_for_delivery: "var(--info-color, #039be5)",
  in_transit: "var(--primary-color)",
  info_received: "var(--secondary-text-color)",
  pending: "var(--secondary-text-color)",
  failed_attempt: "var(--warning-color, #ffa600)",
  not_found: "var(--error-color, #db4437)",
  error: "var(--error-color, #db4437)",
  unknown: "var(--secondary-text-color)",
};
const LABELS = {
  delivered: "Delivered", out_for_delivery: "Out for delivery", in_transit: "In transit",
  info_received: "Picked up", pending: "Pending", failed_attempt: "Delivery attempt failed",
  not_found: "Not found", error: "Error", unknown: "Unknown",
};

class JapanTrackerCard extends HTMLElement {
  setConfig(config) {
    // delivered_entity: false (older configs) is the same as show_delivered: false.
    this._config = {
      entity: "sensor.japan_tracker_packages", delivered_entity: "sensor.japan_tracker_delivered",
      show_delivered: config.delivered_entity !== false, title: "Parcels", ...config,
    };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._sig = undefined;
    if (this._hass) this.hass = this._hass;
  }
  getCardSize() { return 4; }
  static getStubConfig() { return { entity: "sensor.japan_tracker_packages" }; }
  static getConfigForm() {
    return configForm([
      { name: "entity", required: true, selector: sensorSelector },
      { name: "title", selector: { text: {} } },
      { name: "show_delivered", selector: { boolean: {} } },
      { name: "delivered_entity", selector: sensorSelector },
    ]);
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const st = hass.states[this._config.entity];
    const showDone = this._config.show_delivered !== false && this._config.delivered_entity;
    const dst = showDone ? hass.states[this._config.delivered_entity] : undefined;
    const sig = `${st ? st.last_updated : "none"}|${dst ? dst.last_updated : "none"}`;
    if (sig === this._sig && this._rendered) return; // avoid wiping input while typing
    this._sig = sig;
    this._render(st, dst);
  }

  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  _render(st, dst) {
    this._rendered = true;
    const keep = matches(this._config);
    const pkgs = (st?.attributes?.packages ?? []).filter(keep);
    const done = (dst?.attributes?.packages ?? []).filter(keep);
    const filtered = asList(this._config.statuses).length || asList(this._config.carriers).length;
    const note = setCardHidden(this, this._config.hide_empty && st && !pkgs.length && !done.length) ? HIDDEN_NOTE : "";
    const row = (p) => `
      <div class="pkg">
        <div class="head">
          ${p.url ? `<a href="${this._esc(p.url)}" target="_blank" rel="noopener">${this._esc(p.name)}</a>` : `<a>${this._esc(p.name)}</a>`}
          <span class="badge" style="background:${COLORS[p.status] || COLORS.unknown}">${LABELS[p.status] || p.status}</span>
          <button class="rm" data-uid="${this._esc(p.uid)}" title="Remove">✕</button>
        </div>
        <div class="sub">${this._esc(p.carrier_name)} · ${this._esc(p.number)}</div>
        ${p.last_description ? `<div class="last">${this._esc(p.last_description)}
          <span class="muted">${this._esc(p.last_time)} ${this._esc(p.last_location)}</span></div>` : ""}
        <details><summary>History</summary>
          ${(p.events || []).map((e) => `<div class="ev"><span class="muted">${this._esc(e.time)}</span> ${this._esc(e.description)} <span class="muted">${this._esc(e.location)}</span></div>`).join("") || '<div class="muted">No events yet</div>'}
        </details>
      </div>`;
    const rows = pkgs.map(row).join("");
    const doneRows = done.length ? `
      <details class="done" ${this._doneOpen ? "open" : ""}>
        <summary>Delivered · ${done.length}</summary>
        ${done.map(row).join("")}
      </details>` : "";

    this.shadowRoot.innerHTML = `
      <style>
        .pkg{padding:10px 0;border-bottom:1px solid var(--divider-color)}
        .head{display:flex;align-items:center;gap:8px}
        .head a{flex:1;font-weight:500;color:var(--primary-text-color);text-decoration:none}
        .badge{color:#fff;border-radius:10px;padding:2px 8px;font-size:12px}
        .rm{background:none;border:none;cursor:pointer;color:var(--secondary-text-color)}
        .sub,.muted{color:var(--secondary-text-color);font-size:12px}
        .last{margin-top:4px;font-size:14px}
        .ev{font-size:13px;padding:2px 0}
        summary{cursor:pointer;font-size:12px;color:var(--secondary-text-color);margin-top:4px}
        form{display:flex;gap:6px;margin-top:12px;flex-wrap:wrap}
        form select,form input,form button{padding:6px;border-radius:6px;border:1px solid var(--divider-color);background:var(--card-background-color);color:var(--primary-text-color)}
        form input{flex:1;min-width:120px}
        form button{background:var(--primary-color);color:var(--text-primary-color,#fff);cursor:pointer}
        .empty{padding:12px 0;color:var(--secondary-text-color)}
        details.done{margin-top:8px}
        details.done>summary{font-size:13px;padding:6px 0}
        details.done .pkg{opacity:.75}
        ${HIDDEN_NOTE_CSS}
      </style>
      <ha-card header="${this._esc(this._config.title)}">
        <div class="card-content">
          ${note}
          ${rows || (filtered ? '<div class="empty">No parcel matches the show conditions.</div>'
            : '<div class="empty">No parcels on the list. Add a tracking number below or to the to-do list.</div>')}
          ${doneRows}
          <form>
            <select name="carrier" title="Carrier">
              <option value="">Auto-detect</option>
              <option value="sagawa">Sagawa</option>
              <option value="yamato">Kuroneko</option>
              <option value="jp">Japan Post</option>
              <option value="ali">Cainiao</option>
            </select>
            <input name="number" placeholder="Tracking number" required>
            <input name="name" placeholder="Name (optional)">
            <button type="submit">Add</button>
          </form>
        </div>
      </ha-card>`;

    this.shadowRoot.querySelector("details.done")?.addEventListener("toggle", (ev) => { this._doneOpen = ev.target.open; });

    // Parcels live on the to-do list chosen at setup; the card just edits that list.
    const todo = st?.attributes?.todo_entity;
    this.shadowRoot.querySelector("form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const f = ev.target;
      if (!todo) return alert("Japan Tracker: to-do list not found");
      const prefix = f.carrier.value ? `${f.carrier.value}: ` : "";
      const item = `${prefix}${f.number.value.trim()} ${f.name.value.trim()}`.trim();
      this._hass.callService("todo", "add_item", { item }, { entity_id: todo })
        .then(() => f.reset()).catch((e) => alert(e.message || e));
    });
    this.shadowRoot.querySelectorAll(".rm").forEach((b) =>
      b.addEventListener("click", () => {
        if (todo && confirm("Remove this parcel from the list?"))
          this._hass.callService("todo", "remove_item", { item: b.dataset.uid }, { entity_id: todo });
      }));
  }
}
trackEditing(JapanTrackerCard);
customElements.define("japan-tracker-card", JapanTrackerCard);

/* ------------------------------------------------------------------------- *
 * Status card: visual progress per parcel.
 *   type: custom:japan-tracker-status-card
 *   entity: sensor.japan_tracker_packages   (default)
 *   title: Deliveries                        (default)
 *   show_number: true                        (show tracking numbers)
 *   layout: auto | full | compact
 *   statuses / carriers / hide_empty         (show conditions, see top of file)
 * ------------------------------------------------------------------------- */

const STEPS = [
  { key: "info_received", label: "Picked up" },
  { key: "in_transit", label: "In transit" },
  { key: "out_for_delivery", label: "Out for delivery" },
  { key: "delivered", label: "Delivered" },
];
// Where each status sits on the 4-step track (-1 = not on the track yet).
const STEP_INDEX = {
  pending: -1, info_received: 0, in_transit: 1, out_for_delivery: 2,
  failed_attempt: 2, delivered: 3, not_found: -1, error: -1, unknown: -1,
};
// Which parcels to show first.
const PRIORITY = {
  out_for_delivery: 0, failed_attempt: 1, in_transit: 2, info_received: 3,
  pending: 4, not_found: 5, unknown: 6, error: 7, delivered: 8,
};
const TONE = {
  delivered: "ok", out_for_delivery: "accent", in_transit: "accent", info_received: "accent",
  failed_attempt: "warn", error: "bad", not_found: "muted", pending: "muted", unknown: "muted",
};
const STATUS_MESSAGE = {
  not_found: "Not registered with the carrier yet",
  pending: "Waiting for first update",
  error: "Couldn't reach the carrier",
  unknown: "Status unknown",
};
const CARRIER_SHORT = { sagawa: "Sagawa", kuroneko: "Yamato", japanpost: "Japan Post", cainiao: "Cainiao" };
const STATE_LABEL = {
  info_received: "Picked up", in_transit: "In transit", out_for_delivery: "Out for delivery",
  failed_attempt: "Missed delivery", delivered: "Delivered", pending: "Pending",
  not_found: "Not found", error: "Error", unknown: "Unknown",
};
// Above this many parcels, layout "auto" switches to one line per parcel.
const COMPACT_ABOVE = 4;

// "2026-11-13 22:58:23" / "2026/11/14 13:40" -> "11/13 22:58"; other formats unchanged.
const shortTime = (t) => {
  const m = String(t || "").match(/^\d{4}[-/](\d{2})[-/](\d{2})(?:[ T](\d{2}:\d{2}))?/);
  return m ? `${m[1]}/${m[2]}${m[3] ? " " + m[3] : ""}` : String(t || "");
};

const ICON = {
  check: '<path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>',
  warn: '<path d="M12 7v6" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/><circle cx="12" cy="17" r="1.5" fill="currentColor"/>',
  truck: '<path d="M3 7h10v8H3zM13 10h4l3 3v2h-7z" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"/><circle cx="7" cy="17" r="1.8" fill="currentColor"/><circle cx="17" cy="17" r="1.8" fill="currentColor"/>',
  dot: '<circle cx="12" cy="12" r="4" fill="currentColor"/>',
};
const svg = (name, size = 14) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true">${ICON[name]}</svg>`;

class JapanTrackerStatusCard extends HTMLElement {
  setConfig(config) {
    // layout: "auto" (compact above COMPACT_ABOVE parcels) | "full" | "compact"
    this._config = { entity: "sensor.japan_tracker_packages", title: "Deliveries", show_number: true, layout: "auto", ...config };
    this._open = this._open || new Set(); // parcels whose history is expanded
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._sig = undefined;
    if (this._hass) this.hass = this._hass;
  }
  static getStubConfig() { return { entity: "sensor.japan_tracker_packages" }; }
  static getConfigForm() {
    return configForm([
      { name: "entity", required: true, selector: sensorSelector },
      { name: "title", selector: { text: {} } },
      { name: "layout", selector: { select: { mode: "dropdown", options: [
        { value: "auto", label: "Auto" }, { value: "full", label: "Full" }, { value: "compact", label: "Compact" }] } } },
      { name: "show_number", selector: { boolean: {} } },
    ]);
  }
  getCardSize() {
    const n = this._hass?.states[this._config.entity]?.attributes?.packages?.length || 0;
    return 2 + n * 2;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const st = hass.states[this._config.entity];
    const sig = st ? `${st.last_updated}|${st.state}` : "missing";
    if (sig === this._sig) return; // only re-render when the data changed
    this._sig = sig;
    this._render(st);
  }

  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  _eta(p) {
    if (p.scheduled_delivery) return p.scheduled_delivery; // Yamato
    if (Array.isArray(p.delivery_dates) && p.delivery_dates.length) return p.delivery_dates.join(" "); // Sagawa
    const m = (p.events || []).map((e) => e.description).join(" ").match(/配達予定日[：:]\s*([^\s]+(?:\s*午[前後])?)/);
    return m ? m[1] : ""; // Japan Post "配達予定日：11月15日 午前"
  }

  _track(p) {
    const idx = STEP_INDEX[p.status] ?? -1;
    const warn = p.status === "failed_attempt";
    const done = p.status === "delivered";
    const cells = STEPS.map((s, i) => {
      let cls = "todo", icon = "";
      if (i < idx || (done && i === idx)) { cls = "done"; icon = svg("check", 12); }
      else if (i === idx) { cls = warn ? "current warn" : "current"; icon = warn ? svg("warn", 13) : i === 2 ? svg("truck", 14) : svg("dot", 12); }
      const label = warn && i === idx ? "Missed delivery" : s.label;
      return `<li class="step ${cls}"><span class="node">${icon}</span><span class="lbl">${label}</span></li>`;
    }).join("");
    const pct = idx < 0 ? 0 : (Math.min(idx, 3) / 3) * 100;
    const where = idx < 0 ? "not started" : warn ? "delivery attempt failed" : STEPS[idx].label.toLowerCase();
    return `<ol class="track" style="--fill:${pct.toFixed(2)}" aria-label="Progress: ${where}">${cells}</ol>`;
  }

  _carrier(p) { return CARRIER_SHORT[p.carrier] || p.carrier_name || "Detecting…"; }

  _meta(p) {
    const eta = this._eta(p);
    // Cainiao stage + %, only once the parcel is actually moving (not "0%").
    const progress = p.carrier === "cainiao" && p.stage && Number(p.progress) > 0 && p.status !== "delivered"
      ? `<span class="meta-pill">${this._esc(p.stage)} ${Number(p.progress)}%</span>` : "";
    return eta || progress
      ? `<div class="meta">${eta ? `<span class="meta-pill eta">ETA ${this._esc(eta)}</span>` : ""}${progress}</div>` : "";
  }

  _timeline(p) {
    return `<ol class="timeline">${(p.events || []).map((e, i) => `
      <li class="${i === 0 ? "latest" : ""}"><span class="t">${this._esc(shortTime(e.time))}</span>
        <span class="d">${this._esc(e.description)}</span>
        ${e.location ? `<span class="l">${this._esc(e.location)}</span>` : ""}</li>`).join("")}
    </ol>`;
  }

  /* One line per parcel; tap to expand the tracker and history. */
  _compactRow(p) {
    const tone = TONE[p.status] || "muted";
    const idx = STEP_INDEX[p.status] ?? -1;
    const onTrack = idx >= 0;
    const warn = p.status === "failed_attempt";
    const sub = onTrack
      ? [p.last_description, shortTime(p.last_time)].filter(Boolean).map((x) => this._esc(x)).join(" · ")
      : this._esc(p.status_text || STATUS_MESSAGE[p.status] || "");
    const segs = STEPS.map((_, i) =>
      `<i class="${i < idx || (i === idx && p.status === "delivered") ? "on" : i === idx ? (warn ? "on warn" : "on cur") : ""}"></i>`).join("");
    // Plain text: the whole line toggles the row; the carrier link lives inside.
    const name = this._esc(p.name);
    const link = p.url
      ? `<a class="clink" href="${this._esc(p.url)}" target="_blank" rel="noopener">Open on ${this._esc(this._carrier(p))} site ↗</a>` : "";
    return `
      <details class="crow tone-${tone}" data-n="${this._esc(p.number)}" ${this._open.has(p.number) ? "open" : ""}>
        <summary>
          <span class="cmain">
            <span class="cname">${name}</span>
            <span class="csub">${sub}</span>
          </span>
          <span class="cside">
            <span class="cstate">${STATE_LABEL[p.status] || this._esc(p.status)}</span>
            <span class="mini" aria-hidden="true">${segs}</span>
            <span class="ccarrier">${this._esc(this._carrier(p))}</span>
          </span>
        </summary>
        <div class="cbody">
          ${onTrack ? this._track(p) : ""}
          ${p.last_location ? `<div class="when">${this._esc(p.last_location)}</div>` : ""}
          ${this._meta(p)}
          ${(p.events || []).length ? this._timeline(p) : ""}
          ${link}
        </div>
      </details>`;
  }

  _row(p) {
    const tone = TONE[p.status] || "muted";
    const onTrack = (STEP_INDEX[p.status] ?? -1) >= 0;
    const lastBits = [shortTime(p.last_time), p.last_location].filter(Boolean).map((x) => this._esc(x)).join(" · ");
    const carrier = this._carrier(p);
    const events = p.events || [];
    const history = events.length
      ? `<details data-n="${this._esc(p.number)}" ${this._open.has(p.number) ? "open" : ""}>
           <summary>History · ${events.length}</summary>${this._timeline(p)}</details>`
      : "";

    return `
      <article class="parcel tone-${tone}">
        <header>
          <div class="title">
            ${p.url ? `<a href="${this._esc(p.url)}" target="_blank" rel="noopener">${this._esc(p.name)}</a>` : `<span>${this._esc(p.name)}</span>`}
            ${this._config.show_number && p.name !== p.number ? `<span class="num">${this._esc(p.number)}</span>` : ""}
          </div>
          <span class="carrier">${this._esc(carrier)}</span>
        </header>
        ${onTrack ? this._track(p) : `<div class="notice">${this._esc(p.status_text) || STATUS_MESSAGE[p.status] || this._esc(p.status)}</div>`}
        ${p.last_description || lastBits ? `<div class="last">
          ${p.last_description ? `<span class="what">${this._esc(p.last_description)}</span>` : ""}
          ${lastBits ? `<span class="when">${lastBits}</span>` : ""}
        </div>` : ""}
        ${this._meta(p)}
        ${history}
      </article>`;
  }

  _chips(pkgs) {
    const order = ["out_for_delivery", "failed_attempt", "in_transit", "info_received", "pending", "not_found", "error"];
    const names = {
      out_for_delivery: "out for delivery", failed_attempt: "missed", in_transit: "in transit",
      info_received: "picked up", pending: "pending", not_found: "not found", error: "error",
    };
    const counts = {};
    pkgs.forEach((p) => { counts[p.status] = (counts[p.status] || 0) + 1; });
    return order.filter((s) => counts[s])
      .map((s) => `<span class="chip tone-${TONE[s]}">${counts[s]} ${names[s]}</span>`).join("");
  }

  _render(st) {
    const root = this.shadowRoot;
    if (!st) {
      setCardHidden(this, false);
      root.innerHTML = `<ha-card><div class="pad warn-msg">Entity ${this._esc(this._config.entity)} not found.</div></ha-card>`;
      return;
    }
    const pkgs = (st.attributes.packages || []).filter(matches(this._config)).sort(
      (a, b) => (PRIORITY[a.status] ?? 9) - (PRIORITY[b.status] ?? 9) || String(a.name).localeCompare(String(b.name)));
    const layout = this._config.layout;
    const compact = layout === "compact" || (layout === "auto" && pkgs.length > COMPACT_ABOVE);
    const note = setCardHidden(this, this._config.hide_empty && !pkgs.length) ? HIDDEN_NOTE : "";
    const filtered = asList(this._config.statuses).length || asList(this._config.carriers).length;

    root.innerHTML = `
      <style>${JapanTrackerStatusCard.styles}</style>
      <ha-card>
        <div class="head">
          <h2>${this._esc(this._config.title)}</h2>
          <div class="chips">${this._chips(pkgs)}</div>
        </div>
        <div class="list ${compact ? "compact" : ""}">
          ${note}
          ${pkgs.map((p) => (compact ? this._compactRow(p) : this._row(p))).join("")
            || `<div class="empty">${filtered ? "No parcel matches the show conditions." : "No parcels on the way."}</div>`}
        </div>
      </ha-card>`;

    root.querySelectorAll("details[data-n]").forEach((d) =>
      d.addEventListener("toggle", () => (d.open ? this._open.add(d.dataset.n) : this._open.delete(d.dataset.n))));
  }
}

JapanTrackerStatusCard.styles = `
  :host {
    --jt-accent: var(--primary-color, #03a9f4);
    --jt-ok: var(--success-color, #43a047);
    --jt-warn: var(--warning-color, #ffa600);
    --jt-bad: var(--error-color, #db4437);
    --jt-muted: var(--secondary-text-color, #727272);
    --jt-line: var(--divider-color, rgba(127,127,127,.25));
    --jt-text: var(--primary-text-color, #212121);
    --jt-bg: var(--card-background-color, var(--ha-card-background, #fff));
  }
  ha-card { overflow: hidden; }
  .head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 16px 16px 4px; }
  h2 { margin: 0; font-size: 1.15rem; font-weight: 500; color: var(--jt-text); flex: 1 1 auto; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; line-height: 1; padding: 5px 9px; border-radius: 999px; white-space: nowrap;
          color: var(--tone); background: color-mix(in srgb, var(--tone) 14%, transparent); }
  .list { padding: 4px 16px 12px; }
  .empty, .pad { padding: 16px; color: var(--jt-muted); }
  .warn-msg { color: var(--jt-bad); }

  .tone-ok { --tone: var(--jt-ok); } .tone-accent { --tone: var(--jt-accent); }
  .tone-warn { --tone: var(--jt-warn); } .tone-bad { --tone: var(--jt-bad); } .tone-muted { --tone: var(--jt-muted); }

  .parcel { padding: 14px 0; border-top: 1px solid var(--jt-line); }
  .parcel:first-child { border-top: 0; }
  .parcel header { display: flex; align-items: flex-start; gap: 8px; }
  .title { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .title a, .title span:first-child { color: var(--jt-text); text-decoration: none; font-weight: 500;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .title a:hover { text-decoration: underline; }
  .num { font-size: 12px; color: var(--jt-muted); font-variant-numeric: tabular-nums; }
  .carrier { font-size: 11px; padding: 3px 8px; border-radius: 6px; border: 1px solid var(--jt-line);
          color: var(--jt-muted); white-space: nowrap; }

  /* 4-step progress track */
  .track { list-style: none; margin: 14px 0 6px; padding: 0; display: grid;
           grid-template-columns: repeat(4, 1fr); position: relative; }
  .track::before, .track::after { content: ""; position: absolute; top: 10px; height: 3px; border-radius: 3px;
           left: 12.5%; }
  .track::before { right: 12.5%; background: var(--jt-line); }
  /* line spans the node centres (12.5% .. 87.5%); --fill is 0..100 */
  .track::after { width: calc(75% * var(--fill) / 100); background: var(--tone); }
  .step { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; gap: 6px;
          text-align: center; min-width: 0; }
  .node { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; box-sizing: border-box;
          background: var(--jt-bg); border: 2px solid var(--jt-line); color: var(--jt-bg); }
  .step.done .node { background: var(--tone); border-color: var(--tone); }
  .step.current .node { background: var(--tone); border-color: var(--tone);
          box-shadow: 0 0 0 4px color-mix(in srgb, var(--tone) 22%, transparent); }
  .step.current.warn .node { background: var(--jt-warn); border-color: var(--jt-warn);
          box-shadow: 0 0 0 4px color-mix(in srgb, var(--jt-warn) 25%, transparent); }
  .lbl { font-size: 11px; line-height: 1.25; color: var(--jt-muted); padding: 0 2px; }
  .step.current .lbl { color: var(--jt-text); font-weight: 600; }
  .step.current.warn .lbl { color: var(--jt-warn); }
  @media (prefers-reduced-motion: no-preference) {
    .tone-accent .step.current .node { animation: jt-pulse 2.4s ease-in-out infinite; }
  }
  @keyframes jt-pulse { 50% { box-shadow: 0 0 0 7px color-mix(in srgb, var(--tone) 8%, transparent); } }

  .notice { margin: 12px 0 4px; padding: 8px 10px; border-radius: 8px; font-size: 13px;
            color: var(--tone); background: color-mix(in srgb, var(--tone) 10%, transparent); }
  .last { margin-top: 8px; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
  .what { color: var(--jt-text); }
  .when { color: var(--jt-muted); font-size: 12px; }
  .meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .meta-pill { font-size: 11px; padding: 3px 8px; border-radius: 6px; color: var(--jt-text);
               background: color-mix(in srgb, var(--jt-muted) 12%, transparent); }
  .meta-pill.eta { color: var(--jt-accent); background: color-mix(in srgb, var(--jt-accent) 12%, transparent); font-weight: 500; }

  details { margin-top: 8px; }
  summary { cursor: pointer; font-size: 12px; color: var(--jt-muted); width: fit-content; }
  summary:focus-visible { outline: 2px solid var(--jt-accent); outline-offset: 2px; border-radius: 4px; }
  .timeline { list-style: none; margin: 8px 0 0 5px; padding: 0 0 0 14px; border-left: 2px solid var(--jt-line); }
  .timeline li { position: relative; padding: 3px 0 7px; font-size: 12px; display: flex; flex-wrap: wrap; gap: 2px 8px; }
  .timeline li::before { content: ""; position: absolute; left: -20px; top: 7px; width: 8px; height: 8px;
          border-radius: 50%; background: var(--jt-line); border: 2px solid var(--jt-bg); }
  .timeline li.latest::before { background: var(--tone); }
  .timeline .t { color: var(--jt-muted); font-variant-numeric: tabular-nums; min-width: 92px; }
  .timeline .d { color: var(--jt-text); }
  .timeline .l { color: var(--jt-muted); flex-basis: 100%; padding-left: 100px; }
  @media (max-width: 420px) { .timeline .l { padding-left: 0; } .timeline .t { min-width: 0; } }

  /* Compact layout: one line per parcel */
  .list.compact { padding-top: 8px; }
  .crow { border-top: 1px solid var(--jt-line); margin: 0; }
  .crow:first-child { border-top: 0; }
  .crow > summary { list-style: none; display: flex; align-items: center; gap: 12px; padding: 10px 0;
                    cursor: pointer; width: auto; font-size: 14px; color: var(--jt-text); }
  .crow > summary::-webkit-details-marker { display: none; }
  .crow > summary:hover .cname { color: var(--tone); }
  .cmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .cname { font-weight: 500; font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .csub { font-size: 12px; color: var(--jt-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cside { flex: 0 0 auto; display: grid; grid-template-columns: auto auto; grid-template-rows: auto auto;
           align-items: center; justify-items: end; gap: 4px 10px; }
  .cstate { grid-column: 1; font-size: 12px; font-weight: 600; color: var(--tone); white-space: nowrap; }
  .ccarrier { grid-column: 2; grid-row: 1 / span 2; font-size: 11px; padding: 3px 8px; border-radius: 6px;
              border: 1px solid var(--jt-line); color: var(--jt-muted); white-space: nowrap; min-width: 52px; text-align: center; }
  .mini { grid-column: 1; display: grid; grid-template-columns: repeat(4, 12px); gap: 3px; }
  .mini i { height: 4px; border-radius: 2px; background: var(--jt-line); }
  .mini i.on { background: var(--tone); }
  .mini i.warn { background: var(--jt-warn); }
  .cbody { padding: 0 0 12px; }
  .cbody .track { margin-top: 4px; }
  .cbody .when { font-size: 12px; color: var(--jt-muted); }
  .clink { display: inline-block; margin-top: 6px; font-size: 12px; color: var(--jt-accent); text-decoration: none; }
  .clink:hover { text-decoration: underline; }
  .clink:focus-visible, .crow > summary:focus-visible { outline: 2px solid var(--jt-accent); outline-offset: 2px; border-radius: 4px; }
  @media (max-width: 380px) { .ccarrier { display: none; } }
  ${HIDDEN_NOTE_CSS}
`;

trackEditing(JapanTrackerStatusCard);
customElements.define("japan-tracker-status-card", JapanTrackerStatusCard);

window.customCards = window.customCards || [];
window.customCards.push(
  { type: "japan-tracker-card", name: "Japan Parcel Tracker", description: "Parcel list with add/remove" },
  { type: "japan-tracker-status-card", name: "Japan Parcel Status", description: "Visual delivery progress for each parcel", preview: true },
);
