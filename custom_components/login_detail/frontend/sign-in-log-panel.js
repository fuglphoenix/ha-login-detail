// Login Detail - the admin-only "Sign-in log" panel.
//
// One plain custom element, no build step and no dependencies: it only uses
// what Home Assistant hands every panel (the `hass` object), the theme's CSS
// variables and <ha-menu-button>. All data comes from the websocket command
// login_detail/sign_in_log/list, which core refuses for non-admin users.
//
// The panel shows personal data (username, IP address, user agent). It never
// stores anything in the browser.

const COMMAND = "login_detail/sign_in_log/list";

const TYPES = {
  "auth.login_failed": { label: "Failed", color: "var(--error-color, #db4437)" },
  "auth.login_success": { label: "Success", color: "var(--success-color, #43a047)" },
  "auth.request_rejected": { label: "Rejected", color: "var(--warning-color, #ffa600)" },
};

const COLUMNS = [
  ["ts", "Time"],
  ["type", "Type"],
  ["reason", "Reason"],
  ["username", "Username"],
  ["user_id", "User ID"],
  ["client", "Client"],
  ["auth_provider", "Method"],
  ["ip", "IP address"],
  ["endpoint", "Endpoint"],
  ["user_agent", "User agent"],
  ["flow_id", "Flow ID"],
];

const TOKEN_TYPES = {
  normal: "access token",
  long_lived_access_token: "long-lived access token",
  system: "system token",
};

const STYLE = `
  :host {
    display: block;
    min-height: 100%;
    background: var(--primary-background-color);
    color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, var(--ha-font-family-body, Roboto, sans-serif));
  }
  .toolbar {
    display: flex;
    align-items: center;
    height: var(--header-height, 56px);
    padding: 0 12px;
    box-sizing: border-box;
    background: var(--app-header-background-color, var(--primary-color));
    color: var(--app-header-text-color, white);
    border-bottom: var(--app-header-border-bottom, none);
    font-size: 20px;
  }
  .toolbar .title { margin-left: 12px; flex: 1; }
  .content { padding: 16px; max-width: 1600px; margin: 0 auto; }
  .card {
    background: var(--ha-card-background, var(--card-background-color, white));
    border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color, #e0e0e0);
    box-shadow: var(--ha-card-box-shadow, none);
    overflow: hidden;
  }
  .intro { padding: 16px 16px 0; color: var(--secondary-text-color); font-size: 14px; }
  .filters {
    display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
    padding: 16px; border-bottom: 1px solid var(--divider-color, #e0e0e0);
  }
  label { display: flex; flex-direction: column; font-size: 12px; color: var(--secondary-text-color); gap: 4px; }
  input, select, button {
    font: inherit; font-size: 14px; color: var(--primary-text-color);
    background: var(--input-fill-color, var(--secondary-background-color, #f5f5f5));
    border: 1px solid var(--input-idle-line-color, var(--divider-color, #ccc));
    border-radius: 6px; padding: 7px 9px; min-height: 36px; box-sizing: border-box;
  }
  button {
    background: var(--primary-color); color: var(--text-primary-color, white);
    border: none; cursor: pointer; padding: 7px 16px;
  }
  button.secondary { background: transparent; color: var(--primary-color); }
  .summary { padding: 10px 16px; font-size: 13px; color: var(--secondary-text-color); display: flex; gap: 16px; flex-wrap: wrap; }
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th {
    text-align: left; font-weight: 500; color: var(--secondary-text-color);
    padding: 10px 12px; border-bottom: 1px solid var(--divider-color, #e0e0e0); white-space: nowrap;
  }
  td { padding: 9px 12px; border-bottom: 1px solid var(--divider-color, #eee); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  td.mono { font-family: var(--code-font-family, monospace); font-size: 12px; }
  td.ua { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  td.nowrap { white-space: nowrap; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px;
    font-weight: 500; color: white; white-space: nowrap;
  }
  .count { color: var(--secondary-text-color); font-size: 12px; margin-left: 6px; }
  .muted { color: var(--disabled-text-color, #9e9e9e); }
  .empty, .error { padding: 32px 16px; text-align: center; color: var(--secondary-text-color); }
  .error { color: var(--error-color, #db4437); }
`;

function text(value) {
  return value === null || value === undefined || value === "" ? null : String(value);
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

class LoginDetailSignInLog extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._filters = {};
    this._result = null;
    this._error = null;
    this._built = false;
    this._timer = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._menu) this._menu.hass = hass;
    if (first) {
      this._build();
      this._fetch();
    }
  }

  get hass() {
    return this._hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  set panel(panel) {
    this._panel = panel;
  }

  _build() {
    if (this._built) return;
    this._built = true;
    const root = this.shadowRoot;
    root.append(el("style", { text: STYLE }));

    this._menu = document.createElement("ha-menu-button");
    this._menu.hass = this._hass;
    this._menu.narrow = this._narrow;
    root.append(el("div", { class: "toolbar" }, [this._menu, el("div", { class: "title", text: "Sign-in log" })]));

    const type = el("select", { name: "log_type" }, [
      el("option", { value: "", text: "All" }),
      ...Object.entries(TYPES).map(([value, { label }]) => el("option", { value, text: label })),
    ]);
    const username = el("input", { name: "username", type: "search", placeholder: "Any user" });
    const ip = el("input", { name: "ip", type: "search", placeholder: "e.g. 192.0.2." });
    const from = el("input", { name: "start_time", type: "datetime-local" });
    const to = el("input", { name: "end_time", type: "datetime-local" });
    const refresh = el("button", { text: "Refresh" });
    const clear = el("button", { class: "secondary", text: "Clear" });

    for (const input of [type, username, ip, from, to]) {
      input.addEventListener(input.tagName === "SELECT" || input.type === "datetime-local" ? "change" : "input", () => {
        this._filters[input.name] = input.value;
        this._schedule();
      });
    }
    refresh.addEventListener("click", () => this._fetch());
    clear.addEventListener("click", () => {
      for (const input of [type, username, ip, from, to]) input.value = "";
      this._filters = {};
      this._fetch();
    });

    this._summary = el("div", { class: "summary" });
    this._body = el("div");
    root.append(
      el("div", { class: "content" }, [
        el("div", { class: "card" }, [
          el("div", {
            class: "intro",
            text:
              "Login attempts, logins and rejected requests, newest first. " +
              "Repeated rejections from the same IP address on the same endpoint, for the same reason and client, are folded into one entry. " +
              "A rejected request only shows a user and client when its token is proven to be theirs. " +
              "Visible to administrators only.",
          }),
          el("div", { class: "filters" }, [
            el("label", {}, ["Type", type]),
            el("label", {}, ["User", username]),
            el("label", {}, ["IP address", ip]),
            el("label", {}, ["From", from]),
            el("label", {}, ["To", to]),
            refresh,
            clear,
          ]),
          this._summary,
          this._body,
        ]),
      ])
    );
  }

  _schedule() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._fetch(), 300);
  }

  _request() {
    const message = { type: COMMAND };
    for (const [key, value] of Object.entries(this._filters)) {
      if (!value || !value.trim()) continue;
      // datetime-local is local time without a zone; send it as UTC.
      message[key] = key.endsWith("_time") ? new Date(value).toISOString() : value.trim();
    }
    return message;
  }

  async _fetch() {
    if (!this._hass) return;
    try {
      this._result = await this._hass.callWS(this._request());
      this._error = null;
    } catch (err) {
      this._result = null;
      this._error = (err && (err.message || err.code)) || String(err);
    }
    this._render();
  }

  _formatTime(ts) {
    if (!ts) return "";
    const date = new Date(ts);
    if (Number.isNaN(date.getTime())) return ts;
    return date.toLocaleString(this._hass.locale?.language || this._hass.language, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }

  _cell(key, entry) {
    // A rejected request's client is only known once its token is proven:
    // the long-lived token's name, or the client ID (e.g. https://claude.ai).
    const value = key === "client" ? entry.client_name || entry.client_id : entry[key];
    if (key === "ts") return el("td", { class: "nowrap", text: this._formatTime(value) });
    if (key === "type") {
      const type = TYPES[value] || { label: value, color: "var(--secondary-text-color)" };
      const badge = el("span", { class: "badge", text: type.label });
      badge.style.background = type.color;
      const cell = el("td", { class: "nowrap" }, [badge]);
      if (entry.count > 1) cell.append(el("span", { class: "count", text: `×${entry.count}` }));
      return cell;
    }
    const shown = text(value);
    const cls = ["user_id", "flow_id", "ip", "endpoint"].includes(key) ? "mono" : key === "user_agent" ? "ua" : "";
    const cell = el("td", { class: cls });
    if (shown === null) {
      cell.append(el("span", { class: "muted", text: "—" }));
    } else {
      cell.textContent = key === "user_id" || key === "flow_id" ? shown.slice(0, 8) : shown;
      cell.title = key === "ip" && entry.hostname ? `${shown} (${entry.hostname})` : shown;
      if (key === "client" && entry.token_type) {
        cell.title = `${shown} (${TOKEN_TYPES[entry.token_type] || entry.token_type})`;
      }
    }
    return cell;
  }

  _render() {
    this._summary.replaceChildren();
    if (this._error) {
      this._body.replaceChildren(el("div", { class: "error", text: `Could not load the sign-in log: ${this._error}` }));
      return;
    }
    const { entries, total, max_entries, max_days } = this._result;
    this._summary.append(
      el("span", { text: `${total} ${total === 1 ? "entry" : "entries"}` }),
      el("span", { text: `Kept for ${max_days} days, at most ${max_entries} entries` })
    );
    if (!entries.length) {
      this._body.replaceChildren(el("div", { class: "empty", text: "No entries match the filters." }));
      return;
    }
    const head = el("tr", {}, COLUMNS.map(([, label]) => el("th", { text: label })));
    const rows = entries.map((entry) => el("tr", {}, COLUMNS.map(([key]) => this._cell(key, entry))));
    this._body.replaceChildren(
      el("div", { class: "table-wrap" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)])])
    );
  }
}

// A tab that was open during an upgrade loads the module again under the new
// ?v= URL; the name is already taken there, and the tab keeps the old class
// until it is reloaded.
if (!customElements.get("login-detail-sign-in-log")) {
  customElements.define("login-detail-sign-in-log", LoginDetailSignInLog);
}
