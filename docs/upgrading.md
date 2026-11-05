# Upgrading

## Upgrading to 0.11.2

Nothing to do, and nothing changes in the events, entity IDs, unique IDs or
attribute keys. On **Home Assistant 2026.11** with 0.11.1 or older, every
rejection on `/auth/token` was reported as `rejection: invalid_auth_request`
with no `client_id`, `grant_type`, `token_type` or user - the detail 0.10.0
added was silently gone, and the self-check still said `token_endpoint`
passed. 0.11.2 restores it and makes the self-check catch that class of
change. On 2026.10 nothing was affected and nothing changes.

Two things on 2026.11 are core's own choice and stay that way:

- An MCP server with "require admin" answers a non-admin with 403 instead of
  401. Core does not treat 403 as a failed login, so it is not reported and
  does not count towards the IP ban. The call is still refused.
- A request to `/auth/token` that repeats a form field is refused by core
  before it looks at the grant type. It is reported with the reason the grant
  itself would have got.

See [Known limitations](known-limitations.md).

## Upgrading to 0.11.1

Nothing to do, and nothing changes in the events, entity IDs, unique IDs or
attribute keys. 0.11.1 only makes the integration load on **Home Assistant
2026.11**, which moved the two-factor steps of the login flow to a new class.
On 0.11.0 and older, 2026.11 leaves the integration failing to set up with
`AttributeError: type object 'LoginFlow' has no attribute 'async_step_mfa'` -
no sensors, no Sign-in log panel and no events, while logging in itself keeps
working normally. 0.11.1 supports 2026.10 and 2026.11 both.

## Upgrading to 0.10.0

Nothing to do. Events, entity IDs, unique IDs and attribute keys are kept;
three things are new or different:

- Every event, history entry and websocket entry gets `ingress`, `host` and
  `x_forwarded_for_untrusted`, and `login_detail_request_rejected` gets
  `grant_type`. Entries stored before have `null` there.
- A rejection on `/auth/token` now says why (`unknown_or_revoked_token`,
  `client_id_mismatch`, `invalid_client_id`, `invalid_authorization_code`,
  ...) instead of `invalid_auth_request`. An automation that filters on
  `rejection: invalid_auth_request` for `/auth/token` must use the new values.
- A bearer value that is not a JWT at all is now `malformed_credentials`
  instead of `unknown_or_revoked_token`.

See [Events](events.md).

## Upgrading to 0.8.0

0.8.0 makes the sensor names, units and attribute keys English. Entity IDs,
unique IDs, states, events and the websocket API are unchanged.

### Which entity IDs you get

Since 0.8.0 the entity names (for example "Login failed 24h") and units are
English. Home Assistant derives the entity ID from the name only the first
time it sees an entity; after that the entity registry finds the entity by its
unique ID, which has not changed. So:

- **A new installation** gets the English entity IDs, for example
  `sensor.login_failed_24h`.
- **An existing installation keeps its entity IDs**, for example
  `sensor.login_mislykkede_24t` - whatever refers to the entity ID keeps
  working. The displayed name and unit change, and so do the attribute keys
  (below). If you have renamed an entity yourself, your name is kept.
- To switch an existing installation to the English IDs, rename them under
  **Settings → Entities** (entity ID field), and update whatever refers to
  them.

Both entity IDs of every sensor are listed in [Entities](entities.md).

### Attribute keys

Since 0.8.0 the attribute keys are English as well. This is a breaking
change, with no compatibility aliases: a template, automation or dashboard
card that reads an attribute, for example
`state_attr('sensor.login_failed_24h', 'brugernavne')`, must use the new key
(`'usernames'`). Keys not listed (`ip`, `endpoint`, `endpoints`, `count`,
`ok`, `type`) are unchanged.

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

### Unit of the IP address sensor

The unit of the IP address sensor changed from `adresser` to `addresses`. It
has long-term statistics, so on an existing installation Home Assistant
reports that the unit changed and stops compiling its statistics until you
resolve it under **Developer tools → Statistics**. The other sensors are not
affected (`logins` is unchanged, and the history and sessions sensors have no
statistics).

## After a Home Assistant upgrade

This is the one maintenance task the integration needs. Make sure
`custom_components.login_detail` logs at `info` (see
[Known limitations](known-limitations.md)), restart, and check:

| What you see | What it means | What to do |
|---|---|---|
| The integration shows **Failed to set up** under Settings → Devices & services, with an `AttributeError` from `custom_components/login_detail/__init__.py` in the log | A name the monkeypatch reaches for is gone - core has renamed or moved part of the login code. This is what 2026.11 did (`LoginFlow.async_step_mfa`). Logging in is unaffected. | Upgrade the integration. If there is no newer release, open an issue with the traceback and your Home Assistant version. |
| `login_detail active - logins fire the events ...` is **missing** from the startup log | The monkeypatch could not be applied - core has moved the login code. | Check the log for a `login_detail` error. Open an issue with your Home Assistant version. |
| The line is there, but a deliberately wrong login with a made-up username (e.g. `does-not-exist`) gives **no** `Failed login: user=does-not-exist ...` warning | The patch is applied to methods core no longer calls. | Open an issue with your Home Assistant version. |
| The warning appears with the made-up username, and a real login raises `sensor.login_ok_24h` (`sensor.login_ok_24t` on an installation from before 0.8.0) by **exactly 1** | It works. | Nothing. |
| `login_detail self-check passed: token_endpoint, token_identification` is in the startup log | The private core API behind the rejection details (0.7.1 and 0.10.0) is as expected. | Nothing. |
| `login_detail self-check: token_identification does not match this Home Assistant version and is switched off` | Core changed the access token format (an HS256 JWT whose `iss` is the refresh token ID) or `async_get_refresh_token`. Rejected tokens are reported without a reason or user, as before 0.7.1 - nothing breaks. | Open an issue with your Home Assistant version. |
| `login_detail self-check: token_endpoint does not match this Home Assistant version and is switched off` | Core changed the `/auth/token` view (the form it reads, the order of its checks, `log_invalid_auth`) or `async_get_refresh_token_by_token`. Rejections on `/auth/token` are reported as `invalid_auth_request`, as before 0.10.0 - nothing breaks. | Open an issue with your Home Assistant version. |

The self-check runs every time the integration is set up. It compares core's
own source with what Login Detail relies on, without creating or touching a
token, and switches off only the feature that no longer matches - so a core
change makes the details disappear with a warning, instead of making them
wrong. It does not cover a change in behaviour that leaves the source text
the same; the acceptance test does.

For a thorough check, run the acceptance test in [`tests/`](../tests/README.md).
It exercises the whole login path, including 2FA, against a real Home
Assistant.
