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
| `login_detail_failed` | `username`, `ip_address`, `reason`, `flow_id`, `auth_provider`, `endpoint` |
| `login_detail_succeeded` | `username`, `ip_address`, `flow_id`, `auth_provider`, `endpoint` |
| `login_detail_request_rejected` | `ip_address`, `endpoint`, `reason` |

Failed attempts are also logged at `WARNING`, successful logins and rejected
requests at `INFO` (core already logs rejected requests at `WARNING`).

`login_detail_request_rejected` covers requests that are rejected for invalid
authentication *outside* the login flow - typically a client with a wrong or
expired token on `/api/mcp`, `/api/websocket` or `/auth/token`. These carry no
username. `endpoint` is the route pattern (`/api/mcp`,
`/api/hassio_ingress/{token}/{path}`), never the literal path or query string,
which can carry secrets. Login flow attempts always report `/auth/login_flow`.
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

## Entities

Six sensors are created automatically. No YAML, no helpers.

| Entity | Shows |
|---|---|
| `sensor.login_seneste_haendelse` | Latest login event as `result · username · ip` (the endpoint instead of the username for a rejected request). Attributes: time, result, user, IP, hostname, auth provider, endpoint, count. |
| `sensor.login_ok_24t` | Number of successful logins in the last 24 hours, with per-IP, per-username and per-endpoint counts. |
| `sensor.login_mislykkede_24t` | Number of failed login attempts and rejected requests in the last 24 hours, with per-IP, per-username and per-endpoint (`endpoints`) counts. |
| `sensor.login_ip_adresser_24t` | Number of unique IP addresses that tried to log in or were rejected in the last 24 hours, with hostname, counts, usernames and endpoints per IP. |
| `sensor.login_historik` | Number of stored events (up to 50); the latest 25 are in the attributes. |
| `sensor.login_aktive_sessioner` | Active refresh tokens per user, with the IP address and time they were last used. System tokens are not counted. |

The entity names, attribute keys and units are in Danish - see
[Known limitations](#known-limitations).

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
   There is nothing to configure; the dialog is a confirmation.

Do **not** add `login_detail:` to `configuration.yaml`. If an older setup has
the line, it is imported into a config entry automatically on the first
startup, after which the line can be removed.

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

- **Usernames and IP addresses are stored in plain text** - in the events, in
  the Home Assistant log, in `.storage/login_detail_history` (up to 50
  entries), in the sensor attributes and therefore in the recorder database.
- Attempted usernames from failed logins are stored as typed. A user who types
  their password into the username field will have their password stored as a
  "username".
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
| The warning appears with the made-up username, and a real login raises `sensor.login_ok_24t` by **exactly 1** | It works. | Nothing. |

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
- **Entity names, attribute keys and units are in Danish** (for example
  `sensor.login_mislykkede_24t`, attribute `brugernavne`, unit `adresser`).
  They are kept for compatibility with existing installations; changing them
  would break existing dashboards, templates and long-term statistics.
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
