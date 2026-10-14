# Upgrading

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
| `login_detail active - logins fire the events ...` is **missing** from the startup log | The monkeypatch could not be applied - core has moved the login code. | Check the log for a `login_detail` error. Open an issue with your Home Assistant version. |
| The line is there, but a deliberately wrong login with a made-up username (e.g. `does-not-exist`) gives **no** `Failed login: user=does-not-exist ...` warning | The patch is applied to methods core no longer calls. | Open an issue with your Home Assistant version. |
| The warning appears with the made-up username, and a real login raises `sensor.login_ok_24h` (`sensor.login_ok_24t` on an installation from before 0.8.0) by **exactly 1** | It works. | Nothing. |

For a thorough check, run the acceptance test in [`tests/`](../tests/README.md).
It exercises the whole login path, including 2FA, against a real Home
Assistant.
