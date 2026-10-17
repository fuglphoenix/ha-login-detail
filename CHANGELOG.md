# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.1] - 2026-10-17

### Changed
- **The Sign-in log panel no longer scrolls sideways.** A row now shows
  time, type, user, IP address and the reason with its endpoint; the reason
  is cut to the width that is left. Everything else - hostname, user ID,
  client, client ID, token type, grant type, method, user agent, ingress,
  host, X-Forwarded-For (untrusted) and flow ID - is shown when the row is
  expanded, by a click on the row or with the keyboard on its button
  (`aria-expanded`). Empty fields are left out. On a screen up to 600 px
  wide, the IP address, reason and endpoint move into the expanded row too.
  An expanded row stays open across a refresh or a change of filter.
- The client ID and the grant type, before only on hover, and the full
  user agent, user ID and flow ID are now readable in the expanded row.
- A new screenshot in the README, with test data and one row expanded.

Display only: no stored data, event, websocket field or filter changed.

## [0.10.0] - 2026-10-17

### Added
- **Why `/auth/token` turned a grant down** - the case behind most reports
  in home-assistant/core#148110 (a phone app or browser with a dead refresh
  token). `rejection` on `login_detail_request_rejected` is now
  `unknown_or_revoked_token`, `client_id_mismatch`, `user_not_allowed`,
  `invalid_client_id`, `malformed_credentials`, `invalid_authorization_code`
  or `unsupported_grant_type` instead of `invalid_auth_request`, and the event
  has `grant_type` and the requested `client_id`. The user is named only if
  the refresh token sent exists. Read from the form core has already parsed;
  the refresh token, code and verifier are never stored or logged. See
  "/auth/token" in docs/events.md.
- **How the request came in**, on every event, history entry and websocket
  entry: `ingress` (`nabu_casa` or `direct`), `host` (the Host header) and
  `x_forwarded_for_untrusted` (the raw X-Forwarded-For chain). The latter is
  set by the client and can be forged; its name and the Sign-in log panel say
  so. `ip_address` is unchanged.
- Three columns in the Sign-in log panel: Ingress, Host and
  "X-Forwarded-For (untrusted)"; the grant type is shown on hover over the
  client.
- **A startup self-check** of the private core API behind the rejection
  details: the access token format and `async_get_refresh_token` (0.7.1), and
  the `/auth/token` view and `async_get_refresh_token_by_token` (0.10.0).
  A feature that no longer matches is switched off with a WARNING and falls
  back to what was reported before it existed, instead of failing silently
  or guessing. `login_detail self-check passed: ...` is logged at INFO when
  all is well. See docs/upgrading.md.
- Acceptance probe F (`login_detail_probe6`).

### Changed
- A bearer value that is not a JWT at all - a random string - is now
  `malformed_credentials` instead of `unknown_or_revoked_token`.
- The log line has four more fields: `grant_type`, `ingress`, `host` and
  `x_forwarded_for_untrusted`.
- A folded rejection keeps the newest `ingress`, `host` and
  `x_forwarded_for_untrusted`, like the user agent.
- `auth` is in `after_dependencies`, since the `/auth/token` details import
  from it.

No events, event fields, entity IDs, unique IDs or attribute keys were
removed or renamed. `user_agent` (0.7.0) and the token identification of
0.7.1 are unchanged.

## [0.9.0] - 2026-10-14

### Added
- **Option: store usernames that do not exist** (`store_unknown_usernames`,
  off by default). When it is on, a failed login with a username no user has
  keeps the username as typed - in the `login_detail_failed` event, the log
  line, the sensors and the Sign-in log - so you can see what a brute force
  attempt tries. Such an entry carries `known_user: false` and has no
  `user_id`; the panel marks it "no such user". The name is cut to 64
  characters and control characters are replaced, so a stranger cannot fill
  the log or break a log line. **Warning:** a password typed into the
  username field is then stored in plain text, in the log, the recorder
  database and backups. See docs/privacy.md.
- The option is under **Configure** on the integration, also on an existing
  installation: no need to remove and re-add it. An entry from before 0.9.0
  has no such key and reads it as off; nothing is migrated. Saving reloads
  the entry, so the change applies at once, without a restart.
- With the option off, nothing changes: unknown usernames are still `null`,
  and no event, entry or log line has a `known_user` key.

### Fixed
- The Sign-in log panel no longer logs `Failed to execute 'define' on
  'CustomElementRegistry'` when a browser tab that was open during an
  upgrade loads the new version. The tab keeps the old panel until it is
  reloaded.

## [0.8.1] - 2026-10-14

### Added
- **An icon.** A shield with a person, shipped in
  `custom_components/login_detail/brand/` (`icon.png`, `icon@2x.png`), so
  **Settings → Devices & services** no longer shows a generic placeholder
  (Home Assistant 2026.3 and newer). Drawn by `tools/brand.py`; the source is
  `assets/icon.svg`. The HACS dashboard does not show it yet, see
  docs/known-limitations.md.

## [0.8.0] - 2026-10-14

### Changed
- **English sensor names and units.** The six sensors are now called
  "Login latest event", "Login ok 24h", "Login failed 24h", "Login IP
  addresses 24h", "Login history" and "Login active sessions", with the units
  `addresses`, `events` and `sessions`. The unique IDs are unchanged, so an
  existing installation keeps its entity IDs (for example
  `sensor.login_mislykkede_24t`); a new installation gets English ones (for
  example `sensor.login_failed_24h`). See docs/upgrading.md.
- The unit change on the IP address sensor makes Home Assistant report a unit
  change for its long-term statistics on an existing installation; resolve it
  under Developer tools → Statistics. See docs/upgrading.md.
- **Breaking: English attribute keys.** Templates, automations and dashboard
  cards that read a sensor attribute must use the new keys. Keys not listed
  (`ip`, `endpoint`, `endpoints`, `count`, `ok`, `type`) are unchanged; so
  are entity IDs, unique IDs, states, events and the websocket API.

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

### Documentation
- Documentation restructured: the README covers evaluating, installing and
  getting started, with a screenshot of the Sign-in log panel; the reference
  material moved to `docs/`.

## [0.7.1] - 2026-10-14

### Added
- **Why a request was rejected.** `login_detail_request_rejected` has a new
  field `rejection`, and the sign-in log and the log line show it as the
  reason: `no_credentials`, `expired_token`, `user_not_allowed`,
  `unknown_or_revoked_token`, `malformed_credentials`, `invalid_signed_path`,
  `not_admin`, `permission_denied` or `invalid_auth_request`. See
  "Rejected requests" in docs/events.md.
- **Who sent it, when that is proven.** For a rejected token, the signature
  is checked against the key of the refresh token it names (everything core
  checks except the expiry). Only if it matches, the event, the history and
  the sign-in log get `user_id`, `username`, `client_id`, `token_type` and,
  for a long-lived token, `client_name`. Forged, unsigned, unknown and
  revoked tokens name nobody. The token itself is never stored or logged.
- `not_admin` for a valid user turned away by an endpoint that requires an
  administrator - for example the MCP server with "require admin" - without
  patching core.
- Websocket command `login_detail/sign_in_log/list`: filter `reason`, and the
  entry fields `client_id`, `client_name` and `token_type`.
- A "Client" column in the Sign-in log panel (the long-lived token's name or
  the client ID, with the token type on hover).
- Acceptance probe D (`login_detail_probe4`) for all of the above.

### Changed
- Rejected requests from the same IP address on the same endpoint are only
  folded together if they also have the same reason, user and client.
- The log line has three more fields, `client_id`, `token_type` and
  `client_name`, on every event (`None` unless proven), so all lines keep
  one format.
- `AuthPhase.async_handle` of the websocket is patched too, to see the token
  in the auth message. It only passes the token on, in memory, for the
  duration of the call.

The `reason` of `login_detail_request_rejected` is still
`invalid_request_auth`. No events, event fields, entity IDs or `unique_id`s
were removed or renamed.

## [0.7.0] - 2026-10-14

### Added
- **Sign-in log panel**: an admin-only panel in the sidebar showing every
  recorded event as one row (time, type, reason, username, user ID, login
  method, IP address, endpoint, user agent, flow ID), with filters for type,
  user, IP address and time span. A proof of concept for
  home-assistant/architecture#1346. It can be hidden in the options.
- Websocket command `login_detail/sign_in_log/list`, admin only, with the
  optional filters `log_type`, `username`, `user_id`, `ip`, `start_time`,
  `end_time` and `limit`. The panel uses nothing else.
- `user_id` on `login_detail_failed` and `login_detail_succeeded` and in the
  history, whenever the user is known.
- `user_agent` on all three events and in the history, cut to 256 characters.
- Options for retention: maximum number of entries (default 500, 50-5000) and
  maximum age in days (default 7, 1-365). Expired entries are removed hourly.

### Changed
- **The username of a failed login is only recorded if it belongs to an
  existing user.** Otherwise `username` is `null` in the event, the log line,
  the history and the sensors. Before, it was recorded as typed - including a
  password typed into the username field.
- One log line per event in the format of the sign-in log
  (`Sign-in log: type=… reason=… username=… …`), replacing the previous
  `Failed login: …`, `Login ok: …` and `Request rejected: …` lines. The levels
  are unchanged.
- The history keeps up to `max_entries` (default 500) instead of 50 entries,
  and entries older than `max_days` (default 7) are removed. The state of
  `sensor.login_historik` is the number of stored entries and can therefore
  go above 50; its attributes still show the newest 25. The 24-hour sensors
  are unaffected.
- `manifest.json` declares `panel_custom` and `websocket_api` as
  dependencies.

No events, event fields, entity IDs or `unique_id`s were removed or renamed.

## [0.6.0] - 2026-10-14

### Added
- Requests rejected for invalid authentication outside the login flow - a
  wrong or expired token on `/api/mcp`, `/api/websocket`, `/auth/token` or any
  other authenticated endpoint - are now recorded. They fire the new event
  `login_detail_request_rejected` (`ip_address`, `endpoint`, `reason`) and are
  stored in the history with `result: invalid_request_auth`.
- An `endpoint` field on every event and history entry. Login flow attempts
  report `/auth/login_flow`; rejected requests report the route pattern, for
  example `/api/mcp` or `/api/hassio_ingress/{token}/{path}` - never the
  literal path or the query string, which can carry secrets.
- Sensor attributes: `endpoints` (attempts per endpoint) on
  `sensor.login_ok_24t` and `sensor.login_mislykkede_24t`, `endpoints` per IP
  address on `sensor.login_ip_adresser_24t`, and `endpoint` and `count` on the
  entries of `sensor.login_seneste_haendelse` and `sensor.login_historik`.

### Changed
- `sensor.login_mislykkede_24t` and the `mislykkede` count per IP address now
  include rejected requests. Expect the number to rise if a client with a
  stale token is polling your instance.
- Repeated rejections from the same IP address on the same endpoint within 10
  minutes are folded into one history entry with a `count`, so a retrying
  client cannot push real login attempts out of the 50-entry history.
- `sensor.login_seneste_haendelse` shows the endpoint instead of the username
  for a rejected request, which has none.
- `manifest.json` declares `http` as a dependency, which hassfest requires for
  importing `homeassistant.components.http`.
- The integration now also patches `process_wrong_login` in
  `homeassistant.components.http.ban` and
  `homeassistant.components.websocket_api.auth` (private core API). Core's own
  handling - the log line, the notification and IP banning - runs unchanged
  and first.

## [0.5.0] - 2026-10-14

No change in behaviour. Events, event fields, entity IDs and `unique_id`s are
unchanged.

### Changed
- All documentation, code comments and log messages are now in English. If you
  filter the log on the old message texts, update the filter; the events are
  unchanged.
- `manifest.json` now points at this repository for documentation and issues,
  and lists a code owner.
- The acceptance test probes are renamed to `login_detail_probe` and
  `login_detail_probe2`.

### Added
- MIT licence.
- `hacs.json`, so the integration can be installed as a HACS custom repository.
- hassfest and HACS validation in CI.
- Static checks with `pytest` and `ruff` in CI.

## [0.4.0] - 2026-10-12

### Added
- Setup from the UI: **Settings → Devices & services → Add integration →
  Login Detail**. `config_flow.py`, `strings.json` and Danish and English
  translations.
- `single_config_entry: true` in the manifest, plus a check in
  `async_step_user`.

### Changed
- `login_detail:` in `configuration.yaml` is no longer needed. An existing
  line is imported into a config entry automatically (`async_step_import`).
- `unique_id`s are unchanged, so the entity registry adopts the existing
  entities. Entity IDs, names and recorder history carry over.

### Fixed
- Removing or reloading the integration from the UI now takes the monkeypatch
  of core's three login methods off again, without a restart. In 0.3.0 it
  stayed in place until the next restart.
- The history could lose its newest entries. `Store.async_delay_save` waits
  10 seconds, and on a reload nothing forced the last write through - Home
  Assistant only does that on a restart. The bug also existed in 0.2.0 and
  0.3.0, but only became visible once the integration could be reloaded
  without a restart. Fixed with `async_flush()` in `async_unload_entry`.

## [0.3.0] - 2026-10-12

### Security
- `ok` was reported before 2FA had been entered. 0.2.0 hooked
  `LoginFlow.async_finish`, which core already calls once the **password** has
  been accepted; the interruption for 2FA happens one layer up, in
  `AuthManagerFlowManager.async_finish_flow`. As a result:
  1. One login was counted as two.
  2. **An attempt with the correct password where 2FA was never entered was
     recorded as `ok`.** Someone with the right password who did not get past
     2FA showed up as a completed login.

  Success is now reported from `async_finish_flow`, the only place where the
  final outcome is known. The intermediate state is reported as `mfa_pending`
  and replaced by `ok` when the same flow completes. If 2FA is never
  submitted, `mfa_pending` remains - and that is exactly the attempt worth
  seeing.

  The bug only affected users with 2FA enabled. Running the 0.3.0 acceptance
  test against 0.2.0 fails on exactly the five checks the bug is about.

## [0.2.0] - 2026-10-12

### Added
- Sensors (`sensor.py`, `const.py`): six entities.
- `login_detail_succeeded` event. 0.1.0 only saw failures and could not answer
  "who actually got in".
- History in `.storage/login_detail_history`, so it survives a restart.
- Reverse DNS lookup for each entry, in a background task.

### Known issues
- Reports `ok` before 2FA has been entered. Fixed in 0.3.0.

## [0.1.0] - 2026-10-12

### Added
- First version. Monkeypatches `HassLoginFlow.async_step_init` and
  `LoginFlow.async_step_mfa` and fires `login_detail_failed` with `username`,
  `ip_address`, `reason` and `flow_id`. Set up with `login_detail:` in
  `configuration.yaml`.
- Closes the gap that Home Assistant does not log *which* username was used in
  a failed login attempt
  ([home-assistant/core#148110](https://github.com/home-assistant/core/issues/148110)).

[Unreleased]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.10.1...HEAD
[0.10.1]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.9.0...v0.10.0
[0.7.1]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/fuglphoenix/ha-login-detail/releases/tag/v0.5.0
