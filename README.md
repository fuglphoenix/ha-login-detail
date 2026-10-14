# Login Detail

Home Assistant integration that tells you which username was used in a failed
login attempt - and when, and from which IP address.

[![CI](https://github.com/fuglphoenix/ha-login-detail/actions/workflows/ci.yml/badge.svg)](https://github.com/fuglphoenix/ha-login-detail/actions/workflows/ci.yml)
[![Validate](https://github.com/fuglphoenix/ha-login-detail/actions/workflows/validate.yml/badge.svg)](https://github.com/fuglphoenix/ha-login-detail/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

## Why it exists

When a login fails, Home Assistant logs the IP address and the URL - never the
username that was tried. A successful login is not logged at all. The first
part is a known, open issue in core:
[home-assistant/core#148110](https://github.com/home-assistant/core/issues/148110).

Login Detail fills that gap. It fires an event for every failed and every
successful login, with the attempted username and the client's IP address,
and keeps a short history in six sensors.

Passwords and TOTP codes are never read, logged or forwarded.

> [!WARNING]
> This integration **monkeypatches private Home Assistant core API**:
> `HassLoginFlow.async_step_init`, `LoginFlow.async_step_mfa`,
> `AuthManagerFlowManager.async_finish_flow` and `process_wrong_login`. Core can change these at any
> time. After a Home Assistant upgrade the integration may silently stop
> reporting - no error, nothing breaks, logins keep working, you just stop
> getting events. All patched code is wrapped so that a failure can never
> break a login. Do the
> [post-upgrade check](#after-a-home-assistant-upgrade) after every upgrade.

## Events

| Event | Fields |
|---|---|
| `login_detail_failed` | `username`, `user_id`, `ip_address`, `reason`, `flow_id`, `auth_provider`, `endpoint`, `user_agent` |
| `login_detail_succeeded` | `username`, `user_id`, `ip_address`, `flow_id`, `auth_provider`, `endpoint`, `user_agent` |
| `login_detail_request_rejected` | `ip_address`, `endpoint`, `reason`, `rejection`, `user_id`, `username`, `client_id`, `client_name`, `token_type`, `user_agent` |

`username` on a failed attempt is only filled in if it belongs to an existing
user; otherwise it is `null`, so a password typed into the username field is
never recorded. `user_id` is filled in whenever the user is known.
`user_agent` is the client's `User-Agent` header, cut to 256 characters.

Every event is also written as one line to the log - failed attempts at
`WARNING`, successful logins and rejected requests at `INFO` (core already
logs rejected requests at `WARNING`):

```text
Sign-in log: type=auth.login_failed reason=invalid_auth username=alice user_id=9f2c… auth_provider=homeassistant client_id=None token_type=None client_name=None ip=192.0.2.10 endpoint=/auth/login_flow user_agent='Mozilla/5.0 …' flow_id=01J…
Sign-in log: type=auth.request_rejected reason=expired_token username=alice user_id=9f2c… auth_provider=None client_id=https://mcp-client.example token_type=normal client_name=None ip=192.0.2.10 endpoint=/api/mcp user_agent='…' flow_id=None
```

`login_detail_request_rejected` covers requests that are rejected for invalid
authentication *outside* the login flow - typically a client with a wrong or
expired token on `/api/mcp`, `/api/websocket` or `/auth/token`. `reason` is
always `invalid_request_auth`; `rejection` says why (see
[Rejected requests](#rejected-requests)). `endpoint` is the route pattern (`/api/mcp`,
`/api/hassio_ingress/{token}/{path}`), never the literal path or query string,
which can carry secrets. Login flow attempts always report `/auth/login_flow`.
Rejections on ordinary views such as `/api/mcp` require IP banning to be
enabled - see [Known limitations](#known-limitations).
A request to `/auth/login_flow/{flow_id}` that core rejects (for example an
expired flow) is a rejected request, not a failed login.

The `reason` field of `login_detail_failed`:

| `reason` | Meaning |
|---|---|
| `invalid_auth` | Wrong username or password. |
| `invalid_mfa_code` | **The password was correct**; only the 2FA code was wrong. |
| `mfa_too_many_retry` | The password was correct; the flow was aborted after too many wrong 2FA codes. |
| `mfa_login_expired` | The password was correct; the 2FA step expired. |
| `mfa_pending` | The password was correct, and the 2FA code has not been submitted (yet). Replaced by `ok` in the history if the same flow completes. |
| `invalid_request_auth` | A request outside the login flow was rejected for invalid authentication (only on `login_detail_request_rejected` and in the history). |

`invalid_mfa_code` is the one that matters most: someone had the right
password and was stopped only by 2FA. `mfa_pending` that never turns into a
successful login means the same.

### Rejected requests

Core rejects every request with the same 401. Since 0.7.1, Login Detail
works out why from what the request carried, and puts it in `rejection` on
the event and in `reason` in the sign-in log and the log line:

| `rejection` | Meaning | User and client |
|---|---|---|
| `no_credentials` | No token at all - for example an MCP client's first call, which core answers with 401 and `WWW-Authenticate` so the client starts OAuth. | - |
| `expired_token` | A token that really was issued to this user, but has expired. | yes |
| `user_not_allowed` | A valid, unexpired token, but core does not let the user in: the user is set to local access only and connects from outside. | yes |
| `unknown_or_revoked_token` | A token for a refresh token that does not exist (any more) - revoked, logged out, or the user was deactivated - or a forged or garbled token. | - |
| `malformed_credentials` | An `Authorization` header that is not `Bearer <token>`, or a websocket auth message without a usable token. | - |
| `invalid_signed_path` | A signed path (`authSig`) that core did not accept. | - |
| `not_admin` | A valid user, turned away by the endpoint because they are not an administrator - for example the MCP server with "require admin" on. | yes |
| `permission_denied` | The same for an administrator (another permission check). | yes |
| `invalid_auth_request` | `/auth/token` or `/auth/login_flow` rejected the request, for example a refresh token that no longer exists. The request body is not read. | - |

**User and client are only filled in when it is proven.** For a rejected
token, the token's signature is checked against the key of the refresh token
it names - the same check core makes, except for the expiry. Only if it
matches are `user_id`, `username`, `client_id` (for example the OAuth client
ID of an MCP client), `token_type` (`normal`, `long_lived_access_token` or
`system`) and, for long-lived tokens, `client_name` (the name the user gave
the token) filled in. A forged, unsigned or unknown token gets `null` in all
of them, whatever it claims. For `not_admin` and `permission_denied`, core
has already verified the token. `username` comes from the user's stored
credentials, never from the request.

**The token itself is never stored, logged or shown** - not the token, not
its signature, not its payload. Only what is derived from it is kept. The
check is two JWT decodes in memory after core has answered: no I/O, no
network calls, and a failure only means the fields stay `null`.

Rejected requests stored before 0.7.1 show `invalid_request_auth` as their
reason.

## Entities

Six sensors are created automatically. No YAML, no helpers.

| Entity ID (new installation) | Entity ID (installed before 0.8.0) | Shows |
|---|---|---|
| `sensor.login_latest_event` | `sensor.login_seneste_haendelse` | Latest login event as `result · username · ip` (the endpoint instead of the username for a rejected request). Attributes: `time`, `result`, `username`, `ip`, `hostname`, `auth_provider`, `endpoint`, `count`. |
| `sensor.login_ok_24h` | `sensor.login_ok_24t` | Number of successful logins in the last 24 hours, with per-IP, per-username and per-endpoint counts. |
| `sensor.login_failed_24h` | `sensor.login_mislykkede_24t` | Number of failed login attempts and rejected requests in the last 24 hours, with per-IP, per-username and per-endpoint (`endpoints`) counts. |
| `sensor.login_ip_addresses_24h` | `sensor.login_ip_adresser_24t` | Number of unique IP addresses that tried to log in or were rejected in the last 24 hours, with hostname, counts, usernames and endpoints per IP. |
| `sensor.login_history` | `sensor.login_historik` | Number of stored events (up to the `max_entries` option, default 500); the latest 25 are in the attributes. |
| `sensor.login_active_sessions` | `sensor.login_aktive_sessioner` | Active refresh tokens per user, with the IP address and time they were last used. System tokens are not counted. |

**Which entity IDs you get.** Since 0.8.0 the entity names (for example
"Login failed 24h") and units are English. Home Assistant derives the entity
ID from the name only the first time it sees an entity; after that the entity
registry finds the entity by its unique ID, which has not changed. So:

- **A new installation** gets the English entity IDs in the first column.
- **An existing installation keeps its entity IDs** from the second column -
  whatever refers to the entity ID keeps working. The displayed name and unit
  change, and so do the attribute keys (below). If you have renamed an entity
  yourself, your name is kept.
- To switch an existing installation to the English IDs, rename them under
  **Settings → Entities** (entity ID field), and update whatever refers to
  them.

**Attribute keys.** Since 0.8.0 the attribute keys are English as well. This
is a breaking change: a template, automation or dashboard card that reads an
attribute, for example `state_attr('sensor.login_failed_24h', 'brugernavne')`,
must use the new key (`'usernames'`). Keys not listed (`ip`, `endpoint`,
`endpoints`, `count`, `ok`, `type`) are unchanged.

| Sensor | Before 0.8.0 | Since 0.8.0 |
|---|---|---|
| Latest event, and each entry of `events` | `tidspunkt` | `time` |
| | `resultat` | `result` |
| | `bruger` | `username` |
| | `vaert` | `hostname` |
| | `metode` | `auth_provider` |
| Ok 24h, failed 24h | `ip_fordeling` | `ip_addresses` |
| | `brugernavne` | `usernames` |
| | `seneste` | `latest` |
| IP addresses 24h | `adresser` | `addresses` |
| each entry of `addresses` | `vaert` | `hostname` |
| | `mislykkede` | `failed` |
| | `seneste` | `latest` |
| | `brugere` | `usernames` |
| History | `hændelser` | `events` |
| Active sessions | `sessioner` | `sessions` |
| | `ip_fordeling` | `ip_addresses` |
| each entry of `sessions` | `bruger` | `user` |
| | `sidst_brugt` | `last_used` |
| | `klient` | `client` |
| All tallies and `addresses` | key `ukendt` (no IP or username) | key `unknown` |

Each history entry gets a hostname from a reverse DNS lookup. The lookup runs
in a background task, so a slow or missing PTR record never delays a login.

Repeated rejections from the same IP address on the same endpoint within 10
minutes are folded into one history entry with a `count`, so a client retrying
with a stale token cannot push real login attempts out of the history. The
24-hour counters count every attempt.

The sensors only write a new state when the content actually changes, so no
`recorder` exclusion is needed.

## Requirements

- Home Assistant **2026.10.0** or newer. The integration has only been tested
  against Home Assistant **2026.10.1**.
- No external Python packages.

## Installation

### HACS (custom repository)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/fuglphoenix/ha-login-detail` with type
   **Integration**.
3. Find **Login Detail** in HACS and install it.

### Manual

Copy `custom_components/login_detail/` from this repository into
`config/custom_components/` on your Home Assistant installation.

### Then

1. Restart Home Assistant.
2. Go to **Settings → Devices & services → Add integration → Login Detail**.
   The dialog is a confirmation. Retention and the side panel are set later
   under **Configure** on the integration (see [Sign-in log](#sign-in-log)).

Do **not** add `login_detail:` to `configuration.yaml`. If an older setup has
the line, it is imported into a config entry automatically on the first
startup, after which the line can be removed.

## Sign-in log

An admin-only panel, **Sign-in log**, in the sidebar shows every recorded
event as one row, newest first: time, type, reason, username, user ID, login
method, client, IP address, endpoint, user agent and flow ID. Repeated
rejections from the same IP address on the same endpoint, for the same
reason, user and client, are one row with a count. The
table can be filtered by type, user, IP address (prefix, so `192.0.2.`
matches the range) and time span.

The panel is a proof of concept for a sign-in log in core
([architecture#1346](https://github.com/home-assistant/architecture/discussions/1346))
and is built the way core would build it: the panel only talks to the
websocket command `login_detail/sign_in_log/list`, which core refuses for
users who are not administrators. Non-admin users do not see the panel.

```json
{"id": 1, "type": "login_detail/sign_in_log/list",
 "log_type": "auth.login_failed", "ip": "192.0.2.", "limit": 100}
```

All filters are optional: `log_type` (`auth.login_failed`,
`auth.login_success`, `auth.request_rejected`), `reason` (exact, for example
`expired_token`), `username`, `user_id`, `ip`, `start_time`, `end_time`
(ISO 8601) and `limit`. The result is
`{"entries": [...], "total": n, "max_entries": n, "max_days": n}`.

Each entry has `ts`, `type`, `reason`, `username`, `user_id`,
`auth_provider`, `ip`, `hostname`, `endpoint`, `user_agent`, `flow_id`,
`count`, and since 0.7.1 `client_id`, `client_name` and `token_type`. The
last three are only set for a rejected request whose token was proven (see
[Rejected requests](#rejected-requests)); otherwise they are `null`.

**Options** (Settings → Devices & services → Login Detail → Configure):

| Option | Default | Range |
|---|---|---|
| Maximum number of entries | 500 | 50-5000 |
| Keep entries for (days) | 7 | 1-365 |
| Show the Sign-in log panel | on | |

An entry is deleted when either limit is reached. Expired entries are removed
hourly, also when nothing new is logged.

## Automation example

Send a notification for every failed login attempt:

```yaml
automation:
  - alias: "Notify on failed login"
    triggers:
      - trigger: event
        event_type: login_detail_failed
    actions:
      - action: notify.notify
        data:
          title: "Failed Home Assistant login"
          message: >-
            {{ trigger.event.data.username }} from
            {{ trigger.event.data.ip_address }}
            ({{ trigger.event.data.reason }})
```

Example notification:

```text
Failed Home Assistant login
alice from 203.0.113.7 (invalid_mfa_code)
```

## Privacy

This integration processes personal data. Be aware of what it stores and
where:

- **Usernames, user IDs, IP addresses and user agents are stored in plain
  text** - in the events, in the Home Assistant log, in
  `.storage/login_detail_history`, in the sensor attributes and therefore in
  the recorder database. The storage file is bounded by the retention options
  (default 7 days and 500 entries); the log, the recorder and backups follow
  their own retention.
- A rejected request names a user, an OAuth client ID and the name of a
  long-lived token only when the token's signature proves it. Tokens
  themselves are never stored.
- The username of a failed login is only stored if it belongs to an existing
  user, in the form Home Assistant stores it. Anything else - including a
  password typed into the username field - is recorded as `null`. (Before
  0.7.0 it was stored as typed.)
- The Sign-in log panel and its websocket command are for administrators
  only. Screenshots of the panel contain all of the above; anonymise them
  before sharing.
- Each IP address is looked up with **reverse DNS** using the DNS servers Home
  Assistant uses. That means a DNS query for the IP address of whoever tried
  to log in.
- Anyone who can see the entities, the log, automations triggered by the
  events, or a backup of Home Assistant can see this data. Notifications sent
  by automations carry it to wherever they are delivered.

If you run Home Assistant for other people, tell them, and consider how long
you keep the recorder history.

## After a Home Assistant upgrade

This is the one maintenance task the integration needs. Make sure
`custom_components.login_detail` logs at `info` (see
[Known limitations](#known-limitations)), restart, and check:

| What you see | What it means | What to do |
|---|---|---|
| `login_detail active - logins fire the events ...` is **missing** from the startup log | The monkeypatch could not be applied - core has moved the login code. | Check the log for a `login_detail` error. Open an issue with your Home Assistant version. |
| The line is there, but a deliberately wrong login with a made-up username (e.g. `does-not-exist`) gives **no** `Failed login: user=does-not-exist ...` warning | The patch is applied to methods core no longer calls. | Open an issue with your Home Assistant version. |
| The warning appears with the made-up username, and a real login raises `sensor.login_ok_24h` (`sensor.login_ok_24t` on an installation from before 0.8.0) by **exactly 1** | It works. | Nothing. |

For a thorough check, run the acceptance test in [`tests/`](tests/README.md).
It exercises the whole login path, including 2FA, against a real Home
Assistant.

## Removal

1. **Settings → Devices & services → Login Detail → Delete.** The monkeypatch
   is taken off immediately and the sensors disappear, without a restart.
2. Delete `config/custom_components/login_detail/` (or uninstall it in HACS).
3. Restart Home Assistant.

The history file `.storage/login_detail_history` is kept, so a re-install
picks up where it left off. Delete it as well if you want the stored
usernames and IP addresses gone.

## Known limitations

- **Private core API.** See the warning at the top and
  [After a Home Assistant upgrade](#after-a-home-assistant-upgrade). This is
  the main limitation of the integration.
- **`username` on `invalid_mfa_code` may be the display name.** After the
  password step the username is taken from the flow's credential. If core has
  not set one, the user's display name is used instead of the login name.
- **`ip_address` is only as accurate as Home Assistant's HTTP setup.** It is
  the address Home Assistant sees, as resolved by `use_x_forwarded_for` and
  `trusted_proxies`. Behind a reverse proxy that is not trusted, or behind
  source NAT (including hairpin NAT for local clients), several clients can
  show up with the same address.
- **Rejected requests on ordinary views need IP banning enabled.** A 401 on
  `/api/mcp`, `/api/states` and other REST views only reaches core's
  `process_wrong_login` through the IP ban middleware, which Home Assistant
  only installs when `ip_ban_enabled` is true. With IP banning disabled, core
  does not log these requests and `login_detail_request_rejected` is not fired
  for them. The login flow, `/auth/token` and `/api/websocket` call core
  directly and are reported either way.
- **Calls without a token count towards the IP ban.** Core counts every
  rejected request against the IP address - also an MCP client's first call
  without a token, its calls with an expired token, and `not_admin`. With
  `login_attempts_threshold` set, enough of them ban the client's IP address
  for good (`ip_bans.yaml`); the counter is only reset by a successful login
  or websocket authentication *from the same address*, which never happens
  for a cloud MCP client, whose calls come from the provider's
  servers. Behind a reverse proxy that is not trusted, the proxy's address
  is banned - and with it everyone. This is core's behaviour; Login Detail
  only reports it. Without `login_attempts_threshold` (the default), nothing
  is counted or banned.
- **`not_admin` on websocket commands is not reported.** A websocket command
  that requires admin answers with an error message and does not call core's
  `process_wrong_login`, so there is nothing to capture without patching core.
  `not_admin` covers REST views such as `/api/mcp`.
- **A strict `logger.default` silences the log lines.** With for example
  `logger: default: error`, nothing is written to the log - the events are
  still fired. To keep the log lines, add:

  ```yaml
  logger:
    default: error
    logs:
      custom_components.login_detail: info
  ```

  `info` rather than `warning`, because it also gives you the startup line
  the post-upgrade check relies on.
- **The attribute keys changed in 0.8.0** from Danish to English, with no
  compatibility aliases. Update templates and cards that read attributes, see
  [Entities](#entities).
- **Installations from before 0.8.0 keep their Danish entity IDs** (for
  example `sensor.login_mislykkede_24t`), see [Entities](#entities).
- **The unit of the IP address sensor changed in 0.8.0** from `adresser` to
  `addresses`. It has long-term statistics, so on an existing installation
  Home Assistant reports that the unit changed and stops compiling its
  statistics until you resolve it under **Developer tools → Statistics**. The other sensors are not affected (`logins` is unchanged, and the
  history and sessions sensors have no statistics).
- **The config flow imports `probatio`**, the schema library shipped with Home
  Assistant 2026.10. If your Home Assistant version ships it under another
  name, the import fails.

## Contributing and issues

Bug reports and pull requests are welcome in the
[issue tracker](https://github.com/fuglphoenix/ha-login-detail/issues). Please
include your Home Assistant version and the relevant `login_detail` log lines -
with usernames and IP addresses removed.

Changes are recorded in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE)
