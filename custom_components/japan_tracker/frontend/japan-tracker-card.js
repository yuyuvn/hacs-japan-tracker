/* Japan Parcel Tracker card. Config: type: custom:japan-tracker-card, entity: sensor.japan_tracker_packages */
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
    this._config = { entity: "sensor.japan_tracker_packages", title: "Parcels", ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }
  getCardSize() { return 4; }
  static getStubConfig() { return { entity: "sensor.japan_tracker_packages" }; }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const sig = st ? st.last_updated : "none";
    if (sig === this._sig && this._rendered) return; // avoid wiping input while typing
    this._sig = sig;
    this._render(st);
  }

  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  _render(st) {
    this._rendered = true;
    const pkgs = st?.attributes?.packages ?? [];
    const rows = pkgs.map((p) => `
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
      </div>`).join("");

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
      </style>
      <ha-card header="${this._esc(this._config.title)}">
        <div class="card-content">
          ${rows || '<div class="empty">No parcels on the list. Add a tracking number below or to the to-do list.</div>'}
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
customElements.define("japan-tracker-card", JapanTrackerCard);
window.customCards = window.customCards || [];
window.customCards.push({ type: "japan-tracker-card", name: "Japan Parcel Tracker", description: "Sagawa / Kuroneko / Cainiao parcels" });
