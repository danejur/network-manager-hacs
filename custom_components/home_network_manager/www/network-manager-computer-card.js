/**
 * Home Network Manager: one computer at a glance (state, CPU, memory, root disk, temperatures, uptime, alerts).
 *
 *   type: custom:network-manager-computer-card
 *   device: <device id, or the computer's name>
 *   name: <optional title>
 *
 * Served and loaded by the integration itself. Entities are looked up by data point key through the
 * `home_network_manager/devices` command, so renaming an entity doesn't break the card.
 */

const DOMAIN = "home_network_manager";
const CARD = "network-manager-computer-card";
const EDITOR = `${CARD}-editor`;
const REFRESH_MS = 5 * 60 * 1000;

const MODEL_ICONS = {
  Desktop: "mdi:monitor",
  Laptop: "mdi:laptop",
  Handheld: "mdi:controller",
  Server: "mdi:server",
};

const STATES = {
  online: ["Online", "ok"],
  sleeping: ["Sleeping", "info"],
  offline: ["Offline", "off"],
  pending_enrollment: ["Not enrolled", "off"],
  revoked: ["Revoked", "err"],
};

// One request per Home Assistant connection, refreshed now and then for entities that appear later (a second GPU).
const deviceCache = new WeakMap();

function loadDevices(hass, force = false) {
  const cached = deviceCache.get(hass.connection);
  if (cached && !force && Date.now() - cached.at < REFRESH_MS) {
    return cached.promise;
  }
  const promise = hass.callWS({ type: `${DOMAIN}/devices` }).then((r) => r.devices);
  deviceCache.set(hass.connection, { at: Date.now(), promise });
  promise.catch(() => deviceCache.delete(hass.connection));
  return promise;
}

function findDevice(devices, wanted) {
  if (!wanted) return devices[0];
  const lower = String(wanted).toLowerCase();
  return devices.find((d) => d.id === wanted) || devices.find((d) => String(d.name).toLowerCase() === lower);
}

const escape = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function relativeDays(date) {
  const minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

function clock(date) {
  const sameDay = date.toDateString() === new Date().toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${date.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

function fillFor(percent) {
  if (percent >= 90) return "var(--nm-err)";
  if (percent >= 75) return "var(--nm-warn)";
  return "var(--primary-color)";
}

const STYLE = `
  :host {
    --nm-ok: var(--success-color, #43a047);
    --nm-info: var(--info-color, #039be5);
    --nm-warn: var(--warning-color, #ff9800);
    --nm-err: var(--error-color, #db4437);
    --nm-off: var(--disabled-text-color, #9e9e9e);
  }
  ha-card { overflow: hidden; }
  .header { display: flex; align-items: center; gap: 12px; padding: 16px 16px 8px; cursor: pointer; }
  .badge { width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; flex: none;
    color: var(--tone); background: color-mix(in srgb, var(--tone) 18%, transparent); }
  .title { flex: 1; min-width: 0; }
  .name { font-size: 18px; font-weight: 500; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub { color: var(--secondary-text-color); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 10px; border-radius: 999px; font-size: 12px;
    font-weight: 500; white-space: nowrap; color: var(--tone); background: color-mix(in srgb, var(--tone) 16%, transparent); }
  .chip ha-icon { --mdc-icon-size: 16px; }
  button.chip { border: 0; font: inherit; font-size: 12px; font-weight: 500; cursor: pointer; }
  .tone-ok { --tone: var(--nm-ok); } .tone-info { --tone: var(--nm-info); } .tone-off { --tone: var(--nm-off); }
  .tone-warn { --tone: var(--nm-warn); } .tone-err { --tone: var(--nm-err); }
  .content { padding: 8px 16px 16px; }
  .bar-row { display: grid; grid-template-columns: 20px 92px 1fr 44px; align-items: center; gap: 8px; margin: 8px 0;
    color: var(--secondary-text-color); cursor: pointer; }
  .bar-row ha-icon { --mdc-icon-size: 18px; }
  .bar-row .label { color: var(--primary-text-color); }
  .bar-row .value { text-align: right; color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
  .bar { height: 6px; border-radius: 3px; background: var(--secondary-background-color, rgba(127,127,127,.15)); overflow: hidden; }
  .bar > i { display: block; height: 100%; border-radius: 3px; }
  .facts { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; color: var(--secondary-text-color); font-size: 13px; }
  .facts span { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
  .facts ha-icon { --mdc-icon-size: 16px; }
  .facts b { color: var(--primary-text-color); font-weight: 500; }
  .footer { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 16px 14px; border-top: 1px solid var(--divider-color); }
  .message { padding: 16px; color: var(--secondary-text-color); }
`;

class NetworkManagerComputerCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement(EDITOR);
  }

  static async getStubConfig(hass) {
    try {
      const devices = await loadDevices(hass);
      return { device: devices[0]?.id ?? "" };
    } catch {
      return { device: "" };
    }
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = { ...config };
    this._device = undefined;
    this._rendered = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    // First time, and again now and then so data points that appear later (a second GPU) get their entities.
    if (!this._loading && (this._device === undefined || Date.now() - (this._resolvedAt ?? 0) > REFRESH_MS)) {
      this._resolve();
    }
    this._render();
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  async _resolve(force = false) {
    if (!this._hass) return;
    this._loading = true;
    try {
      let devices = await loadDevices(this._hass, force);
      let device = findDevice(devices, this._config?.device);
      if (!device && !force) {
        // Possibly added since the list was cached.
        devices = await loadDevices(this._hass, true);
        device = findDevice(devices, this._config?.device);
      }
      this._device = device ?? null;
      this._resolvedAt = Date.now();
      this._error = undefined;
    } catch (err) {
      this._device = null;
      this._resolvedAt = Date.now();
      this._error = err?.message ?? String(err);
    } finally {
      this._loading = false;
      this._rendered = undefined;
      this._render();
    }
  }

  /** The entity's state object for a data point, or undefined when it has no usable value right now. */
  _state(key) {
    const entityId = this._device?.entities?.[key];
    const state = entityId ? this._hass?.states?.[entityId] : undefined;
    if (!state || state.state === "unavailable" || state.state === "unknown") return undefined;
    return state;
  }

  _number(key) {
    const value = Number(this._state(key)?.state);
    return Number.isFinite(value) ? value : undefined;
  }

  _firstKey(predicate) {
    return Object.keys(this._device?.entities ?? {}).sort().find((key) => predicate(key) && this._state(key));
  }

  _render() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (event) => this._click(event));
    }
    const html = this._html();
    // Home Assistant sets `hass` on every state change anywhere; only repaint when this card's output changes.
    if (html === this._rendered) return;
    this._rendered = html;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${html}</ha-card>`;
  }

  _html() {
    if (!this._config || !this._hass) return "";
    if (this._device === undefined) return `<div class="message">Loading…</div>`;
    if (this._error) return `<div class="message">Home Network Manager isn't available: ${escape(this._error)}</div>`;
    if (!this._device) {
      return `<div class="message">No Home Network Manager computer matches "${escape(this._config.device ?? "")}".</div>`;
    }

    const device = this._device;
    const stateValue = this._state("state")?.state;
    const [stateLabel, tone] = STATES[stateValue] ?? ["Unavailable", "off"];
    const online = stateValue === "online";
    const os = this._state("operating_system")?.state;
    const addressKey = this._firstKey((k) => k.startsWith("interface_") && k.endsWith("_address"));
    const address = addressKey ? this._state(addressKey).state : undefined;
    const sub = [os, address].filter(Boolean).map(escape).join(" · ");

    const header = `
      <div class="header" data-entity="state">
        <div class="badge tone-${tone}"><ha-icon icon="${MODEL_ICONS[device.model] ?? "mdi:desktop-classic"}"></ha-icon></div>
        <div class="title"><div class="name">${escape(this._config.name || device.name)}</div>
          ${sub ? `<div class="sub">${sub}</div>` : ""}</div>
        <span class="chip tone-${tone}">${stateLabel}</span>
      </div>`;

    const rows = [];
    if (online) {
      rows.push(this._bar("cpu_usage", "mdi:cpu-64-bit", "CPU"));
      rows.push(this._bar("memory_usage", "mdi:memory", "Memory"));
    }
    const diskKey = this._state("disk_root_usage")
      ? "disk_root_usage"
      : this._firstKey((k) => k.startsWith("disk_") && k.endsWith("_usage"));
    if (diskKey) rows.push(this._bar(diskKey, "mdi:harddisk", "Disk"));

    const facts = [];
    if (online) {
      const cpuTemp = this._number("cpu_temperature");
      if (cpuTemp !== undefined) {
        facts.push(this._fact("cpu_temperature", "mdi:thermometer", "CPU", `${Math.round(cpuTemp)} °C`));
      }
      const gpu = this._number("gpu_0_usage");
      const gpuTemp = this._number("gpu_0_temperature");
      if (gpu !== undefined || gpuTemp !== undefined) {
        const parts = [gpu !== undefined ? `${Math.round(gpu)}%` : "", gpuTemp !== undefined ? `${Math.round(gpuTemp)} °C` : ""];
        facts.push(this._fact(gpu !== undefined ? "gpu_0_usage" : "gpu_0_temperature", "mdi:expansion-card", "GPU", parts.filter(Boolean).join(" · ")));
      }
      const boot = this._state("last_boot");
      if (boot) facts.push(this._fact("last_boot", "mdi:power", "Up for", relativeDays(new Date(boot.state))));
    } else if (stateValue === "sleeping") {
      const since = this._state("sleeping_since");
      facts.push(this._fact("sleeping_since", "mdi:power-sleep", "Sleeping", since ? `since ${clock(new Date(since.state))}` : ""));
    } else if (stateValue) {
      facts.push(`<span data-entity="state"><ha-icon icon="mdi:power"></ha-icon>Live values hidden while ${stateLabel.toLowerCase()}</span>`);
    }

    const chips = [];
    const updates = this._number("updates");
    if (updates) {
      chips.push(`<button class="chip tone-info" data-entity="updates"><ha-icon icon="mdi:package-up"></ha-icon>${updates} update${updates === 1 ? "" : "s"}</button>`);
    }
    if (this._state("reboot_required")?.state === "on") {
      chips.push(`<button class="chip tone-warn" data-entity="reboot_required"><ha-icon icon="mdi:restart-alert"></ha-icon>Reboot required</button>`);
    }
    if (this._state("agent_update")?.state === "on") {
      chips.push(`<button class="chip tone-warn" data-entity="agent_update"><ha-icon icon="mdi:update"></ha-icon>Agent update</button>`);
    }

    return `${header}
      <div class="content">${rows.join("")}${facts.length ? `<div class="facts">${facts.join("")}</div>` : ""}</div>
      ${chips.length ? `<div class="footer">${chips.join("")}</div>` : ""}`;
  }

  _bar(key, icon, label) {
    const value = this._number(key);
    if (value === undefined) return "";
    const percent = Math.max(0, Math.min(100, Math.round(value)));
    return `<div class="bar-row" data-entity="${key}"><ha-icon icon="${icon}"></ha-icon><span class="label">${label}</span>
      <div class="bar"><i style="width:${percent}%;background:${fillFor(percent)}"></i></div><span class="value">${percent}%</span></div>`;
  }

  _fact(key, icon, label, value) {
    return `<span data-entity="${key}"><ha-icon icon="${icon}"></ha-icon>${label} <b>${escape(value)}</b></span>`;
  }

  /** A click on any part opens that data point's entity, like Home Assistant's own cards. */
  _click(event) {
    const target = event.composedPath().find((el) => el?.dataset?.entity);
    const entityId = target && this._device?.entities?.[target.dataset.entity];
    if (!entityId) return;
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }
}

class NetworkManagerComputerCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = [
        { name: "device", required: true, selector: { device: { integration: DOMAIN } } },
        { name: "name", selector: { text: {} } },
      ];
      this._form.computeLabel = (schema) => ({ device: "Computer", name: "Title (optional)" })[schema.name];
      this._form.addEventListener("value-changed", (event) => {
        const config = { ...this._config, ...event.detail.value };
        if (!config.name) delete config.name;
        this._config = config;
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
  }
}

if (!customElements.get(CARD)) {
  customElements.define(CARD, NetworkManagerComputerCard);
  customElements.define(EDITOR, NetworkManagerComputerCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD,
    name: "Network Manager computer",
    description: "One computer at a glance: state, CPU, memory, disk, temperatures and alerts.",
    preview: true,
  });
}
