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

// The row only has what tells entries apart; everything else is in the
// details below it. "wide" columns move into the details on a narrow screen.
const COLUMNS = [
  ["ts", "Time"],
  ["type", "Type"],
  ["username", "User"],
  ["ip", "IP address", "wide"],
  ["summary", "Reason / endpoint", "wide"],
];

// [key, label, only in the details on a narrow screen]
const DETAILS = [
  ["ip", "IP address", true],
  ["reason", "Reason", true],
  ["endpoint", "Endpoint", true],
  ["hostname", "Hostname"],
  ["user_id", "User ID"],
  ["client_name", "Client"],
  ["client_id", "Client ID"],
  ["token_type", "Token type"],
  ["grant_type", "Grant type"],
  ["auth_provider", "Method"],
  ["user_agent", "User agent"],
  ["ingress", "Ingress"],
  ["host", "Host"],
  ["x_forwarded_for_untrusted", "X-Forwarded-For (untrusted)"],
  ["flow_id", "Flow ID"],
];

const MONO = ["user_id", "flow_id", "ip", "endpoint", "host", "x_forwarded_for_untrusted", "client_id"];

const INGRESS = {
  nabu_casa: "Nabu Casa",
  direct: "direct",
};

// X-Forwarded-For is whatever the client wrote into the header.
const UNTRUSTED_HINT = "Client-supplied header - can be forged. The IP address is what Home Assistant resolved.";

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
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th {
    text-align: left; font-weight: 500; color: var(--secondary-text-color);
    padding: 10px 12px; border-bottom: 1px solid var(--divider-color, #e0e0e0); white-space: nowrap;
  }
  td { padding: 9px 12px; border-bottom: 1px solid var(--divider-color, #eee); vertical-align: top; }
  tr.entry.open td, tbody tr:last-child td, tbody tr.entry:nth-last-child(2) td { border-bottom: none; }
  tr.entry { cursor: pointer; }
  tr.entry:hover td, tr.entry.open td, tr.details td { background: var(--secondary-background-color, #f5f5f5); }
  .mono { font-family: var(--code-font-family, monospace); font-size: 12px; }
  td.nowrap { white-space: nowrap; }
  td.user { white-space: nowrap; max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
  /* Takes the width that is left; its text is cut there, so the table never
     gets wider than the card. */
  td.gist { position: relative; width: 100%; }
  td.gist > div {
    position: absolute; left: 12px; right: 12px; top: 9px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  td.gist .mono:not(:first-child) { margin-left: 8px; }
  td.gist .mono { color: var(--secondary-text-color); }
  th.toggle, td.toggle { width: 28px; padding-right: 0; }
  button.expand {
    background: transparent; color: var(--secondary-text-color); border: none;
    min-height: 0; padding: 2px; width: 24px; height: 24px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
  }
  button.expand:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 1px; }
  .chevron {
    width: 7px; height: 7px; border: solid currentColor; border-width: 0 2px 2px 0;
    transform: rotate(-45deg); transition: transform 0.15s;
  }
  button.expand[aria-expanded="true"] .chevron { transform: rotate(45deg); }
  tr.details td { padding: 0 12px 14px 52px; }
  dl {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 10px 24px; margin: 0;
  }
  dl > div { min-width: 0; }
  dt { font-size: 12px; color: var(--secondary-text-color); }
  dd { margin: 2px 0 0; overflow-wrap: anywhere; }
  .hint { display: block; font-size: 12px; color: var(--secondary-text-color); }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
  dl > div.narrow { display: none; }
  @media (max-width: 600px) {
    .content { padding: 8px; }
    .wide { display: none; }
    dl > div.narrow { display: block; }
    th, td { padding-left: 6px; padding-right: 6px; }
    td.nowrap.time { white-space: normal; }
    td.user { white-space: normal; max-width: none; overflow-wrap: anywhere; }
    td.user .count { display: block; margin-left: 0; }
    tr.details td { padding: 0 8px 12px 34px; }
    dl { grid-template-columns: 1fr; }
  }
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
    // Expanded entries, kept across a refresh or a change of filter.
    this._open = new Set();
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

  _key(entry) {
    return [entry.ts, entry.type, entry.ip, entry.endpoint, entry.reason, entry.flow_id].join("|");
  }

  _username(entry) {
    const shown = text(entry.username);
    if (shown === null) return [el("span", { class: "muted", text: "—" })];
    // Stored only with the option store_unknown_usernames: typed by whoever
    // was at the login form, and no user has it.
    if (entry.known_user === false) return [shown, el("span", { class: "count", text: "no such user" })];
    return [shown];
  }

  _cell(key, entry) {
    if (key === "ts") return el("td", { class: "nowrap time", text: this._formatTime(entry.ts) });
    if (key === "type") {
      const type = TYPES[entry.type] || { label: entry.type, color: "var(--secondary-text-color)" };
      const badge = el("span", { class: "badge", text: type.label });
      badge.style.background = type.color;
      const cell = el("td", { class: "nowrap" }, [badge]);
      if (entry.count > 1) cell.append(el("span", { class: "count", text: `×${entry.count}` }));
      return cell;
    }
    if (key === "username") {
      const cell = el("td", { class: "user" }, this._username(entry));
      cell.title = cell.textContent;
      return cell;
    }
    if (key === "ip") {
      const shown = text(entry.ip);
      if (shown === null) return el("td", { class: "wide" }, [el("span", { class: "muted", text: "—" })]);
      const cell = el("td", { class: "wide nowrap mono", text: shown });
      if (entry.hostname) cell.title = `${shown} (${entry.hostname})`;
      return cell;
    }
    // key === "summary": the reason and the endpoint, cut to the space left.
    const reason = text(entry.reason);
    const endpoint = text(entry.endpoint);
    const clip = el("div");
    if (reason === null && endpoint === null) clip.append(el("span", { class: "muted", text: "—" }));
    if (reason !== null) clip.append(el("span", { text: reason }));
    if (endpoint !== null) clip.append(el("span", { class: "mono", text: endpoint }));
    const cell = el("td", { class: "wide gist" }, [clip]);
    cell.title = [reason, endpoint].filter((part) => part !== null).join(" ");
    return cell;
  }

  _detail(key, entry) {
    // Empty fields are left out.
    const shown = text(entry[key]);
    if (shown === null) return null;
    if (MONO.includes(key) && key !== "x_forwarded_for_untrusted") return [el("span", { class: "mono", text: shown })];
    if (key === "token_type") return [TOKEN_TYPES[shown] || shown];
    if (key === "ingress") return [INGRESS[shown] || shown];
    if (key === "x_forwarded_for_untrusted") {
      return [el("span", { class: "mono", text: shown }), el("span", { class: "hint", text: UNTRUSTED_HINT })];
    }
    return [shown];
  }

  _details(entry) {
    const fields = [];
    for (const [key, label, narrowOnly] of DETAILS) {
      const value = this._detail(key, entry);
      if (value === null) continue;
      fields.push(
        el("div", { class: narrowOnly ? "narrow" : "" }, [
          el("dt", { text: label }),
          el("dd", {}, value),
        ])
      );
    }
    return fields;
  }

  _row(entry, index) {
    const key = this._key(entry);
    const id = `details-${index}`;
    const open = this._open.has(key);
    const button = el("button", {
      class: "expand",
      type: "button",
      "aria-expanded": String(open),
      "aria-controls": id,
      "aria-label": "Details",
    }, [el("span", { class: "chevron" })]);
    const row = el("tr", { class: open ? "entry open" : "entry" }, [
      el("td", { class: "toggle" }, [button]),
      ...COLUMNS.map(([column]) => this._cell(column, entry)),
    ]);
    const details = el("tr", { class: "details", id }, [
      el("td", { colspan: String(COLUMNS.length + 1) }, [el("dl", {}, this._details(entry))]),
    ]);
    details.hidden = !open;
    // The whole row toggles; the button is what a keyboard or screen reader
    // uses, and its click lands here too.
    row.addEventListener("click", () => {
      const nowOpen = details.hidden;
      details.hidden = !nowOpen;
      button.setAttribute("aria-expanded", String(nowOpen));
      row.classList.toggle("open", nowOpen);
      if (nowOpen) this._open.add(key);
      else this._open.delete(key);
    });
    return [row, details];
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
    const head = el("tr", {}, [
      el("th", { class: "toggle" }, [el("span", { class: "sr-only", text: "Details" })]),
      ...COLUMNS.map(([, label, wide]) => el("th", { class: wide ? "wide" : "", text: label })),
    ]);
    const rows = entries.flatMap((entry, index) => this._row(entry, index));
    this._body.replaceChildren(el("table", {}, [el("thead", {}, [head]), el("tbody", {}, rows)]));
  }
}

// A tab that was open during an upgrade loads the module again under the new
// ?v= URL; the name is already taken there, and the tab keeps the old class
// until it is reloaded.
if (!customElements.get("login-detail-sign-in-log")) {
  customElements.define("login-detail-sign-in-log", LoginDetailSignInLog);
}
